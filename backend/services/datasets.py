"""Dataset discovery, snapshot listing, and dataset-load helpers.

Extracted from `backend/app.py` (REFACTOR_PLAN.md §1, step 1.3). These
helpers are called by `/datasets`, `/dataset/load`, `/snapshots/*`,
`/snapshot/*`, and `/netapp-volume/*` route handlers — those routes will
move into `backend/routes/datasets.py` in step 1.5d.

Several functions in this module return Flask `Response` objects (built
with `jsonify`). That mirrors the pre-refactor shape exactly — the
refactor's hard rule is zero behavior change, so we preserve the existing
return type even though it couples these helpers to a Flask request
context. The route layer will continue to call them as `return helper(...)`.

`domino_data` SDK imports are deferred to function bodies (matches the
pre-refactor pattern). They're slow to import and only needed when the app
is actually running inside a Domino environment.
"""
from contextlib import contextmanager
from dataclasses import dataclass
import io
import logging
import os
import tempfile
import traceback
from pathlib import Path

import requests
from flask import jsonify
from werkzeug.exceptions import (
    BadRequest,
    Forbidden,
    HTTPException,
    InternalServerError,
    NotFound,
    Unauthorized,
)
from werkzeug.wrappers import Response

from backend import config
from backend.auth import (
    get_domino_api_host,
    get_passthrough_token,
    get_passthrough_token_from_authorization_header,
)
from backend.services.dataset_load_request_queue import DatasetLoadRequest
from backend.services.download_file_metadata_cache import get_file_cache
import backend.services.httpclient as httpclient
from backend.session import get_session_id, mcp_post
from backend.types import SourceType
from chat_agent import clear_history

logger = logging.getLogger(__name__)

# Supported file extensions for data files.
# Note: this duplicates the constant in mcp_server/services/data_loading.py.
# Keep the two in sync (pre-existing tech debt — not deduplicated here).
SUPPORTED_EXTENSIONS = {'.csv', '.parquet', '.pq', '.sas7bdat', '.xpt', '.json', '.ndjson', '.dsjc'}


@dataclass(frozen=True)
class DatasetLoadTarget:
    file_snapshot_path: str
    dataset_id: str | None = None
    snapshot_id: str | None = None
    source_type: SourceType | None = None
    volume_key: str | None = None
    volume_id: str | None = None
    snapshot_version: int | str | None = None
    file_path: str | None = None


class ProjectDatasetEntriesError(Exception):
    def __init__(self, payload, status_code):
        super().__init__(payload.get('error', 'Failed to list datasets'))
        self.payload = payload
        self.status_code = status_code

    def to_response(self):
        return jsonify(self.payload), self.status_code


def find_data_files_fallback():
    """
    Fallback function to find data files when MCP server is unavailable.
    Searches only the repo datasets/ folder used for bundled local data.
    """
    data_files = []
    datasets_folder = Path('datasets')

    if datasets_folder.exists():
        for ext in SUPPORTED_EXTENSIONS:
            for f in datasets_folder.glob(f"*{ext}"):
                data_files.append(f.name)

    return data_files


def _get_remotefs_host():
    remotefs_host = config.get_domino_remote_file_system_hostport()
    if not remotefs_host:
        return None
    if not remotefs_host.startswith('http'):
        remotefs_host = f'http://{remotefs_host}'
    return remotefs_host


def _fetch_remotefs_volumes(token, params):
    remotefs_host = _get_remotefs_host()
    if not remotefs_host:
        logger.debug("DOMINO_REMOTE_FILE_SYSTEM_HOSTPORT not set, skipping NetApp volume discovery")
        return []

    response = requests.get(
        f'{remotefs_host}/remotefs/v1/volumes',
        params=params,
        headers={'Authorization': f'Bearer {token}'},
        timeout=30,
    )

    if response.status_code != 200:
        logger.warning(f"NetApp volumes API returned {response.status_code}: {response.text[:200]}")
        _raise_remotefs_http_exception(response, "NetApp volumes")

    volumes_data = response.json()
    return volumes_data if isinstance(volumes_data, list) else volumes_data.get('data', volumes_data.get('volumes', []))


def _fetch_remotefs_volume(volume_id, token):
    remotefs_host = _get_remotefs_host()
    if not remotefs_host:
        raise InternalServerError("RemoteFS host is not configured")

    response = requests.get(
        f'{remotefs_host}/remotefs/v1/volumes/{volume_id}',
        headers={'Authorization': f'Bearer {token}'},
        timeout=30,
    )

    if response.status_code != 200:
        logger.warning(f"NetApp volume API returned {response.status_code}: {response.text[:200]}")
        _raise_remotefs_http_exception(response, f'NetApp volume "{volume_id}"')

    return response.json()


def _fetch_remotefs_snapshot(snapshot_id, token):
    remotefs_host = _get_remotefs_host()
    if not remotefs_host:
        raise InternalServerError("RemoteFS host is not configured")

    response = requests.get(
        f'{remotefs_host}/remotefs/v1/snapshots/{snapshot_id}',
        headers={'Authorization': f'Bearer {token}'},
        timeout=30,
    )

    if response.status_code != 200:
        logger.warning(f"NetApp snapshot API returned {response.status_code}: {response.text[:200]}")
        _raise_remotefs_http_exception(response, f'NetApp snapshot "{snapshot_id}"')

    return response.json()


def _raise_remotefs_http_exception(response, resource_name):
    if response.status_code == 401:
        raise Unauthorized(f'Authentication failed while accessing {resource_name}')
    if response.status_code == 403:
        raise Forbidden(f'Access denied while accessing {resource_name}')
    if response.status_code == 404:
        raise NotFound(f'{resource_name} not found or not accessible')
    exc = HTTPException(
        description=f'RemoteFS returned HTTP {response.status_code} while accessing {resource_name}',
        response=Response(status=response.status_code),
    )
    exc.code = response.status_code
    raise exc


def _netapp_volume_metadata(vol):
    if not isinstance(vol, dict):
        return None

    vol_name = vol.get('name', '')
    vol_id = vol.get('id', '')
    vol_unique_name = vol.get('uniqueName', vol.get('unique_name', f'netapp-volume-{vol_name}-{vol_id}'))

    return {
        'id': vol_id,
        'name': vol_name,
        'unique_name': vol_unique_name,
    }


def _first_non_empty(*values):
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _dataset_owner_name(ds):
    project_info = ds.get('projectInfo') or {}
    return _first_non_empty(ds.get('owner_name'), project_info.get('projectOwnerUsername'))


def _dataset_info_entry(ds):
    info = {'id': ds['id'], 'name': ds['name']}
    owner_name = _dataset_owner_name(ds)
    if owner_name:
        info['owner_name'] = owner_name
    return info


def _volume_matches_identifier(vol, volume_id):
    meta = _netapp_volume_metadata(vol)
    return bool(meta and meta['id'] == volume_id)


def _netapp_snapshot_version(token, snapshot_id):
    if not snapshot_id or snapshot_id == 'latest':
        return None

    snapshot = _fetch_remotefs_snapshot(snapshot_id, token)
    if not snapshot:
        return None

    return snapshot.get('version')


def _list_netapp_files(vol_client, volume_key, token, snapshot_id=None):
    snapshot_version = _netapp_snapshot_version(token, snapshot_id)
    if snapshot_version is None:
        return vol_client.list_files(volume_key) or []

    from domino_data.data_sources import NetAppVolumeConfig
    volume = vol_client.get_volume(volume_key)
    volume.update(NetAppVolumeConfig(snapshot_version=str(snapshot_version)))
    file_objects = volume.list_files() or []
    return [f.key if hasattr(f, 'key') else str(f) for f in file_objects]


def _discover_netapp_files_from_volumes(volumes, token, snapshot_id=None):
    if not volumes:
        return [], []

    from domino_data.netapp_volumes import NetAppVolumeClient
    vol_client = NetAppVolumeClient(token=token)

    netapp_files = []
    netapp_volumes = []
    seen_volumes = set()

    for vol in volumes:
        volume_meta = _netapp_volume_metadata(vol)
        if not volume_meta or volume_meta['unique_name'] in seen_volumes:
            continue

        seen_volumes.add(volume_meta['unique_name'])
        netapp_volumes.append(volume_meta)

        try:
            files = _list_netapp_files(vol_client, volume_meta['unique_name'], token, snapshot_id)
            for fname in files:
                ext = os.path.splitext(fname)[1].lower()
                if ext in SUPPORTED_EXTENSIONS:
                    netapp_files.append({
                        'display_name': f"{volume_meta['name']}/{fname}",
                        'volume_key': volume_meta['unique_name'],
                        'volume_name': volume_meta['name'],
                        'volume_id': volume_meta['id'],
                    })
        except Exception as e:
            logger.warning(f"Failed to list files for NetApp volume {volume_meta['id']}: {e}")

    return netapp_files, netapp_volumes


def discover_netapp_files_for_project(project_id, token):
    """Discover NetApp volumes (and their r/w-head files) for a project.
    Queries the RemoteFS microservice for volumes attached to the project,
    then lists supported files in each volume using the domino_data SDK.
    Returns (netapp_files, netapp_volumes):
      - netapp_files: list of {display_name, volume_key, volume_name, volume_id}
      - netapp_volumes: list of {id, name, unique_name} for every volume,
        even ones whose r/w head currently has no supported files. The
        netapp deeplink flow needs the volume registry to resolve a
        netAppVolumeId in the URL when the target file lives only in a
        non-current snapshot.
    """
    volumes = _fetch_remotefs_volumes(
        token,
        {'status': 'Active', 'project_id': project_id},
    )
    return _discover_netapp_files_from_volumes(volumes, token)


def discover_netapp_files_for_volume(volume_id, token, snapshot_id=None):
    """Discover one accessible NetApp volume and its r/w-head supported files."""
    volume = _fetch_remotefs_volume(volume_id, token)
    if not _volume_matches_identifier(volume, volume_id):
        raise NotFound(f'NetApp volume "{volume_id}" not found or not accessible')

    return _discover_netapp_files_from_volumes([volume], token, snapshot_id)


def _dataset_entry(entry):
    dataset = dict(entry['dataset'])
    project_info = entry.get('projectInfo') or {}
    if project_info:
        dataset['projectInfo'] = project_info
        owner_name = project_info.get('projectOwnerUsername')
        if owner_name:
            dataset['owner_name'] = owner_name
    return dataset


def _dataset_client_key(ds):
    return f"dataset-{ds['name']}-{ds['id']}"


def _fetch_dataset_details(api_host, dataset_id, headers):
    response = requests.get(
        f'{api_host}/api/datasetrw/v1/datasets/{dataset_id}',
        headers=headers,
        timeout=30,
    )

    if response.status_code != 200:
        logger.warning(
            "Dataset details API returned %s for %s: %s",
            response.status_code,
            dataset_id,
            response.text[:200],
        )
        return None

    return response.json().get('dataset')


def _fetch_project_owner_username(api_host, project_id, headers, project_owner_cache):
    if not project_id:
        return None
    if project_id in project_owner_cache:
        return project_owner_cache[project_id]

    response = requests.get(
        f'{api_host}/api/projects/v1/projects/{project_id}',
        headers=headers,
        timeout=30,
    )

    if response.status_code != 200:
        logger.warning(
            "Project details API returned %s for %s: %s",
            response.status_code,
            project_id,
            response.text[:200],
        )
        project_owner_cache[project_id] = None
        return None

    owner_name = (response.json().get('project') or {}).get('ownerUsername')
    project_owner_cache[project_id] = owner_name
    return owner_name


def _fetch_project_shared_datasets(api_host, project_id, headers):
    response = requests.get(
        f'{api_host}/api/projects/v1/projects/{project_id}/shared-datasets',
        headers=headers,
        timeout=30,
    )

    if response.status_code != 200:
        logger.warning(
            "Shared datasets API returned %s: %s",
            response.status_code,
            response.text[:200],
        )
        return []

    shared_dataset_ids = (response.json().get('dataset') or {}).get('sharedDatasetIds') or []
    shared_datasets = []
    project_owner_cache = {}
    for shared_dataset_id in shared_dataset_ids:
        dataset = _fetch_dataset_details(api_host, shared_dataset_id, headers)
        if not dataset:
            continue
        dataset['shared'] = True
        owner_name = _fetch_project_owner_username(
            api_host,
            dataset.get('projectId'),
            headers,
            project_owner_cache,
        )
        if owner_name:
            dataset['owner_name'] = owner_name
        shared_datasets.append(dataset)

    return shared_datasets


def _project_dataset_entries(api_host, project_id, headers, purpose='list'):
    response = requests.get(
        f'{api_host}/api/datasetrw/v2/datasets',
        params={
            'projectIdsToInclude': project_id,
            'includeProjectInfo': True,
            'limit': 100,
        },
        headers=headers,
        timeout=30
    )

    if response.status_code == 401 or response.status_code == 403:
        if purpose == 'load':
            raise ProjectDatasetEntriesError(
                {'error': 'Access denied. Your session may have expired. Please refresh the page.'},
                response.status_code,
            )
        raise ProjectDatasetEntriesError(
            {
                'error': 'Access denied. You may not have permission to access this project\'s datasets.',
                'auth_error': True,
                'datasets': [],
            },
            response.status_code,
        )

    if response.status_code != 200:
        logger.error(f"Datasets API error: {response.status_code} - {response.text}")
        if purpose == 'load':
            raise ProjectDatasetEntriesError({'error': 'Failed to resolve dataset'}, 500)
        raise ProjectDatasetEntriesError(
            {'error': f'Failed to list datasets (HTTP {response.status_code})', 'datasets': []},
            500,
        )

    all_datasets = response.json().get('datasets', [])
    project_datasets = [
        _dataset_entry(d) for d in all_datasets
        if _dataset_entry(d).get('projectId') == project_id
    ]

    seen_ids = {ds.get('id') for ds in project_datasets}
    for shared_ds in _fetch_project_shared_datasets(api_host, project_id, headers):
        if shared_ds.get('id') not in seen_ids:
            project_datasets.append(shared_ds)
            seen_ids.add(shared_ds.get('id'))

    return project_datasets


def list_datasets_via_api(project_id):
    """List datasets and their files for a target project using Domino API with passthrough auth."""
    token = get_passthrough_token()
    if not token:
        return jsonify({
            'error': 'Authentication required. Please ensure you are accessing this app through Domino.',
            'auth_error': True,
            'datasets': []
        }), 401

    api_host = get_domino_api_host()
    if not api_host:
        return jsonify({'error': 'Domino API host not configured', 'datasets': []}), 500

    try:
        headers = {'Authorization': f'Bearer {token}'}

        project_datasets = _project_dataset_entries(api_host, project_id, headers)

        # List files from datasets
        file_list = []
        if project_datasets:
            from domino_data.datasets import DatasetClient
            client = DatasetClient(token=token)

            for ds in project_datasets:
                ds_id = ds['id']
                ds_name = ds['name']
                dataset_key = _dataset_client_key(ds)

                try:
                    dataset = client.get_dataset(dataset_key)
                    files = dataset.list_files()
                    for f in files:
                        ext = os.path.splitext(f.name)[1].lower()
                        if ext in SUPPORTED_EXTENSIONS:
                            file_list.append(f'{ds_name}/{f.name}')
                except Exception as e:
                    logger.warning(f'Failed to list files for dataset {ds_id}: {e}')

        # Build dataset_info for the frontend (needed for snapshot browsing)
        dataset_info = [_dataset_info_entry(ds) for ds in project_datasets]

    except ProjectDatasetEntriesError as e:
        return e.to_response()
    except requests.exceptions.ConnectionError:
        logger.error("Could not connect to Domino API for dataset listing")
        return jsonify({'error': 'Could not connect to Domino API', 'datasets': []}), 503
    except Exception as e:
        logger.error(f"Error listing datasets via API: {e}")
        logger.error(traceback.format_exc())
        return jsonify({'error': f'Error listing datasets: {str(e)}', 'datasets': []}), 500

    # Also discover NetApp volume files (and the volume registry) for this
    # project. NetApp discovery errors should propagate so the app's exception
    # middleware can return the standardized error shape.
    netapp_files, netapp_volumes = discover_netapp_files_for_project(project_id, token)

    return jsonify({
        'datasets': file_list,
        'dataset_info': dataset_info,
        'netapp_files': netapp_files,
        'netapp_volumes': netapp_volumes,
        'current_dataset': None,
        'extension_mode': True,
        'project_id': project_id
    })


def list_netapp_volume_files_by_id(volume_id, snapshot_id=None):
    """List supported files for a NetApp volume opened from the global volume view."""
    token = get_passthrough_token()
    if not token:
        raise Unauthorized("Authentication required. Please ensure you are accessing this app through Domino.")

    netapp_files, netapp_volumes = discover_netapp_files_for_volume(volume_id, token, snapshot_id)
    if not netapp_volumes:
        raise NotFound(f'NetApp volume with ID "{volume_id}" not found or not accessible')

    return jsonify({
        'datasets': [],
        'dataset_info': [],
        'netapp_files': netapp_files,
        'netapp_volumes': netapp_volumes,
        'current_dataset': None,
        'extension_mode': True,
        'netapp_volume_id': volume_id,
        'netapp_volume_snapshot_id': snapshot_id,
    })


def list_dataset_files_by_id(dataset_id, snapshot_id=None):
    """List files in a specific dataset by dataset ID using Domino API with passthrough auth.
    Used when the app is opened via 'Open with...' on a specific file (datasetFileContext mode).
    """
    token = get_passthrough_token()
    if not token:
        return jsonify({
            'error': 'Authentication required. Please ensure you are accessing this app through Domino.',
            'auth_error': True,
            'datasets': []
        }), 401

    api_host = get_domino_api_host()
    if not api_host:
        return jsonify({'error': 'Domino API host not configured', 'datasets': []}), 500

    try:
        headers = {'Authorization': f'Bearer {token}'}

        response = requests.get(
            f'{api_host}/api/datasetrw/v1/datasets/{dataset_id}',
            headers=headers,
            timeout=30
        )

        if response.status_code == 401 or response.status_code == 403:
            return jsonify({
                'error': 'Access denied. You may not have permission to access this dataset.',
                'auth_error': True,
                'datasets': []
            }), response.status_code

        if response.status_code == 404:
            return jsonify({'error': f'Dataset with ID "{dataset_id}" not found or not accessible', 'datasets': []}), 404

        if response.status_code != 200:
            logger.error(f"Dataset API error: {response.status_code} - {response.text}")
            return jsonify({'error': f'Failed to get dataset (HTTP {response.status_code})', 'datasets': []}), 500

        target_ds = response.json().get('dataset')
        if not target_ds:
            logger.error(f"Dataset API returned an unexpected payload for dataset {dataset_id}: {response.text}")
            return jsonify({'error': 'Dataset API returned an unexpected response', 'datasets': []}), 500

        ds_name = target_ds['name']
        ds_id = target_ds['id']

        # List files using domino_data
        from domino_data.datasets import DatasetClient
        dataset_key = f'dataset-{ds_name}-{ds_id}'
        client = DatasetClient(token=token)
        dataset = client.get_dataset(dataset_key)
        files = dataset.list_files()

        file_list = []
        for f in files:
            ext = os.path.splitext(f.name)[1].lower()
            if ext in SUPPORTED_EXTENSIONS:
                file_list.append(f'{ds_name}/{f.name}')

        return jsonify({
            'datasets': file_list,
            'dataset_info': [{'id': ds_id, 'name': ds_name}],
            'current_dataset': None,
            'extension_mode': True,
            'dataset_id': ds_id
        })

    except requests.exceptions.ConnectionError:
        logger.error("Could not connect to Domino API for dataset file listing")
        return jsonify({'error': 'Could not connect to Domino API', 'datasets': []}), 503
    except Exception as e:
        logger.error(f"Error listing dataset files by ID: {e}")
        logger.error(traceback.format_exc())
        return jsonify({'error': f'Error listing dataset files: {str(e)}', 'datasets': []}), 500


def _get_active_dataset_snapshot_id(api_host, dataset_id, token):
    """Return the snapshot id of the dataset's current (read-write head) snapshot,
    or None if it can't be resolved.

    Needed so the governance check can filter by the exact snapshot we're reading
    from. Without a snapshot id, governance queries match any bundle containing
    the same filename under the same dataset, regardless of which snapshot it was
    attached from.
    """
    if not api_host or not dataset_id or not token:
        return None
    try:
        headers = {'Authorization': f'Bearer {token}'}
        response = requests.get(
            f'{api_host}/v4/datasetrw/snapshots/{dataset_id}',
            headers=headers,
            timeout=30
        )
        if response.status_code != 200:
            logger.debug(f"Could not list snapshots for dataset {dataset_id}: HTTP {response.status_code}")
            return None
        raw = response.json()
        if not isinstance(raw, list):
            raw = raw.get('data', raw.get('snapshots', []))
        # The read-write snapshot is the live head; prefer it.
        for s in raw:
            if s.get('isReadWrite'):
                return s.get('id')
        # Fall back to the highest-version Active snapshot.
        actives = [s for s in raw if s.get('lifecycleStatus') in ('Active', 'active', None, '')]
        if actives:
            actives.sort(key=lambda s: s.get('version', 0), reverse=True)
            return actives[0].get('id')
    except Exception as e:
        logger.debug(f"Error resolving active snapshot for dataset {dataset_id}: {e}")
    return None


def _download_dataset_file(dataset, file_name, token):
    """Download a file from a dataset, working around a SDK bug where nested paths
    (containing slashes) in the signed URL cause 404 errors."""
    import urllib.parse
    import httpx

    url = dataset.get_file_url(file_name)

    # The SDK generates URLs like .../keys/sub_folder/sub_sub_folder/file.csv
    # where the slashes in the object key are unencoded, causing the server to 404.
    # Fix by URL-encoding the key portion after /keys/.
    if '/' in file_name and '/keys/' in url:
        encoded_name = urllib.parse.quote(file_name, safe='')
        url = url.replace('/keys/' + file_name, '/keys/' + encoded_name)

    headers = {'Authorization': f'Bearer {token}'}
    with httpx.Client() as http_client:
        response = http_client.get(url, headers=headers)
        response.raise_for_status()
        return response.content


def load_local_dataset_file(dataset_display_name, session_id=None):
    """Load a filesystem-backed dataset into the MCP server."""
    session_id = session_id or get_session_id()

    try:
        response = mcp_post(
            "/dataset/load",
            params={'file_snapshot_path': dataset_display_name},
            session_id=session_id,
        )
        if response.status_code == 200:
            clear_history(session_id=session_id)
            return jsonify(response.json())
        return jsonify({'error': response.json().get('detail', 'Failed to load dataset')}), response.status_code
    except Exception as e:
        logger.error(f"Error loading dataset: {e}")
        return jsonify({'error': 'Could not connect to MCP server'}), 500


def _split_display_name_path(dataset_display_name: str) -> tuple[str, str]:
    parts = dataset_display_name.split('/', 1)
    if len(parts) != 2:
        raise ValueError(f'Invalid dataset reference: {dataset_display_name}')
    return parts[0], parts[1]


def _download_cache_path(source_type: SourceType, dataset_id: str, snapshot_id: str | int | None, file_path: str) -> str:
    snapshot_key = "unset_snapshot_id" if snapshot_id in (None, '') else str(snapshot_id)
    return str(get_file_cache().create_file_path(str(dataset_id), str(file_path), source_type, snapshot_key))


def _create_download_cache_file(source_type: SourceType, dataset_id: str, snapshot_id: str | int | None, file_path: str) -> Path:
    snapshot_key = "unset_snapshot_id" if snapshot_id in (None, '') else str(snapshot_id)
    temp_path = get_file_cache().set(str(source_type), str(dataset_id), snapshot_key, str(file_path))
    if temp_path.exists():
        temp_path.write_bytes(b"")
    return temp_path


def _resolve_netapp_snapshot_version(volume_key: str, snapshot_id: str | None, snapshot_version, token=None):
    if snapshot_version not in (None, ''):
        return snapshot_version
    if not snapshot_id or snapshot_id == 'latest':
        return None

    from domino_data.netapp_volumes import NetAppVolumeClient
    vol_client = NetAppVolumeClient(token=token)
    snapshots = vol_client.list_snapshots(volume_unique_name=volume_key) or []
    for snap in snapshots:
        if getattr(snap, 'id', None) == snapshot_id:
            return getattr(snap, 'version', None)
    return None


def resolve_dataset_load_target(load_request: DatasetLoadRequest, token=None) -> DatasetLoadTarget:
    """Resolve a logical load request to the concrete MCP file path it would load."""
    token = token or get_passthrough_token_from_authorization_header(load_request.authorization_header)

    if load_request.source_type == 'netapp' and load_request.volume_key:
        _, file_path = _split_display_name_path(load_request.dataset)
        snapshot_version = _resolve_netapp_snapshot_version(
            load_request.volume_key,
            load_request.snapshot_id,
            load_request.snapshot_version,
            token=token,
        )
        return DatasetLoadTarget(
            file_snapshot_path=_download_cache_path('netapp', load_request.volume_key, snapshot_version, file_path),
            source_type='netapp',
            volume_key=load_request.volume_key,
            volume_id=load_request.volume_id,
            snapshot_id=load_request.snapshot_id,
            snapshot_version=snapshot_version,
            file_path=file_path,
        )

    if load_request.dataset_id:
        _, file_path = _split_display_name_path(load_request.dataset)
        snapshot_id = load_request.snapshot_id
        if not snapshot_id:
            from backend.services.dataset_load_request_file_size_resolver import _get_default_dataset_snapshot_id
            snapshot_id = _get_default_dataset_snapshot_id(load_request.dataset_id, token=token)
        return DatasetLoadTarget(
            file_snapshot_path=_download_cache_path('dataset', load_request.dataset_id, snapshot_id, file_path),
            dataset_id=load_request.dataset_id,
            snapshot_id=snapshot_id,
            source_type='dataset',
            file_path=file_path,
        )

    if load_request.project_id:
        _, file_path = _split_display_name_path(load_request.dataset)
        from backend.services.dataset_load_request_file_size_resolver import (
            _get_default_dataset_snapshot_id,
            _resolve_project_dataset_id,
        )
        dataset_id = _resolve_project_dataset_id(load_request.dataset, load_request.project_id, token=token)
        snapshot_id = _get_default_dataset_snapshot_id(dataset_id, token=token)
        return DatasetLoadTarget(
            file_snapshot_path=_download_cache_path('dataset', dataset_id, snapshot_id, file_path),
            dataset_id=dataset_id,
            snapshot_id=snapshot_id,
            source_type='dataset',
            file_path=file_path,
        )

    return DatasetLoadTarget(file_snapshot_path=load_request.dataset)


def load_existing_session_dataframe(load_request: DatasetLoadRequest, target: DatasetLoadTarget):
    """Return load metadata for a matching already-loaded MCP dataframe."""
    mcp_response = mcp_post(
        "/dataset/load",
        params={'file_snapshot_path': target.file_snapshot_path},
        session_id=load_request.session_id,
    )

    if mcp_response.status_code != 200:
        error_detail = mcp_response.json().get('detail', 'Failed to load dataset')
        return jsonify({'error': error_detail}), mcp_response.status_code

    result = mcp_response.json()
    result['dataset'] = load_request.dataset
    if target.source_type:
        result['sourceType'] = target.source_type
    if target.dataset_id:
        result['datasetId'] = target.dataset_id
    if target.snapshot_id and target.snapshot_id != 'latest':
        result['snapshotId'] = target.snapshot_id
    if target.volume_id:
        result['volumeId'] = target.volume_id
    if target.snapshot_version not in (None, ''):
        result['snapshotVersion'] = target.snapshot_version
    if target.file_path:
        result['governanceFilename'] = target.file_path.split('/')[-1]
    return jsonify(result)


def _dataset_file_metadata_response(load_request: DatasetLoadRequest, target: DatasetLoadTarget) -> dict:
    result = {
        'dataset': load_request.dataset,
        'file_snapshot_path': target.file_snapshot_path,
    }
    if target.source_type:
        result['sourceType'] = target.source_type
    if target.dataset_id:
        result['datasetId'] = target.dataset_id
    if target.snapshot_id and target.snapshot_id != 'latest':
        result['snapshotId'] = target.snapshot_id
    if target.volume_id:
        result['volumeId'] = target.volume_id
    if target.snapshot_version not in (None, ''):
        result['snapshotVersion'] = target.snapshot_version
    if target.file_path:
        result['governanceFilename'] = target.file_path.split('/')[-1]
    return result


def load_dataset_via_api(dataset_display_name, project_id, token=None, session_id=None):
    """Download a file from a Domino dataset via API and load it into the MCP server."""
    token = token or get_passthrough_token()
    session_id = session_id or get_session_id()
    if not token:
        return jsonify({'error': 'Authentication required. Please ensure you are accessing this app through Domino.'}), 401

    api_host = get_domino_api_host()
    if not api_host:
        return jsonify({'error': 'Domino API host not configured'}), 500

    # Parse "dataset_name/file_name" format
    parts = dataset_display_name.split('/', 1)
    if len(parts) != 2:
        return jsonify({'error': f'Invalid dataset reference: {dataset_display_name}'}), 400

    ds_name, file_name = parts

    try:
        headers = {'Authorization': f'Bearer {token}'}

        project_datasets = _project_dataset_entries(api_host, project_id, headers, purpose='load')

        target_ds = None
        for ds in project_datasets:
            if ds.get('name') == ds_name:
                target_ds = ds
                break

        if not target_ds:
            return jsonify({'error': f'Dataset "{ds_name}" not found in project'}), 404

        ds_id = target_ds['id']
        return load_dataset_file_by_id(dataset_display_name, ds_id, token, session_id)
    except ProjectDatasetEntriesError as e:
        return e.to_response()
    except requests.exceptions.ConnectionError as e:
        logger.error(f"Connection error loading dataset via API: {e}")
        return jsonify({'error': 'Could not connect to required services'}), 503
    except Exception as e:
        logger.error(f"Error loading dataset via API: {e}")
        logger.error(traceback.format_exc())
        return jsonify({'error': f'Error loading dataset: {str(e)}'}), 500


def load_dataset_file_by_id(dataset_display_name, dataset_id, token=None, session_id=None):
    """Download a file from a Domino dataset by dataset ID and load it into the MCP server.
    Used when the app is opened via 'Open with...' (datasetFileContext mode).
    Skips the project-based dataset lookup since we already have the dataset ID.
    """
    token = token or get_passthrough_token()
    session_id = session_id or get_session_id()
    if not token:
        return jsonify({'error': 'Authentication required. Please ensure you are accessing this app through Domino.'}), 401
    api_host = get_domino_api_host()
    if not api_host:
        return jsonify({'error': 'Domino API host not configured'}), 500

    # Parse "dataset_name/file_name" format
    parts = dataset_display_name.split('/', 1)
    if len(parts) != 2:
        return jsonify({'error': f'Invalid dataset reference: {dataset_display_name}'}), 400

    ds_name, file_name = parts

    headers = {'Authorization': f'Bearer {token}'}

    try:
        snapshots_list_response = httpclient.get(
            f'{api_host}/api/datasetrw/v1/datasets/{dataset_id}/snapshots',
            params={'limit': 1},
            headers=headers,
        )
        snapshots = snapshots_list_response.get("snapshots", [])
        if len(snapshots) == 0:
            return jsonify({'error': f'No snapshots found for dataset {dataset_id}'}), 422

        default_snapshot_id = snapshots[0]["id"]
        return load_dataset_file_from_snapshot(
            dataset_display_name,
            dataset_id,
            default_snapshot_id,
            token,
            session_id,
        )
    except httpclient.HTTPClientError as exc:
        logger.error(f"Error listing snapshots for dataset {dataset_id}: {exc.text}")
        return jsonify({'error': exc.text}), exc.status_code
    except Exception as e:
        logger.error(f"Error loading dataset file by ID: {e}")
        logger.error(traceback.format_exc())
        return jsonify({'error': f'Error loading dataset: {str(e)}'}), 500


def load_dataset_file_from_snapshot(dataset_display_name, dataset_id, snapshot_id, token=None, session_id=None):
    """Download a file from a specific dataset snapshot using Domino API.
    Unlike DatasetClient which always uses the active snapshot,
    this uses /v4/datasetrw/snapshot/{snapshotId}/file/raw to download from any snapshot.
    """
    session_id = session_id or get_session_id()

    try:
        target = download_dataset_file_from_snapshot_to_cache(
            dataset_display_name,
            dataset_id,
            snapshot_id,
            token=token,
        )
        try:
            # Load into MCP server
            mcp_response = mcp_post(
                "/dataset/load",
                params={'file_snapshot_path': target.file_snapshot_path},
                session_id=session_id,
            )

            if mcp_response.status_code == 200:
                result = mcp_response.json()
                result['dataset'] = dataset_display_name
                # Identifier fields for snapshot-specific governance lookup
                result['sourceType'] = 'dataset'
                result['datasetId'] = dataset_id
                result['snapshotId'] = snapshot_id
                result['governanceFilename'] = target.file_path.split('/')[-1]
                clear_history(session_id=session_id)
                return jsonify(result)
            else:
                error_detail = mcp_response.json().get('detail', 'Failed to load dataset')
                return jsonify({'error': error_detail}), mcp_response.status_code
        finally:
            get_file_cache().remove('dataset', dataset_id, snapshot_id, target.file_path)

    except requests.exceptions.ConnectionError as e:
        logger.error(f"Connection error loading snapshot file: {e}")
        return jsonify({'error': 'Could not connect to required services'}), 503
    except HTTPException as e:
        return jsonify({'error': e.description}), e.code
    except Exception as e:
        logger.error(f"Error loading file from snapshot: {e}")
        logger.error(traceback.format_exc())
        return jsonify({'error': f'Error loading file from snapshot: {str(e)}'}), 500


def download_dataset_file_from_snapshot_to_cache(dataset_display_name, dataset_id, snapshot_id, token=None) -> DatasetLoadTarget:
    """Download a Domino dataset snapshot file into the backend cache."""
    api_host = get_domino_api_host()
    token = token or get_passthrough_token()
    if not token:
        raise Unauthorized('Authentication required.')

    if not api_host:
        raise InternalServerError('Domino API host not configured')

    parts = dataset_display_name.split('/', 1)
    if len(parts) != 2:
        raise BadRequest(f'Invalid dataset reference: {dataset_display_name}')

    _, file_path = parts

    headers = {'Authorization': f'Bearer {token}'}
    download_url = f'{api_host}/v4/datasetrw/snapshot/{snapshot_id}/file/raw'
    response = requests.get(
        download_url,
        params={'path': file_path, 'download': 'true'},
        headers=headers,
        timeout=120,
        stream=True
    )

    if response.status_code == 401:
        raise Unauthorized('Access denied. Your session may have expired.')
    if response.status_code == 403:
        raise Forbidden('Access denied. Your session may have expired.')
    if response.status_code == 404:
        raise NotFound(f'File "{file_path}" not found in snapshot "{snapshot_id}"')
    if response.status_code != 200:
        logger.error(f"Snapshot file download failed: {response.status_code} - {response.text[:200]}")
        exc = HTTPException(description=f'Failed to download file from snapshot (HTTP {response.status_code})')
        exc.code = response.status_code
        raise exc

    temp_path = _create_download_cache_file('dataset', dataset_id, snapshot_id, file_path)
    logger.info(f"Downloading {file_path} from snapshot {snapshot_id} to {temp_path}")
    with open(temp_path, 'wb') as f:
        for chunk in response.iter_content(chunk_size=8192):
            f.write(chunk)
    logger.info(f"Downloaded snapshot file to {temp_path}")

    return DatasetLoadTarget(
        file_snapshot_path=str(temp_path),
        dataset_id=dataset_id,
        snapshot_id=snapshot_id,
        source_type='dataset',
        file_path=file_path,
    )


def load_netapp_volume_file(dataset_display_name, volume_key, snapshot_version=None, snapshot_id=None, token=None, session_id=None):
    """Download a file from a NetApp volume and load it into the MCP server.
    Args:
        dataset_display_name: "VolumeName/file_name" format
        volume_key: The volume unique name (e.g. "netapp-volume-Name-uuid")
        snapshot_version: Optional per-volume integer snapshot version. When
            provided, reads the file from that snapshot; otherwise reads the
            r/w head.
        snapshot_id: Optional globally-unique UUID of the snapshot — used to
            populate governance context in the response. The SDK pins the read
            by version, but governance attachments are keyed by snapshotId.
    """
    session_id = session_id or get_session_id()

    try:
        target = download_netapp_volume_file_to_cache(
            dataset_display_name,
            volume_key,
            snapshot_version,
            snapshot_id,
            token=token,
        )
        try:
            # Tell the MCP server to load this file from the temp path
            mcp_response = mcp_post(
                "/dataset/load",
                params={'file_snapshot_path': target.file_snapshot_path},
                session_id=session_id,
            )

            if mcp_response.status_code == 200:
                result = mcp_response.json()
                result['dataset'] = dataset_display_name
                # Identifier fields for governance lookup. Only when the load was
                # pinned to a specific snapshot version can this match an attachment
                # (r/w-head files cannot be attached to a bundle).
                result['sourceType'] = 'netapp'
                if target.volume_id:
                    result['volumeId'] = target.volume_id
                if target.snapshot_version is not None and target.snapshot_version != '':
                    result['snapshotVersion'] = target.snapshot_version
                if target.snapshot_id:
                    result['snapshotId'] = target.snapshot_id
                result['governanceFilename'] = target.file_path.split('/')[-1]
                clear_history(session_id=session_id)
                return jsonify(result)
            else:
                error_detail = mcp_response.json().get('detail', 'Failed to load dataset')
                return jsonify({'error': error_detail}), mcp_response.status_code
        finally:
            snapshot_key = "unset_snapshot_id" if target.snapshot_version in (None, '') else target.snapshot_version
            get_file_cache().remove('netapp', volume_key, snapshot_key, target.file_path)

    except requests.exceptions.ConnectionError as e:
        logger.error(f"Connection error loading NetApp volume file: {e}")
        return jsonify({'error': 'Could not connect to required services'}), 503
    except HTTPException as e:
        return jsonify({'error': e.description}), e.code
    except Exception as e:
        logger.error(f"Error loading NetApp volume file: {e}")
        logger.error(traceback.format_exc())
        # TODO there should be file cleanup logic here
        return jsonify({'error': f'Error loading file from volume: {str(e)}'}), 500


def download_netapp_volume_file_to_cache(dataset_display_name, volume_key, snapshot_version=None, snapshot_id=None, token=None) -> DatasetLoadTarget:
    """Download a NetApp volume file into the backend cache."""
    token = token or get_passthrough_token()
    if not token:
        raise Unauthorized('Authentication required. Please ensure you are accessing this app through Domino.')

    parts = dataset_display_name.split('/', 1)
    if len(parts) != 2:
        raise BadRequest(f'Invalid volume file reference: {dataset_display_name}')

    vol_name, file_name = parts

    from domino_data.netapp_volumes import NetAppVolumeClient
    vol_client = NetAppVolumeClient(token=token)
    volume = vol_client.get_volume(volume_key)

    if (snapshot_version is None or snapshot_version == '') and snapshot_id and snapshot_id != 'latest':
        try:
            snaps = vol_client.list_snapshots(volume_unique_name=volume_key) or []
            for s in snaps:
                if s.id == snapshot_id:
                    snapshot_version = s.version
                    break
        except Exception as e:
            logger.warning(f"Could not resolve snapshot version for {snapshot_id} on {volume_key}: {e}")

    if snapshot_version is not None and snapshot_version != '':
        from domino_data.data_sources import NetAppVolumeConfig
        volume.update(NetAppVolumeConfig(snapshot_version=str(snapshot_version)))

    if snapshot_version is not None and snapshot_version != '':
        file_objects = volume.list_files() or []
        files = [f.key if hasattr(f, 'key') else str(f) for f in file_objects]
    else:
        files = vol_client.list_files(volume_key)

    if file_name not in files:
        raise NotFound(f'File "{file_name}" not found in volume "{vol_name}"')

    target_file = volume.File(file_name)
    temp_path = _create_download_cache_file('netapp', volume_key, snapshot_version, file_name)
    logger.info(f"Downloading {file_name} from NetApp volume {volume_key} to {temp_path}")
    buf = io.BytesIO()
    target_file.download_fileobj(buf)
    with open(temp_path, 'wb') as f:
        f.write(buf.getbuffer())
    logger.info(f"Downloaded {len(buf.getbuffer())} bytes to {temp_path}")

    vol_id = getattr(volume, 'id', None) or getattr(volume, 'volume_id', None)
    return DatasetLoadTarget(
        file_snapshot_path=str(temp_path),
        source_type='netapp',
        volume_key=volume_key,
        volume_id=vol_id,
        snapshot_id=snapshot_id,
        snapshot_version=snapshot_version,
        file_path=file_name,
    )


def process_dataset_download_file_request(load_request: DatasetLoadRequest):
    """Process a queued dataset request through file resolution and download only."""
    token = get_passthrough_token_from_authorization_header(load_request.authorization_header)
    target = resolve_dataset_load_target(load_request, token=token)

    if target.source_type == 'netapp' and target.volume_key:
        target = download_netapp_volume_file_to_cache(
            load_request.dataset,
            target.volume_key,
            target.snapshot_version,
            target.snapshot_id,
            token=token,
        )
    elif target.dataset_id and target.snapshot_id:
        target = download_dataset_file_from_snapshot_to_cache(
            load_request.dataset,
            target.dataset_id,
            target.snapshot_id,
            token=token,
        )

    return jsonify(_dataset_file_metadata_response(load_request, target))


def process_dataset_load_request(load_request: DatasetLoadRequest):
    """Process a queued dataset-load request through the appropriate load path."""
    if not load_request.create_dataframe:
        return process_dataset_download_file_request(load_request)

    token = get_passthrough_token_from_authorization_header(load_request.authorization_header)

    if load_request.source_type == 'netapp' and load_request.volume_key:
        return load_netapp_volume_file(
            load_request.dataset,
            load_request.volume_key,
            load_request.snapshot_version,
            load_request.snapshot_id,
            token=token,
            session_id=load_request.session_id,
        )

    if load_request.dataset_id and load_request.snapshot_id:
        return load_dataset_file_from_snapshot(
            load_request.dataset,
            load_request.dataset_id,
            load_request.snapshot_id,
            token=token,
            session_id=load_request.session_id,
        )

    if load_request.dataset_id:
        return load_dataset_file_by_id(
            load_request.dataset,
            load_request.dataset_id,
            token=token,
            session_id=load_request.session_id,
        )

    if load_request.project_id:
        return load_dataset_via_api(
            load_request.dataset,
            load_request.project_id,
            token=token,
            session_id=load_request.session_id,
        )

    return load_local_dataset_file(load_request.dataset, session_id=load_request.session_id)


def _list_dataset_snapshots(dataset_id, token):
    """List snapshots for a Domino dataset via the datasetrw API."""
    api_host = get_domino_api_host()
    if not api_host:
        return jsonify({'error': 'Domino API host not configured', 'snapshots': []}), 500

    try:
        headers = {'Authorization': f'Bearer {token}'}
        response = requests.get(
            f'{api_host}/v4/datasetrw/snapshots/{dataset_id}',
            headers=headers,
            timeout=30
        )

        if response.status_code in (401, 403):
            return jsonify({'error': 'Access denied', 'snapshots': []}), response.status_code
        if response.status_code != 200:
            logger.warning(f"Snapshots API returned {response.status_code}: {response.text[:200]}")
            return jsonify({'error': f'Failed to list snapshots (HTTP {response.status_code})', 'snapshots': []}), 500

        raw_snapshots = response.json()
        if not isinstance(raw_snapshots, list):
            raw_snapshots = raw_snapshots.get('data', raw_snapshots.get('snapshots', []))

        # Filter to Active, sort by version desc
        snapshots = []
        for s in raw_snapshots:
            status = s.get('lifecycleStatus', s.get('status', ''))
            if status in ('Active', 'active', ''):
                snapshots.append({
                    'id': s.get('id', ''),
                    'version': s.get('version', 0),
                    'description': s.get('description'),
                    'creationTime': s.get('creationTime', 0),
                    'isReadWrite': s.get('isReadWrite', False),
                    'lifecycleStatus': status or 'Active',
                })

        snapshots.sort(key=lambda x: x['version'], reverse=True)

        return jsonify({
            'snapshots': snapshots,
            'sourceType': 'dataset',
            'datasetId': dataset_id
        })

    except requests.exceptions.ConnectionError:
        logger.error("Could not connect to Domino API for snapshot listing")
        return jsonify({'error': 'Could not connect to Domino API', 'snapshots': []}), 503
    except Exception as e:
        logger.error(f"Error listing dataset snapshots: {e}")
        logger.error(traceback.format_exc())
        return jsonify({'error': f'Error listing snapshots: {str(e)}', 'snapshots': []}), 500


def _list_netapp_snapshots(volume_unique_name, token):
    """List snapshots for a NetApp volume using the domino_data SDK.
    Includes a synthetic 'latest' entry for the current (non-snapshot) state.
    """
    try:
        from domino_data.netapp_volumes import NetAppVolumeClient
        vol_client = NetAppVolumeClient(token=token)
        raw_snapshots = vol_client.list_snapshots(volume_unique_name=volume_unique_name)

        snapshots = []
        max_version = -1
        for s in raw_snapshots:
            status = ''
            if hasattr(s, 'status') and s.status:
                status = str(s.status)
            # TODO do these usually not have versions?
            ver = s.version if hasattr(s, 'version') else 0
            if ver > max_version:
                max_version = ver
            snapshots.append({
                'id': s.id if hasattr(s, 'id') else '',
                'version': ver,
                'description': s.description if hasattr(s, 'description') else None,
                'createdAt': s.created_at if hasattr(s, 'created_at') else None,
                'status': status,
                'volumeId': s.volume_id if hasattr(s, 'volume_id') else '',
            })

        # Add synthetic "latest" entry for the current read-write volume state
        # (not a real snapshot — identified by id='latest' and no version number)
        snapshots.append({
            'id': 'latest',
            'version': max_version + 1,
            'description': 'Current volume data (latest)',
            'createdAt': None,
            'status': 'Active',
            'volumeId': '',
            'isLatest': True,
        })

        snapshots.sort(key=lambda x: x['version'], reverse=True)

        return jsonify({
            'snapshots': snapshots,
            'sourceType': 'netapp',
            'volumeKey': volume_unique_name
        })

    except Exception as e:
        logger.error(f"Error listing NetApp volume snapshots: {e}")
        logger.error(traceback.format_exc())
        return jsonify({'error': f'Error listing snapshots: {str(e)}', 'snapshots': []}), 500


def _parse_datasetrw_rows(rows, subpath):
    """Parse rows from the datasetrw files API into our entry format."""
    entries = []
    for row in rows:
        name_entry = row.get('name', {})
        size_entry = row.get('size', {})

        is_dir = name_entry.get('isDirectory', name_entry.get('isDir', False))
        label = name_entry.get('label', '')

        if label.startswith('.'):
            continue
        if not is_dir:
            ext = os.path.splitext(label)[1].lower()
            if ext not in SUPPORTED_EXTENSIONS:
                continue

        full_path = f'{subpath}/{label}' if subpath else label
        entries.append({
            'name': label,
            'isDir': is_dir,
            'fileName': label,
            'size': size_entry.get('sizeInBytes') or size_entry.get('inBytes') or size_entry.get('label', ''),
            'path': full_path,
        })

    entries.sort(key=lambda e: (0 if e['isDir'] else 1, e['name'].lower()))
    return entries


@contextmanager
def data_file_path(dataset_id: str, file_name: str, source_type: SourceType = 'dataset', snapshot_id: str = "unset_snapshot_id") -> str:
    """
    This creates a temporary path for downloading a dataset or netapp volume's file into
    The temp dir is cleaned up after use and a file cache will handle removing any files that get orphaned while the pod
    is still running.
    """
    file_cache = get_file_cache()
    dataset_id = str(dataset_id)
    file_name = str(file_name)
    snapshot_id = "unset_snapshot_id" if snapshot_id in (None, '') else str(snapshot_id)

    try:
        temp_path = file_cache.set(source_type, dataset_id, snapshot_id, file_name)
        if temp_path.exists():
            # remove the file contents that are there
            Path(temp_path).write_text("")

        yield temp_path
    finally:
        file_cache.remove(source_type, dataset_id, snapshot_id, file_name)
