from functools import lru_cache
import os
import shutil
import tempfile
from pathlib import Path
from typing import Optional

from cachetools import TTLCache

from backend.types import SourceType

EXPIRATION_SECONDS = 60
MAX_ITEM_COUNT = 100


"""
This stores file metadata references for downloaded files
This is here to cleanup files in the case that they have not already been
cleaned up after use
"""
class DataFileCache(TTLCache):
    def __init__(self, temp_root: Optional[str] = None, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # for testing
        self.temp_root = temp_root or tempfile.gettempdir()

    @staticmethod
    def create_key(dataset_id: str, file_name: str, source_type: SourceType = 'dataset', snapshot_id: str = "unset_snapshot_id") -> str:
        return f"{dataset_id}_{file_name}_{source_type}_{snapshot_id}"

    def create_file_path(self, dataset_id: str, file_name: str, source_type: SourceType = 'dataset', snapshot_id: str = "unset_snapshot_id") -> Path:
        return Path(os.path.join(self.cache_root(), source_type, dataset_id, snapshot_id, file_name))

    def cache_root(self) -> str:
        return os.path.join(self.temp_root, 'domino_api_datasets')

    def cleanup_path(self, path):
        if os.path.isdir(path):
            shutil.rmtree(path, ignore_errors=True)
        elif os.path.exists(path):
            try:
                os.remove(path)
            except FileNotFoundError:
                pass

        # cleans up the remaining directories if empty
        cleanup_root = self.cache_root()
        current = os.path.abspath(os.path.dirname(path))
        while os.path.commonpath([cleanup_root, current]) == cleanup_root:
            try:
                # this only succeeds if current is empty
                os.rmdir(current)
            except OSError:
                break
            # stop removing if we are at the cleanup root
            if current == cleanup_root:
                break
            current = os.path.abspath(os.path.dirname(current))

    def set(self, source_type: str, dataset_id: str, snapshot_id: str, file_name: str) -> Path:
        key = DataFileCache.create_key(dataset_id, file_name, source_type, snapshot_id)
        value = self.create_file_path(dataset_id, file_name, source_type, snapshot_id)

        os.makedirs(os.path.dirname(value), exist_ok=True)

        self[key] = value

        return value

    def remove(self, source_type: str, dataset_id: str, snapshot_id: str, file_name: str):
        key = DataFileCache.create_key(dataset_id, file_name, source_type, snapshot_id)
        if key in self:
            del self[key]

    def __delitem__(self, key):
        value = super().__getitem__(key)
        super().__delitem__(key)
        self.cleanup_path(value)

    def popitem(self):
        key, value = super().popitem()
        self.cleanup_path(value)
        return key, value

    def expire(self, time=None):
        expired = super().expire(time)
        for _, value in expired:
            self.cleanup_path(value)
        return expired

@lru_cache(maxsize=1)
def get_file_cache():
    """
    Returns singleton metadata file cache
    """
    return DataFileCache(maxsize=MAX_ITEM_COUNT, ttl=EXPIRATION_SECONDS)
