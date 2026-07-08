import importlib
import threading
import time

import backend.config as config_module
import backend.services.dataset_load_request_queue as dataset_load_request_queue_module
import pytest

from backend.services.dataset_load_request_queue import (
    DatasetLoadRequest,
    DatasetLoadRequestQueue,
    DatasetLoadRequestQueueFullError,
    get_dataset_load_request_queue,
)

_GET_CURRENT_SESSION_DATAFRAME_SIZE_BYTES = DatasetLoadRequestQueue._get_current_session_dataframe_size_bytes


class _FakeMcpResponse:
    def __init__(self, payload, status_error=None):
        self._payload = payload
        self._status_error = status_error
        self.raise_for_status_called = False

    def raise_for_status(self):
        self.raise_for_status_called = True
        if self._status_error:
            raise self._status_error

    def json(self):
        return self._payload


@pytest.fixture(autouse=True)
def stub_mcp_dataframe_hooks(monkeypatch):
    monkeypatch.setattr(
        DatasetLoadRequestQueue,
        "_get_current_session_dataframe_size_bytes",
        lambda self, authorization_header: 0,
    )
    monkeypatch.setattr(
        DatasetLoadRequestQueue,
        "_evict_current_session_dataframe",
        lambda self, authorization_header: None,
    )


def test_get_current_session_dataframe_size_requires_successful_mcp_response(monkeypatch):
    response = _FakeMcpResponse({"dataframe_size_bytes": 1234})
    calls = []

    def fake_get(url, headers, timeout):
        calls.append((url, headers, timeout))
        return response

    monkeypatch.setattr(dataset_load_request_queue_module.requests, "get", fake_get)

    size = _GET_CURRENT_SESSION_DATAFRAME_SIZE_BYTES(DatasetLoadRequestQueue(), "Bearer tok-1")

    assert size == 1234
    assert response.raise_for_status_called
    assert calls == [
        (
            f"{dataset_load_request_queue_module.config.MCP_SERVER_URL}/dataframe/size",
            {"Authorization": "Bearer tok-1"},
            dataset_load_request_queue_module.config.MCP_REQUEST_TIMEOUT_SECONDS,
        )
    ]


def test_get_current_session_dataframe_size_propagates_mcp_status_errors(monkeypatch):
    response = _FakeMcpResponse({"dataframe_size_bytes": 1234}, status_error=RuntimeError("boom"))
    monkeypatch.setattr(dataset_load_request_queue_module.requests, "get", lambda *args, **kwargs: response)

    with pytest.raises(RuntimeError, match="boom"):
        _GET_CURRENT_SESSION_DATAFRAME_SIZE_BYTES(DatasetLoadRequestQueue(), "Bearer tok-1")

    assert response.raise_for_status_called


def test_get_dataset_load_request_queue_returns_singleton():
    queue_one = get_dataset_load_request_queue()
    queue_two = get_dataset_load_request_queue()

    assert queue_one is queue_two


def test_dataset_load_request_queue_is_fifo_and_clearable():
    queue = get_dataset_load_request_queue()
    queue.clear()

    first = DatasetLoadRequest(dataset="one.csv", session_id="sid-1")
    second = DatasetLoadRequest(dataset="two.csv", session_id="sid-2")

    queue.put(first)
    queue.put(second)

    assert queue.qsize() == 2
    assert queue.peek_all() == [first, second]
    assert queue.get() == first
    assert queue.get() == second
    assert queue.qsize() == 0

    queue.put(first)
    queue.clear()

    assert queue.qsize() == 0
    assert queue.peek_all() == []


def test_dataset_load_request_queue_raises_when_full():
    queue = DatasetLoadRequestQueue(max_length=1)
    queue.put(DatasetLoadRequest(dataset="one.csv", session_id="sid-1"))

    try:
        queue.put(DatasetLoadRequest(dataset="two.csv", session_id="sid-2"))
        assert False, "expected DatasetLoadRequestQueueFullError"
    except DatasetLoadRequestQueueFullError as exc:
        assert str(exc) == "dataset load request queue is full (max_length=1)"

    assert queue.qsize() == 1
    assert queue.peek_all()[0].dataset == "one.csv"


def test_dataset_load_request_queue_submit_and_wait_processes_allowed_requests_concurrently(monkeypatch):
    queue = DatasetLoadRequestQueue(max_length=10)
    monkeypatch.setattr(dataset_load_request_queue_module, "resolve_dataset_load_request_file_size", lambda entry: 1)
    first_started = threading.Event()
    allow_first_to_finish = threading.Event()
    second_started = threading.Event()
    state_lock = threading.Lock()
    active_processors = {"count": 0, "max": 0}
    processed = []
    results = {}

    def processor(entry):
        with state_lock:
            active_processors["count"] += 1
            active_processors["max"] = max(active_processors["max"], active_processors["count"])
            processed.append(entry.dataset)
            if entry.dataset == "one.csv":
                first_started.set()
            if entry.dataset == "two.csv":
                second_started.set()

        allow_first_to_finish.wait(timeout=1)
        time.sleep(0.01)

        with state_lock:
            active_processors["count"] -= 1

        return f"loaded:{entry.dataset}"

    def run_request(name):
        results[name] = queue.submit_and_wait(
            DatasetLoadRequest(dataset=name, session_id=f"sid-{name}"),
            processor,
        )

    first_thread = threading.Thread(target=run_request, args=("one.csv",))
    second_thread = threading.Thread(target=run_request, args=("two.csv",))

    first_thread.start()
    assert first_started.wait(timeout=1)

    second_thread.start()
    assert second_started.wait(timeout=1)
    assert queue.qsize() == 2
    assert active_processors["max"] == 2

    allow_first_to_finish.set()
    first_thread.join(timeout=1)
    second_thread.join(timeout=1)

    assert results == {
        "one.csv": "loaded:one.csv",
        "two.csv": "loaded:two.csv",
    }
    assert set(processed) == {"one.csv", "two.csv"}
    assert active_processors["max"] == 2
    assert queue.qsize() == 0


def test_dataset_load_request_queue_submit_and_wait_processes_three_allowed_requests_concurrently(monkeypatch):
    queue = DatasetLoadRequestQueue(max_length=10)
    monkeypatch.setattr(dataset_load_request_queue_module, "resolve_dataset_load_request_file_size", lambda entry: 1)
    allow_first_to_finish = threading.Event()
    state_lock = threading.Lock()
    active_processors = {"count": 0, "max": 0}
    processed = []
    results = {}

    def processor(entry):
        with state_lock:
            active_processors["count"] += 1
            active_processors["max"] = max(active_processors["max"], active_processors["count"])
            processed.append(entry.dataset)

        allow_first_to_finish.wait(timeout=1)
        time.sleep(0.01)

        with state_lock:
            active_processors["count"] -= 1

        return f"loaded:{entry.dataset}"

    def run_request(name):
        results[name] = queue.submit_and_wait(
            DatasetLoadRequest(dataset=name, session_id=f"sid-{name}"),
            processor,
        )

    threads = [
        threading.Thread(target=run_request, args=("one.csv",)),
        threading.Thread(target=run_request, args=("two.csv",)),
        threading.Thread(target=run_request, args=("three.csv",)),
    ]

    threads[0].start()
    time.sleep(0.02)
    threads[1].start()
    threads[2].start()
    time.sleep(0.02)

    assert queue.qsize() == 3
    allow_first_to_finish.set()

    for thread in threads:
        thread.join(timeout=1)

    assert results == {
        "one.csv": "loaded:one.csv",
        "two.csv": "loaded:two.csv",
        "three.csv": "loaded:three.csv",
    }
    assert set(processed) == {"one.csv", "two.csv", "three.csv"}
    assert active_processors["max"] == 3
    assert queue.qsize() == 0


def test_dataset_load_request_queue_admits_concurrent_request_with_projected_active_load(monkeypatch):
    queue = DatasetLoadRequestQueue(max_length=10)
    first_started = threading.Event()
    second_started = threading.Event()
    allow_first_to_finish = threading.Event()
    results = {}
    admission_checks = []

    file_sizes = {
        "one.csv": 10,
        "two.csv": 20,
    }

    monkeypatch.setattr(
        dataset_load_request_queue_module,
        "resolve_dataset_load_request_file_size",
        lambda entry: file_sizes[entry.dataset],
    )
    monkeypatch.setattr(dataset_load_request_queue_module.file_size_limits, "get_memory_usage_snapshot_bytes", lambda: 123)

    def enforce(file_name, file_size, additional_projected_dataframe_size_b, used_memory_bytes):
        admission_checks.append(
            {
                "file_name": file_name,
                "file_size": file_size,
                "additional_projected_dataframe_size_b": additional_projected_dataframe_size_b,
                "used_memory_bytes": used_memory_bytes,
            }
        )

    monkeypatch.setattr(dataset_load_request_queue_module.file_size_limits, "enforce", enforce)

    def processor(entry):
        if entry.dataset == "one.csv":
            first_started.set()
            allow_first_to_finish.wait(timeout=1)
        if entry.dataset == "two.csv":
            second_started.set()
        return f"loaded:{entry.dataset}"

    first_thread = threading.Thread(
        target=lambda: results.update(
            {
                "one.csv": queue.submit_and_wait(
                    DatasetLoadRequest(dataset="one.csv", session_id="sid-1"),
                    processor,
                )
            }
        )
    )

    first_thread.start()
    assert first_started.wait(timeout=1)

    results["two.csv"] = queue.submit_and_wait(
        DatasetLoadRequest(dataset="two.csv", session_id="sid-2"),
        processor,
    )

    assert second_started.is_set()
    assert queue.qsize() == 1
    assert admission_checks == [
        {
            "file_name": "one.csv",
            "file_size": 10,
            "additional_projected_dataframe_size_b": 0,
            "used_memory_bytes": 123,
        },
        {
            "file_name": "two.csv",
            "file_size": 20,
            "additional_projected_dataframe_size_b": 50,
            "used_memory_bytes": 123,
        },
    ]

    allow_first_to_finish.set()
    first_thread.join(timeout=1)

    assert results == {
        "one.csv": "loaded:one.csv",
        "two.csv": "loaded:two.csv",
    }
    assert queue.qsize() == 0


def test_dataset_load_request_queue_subtracts_current_session_dataframe_before_admission(monkeypatch):
    queue = DatasetLoadRequestQueue(max_length=10)
    events = []

    monkeypatch.setattr(dataset_load_request_queue_module, "resolve_dataset_load_request_file_size", lambda entry: 100)
    monkeypatch.setattr(dataset_load_request_queue_module.file_size_limits, "get_memory_usage_snapshot_bytes", lambda: 700)
    monkeypatch.setattr(dataset_load_request_queue_module.file_size_limits, "get_memory_limit_bytes", lambda: 1000)
    monkeypatch.setattr(
        DatasetLoadRequestQueue,
        "_get_current_session_dataframe_size_bytes",
        lambda self, authorization_header: events.append(("size", authorization_header)) or 300,
    )
    monkeypatch.setattr(
        DatasetLoadRequestQueue,
        "_evict_current_session_dataframe",
        lambda self, authorization_header: events.append(("evict", authorization_header)),
    )

    result = queue.submit_and_wait(
        DatasetLoadRequest(dataset="replacement.csv", session_id="sid-1", authorization_header="Bearer tok-1"),
        lambda entry: events.append(("process", entry.session_id)) or "loaded",
    )

    assert result == "loaded"
    assert events == [
        ("size", "Bearer tok-1"),
        ("evict", "Bearer tok-1"),
        ("process", "sid-1"),
    ]
    assert queue.qsize() == 0


def test_dataset_load_request_queue_serializes_memory_snapshot_admission(monkeypatch):
    queue = DatasetLoadRequestQueue(max_length=10)
    snapshot_started = threading.Event()
    allow_snapshot_to_finish = threading.Event()
    first_processor_started = threading.Event()
    allow_first_to_finish = threading.Event()
    second_admitted = threading.Event()
    second_processed = threading.Event()
    snapshot_state_lock = threading.Lock()
    snapshot_state = {"active": 0, "max": 0, "calls": 0}
    results = {}

    monkeypatch.setattr(dataset_load_request_queue_module, "resolve_dataset_load_request_file_size", lambda entry: 1)

    def get_memory_usage_snapshot_bytes():
        with snapshot_state_lock:
            snapshot_state["active"] += 1
            snapshot_state["calls"] += 1
            snapshot_state["max"] = max(snapshot_state["max"], snapshot_state["active"])

        snapshot_started.set()
        allow_snapshot_to_finish.wait(timeout=1)

        with snapshot_state_lock:
            snapshot_state["active"] -= 1

        return 123

    def enforce(file_name, file_size, additional_projected_dataframe_size_b, used_memory_bytes):
        if file_name == "two.csv":
            second_admitted.set()

    monkeypatch.setattr(
        dataset_load_request_queue_module.file_size_limits,
        "get_memory_usage_snapshot_bytes",
        get_memory_usage_snapshot_bytes,
    )
    monkeypatch.setattr(dataset_load_request_queue_module.file_size_limits, "enforce", enforce)

    def processor(entry):
        if entry.dataset == "one.csv":
            first_processor_started.set()
            allow_first_to_finish.wait(timeout=1)
        if entry.dataset == "two.csv":
            second_processed.set()
        return f"loaded:{entry.dataset}"

    first_thread = threading.Thread(
        target=lambda: results.update(
            {
                "one.csv": queue.submit_and_wait(
                    DatasetLoadRequest(dataset="one.csv", session_id="sid-1"),
                    processor,
                )
            }
        )
    )
    second_thread = threading.Thread(
        target=lambda: results.update(
            {
                "two.csv": queue.submit_and_wait(
                    DatasetLoadRequest(dataset="two.csv", session_id="sid-2"),
                    processor,
                )
            }
        )
    )

    first_thread.start()
    assert snapshot_started.wait(timeout=1)

    second_thread.start()
    time.sleep(0.02)

    assert not second_admitted.is_set()
    assert not second_processed.is_set()
    assert snapshot_state == {
        "active": 1,
        "max": 1,
        "calls": 1,
    }

    allow_snapshot_to_finish.set()
    assert first_processor_started.wait(timeout=1)
    assert second_admitted.wait(timeout=1)
    assert second_processed.wait(timeout=1)

    allow_first_to_finish.set()
    first_thread.join(timeout=1)
    second_thread.join(timeout=1)

    assert snapshot_state == {
        "active": 0,
        "max": 1,
        "calls": 1,
    }
    assert results == {
        "one.csv": "loaded:one.csv",
        "two.csv": "loaded:two.csv",
    }
    assert queue.qsize() == 0


def test_dataset_load_request_queue_submit_and_wait_unblocks_next_request_when_processor_raises(monkeypatch):
    queue = DatasetLoadRequestQueue(max_length=10)
    monkeypatch.setattr(dataset_load_request_queue_module, "resolve_dataset_load_request_file_size", lambda entry: 1)
    first_started = threading.Event()
    allow_first_to_finish = threading.Event()
    active_processors = {"count": 0, "max": 0}
    state_lock = threading.Lock()
    processed = []
    results = {}
    errors = {}

    def processor(entry):
        with state_lock:
            active_processors["count"] += 1
            active_processors["max"] = max(active_processors["max"], active_processors["count"])
            processed.append(entry.dataset)
            if entry.dataset == "one.csv":
                first_started.set()

        try:
            if entry.dataset == "one.csv":
                allow_first_to_finish.wait(timeout=1)
                raise RuntimeError("first failed")
            return f"loaded:{entry.dataset}"
        finally:
            with state_lock:
                active_processors["count"] -= 1

    def run_request(name):
        try:
            results[name] = queue.submit_and_wait(
                DatasetLoadRequest(dataset=name, session_id=f"sid-{name}"),
                processor,
            )
        except Exception as exc:  # pragma: no cover - assertion inspects captured error
            errors[name] = exc

    first_thread = threading.Thread(target=run_request, args=("one.csv",))
    second_thread = threading.Thread(target=run_request, args=("two.csv",))

    first_thread.start()
    assert first_started.wait(timeout=1)

    second_thread.start()
    time.sleep(0.02)
    allow_first_to_finish.set()

    first_thread.join(timeout=1)
    second_thread.join(timeout=1)

    assert str(errors["one.csv"]) == "first failed"
    assert results["two.csv"] == "loaded:two.csv"
    assert set(processed) == {"one.csv", "two.csv"}
    assert active_processors["max"] == 2
    assert queue.qsize() == 0


def test_dataset_load_request_queue_rejects_request_when_projected_loads_exceed_memory(monkeypatch):
    queue = DatasetLoadRequestQueue(max_length=10)
    first_started = threading.Event()
    allow_first_to_finish = threading.Event()

    monkeypatch.setattr(dataset_load_request_queue_module, "resolve_dataset_load_request_file_size", lambda entry: 100)
    monkeypatch.setattr(dataset_load_request_queue_module.file_size_limits, "get_memory_usage_snapshot_bytes", lambda: 100)
    monkeypatch.setattr(dataset_load_request_queue_module.file_size_limits, "get_memory_limit_bytes", lambda: 1000)

    def processor(entry):
        first_started.set()
        allow_first_to_finish.wait(timeout=1)
        return f"loaded:{entry.dataset}"

    first_thread = threading.Thread(
        target=lambda: queue.submit_and_wait(
            DatasetLoadRequest(
                dataset="one.csv",
                session_id="sid-1",
            ),
            processor,
        )
    )

    first_thread.start()
    assert first_started.wait(timeout=1)

    try:
        queue.submit_and_wait(
            DatasetLoadRequest(
                dataset="two.csv",
                session_id="sid-2",
            ),
            lambda entry: "unexpected",
        )
        assert False, "expected DataFileTooLarge"
    except dataset_load_request_queue_module.file_size_limits.DataFileTooLarge as exc:
        assert "There's not enough space to process two.csv." in str(exc)

    allow_first_to_finish.set()
    first_thread.join(timeout=1)
    assert queue.qsize() == 0


def test_dataset_load_request_queue_snapshots_memory_once_until_queue_drains(monkeypatch):
    queue = DatasetLoadRequestQueue(max_length=10)
    memory_snapshots = iter([100, 900])
    first_started = threading.Event()
    allow_first_to_finish = threading.Event()

    monkeypatch.setattr(dataset_load_request_queue_module, "resolve_dataset_load_request_file_size", lambda entry: 40)
    monkeypatch.setattr(
        dataset_load_request_queue_module.file_size_limits,
        "get_memory_usage_snapshot_bytes",
        lambda: next(memory_snapshots),
    )
    monkeypatch.setattr(dataset_load_request_queue_module.file_size_limits, "get_memory_limit_bytes", lambda: 1000)

    def processor(entry):
        if entry.dataset == "one.csv":
            first_started.set()
            allow_first_to_finish.wait(timeout=1)
        return f"loaded:{entry.dataset}"

    first_thread = threading.Thread(
        target=lambda: queue.submit_and_wait(
            DatasetLoadRequest(
                dataset="one.csv",
                session_id="sid-1",
            ),
            processor,
        )
    )

    first_thread.start()
    assert first_started.wait(timeout=1)

    result = queue.submit_and_wait(
        DatasetLoadRequest(
            dataset="two.csv",
            session_id="sid-2",
        ),
        processor,
    )

    allow_first_to_finish.set()
    first_thread.join(timeout=1)

    assert result == "loaded:two.csv"
    assert queue.qsize() == 0


def test_dataset_load_request_queue_resets_memory_snapshot_after_queue_drains(monkeypatch):
    queue = DatasetLoadRequestQueue(max_length=10)
    memory_snapshots = iter([100, 900])

    monkeypatch.setattr(dataset_load_request_queue_module, "resolve_dataset_load_request_file_size", lambda entry: 40)
    monkeypatch.setattr(
        dataset_load_request_queue_module.file_size_limits,
        "get_memory_usage_snapshot_bytes",
        lambda: next(memory_snapshots),
    )
    monkeypatch.setattr(dataset_load_request_queue_module.file_size_limits, "get_memory_limit_bytes", lambda: 1000)

    assert queue.submit_and_wait(
        DatasetLoadRequest(
            dataset="one.csv",
            session_id="sid-1",
        ),
        lambda entry: f"loaded:{entry.dataset}",
    ) == "loaded:one.csv"
    assert queue._memory_usage_baseline_bytes is None
    assert queue._projected_dataframe_size_bytes is None

    try:
        queue.submit_and_wait(
            DatasetLoadRequest(
                dataset="two.csv",
                session_id="sid-2",
            ),
            lambda entry: "unexpected",
        )
        assert False, "expected DataFileTooLarge"
    except dataset_load_request_queue_module.file_size_limits.DataFileTooLarge as exc:
        assert "There's not enough space to process two.csv." in str(exc)

    assert queue.qsize() == 0
    assert queue._memory_usage_baseline_bytes is None
    assert queue._projected_dataframe_size_bytes is None


def test_dataset_load_request_queue_clear_removes_active_request_accounting(monkeypatch):
    queue = DatasetLoadRequestQueue(max_length=10)
    monkeypatch.setattr(dataset_load_request_queue_module, "resolve_dataset_load_request_file_size", lambda entry: 1)
    first_started = threading.Event()
    allow_first_to_finish = threading.Event()
    results = {}

    def processor(entry):
        if entry.dataset == "one.csv":
            first_started.set()
            allow_first_to_finish.wait(timeout=1)
        return f"loaded:{entry.dataset}"

    def run_first():
        results["one.csv"] = queue.submit_and_wait(
            DatasetLoadRequest(dataset="one.csv", session_id="sid-1"),
            processor,
        )

    first_thread = threading.Thread(target=run_first)

    first_thread.start()
    assert first_started.wait(timeout=1)
    assert queue.qsize() == 1

    queue.clear()
    assert queue.qsize() == 0
    allow_first_to_finish.set()

    first_thread.join(timeout=1)

    assert results["one.csv"] == "loaded:one.csv"
    assert queue.qsize() == 0


def test_get_dataset_load_request_queue_uses_env_max_length(monkeypatch):
    monkeypatch.setenv("DATASET_LOAD_REQUEST_QUEUE_MAX_LENGTH", "7")

    importlib.reload(config_module)
    reloaded_module = importlib.reload(dataset_load_request_queue_module)
    reloaded_module.get_dataset_load_request_queue.cache_clear()

    queue = reloaded_module.get_dataset_load_request_queue()

    assert reloaded_module.MAX_QUEUE_LENGTH == 7
    assert queue.max_length == 7

    monkeypatch.delenv("DATASET_LOAD_REQUEST_QUEUE_MAX_LENGTH", raising=False)
    importlib.reload(config_module)
    importlib.reload(reloaded_module).get_dataset_load_request_queue.cache_clear()
