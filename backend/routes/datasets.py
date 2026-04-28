"""Datasets blueprint — dataset/snapshot/NetApp volume discovery + browsing.

Extracted from `backend/app.py` (REFACTOR_PLAN.md §1, step 1.5d). Owns
the four dataset-discovery / file-browser endpoints:

- `GET  /datasets`
- `GET  /snapshots/<source_type>/<source_id>`
- `GET  /snapshot/<snapshot_id>/files`
- `GET  /netapp-volume/<volume_key>/files`

Behavior is preserved verbatim: same paths, same query-param handling,
same response envelopes (`entries`, `snapshots`, `datasets`,
`current_dataset`, etc.), same status codes, same logging messages.

Note: `/dataset/load` and `/dataset/data` (singular `/dataset/*`)
intentionally stay in `backend/app.py` for now — they belong to the
`routes/data.py` blueprint per the target layout (REFACTOR_PLAN.md §1,
step 1.5e / P5).
"""
import logging
import os
import traceback

import requests
from flask import Blueprint, jsonify, request

from backend.auth import get_domino_api_host, get_passthrough_token
from backend.services.datasets import (
    SUPPORTED_EXTENSIONS,
    _list_dataset_snapshots,
    _list_netapp_snapshots,
    _parse_datasetrw_rows,
    find_data_files_fallback,
    list_dataset_files_by_id,
    list_datasets_via_api,
)
from backend.session import mcp_get

logger = logging.getLogger(__name__)

bp = Blueprint('datasets', __name__)


@bp.route('/datasets', methods=['GET'])
def list_datasets():
    """List all available data files. In extension mode (projectId or datasetId param), uses Domino API."""
    # Dataset file context mode: list files from a specific dataset by ID
    # (used when opened via "Open with..." on a file)
    dataset_id = request.args.get('datasetId')
    if dataset_id:
        snapshot_id = request.args.get('snapshotId')
        return list_dataset_files_by_id(dataset_id, snapshot_id)

    # Extension mode: list datasets from target project via API
    project_id = request.args.get('projectId')
    if project_id:
        return list_datasets_via_api(project_id)

    # Normal mode: list files from local filesystem
    try:
        # First try to get from MCP server
        response = mcp_get("/datasets/list")
        if response.status_code == 200:
            return jsonify(response.json())
    except:
        pass

    # Fallback: search for data files directly
    data_files = find_data_files_fallback()
    return jsonify({'datasets': data_files, 'current_dataset': None})


# ===== SNAPSHOT & FILE BROWSING ENDPOINTS =====

@bp.route('/snapshots/<source_type>/<path:source_id>', methods=['GET'])
def list_snapshots(source_type, source_id):
    """List snapshots for a dataset or NetApp volume.
    source_type: 'dataset' or 'netapp'
    source_id: dataset ID (hex) or volume unique name
    """
    token = get_passthrough_token()
    if not token:
        return jsonify({'error': 'Authentication required', 'snapshots': []}), 401

    if source_type == 'dataset':
        return _list_dataset_snapshots(source_id, token)
    elif source_type == 'netapp':
        return _list_netapp_snapshots(source_id, token)
    else:
        return jsonify({'error': f'Unknown source type: {source_type}', 'snapshots': []}), 400


@bp.route('/snapshot/<snapshot_id>/files', methods=['GET'])
def browse_snapshot_files(snapshot_id):
    """Browse files in a dataset snapshot. Supports nested directory navigation.
    Query param 'path' specifies subdirectory to browse (empty = root).
    Proxies to Domino's /v4/datasetrw/files/{snapshotId}?path= API.
    """
    token = get_passthrough_token()
    if not token:
        return jsonify({'error': 'Authentication required', 'entries': []}), 401

    api_host = get_domino_api_host()
    if not api_host:
        return jsonify({'error': 'Domino API host not configured', 'entries': []}), 500

    subpath = request.args.get('path', '').strip('/')

    try:
        headers = {'Authorization': f'Bearer {token}'}

        url = f'{api_host}/v4/datasetrw/files/{snapshot_id}'
        response = requests.get(url, params={'path': subpath}, headers=headers, timeout=30)

        if response.status_code in (401, 403):
            return jsonify({'error': 'Access denied', 'entries': []}), response.status_code
        if response.status_code != 200:
            logger.warning(f"Snapshot files API returned {response.status_code}: {response.text[:200]}")
            return jsonify({'error': f'Failed to browse files (HTTP {response.status_code})', 'entries': []}), 500

        data = response.json()
        entries = _parse_datasetrw_rows(data.get('rows', []), subpath)

        return jsonify({
            'entries': entries,
            'snapshotId': snapshot_id,
            'currentPath': subpath,
        })

    except requests.exceptions.ConnectionError:
        logger.error("Could not connect to Domino API for snapshot file browsing")
        return jsonify({'error': 'Could not connect to Domino API', 'entries': []}), 503
    except Exception as e:
        logger.error(f"Error browsing snapshot files: {e}")
        logger.error(traceback.format_exc())
        return jsonify({'error': f'Error browsing files: {str(e)}', 'entries': []}), 500


@bp.route('/netapp-volume/<path:volume_key>/files', methods=['GET'])
def browse_netapp_volume_files(volume_key):
    """List files in a NetApp volume with folder structure.
    The SDK returns flat file paths; we group them into a directory tree.
    Query params: 'path' for subdirectory, 'snapshotVersion' for snapshot-specific files.
    """
    token = get_passthrough_token()
    if not token:
        return jsonify({'error': 'Authentication required', 'entries': []}), 401

    subpath = request.args.get('path', '').strip('/')
    snapshot_version = request.args.get('snapshotVersion', '').strip()

    try:
        from domino_data.netapp_volumes import NetAppVolumeClient
        from domino_data.data_sources import NetAppVolumeConfig
        vol_client = NetAppVolumeClient(token=token)

        # If a snapshot version is specified, configure the volume to read from that snapshot
        if snapshot_version:
            volume = vol_client.get_volume(volume_key)
            volume.update(NetAppVolumeConfig(snapshot_version=snapshot_version))
            file_objects = volume.list_files()
            all_files = [f.key if hasattr(f, 'key') else str(f) for f in file_objects] if file_objects else []
        else:
            all_files = vol_client.list_files(volume_key) or []

        # Build entries for the requested directory level
        seen_dirs = set()
        entries = []

        prefix = f'{subpath}/' if subpath else ''

        for fpath in all_files:
            # Only process files under the requested path
            if prefix and not fpath.startswith(prefix):
                continue

            relative = fpath[len(prefix):]
            parts = relative.split('/')

            if len(parts) == 1:
                # Direct file in this directory
                fname = parts[0]
                ext = os.path.splitext(fname)[1].lower()
                if ext not in SUPPORTED_EXTENSIONS:
                    continue
                entries.append({
                    'name': fname,
                    'isDir': False,
                    'fileName': fname,
                    'size': '',
                    'path': fpath,
                })
            else:
                # Subdirectory
                dir_name = parts[0]
                if dir_name not in seen_dirs:
                    seen_dirs.add(dir_name)
                    dir_path = f'{subpath}/{dir_name}' if subpath else dir_name
                    entries.append({
                        'name': dir_name,
                        'isDir': True,
                        'fileName': dir_name,
                        'size': '',
                        'path': dir_path,
                    })

        entries.sort(key=lambda e: (0 if e['isDir'] else 1, e['name'].lower()))

        return jsonify({
            'entries': entries,
            'volumeKey': volume_key,
            'currentPath': subpath,
        })

    except Exception as e:
        logger.error(f"Error browsing NetApp volume files: {e}")
        logger.error(traceback.format_exc())
        return jsonify({'error': f'Error browsing files: {str(e)}', 'entries': []}), 500
