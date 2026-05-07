import importlib

import mcp_server.dataframe_cache as dataframe_cache_module


def test_dataframe_cache_size_environment_value_is_int(monkeypatch):
    monkeypatch.setenv("MCP_SERVER_DATAFRAME_CACHE_SIZE_B", "12345")

    reloaded_module = importlib.reload(dataframe_cache_module)
    reloaded_module.get_cache.cache_clear()

    cache = reloaded_module.get_cache()

    assert reloaded_module.MAX_CACHE_SIZE == 12345
    assert isinstance(reloaded_module.MAX_CACHE_SIZE, int)
    assert cache.maxsize == 12345

    monkeypatch.delenv("MCP_SERVER_DATAFRAME_CACHE_SIZE_B", raising=False)
    importlib.reload(reloaded_module).get_cache.cache_clear()
