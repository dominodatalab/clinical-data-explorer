import importlib

from cachetools import TTLCache

from backend import config
import backend.services.current_dataset_context as context_module


def test_current_dataset_context_cache_is_ttl_lru_and_keyed_by_user(monkeypatch):
    monkeypatch.setattr(config, "CURRENT_DATASET_CONTEXT_CACHE_TTL_SECONDS", 86400)
    monkeypatch.setattr(config, "CURRENT_DATASET_CONTEXT_CACHE_MAX_ITEM_COUNT", 2)
    context_module.get_current_dataset_context_cache.cache_clear()

    first = context_module.CurrentDatasetContext(dataset="Study/adsl.csv", dataset_id="ds-1")
    second = context_module.CurrentDatasetContext(dataset="Study/adae.csv", dataset_id="ds-2")

    context_module.set_current_dataset_context("user-1", first)
    context_module.set_current_dataset_context("user-2", second)

    cache = context_module.get_current_dataset_context_cache()
    assert isinstance(cache, TTLCache)
    assert cache.ttl == 86400
    assert cache.maxsize == 2
    assert context_module.get_current_dataset_context("user-1") == first
    assert context_module.get_current_dataset_context("user-2") == second

    context_module.get_current_dataset_context_cache.cache_clear()


def test_current_dataset_context_default_ttl_is_24_hours(monkeypatch):
    monkeypatch.delenv("CURRENT_DATASET_CONTEXT_CACHE_TTL_SECONDS", raising=False)
    reloaded_config = importlib.reload(config)
    reloaded_context_module = importlib.reload(context_module)
    reloaded_context_module.get_current_dataset_context_cache.cache_clear()

    cache = reloaded_context_module.get_current_dataset_context_cache()

    assert cache.ttl == 24 * 60 * 60

    importlib.reload(reloaded_config)
    importlib.reload(reloaded_context_module)
