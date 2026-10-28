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


def get_domino_url():
    return os.environ.get("DOMINO_URL")


def get_domino_external_url():
    return os.environ.get("DOMINO_EXTERNAL_URL")


def get_domino_webvfs_host():
    value = os.environ.get("DOMINO_WEBVFS_HOST")
    return value.strip() if value else value


def get_domino_project_id():
    return os.environ.get("DOMINO_PROJECT_ID")


def get_domino_run_id():
    return os.environ.get("DOMINO_RUN_ID")


def get_domino_remote_file_system_hostport():
    return os.environ.get("DOMINO_REMOTE_FILE_SYSTEM_HOSTPORT")


# ===== CHAT TOOL ACCESS POLICY =====
# See backend/chat_tools/ and README "Chat tool access settings".

CHAT_TOOL_POLICY_FILE_NAME = "chat_tool_policy.json"
# The file lives in a hidden folder so it doesn't show up among the project's data files.
CHAT_TOOL_POLICY_DIR_NAME = ".clinical-data-explorer"
CHAT_TOOL_POLICY_DATASET_FALLBACK = "chat-tool-settings"
CHAT_USER_ONLY_RESULT_MAX_BYTES_DEFAULT = 256 * 1024

# Where Domino mounts a project's datasets, by project type:
# Git-based projects at <DOMINO_DATASETS_DIR, usually /mnt/data>/<name>;
# DFS projects at /domino/datasets/local/<name>.
GIT_DATASETS_ROOT = "/mnt/data"
DFS_DATASETS_ROOT = "/domino/datasets/local"
LOCAL_CHAT_TOOL_POLICY_PATH = ".chat_tool_policy.json"


def get_chat_tool_policy_dataset() -> str:
    """Name of the Domino Dataset that holds the policy file.

    CHAT_TOOL_POLICY_DATASET if set; otherwise the project's *default dataset*, which
    Domino creates for every project and names after the project (DOMINO_PROJECT_NAME),
    so there is nothing for the owner to create."""
    return (
        os.environ.get("CHAT_TOOL_POLICY_DATASET")
        or os.environ.get("DOMINO_PROJECT_NAME")
        or CHAT_TOOL_POLICY_DATASET_FALLBACK
    )


def _dataset_parent_candidates() -> list:
    """Directories that may contain the dataset, most likely first for this project type."""
    git_roots = [os.environ.get("DOMINO_DATASETS_DIR") or GIT_DATASETS_ROOT, GIT_DATASETS_ROOT]
    dfs_roots = [DFS_DATASETS_ROOT]
    # DFS-project executions may point DOMINO_DATASETS_DIR at /domino/datasets.
    datasets_dir = os.environ.get("DOMINO_DATASETS_DIR")
    if datasets_dir:
        dfs_roots.insert(0, os.path.join(datasets_dir, "local"))
    is_git = os.environ.get("DOMINO_IS_GIT_BASED", "true").lower() != "false"
    ordered = git_roots + dfs_roots if is_git else dfs_roots + git_roots
    return list(dict.fromkeys(ordered))


def get_chat_tool_policy_dataset_dir() -> str:
    """Directory of the dataset holding the policy, found by checking where Domino
    mounted it (works for both Git-based and DFS projects). When it isn't mounted
    anywhere, the path the project type would use is returned so errors can name it."""
    dataset = get_chat_tool_policy_dataset()
    candidates = [os.path.join(parent, dataset) for parent in _dataset_parent_candidates()]
    for candidate in candidates:
        if os.path.isdir(candidate):
            return candidate
    return candidates[0]


def get_chat_tool_policy_path_override():
    """CHAT_TOOL_POLICY_PATH with `~` expanded, or None when it is unset or blank.

    A blank value counts as unset, so it can't turn into a file named after
    whitespace in the working directory."""
    raw = (os.environ.get("CHAT_TOOL_POLICY_PATH") or "").strip()
    return os.path.expanduser(raw) if raw else None


def get_chat_tool_policy_path() -> str:
    """Resolve where the chat tool policy file lives. First match wins:

    1. CHAT_TOOL_POLICY_PATH: an explicit full path to the file (an optional override;
       blank counts as unset).
    2. Local dev (no DOMINO_RUN_ID): ./.chat_tool_policy.json
    3. On Domino: `.clinical-data-explorer/chat_tool_policy.json` inside the project's
       default dataset (or CHAT_TOOL_POLICY_DATASET), wherever Domino mounted it.

    The file is seeded with defaults at app start (see `seed_default_policy`), so the
    owner never has to create anything. If the dataset isn't mounted, the store
    reports that instead of silently writing to ephemeral local disk, and the chat
    uses the built-in defaults (see `backend.chat_tools.policy`).
    """
    explicit = get_chat_tool_policy_path_override()
    if explicit:
        return explicit
    if not get_domino_run_id():
        return LOCAL_CHAT_TOOL_POLICY_PATH
    return os.path.join(
        get_chat_tool_policy_dataset_dir(), CHAT_TOOL_POLICY_DIR_NAME, CHAT_TOOL_POLICY_FILE_NAME
    )


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
DOMINO_URL = get_domino_url()
DOMINO_EXTERNAL_URL = get_domino_external_url()
DOMINO_PROJECT_ID = get_domino_project_id()
DOMINO_RUN_ID = get_domino_run_id()
DOMINO_REMOTE_FILE_SYSTEM_HOSTPORT = get_domino_remote_file_system_hostport()
VSCODE_PROXY_URI = get_vscode_proxy_uri()

DATA_FILE_SIZE_LIMIT = int(os.environ.get("DATA_FILE_SIZE_LIMIT_B", 500 * ONE_MB))
DATA_FILE_CACHE_EXPIRATION_SECONDS = int(os.environ.get("DATA_FILE_CACHE_EXPIRATION_SECONDS", 60))
DATA_FILE_CACHE_MAX_ITEM_COUNT = int(os.environ.get("DATA_FILE_CACHE_MAX_ITEM_COUNT", 100))
DATASET_LOAD_REQUEST_QUEUE_MAX_LENGTH = int(os.environ.get("DATASET_LOAD_REQUEST_QUEUE_MAX_LENGTH", 10))
