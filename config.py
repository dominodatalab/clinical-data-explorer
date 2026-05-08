"""Chat agent configuration constants."""

import os

CHAT_AGENT_MESSAGE_HISTORY_CACHE_SIZE_B = int(
    os.environ.get("CHAT_AGENT_MESSAGE_HISTORY_CACHE_SIZE_B", str(500 * 1024 * 1024))
)
CHAT_AGENT_MESSAGE_HISTORY_CAP = int(
    os.environ.get("CHAT_AGENT_MESSAGE_HISTORY_CAP", "100")
)
