"""Reusable dataset-load orchestration.

This module owns the body of the public `/dataset/load` route so other backend
paths can refresh an evicted DataFrame without making an HTTP request back to
the route. It intentionally does not import Flask; callers adapt the returned
payload/status into route responses.
"""
from dataclasses import dataclass
import logging
from typing import Any

import requests

from backend.services.current_dataset_context import (
    CurrentDatasetContext,
    set_current_dataset_context,
)
import backend.services.dataset_load_request_queue as dataset_load_request_queue
import backend.services.file_size_limits as file_size_limits
from backend.services.datasets import (
    load_existing_session_dataframe,
    process_dataset_load_request,
    resolve_dataset_load_target,
)
from backend.session import mcp_get, mcp_post

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DatasetLoadServiceResult:
    payload: dict[str, Any]
    status_code: int = 200


class DatasetLoadQueueFull(Exception):
    pass


class DatasetLoadTooLarge(Exception):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def _response_to_service_result(response) -> DatasetLoadServiceResult:
    status_code = getattr(response, "status_code", 200)
    payload = None
    if hasattr(response, "get_json"):
        payload = response.get_json(silent=True)
    elif hasattr(response, "json"):
        payload = response.json()

    if payload is None:
        payload = {}
    return DatasetLoadServiceResult(payload=payload, status_code=status_code)


def _normalize_processor_result(result) -> DatasetLoadServiceResult:
    if isinstance(result, tuple):
        response, status_code = result
        normalized = _response_to_service_result(response)
        return DatasetLoadServiceResult(payload=normalized.payload, status_code=status_code)
    return _response_to_service_result(result)


def evict_stale_dataframes_before_load() -> None:
    try:
        response = mcp_post("/dataframes/evict-stale")
        if response.status_code != 200:
            logger.warning("MCP stale DataFrame eviction returned HTTP %s", response.status_code)
    except requests.exceptions.ConnectionError:
        logger.warning("Could not connect to MCP server to evict stale DataFrames before dataset load")
    except requests.exceptions.RequestException as exc:
        logger.warning("Could not evict stale DataFrames before dataset load: %s", exc)


def get_current_session_dataframe_status(session_id: str) -> dict[str, Any]:
    try:
        response = mcp_get("/dataframe/current-session", session_id=session_id)
    except requests.exceptions.ConnectionError:
        logger.warning("Could not connect to MCP server to check current session DataFrame")
        return {"dataset": None, "loaded": False, "cache_hit": False}
    except requests.exceptions.RequestException as exc:
        logger.warning("Could not check current session DataFrame: %s", exc)
        return {"dataset": None, "loaded": False, "cache_hit": False}

    if response.status_code != 200:
        logger.warning("MCP current session DataFrame check returned HTTP %s", response.status_code)
        return {"dataset": None, "loaded": False, "cache_hit": False}

    data = response.json()
    dataset = data.get("dataset")
    return {
        "dataset": dataset,
        "loaded": bool(data.get("loaded", dataset is not None)),
        "cache_hit": bool(data.get("cache_hit", dataset is not None)),
    }


def load_dataset_from_request_context(
    load_request: dataset_load_request_queue.DatasetLoadRequest,
    *,
    clear_chat_history: bool,
) -> DatasetLoadServiceResult:
    target = resolve_dataset_load_target(load_request)
    current_status = get_current_session_dataframe_status(load_request.session_id)
    if (
        current_status.get("dataset") == target.file_snapshot_path
        and current_status.get("cache_hit")
    ):
        result = _normalize_processor_result(
            load_existing_session_dataframe(
                load_request,
                target,
                clear_chat_history=clear_chat_history,
            )
        )
    else:
        evict_stale_dataframes_before_load()
        try:
            result = _normalize_processor_result(
                dataset_load_request_queue.get_dataset_load_request_queue().submit_and_wait(
                    load_request,
                    lambda entry: process_dataset_load_request(
                        entry,
                        clear_chat_history=clear_chat_history,
                    ),
                )
            )
        except dataset_load_request_queue.DatasetLoadRequestQueueFullError as exc:
            raise DatasetLoadQueueFull() from exc
        except file_size_limits.DataFileTooLarge as exc:
            raise DatasetLoadTooLarge(str(exc)) from exc

    if result.status_code < 400:
        set_current_dataset_context(
            load_request.session_id,
            CurrentDatasetContext(
                dataset=load_request.dataset,
                project_id=load_request.project_id,
                dataset_id=load_request.dataset_id,
                snapshot_id=load_request.snapshot_id,
                source_type=load_request.source_type,
                volume_key=load_request.volume_key,
                volume_id=load_request.volume_id,
                snapshot_version=load_request.snapshot_version,
                resolved_file_snapshot_path=target.file_snapshot_path,
            ),
        )

    return result
