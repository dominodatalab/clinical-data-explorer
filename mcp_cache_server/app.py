import pickle

from fastapi import FastAPI, Query, Request, Response

from mcp_cache_server import store

app = FastAPI(
    title="Clinical Data Explorer MCP Cache Service",
    description="Singleton session and DataFrame cache for MCP workers",
    version="1.0.0",
)


def _dataframe_response(df):
    return Response(
        content=pickle.dumps(df, protocol=pickle.HIGHEST_PROTOCOL),
        media_type="application/octet-stream",
    )


@app.get("/")
async def read_root():
    return {"message": "MCP cache service is running"}


@app.post("/sessions/{session_id}/load")
def load_session_dataframe(
    session_id: str,
    file_snapshot_path: str = Query(..., description="Dataset file path or downloaded snapshot path to load"),
):
    df = store.load_df_for_session(session_id, file_snapshot_path)
    return _dataframe_response(df)


@app.put("/sessions/{session_id}/dataframe")
async def set_session_dataframe(session_id: str, request: Request):
    payload = pickle.loads(await request.body())
    store.set_session_dataframe(
        session_id,
        payload["dataframe"],
        payload["file_snapshot_path"],
        payload.get("metadata"),
    )
    return {"ok": True}


@app.get("/sessions/{session_id}/dataframe")
def get_session_dataframe(session_id: str):
    df = store.get_df_for_session(session_id)
    return _dataframe_response(df)


@app.get("/sessions/{session_id}/dataset_name")
def get_session_dataset_name(session_id: str):
    return {"dataset": store.get_session_dataset_name(session_id)}


@app.get("/sessions/{session_id}/metadata")
def get_session_metadata(session_id: str):
    return store.get_session_metadata(session_id)


@app.post("/sessions/{session_id}/touch")
def touch_session(session_id: str):
    store.touch_session(session_id)
    return {"ok": True}
