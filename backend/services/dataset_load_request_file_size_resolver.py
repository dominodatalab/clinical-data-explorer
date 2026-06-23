"""Resolve source file sizes for dataset-load memory admission."""

import os
from typing import TYPE_CHECKING

from werkzeug.exceptions import NotFound

import backend.services.httpclient as httpclient
from backend.auth import (
    get_domino_api_host,
    get_domino_external_url,
    get_passthrough_token,
    get_passthrough_token_from_authorization_header,
)

if TYPE_CHECKING:
    from backend.services.dataset_load_request_queue import DatasetLoadRequest


def resolve_dataset_load_request_file_size(load_request: "DatasetLoadRequest") -> int:
    token = get_passthrough_token_from_authorization_header(load_request.authorization_header)

    if load_request.source_type == 'netapp':
        return _get_netapp_volume_file_size(
            load_request.dataset,
            load_request.volume_id,
            token=token,
        )

    if load_request.dataset_id and load_request.snapshot_id:
        return _get_dataset_snapshot_file_size(
            load_request.dataset,
            load_request.snapshot_id,
            token=token,
        )

    if load_request.dataset_id:
        snapshot_id = _get_default_dataset_snapshot_id(load_request.dataset_id, token=token)
        return _get_dataset_snapshot_file_size(
            load_request.dataset,
            snapshot_id,
            token=token,
        )

    if load_request.project_id:
        dataset_id = _resolve_project_dataset_id(load_request.dataset, load_request.project_id, token=token)
        snapshot_id = _get_default_dataset_snapshot_id(dataset_id, token=token)
        return _get_dataset_snapshot_file_size(
            load_request.dataset,
            snapshot_id,
            token=token,
        )

    return os.path.getsize(f"./datasets/{load_request.dataset}")



def _split_dataset_file_path(dataset_display_name: str) -> str:
    parts = dataset_display_name.split('/', 1)
    if len(parts) != 2:
        raise RuntimeError(f'Invalid dataset reference: {dataset_display_name}')
    return parts[1]


def _get_netapp_volume_file_size(
    volume_file_display_name: str,
    volume_id: str | None,
    token=None,
) -> int:
    if not volume_id:
        raise RuntimeError(f'Missing NetApp volume ID for {volume_file_display_name}')

    file_path = _split_dataset_file_path(volume_file_display_name)
    metadata = get_netapp_volume_file_metadata(volume_id, file_path, token=token)
    file_size = metadata.get("fileSize")
    if file_size is None:
        raise NotFound(f'Missing fileSize in metadata for {file_path}')
    return file_size


def get_netapp_volume_file_metadata(volume_id: str, file_path: str, token=None, external_url=None):
    external_url = external_url or get_domino_external_url()
    if not external_url:
        raise RuntimeError('Domino external URL not configured')

    return httpclient.get(
        f"{external_url}/webvfs/remotefs/v1/volumes/{volume_id}/files/metadata",
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
        f'{api_host}/api/datasetrw/v2/datasets?projectIdsToInclude={project_id}&limit=100',
        headers={'Authorization': f'Bearer {token}'},
    )

    for dataset_entry in response.get('datasets', []):
        dataset = dataset_entry.get('dataset', dataset_entry)
        if dataset.get('name') == ds_name and dataset.get('projectId') == project_id:
            return dataset['id']

    shared_mounts = httpclient.get(
        f'{api_host}/v4/datasetrw/mounts-v2/{project_id}/shared',
        params={'minimumPermission': 'ListDatasetRwV2'},
        headers={'Authorization': f'Bearer {token}'},
    )

    for mount in shared_mounts:
        if mount.get('name') == ds_name and mount.get('datasetId'):
            return mount['datasetId']

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


def _get_dataset_snapshot_file_size(dataset_display_name: str, snapshot_id: str, token=None) -> int:
    file_path = _split_dataset_file_path(dataset_display_name)
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
