import os
import sys
from functools import lru_cache

from cachetools import LRUCache

DEFAULT_MAX_CACHE_SIZE_BYTES = 1024 * 1024 * 1024
MAX_CACHE_SIZE = int(os.environ.get('MCP_SERVER_DATAFRAME_CACHE_SIZE_B', DEFAULT_MAX_CACHE_SIZE_BYTES))

"""
This is for caching pandas dataframes
The default max size for the cache is 500 mb
"""

@lru_cache(maxsize=1)
def get_cache():
    """
    Returns singleton cache instance
    """
    return LRUCache(maxsize=MAX_CACHE_SIZE, getsizeof=sys.getsizeof)
