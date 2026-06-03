"""Session helpers for MCP routes.

In production, the authoritative session map and DataFrame cache live in the
separate MCP cache service configured by `MCP_CACHE_SERVER_URL`. MCP workers
keep only request-local session context and fetch DataFrames from that
singleton cache layer.
"""
import contextvars
import pickle
import warnings
from urllib.parse import quote

import pandas as pd
import requests
from fastapi import HTTPException, Request
from starlette.middleware.base import BaseHTTPMiddleware

from mcp_server.config import MCP_CACHE_SERVER_URL

_current_session_id: contextvars.ContextVar[str] = contextvars.ContextVar('session_id', default='default')


def _get_local_store():
    # Single-worker dev/test fallback only. Production MCP workers must use the
    # singleton MCP cache service so session and DataFrame state is shared
    # across worker processes.
    warnings.warn(
        "Using in-process MCP cache store fallback. Do not use this path in production; "
        "set MCP_CACHE_SERVER_URL so MCP workers share cache state through the cache service.",
        RuntimeWarning,
        stacklevel=2,
    )
    from mcp_cache_server import store
    return store


def _cache_url(path: str) -> str:
    return f"{MCP_CACHE_SERVER_URL.rstrip('/')}{path}"


def _session_path(session_id: str, suffix: str) -> str:
    return f"/sessions/{quote(session_id, safe='')}{suffix}"


def _raise_for_cache_response(response: requests.Response) -> None:
    if response.status_code < 400:
        return
    try:
        detail = response.json().get("detail")
    except ValueError:
        detail = response.text
    raise HTTPException(status_code=response.status_code, detail=detail)


def _cache_get_json(session_id: str, suffix: str) -> dict:
    response = requests.get(_cache_url(_session_path(session_id, suffix)), timeout=120)
    _raise_for_cache_response(response)
    return response.json()


def _cache_get_dataframe(session_id: str) -> pd.DataFrame:
    response = requests.get(_cache_url(_session_path(session_id, "/dataframe")), timeout=120)
    _raise_for_cache_response(response)
    return pickle.loads(response.content)


def _cache_load_dataframe(session_id: str, file_snapshot_path: str) -> pd.DataFrame:
    response = requests.post(
        _cache_url(_session_path(session_id, "/load")),
        params={"file_snapshot_path": file_snapshot_path},
        timeout=120,
    )
    _raise_for_cache_response(response)
    return pickle.loads(response.content)


def _cache_set_dataframe(session_id: str, df: pd.DataFrame, file_snapshot_path: str) -> None:
    payload = {
        "dataframe": df,
        "file_snapshot_path": file_snapshot_path,
    }
    response = requests.put(
        _cache_url(_session_path(session_id, "/dataframe")),
        data=pickle.dumps(payload, protocol=pickle.HIGHEST_PROTOCOL),
        timeout=120,
    )
    _raise_for_cache_response(response)


class SessionMiddleware(BaseHTTPMiddleware):
    """Extract X-Session-Id header and set it in contextvars for the request."""
    async def dispatch(self, request: Request, call_next):
        session_id = request.headers.get("x-session-id", "default")
        _current_session_id.set(session_id)
        if MCP_CACHE_SERVER_URL:
            try:
                requests.post(_cache_url(_session_path(session_id, "/touch")), timeout=5)
            except requests.RequestException:
                pass
        else:
            # Single-worker dev/test fallback only; production must use the
            # cache-service branch above.
            _get_local_store().touch_session(session_id)
        response = await call_next(request)
        return response


def _set_current_df(df: pd.DataFrame, file_snapshot_path: str):
    session_id = _current_session_id.get()
    if MCP_CACHE_SERVER_URL:
        _cache_set_dataframe(session_id, df, file_snapshot_path)
    else:
        # Single-worker dev/test fallback only; production must use the
        # cache-service branch above.
        _get_local_store().set_session_dataframe(session_id, df, file_snapshot_path)


def _get_session_dataset_name() -> str | None:
    session_id = _current_session_id.get()
    if MCP_CACHE_SERVER_URL:
        return _cache_get_json(session_id, "/dataset_name").get("dataset")
    # Single-worker dev/test fallback only; production must use the
    # cache-service branch above.
    return _get_local_store().get_session_dataset_name(session_id)


def load_df_for_session(session_id: str, file_snapshot_path: str) -> pd.DataFrame:
    if MCP_CACHE_SERVER_URL:
        return _cache_load_dataframe(session_id, file_snapshot_path)
    # Single-worker dev/test fallback only; production must use the
    # cache-service branch above.
    return _get_local_store().load_df_for_session(session_id, file_snapshot_path)


def load_current_df(file_snapshot_path: str) -> pd.DataFrame:
    return load_df_for_session(_current_session_id.get(), file_snapshot_path)


def get_current_df() -> pd.DataFrame:
    session_id = _current_session_id.get()
    if MCP_CACHE_SERVER_URL:
        return _cache_get_dataframe(session_id)
    # Single-worker dev/test fallback only; production must use the
    # cache-service branch above.
    return _get_local_store().get_df_for_session(session_id)
