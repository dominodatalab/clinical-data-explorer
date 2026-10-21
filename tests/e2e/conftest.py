"""E2E fixtures: start Flask + MCP, make sample fixture visible to the file browser.

Kept separate from tests/conftest.py so the contract suite doesn't depend on
Playwright / a running server.
"""
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
START_SCRIPT = REPO_ROOT / "start_servers.sh"
SAMPLE_CSV = REPO_ROOT / "tests" / "fixtures" / "sample.csv"
DATASETS_DIR = REPO_ROOT / "datasets"
E2E_FIXTURE_NAME = "_e2e_sample.csv"
FLASK_PORT = 8888
MCP_PORT = 3333
FLASK_URL = f"http://localhost:{FLASK_PORT}"
MCP_URL = f"http://localhost:{MCP_PORT}"

_CHILD_ENV_VARS_TO_CLEAR = {
    "API_KEY_OVERRIDE",
    "CHAT_TOOL_POLICY_PATH",
    "FLASK_HOST",
    "LLM_API_KEY",
    "LLM_BASE_URL",
    "LLM_MODEL",
    "MAIN_APP_PORT",
    "MCP_SERVER_URL",
    "OPENAI_API_KEY",
    "VSCODE_PROXY_URI",
}


def _port_open(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.2)
        try:
            s.connect(("127.0.0.1", port))
            return True
        except OSError:
            return False


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _wait_for(url: str, timeout: float = 30.0) -> None:
    """Poll url until it responds 2xx, raise TimeoutError otherwise."""
    import urllib.error
    import urllib.request

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1.0) as resp:
                if 200 <= resp.status < 300:
                    return
        except (urllib.error.URLError, ConnectionError, TimeoutError):
            pass
        time.sleep(0.3)
    raise TimeoutError(f"{url} did not become ready in {timeout}s")


def _stop_process_group(proc: subprocess.Popen | None) -> None:
    if proc is None:
        return
    try:
        os.killpg(os.getpgid(proc.pid), 15)
    except ProcessLookupError:
        return
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        os.killpg(os.getpgid(proc.pid), 9)
        proc.wait(timeout=10)


@pytest.fixture(scope="session")
def live_servers():
    """Start Flask + MCP via start_servers.sh, wait for readiness, teardown at session end.

    Copies tests/fixtures/sample.csv into datasets/ under a predictable name so
    the file browser can see it. Removes the copy at teardown.
    """
    if _port_open(FLASK_PORT) or _port_open(MCP_PORT):
        pytest.skip(
            f"Port {FLASK_PORT} or {MCP_PORT} already in use — stop the existing "
            f"servers before running the E2E suite."
        )

    DATASETS_DIR.mkdir(exist_ok=True)
    fixture_copy = DATASETS_DIR / E2E_FIXTURE_NAME
    shutil.copy(SAMPLE_CSV, fixture_copy)

    child_env = os.environ.copy()
    for name in list(child_env):
        if name.startswith("DOMINO_") or name in _CHILD_ENV_VARS_TO_CLEAR:
            child_env.pop(name, None)

    mock_port = _free_port()
    mock_url = f"http://127.0.0.1:{mock_port}"
    child_env.update({
        "DEV_ACCESS_TOKEN": "cde-e2e-token",
        "DEV_MODE": "dev",
        "DOMINO_API_HOST": mock_url,
        "FLASK_DEBUG": "false",
        "FLASK_HOST": "127.0.0.1",
        "MAIN_APP_PORT": str(FLASK_PORT),
        "MCP_SERVER_URL": MCP_URL,
    })

    mock_proc = None
    app_proc = None
    try:
        mock_proc = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "mock_server.app:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(mock_port),
            ],
            cwd=str(REPO_ROOT),
            env=child_env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            preexec_fn=os.setsid,
        )
        _wait_for(f"{mock_url}/api/users/v1/self", timeout=30.0)

        app_proc = subprocess.Popen(
            ["bash", str(START_SCRIPT)],
            cwd=str(REPO_ROOT),
            env=child_env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            preexec_fn=os.setsid,
        )
        _wait_for(f"{MCP_URL}/", timeout=45.0)
        _wait_for(f"{FLASK_URL}/", timeout=45.0)
        yield {"flask_url": FLASK_URL, "fixture_name": E2E_FIXTURE_NAME}
    finally:
        _stop_process_group(app_proc)
        _stop_process_group(mock_proc)
        if fixture_copy.exists():
            fixture_copy.unlink()
