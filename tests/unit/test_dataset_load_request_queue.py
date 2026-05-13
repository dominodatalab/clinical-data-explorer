import importlib
import threading
import time

import backend.config as config_module
import backend.services.dataset_load_request_queue as dataset_load_request_queue_module

from backend.services.dataset_load_request_queue import (
    DatasetLoadRequest,
    DatasetLoadRequestQueueClearedError,
    DatasetLoadRequestQueue,
    DatasetLoadRequestQueueFullError,
    get_dataset_load_request_queue,
)


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


def test_dataset_load_request_queue_submit_and_wait_processes_one_request_at_a_time():
    queue = DatasetLoadRequestQueue(max_length=10)
    first_started = threading.Event()
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
            if entry.dataset == "one.csv":
                first_started.set()

        if entry.dataset == "one.csv":
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
    time.sleep(0.02)
    assert queue.qsize() == 2

    allow_first_to_finish.set()
    first_thread.join(timeout=1)
    second_thread.join(timeout=1)

    assert results == {
        "one.csv": "loaded:one.csv",
        "two.csv": "loaded:two.csv",
    }
    assert processed == ["one.csv", "two.csv"]
    assert active_processors["max"] == 1
    assert queue.qsize() == 0


def test_dataset_load_request_queue_submit_and_wait_preserves_order_for_three_waiters():
    queue = DatasetLoadRequestQueue(max_length=10)
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

        if entry.dataset == "one.csv":
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
    assert processed == ["one.csv", "two.csv", "three.csv"]
    assert active_processors["max"] == 1
    assert queue.qsize() == 0


def test_dataset_load_request_queue_submit_and_wait_unblocks_next_request_when_processor_raises():
    queue = DatasetLoadRequestQueue(max_length=10)
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
    assert processed == ["one.csv", "two.csv"]
    assert active_processors["max"] == 1
    assert queue.qsize() == 0


def test_dataset_load_request_queue_clear_wakes_waiting_request_with_explicit_error():
    queue = DatasetLoadRequestQueue(max_length=10)
    first_started = threading.Event()
    allow_first_to_finish = threading.Event()
    waiter_ready = threading.Event()
    results = {}
    errors = {}

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

    def run_second():
        waiter_ready.set()
        try:
            results["two.csv"] = queue.submit_and_wait(
                DatasetLoadRequest(dataset="two.csv", session_id="sid-2"),
                processor,
            )
        except Exception as exc:  # pragma: no cover - assertion inspects captured error
            errors["two.csv"] = exc

    first_thread = threading.Thread(target=run_first)
    second_thread = threading.Thread(target=run_second)

    first_thread.start()
    assert first_started.wait(timeout=1)

    second_thread.start()
    assert waiter_ready.wait(timeout=1)
    time.sleep(0.02)

    queue.clear()
    allow_first_to_finish.set()

    first_thread.join(timeout=1)
    second_thread.join(timeout=1)

    assert results["one.csv"] == "loaded:one.csv"
    assert isinstance(errors["two.csv"], DatasetLoadRequestQueueClearedError)
    assert str(errors["two.csv"]) == "dataset load request was removed from the queue before it could be processed"
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
