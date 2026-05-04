"""Unit tests for filesystem cleanup behavior in DownloadFileMetadataCache."""

import importlib

import backend.services.download_file_metadata_cache as download_file_metadata_cache_module

from pathlib import Path

from backend.services.download_file_metadata_cache import DownloadFileMetadataCache


def test_expire_removes_expired_file_from_filesystem(tmp_path):
    now = [100.0]
    cache = DownloadFileMetadataCache(temp_root=tmp_path, maxsize=10, ttl=1, timer=lambda: now[0])

    cached_file = cache.set(
        source_type="dataset",
        dataset_id="ds-1",
        snapshot_id="snap-1",
        file_name="nested/adsl.csv",
    )
    cached_file.write_text("expired contents", encoding="utf-8")

    assert cached_file.exists()

    # Advance past the TTL and explicitly expire entries so the cache runs its
    # filesystem cleanup hook for the stale path.
    now[0] = 102.0
    expired = cache.expire()

    assert len(expired) == 1
    assert not cached_file.exists()
    assert not cached_file.parent.exists()


def test_popitem_removes_file_when_cache_exceeds_maxsize(tmp_path):
    cache = DownloadFileMetadataCache(temp_root=tmp_path, maxsize=1, ttl=60)

    evicted_file = cache.set(
        source_type="dataset",
        dataset_id="ds-1",
        snapshot_id="snap-1",
        file_name="adsl.csv",
    )
    evicted_file.write_text("first contents", encoding="utf-8")

    surviving_file = cache.set(
        source_type="dataset",
        dataset_id="ds-2",
        snapshot_id="snap-2",
        file_name="adae.csv",
    )

    # Adding the second entry forces TTLCache to evict the older one, which
    # should trigger DownloadFileMetadataCache.popitem() and delete the old file on disk.
    assert not evicted_file.exists()
    assert surviving_file == Path(tmp_path) / "domino_api_datasets" / "dataset" / "ds-2" / "snap-2" / "adae.csv"
    assert surviving_file.parent.exists()


def test_get_file_cache_uses_cache_config_environment_variables(monkeypatch):
    monkeypatch.setenv("DATA_FILE_CACHE_EXPIRATION_SECONDS", "7")
    monkeypatch.setenv("DATA_FILE_CACHE_MAX_ITEM_COUNT", "3")

    # These settings are read when the module is imported, so reload it after
    # changing the environment and then construct the singleton cache.
    reloaded_module = importlib.reload(download_file_metadata_cache_module)
    reloaded_module.get_file_cache.cache_clear()

    cache = reloaded_module.get_file_cache()

    assert reloaded_module.EXPIRATION_SECONDS == 7
    assert reloaded_module.MAX_ITEM_COUNT == 3
    assert cache.ttl == 7
    assert cache.maxsize == 3

    # cleanup
    monkeypatch.delenv("DATA_FILE_CACHE_EXPIRATION_SECONDS", raising=False)
    monkeypatch.delenv("DATA_FILE_CACHE_MAX_ITEM_COUNT", raising=False)
    importlib.reload(reloaded_module).get_file_cache.cache_clear()
