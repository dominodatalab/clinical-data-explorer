"""Chat blueprint — proxies the Pydantic-AI chat agent.

Extracted from `backend/app.py` (REFACTOR_PLAN.md §1, step 1.5a). Owns the
three `/chat*` endpoints. Behavior is preserved verbatim: same paths,
same request/response shapes, same status codes, same logging messages,
same error-classification heuristics.
"""
import asyncio
import logging
import traceback

from flask import Blueprint, jsonify, request

from chat_agent import (
    clear_history,
    get_agent_response,
    get_chat_status,
    is_chat_configured,
)

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


@bp.route('/chat', methods=['POST'])
def chat():
    # Check if chat is configured first
    if not is_chat_configured():
        logger.warning("Chat request received but chat is not configured")
        return jsonify({
            'error': 'Chat is not configured',
            'error_detail': 'Please set the required environment variables (LLM_API_KEY, and optionally LLM_BASE_URL and LLM_MODEL) to enable the chat feature.',
            'error_type': 'NotConfigured'
        }), 503

    user_message = request.json.get('message')
    if not user_message:
        logger.warning("Chat request received with no message")
        return jsonify({'error': 'No message provided'}), 400

    message_length = len(user_message)
    logger.info(f"Processing chat message with {message_length} characters")

    # Get response from the chat agent using the async function
    try:
        agent_response = asyncio.run(get_agent_response(user_message, session_id=get_session_id()))
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
