import contextvars
from dataclasses import dataclass
import logging
from pathlib import Path
import threading
import time
from typing import Dict, Optional

import pandas as pd
from fastapi import HTTPException

from mcp_server import dataframe_cache
from mcp_server.config import SESSION_MAX_AGE, SESSION_MAX_COUNT
from mcp_server.services.data_loading import extract_dataset_metadata, load_dataset

logger = logging.getLogger(__name__)

_current_session_id: contextvars.ContextVar[str] = contextvars.ContextVar('session_id', default='default')


@dataclass
class LoadedDataEntry:
    file_snapshot_path: str
    last_accessed: float = 0
    metadata: Optional[dict] = None


_sessions: Dict[str, LoadedDataEntry] = {}
_sessions_lock = threading.RLock()


def get_cache():
    return dataframe_cache.get_cache()


def touch_session(session_id: str) -> None:
    with _sessions_lock:
        session = _sessions.get(session_id)
        if session is not None:
            session.last_accessed = time.time()


def _evict_stale_sessions() -> None:
    now = time.time()
    with _sessions_lock:
        stale = [
            sid for sid, session in _sessions.items()
            if now - session.last_accessed > SESSION_MAX_AGE
        ]
        for sid in stale:
            logger.info("Evicting stale session: %s", sid)
            del _sessions[sid]

        if len(_sessions) > SESSION_MAX_COUNT:
            by_age = sorted(_sessions.items(), key=lambda item: item[1].last_accessed)
            for sid, _ in by_age[:len(_sessions) - SESSION_MAX_COUNT]:
                logger.info("Evicting session (over limit): %s", sid)
                del _sessions[sid]


def set_session_dataframe(
    session_id: str,
    df: pd.DataFrame,
    file_snapshot_path: str,
    metadata: Optional[dict] = None,
) -> None:
    try:
        dataframe_cache.save_to_cache(file_snapshot_path, df)
    except dataframe_cache.DataFrameCacheValueTooLarge as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc

    with _sessions_lock:
        _sessions[session_id] = LoadedDataEntry(
            file_snapshot_path=file_snapshot_path,
            last_accessed=time.time(),
            metadata=metadata,
        )
    _evict_stale_sessions()


def get_session_entry(session_id: str) -> LoadedDataEntry | None:
    with _sessions_lock:
        return _sessions.get(session_id)


def get_session_dataset_name(session_id: str) -> str | None:
    session = get_session_entry(session_id)
    if session is None:
        return None
    return session.file_snapshot_path


def get_session_metadata(session_id: str) -> dict:
    session = get_session_entry(session_id)
    if session is None:
        raise HTTPException(status_code=400, detail="No dataset loaded. Please load a dataset first using /dataset/load")
    if session.metadata is not None:
        return session.metadata
    return extract_dataset_metadata(Path(session.file_snapshot_path))


def load_df_for_session(session_id: str, file_snapshot_path: str) -> pd.DataFrame:
    df = load_dataset(file_snapshot_path)
    metadata = extract_dataset_metadata(Path(file_snapshot_path))
    set_session_dataframe(session_id, df, file_snapshot_path, metadata)
    return df


def load_current_df(file_snapshot_path: str) -> pd.DataFrame:
    return load_df_for_session(_current_session_id.get(), file_snapshot_path)


def get_df_for_session(session_id: str) -> pd.DataFrame:
    session = get_session_entry(session_id)
    if session is None:
        raise HTTPException(status_code=400, detail="No dataset loaded. Please load a dataset first using /dataset/load")

    df = dataframe_cache.get_from_cache(session.file_snapshot_path)
    if df is None:
        logger.debug("Cache miss for session %s dataset %s; reloading from disk", session_id, session.file_snapshot_path)
        return load_df_for_session(session_id, session.file_snapshot_path)
    touch_session(session_id)
    return df


def get_current_df() -> pd.DataFrame:
    return get_df_for_session(_current_session_id.get())
