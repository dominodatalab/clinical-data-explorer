"""Backend configuration module.

Environment-backed settings for the Flask backend, chat agent, and backend
services live here so they can be audited from one place.
"""
import logging
import os

ONE_MB = 1024 * 1024


def _env_bool(name: str, default: bool) -> bool:
    return os.environ.get(name, str(default).lower()).lower() == "true"


def get_log_level():
    return os.environ.get("LOG_LEVEL", logging.INFO)


def get_verbose_logging() -> bool:
    return _env_bool("VERBOSE_LOGGING", False)


def get_flask_host() -> str:
    return os.environ.get("FLASK_HOST", "0.0.0.0")


def get_flask_debug() -> bool:
    return _env_bool("FLASK_DEBUG", True)


def get_flask_secret_key():
    return os.environ.get("FLASK_SECRET_KEY")


def get_mcp_server_url() -> str:
    return os.environ.get("MCP_SERVER_URL", "http://localhost:3333")


def get_mcp_server_mcp_url() -> str:
    return f"{get_mcp_server_url().rstrip('/')}/mcp"


def get_mcp_request_timeout_seconds() -> float:
    return float(os.environ.get("MCP_REQUEST_TIMEOUT_SECONDS", "120"))


def get_llm_config():
    """Return (base_url, api_key, model) for the direct OpenAI-compatible path.

    This is the *non-Gateway* configuration. See the Domino LLM Gateway
    getters below; `chat_agent.get_llm_config()` picks between the two.
    """
    return (
        os.environ.get("LLM_BASE_URL", "https://api.openai.com/v1"),
        os.environ.get("LLM_API_KEY") or os.environ.get("OPENAI_API_KEY"),
        os.environ.get("LLM_MODEL", "gpt-4o-mini"),
    )


# ===== DOMINO LLM GATEWAY =====
# The Gateway is OpenAI-API-compatible, so it reuses the OpenAI provider. What
# differs is auth: the bearer token is an ephemeral Domino access token that has
# to be re-fetched per request from the sidecar below (see
# `chat_agent.DominoAccessTokenAuth`).
#
# These are deliberately read per call rather than snapshotted into module-level
# constants like the settings above, because the auth flow re-reads
# API_KEY_OVERRIDE on every outbound request.

DOMINO_TOKEN_URL_DEFAULT = "http://localhost:8899/access-token"


def get_domino_llm_gateway_url():
    """Gateway App base URL, e.g. https://<deploy>/apps/<app-id> (with or without /v1)."""
    return os.environ.get("DOMINO_LLM_GATEWAY_URL")


def get_domino_llm_gateway_model():
    """Model alias as registered in the Gateway App, e.g. gpt-5.4-nano."""
    return os.environ.get("DOMINO_LLM_GATEWAY_MODEL")


def get_domino_llm_gateway_token_url():
    """Domino access-token sidecar URL. Only overridden for local development —
    real Domino workspaces/jobs/apps always expose it on localhost:8899."""
    return os.environ.get("DOMINO_LLM_GATEWAY_TOKEN_URL", DOMINO_TOKEN_URL_DEFAULT)


def get_api_key_override():
    """Standard Domino-app convention: when set, used verbatim as the bearer
    token for Gateway calls and the sidecar is skipped entirely."""
    return os.environ.get("API_KEY_OVERRIDE")


def get_dev_access_token():
    return os.environ.get("DEV_ACCESS_TOKEN")


def get_domino_api_host_override():
    return os.environ.get("DOMINO_API_HOST_OVERD")


def get_domino_api_host():
    return os.environ.get("DOMINO_API_HOST")


def get_domino_external_url():
    return os.environ.get("DOMINO_EXTERNAL_URL")


def get_domino_project_id():
    return os.environ.get("DOMINO_PROJECT_ID")


def get_domino_run_id():
    return os.environ.get("DOMINO_RUN_ID")


def get_domino_remote_file_system_hostport():
    return os.environ.get("DOMINO_REMOTE_FILE_SYSTEM_HOSTPORT")


# ===== CHAT TOOL ACCESS POLICY =====
# See backend/chat_tools/ and README "Chat tool access settings".

CHAT_TOOL_POLICY_FILE_NAME = "chat_tool_policy.json"
CHAT_TOOL_POLICY_DATASET_DEFAULT = "chat-tool-settings"
CHAT_USER_ONLY_RESULT_MAX_BYTES_DEFAULT = 256 * 1024

# (directory whose presence means "this kind of project", where its datasets are
# mounted). Git-based projects mount datasets at /mnt/data/<name>; DFS projects
# at /domino/datasets/local/<name>.
DOMINO_DATASET_ROOTS = (
    ("/mnt/data", "/mnt/data"),
    ("/domino/datasets", "/domino/datasets/local"),
)
LOCAL_CHAT_TOOL_POLICY_PATH = ".chat_tool_policy.json"


def get_chat_tool_policy_dataset() -> str:
    """Name of the Domino Dataset (in the extension's project) holding the policy file."""
    return os.environ.get("CHAT_TOOL_POLICY_DATASET") or CHAT_TOOL_POLICY_DATASET_DEFAULT


def get_chat_tool_policy_path() -> str:
    """Resolve where the chat tool policy file lives. First match wins:

    1. CHAT_TOOL_POLICY_PATH: an explicit full path to the file.
    2. On Domino, the mounted policy dataset: /mnt/data/<dataset> (Git-based
       projects) or /domino/datasets/local/<dataset> (DFS projects), whichever
       has the dataset mounted.
    3. Local dev (no DOMINO_RUN_ID): ./.chat_tool_policy.json

    On Domino the returned path may not exist yet (dataset not mounted); the
    store reports that rather than silently writing to ephemeral local disk.
    """
    explicit = os.environ.get("CHAT_TOOL_POLICY_PATH")
    if explicit:
        return explicit

    if not get_domino_run_id():
        return LOCAL_CHAT_TOOL_POLICY_PATH

    dataset = get_chat_tool_policy_dataset()
    roots = [(probe, parent) for probe, parent in DOMINO_DATASET_ROOTS if os.path.isdir(probe)]
    for _probe, parent in roots:
        if os.path.isdir(os.path.join(parent, dataset)):
            return os.path.join(parent, dataset, CHAT_TOOL_POLICY_FILE_NAME)
    parent = roots[0][1] if roots else DOMINO_DATASET_ROOTS[0][1]
    return os.path.join(parent, dataset, CHAT_TOOL_POLICY_FILE_NAME)


def get_chat_tool_admin_usernames() -> set:
    """Lower-cased usernames that count as chat-tool admins regardless of project role.

    For local dev (no Domino project) and as a break-glass option."""
    raw = os.environ.get("CHAT_TOOL_ADMIN_USERNAMES", "")
    return {name.strip().lower() for name in raw.split(",") if name.strip()}


def get_chat_user_only_result_max_bytes() -> int:
    """Cap on one hidden-from-AI tool result kept in the chat history cache."""
    return int(os.environ.get("CHAT_USER_ONLY_RESULT_MAX_BYTES", CHAT_USER_ONLY_RESULT_MAX_BYTES_DEFAULT))


def get_vscode_proxy_uri():
    return os.environ.get("VSCODE_PROXY_URI")


LOG_LEVEL = get_log_level()
VERBOSE_LOGGING = get_verbose_logging()

FLASK_HOST = get_flask_host()
FLASK_DEBUG = get_flask_debug()
FLASK_SECRET_KEY = get_flask_secret_key()

MCP_SERVER_URL = get_mcp_server_url()
MCP_SERVER_MCP_URL = get_mcp_server_mcp_url()
MCP_REQUEST_TIMEOUT_SECONDS = get_mcp_request_timeout_seconds()

LLM_BASE_URL, LLM_API_KEY, LLM_MODEL = get_llm_config()

DEV_ACCESS_TOKEN = get_dev_access_token()
DOMINO_API_HOST_OVERRIDE = get_domino_api_host_override()
DOMINO_API_HOST = get_domino_api_host()
DOMINO_EXTERNAL_URL = get_domino_external_url()
DOMINO_PROJECT_ID = get_domino_project_id()
DOMINO_RUN_ID = get_domino_run_id()
DOMINO_REMOTE_FILE_SYSTEM_HOSTPORT = get_domino_remote_file_system_hostport()
VSCODE_PROXY_URI = get_vscode_proxy_uri()

DATA_FILE_SIZE_LIMIT = int(os.environ.get("DATA_FILE_SIZE_LIMIT_B", 500 * ONE_MB))
DATA_FILE_CACHE_EXPIRATION_SECONDS = int(os.environ.get("DATA_FILE_CACHE_EXPIRATION_SECONDS", 60))
DATA_FILE_CACHE_MAX_ITEM_COUNT = int(os.environ.get("DATA_FILE_CACHE_MAX_ITEM_COUNT", 100))
DATASET_LOAD_REQUEST_QUEUE_MAX_LENGTH = int(os.environ.get("DATASET_LOAD_REQUEST_QUEUE_MAX_LENGTH", 10))
