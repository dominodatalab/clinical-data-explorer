from dataclasses import dataclass
import threading
import time
from typing import Optional


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


_contexts: dict[str, DatasetReloadContext] = {}
_lock = threading.Lock()


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

    with _lock:
        _contexts[session_id] = context
    return context


def get_reload_context(session_id):
    with _lock:
        return _contexts.get(session_id)


def clear_reload_context(session_id):
    with _lock:
        _contexts.pop(session_id, None)


def clear_reload_contexts():
    with _lock:
        _contexts.clear()
