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
import io
import logging
import os
import shutil
import tempfile
import traceback
from pathlib import Path

import requests
from flask import jsonify

from backend.auth import get_domino_api_host, get_passthrough_token
from backend.session import get_session_id, mcp_post
from chat_agent import clear_history

logger = logging.getLogger(__name__)

# Supported file extensions for data files.
SUPPORTED_EXTENSIONS = {'.csv', '.parquet', '.pq', '.sas7bdat', '.xpt'}


def find_data_files_fallback():
    """
    Fallback function to find data files when MCP server is unavailable.
    Searches datasets/ folder, /mnt/data/, /mnt/netapp-volumes/, /domino/datasets/, and /domino/netapp-volumes/ recursively.
    """
    data_files = []
    datasets_folder = Path('datasets')
    mnt_data_folder = Path('/mnt/data')
    mnt_netapp_folder = Path('/mnt/netapp-volumes')
    domino_datasets_folder = Path('/domino/datasets')
    domino_netapp_folder = Path('/domino/netapp-volumes')

    # Search in datasets/ folder (flat search)
    if datasets_folder.exists():
        for ext in SUPPORTED_EXTENSIONS:
            for f in datasets_folder.glob(f"*{ext}"):
                data_files.append(f.name)

    # Search in /mnt/data/ folder recursively
    if mnt_data_folder.exists():
        for ext in SUPPORTED_EXTENSIONS:
            for f in mnt_data_folder.rglob(f"*{ext}"):
                relative_path = f.relative_to(mnt_data_folder)
                data_files.append(f"/mnt/data/{relative_path}")

    # Search in /mnt/netapp-volumes/ folder recursively
    if mnt_netapp_folder.exists():
        for ext in SUPPORTED_EXTENSIONS:
            for f in mnt_netapp_folder.rglob(f"*{ext}"):
                relative_path = f.relative_to(mnt_netapp_folder)
                data_files.append(f"/mnt/netapp-volumes/{relative_path}")

    # Search in /domino/datasets/ folder recursively
    if domino_datasets_folder.exists():
        for ext in SUPPORTED_EXTENSIONS:
            for f in domino_datasets_folder.rglob(f"*{ext}"):
                relative_path = f.relative_to(domino_datasets_folder)
                data_files.append(f"/domino/datasets/{relative_path}")

    # Search in /domino/netapp-volumes/ folder recursively
    if domino_netapp_folder.exists():
        for ext in SUPPORTED_EXTENSIONS:
            for f in domino_netapp_folder.rglob(f"*{ext}"):
                relative_path = f.relative_to(domino_netapp_folder)
                data_files.append(f"/domino/netapp-volumes/{relative_path}")

    return data_files


def discover_netapp_files_for_project(project_id, token):
    """Discover NetApp volume files for a project.
    Queries the RemoteFS microservice for volumes attached to the project,
    then lists supported files in each volume using the domino_data SDK.
    Returns a list of {display_name, volume_key} dicts, or empty list on failure.
    """
    remotefs_host = os.environ.get('DOMINO_REMOTE_FILE_SYSTEM_HOSTPORT')
    if not remotefs_host:
        logger.debug("DOMINO_REMOTE_FILE_SYSTEM_HOSTPORT not set, skipping NetApp volume discovery")
        return []

    # Ensure the host has a scheme
    if not remotefs_host.startswith('http'):
        remotefs_host = f'http://{remotefs_host}'

    try:
        headers = {'Authorization': f'Bearer {token}'}

        # Query RemoteFS API for active volumes attached to this project
        response = requests.get(
            f'{remotefs_host}/remotefs/v1/volumes',
            params={'status': 'Active', 'project_id': project_id},
            headers=headers,
            timeout=30
        )

        if response.status_code != 200:
            logger.warning(f"NetApp volumes API returned {response.status_code}: {response.text[:200]}")
            return []

        volumes_data = response.json()
        # The response may be a list directly or wrapped in a key
        volumes = volumes_data if isinstance(volumes_data, list) else volumes_data.get('data', volumes_data.get('volumes', []))

        if not volumes:
            return []

        from domino_data.netapp_volumes import NetAppVolumeClient
        vol_client = NetAppVolumeClient(token=token)

        netapp_files = []
        for vol in volumes:
            vol_name = vol.get('name', '')
            vol_id = vol.get('id', '')
            vol_unique_name = vol.get('uniqueName', vol.get('unique_name', f'netapp-volume-{vol_name}-{vol_id}'))

            try:
                # Use client.list_files() which returns plain strings (file paths),
                # not Volume.list_files() which returns _File objects
                files = vol_client.list_files(vol_unique_name) or []
                for fname in files:
                    ext = os.path.splitext(fname)[1].lower()
                    if ext in SUPPORTED_EXTENSIONS:
                        netapp_files.append({
                            'display_name': f'{vol_name}/{fname}',
                            'volume_key': vol_unique_name,
                            'volume_name': vol_name,
                            'volume_id': vol_id
                        })
            except Exception as e:
                error_msg = str(e).split('\n')[0][:200]
                logger.warning(f'Failed to list files for NetApp volume {vol_name}: {error_msg}')

        return netapp_files

    except requests.exceptions.ConnectionError:
        logger.warning("Could not connect to RemoteFS service for NetApp volume discovery")
        return []
    except Exception as e:
        logger.warning(f"Error discovering NetApp volumes: {e}")
        return []


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

        # Get datasets (the API returns all accessible datasets; we filter by projectId)
        response = requests.get(
            f'{api_host}/api/datasetrw/v2/datasets?projectIdsToInclude={project_id}&limit=100',
            headers=headers,
            timeout=30
        )

        if response.status_code == 401 or response.status_code == 403:
            return jsonify({
                'error': 'Access denied. You may not have permission to access this project\'s datasets.',
                'auth_error': True,
                'datasets': []
            }), response.status_code

        if response.status_code != 200:
            logger.error(f"Datasets API error: {response.status_code} - {response.text}")
            return jsonify({'error': f'Failed to list datasets (HTTP {response.status_code})', 'datasets': []}), 500

        all_datasets = response.json().get('datasets', [])
        # Filter to only datasets belonging to the target project
        project_datasets = [
            d.get('dataset', d) for d in all_datasets
            if d.get('dataset', d).get('projectId') == project_id
        ]

        # List files from datasets
        file_list = []
        if project_datasets:
            from domino_data.datasets import DatasetClient
            client = DatasetClient(token=token)

            for ds in project_datasets:
                ds_id = ds['id']
                ds_name = ds['name']
                dataset_key = f'dataset-{ds_name}-{ds_id}'

                try:
                    dataset = client.get_dataset(dataset_key)
                    files = dataset.list_files()
                    for f in files:
                        ext = os.path.splitext(f.name)[1].lower()
                        if ext in SUPPORTED_EXTENSIONS:
                            file_list.append(f'{ds_name}/{f.name}')
                except Exception as e:
                    error_msg = str(e).split('\n')[0][:200]
                    logger.warning(f'Failed to list files for dataset {ds_name}: {error_msg}')

        # Build dataset_info for the frontend (needed for snapshot browsing)
        dataset_info = [{'id': ds['id'], 'name': ds['name']} for ds in project_datasets]

        # Also discover NetApp volume files for this project
        netapp_files = discover_netapp_files_for_project(project_id, token)

        return jsonify({
            'datasets': file_list,
            'dataset_info': dataset_info,
            'netapp_files': netapp_files,
            'current_dataset': None,
            'extension_mode': True,
            'project_id': project_id
        })

    except requests.exceptions.ConnectionError:
        logger.error("Could not connect to Domino API for dataset listing")
        return jsonify({'error': 'Could not connect to Domino API', 'datasets': []}), 503
    except Exception as e:
        logger.error(f"Error listing datasets via API: {e}")
        logger.error(traceback.format_exc())
        return jsonify({'error': f'Error listing datasets: {str(e)}', 'datasets': []}), 500


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


def load_dataset_via_api(dataset_display_name, project_id):
    """Download a file from a Domino dataset via API and load it into the MCP server."""
    token = get_passthrough_token()
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

        # Resolve dataset ID by querying the API
        response = requests.get(
            f'{api_host}/api/datasetrw/v2/datasets?projectId={project_id}&limit=100',
            headers=headers,
            timeout=30
        )

        if response.status_code == 401 or response.status_code == 403:
            return jsonify({'error': 'Access denied. Your session may have expired. Please refresh the page.'}), response.status_code

        if response.status_code != 200:
            return jsonify({'error': 'Failed to resolve dataset'}), 500

        all_datasets = response.json().get('datasets', [])
        target_ds = None
        for d in all_datasets:
            ds = d.get('dataset', d)
            if ds.get('name') == ds_name and ds.get('projectId') == project_id:
                target_ds = ds
                break

        if not target_ds:
            return jsonify({'error': f'Dataset "{ds_name}" not found in project'}), 404

        ds_id = target_ds['id']
        dataset_key = f'dataset-{ds_name}-{ds_id}'

        # Download the file using domino_data
        from domino_data.datasets import DatasetClient
        client = DatasetClient(token=token)
        dataset = client.get_dataset(dataset_key)

        # Find the file in the dataset
        files = dataset.list_files()
        target_file = None
        for f in files:
            if f.name == file_name:
                target_file = f
                break

        if not target_file:
            return jsonify({'error': f'File "{file_name}" not found in dataset "{ds_name}"'}), 404

        # Download to session-specific temp directory (avoids filename collisions between users)
        sid = get_session_id()
        temp_dir = os.path.join(tempfile.gettempdir(), 'domino_api_datasets', sid)
        # Clean previous downloads for this session
        if os.path.exists(temp_dir):
            shutil.rmtree(temp_dir, ignore_errors=True)
        os.makedirs(temp_dir, exist_ok=True)
        temp_path = os.path.join(temp_dir, file_name)
        os.makedirs(os.path.dirname(temp_path), exist_ok=True)

        logger.info(f"Downloading {file_name} from dataset {ds_name} to {temp_path}")
        file_content = _download_dataset_file(dataset, file_name, token)
        with open(temp_path, 'wb') as f:
            f.write(file_content)
        logger.info(f"Downloaded {len(file_content)} bytes to {temp_path}")

        # Tell the MCP server to load this file from the temp path
        mcp_response = mcp_post("/dataset/load", params={'dataset_name': temp_path})

        if mcp_response.status_code == 200:
            result = mcp_response.json()
            # Show the friendly display name, not the temp path
            result['dataset'] = dataset_display_name
            # Identifier fields for snapshot-specific governance lookup
            active_snap_id = _get_active_dataset_snapshot_id(api_host, ds_id, token)
            result['sourceType'] = 'dataset'
            result['datasetId'] = ds_id
            if active_snap_id:
                result['snapshotId'] = active_snap_id
            result['governanceFilename'] = file_name
            clear_history(session_id=get_session_id())
            return jsonify(result)
        else:
            error_detail = mcp_response.json().get('detail', 'Failed to load dataset')
            return jsonify({'error': error_detail}), mcp_response.status_code

    except requests.exceptions.ConnectionError as e:
        logger.error(f"Connection error loading dataset via API: {e}")
        return jsonify({'error': 'Could not connect to required services'}), 503
    except Exception as e:
        logger.error(f"Error loading dataset via API: {e}")
        logger.error(traceback.format_exc())
        return jsonify({'error': f'Error loading dataset: {str(e)}'}), 500


def load_dataset_file_by_id(dataset_display_name, dataset_id):
    """Download a file from a Domino dataset by dataset ID and load it into the MCP server.
    Used when the app is opened via 'Open with...' (datasetFileContext mode).
    Skips the project-based dataset lookup since we already have the dataset ID.
    """
    token = get_passthrough_token()
    if not token:
        return jsonify({'error': 'Authentication required. Please ensure you are accessing this app through Domino.'}), 401

    # Parse "dataset_name/file_name" format
    parts = dataset_display_name.split('/', 1)
    if len(parts) != 2:
        return jsonify({'error': f'Invalid dataset reference: {dataset_display_name}'}), 400

    ds_name, file_name = parts

    try:
        # We already have the dataset ID — construct the key directly
        dataset_key = f'dataset-{ds_name}-{dataset_id}'

        from domino_data.datasets import DatasetClient
        client = DatasetClient(token=token)
        dataset = client.get_dataset(dataset_key)

        # Find the file in the dataset
        files = dataset.list_files()
        target_file = None
        for f in files:
            if f.name == file_name:
                target_file = f
                break

        if not target_file:
            return jsonify({'error': f'File "{file_name}" not found in dataset "{ds_name}"'}), 404

        # Download to session-specific temp directory (avoids filename collisions between users)
        sid = get_session_id()
        temp_dir = os.path.join(tempfile.gettempdir(), 'domino_api_datasets', sid)
        if os.path.exists(temp_dir):
            shutil.rmtree(temp_dir, ignore_errors=True)
        os.makedirs(temp_dir, exist_ok=True)
        temp_path = os.path.join(temp_dir, file_name)
        os.makedirs(os.path.dirname(temp_path), exist_ok=True)

        logger.info(f"Downloading {file_name} from dataset {ds_name} (id={dataset_id}) to {temp_path}")
        file_content = _download_dataset_file(dataset, file_name, token)
        with open(temp_path, 'wb') as f:
            f.write(file_content)
        logger.info(f"Downloaded {len(file_content)} bytes to {temp_path}")

        # Tell the MCP server to load this file from the temp path
        mcp_response = mcp_post("/dataset/load", params={'dataset_name': temp_path})

        if mcp_response.status_code == 200:
            result = mcp_response.json()
            result['dataset'] = dataset_display_name
            # Identifier fields for snapshot-specific governance lookup.
            # No explicit snapshot was requested, so resolve the active one.
            active_snap_id = _get_active_dataset_snapshot_id(get_domino_api_host(), dataset_id, token)
            result['sourceType'] = 'dataset'
            result['datasetId'] = dataset_id
            if active_snap_id:
                result['snapshotId'] = active_snap_id
            result['governanceFilename'] = file_name
            clear_history(session_id=get_session_id())
            return jsonify(result)
        else:
            error_detail = mcp_response.json().get('detail', 'Failed to load dataset')
            return jsonify({'error': error_detail}), mcp_response.status_code

    except requests.exceptions.ConnectionError as e:
        logger.error(f"Connection error loading dataset file by ID: {e}")
        return jsonify({'error': 'Could not connect to required services'}), 503
    except Exception as e:
        logger.error(f"Error loading dataset file by ID: {e}")
        logger.error(traceback.format_exc())
        return jsonify({'error': f'Error loading dataset: {str(e)}'}), 500


def load_dataset_file_from_snapshot(dataset_display_name, dataset_id, snapshot_id):
    """Download a file from a specific dataset snapshot using Domino API.
    Unlike DatasetClient which always uses the active snapshot,
    this uses /v4/datasetrw/snapshot/{snapshotId}/file/raw to download from any snapshot.
    """
    token = get_passthrough_token()
    if not token:
        return jsonify({'error': 'Authentication required.'}), 401

    # Parse "dataset_name/file_path" format (may include subdirectory paths)
    parts = dataset_display_name.split('/', 1)
    if len(parts) != 2:
        return jsonify({'error': f'Invalid dataset reference: {dataset_display_name}'}), 400

    ds_name, file_path = parts

    api_host = get_domino_api_host()
    if not api_host:
        return jsonify({'error': 'Domino API host not configured'}), 500

    try:
        headers = {'Authorization': f'Bearer {token}'}

        # Download file from specific snapshot via raw content API
        download_url = f'{api_host}/v4/datasetrw/snapshot/{snapshot_id}/file/raw'
        response = requests.get(
            download_url,
            params={'path': file_path, 'download': 'true'},
            headers=headers,
            timeout=120,
            stream=True
        )

        if response.status_code in (401, 403):
            return jsonify({'error': 'Access denied. Your session may have expired.'}), response.status_code
        if response.status_code != 200:
            logger.error(f"Snapshot file download failed: {response.status_code} - {response.text[:200]}")
            return jsonify({'error': f'Failed to download file from snapshot (HTTP {response.status_code})'}), response.status_code

        # Save to session-specific temp directory
        sid = get_session_id()
        temp_dir = os.path.join(tempfile.gettempdir(), 'domino_api_datasets', sid)
        if os.path.exists(temp_dir):
            shutil.rmtree(temp_dir, ignore_errors=True)
        os.makedirs(temp_dir, exist_ok=True)

        file_name = file_path.split('/')[-1]
        temp_path = os.path.join(temp_dir, file_name)
        os.makedirs(os.path.dirname(temp_path), exist_ok=True)

        logger.info(f"Downloading {file_path} from snapshot {snapshot_id} to {temp_path}")
        with open(temp_path, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)
        logger.info(f"Downloaded snapshot file to {temp_path}")

        # Load into MCP server
        mcp_response = mcp_post("/dataset/load", params={'dataset_name': temp_path})

        if mcp_response.status_code == 200:
            result = mcp_response.json()
            result['dataset'] = dataset_display_name
            # Identifier fields for snapshot-specific governance lookup
            result['sourceType'] = 'dataset'
            result['datasetId'] = dataset_id
            result['snapshotId'] = snapshot_id
            result['governanceFilename'] = file_path.split('/')[-1]
            clear_history(session_id=get_session_id())
            return jsonify(result)
        else:
            error_detail = mcp_response.json().get('detail', 'Failed to load dataset')
            return jsonify({'error': error_detail}), mcp_response.status_code

    except requests.exceptions.ConnectionError as e:
        logger.error(f"Connection error loading snapshot file: {e}")
        return jsonify({'error': 'Could not connect to required services'}), 503
    except Exception as e:
        logger.error(f"Error loading file from snapshot: {e}")
        logger.error(traceback.format_exc())
        return jsonify({'error': f'Error loading file from snapshot: {str(e)}'}), 500


def load_netapp_volume_file(dataset_display_name, volume_key, snapshot_version=None, snapshot_id=None):
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
    token = get_passthrough_token()
    if not token:
        return jsonify({'error': 'Authentication required. Please ensure you are accessing this app through Domino.'}), 401

    # Parse "volume_name/file_name" format
    parts = dataset_display_name.split('/', 1)
    if len(parts) != 2:
        return jsonify({'error': f'Invalid volume file reference: {dataset_display_name}'}), 400

    vol_name, file_name = parts

    try:
        from domino_data.netapp_volumes import NetAppVolumeClient
        vol_client = NetAppVolumeClient(token=token)
        volume = vol_client.get_volume(volume_key)

        # Pin the volume to a specific snapshot so list_files / File() operate
        # against that snapshot's contents rather than the r/w head.
        if snapshot_version is not None and snapshot_version != '':
            from domino_data.data_sources import NetAppVolumeConfig
            volume.update(NetAppVolumeConfig(snapshot_version=str(snapshot_version)))

        # Verify the file exists. For snapshot reads we list via the volume
        # (which respects the pinned snapshot); otherwise list via the client
        # (r/w head).
        if snapshot_version is not None and snapshot_version != '':
            file_objects = volume.list_files() or []
            files = [f.key if hasattr(f, 'key') else str(f) for f in file_objects]
        else:
            files = vol_client.list_files(volume_key)
        if file_name not in files:
            return jsonify({'error': f'File "{file_name}" not found in volume "{vol_name}"'}), 404

        # Use volume.File() factory to get a downloadable file handle
        target_file = volume.File(file_name)

        # Download to session-specific temp directory
        sid = get_session_id()
        temp_dir = os.path.join(tempfile.gettempdir(), 'domino_api_datasets', sid)
        if os.path.exists(temp_dir):
            shutil.rmtree(temp_dir, ignore_errors=True)
        os.makedirs(temp_dir, exist_ok=True)
        temp_path = os.path.join(temp_dir, file_name)

        logger.info(f"Downloading {file_name} from NetApp volume {vol_name} to {temp_path}")
        buf = io.BytesIO()
        target_file.download_fileobj(buf)
        with open(temp_path, 'wb') as f:
            f.write(buf.getbuffer())
        logger.info(f"Downloaded {len(buf.getbuffer())} bytes to {temp_path}")

        # Tell the MCP server to load this file from the temp path
        mcp_response = mcp_post("/dataset/load", params={'dataset_name': temp_path})

        if mcp_response.status_code == 200:
            result = mcp_response.json()
            result['dataset'] = dataset_display_name
            # Identifier fields for governance lookup. Only when the load was
            # pinned to a specific snapshot version can this match an attachment
            # (r/w-head files cannot be attached to a bundle).
            vol_id = getattr(volume, 'id', None) or getattr(volume, 'volume_id', None)
            result['sourceType'] = 'netapp'
            if vol_id:
                result['volumeId'] = vol_id
            if snapshot_version is not None and snapshot_version != '':
                result['snapshotVersion'] = snapshot_version
            if snapshot_id:
                result['snapshotId'] = snapshot_id
            result['governanceFilename'] = file_name.split('/')[-1]
            clear_history(session_id=get_session_id())
            return jsonify(result)
        else:
            error_detail = mcp_response.json().get('detail', 'Failed to load dataset')
            return jsonify({'error': error_detail}), mcp_response.status_code

    except requests.exceptions.ConnectionError as e:
        logger.error(f"Connection error loading NetApp volume file: {e}")
        return jsonify({'error': 'Could not connect to required services'}), 503
    except Exception as e:
        logger.error(f"Error loading NetApp volume file: {e}")
        logger.error(traceback.format_exc())
        return jsonify({'error': f'Error loading file from volume: {str(e)}'}), 500


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
