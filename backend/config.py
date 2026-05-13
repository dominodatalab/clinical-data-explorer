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
    return (
        os.environ.get("LLM_BASE_URL", "https://api.openai.com/v1"),
        os.environ.get("LLM_API_KEY") or os.environ.get("OPENAI_API_KEY"),
        os.environ.get("LLM_MODEL", "gpt-4o-mini"),
    )


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


def get_domino_remote_file_system_hostport():
    return os.environ.get("DOMINO_REMOTE_FILE_SYSTEM_HOSTPORT")


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
DOMINO_REMOTE_FILE_SYSTEM_HOSTPORT = get_domino_remote_file_system_hostport()
VSCODE_PROXY_URI = get_vscode_proxy_uri()

DATA_FILE_SIZE_LIMIT = int(os.environ.get("DATA_FILE_SIZE_LIMIT_B", 500 * ONE_MB))
DATA_FILE_CACHE_EXPIRATION_SECONDS = int(os.environ.get("DATA_FILE_CACHE_EXPIRATION_SECONDS", 60))
DATA_FILE_CACHE_MAX_ITEM_COUNT = int(os.environ.get("DATA_FILE_CACHE_MAX_ITEM_COUNT", 100))
DATASET_LOAD_REQUEST_QUEUE_MAX_LENGTH = int(os.environ.get("DATASET_LOAD_REQUEST_QUEUE_MAX_LENGTH", 10))
