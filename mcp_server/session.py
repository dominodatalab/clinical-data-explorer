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
import contextvars
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from functools import lru_cache
import logging
from pathlib import Path
import threading
import time
from typing import Dict, Optional

import objsize
import pandas as pd
from fastapi import HTTPException, Request
from starlette.middleware.base import BaseHTTPMiddleware

from mcp_server import dataframe_cache
from mcp_server.auth import set_auth_header
from mcp_server.config import SESSION_MAX_AGE, SESSION_MAX_COUNT
from mcp_server.services.data_loading import extract_dataset_metadata, load_dataset
from mcp_server.services.httpclient import get_current_user

logger = logging.getLogger(__name__)

_current_user_id: contextvars.ContextVar[str] = contextvars.ContextVar('current_user_id', default=None)

@dataclass
class LoadedDataEntry:
    file_snapshot_path: str
    last_accessed: float = 0
    # Verbatim file/variable metadata captured at load time. Stored here
    # because Domino-sourced files are downloaded to a temp path that gets
    # deleted right after load, so it can't be re-read on demand later.
    metadata: Optional[dict] = None
    dataframe_size_bytes: int = 0


@dataclass
class DataFrameLoadResult:
    dataframe: pd.DataFrame
    metadata: dict


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


def _drop_unreferenced_cache_entries(file_snapshot_paths: list[str]) -> int:
    """Remove cached DataFrames that no active session references."""
    if not file_snapshot_paths:
        return 0

    sessions = _get_sessions()
    referenced_paths = {session.file_snapshot_path for session in sessions.values()}
    cache = get_cache()
    evicted_count = 0
    for file_snapshot_path in set(file_snapshot_paths) - referenced_paths:
        if cache.pop(file_snapshot_path, None) is not None:
            evicted_count += 1
    return evicted_count


def _evict_stale_sessions():
    """Remove sessions that haven't been accessed recently."""
    sessions = _get_sessions()
    now = time.time()
    stale = [sid for sid, s in sessions.items()
             if now - s.last_accessed > SESSION_MAX_AGE]
    evicted_paths = []
    for sid in stale:
        logger.info(f"Evicting stale session: {sid}")
        evicted_paths.append(sessions[sid].file_snapshot_path)
        del sessions[sid]
    # If still over limit, evict oldest
    if len(sessions) > SESSION_MAX_COUNT:
        by_age = sorted(sessions.items(), key=lambda x: x[1].last_accessed)
        for sid, _ in by_age[:len(sessions) - SESSION_MAX_COUNT]:
            logger.info(f"Evicting session (over limit): {sid}")
            evicted_paths.append(sessions[sid].file_snapshot_path)
            del sessions[sid]
    return SessionEvictionResult(
        evicted_sessions=len(evicted_paths),
        evicted_dataframes=_drop_unreferenced_cache_entries(evicted_paths),
    )


def _set_current_df(df: pd.DataFrame, file_snapshot_path: str, metadata: Optional[dict] = None):
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
        last_accessed=time.time(),
        metadata=metadata,
        dataframe_size_bytes=objsize.get_deep_size(df),
    )
    if previous_file_snapshot_path and previous_file_snapshot_path != file_snapshot_path:
        _drop_unreferenced_cache_entries([previous_file_snapshot_path])
    _evict_stale_sessions()


def _get_session_dataset_name() -> Optional[str]:
    """Get the dataset name for the current session."""
    session_id = _current_user_id.get()
    session = _get_sessions().get(session_id)
    if session:
        return session.file_snapshot_path
    return None


def _create_dataframe_entry(file_snapshot_path: str) -> DataFrameLoadResult:
    df = load_dataset(file_snapshot_path)
    metadata = extract_dataset_metadata(Path(file_snapshot_path))
    return DataFrameLoadResult(dataframe=df, metadata=metadata)


def _create_dataframe_entry_in_thread(file_snapshot_path: str) -> DataFrameLoadResult:
    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="mcp-dataframe-loader") as executor:
        return executor.submit(_create_dataframe_entry, file_snapshot_path).result()


def load_current_df(file_snapshot_path: str) -> pd.DataFrame:
    """Load a dataset file for the current session and cache it."""
    result = _create_dataframe_entry_in_thread(file_snapshot_path)
    _set_current_df(result.dataframe, file_snapshot_path, result.metadata)
    return result.dataframe


def get_current_dataframe_size_bytes() -> int:
    """Return the cached DataFrame size for the current session, or 0 when empty."""
    session_id = _current_user_id.get()
    session = _get_sessions().get(session_id)
    if session is None:
        logger.warning("No loaded DataFrame found for user %s when reading DataFrame size", session_id)
        return 0
    return session.dataframe_size_bytes


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
        evicted_dataframes=_drop_unreferenced_cache_entries([session.file_snapshot_path]),
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


def get_current_df() -> pd.DataFrame:
    """Get the current dataframe for this session, reloading on cache miss."""
    session_id = _current_user_id.get()
    session = _get_sessions().get(session_id)
    if session is None:
        raise HTTPException(status_code=400, detail="No dataset loaded. Please load a dataset first using /dataset/load")

    df_cache = get_cache()
    df = df_cache.get(session.file_snapshot_path)
    if df is None:
        logger.debug("Cache miss for user %s dataset %s; reloading from disk", session_id, session.file_snapshot_path)
        return load_current_df(session.file_snapshot_path)
    return df
