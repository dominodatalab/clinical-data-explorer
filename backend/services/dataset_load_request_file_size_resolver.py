"""Resolve source file sizes for dataset-load memory admission."""

import logging
import os
from typing import TYPE_CHECKING

import requests
from werkzeug.exceptions import NotFound

import backend.services.httpclient as httpclient
from backend.auth import (
    get_domino_api_host,
    get_domino_external_url,
    get_domino_webvfs_host,
    get_passthrough_token,
    get_passthrough_token_from_authorization_header,
)

if TYPE_CHECKING:
    from backend.services.dataset_load_request_queue import DatasetLoadRequest

logger = logging.getLogger(__name__)


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


def _webvfs_base_urls(external_url=None) -> list[str]:
    """Hosts that may serve `/webvfs/...`, most reliable first, without duplicates.

    The in-cluster WebVFS host works wherever the app runs. The external URL is derived
    from the request's host, which is only right when the app is reached on the main
    Domino domain: the `apps.` ingress answers `/webvfs` with an nginx 404. It stays as a
    fallback for platforms that don't set DOMINO_WEBVFS_HOST.
    """
    candidates = [get_domino_webvfs_host(), external_url or get_domino_external_url()]
    return list(dict.fromkeys(url for url in candidates if url))


def _is_wrong_host(exc: Exception) -> bool:
    """A 404 (route not served there) or a connection failure: try the next host."""
    if isinstance(exc, httpclient.HTTPClientError):
        return exc.status_code == 404
    return isinstance(exc, requests.exceptions.ConnectionError | requests.exceptions.Timeout)


def get_netapp_volume_file_metadata(volume_id: str, file_path: str, token=None, external_url=None):
    base_urls = _webvfs_base_urls(external_url)
    if not base_urls:
        raise RuntimeError('Domino external URL not configured')

    for index, base_url in enumerate(base_urls):
        try:
            return httpclient.get(
                f"{base_url}/webvfs/remotefs/v1/volumes/{volume_id}/files/metadata",
                params={'path': file_path},
                headers={
                    'accept': 'application/json',
                    'Authorization': f'Bearer {token}',
                },
            )
        except Exception as exc:
            if index == len(base_urls) - 1 or not _is_wrong_host(exc):
                raise
            logger.warning(
                "WebVFS metadata request to %s failed (%s); trying %s",
                base_url, type(exc).__name__, base_urls[index + 1],
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
