from pydantic_ai import Agent
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

from pydantic_ai.mcp import MCPServerSSE
import chat_agent_message_cache
from pydantic_ai.messages import ModelMessage
import asyncio
import json
import re
import os
import logging
import traceback
import sys
from dataclasses import replace
from pathlib import Path

import config as chat_agent_config

MCP_SERVER_URL = 'http://localhost:3333/mcp'


CHART_DATA_PATTERN = re.compile(r'\[CHART_DATA\](.*?)\[/CHART_DATA\]', re.DOTALL)
CHART_HISTORY_REPLACEMENT = '[Chart data omitted from chat history.]'
MALFORMED_CHART_WARNING = 'A chart could not be rendered because the chart data was malformed.'

# Configure logging - write to both file and stdout so logs appear in Domino app logs
# Set VERBOSE_LOGGING=true to enable DEBUG for all libraries (mcp, openai, etc.)
_verbose = os.environ.get('VERBOSE_LOGGING', 'false').lower() == 'true'
logging.basicConfig(
    level=logging.DEBUG if _verbose else logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('chat_agent.log'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)


# ===== LLM CONFIGURATION =====
# Environment variables for flexible LLM configuration:
#   LLM_BASE_URL - Base URL for OpenAI-compatible API (default: https://api.openai.com/v1)
#   LLM_API_KEY  - API key for the LLM provider (required for most providers)
#   LLM_MODEL    - Model name to use (default: gpt-4o-mini)
#
# Examples:
#   OpenAI:     LLM_API_KEY=sk-xxx LLM_MODEL=gpt-4o
#   Ollama:     LLM_BASE_URL=http://localhost:11434/v1 LLM_MODEL=llama3
#   Azure:      LLM_BASE_URL=https://your-resource.openai.azure.com/openai/deployments/your-deployment
#   Together:   LLM_BASE_URL=https://api.together.xyz/v1 LLM_API_KEY=xxx LLM_MODEL=meta-llama/Llama-3-70b-chat-hf

def get_llm_config():
    """Get LLM configuration from environment variables."""
    base_url = os.environ.get('LLM_BASE_URL', 'https://api.openai.com/v1')
    api_key = os.environ.get('LLM_API_KEY') or os.environ.get('OPENAI_API_KEY')
    model = os.environ.get('LLM_MODEL', 'gpt-4o-mini')
    return base_url, api_key, model


def is_chat_configured():
    """Check if the chat feature is properly configured."""
    base_url, api_key, model = get_llm_config()

    # For local Ollama, API key is optional
    is_local_ollama = base_url and ('localhost' in base_url or '127.0.0.1' in base_url)

    if is_local_ollama:
        # Ollama just needs a base URL and model
        return bool(base_url and model)
    else:
        # Remote providers need an API key
        return bool(api_key and model)


def get_chat_status():
    """Get detailed chat configuration status for the UI."""
    base_url, api_key, model = get_llm_config()
    is_local = base_url and ('localhost' in base_url or '127.0.0.1' in base_url)

    configured = is_chat_configured()

    return {
        'configured': configured,
        'base_url': base_url if configured else None,
        'model': model if configured else None,
        'is_local': is_local,
        'missing': [] if configured else (
            ['LLM_API_KEY'] if not api_key and not is_local else
            ['LLM_BASE_URL'] if not base_url else []
        )
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

    base_url, api_key, model = get_llm_config()
    logger.info(f"Creating LLM model: {model}, base_url: {base_url}")

    provider = OpenAIProvider(
        base_url=base_url,
        api_key=api_key or 'ollama'
    )
    _llm_model = OpenAIChatModel(
        model_name=model,
        provider=provider
    )
    return _llm_model


def _create_agent_for_session(session_id: str) -> Agent | None:
    """Create an agent with an MCP server connection bound to a specific session."""
    llm_model = _get_llm_model()
    if llm_model is None:
        return None

    # Each session's MCP connection carries the session ID header so the
    # MCP server routes tool calls to the correct DataFrame.
    server = MCPServerSSE(
        url=MCP_SERVER_URL,
        headers={'X-Session-Id': session_id},
    )
    return Agent(llm_model, toolsets=[server], system_prompt=SYSTEM_PROMPT, retries=5)


def _create_agent_without_mcp() -> Agent | None:
    """Create an agent without MCP tools for degraded chat responses."""
    llm_model = _get_llm_model()
    if llm_model is None:
        return None

    return Agent(llm_model, system_prompt=SYSTEM_PROMPT, retries=5)


def _strip_chart_blocks_from_text(text: str) -> str:
    return CHART_DATA_PATTERN.sub(CHART_HISTORY_REPLACEMENT, text)


def _sanitize_message_for_history(message: ModelMessage) -> ModelMessage:
    """Remove chart payloads from text-bearing message parts before storage."""
    parts = getattr(message, 'parts', None)
    if parts is None:
        return message

    sanitized_parts = []
    changed = False
    for part in parts:
        content = getattr(part, 'content', None)
        if isinstance(content, str):
            sanitized_content = _strip_chart_blocks_from_text(content)
            if sanitized_content != content:
                part = replace(part, content=sanitized_content)
                changed = True
        sanitized_parts.append(part)

    if not changed:
        return message
    return replace(message, parts=sanitized_parts)


def _prepare_message_history_for_storage(message_history: list[ModelMessage]) -> list[ModelMessage]:
    return [_sanitize_message_for_history(message) for message in message_history]


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

    clean_text = CHART_DATA_PATTERN.sub(remove_chart_block, response_text).strip()
    if malformed_chart_count:
        clean_text = f"{clean_text}\n\n{MALFORMED_CHART_WARNING}".strip()

    return {
        'text': clean_text,
        'charts': charts,
    }


async def get_agent_response(message: str, session_id: str = 'default') -> dict:
    """Gets a response from the agent, running with MCP servers.
    Returns a dict with 'text' and optional 'charts' list.
    Raises RuntimeError if chat is not configured."""

    if not is_chat_configured():
        raise RuntimeError("Chat is not configured. Please set the required environment variables.")

    current_agent = _create_agent_for_session(session_id)
    if current_agent is None:
        raise RuntimeError("Chat is not configured. Please set the required environment variables.")

    message_history = chat_agent_message_cache.get_messages(session_id)
    message_history[:] = _prepare_message_history_for_storage(message_history)

    logger.info(f"Starting agent response for session {session_id[:8]}...")

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
                raise RuntimeError("Chat is not configured. Please set the required environment variables.")
            result = await fallback_agent.run(message, message_history=message_history)

        logger.debug("Agent run completed successfully")

        # Update this session's history
        new_messages = _prepare_message_history_for_storage(result.new_messages())
        message_history = chat_agent_message_cache.add_messages(session_id, new_messages)
        logger.debug(f"Session {session_id[:8]} history now has {len(message_history)} messages")

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
    logger.info(f"Chat history cleared for session {session_id[:8]}...")

async def main():
    if not is_chat_configured():
        print("Chat not configured. Set LLM_API_KEY and optionally LLM_BASE_URL and LLM_MODEL.")
        return

    current_agent = _create_agent_for_session('default')
    if current_agent is None:
        return

    async with current_agent:
        result = await current_agent.run('What attributes have the strongest correlation?')
    print(result.output)

if __name__ == "__main__":
    asyncio.run(main())
