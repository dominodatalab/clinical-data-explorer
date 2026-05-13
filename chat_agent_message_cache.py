import sys
import threading

from cachetools import LRUCache
from pydantic_ai.messages import ModelMessage

import config as chat_agent_config

"""
This cache saves chat message histories per session ID, which is linked to the cookie in a user's browser.
"""


_message_histories: LRUCache[str, list[ModelMessage]] = LRUCache(
    maxsize=max(chat_agent_config.CHAT_AGENT_MESSAGE_HISTORY_CACHE_SIZE_B, 1),
    getsizeof=sys.getsizeof,
)
_cache_lock = threading.Lock()


def get_cache() -> LRUCache[str, list[ModelMessage]]:
    """Get per-session message histories."""
    return _message_histories


def clear_cache() -> None:
    with _cache_lock:
        get_cache().clear()


def add_messages(session_id: str, messages: list[ModelMessage]) -> list[ModelMessage]:
    with _cache_lock:
        message_history = _get_message_history(session_id)
        message_history.extend(messages)
        _cap_message_history(message_history)
        _save_message_history(session_id, message_history)
        return message_history


def get_messages(session_id: str) -> list[ModelMessage]:
    with _cache_lock:
        message_history = _get_message_history(session_id)
        _cap_message_history(message_history)
        _save_message_history(session_id, message_history)
        return message_history


def clear_messages(session_id: str) -> None:
    with _cache_lock:
        get_cache().pop(session_id, None)


def _get_message_history(session_id: str) -> list[ModelMessage]:
    message_histories = get_cache()
    message_history = message_histories.get(session_id)
    if message_history is None:
        message_history = []
        message_histories[session_id] = message_history
    return message_history


def _save_message_history(session_id: str, message_history: list[ModelMessage]) -> None:
    get_cache()[session_id] = message_history


def _cap_message_history(message_history: list[ModelMessage]) -> None:
    cap = max(chat_agent_config.CHAT_AGENT_MESSAGE_HISTORY_CAP, 0)
    if cap == 0:
        message_history.clear()
    elif len(message_history) > cap:
        del message_history[:-cap]
