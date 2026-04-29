from functools import lru_cache
import os
import shutil

from cachetools import TTLCache

from backend.types import SourceType

EXPIRATION_SECONDS = 60
MAX_ITEM_COUNT = 100

def create_key(dataset_id: str, file_name: str, source_type: SourceType = 'dataset', snapshot_id: str = "unset_snapshot_id") -> str:
    return f"{dataset_id}_{file_name}_{source_type}__{snapshot_id}"

"""
This stores file metadata references for downloaded files
This is here to cleanup files in the case that they have not already been
cleaned up after use
"""
class DataFileCache(TTLCache):
    @staticmethod
    def _cleanup_path(path):
        if os.path.isdir(path):
            shutil.rmtree(path, ignore_errors=True)
        elif os.path.exists(path):
            try:
                os.remove(path)
            except FileNotFoundError:
                pass

    def __delitem__(self, key):
        value = super().__getitem__(key)
        super().__delitem__(key)
        self._cleanup_path(value)

    def popitem(self):
        key, value = super().popitem()
        self._cleanup_path(value)
        return key, value

    def expire(self, time=None):
        expired = super().expire(time)
        for _, value in expired:
            self._cleanup_path(value)
        return expired

@lru_cache(maxsize=1)
def get_file_cache():
    """
    Returns singleton metadata file cache
    """
    return DataFileCache(maxsize=MAX_ITEM_COUNT, ttl=EXPIRATION_SECONDS)
