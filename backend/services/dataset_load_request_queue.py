"""Thread-safe in-memory queue for serialized dataset-load requests.

This module is responsible for accepting dataset-load requests from the Flask
route layer and ensuring that only one request is actively executing the
load/download path at a time.
"""

from collections import deque
from dataclasses import dataclass, field
from functools import lru_cache
import os
import threading
import time
from typing import Callable, Deque, Optional

# Hard cap for queued load requests. When the queue reaches this size, new
# requests are rejected so the server does not accumulate unbounded work.
MAX_QUEUE_LENGTH = int(os.environ.get("DATASET_LOAD_REQUEST_QUEUE_MAX_LENGTH", 50))


@dataclass(frozen=True)
class DatasetLoadRequest:
    """Immutable description of one dataset-load request.

    Fields:
        dataset: User-facing dataset/file identifier to load.
        session_id: Flask/MCP session identifier that should own the load.
        authorization_header: Raw Authorization header for Domino passthrough auth.
        project_id: Domino project ID for project-scoped dataset loads.
        dataset_id: Domino dataset ID for dataset-context loads.
        snapshot_id: Snapshot identifier for snapshot-specific loads.
        source_type: Logical source type, such as ``"netapp"``.
        volume_key: NetApp volume key for NetApp-backed loads.
        snapshot_version: NetApp snapshot version when applicable.
        enqueued_at: Timestamp recording when the request entered the queue.
    """

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
    """Raised when a dataset-load request cannot be added because the queue is full."""

    pass


class DatasetLoadRequestQueueClearedError(RuntimeError):
    """Raised when a waiting dataset-load request is removed by ``clear()``."""

    pass


@dataclass(eq=False)
class _QueuedDatasetLoadRequest:
    """Internal queue wrapper used for identity-based coordination."""

    entry: DatasetLoadRequest


class DatasetLoadRequestQueue:
    """Thread-safe FIFO queue that serializes dataset-load execution.

    The queue serves two purposes:
    1. It retains the metadata needed to execute a dataset load.
    2. It ensures that only one queued request is actively running the load
       processor at a time.

    ``submit_and_wait(...)`` is the main API used by the route layer. It
    enqueues the request, blocks until that request reaches the head of the
    queue, runs the provided processor, and then wakes the next waiting
    request.
    """

    def __init__(self, max_length: int = MAX_QUEUE_LENGTH):
        """Initialize a queue with a maximum number of queued requests."""
        # FIFO storage for queued requests. Entries are wrapped so individual
        # waiting threads can compare object identity against the queue head.
        self._entries: Deque[_QueuedDatasetLoadRequest] = deque()
        # Condition variable protecting queue state and coordinating wait/notify
        # between queued request threads.
        self._condition = threading.Condition()
        # Maximum number of requests allowed to be queued at once.
        self.max_length = max_length

    def put(self, entry: DatasetLoadRequest):
        """Append a request without processing it.

        Raises:
            DatasetLoadRequestQueueFullError: if the queue has reached
                ``max_length``.
        """
        with self._condition:
            if len(self._entries) >= self.max_length:
                raise DatasetLoadRequestQueueFullError(
                    f"dataset load request queue is full (max_length={self.max_length})"
                )
            self._entries.append(_QueuedDatasetLoadRequest(entry))
            self._condition.notify_all()

    def get(self) -> DatasetLoadRequest:
        """Remove and return the next queued request.

        Raises:
            IndexError: if the queue is empty.
        """
        with self._condition:
            if not self._entries:
                raise IndexError("dataset load request queue is empty")
            return self._entries.popleft().entry

    def submit_and_wait(self, entry: DatasetLoadRequest, processor: Callable[[DatasetLoadRequest], object]):
        """Queue a request and process it when it reaches the head of the queue.

        This method blocks the calling thread until:
        1. the request has been enqueued,
        2. all earlier requests have finished, and
        3. ``processor(entry)`` has run.

        Only one thread may be inside ``processor(...)`` at a time.

        Raises:
            DatasetLoadRequestQueueFullError: if the queue has reached
                ``max_length`` before the request can be added.
            DatasetLoadRequestQueueClearedError: if the request is removed from
                the queue before it reaches the head.
        """
        queued_entry = _QueuedDatasetLoadRequest(entry)

        with self._condition:
            if len(self._entries) >= self.max_length:
                raise DatasetLoadRequestQueueFullError(
                    f"dataset load request queue is full (max_length={self.max_length})"
                )
            self._entries.append(queued_entry)
            self._condition.notify_all()

            while True:
                if not any(queued is queued_entry for queued in self._entries):
                    raise DatasetLoadRequestQueueClearedError(
                        "dataset load request was removed from the queue before it could be processed"
                    )
                if self._entries[0] is queued_entry:
                    break
                # blocks here until
                # this entry is the first one
                # in the queue
                self._condition.wait()

        try:
            return processor(entry)
        finally:
            with self._condition:
                if self._entries and self._entries[0] is queued_entry:
                    # if this entry is the 1st one
                    # remove it
                    self._entries.popleft()
                else:
                    try:
                        # falls back to removing
                        # which is more expensive
                        self._entries.remove(queued_entry)
                    except ValueError:
                        pass
                self._condition.notify_all()

    def peek_all(self) -> list[DatasetLoadRequest]:
        """Return a snapshot of the queued requests in FIFO order."""
        with self._condition:
            return [queued.entry for queued in self._entries]

    def clear(self):
        """Remove all queued requests and wake any waiters."""
        with self._condition:
            self._entries.clear()
            self._condition.notify_all()

    def qsize(self) -> int:
        """Return the current number of queued requests."""
        with self._condition:
            return len(self._entries)


@lru_cache(maxsize=1)
def get_dataset_load_request_queue():
    """Return the process-wide singleton queue for dataset-load requests."""
    return DatasetLoadRequestQueue(max_length=MAX_QUEUE_LENGTH)
