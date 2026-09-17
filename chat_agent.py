from pydantic_ai import Agent
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.settings import ModelSettings

from pydantic_ai.mcp import MCPServerSSE
import chat_agent_message_cache
import asyncio
import json
import re
import logging
import traceback
import sys
from contextvars import ContextVar
from pathlib import Path

import httpx
import requests

import config as chat_agent_config
from backend import config as backend_config

MCP_SERVER_URL = backend_config.MCP_SERVER_MCP_URL


CHART_DATA_PATTERN = re.compile(
    r'(?:```[a-zA-Z]*\s*)?\[CHART_DATA\](.*?)\[/CHART_DATA\](?:\s*```)?',
    re.DOTALL,
)
UNTERMINATED_CHART_DATA_PATTERN = re.compile(r'\[CHART_DATA\][\s\S]*$')
BLANK_LINE_RUN_PATTERN = re.compile(r'\n{3,}')
MALFORMED_CHART_WARNING = 'A chart could not be rendered because the chart data was malformed.'
TRUNCATED_CHART_WARNING = 'A chart could not be rendered because the response was cut off.'

# Per-turn output cap, applied at the agent level so it can be tuned without
# rebuilding the shared model singleton. Generous because a tool-calling data
# agent burns tokens on orchestration before it ever writes the final answer.
# This applies to every provider, not just the Gateway, for parity — models that
# cap below this will reject the request with a 400.
LLM_MAX_OUTPUT_TOKENS = 8192

# One source of truth for the "not configured" wording. The route serves it as
# the 503 `error_detail`, and `get_agent_response` raises it as a RuntimeError
# that the route also surfaces verbatim — two copies would drift.
NOT_CONFIGURED_ERROR_DETAIL = (
    'Please set the required environment variables to enable the chat feature. '
    'Use one of:\n'
    '  • Domino LLM Gateway: DOMINO_LLM_GATEWAY_URL + DOMINO_LLM_GATEWAY_MODEL\n'
    '  • OpenAI-compatible: LLM_API_KEY (+ optional LLM_BASE_URL, LLM_MODEL)\n'
    '  • Local Ollama:      LLM_BASE_URL + LLM_MODEL'
)
NOT_CONFIGURED_ERROR = f'Chat is not configured. {NOT_CONFIGURED_ERROR_DETAIL}'

# Configure logging - write to both file and stdout so logs appear in Domino app logs
logging.basicConfig(
    level=logging.DEBUG if backend_config.VERBOSE_LOGGING else logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('chat_agent.log'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)


# ===== LLM CONFIGURATION =====
# Three provider paths, checked in this order (first match wins):
#
#   1. Domino LLM Gateway — DOMINO_LLM_GATEWAY_URL + DOMINO_LLM_GATEWAY_MODEL
#      The Gateway is OpenAI-API-compatible, so it reuses the same provider
#      class; only the auth differs (see DominoAccessTokenAuth below).
#      It deliberately outranks LLM_API_KEY because operators routinely leave
#      stale keys behind in project settings. Setting only *one* of the two
#      Gateway vars means "Gateway not requested" and falls through — there is
#      no half-configured error state.
#   2. OpenAI-compatible cloud — LLM_API_KEY (or OPENAI_API_KEY),
#      optionally LLM_BASE_URL (default https://api.openai.com/v1) and
#      LLM_MODEL (default gpt-4o-mini).
#   3. Local Ollama — LLM_BASE_URL pointing at localhost/127.0.0.1 + LLM_MODEL,
#      no API key needed.
#
# Examples:
#   Gateway:    DOMINO_LLM_GATEWAY_URL=https://<deploy>/apps/<app-id> DOMINO_LLM_GATEWAY_MODEL=gpt-5.4-nano
#   OpenAI:     LLM_API_KEY=sk-xxx LLM_MODEL=gpt-4o
#   Ollama:     LLM_BASE_URL=http://localhost:11434/v1 LLM_MODEL=llama3
#   Azure:      LLM_BASE_URL=https://your-resource.openai.azure.com/openai/deployments/your-deployment
#   Together:   LLM_BASE_URL=https://api.together.xyz/v1 LLM_API_KEY=xxx LLM_MODEL=meta-llama/Llama-3-70b-chat-hf

PROVIDER_DOMINO_GATEWAY = 'domino_gateway'
PROVIDER_OPENAI = 'openai'
PROVIDER_OLLAMA = 'ollama'

# The visiting user's token for the current request. The HTTP route sets this
# from the inbound Authorization header before invoking the agent, and
# DominoAccessTokenAuth reads it when building the outbound header.
#
# A ContextVar is what makes this work across the sync/async boundary: the JWT
# arrives in a Flask (sync) request context, but the outbound Gateway call
# happens inside an asyncio.run(...) loop behind a shared client singleton.
# asyncio tasks copy the current context at creation time, so setting the var
# *before* asyncio.run is enough for the auth flow deep inside to see it.
_passthrough_token_var: ContextVar = ContextVar(
    'domino_gateway_passthrough_token', default=None
)


def set_gateway_passthrough_token(token):
    """Set the visiting user's token for the current request.

    Returns the ContextVar handle so the caller can reset it in a `finally`
    block. Pass None to explicitly unset.
    """
    return _passthrough_token_var.set(token)


def reset_gateway_passthrough_token(handle):
    """Restore the passthrough token to its previous value.

    Not optional: without it a stale visitor JWT can leak into a later request
    that arrives without one, which fails *open* with the wrong user's identity
    rather than erroring.
    """
    _passthrough_token_var.reset(handle)


def _normalize_gateway_base_url(raw: str) -> str:
    """Strip the trailing slash and append /v1 unless it is already there.

    Operators paste the Gateway App URL in all three shapes:
    `https://<deploy>/apps/<id>`, `.../v1` and `.../v1/`.
    """
    base = raw.rstrip('/')
    if not base.endswith('/v1'):
        base = f'{base}/v1'
    return base


class DominoAccessTokenAuth(httpx.Auth):
    """httpx auth flow that picks the right Domino token per request.

    Attaching this to the AsyncClient handed to OpenAIProvider is what lets the
    model/client object stay a process-wide singleton while the credential is
    recomputed on every outbound request.

    Token-source precedence (first match wins):

    1. `API_KEY_OVERRIDE` — used verbatim. Local dev / long-lived key.
    2. The per-request passthrough token — the visiting user's JWT, so Gateway
       audit logs attribute the call to the right person. This matches what the
       app's other Domino API calls (datasets, governance) already do.
    3. The Domino access-token sidecar on localhost:8899 — fetched fresh per
       request because the token expires within minutes. Returns the *app
       owner's* identity; the only option in classic App mode.

    There is deliberately no fallback from a failed passthrough token to the
    owner token: that would quietly re-introduce the misattribution.
    """

    requires_request_body = False
    requires_response_body = False

    def __init__(self, token_url: str):
        self._token_url = token_url

    def _fetch_token(self) -> str:
        override = backend_config.get_api_key_override()
        if override:
            return override

        passthrough = _passthrough_token_var.get()
        if passthrough:
            return passthrough

        # `requests` (sync) is used deliberately: one code path serves both the
        # sync and async httpx clients, and the loopback endpoint answers in
        # milliseconds so briefly blocking the event loop is acceptable.
        resp = requests.get(self._token_url, timeout=5)
        resp.raise_for_status()
        token = resp.text.strip()
        # Some sidecars hand back the whole header value rather than the token.
        if token.startswith('Bearer '):
            token = token[len('Bearer '):]
        return token

    def auth_flow(self, request):
        request.headers['Authorization'] = f'Bearer {self._fetch_token()}'
        yield request


def _domino_gateway_config():
    """Return (base_url, model, token_url) if Gateway env is set, else None."""
    base = backend_config.get_domino_llm_gateway_url()
    model = backend_config.get_domino_llm_gateway_model()
    if not (base and model):
        return None
    return (
        _normalize_gateway_base_url(base),
        model,
        backend_config.get_domino_llm_gateway_token_url(),
    )


def get_llm_config():
    """Return (provider, base_url, api_key, model) for the selected provider.

    `provider` is one of 'domino_gateway', 'openai' or 'ollama'. `api_key` is
    None for the Gateway — auth there is per-request via DominoAccessTokenAuth.
    """
    gateway = _domino_gateway_config()
    if gateway is not None:
        base_url, model, _token_url = gateway
        return PROVIDER_DOMINO_GATEWAY, base_url, None, model

    base_url, api_key, model = backend_config.get_llm_config()
    is_local = bool(base_url) and ('localhost' in base_url or '127.0.0.1' in base_url)
    return (PROVIDER_OLLAMA if is_local else PROVIDER_OPENAI), base_url, api_key, model


def is_chat_configured():
    """Check if the chat feature is properly configured."""
    provider, base_url, api_key, model = get_llm_config()

    # The Gateway carries its own per-request auth and Ollama needs no key, so
    # both only require a base URL and a model. Remote providers need a key.
    if provider in (PROVIDER_DOMINO_GATEWAY, PROVIDER_OLLAMA):
        return bool(base_url and model)
    return bool(api_key and model)


def get_chat_status():
    """Get detailed chat configuration status for the UI."""
    provider, base_url, api_key, model = get_llm_config()
    configured = is_chat_configured()

    missing = []
    if not configured:
        if provider == PROVIDER_DOMINO_GATEWAY:
            if not backend_config.get_domino_llm_gateway_url():
                missing.append('DOMINO_LLM_GATEWAY_URL')
            if not backend_config.get_domino_llm_gateway_model():
                missing.append('DOMINO_LLM_GATEWAY_MODEL')
        elif provider == PROVIDER_OPENAI and not api_key:
            missing.append('LLM_API_KEY')
        elif provider == PROVIDER_OLLAMA and not base_url:
            missing.append('LLM_BASE_URL')

    return {
        'configured': configured,
        'provider': provider,
        'base_url': base_url if configured else None,
        'model': model if configured else None,
        'is_local': provider == PROVIDER_OLLAMA,
        'missing': missing,
    }


get_message_histories = chat_agent_message_cache.get_cache

# System prompt is loaded from backend/prompts/chat_system_prompt.md so that
# editing the chart-spec instructions does not require a Python diff. The
# trailing newline is stripped to keep the in-memory string byte-equivalent
# to the previous inline triple-quoted literal.
SYSTEM_PROMPT = (
    Path(__file__).parent / "backend" / "prompts" / "chat_system_prompt.md"
).read_text(encoding="utf-8").rstrip("\n")

# ===== AGENT INITIALIZATION =====
# The LLM model is shared, but each session gets its own MCP server connection
# (with the session ID header) and its own message history.

_llm_model = None


def _get_llm_model():
    """Get or create the shared LLM model instance."""
    global _llm_model
    if _llm_model is not None:
        return _llm_model

    if not is_chat_configured():
        return None

    provider, base_url, api_key, model = get_llm_config()
    logger.info(
        f"Creating LLM model: provider={provider}, model={model}, base_url={base_url}"
    )

    if provider == PROVIDER_DOMINO_GATEWAY:
        token_url = backend_config.get_domino_llm_gateway_token_url()
        # The auth flow rewrites the Authorization header on every request, but
        # the OpenAI SDK still refuses to construct without a non-empty api_key,
        # hence the placeholder.
        provider_obj = OpenAIProvider(
            base_url=base_url,
            api_key='domino-gateway',
            http_client=httpx.AsyncClient(auth=DominoAccessTokenAuth(token_url)),
        )
    else:
        provider_obj = OpenAIProvider(
            base_url=base_url,
            api_key=api_key or 'ollama'
        )

    _llm_model = OpenAIChatModel(
        model_name=model,
        provider=provider_obj
    )
    return _llm_model


def _create_agent_for_session(session_id: str, authorization_header: str = None) -> Agent | None:
    """Create an agent with an MCP server connection bound to a specific session.

    session_id is the Domino user ID, used only for keying chat history.
    authorization_header is the raw Authorization header value (e.g. "Bearer <JWT>")
    forwarded from the original browser request so the MCP server can authenticate
    and route tool calls to the correct DataFrame.
    """
    llm_model = _get_llm_model()
    if llm_model is None:
        return None

    # The MCP server derives the session key by calling /api/users/v1/self with the
    # Authorization header, so we must forward the original JWT — not the user ID.
    mcp_auth_header = authorization_header or f'Bearer {session_id}'
    server = MCPServerSSE(
        url=MCP_SERVER_URL,
        headers={'Authorization': mcp_auth_header},
    )
    return Agent(
        llm_model,
        toolsets=[server],
        system_prompt=SYSTEM_PROMPT,
        retries=5,
        model_settings=ModelSettings(max_tokens=LLM_MAX_OUTPUT_TOKENS),
    )


def _create_agent_without_mcp() -> Agent | None:
    """Create an agent without MCP tools for degraded chat responses."""
    llm_model = _get_llm_model()
    if llm_model is None:
        return None

    return Agent(
        llm_model,
        system_prompt=SYSTEM_PROMPT,
        retries=5,
        model_settings=ModelSettings(max_tokens=LLM_MAX_OUTPUT_TOKENS),
    )


def _extract_response_payload(response_text: str) -> dict:
    charts = []
    malformed_chart_count = 0

    def remove_chart_block(match):
        nonlocal malformed_chart_count
        chart_json = match.group(1).strip()
        try:
            chart_data = json.loads(chart_json)
            charts.append(chart_data)
            chart_type = chart_data.get('type', 'unknown') if isinstance(chart_data, dict) else 'unknown'
            logger.debug(f"Successfully parsed chart: {chart_type}")
        except json.JSONDecodeError as e:
            malformed_chart_count += 1
            logger.warning(f"Failed to parse chart data: {e}")
            logger.warning(f"Chart JSON that failed: {chart_json[:200]}")
        return ''

    clean_text = CHART_DATA_PATTERN.sub(remove_chart_block, response_text)

    truncated = False
    if UNTERMINATED_CHART_DATA_PATTERN.search(clean_text):
        clean_text = UNTERMINATED_CHART_DATA_PATTERN.sub('', clean_text)
        truncated = True
        logger.warning('Dropped an unterminated [CHART_DATA] block from the response.')

    clean_text = BLANK_LINE_RUN_PATTERN.sub('\n\n', clean_text).strip()

    if malformed_chart_count:
        clean_text = f"{clean_text}\n\n{MALFORMED_CHART_WARNING}".strip()
    if truncated:
        clean_text = f"{clean_text}\n\n{TRUNCATED_CHART_WARNING}".strip()

    return {
        'text': clean_text,
        'charts': charts,
    }


def _text_from_part(part) -> str | None:
    content = getattr(part, 'content', None)
    if content is None:
        return None
    if isinstance(content, str):
        return content
    return str(content)


def get_history(session_id: str = 'default') -> list[dict]:
    """Return a UI-friendly transcript for a session's cached chat history."""
    transcript = []
    for message in chat_agent_message_cache.get_messages(session_id):
        parts = getattr(message, 'parts', None)
        if not parts:
            continue

        for part in parts:
            part_type = type(part).__name__
            text = _text_from_part(part)
            if not text:
                continue

            if part_type == 'UserPromptPart':
                transcript.append({'sender': 'user', 'text': text})
            elif part_type == 'TextPart':
                payload = _extract_response_payload(text)
                transcript.append({
                    'sender': 'agent',
                    'text': payload['text'],
                    'charts': payload['charts'],
                })
    return transcript


async def get_agent_response(message: str, session_id: str = 'default', authorization_header: str = None) -> dict:
    """Gets a response from the agent, running with MCP servers.
    Returns a dict with 'text' and optional 'charts' list.
    Raises RuntimeError if chat is not configured.

    session_id: Domino user ID, used for keying per-user chat history.
    authorization_header: raw Authorization header from the browser request (e.g. "Bearer <JWT>"),
        forwarded to the MCP server so it can authenticate the caller. When omitted the
        session_id is used as a fallback (local / dev mode only).
    """

    if not is_chat_configured():
        raise RuntimeError(NOT_CONFIGURED_ERROR)

    current_agent = _create_agent_for_session(session_id, authorization_header)
    if current_agent is None:
        raise RuntimeError(NOT_CONFIGURED_ERROR)

    message_history = chat_agent_message_cache.get_messages(session_id)

    logger.info(f"Starting agent response for Domino user {session_id[:8]}...")

    try:
        logger.debug("Connecting to MCP servers...")
        try:
            async with current_agent:
                logger.debug("Running agent with message...")
                result = await current_agent.run(message, message_history=message_history)
        except Exception as mcp_error:
            logger.warning(f"MCP server connection failed: {mcp_error}")
            logger.info("Falling back to agent without MCP servers...")
            fallback_agent = _create_agent_without_mcp()
            if fallback_agent is None:
                raise RuntimeError(NOT_CONFIGURED_ERROR)
            result = await fallback_agent.run(message, message_history=message_history)

        logger.debug("Agent run completed successfully")

        # Update this session's history
        message_history = chat_agent_message_cache.add_messages(session_id, result.new_messages())
        logger.debug(f"User {session_id[:8]} history now has {len(message_history)} messages")

        response_text = result.output
        logger.debug(f"Got response text of length {len(response_text)}")

        response_payload = _extract_response_payload(response_text)

        logger.info(f"Successfully generated response with {len(response_payload['charts'])} charts")
        return response_payload

    except Exception as e:
        logger.error(f"Error in get_agent_response: {str(e)}")
        logger.error(f"Error type: {type(e).__name__}")
        logger.error(f"Error module: {type(e).__module__}")
        logger.error(f"Full traceback:\n{traceback.format_exc()}")

        error_type = type(e).__name__
        if 'API' in error_type or 'openai' in str(type(e).__module__).lower():
            logger.error("This appears to be an OpenAI API error. Check API key and quota.")
        elif 'Connection' in error_type or 'httpx' in str(type(e).__module__).lower():
            logger.error("This appears to be a connection error. Check network and MCP server.")

        raise


def clear_history(session_id: str = 'default'):
    """Clear the conversation history for a session."""
    chat_agent_message_cache.clear_messages(session_id)
    logger.info(f"Chat history cleared for user {session_id[:8]}...")

async def main():
    if not is_chat_configured():
        logger.error(NOT_CONFIGURED_ERROR)
        return

    current_agent = _create_agent_for_session('default')
    if current_agent is None:
        return

    async with current_agent:
        result = await current_agent.run('What attributes have the strongest correlation?')
    response_length = len(result.output)
    logger.info(f"Agent response generated with {response_length} characters")

if __name__ == "__main__":
    asyncio.run(main())
