import importlib

import pytest

import mcp_server.config as config_module


def test_mcp_session_environment_values_are_ints(monkeypatch):
    monkeypatch.setenv("MCP_SESSION_MAX_AGE", "7")
    monkeypatch.setenv("MCP_SESSION_MAX_COUNT", "3")

    reloaded_module = importlib.reload(config_module)

    assert reloaded_module.SESSION_MAX_AGE == 7
    assert isinstance(reloaded_module.SESSION_MAX_AGE, int)
    assert reloaded_module.SESSION_MAX_COUNT == 3
    assert isinstance(reloaded_module.SESSION_MAX_COUNT, int)

    monkeypatch.delenv("MCP_SESSION_MAX_AGE", raising=False)
    monkeypatch.delenv("MCP_SESSION_MAX_COUNT", raising=False)
    importlib.reload(reloaded_module)


def test_mcp_worker_count_reads_environment(monkeypatch):
    monkeypatch.setenv("MCP_WORKERS", "3")

    reloaded_module = importlib.reload(config_module)

    assert reloaded_module.MCP_WORKERS == 3

    monkeypatch.delenv("MCP_WORKERS", raising=False)
    importlib.reload(reloaded_module)


def test_mcp_worker_count_reads_uvicorn_cli_argument(monkeypatch):
    monkeypatch.delenv("MCP_WORKERS", raising=False)
    monkeypatch.delenv("WEB_CONCURRENCY", raising=False)
    monkeypatch.setattr(config_module.sys, "argv", ["uvicorn", "mcp_server.app:app", "--workers", "4"])

    reloaded_module = importlib.reload(config_module)

    assert reloaded_module.MCP_WORKERS == 4

    monkeypatch.setattr(config_module.sys, "argv", ["pytest"])
    importlib.reload(reloaded_module)


def test_mcp_app_rejects_multiple_workers_without_cache_server(monkeypatch):
    monkeypatch.setattr(config_module, "MCP_WORKERS", 1)
    monkeypatch.setattr(config_module, "MCP_CACHE_SERVER_URL", None)
    import mcp_server.app as app_module

    monkeypatch.setattr(app_module.config, "MCP_WORKERS", 2)
    monkeypatch.setattr(app_module.config, "MCP_CACHE_SERVER_URL", None)

    with pytest.raises(RuntimeError, match="MCP_CACHE_SERVER_URL is not set"):
        app_module._validate_cache_service_configuration()


def test_mcp_app_allows_multiple_workers_with_cache_server(monkeypatch):
    monkeypatch.setattr(config_module, "MCP_WORKERS", 1)
    monkeypatch.setattr(config_module, "MCP_CACHE_SERVER_URL", None)
    import mcp_server.app as app_module

    monkeypatch.setattr(app_module.config, "MCP_WORKERS", 2)
    monkeypatch.setattr(app_module.config, "MCP_CACHE_SERVER_URL", "http://127.0.0.1:3332")

    app_module._validate_cache_service_configuration()
