import os
import sys
from functools import lru_cache
import pandas as pd

from cachetools import LRUCache

DEFAULT_MAX_CACHE_SIZE_BYTES = 1024 * 1024 * 1024
MAX_CACHE_SIZE = int(os.environ.get('MCP_SERVER_DATAFRAME_CACHE_SIZE_B', DEFAULT_MAX_CACHE_SIZE_BYTES))

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
        target_cache[file_snapshot_path] = dataframe
    except ValueError as exc:
        if str(exc) == "value too large":
            raise DataFrameCacheValueTooLarge(file_snapshot_path) from exc
        raise
