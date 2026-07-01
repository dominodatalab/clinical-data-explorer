"""Resolve source file sizes for dataset-load memory admission."""

import logging
import os
from typing import TYPE_CHECKING

from werkzeug.exceptions import NotFound

import backend.services.httpclient as httpclient
from backend import config
from backend.auth import (
    get_domino_api_host,
    get_passthrough_token,
    get_passthrough_token_from_authorization_header,
)

if TYPE_CHECKING:
    from backend.services.dataset_load_request_queue import DatasetLoadRequest

logger = logging.getLogger(__name__)


def resolve_dataset_load_request_file_size(load_request: "DatasetLoadRequest") -> int:
    token = get_passthrough_token_from_authorization_header(load_request.authorization_header)

    if load_request.source_type == 'netapp':
        file_path = _load_request_file_path(load_request)
        try:
            return _get_netapp_volume_file_size(
                file_path,
                load_request.volume_id,
                token=token,
            )
        except (httpclient.HTTPClientError, NotFound, RuntimeError) as exc:
            logger.warning(
                "Could not resolve NetApp file size for %s before load; allowing load to continue: %s",
                file_path,
                exc,
            )
            return 0

    if load_request.dataset_id and load_request.snapshot_id:
        return _get_dataset_snapshot_file_size(
            _load_request_file_path(load_request),
            load_request.snapshot_id,
            token=token,
        )

    if load_request.dataset_id:
        snapshot_id = _get_default_dataset_snapshot_id(load_request.dataset_id, token=token)
        return _get_dataset_snapshot_file_size(
            _load_request_file_path(load_request),
            snapshot_id,
            token=token,
        )

    if load_request.project_id:
        dataset_id = _resolve_project_dataset_id(load_request.dataset, load_request.project_id, token=token)
        snapshot_id = _get_default_dataset_snapshot_id(dataset_id, token=token)
        return _get_dataset_snapshot_file_size(
            _load_request_file_path(load_request),
            snapshot_id,
            token=token,
        )

    return os.path.getsize(f"./datasets/{load_request.dataset}")



def _split_dataset_file_path(dataset_display_name: str) -> str:
    parts = dataset_display_name.split('/', 1)
    if len(parts) != 2:
        raise RuntimeError(f'Invalid dataset reference: {dataset_display_name}')
    return parts[1]


def _load_request_file_path(load_request: "DatasetLoadRequest") -> str:
    if load_request.file_path:
        return load_request.file_path
    return _split_dataset_file_path(load_request.dataset)


def _get_netapp_volume_file_size(
    file_path: str,
    volume_id: str | None,
    token=None,
) -> int:
    if not volume_id:
        raise RuntimeError(f'Missing NetApp volume ID for {file_path}')

    metadata = get_netapp_volume_file_metadata(volume_id, file_path, token=token)
    file_size = metadata.get("fileSize")
    if file_size is None:
        raise NotFound(f'Missing fileSize in metadata for {file_path}')
    return file_size


def _get_remotefs_host():
    remotefs_host = config.get_domino_remote_file_system_hostport()
    if not remotefs_host:
        return None
    if not remotefs_host.startswith('http'):
        remotefs_host = f'http://{remotefs_host}'
    return remotefs_host.rstrip('/')


def get_netapp_volume_file_metadata(volume_id: str, file_path: str, token=None, remotefs_host=None):
    remotefs_host = (remotefs_host or _get_remotefs_host())
    if not remotefs_host:
        raise RuntimeError('RemoteFS host is not configured')

    return httpclient.get(
        f"{remotefs_host}/remotefs/v1/volumes/{volume_id}/files/metadata",
        params={'path': file_path},
        headers={
            'accept': 'application/json',
            'Authorization': f'Bearer {token}',
        },
    )


def _resolve_project_dataset_id(dataset_display_name: str, project_id: str, token=None) -> str:
    token = token or get_passthrough_token()
    api_host = get_domino_api_host()
    if not api_host:
        raise RuntimeError('Domino API host not configured')

    ds_name = dataset_display_name.split('/', 1)[0]
    response = httpclient.get(
        f'{api_host}/api/datasetrw/v2/datasets',
        params={
            'projectIdsToInclude': project_id,
            'limit': 100,
        },
        headers={'Authorization': f'Bearer {token}'},
    )

    for dataset_entry in response.get('datasets', []):
        dataset = dataset_entry['dataset']
        if dataset.get('name') == ds_name and dataset.get('projectId') == project_id:
            return dataset['id']

    shared_datasets_response = httpclient.get(
        f'{api_host}/api/projects/v1/projects/{project_id}/shared-datasets',
        headers={'Authorization': f'Bearer {token}'},
    )

    shared_dataset_ids = (shared_datasets_response.get('dataset') or {}).get('sharedDatasetIds') or []
    for dataset_id in shared_dataset_ids:
        dataset_response = httpclient.get(
            f'{api_host}/api/datasetrw/v1/datasets/{dataset_id}',
            headers={'Authorization': f'Bearer {token}'},
        )
        dataset = dataset_response['dataset']
        if dataset.get('name') == ds_name:
            return dataset['id']

    raise NotFound(f'Dataset "{ds_name}" not found in project')


def _get_default_dataset_snapshot_id(dataset_id: str, token=None) -> str:
    token = token or get_passthrough_token()
    api_host = get_domino_api_host()
    if not api_host:
        raise RuntimeError('Domino API host not configured')

    snapshots_list_response = httpclient.get(
        f'{api_host}/api/datasetrw/v1/datasets/{dataset_id}/snapshots',
        params={'limit': 1},
        headers={'Authorization': f'Bearer {token}'},
    )
    snapshots = snapshots_list_response.get("snapshots", [])
    if len(snapshots) == 0:
        raise NotFound(f'No snapshots found for dataset {dataset_id}')
    return snapshots[0]["id"]


def _get_dataset_snapshot_file_size(file_path: str, snapshot_id: str, token=None) -> int:
    metadata = get_dataset_snapshot_file_metadata(snapshot_id, file_path, token=token)
    file_size = metadata.get("fileSize")
    if file_size is None:
        raise NotFound(f'Missing fileSize in metadata for {file_path}')
    return file_size


def get_dataset_snapshot_file_metadata(snapshot_id: str, file_path: str, token=None, api_host=None):
    api_host = api_host or get_domino_api_host()
    token = token or get_passthrough_token()
    if not api_host:
        raise RuntimeError('Domino API host not configured')

    return httpclient.get(
        f"{api_host}/v4/datasetrw/snapshot/{snapshot_id}/file/meta",
        params={'path': file_path},
        headers={'Authorization': f'Bearer {token}'},
    )
