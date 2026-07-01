import importlib

import mcp_server.config as config_module


def test_mcp_session_environment_values_are_ints(monkeypatch):
    monkeypatch.setenv("MCP_SESSION_MAX_AGE", "7")
    monkeypatch.setenv("MCP_DATAFRAME_MAX_AGE", "5")
    monkeypatch.setenv("MCP_DATASET_RELOAD_CONTEXT_MAX_AGE", "11")
    monkeypatch.setenv("MCP_SESSION_MAX_COUNT", "3")

    reloaded_module = importlib.reload(config_module)

    assert reloaded_module.SESSION_MAX_AGE == 7
    assert isinstance(reloaded_module.SESSION_MAX_AGE, int)
    assert reloaded_module.DATAFRAME_MAX_AGE == 5
    assert isinstance(reloaded_module.DATAFRAME_MAX_AGE, int)
    assert reloaded_module.DATASET_RELOAD_CONTEXT_MAX_AGE == 11
    assert isinstance(reloaded_module.DATASET_RELOAD_CONTEXT_MAX_AGE, int)
    assert reloaded_module.SESSION_MAX_COUNT == 3
    assert isinstance(reloaded_module.SESSION_MAX_COUNT, int)

    monkeypatch.delenv("MCP_SESSION_MAX_AGE", raising=False)
    monkeypatch.delenv("MCP_DATAFRAME_MAX_AGE", raising=False)
    monkeypatch.delenv("MCP_DATASET_RELOAD_CONTEXT_MAX_AGE", raising=False)
    monkeypatch.delenv("MCP_SESSION_MAX_COUNT", raising=False)
    default_module = importlib.reload(reloaded_module)
    assert default_module.DATASET_RELOAD_CONTEXT_MAX_AGE == 86400
