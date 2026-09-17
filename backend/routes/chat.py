"""Chat blueprint — proxies the Pydantic-AI chat agent.

Extracted from `backend/app.py` (REFACTOR_PLAN.md §1, step 1.5a). Owns the
four `/chat*` endpoints. Behavior is preserved verbatim: same paths,
same request/response shapes, same status codes, same logging messages,
same error-classification heuristics.
"""
import asyncio
import logging
import traceback

from flask import Blueprint, jsonify, request

from chat_agent import (
    NOT_CONFIGURED_ERROR_DETAIL,
    clear_history,
    get_agent_response,
    get_history,
    get_chat_status,
    is_chat_configured,
    reset_gateway_passthrough_token,
    set_gateway_passthrough_token,
)

from backend.auth import get_passthrough_token
from backend.session import get_session_id

logger = logging.getLogger(__name__)

bp = Blueprint('chat', __name__)


@bp.route('/chat/status', methods=['GET'])
def chat_status():
    """Check if chat is configured and return status information."""
    status = get_chat_status()
    return jsonify(status)


@bp.route('/chat/clear', methods=['POST'])
def chat_clear():
    """Clear the chat conversation history."""
    clear_history(session_id=get_session_id())
    return jsonify({'status': 'ok'})


@bp.route('/chat/history', methods=['GET'])
def chat_history():
    """Return the current session's chat transcript."""
    return jsonify({'messages': get_history(session_id=get_session_id())})


@bp.route('/chat', methods=['POST'])
def chat():
    # Check if chat is configured first
    if not is_chat_configured():
        logger.warning("Chat request received but chat is not configured")
        return jsonify({
            'error': 'Chat is not configured',
            'error_detail': NOT_CONFIGURED_ERROR_DETAIL,
            'error_type': 'NotConfigured'
        }), 503

    user_message = request.json.get('message')
    if not user_message:
        logger.warning("Chat request received with no message")
        return jsonify({'error': 'No message provided'}), 400

    message_length = len(user_message)
    logger.info(f"Processing chat message with {message_length} characters")

    # Get response from the chat agent using the async function
    token = get_passthrough_token()

    # Forward the visiting user's JWT into the Domino LLM Gateway auth flow so
    # the Gateway audit log attributes the call to the user rather than the app
    # owner, matching what the datasets/governance calls already do. In classic
    # App mode there is no inbound JWT and the auth flow falls back to the
    # access-token sidecar (app owner). Must be reset in `finally` — see
    # reset_gateway_passthrough_token.
    passthrough_handle = set_gateway_passthrough_token(token)
    try:
        agent_response = asyncio.run(get_agent_response(
            user_message,
            session_id=get_session_id(),
            authorization_header=f'Bearer {token}' if token else None,
        ))
        # agent_response is now a dict with 'text' and 'charts' keys
        logger.info("Successfully got agent response")
        return jsonify({
            'response': agent_response['text'],
            'charts': agent_response.get('charts', [])
        })
    except RuntimeError as e:
        # Chat not configured error
        logger.warning(f"Chat not configured: {str(e)}")
        return jsonify({
            'error': 'Chat is not configured',
            'error_detail': str(e),
            'error_type': 'NotConfigured'
        }), 503
    except Exception as e:
        # Log the full exception with traceback
        error_msg = f"Error getting agent response: {str(e)}"
        logger.error(error_msg)
        logger.error(f"Full traceback:\n{traceback.format_exc()}")

        # Provide more specific error message based on exception type
        error_type = type(e).__name__
        if 'openai' in str(type(e).__module__).lower() or 'OpenAI' in error_type:
            error_detail = f"LLM API Error ({error_type}): {str(e)}"
            logger.error(f"LLM API error detected: {error_detail}")
        elif 'httpx' in str(type(e).__module__).lower() or 'requests' in str(type(e).__module__).lower():
            error_detail = f"Network Error ({error_type}): {str(e)}"
            logger.error(f"Network error detected: {error_detail}")
        elif 'timeout' in str(e).lower():
            error_detail = f"Timeout Error: {str(e)}"
            logger.error(f"Timeout detected: {error_detail}")
        else:
            error_detail = f"Unexpected Error ({error_type}): {str(e)}"

        return jsonify({
            'error': 'Error getting agent response',
            'error_detail': error_detail,
            'error_type': error_type
        }), 500
    finally:
        reset_gateway_passthrough_token(passthrough_handle)
