import threading
import time

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
    session_module._get_dataset_reload_context_cache().clear()
    session_module._current_user_id.set("default")
    session_module.get_cache().clear()
    yield
    session_module._sessions.clear()
    session_module._get_dataset_reload_context_cache().clear()
    session_module._current_user_id.set("default")
    session_module.get_cache().clear()


def test_set_current_df_stores_dataframe_and_session_metadata():
    df = pd.DataFrame({"subject_id": [1, 2], "arm": ["A", "B"]})
    session_module._current_user_id.set("session-1")

    session_module._set_current_df(df, "adsl.csv")

    assert session_module._get_session_dataset_name() == "adsl.csv"
    assert session_module._sessions["session-1"].file_snapshot_path == "adsl.csv"
    assert session_module._sessions["session-1"].dataframe_size_bytes > 0
    pd.testing.assert_frame_equal(session_module.get_current_df(), df)


def test_set_current_df_stores_loaded_dataframe_deep_size(monkeypatch):
    df = pd.DataFrame({"subject_id": [1]})
    session_module._current_user_id.set("session-size")

    monkeypatch.setattr(session_module.objsize, "get_deep_size", lambda dataframe: 1234)

    session_module._set_current_df(df, "adsl.csv")

    assert session_module._sessions["session-size"].dataframe_size_bytes == 1234


def test_set_current_df_stores_source_file_size(tmp_path):
    data_file = tmp_path / "adsl.csv"
    data_file.write_text("subject_id\n1\n", encoding="utf-8")
    df = pd.DataFrame({"subject_id": [1]})
    session_module._current_user_id.set("session-file-size")

    session_module._set_current_df(df, str(data_file))

    assert session_module._sessions["session-file-size"].source_file_size_bytes == data_file.stat().st_size


def test_get_current_df_raises_when_no_dataset_is_loaded():
    session_module._current_user_id.set("missing-session")

    with pytest.raises(HTTPException) as excinfo:
        session_module.get_current_df()

    exc = excinfo.value
    assert exc.status_code == 400
    assert exc.detail == "No dataset loaded."


def test_get_current_df_reloads_when_session_metadata_exists_but_cache_entry_is_missing(monkeypatch, tmp_path):
    reloaded_df = pd.DataFrame({"subject_id": [99], "arm": ["Reloaded"]})
    data_file = tmp_path / "adae.csv"
    data_file.write_text("subject_id,arm\n99,Reloaded\n")
    session_module._current_user_id.set("session-2")
    session_module._sessions["session-2"] = session_module.LoadedDataEntry(
        file_snapshot_path=str(data_file),
        last_accessed=50.0,
    )
    load_calls = []

    def fake_load_dataset(file_snapshot_path):
        load_calls.append(file_snapshot_path)
        return reloaded_df

    monkeypatch.setattr(session_module, "load_dataset", fake_load_dataset)

    df = session_module.get_current_df()

    assert load_calls == [str(data_file)]
    pd.testing.assert_frame_equal(df, reloaded_df)
    pd.testing.assert_frame_equal(session_module.get_cache()[str(data_file)], reloaded_df)


def test_get_current_df_reports_expired_when_cache_entry_and_source_file_are_missing(tmp_path):
    missing_file = tmp_path / "domino_api_datasets" / "netapp" / "vol-1" / "unset_snapshot_id" / "adsl.csv"
    session_module._current_user_id.set("missing-source-file")
    session_module._sessions["missing-source-file"] = session_module.LoadedDataEntry(
        file_snapshot_path=str(missing_file),
        dataframe_size_bytes=123,
    )

    with pytest.raises(HTTPException) as excinfo:
        session_module.get_current_df()

    exc = excinfo.value
    assert exc.status_code == 400
    assert exc.detail == "No dataset loaded."
    assert session_module._sessions["missing-source-file"].has_cached_dataframe is False
    assert session_module._sessions["missing-source-file"].dataframe_size_bytes == 0


def test_get_current_df_raises_when_dataframe_was_expired():
    session_module._current_user_id.set("expired-dataframe")
    session_module._sessions["expired-dataframe"] = session_module.LoadedDataEntry(
        file_snapshot_path="adae.csv",
        has_cached_dataframe=False,
    )

    with pytest.raises(HTTPException) as excinfo:
        session_module.get_current_df()

    exc = excinfo.value
    assert exc.status_code == 400
    assert exc.detail == "No dataset loaded."


def test_evict_stale_sessions_removes_idle_sessions(monkeypatch):
    monkeypatch.setattr(session_module, "SESSION_MAX_AGE", 10)
    monkeypatch.setattr(session_module, "DATAFRAME_MAX_AGE", 1000)
    monkeypatch.setattr(session_module.time, "time", lambda: 100.0)
    stale_df = pd.DataFrame({"subject_id": [1]})
    fresh_df = pd.DataFrame({"subject_id": [2]})
    session_module.get_cache()["stale.csv"] = stale_df
    session_module.get_cache()["fresh.csv"] = fresh_df
    session_module._sessions.update(
        {
            "stale": session_module.LoadedDataEntry(file_snapshot_path="stale.csv", last_accessed=89.0),
            "fresh": session_module.LoadedDataEntry(file_snapshot_path="fresh.csv", last_accessed=95.0),
        }
    )

    result = session_module._evict_stale_sessions()

    assert "stale" not in session_module._sessions
    assert "fresh" in session_module._sessions
    assert result == session_module.SessionEvictionResult(evicted_sessions=1, evicted_dataframes=1)
    assert "stale.csv" not in session_module.get_cache()
    pd.testing.assert_frame_equal(session_module.get_cache()["fresh.csv"], fresh_df)


def test_evict_stale_sessions_enforces_session_count_limit(monkeypatch):
    monkeypatch.setattr(session_module, "SESSION_MAX_AGE", 1000)
    monkeypatch.setattr(session_module, "DATAFRAME_MAX_AGE", 1000)
    monkeypatch.setattr(session_module, "SESSION_MAX_COUNT", 2)
    monkeypatch.setattr(session_module.time, "time", lambda: 100.0)
    oldest_df = pd.DataFrame({"subject_id": [1]})
    middle_df = pd.DataFrame({"subject_id": [2]})
    newest_df = pd.DataFrame({"subject_id": [3]})
    session_module.get_cache()["one.csv"] = oldest_df
    session_module.get_cache()["two.csv"] = middle_df
    session_module.get_cache()["three.csv"] = newest_df
    session_module._sessions.update(
        {
            "oldest": session_module.LoadedDataEntry(file_snapshot_path="one.csv", last_accessed=70.0),
            "middle": session_module.LoadedDataEntry(file_snapshot_path="two.csv", last_accessed=80.0),
            "newest": session_module.LoadedDataEntry(file_snapshot_path="three.csv", last_accessed=90.0),
        }
    )

    result = session_module._evict_stale_sessions()

    assert "oldest" not in session_module._sessions
    assert set(session_module._sessions) == {"middle", "newest"}
    assert result == session_module.SessionEvictionResult(evicted_sessions=1, evicted_dataframes=1)
    assert "one.csv" not in session_module.get_cache()
    pd.testing.assert_frame_equal(session_module.get_cache()["two.csv"], middle_df)
    pd.testing.assert_frame_equal(session_module.get_cache()["three.csv"], newest_df)


def test_evict_stale_sessions_keeps_cache_entries_used_by_active_sessions(monkeypatch):
    monkeypatch.setattr(session_module, "SESSION_MAX_AGE", 10)
    monkeypatch.setattr(session_module, "DATAFRAME_MAX_AGE", 1000)
    monkeypatch.setattr(session_module.time, "time", lambda: 100.0)
    shared_df = pd.DataFrame({"subject_id": [1]})
    session_module.get_cache()["shared.csv"] = shared_df
    session_module._sessions.update(
        {
            "stale": session_module.LoadedDataEntry(file_snapshot_path="shared.csv", last_accessed=89.0),
            "fresh": session_module.LoadedDataEntry(file_snapshot_path="shared.csv", last_accessed=95.0),
        }
    )

    result = session_module._evict_stale_sessions()

    assert "stale" not in session_module._sessions
    assert "fresh" in session_module._sessions
    assert result == session_module.SessionEvictionResult(evicted_sessions=1, evicted_dataframes=0)
    pd.testing.assert_frame_equal(session_module.get_cache()["shared.csv"], shared_df)


def test_evict_stale_sessions_removes_expired_dataframe_but_keeps_session(monkeypatch):
    monkeypatch.setattr(session_module, "SESSION_MAX_AGE", 1000)
    monkeypatch.setattr(session_module, "DATAFRAME_MAX_AGE", 10)
    monkeypatch.setattr(session_module.time, "time", lambda: 100.0)
    old_df = pd.DataFrame({"subject_id": [1]})
    session_module.get_cache()["old.csv"] = old_df
    session_module._sessions["old-dataframe"] = session_module.LoadedDataEntry(
        file_snapshot_path="old.csv",
        last_accessed=95.0,
        dataframe_last_accessed=89.0,
        dataframe_size_bytes=1234,
    )

    result = session_module._evict_stale_sessions()

    assert "old-dataframe" in session_module._sessions
    session = session_module._sessions["old-dataframe"]
    assert session.has_cached_dataframe is False
    assert session.dataframe_size_bytes == 0
    assert result == session_module.SessionEvictionResult(evicted_sessions=0, evicted_dataframes=1)
    assert "old.csv" not in session_module.get_cache()


def test_evict_stale_sessions_removes_expired_reload_context(monkeypatch):
    monkeypatch.setattr(session_module, "SESSION_MAX_AGE", 1000)
    monkeypatch.setattr(session_module, "DATAFRAME_MAX_AGE", 1000)
    monkeypatch.setattr(session_module, "DATASET_RELOAD_CONTEXT_MAX_AGE", 10)
    monkeypatch.setattr(session_module.time, "time", lambda: 100.0)
    session_module._get_dataset_reload_context_cache().update(
        {
            "stale-context": session_module.DatasetReloadContextEntry(
                load_body={"dataset": "Clinical Dataset", "filePath": "nested/adsl.csv"},
                created_at=89.0,
            ),
            "fresh-context": session_module.DatasetReloadContextEntry(
                load_body={"dataset": "Clinical Dataset", "filePath": "nested/adae.csv"},
                created_at=95.0,
            ),
        }
    )

    result = session_module._evict_stale_sessions()

    assert "stale-context" not in session_module._get_dataset_reload_context_cache()
    assert "fresh-context" in session_module._get_dataset_reload_context_cache()
    assert result == session_module.SessionEvictionResult(
        evicted_sessions=0,
        evicted_dataframes=0,
        evicted_reload_contexts=1,
    )


def test_evict_stale_sessions_keeps_reload_context_for_expired_session(monkeypatch):
    monkeypatch.setattr(session_module, "SESSION_MAX_AGE", 10)
    monkeypatch.setattr(session_module, "DATAFRAME_MAX_AGE", 1000)
    monkeypatch.setattr(session_module, "DATASET_RELOAD_CONTEXT_MAX_AGE", 1000)
    monkeypatch.setattr(session_module.time, "time", lambda: 100.0)
    session_module._sessions["expired-session"] = session_module.LoadedDataEntry(
        file_snapshot_path="old.csv",
        last_accessed=89.0,
    )
    session_module._get_dataset_reload_context_cache()["expired-session"] = session_module.DatasetReloadContextEntry(
        load_body={"dataset": "Clinical Dataset", "filePath": "nested/adsl.csv"},
        created_at=95.0,
    )

    result = session_module._evict_stale_sessions()

    assert "expired-session" not in session_module._sessions
    assert "expired-session" in session_module._get_dataset_reload_context_cache()
    assert result == session_module.SessionEvictionResult(
        evicted_sessions=1,
        evicted_dataframes=0,
        evicted_reload_contexts=0,
    )


def test_evict_stale_sessions_keeps_shared_dataframe_for_active_session(monkeypatch):
    monkeypatch.setattr(session_module, "SESSION_MAX_AGE", 1000)
    monkeypatch.setattr(session_module, "DATAFRAME_MAX_AGE", 10)
    monkeypatch.setattr(session_module.time, "time", lambda: 100.0)
    shared_df = pd.DataFrame({"subject_id": [1]})
    session_module.get_cache()["shared.csv"] = shared_df
    session_module._sessions.update(
        {
            "old-dataframe": session_module.LoadedDataEntry(
                file_snapshot_path="shared.csv",
                last_accessed=95.0,
                dataframe_last_accessed=89.0,
                dataframe_size_bytes=1234,
            ),
            "fresh-dataframe": session_module.LoadedDataEntry(
                file_snapshot_path="shared.csv",
                last_accessed=95.0,
                dataframe_last_accessed=95.0,
                dataframe_size_bytes=1234,
            ),
        }
    )

    result = session_module._evict_stale_sessions()

    assert session_module._sessions["old-dataframe"].has_cached_dataframe is False
    assert session_module._sessions["fresh-dataframe"].has_cached_dataframe is True
    assert result == session_module.SessionEvictionResult(evicted_sessions=0, evicted_dataframes=0)
    pd.testing.assert_frame_equal(session_module.get_cache()["shared.csv"], shared_df)
    session_module._current_user_id.set("old-dataframe")
    assert session_module.has_current_df("shared.csv") is False
    session_module._current_user_id.set("fresh-dataframe")
    assert session_module.has_current_df("shared.csv") is True


def test_set_current_df_evicts_previous_dataset_for_same_session():
    first_df = pd.DataFrame({"subject_id": [1]})
    second_df = pd.DataFrame({"subject_id": [2]})
    session_module._current_user_id.set("session-5")

    session_module._set_current_df(first_df, "first.csv")
    session_module._set_current_df(second_df, "second.csv")

    assert "first.csv" not in session_module.get_cache()
    pd.testing.assert_frame_equal(session_module.get_cache()["second.csv"], second_df)
    assert session_module._sessions["session-5"].file_snapshot_path == "second.csv"


def test_session_middleware_evicts_idle_session_before_touching_it(monkeypatch):
    monkeypatch.setattr(session_module, "SESSION_MAX_AGE", 10)
    monkeypatch.setattr(session_module, "DATAFRAME_MAX_AGE", 1000)
    monkeypatch.setattr(session_module.time, "time", lambda: 100.0)
    monkeypatch.setattr(session_module, "get_current_user", lambda: {"id": "session-6"})
    old_df = pd.DataFrame({"subject_id": [1]})
    session_module.get_cache()["old.csv"] = old_df
    session_module._sessions["session-6"] = session_module.LoadedDataEntry(
        file_snapshot_path="old.csv",
        last_accessed=89.0,
    )

    app = FastAPI()
    app.add_middleware(session_module.SessionMiddleware)

    @app.get("/session")
    async def read_session():
        return {"has_session": "session-6" in session_module._sessions}

    client = TestClient(app)

    response = client.get("/session")

    assert response.status_code == 200
    assert response.json() == {"has_session": False}
    assert "old.csv" not in session_module.get_cache()


def test_evict_stale_dataframes_endpoint_removes_idle_dataframe(_mcp_app, monkeypatch):
    monkeypatch.setattr(session_module, "SESSION_MAX_AGE", 1000)
    monkeypatch.setattr(session_module, "DATAFRAME_MAX_AGE", 10)
    monkeypatch.setattr(session_module.time, "time", lambda: 100.0)
    monkeypatch.setattr(session_module, "get_current_user", lambda: {"id": "session-7"})
    old_df = pd.DataFrame({"subject_id": [1]})
    session_module.get_cache()["old.csv"] = old_df
    session_module._sessions["session-7"] = session_module.LoadedDataEntry(
        file_snapshot_path="old.csv",
        last_accessed=95.0,
        dataframe_last_accessed=89.0,
    )

    client = TestClient(_mcp_app)

    response = client.post("/dataframes/evict-stale")

    assert response.status_code == 200
    assert response.json() == {
        "evicted_sessions": 0,
        "evicted_dataframes": 1,
        "evicted_reload_contexts": 0,
    }
    assert "session-7" in session_module._sessions
    assert session_module._sessions["session-7"].has_cached_dataframe is False
    assert "old.csv" not in session_module.get_cache()


def test_evict_stale_dataframes_endpoint_removes_idle_reload_context(_mcp_app, monkeypatch):
    monkeypatch.setattr(session_module, "SESSION_MAX_AGE", 1000)
    monkeypatch.setattr(session_module, "DATAFRAME_MAX_AGE", 1000)
    monkeypatch.setattr(session_module, "DATASET_RELOAD_CONTEXT_MAX_AGE", 10)
    monkeypatch.setattr(session_module.time, "time", lambda: 100.0)
    monkeypatch.setattr(session_module, "get_current_user", lambda: {"id": "session-context"})
    session_module._get_dataset_reload_context_cache()["session-context"] = session_module.DatasetReloadContextEntry(
        load_body={"dataset": "Clinical Dataset", "filePath": "nested/adsl.csv"},
        created_at=89.0,
    )

    client = TestClient(_mcp_app)

    response = client.post("/dataframes/evict-stale")

    assert response.status_code == 200
    assert response.json() == {
        "evicted_sessions": 0,
        "evicted_dataframes": 0,
        "evicted_reload_contexts": 1,
    }
    assert "session-context" not in session_module._get_dataset_reload_context_cache()


def test_dataset_load_stores_reload_context_in_current_session(_mcp_app, monkeypatch, tmp_path):
    monkeypatch.setattr(session_module, "DATASET_RELOAD_CONTEXT_MAX_AGE", 1000)
    monkeypatch.setattr(session_module.time, "time", lambda: 100.0)
    monkeypatch.setattr(session_module, "get_current_user", lambda: {"id": "session-context"})
    dataset = tmp_path / "adsl.csv"
    dataset.write_text("subject_id,arm\n1,A\n", encoding="utf-8")
    client = TestClient(_mcp_app)

    load_body = {
        "dataset": "Clinical Dataset",
        "datasetId": "ds-1",
        "snapshotId": "snap-1",
        "filePath": "nested/adsl.csv",
    }

    save_response = client.post("/dataset/load", params={"file_snapshot_path": str(dataset)}, json=load_body)
    current_session_response = client.get("/dataframe/current-session")

    assert save_response.status_code == 200
    assert save_response.json()["dataset"] == str(dataset)
    assert current_session_response.status_code == 200
    assert current_session_response.json()["reload_context"] == load_body


def test_dataframe_size_endpoint_returns_current_session_size(_mcp_app, monkeypatch):
    monkeypatch.setattr(session_module, "get_current_user", lambda: {"id": "session-size"})
    current_df = pd.DataFrame({"subject_id": [1]})
    other_df = pd.DataFrame({"subject_id": [2]})
    session_module.get_cache()["current.csv"] = current_df
    session_module.get_cache()["other.csv"] = other_df
    session_module._sessions["session-size"] = session_module.LoadedDataEntry(
        file_snapshot_path="current.csv",
        last_accessed=time.time(),
        dataframe_size_bytes=1234,
    )
    session_module._sessions["other-session"] = session_module.LoadedDataEntry(
        file_snapshot_path="other.csv",
        last_accessed=time.time(),
        dataframe_size_bytes=5678,
    )

    client = TestClient(_mcp_app)

    response = client.get("/dataframe/size")

    assert response.status_code == 200
    assert response.json() == {"dataframe_size_bytes": 1234}


def test_dataset_info_endpoint_returns_source_file_size(_mcp_app, monkeypatch):
    monkeypatch.setattr(session_module, "get_current_user", lambda: {"id": "session-size"})
    current_df = pd.DataFrame({"subject_id": [1]})
    session_module.get_cache()["current.csv"] = current_df
    session_module._sessions["session-size"] = session_module.LoadedDataEntry(
        file_snapshot_path="current.csv",
        last_accessed=time.time(),
        dataframe_size_bytes=1234,
        source_file_size_bytes=42,
    )

    client = TestClient(_mcp_app)

    response = client.get("/dataset/info")

    assert response.status_code == 200
    assert response.json()["source_file_size_bytes"] == 42


def test_get_current_source_file_size_uses_stored_session_value(monkeypatch):
    session_module._current_user_id.set("session-size")
    session_module._sessions["session-size"] = session_module.LoadedDataEntry(
        file_snapshot_path="/missing/temp/file.csv",
        last_accessed=time.time(),
        source_file_size_bytes=42,
    )
    monkeypatch.setattr(
        session_module,
        "_get_source_file_size_bytes",
        lambda path: (_ for _ in ()).throw(AssertionError("should not stat after load")),
    )

    assert session_module.get_current_source_file_size_bytes() == 42


def test_get_current_dataframe_size_logs_warning_when_session_is_missing(caplog):
    session_module._current_user_id.set("missing-session")

    with caplog.at_level("WARNING", logger=session_module.__name__):
        size = session_module.get_current_dataframe_size_bytes()

    assert size == 0
    assert "No loaded DataFrame found for user missing-session" in caplog.text


def test_evict_current_session_dataframe_endpoint_removes_only_current_session(_mcp_app, monkeypatch):
    monkeypatch.setattr(session_module, "get_current_user", lambda: {"id": "session-current"})
    current_df = pd.DataFrame({"subject_id": [1]})
    other_df = pd.DataFrame({"subject_id": [2]})
    session_module.get_cache()["current.csv"] = current_df
    session_module.get_cache()["other.csv"] = other_df
    session_module._sessions["session-current"] = session_module.LoadedDataEntry(
        file_snapshot_path="current.csv",
        last_accessed=time.time(),
        dataframe_size_bytes=1234,
    )
    session_module._sessions["session-other"] = session_module.LoadedDataEntry(
        file_snapshot_path="other.csv",
        last_accessed=time.time(),
        dataframe_size_bytes=5678,
    )

    session_module._current_user_id.set("session-current")
    client = TestClient(_mcp_app)

    response = client.post("/dataframe/evict-current-session")

    assert response.status_code == 200
    assert response.json() == {
        "evicted_sessions": 1,
        "evicted_dataframes": 1,
        "evicted_reload_contexts": 0,
    }
    assert "session-current" not in session_module._sessions
    assert "session-other" in session_module._sessions
    assert "current.csv" not in session_module.get_cache()
    pd.testing.assert_frame_equal(session_module.get_cache()["other.csv"], other_df)


def test_evict_current_session_dataframe_logs_warning_when_session_is_missing(caplog):
    session_module._current_user_id.set("missing-session")

    with caplog.at_level("WARNING", logger=session_module.__name__):
        result = session_module.evict_current_session_dataframe()

    assert result == session_module.SessionEvictionResult(evicted_sessions=0, evicted_dataframes=0)
    assert "No loaded DataFrame found for user missing-session" in caplog.text


def test_session_middleware_sets_current_user_id_and_touches_existing_session(monkeypatch):
    monkeypatch.setattr(session_module.time, "time", lambda: 123.0)
    monkeypatch.setattr(session_module, "get_current_user", lambda: {"id": "user-default"})
    session_module._current_user_id.set(None)
    session_module._sessions["user-default"] = session_module.LoadedDataEntry(
        file_snapshot_path="adlb.csv",
        last_accessed=1.0,
    )

    app = FastAPI()
    app.add_middleware(session_module.SessionMiddleware)

    @app.get("/session")
    async def read_session():
        return {"session_id": session_module._current_user_id.get()}

    client = TestClient(app)

    response = client.get("/session")

    assert response.status_code == 200
    assert response.json() == {"session_id": "user-default"}
    assert session_module._sessions["user-default"].last_accessed == 123.0


def test_session_middleware_uses_current_user_when_header_is_missing(monkeypatch):
    monkeypatch.setattr(session_module, "get_current_user", lambda: {"id": "user-default"})
    session_module._current_user_id.set(None)

    app = FastAPI()
    app.add_middleware(session_module.SessionMiddleware)

    @app.get("/session")
    async def read_session():
        return {"session_id": session_module._current_user_id.get()}

    client = TestClient(app)

    response = client.get("/session")

    assert response.status_code == 200
    assert response.json() == {"session_id": "user-default"}


def test_dataset_load_reports_when_dataframe_is_too_large_for_cache(_mcp_app, monkeypatch, tmp_path):
    monkeypatch.setattr(session_module, "get_current_user", lambda: {"id": "session-too-large"})
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


def test_get_current_session_dataframe_endpoint_reports_loaded_dataset(_mcp_app, monkeypatch):
    cached_df = pd.DataFrame({"subject_id": [1], "arm": ["A"]})
    monkeypatch.setattr(session_module, "get_current_user", lambda: {"id": "session-current-dataset"})
    session_module._current_user_id.set(None)
    session_module._sessions["session-current-dataset"] = session_module.LoadedDataEntry(
        file_snapshot_path="adsl.csv",
        last_accessed=time.time(),
    )
    dataframe_cache.get_cache()["adsl.csv"] = cached_df

    client = TestClient(_mcp_app)

    response = client.get("/dataframe/current-session")

    assert response.status_code == 200
    assert response.json() == {"dataset": "adsl.csv", "reload_context": None}


def test_load_current_df_reuses_matching_cached_dataframe(monkeypatch):
    cached_df = pd.DataFrame({"subject_id": [1], "arm": ["A"]})
    session_module._current_user_id.set("session-reuse")
    session_module._sessions["session-reuse"] = session_module.LoadedDataEntry(
        file_snapshot_path="adsl.csv",
        last_accessed=1.0,
    )
    dataframe_cache.get_cache()["adsl.csv"] = cached_df

    monkeypatch.setattr(
        session_module,
        "_create_dataframe_entry_in_thread",
        lambda file_snapshot_path: (_ for _ in ()).throw(AssertionError("should not reload")),
    )

    df = session_module.load_current_df("adsl.csv")

    pd.testing.assert_frame_equal(df, cached_df)


def test_load_current_df_creates_dataframe_in_loader_thread(monkeypatch):
    reloaded_df = pd.DataFrame({"subject_id": [1], "arm": ["A"]})
    session_module._current_user_id.set("session-4")
    caller_thread_name = threading.current_thread().name
    loader_thread_names = []

    def fake_load_dataset(file_snapshot_path):
        loader_thread_names.append(threading.current_thread().name)
        return reloaded_df

    monkeypatch.setattr(session_module, "load_dataset", fake_load_dataset)
    monkeypatch.setattr(session_module.objsize, "get_deep_size", lambda dataframe: 4321)

    df = session_module.load_current_df("adsl.csv")

    assert loader_thread_names
    assert loader_thread_names[0] != caller_thread_name
    assert loader_thread_names[0].startswith("mcp-dataframe-loader")
    pd.testing.assert_frame_equal(df, reloaded_df)
    assert session_module._sessions["session-4"].file_snapshot_path == "adsl.csv"
    assert session_module._sessions["session-4"].dataframe_size_bytes == 4321
    pd.testing.assert_frame_equal(dataframe_cache.get_cache()["adsl.csv"], reloaded_df)
