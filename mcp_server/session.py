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

Per the plan watch-out for §2: `_sessions` is module-level state that
every route reaches via `get_current_df()`. After this extraction, every
caller imports `get_current_df` from this module — there is no copy and
no DataFrame-as-parameter passing.
"""
import contextvars
from dataclasses import dataclass
import logging
import time
from typing import Dict, Optional

import pandas as pd
from fastapi import HTTPException, Request
from starlette.middleware.base import BaseHTTPMiddleware

from mcp_server.config import SESSION_MAX_AGE, SESSION_MAX_COUNT
from mcp_server.dataframe_cache import get_cache
from mcp_server.services.data_loading import load_dataset

logger = logging.getLogger(__name__)


# ===== SESSION-BASED DATASET STORAGE =====
# Each user session gets its own DataFrame so concurrent users don't clobber each other.
# Session ID comes from the X-Session-Id header (set by the Flask proxy).
# A "default" session is used when no header is present (normal single-user mode).

_current_session_id: contextvars.ContextVar[str] = contextvars.ContextVar('session_id', default='default')

# TODO not thread safe

@dataclass
class LoadedDataEntry:
    file_snapshot_path: str
    last_accessed: float = 0

_sessions: Dict[str, LoadedDataEntry] = {}

class SessionMiddleware(BaseHTTPMiddleware):
    """Extract X-Session-Id header and set it in contextvars for the request."""
    async def dispatch(self, request: Request, call_next):
        session_id = request.headers.get("x-session-id", "default")
        _current_session_id.set(session_id)
        # Touch the session so it stays alive
        if session_id in _sessions:
            _sessions[session_id].last_accessed = time.time()
        response = await call_next(request)
        return response


def _evict_stale_sessions():
    """Remove sessions that haven't been accessed recently."""
    now = time.time()
    stale = [sid for sid, s in _sessions.items()
             if now - s.last_accessed > SESSION_MAX_AGE]
    for sid in stale:
        logger.info(f"Evicting stale session: {sid}")
        del _sessions[sid]
    # If still over limit, evict oldest
    if len(_sessions) > SESSION_MAX_COUNT:
        by_age = sorted(_sessions.items(), key=lambda x: x[1].last_accessed)
        for sid, _ in by_age[:len(_sessions) - SESSION_MAX_COUNT]:
            logger.info(f"Evicting session (over limit): {sid}")
            del _sessions[sid]


def _set_current_df(df: pd.DataFrame, file_snapshot_path: str):
    """Store a DataFrame for the current session."""
    session_id = _current_session_id.get()

    df_cache = get_cache()
    df_cache[file_snapshot_path] = df

    _sessions[session_id] = LoadedDataEntry(
        file_snapshot_path=file_snapshot_path,
        last_accessed=time.time()
    )
    _evict_stale_sessions()


def _get_session_dataset_name() -> Optional[str]:
    """Get the dataset name for the current session."""
    session_id = _current_session_id.get()
    session = _sessions.get(session_id)
    if session:
        return session.file_snapshot_path
    return None


def load_current_df(file_snapshot_path: str) -> pd.DataFrame:
    """Load a dataset file for the current session and cache it."""
    df = load_dataset(file_snapshot_path)
    _set_current_df(df, file_snapshot_path)
    return df


def get_current_df() -> pd.DataFrame:
    """Get the current dataframe for this session, reloading on cache miss."""
    session_id = _current_session_id.get()
    session = _sessions.get(session_id)
    if session is None:
        raise HTTPException(status_code=400, detail="No dataset loaded. Please load a dataset first using /dataset/load")

    df_cache = get_cache()
    df = df_cache.get(session.file_snapshot_path)
    if df is None:
        # if there is a cache miss here, we have already deleted the file snapshot on disk
        # reloading would mean that this server needs to be able to authorize calling the data APIs
        # it needs the user's identity to do that
        # so we can't reload the dataset from here
        logger.debug("Cache miss for session %s dataset %s; reloading from disk", session_id, session.file_snapshot_path)
    return df
