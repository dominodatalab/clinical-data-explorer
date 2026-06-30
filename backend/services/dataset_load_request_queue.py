"""Thread-safe in-memory queue for dataset-load requests.

This module is responsible for accepting dataset-load requests from the Flask
route layer and ensuring that the aggregate projected load size can fit in
memory before requests execute concurrently.
"""

from collections import deque
from dataclasses import dataclass, field
from functools import lru_cache
import threading
import time
from typing import Callable, Deque, Optional

import requests

from backend import config
from backend.services.dataset_load_request_file_size_resolver import resolve_dataset_load_request_file_size
import backend.services.file_size_limits as file_size_limits

# Hard cap for queued load requests. When the queue reaches this size, new
# requests are rejected so the server does not accumulate unbounded work.
MAX_QUEUE_LENGTH = config.DATASET_LOAD_REQUEST_QUEUE_MAX_LENGTH


@dataclass(frozen=True)
class DatasetLoadRequest:
    """Immutable description of one dataset-load request.

    Fields:
        dataset: User-facing dataset/file identifier to load.
        session_id: Flask/MCP session identifier that should own the load.
        authorization_header: Raw Authorization header for Domino passthrough auth.
        project_id: Domino project ID for project-scoped dataset loads.
        dataset_id: Domino dataset ID for dataset-context loads.
        file_path: Source-relative file path to load when it differs from the display name.
        snapshot_id: Snapshot identifier for snapshot-specific loads.
        source_type: Logical source type, such as ``"netapp"``.
        volume_key: NetApp volume key for NetApp-backed loads.
        volume_id: NetApp volume UUID for WebVFS-backed metadata requests.
        snapshot_version: NetApp snapshot version when applicable.
        reload_context: Load body that MCP can store for backend reloads.
        enqueued_at: Timestamp recording when the request entered the queue.
    """

    dataset: str
    session_id: str
    authorization_header: Optional[str] = None
    project_id: Optional[str] = None
    dataset_id: Optional[str] = None
    file_path: Optional[str] = None
    snapshot_id: Optional[str] = None
    source_type: Optional[str] = None
    volume_key: Optional[str] = None
    volume_id: Optional[str] = None
    snapshot_version: Optional[int | str] = None
    reload_context: Optional[dict] = None
    enqueued_at: float = field(default_factory=time.time)


class DatasetLoadRequestQueueFullError(RuntimeError):
    """Raised when a dataset-load request cannot be added because the queue is full."""

    pass


@dataclass(eq=False)
class _QueuedDatasetLoadRequest:
    """Internal queue wrapper used for identity-based coordination."""

    entry: DatasetLoadRequest


class DatasetLoadRequestQueue:
    """Thread-safe queue that admits concurrent dataset-load execution.

    The queue serves two purposes:
    1. It retains the metadata needed to execute a dataset load.
    2. It tracks projected memory use so concurrent requests are only admitted
       when all loads in a contiguous busy period fit in memory together.

    ``submit_and_wait(...)`` is the main API used by the route layer. It
    resolves the request size, initializes projected memory from real memory
    when the queue is empty, admits the request, runs the provided processor,
    and removes the request when processing finishes.
    """

    def __init__(self, max_length: int = MAX_QUEUE_LENGTH):
        """Initialize a queue with a maximum number of queued requests."""
        # FIFO storage for admitted requests. Entries are wrapped so active
        # processors can remove their own request by object identity.
        self._entries: Deque[_QueuedDatasetLoadRequest] = deque()
        # Condition variable protecting queue state and coordinating wait/notify
        # between queued request threads.
        self._condition = threading.Condition()
        # Maximum number of requests allowed to be queued at once.
        self.max_length = max_length
        # None means no requests are active. The first admitted request sets
        # this to a real memory snapshot, and the value is reused until drain.
        self._memory_usage_baseline_bytes: Optional[int] = None
        # None means no requests are active. During a busy period, this tracks
        # the cumulative projected DataFrame memory for every admitted request.
        self._projected_dataframe_size_bytes: Optional[int] = None

    def _get_current_session_dataframe_size_bytes(self, session_id: str) -> int:
        response = requests.get(
            f"{config.MCP_SERVER_URL}/dataframe/size",
            headers={"X-Session-Id": session_id},
            timeout=config.MCP_REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        return int(response.json()["dataframe_size_bytes"])

    def _evict_current_session_dataframe(self, session_id: str) -> None:
        response = requests.post(
            f"{config.MCP_SERVER_URL}/dataframe/evict-current-session",
            headers={"X-Session-Id": session_id},
            timeout=config.MCP_REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()

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

    def submit_and_wait(
        self,
        entry: DatasetLoadRequest,
        processor: Callable[[DatasetLoadRequest], object],
    ):
        """Queue a request and process it when aggregate memory allows.

        This method blocks the calling thread until:
        1. the request has been enqueued,
        2. ``processor(entry)`` has run.

        Multiple threads may be inside ``processor(...)`` at a time when their
        projected combined DataFrame size fits within the memory limit.

        Raises:
            DatasetLoadRequestQueueFullError: if the queue has reached
                ``max_length`` before the request can be added.
            DataFileTooLarge: if active loads plus this request would exceed
                the memory limit.
        """
        file_size = resolve_dataset_load_request_file_size(entry)
        projected_dataframe_size_bytes = file_size_limits.estimate_dataframe_size_bytes(file_size)

        entry = DatasetLoadRequest(
            dataset=entry.dataset,
            session_id=entry.session_id,
            authorization_header=entry.authorization_header,
            project_id=entry.project_id,
            dataset_id=entry.dataset_id,
            file_path=entry.file_path,
            snapshot_id=entry.snapshot_id,
            source_type=entry.source_type,
            volume_key=entry.volume_key,
            volume_id=entry.volume_id,
            snapshot_version=entry.snapshot_version,
            reload_context=entry.reload_context,
            enqueued_at=entry.enqueued_at,
        )
        queued_entry = _QueuedDatasetLoadRequest(entry)

        with self._condition:
            if len(self._entries) >= self.max_length:
                raise DatasetLoadRequestQueueFullError(
                    f"dataset load request queue is full (max_length={self.max_length})"
                )

            started_busy_period = not self._entries
            if started_busy_period:
                # Empty queue means a new busy period. Capture real memory once,
                # then keep using that stable baseline until every admitted load
                # finishes and the queue drains.
                self._memory_usage_baseline_bytes = file_size_limits.get_memory_usage_snapshot_bytes()
                self._projected_dataframe_size_bytes = 0

            current_session_dataframe_size_bytes = self._get_current_session_dataframe_size_bytes(entry.session_id)
            adjusted_memory_usage_baseline_bytes = self._memory_usage_baseline_bytes
            if adjusted_memory_usage_baseline_bytes is not None:
                adjusted_memory_usage_baseline_bytes = max(
                    0,
                    adjusted_memory_usage_baseline_bytes - current_session_dataframe_size_bytes,
                )

            # The admission check intentionally ignores later real-RAM changes.
            # It uses baseline + already admitted DataFrame projections, then
            # adds this request's projection only after the request is admitted.
            try:
                file_size_limits.enforce(
                    entry.dataset,
                    file_size,
                    additional_projected_dataframe_size_b=self._projected_dataframe_size_bytes or 0,
                    used_memory_bytes=adjusted_memory_usage_baseline_bytes,
                )
                self._evict_current_session_dataframe(entry.session_id)
            except Exception:
                if started_busy_period:
                    self._reset_projected_memory_usage()
                raise

            self._entries.append(queued_entry)
            self._increment_projected_memory_usage(projected_dataframe_size_bytes)
            self._condition.notify_all()

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
                if not self._entries:
                    self._reset_projected_memory_usage()

    def _increment_projected_memory_usage(self, projected_dataframe_size_bytes: int):
        if self._projected_dataframe_size_bytes is not None:
            self._projected_dataframe_size_bytes += projected_dataframe_size_bytes

    def _reset_projected_memory_usage(self):
        self._memory_usage_baseline_bytes = None
        self._projected_dataframe_size_bytes = None

    def peek_all(self) -> list[DatasetLoadRequest]:
        """Return a snapshot of the queued requests in FIFO order."""
        with self._condition:
            return [queued.entry for queued in self._entries]

    def clear(self):
        """Remove all queued requests and wake any waiters."""
        with self._condition:
            self._entries.clear()
            self._reset_projected_memory_usage()
            self._condition.notify_all()

    def qsize(self) -> int:
        """Return the current number of queued requests."""
        with self._condition:
            return len(self._entries)


@lru_cache(maxsize=1)
def get_dataset_load_request_queue():
    """Return the process-wide singleton queue for dataset-load requests."""
    return DatasetLoadRequestQueue(max_length=MAX_QUEUE_LENGTH)
