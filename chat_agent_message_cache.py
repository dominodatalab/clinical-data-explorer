from functools import lru_cache

from pydantic_ai.messages import ModelMessage

import config as chat_agent_config

"""
TODO there should be an expiration on this message cache
messages should be indexed by session_id and dataset url, so that chat history from two different datasets don't mix
"""


@lru_cache(1)
def get_cache() -> dict[str, list[ModelMessage]]:
    """Get per-session message histories."""
    return {}


def add_messages(session_id: str, messages: list[ModelMessage]) -> list[ModelMessage]:
    message_histories = get_cache()
    message_history = message_histories.setdefault(session_id, [])
    message_history.extend(messages)
    _cap_message_history(message_history)
    return message_history


def get_messages(session_id: str) -> list[ModelMessage]:
    message_histories = get_cache()
    message_history = message_histories.setdefault(session_id, [])
    _cap_message_history(message_history)
    return message_history


def clear_messages(session_id: str) -> None:
    get_cache().pop(session_id, None)


def _cap_message_history(message_history: list[ModelMessage]) -> None:
    cap = max(chat_agent_config.CHAT_AGENT_MESSAGE_HISTORY_CAP, 0)
    if cap == 0:
        message_history.clear()
    elif len(message_history) > cap:
        del message_history[:-cap]
