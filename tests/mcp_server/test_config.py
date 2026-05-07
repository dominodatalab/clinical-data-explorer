import importlib

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
