from functools import lru_cache
import time

from pydantic_ai.messages import ModelMessage

import config as chat_agent_config

"""
messages should be indexed by session_id and dataset url, so that chat history from two different datasets don't mix
"""


@lru_cache(1)
def get_cache() -> dict[str, list[ModelMessage]]:
    """Get per-session message histories."""
    return {}


@lru_cache(1)
def get_cache_last_touched() -> dict[str, float]:
    """Get per-session message history last-touch timestamps."""
    return {}


def add_messages(session_id: str, messages: list[ModelMessage]) -> list[ModelMessage]:
    _expire_history(session_id)
    message_histories = get_cache()
    message_history = message_histories.setdefault(session_id, [])
    message_history.extend(messages)
    _cap_message_history(message_history)
    _touch_history(session_id)
    return message_history


def get_messages(session_id: str) -> list[ModelMessage]:
    _expire_history(session_id)
    message_histories = get_cache()
    message_history = message_histories.setdefault(session_id, [])
    _cap_message_history(message_history)
    _touch_history(session_id)
    return message_history


def clear_messages(session_id: str) -> None:
    get_cache().pop(session_id, None)
    get_cache_last_touched().pop(session_id, None)


def _cap_message_history(message_history: list[ModelMessage]) -> None:
    cap = max(chat_agent_config.CHAT_AGENT_MESSAGE_HISTORY_CAP, 0)
    if cap == 0:
        message_history.clear()
    elif len(message_history) > cap:
        del message_history[:-cap]


def _expire_history(session_id: str) -> None:
    ttl_seconds = chat_agent_config.CHAT_AGENT_MESSAGE_HISTORY_TTL_SECONDS
    if ttl_seconds < 0:
        return

    last_touched = get_cache_last_touched().get(session_id)
    if last_touched is None:
        return

    if time.monotonic() - last_touched >= ttl_seconds:
        clear_messages(session_id)


def _touch_history(session_id: str) -> None:
    get_cache_last_touched()[session_id] = time.monotonic()
