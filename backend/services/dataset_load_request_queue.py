from collections import deque
from dataclasses import dataclass, field
from functools import lru_cache
import os
import threading
import time
from typing import Deque, Optional

MAX_QUEUE_LENGTH = int(os.environ.get("DATASET_LOAD_REQUEST_QUEUE_MAX_LENGTH", 50))


@dataclass(frozen=True)
class DatasetLoadRequest:
    dataset: str
    session_id: str
    authorization_header: Optional[str] = None
    project_id: Optional[str] = None
    dataset_id: Optional[str] = None
    snapshot_id: Optional[str] = None
    source_type: Optional[str] = None
    volume_key: Optional[str] = None
    snapshot_version: Optional[int | str] = None
    enqueued_at: float = field(default_factory=time.time)


class DatasetLoadRequestQueueFullError(RuntimeError):
    pass


class DatasetLoadRequestQueue:
    """Thread-safe in-memory FIFO queue for dataset load requests."""

    def __init__(self, max_length: int = MAX_QUEUE_LENGTH):
        self._entries: Deque[DatasetLoadRequest] = deque()
        self._lock = threading.Lock()
        self.max_length = max_length

    def put(self, entry: DatasetLoadRequest):
        with self._lock:
            if len(self._entries) >= self.max_length:
                raise DatasetLoadRequestQueueFullError(
                    f"dataset load request queue is full (max_length={self.max_length})"
                )
            self._entries.append(entry)

    def get(self) -> DatasetLoadRequest:
        with self._lock:
            if not self._entries:
                raise IndexError("dataset load request queue is empty")
            return self._entries.popleft()

    def peek_all(self) -> list[DatasetLoadRequest]:
        with self._lock:
            return list(self._entries)

    def clear(self):
        with self._lock:
            self._entries.clear()

    def qsize(self) -> int:
        with self._lock:
            return len(self._entries)


@lru_cache(maxsize=1)
def get_dataset_load_request_queue():
    """Return the singleton queue that stores dataset load requests."""
    return DatasetLoadRequestQueue(max_length=MAX_QUEUE_LENGTH)
