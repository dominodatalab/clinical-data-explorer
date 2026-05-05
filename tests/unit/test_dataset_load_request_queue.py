import importlib

import backend.services.dataset_load_request_queue as dataset_load_request_queue_module

from backend.services.dataset_load_request_queue import (
    DatasetLoadRequest,
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


def test_get_dataset_load_request_queue_uses_env_max_length(monkeypatch):
    monkeypatch.setenv("DATASET_LOAD_REQUEST_QUEUE_MAX_LENGTH", "7")

    reloaded_module = importlib.reload(dataset_load_request_queue_module)
    reloaded_module.get_dataset_load_request_queue.cache_clear()

    queue = reloaded_module.get_dataset_load_request_queue()

    assert reloaded_module.MAX_QUEUE_LENGTH == 7
    assert queue.max_length == 7

    monkeypatch.delenv("DATASET_LOAD_REQUEST_QUEUE_MAX_LENGTH", raising=False)
    importlib.reload(reloaded_module).get_dataset_load_request_queue.cache_clear()
