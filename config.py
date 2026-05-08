"""Chat agent configuration constants."""

import os

CHAT_AGENT_MESSAGE_HISTORY_CAP = int(
    os.environ.get("CHAT_AGENT_MESSAGE_HISTORY_CAP", "100")
)
CHAT_AGENT_MESSAGE_HISTORY_TTL_SECONDS = int(
    os.environ.get("CHAT_AGENT_MESSAGE_HISTORY_TTL_SECONDS", str(24 * 60 * 60))
)
