import pandas as pd
import pytest
from cachetools import LRUCache
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

import mcp_server.dataframe_cache as dataframe_cache
import mcp_server.session as session_module


@pytest.fixture(autouse=True)
def clear_session_state():
    session_module._sessions.clear()
    session_module._current_session_id.set("default")
    session_module.get_cache().clear()
    yield
    session_module._sessions.clear()
    session_module._current_session_id.set("default")
    session_module.get_cache().clear()


def test_set_current_df_stores_dataframe_and_session_metadata():
    df = pd.DataFrame({"subject_id": [1, 2], "arm": ["A", "B"]})
    session_module._current_session_id.set("session-1")

    session_module._set_current_df(df, "adsl.csv")

    assert session_module._get_session_dataset_name() == "adsl.csv"
    assert session_module._sessions["session-1"].file_snapshot_path == "adsl.csv"
    pd.testing.assert_frame_equal(session_module.get_current_df(), df)


def test_get_current_df_raises_when_no_dataset_is_loaded():
    session_module._current_session_id.set("missing-session")

    with pytest.raises(HTTPException) as excinfo:
        session_module.get_current_df()

    exc = excinfo.value
    assert exc.status_code == 400
    assert exc.detail == "No dataset loaded. Please load a dataset first using /dataset/load"


def test_get_current_df_reloads_when_session_metadata_exists_but_cache_entry_is_missing(monkeypatch):
    reloaded_df = pd.DataFrame({"subject_id": [99], "arm": ["Reloaded"]})
    session_module._current_session_id.set("session-2")
    session_module._sessions["session-2"] = session_module.LoadedDataEntry(
        file_snapshot_path="adae.csv",
        last_accessed=50.0,
    )
    load_calls = []

    def fake_load_dataset(file_snapshot_path):
        load_calls.append(file_snapshot_path)
        return reloaded_df

    monkeypatch.setattr(session_module, "load_dataset", fake_load_dataset)

    df = session_module.get_current_df()

    assert load_calls == ["adae.csv"]
    pd.testing.assert_frame_equal(df, reloaded_df)
    pd.testing.assert_frame_equal(session_module.get_cache()["adae.csv"], reloaded_df)


def test_evict_stale_sessions_removes_idle_sessions(monkeypatch):
    monkeypatch.setattr(session_module, "SESSION_MAX_AGE", 10)
    monkeypatch.setattr(session_module.time, "time", lambda: 100.0)
    session_module._sessions.update(
        {
            "stale": session_module.LoadedDataEntry(file_snapshot_path="stale.csv", last_accessed=89.0),
            "fresh": session_module.LoadedDataEntry(file_snapshot_path="fresh.csv", last_accessed=95.0),
        }
    )

    session_module._evict_stale_sessions()

    assert "stale" not in session_module._sessions
    assert "fresh" in session_module._sessions


def test_evict_stale_sessions_enforces_session_count_limit(monkeypatch):
    monkeypatch.setattr(session_module, "SESSION_MAX_AGE", 1000)
    monkeypatch.setattr(session_module, "SESSION_MAX_COUNT", 2)
    monkeypatch.setattr(session_module.time, "time", lambda: 100.0)
    session_module._sessions.update(
        {
            "oldest": session_module.LoadedDataEntry(file_snapshot_path="one.csv", last_accessed=70.0),
            "middle": session_module.LoadedDataEntry(file_snapshot_path="two.csv", last_accessed=80.0),
            "newest": session_module.LoadedDataEntry(file_snapshot_path="three.csv", last_accessed=90.0),
        }
    )

    session_module._evict_stale_sessions()

    assert "oldest" not in session_module._sessions
    assert set(session_module._sessions) == {"middle", "newest"}


def test_session_middleware_sets_session_id_and_touches_existing_session(monkeypatch):
    monkeypatch.setattr(session_module.time, "time", lambda: 123.0)
    session_module._sessions["session-3"] = session_module.LoadedDataEntry(
        file_snapshot_path="adlb.csv",
        last_accessed=1.0,
    )

    app = FastAPI()
    app.add_middleware(session_module.SessionMiddleware)

    @app.get("/session")
    async def read_session():
        return {"session_id": session_module._current_session_id.get()}

    client = TestClient(app)

    response = client.get("/session", headers={"X-Session-Id": "session-3"})

    assert response.status_code == 200
    assert response.json() == {"session_id": "session-3"}
    assert session_module._sessions["session-3"].last_accessed == 123.0


def test_session_middleware_defaults_session_id_when_header_is_missing():
    app = FastAPI()
    app.add_middleware(session_module.SessionMiddleware)

    @app.get("/session")
    async def read_session():
        return {"session_id": session_module._current_session_id.get()}

    client = TestClient(app)

    response = client.get("/session")

    assert response.status_code == 200
    assert response.json() == {"session_id": "default"}


def test_dataset_load_reports_when_dataframe_is_too_large_for_cache(_mcp_app, monkeypatch, tmp_path):
    dataset = tmp_path / "too_big.csv"
    dataset.write_text("subject_id,arm\n1,A\n2,B\n", encoding="utf-8")
    tiny_cache = LRUCache(maxsize=1, getsizeof=lambda value: 2)
    monkeypatch.setattr(dataframe_cache, "get_cache", lambda: tiny_cache)

    client = TestClient(_mcp_app, raise_server_exceptions=False)

    response = client.post("/dataset/load", params={"file_snapshot_path": str(dataset)})

    assert response.status_code == 413
    assert response.json() == {
        "detail": (
            f"Dataset '{dataset}' is too large to load right now. "
            "Try a smaller file or ask your administrator to increase the amount of memory available."
        )
    }
