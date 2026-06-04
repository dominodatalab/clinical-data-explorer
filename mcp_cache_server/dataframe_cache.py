import sys
from functools import lru_cache
import threading

import pandas as pd
from cachetools import LRUCache

from mcp_server import config

DEFAULT_MAX_CACHE_SIZE_BYTES = config.DEFAULT_DATAFRAME_CACHE_SIZE_BYTES
MAX_CACHE_SIZE = config.DATAFRAME_CACHE_SIZE_BYTES
_cache_lock = threading.RLock()

"""
This is for caching pandas dataframes
The default max size for the cache is 500 mb
"""


class DataFrameCacheValueTooLarge(RuntimeError):
    """Raised when a single value cannot fit in the dataframe cache."""

    def __init__(self, cache_key: str):
        self.cache_key = cache_key
        super().__init__(
            f"Dataset '{cache_key}' is too large to load right now. "
            "Try a smaller file or ask your administrator to increase the amount of memory available."
        )


@lru_cache(maxsize=1)
def get_cache():
    """
    Returns singleton cache instance
    """
    return LRUCache(maxsize=MAX_CACHE_SIZE, getsizeof=sys.getsizeof)


def save_to_cache(file_snapshot_path: str, dataframe: pd.DataFrame) -> None:
    """Save a dataframe to the cache, reporting oversized values clearly."""
    target_cache = get_cache()
    try:
        with _cache_lock:
            target_cache[file_snapshot_path] = dataframe
    except ValueError as exc:
        if str(exc) == "value too large":
            raise DataFrameCacheValueTooLarge(file_snapshot_path) from exc
        raise


def get_from_cache(file_snapshot_path: str) -> pd.DataFrame | None:
    """Read a dataframe from the singleton cache."""
    with _cache_lock:
        return get_cache().get(file_snapshot_path)
