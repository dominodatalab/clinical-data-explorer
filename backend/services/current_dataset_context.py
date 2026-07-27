"""Request-refresh context for each user's current dataset.

The cache stores only non-secret dataset identity needed to rebuild a
DatasetLoadRequest. It must never store bearer tokens or authorization
headers; refreshes combine this context with the Authorization header from the
request currently being served.
"""
from dataclasses import dataclass
from functools import lru_cache
from typing import Optional

from cachetools import TTLCache

from backend import config
from backend.types import SourceType


@dataclass(frozen=True)
class CurrentDatasetContext:
    dataset: str
    project_id: Optional[str] = None
    dataset_id: Optional[str] = None
    snapshot_id: Optional[str] = None
    source_type: Optional[SourceType] = None
    volume_key: Optional[str] = None
    volume_id: Optional[str] = None
    snapshot_version: Optional[int | str] = None
    resolved_file_snapshot_path: Optional[str] = None

    def is_local_filesystem_dataset(self) -> bool:
        return not (
            self.project_id
            or self.dataset_id
            or self.source_type
            or self.volume_key
        )


@lru_cache(maxsize=1)
def get_current_dataset_context_cache():
    return TTLCache(
        maxsize=config.CURRENT_DATASET_CONTEXT_CACHE_MAX_ITEM_COUNT,
        ttl=config.CURRENT_DATASET_CONTEXT_CACHE_TTL_SECONDS,
    )


def set_current_dataset_context(user_id: str, context: CurrentDatasetContext) -> None:
    get_current_dataset_context_cache()[user_id] = context


def get_current_dataset_context(user_id: str) -> Optional[CurrentDatasetContext]:
    return get_current_dataset_context_cache().get(user_id)


def clear_current_dataset_context_cache() -> None:
    get_current_dataset_context_cache().clear()
