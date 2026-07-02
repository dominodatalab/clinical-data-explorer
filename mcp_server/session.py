"""Per-session DataFrame storage + the middleware that wires sessions to requests.

Extracted from `mcp_server/app.py` as step 2.3 of REFACTOR_PLAN.md §2
(mirror of `backend/session.py` on the Flask side, but adapted for the
FastAPI/Starlette middleware world).

Each user session gets its own DataFrame so concurrent users don't clobber
each other. The session ID comes from the `X-Session-Id` request header
(set by the Flask proxy). A `"default"` session ID is used when no header
is present (normal single-user mode). The active session ID is stored in
a `contextvars.ContextVar` so it's correctly isolated per request even
under concurrent async load.

Per the plan watch-out for §2: the session store is module-level state that
every route reaches via `get_current_df()`. After this extraction, every
caller imports `get_current_df` from this module — there is no copy and
no DataFrame-as-parameter passing.
"""
from __future__ import annotations

import contextvars
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from functools import lru_cache
import logging
import os
from pathlib import Path
import threading
import time
from typing import Dict, Optional

import objsize
import pandas as pd
import requests
from fastapi import HTTPException, Request
from starlette.middleware.base import BaseHTTPMiddleware

from mcp_server import dataframe_cache
from mcp_server.auth import get_passthrough_token, set_auth_header
from mcp_server.config import BACKEND_SERVER_URL, SESSION_MAX_AGE, SESSION_MAX_COUNT, DATAFRAME_MAX_AGE
from mcp_server.services.data_loading import extract_dataset_metadata, load_dataset
from mcp_server.services.httpclient import get_current_user

logger = logging.getLogger(__name__)

_current_user_id: contextvars.ContextVar[str] = contextvars.ContextVar('current_user_id', default=None)

@dataclass
class LoadedDataEntry:
    file_snapshot_path: str
    dataframe_exists: bool = True
    last_accessed: float = 0
    # Verbatim file/variable metadata captured at load time. Stored here
    # because Domino-sourced files are downloaded to a temp path that gets
    # deleted right after load, so it can't be re-read on demand later.
    metadata: Optional[dict] = None
    dataframe_size_bytes: int = 0
    source_file_size_bytes: int = 0
    dataframe_last_accessed: Optional[float] = None
    reload_context: Optional[DatasetReloadContextEntry] = None


@dataclass
class DataFrameLoadResult:
    dataframe: pd.DataFrame
    metadata: dict
    source_file_size_bytes: int

@dataclass
class DatasetReloadContextEntry:
    load_body: dict
    last_accessed: float = 0

@dataclass(frozen=True)
class SessionEvictionResult:
    evicted_sessions: int
    evicted_dataframes: int


_sessions: Dict[str, LoadedDataEntry] = {}


@lru_cache(maxsize=1)
def _get_sessions():
    return _sessions


def get_cache():
    return dataframe_cache.get_cache()

def _get_session_id():
    """Return the current user's ID"""
    user_id = _current_user_id.get()

    if not user_id:
        user_id = get_current_user()['id']
        _current_user_id.set(user_id)

    return user_id

class SessionMiddleware(BaseHTTPMiddleware):
    """Extract X-Session-Id header and set it in contextvars for the request."""
    async def dispatch(self, request: Request, call_next):
        set_auth_header(request.headers)

        session_id = _get_session_id()

        _current_user_id.set(session_id)

        request.state.session_eviction_result = _evict_stale_sessions()
        # Touch the session so it stays alive
        sessions = _get_sessions()
        if session_id in sessions:
            sessions[session_id].last_accessed = time.time()
        response = await call_next(request)
        return response


def _drop_unreferenced_cache_entries(unreferenced_paths: list[(str, str)]) -> int:
    """Remove cached DataFrames that no active session references."""
    if not unreferenced_paths:
        return 0

    cache = get_cache()
    evicted_count = 0
    sessions = _get_sessions()
    for (sid, file_snapshot_path) in unreferenced_paths:
        if any(
            active_sid != sid and active_session.file_snapshot_path == file_snapshot_path
            for active_sid, active_session in sessions.items()
        ):
            continue
        if cache.pop(file_snapshot_path, None) is not None:
            evicted_count += 1
            if sid in sessions:
                sessions[sid].dataframe_exists = False
    return evicted_count


def _evict_stale_sessions():
    """Remove sessions that haven't been accessed recently."""
    sessions = _get_sessions()
    now = time.time()
    stale = [sid for sid, s in sessions.items()
             if now - s.last_accessed > SESSION_MAX_AGE]
    evicted_paths = []
    evicted_sessions = []
    for sid in stale:
        logger.info(f"Evicting stale session: {sid}")
        evicted_paths.append((sid, sessions[sid].file_snapshot_path))
        evicted_sessions.append(sessions[sid].file_snapshot_path)
        del sessions[sid]

    for sid, s in sessions.items():
        if s.dataframe_last_accessed and now - s.dataframe_last_accessed > DATAFRAME_MAX_AGE:
            logger.info(f"Evicting stale dataframe: {sid}")

            # delete only the dataframe, but want to keep the session, so don't delete the session entry, but do update
            # evicted paths
            evicted_paths.append((sid, sessions[sid].file_snapshot_path))

    # If still over limit, evict oldest
    if len(sessions) > SESSION_MAX_COUNT:
        by_age = sorted(sessions.items(), key=lambda x: x[1].last_accessed)
        for sid, _ in by_age[:len(sessions) - SESSION_MAX_COUNT]:
            logger.info(f"Evicting session (over limit): {sid}")
            evicted_paths.append((sid, sessions[sid].file_snapshot_path))
            evicted_sessions.append(sessions[sid].file_snapshot_path)
            del sessions[sid]
    return SessionEvictionResult(
        evicted_sessions=len(evicted_sessions),
        evicted_dataframes=_drop_unreferenced_cache_entries(evicted_paths),
    )


def _get_source_file_size_bytes(file_snapshot_path: str) -> int:
    try:
        return os.path.getsize(file_snapshot_path)
    except OSError:
        logger.warning("Could not determine source file size for %s", file_snapshot_path)
        return 0


def _set_current_df(
    df: pd.DataFrame,
    file_snapshot_path: str,
    reload_context: Optional[dict] = None,
    metadata: Optional[dict] = None,
    source_file_size_bytes: Optional[int] = None,
):
    """Store a DataFrame for the current session."""
    session_id = _current_user_id.get()
    sessions = _get_sessions()
    previous_session = sessions.get(session_id)
    previous_file_snapshot_path = previous_session.file_snapshot_path if previous_session else None

    try:
        dataframe_cache.save_to_cache(file_snapshot_path, df)
    except dataframe_cache.DataFrameCacheValueTooLarge as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc

    sessions[session_id] = LoadedDataEntry(
        file_snapshot_path=file_snapshot_path,
        dataframe_exists=True,
        last_accessed=time.time(),
        metadata=metadata,
        dataframe_size_bytes=objsize.get_deep_size(df),
        source_file_size_bytes=(
            source_file_size_bytes
            if source_file_size_bytes is not None
            else _get_source_file_size_bytes(file_snapshot_path)
        ),
        reload_context=(
            DatasetReloadContextEntry(load_body=reload_context, last_accessed=time.time())
            if reload_context
            else None
        ),
    )
    if previous_file_snapshot_path and previous_file_snapshot_path != file_snapshot_path:
        _drop_unreferenced_cache_entries([(session_id, previous_file_snapshot_path)])
    _evict_stale_sessions()


def _get_session_dataset_name() -> Optional[str]:
    """Get the dataset name for the current session."""
    session_id = _current_user_id.get()
    session = _get_sessions().get(session_id)
    if session:
        return session.file_snapshot_path
    return None


def has_current_df(file_snapshot_path: str) -> bool:
    """Return true when the current session has this dataset cached."""
    session_id = _current_user_id.get()
    session = _get_sessions().get(session_id)
    if session is None or session.file_snapshot_path != file_snapshot_path:
        return False
    return get_cache().get(file_snapshot_path) is not None


def _create_dataframe_entry(file_snapshot_path: str) -> DataFrameLoadResult:
    source_file_size_bytes = _get_source_file_size_bytes(file_snapshot_path)
    df = load_dataset(file_snapshot_path)
    metadata = extract_dataset_metadata(Path(file_snapshot_path))
    return DataFrameLoadResult(
        dataframe=df,
        metadata=metadata,
        source_file_size_bytes=source_file_size_bytes,
    )


def _create_dataframe_entry_in_thread(file_snapshot_path: str) -> DataFrameLoadResult:
    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="mcp-dataframe-loader") as executor:
        return executor.submit(_create_dataframe_entry, file_snapshot_path).result()


def load_current_df(file_snapshot_path: str, reload_context: Optional[dict] = None) -> pd.DataFrame:
    """Load a dataset file for the current session and cache it."""
    if has_current_df(file_snapshot_path):
        return get_cache()[file_snapshot_path]

    result = _create_dataframe_entry_in_thread(file_snapshot_path)
    _set_current_df(
        result.dataframe,
        file_snapshot_path,
        reload_context=reload_context,
        metadata=result.metadata,
        source_file_size_bytes=result.source_file_size_bytes,
    )
    return result.dataframe


def get_current_dataframe_size_bytes() -> int:
    """Return the cached DataFrame size for the current session, or 0 when empty."""
    session_id = _current_user_id.get()
    session = _get_sessions().get(session_id)
    if session is None:
        logger.warning("No loaded DataFrame found for user %s when reading DataFrame size", session_id)
        return 0
    return session.dataframe_size_bytes


def get_current_source_file_size_bytes() -> int:
    """Return the loaded source file size for the current session, or 0 when empty."""
    session_id = _current_user_id.get()
    session = _get_sessions().get(session_id)
    if session is None:
        logger.warning("No loaded DataFrame found for user %s when reading source file size", session_id)
        return 0
    return session.source_file_size_bytes


def download_dataset_file(load_body: dict) -> str:
    """Ask the backend to redownload the current dataset file and return its cached path."""
    token = get_passthrough_token()
    headers = {'Authorization': f'Bearer {token}'} if token else {}
    download_body = dict(load_body)
    download_body["createDataframe"] = False
    try:
        response = requests.post(
            f"{BACKEND_SERVER_URL}/dataset/load",
            json=download_body,
            headers=headers,
            timeout=120,
        )
    except requests.exceptions.ConnectionError as exc:
        raise HTTPException(status_code=503, detail="Could not connect to backend server") from exc

    if response.status_code != 200:
        try:
            detail = response.json().get('error') or response.json().get('detail')
        except ValueError:
            detail = response.text
        raise HTTPException(status_code=response.status_code, detail=detail or "Failed to download dataset file")

    file_snapshot_path = response.json().get("file_snapshot_path")
    if not file_snapshot_path:
        raise HTTPException(status_code=500, detail="Backend did not return file_snapshot_path")
    return file_snapshot_path


def evict_current_session_dataframe() -> SessionEvictionResult:
    """Remove the current session and its unreferenced cached DataFrame."""
    session_id = _current_user_id.get()
    sessions = _get_sessions()
    session = sessions.pop(session_id, None)
    if session is None:
        logger.warning("No loaded DataFrame found for user %s when evicting current session", session_id)
        return SessionEvictionResult(evicted_sessions=0, evicted_dataframes=0)

    return SessionEvictionResult(
        evicted_sessions=1,
        evicted_dataframes=_drop_unreferenced_cache_entries([(session_id, session.file_snapshot_path)]),
    )


def get_current_metadata() -> dict:
    """Return the verbatim file metadata captured for the current session."""
    session_id = _current_user_id.get()
    session = _get_sessions().get(session_id)
    if session is None:
        raise HTTPException(status_code=400, detail="No dataset loaded. Please load a dataset first using /dataset/load")
    if session.metadata is not None:
        return session.metadata
    # Fallback for sessions loaded before metadata capture existed: extract
    # now if the source file is still present (no-op safe — never raises).
    return extract_dataset_metadata(Path(session.file_snapshot_path))


def get_current_df(reload_context: Optional[dict] = None) -> pd.DataFrame:
    """Get the current dataframe for this session, reloading on cache miss."""
    session_id = _current_user_id.get()
    session = _get_sessions().get(session_id)
    if session is None:
        raise HTTPException(status_code=400, detail="No dataset loaded. Please load a dataset first using /dataset/load")

    df_cache = get_cache()
    df = df_cache.get(session.file_snapshot_path)
    if df is None:

        if os.path.exists(session.file_snapshot_path):
            logger.debug("Cache miss for user %s dataset %s; reloading from disk", session_id, session.file_snapshot_path)
            return load_current_df(session.file_snapshot_path, None)
        else:
            load_body = reload_context or (session.reload_context.load_body if session.reload_context else None)

            if not load_body:
                raise HTTPException(status_code=500, detail="Could not reload dataframe, because no reload context exists")

            logger.debug("Cache miss for user %s dataset %s; reloading from reload context", session_id, session.file_snapshot_path)
            file_snapshot_path = download_dataset_file(load_body)
            return load_current_df(file_snapshot_path, load_body)

    return df
