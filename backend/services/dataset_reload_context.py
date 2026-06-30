from dataclasses import dataclass
import time
from typing import Optional

import requests

from backend.session import mcp_get, mcp_post


@dataclass(frozen=True)
class DatasetReloadContext:
    dataset: str
    file_path: str
    project_id: Optional[str] = None
    dataset_id: Optional[str] = None
    snapshot_id: Optional[str] = None
    source_type: Optional[str] = None
    volume_key: Optional[str] = None
    volume_id: Optional[str] = None
    snapshot_version: Optional[str] = None
    updated_at: float = 0

    def to_load_body(self):
        body = {
            "dataset": self.dataset,
            "filePath": self.file_path,
        }
        optional_fields = {
            "projectId": self.project_id,
            "datasetId": self.dataset_id,
            "snapshotId": self.snapshot_id,
            "sourceType": self.source_type,
            "volumeKey": self.volume_key,
            "volumeId": self.volume_id,
            "snapshotVersion": self.snapshot_version,
        }
        body.update({key: value for key, value in optional_fields.items() if value is not None})
        return body


def context_from_load_body(load_body):
    dataset = load_body.get("dataset")
    file_path = load_body.get("filePath")
    if not dataset or not file_path:
        return None

    return DatasetReloadContext(
        dataset=dataset,
        file_path=file_path,
        project_id=load_body.get("projectId"),
        dataset_id=load_body.get("datasetId"),
        snapshot_id=load_body.get("snapshotId"),
        source_type=load_body.get("sourceType"),
        volume_key=load_body.get("volumeKey"),
        volume_id=load_body.get("volumeId"),
        snapshot_version=load_body.get("snapshotVersion"),
        updated_at=time.time(),
    )


def save_reload_context(session_id, load_body):
    context = context_from_load_body(load_body)
    if context is None:
        return None

    mcp_post("/dataset/reload-context", session_id=session_id, json=context.to_load_body())
    return context


def get_reload_context(session_id):
    try:
        response = mcp_get("/dataset/reload-context", session_id=session_id)
    except requests.exceptions.RequestException:
        return None

    if response.status_code != 200:
        return None

    load_body = response.json().get("load_body")
    if not load_body:
        return None
    return context_from_load_body(load_body)
