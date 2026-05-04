// File browser modal for the Data Explorer frontend.
//
// Owns the "Browse files…" modal UX:
//   - Source picker (datasets, NetApp volumes, or local files).
//   - Snapshot picker (per-dataset / per-volume version dropdown).
//   - Directory navigation with breadcrumb + click-to-enter folders.
//   - Debounced full-tree search (with a flat-files cache rebuilt per
//     source+snapshot pair).
//   - Selection → "Load" handoff to the dataset-loading flow.
//
// Two exports:
//   - `initFileBrowser({ performDatasetLoad })` — call once from script.js's
//     DOMContentLoaded callback. Stores the performDatasetLoad reference and
//     wires every modal-related event listener. This shape (init-with-deps)
//     is needed because performDatasetLoad lives in script.js's
//     DOMContentLoaded scope (it's the central single-source dataset loader
//     used by both auto-load and file-browser-load paths) and won't migrate
//     to a module until the dataset-loading code itself does. Per ground
//     rule #2 we don't move performDatasetLoad here just to make this
//     module's import graph cleaner.
//   - `openFileBrowserModal()` — also exported because `autoLoadDataset` in
//     script.js needs to open the browser when an autoload dataset name
//     can't be resolved (e.g. permalink for a file the user can't see).
//
// Module-private state (allFilesCache, allFilesCacheKey, fbSearchTimer)
// stays inside this file — it's all feature-local and was at the IIFE top
// scope of the pre-extraction script.js for closure reasons rather than
// because it was conceptually app-wide.
//
// `loadFileFromBrowser` calls the injected performDatasetLoad — signature
// preserved (it takes no args; the dependency is bound at init time).
//
// DOM lookup happens at module load. JS modules are deferred under the
// HTML spec, so the document is fully parsed by the time this file
// evaluates. Same pattern as `modules/governance.js`.

import { state } from '../core/state.js';
import { apiUrl, fetchJson } from '../core/api.js';
import { escapeHtml } from '../core/dom.js';
import { openModal, closeModal, attachOverlayDismiss } from '../core/modals.js';

let performDatasetLoadFn = null;
let allFilesCache = null;
let allFilesCacheKey = null;
let fbSearchTimer = null;

export function initFileBrowser({ performDatasetLoad }) {
    performDatasetLoadFn = performDatasetLoad;

    document.getElementById('browse-files-button').addEventListener('click', openFileBrowserModal);
    document.getElementById('file-browser-modal-close').addEventListener('click', closeFileBrowserModal);
    document.getElementById('file-browser-cancel-btn').addEventListener('click', closeFileBrowserModal);
    document.getElementById('file-browser-load-btn').addEventListener('click', loadFileFromBrowser);
    document.getElementById('fb-source-select').addEventListener('change', onSourceSelected);
    document.getElementById('fb-snapshot-select').addEventListener('change', onSnapshotSelected);
    document.getElementById('fb-search-input').addEventListener('input', (e) => {
        state.fileBrowserState.searchQuery = e.target.value.trim();
        clearTimeout(fbSearchTimer);
        if (state.fileBrowserState.searchQuery.length >= 2) {
            // Debounce: wait 300ms then do a backend search
            fbSearchTimer = setTimeout(() => performFileSearch(state.fileBrowserState.searchQuery), 300);
        } else if (state.fileBrowserState.searchQuery.length === 0) {
            // Cleared search — reload current directory
            state.fileBrowserState.isSearchResult = false;
            navigateToPath(state.fileBrowserState.currentPath);
        }
    });
    attachOverlayDismiss(
        document.getElementById('file-browser-modal-overlay'),
        closeFileBrowserModal
    );
}

export function openFileBrowserModal() {
    state.fileBrowserState.isOpen = true;
    state.fileBrowserState.searchQuery = '';
    document.getElementById('fb-search-input').value = '';
    openModal(document.getElementById('file-browser-modal-overlay'));

    populateSourceDropdown();

    // Restore previously selected source, or pre-select from context
    const previousSource = state.fileBrowserState.selectedSource;
    const srcSelect = document.getElementById('fb-source-select');

    if (previousSource && state.fileBrowserState.entries.length > 0) {
        // Re-opening with existing state — restore everything without re-fetching
        srcSelect.value = previousSource.id;

        // Restore snapshot dropdown without triggering onSnapshotSelected
        if (state.fileBrowserState.snapshots.length > 0 && previousSource.type !== 'local') {
            document.getElementById('fb-snapshot-group').style.display = '';
            rebuildSnapshotDropdownOnly();
        } else {
            document.getElementById('fb-snapshot-group').style.display = 'none';
        }

        // Re-render file list and breadcrumb from cached entries
        updateBreadcrumb(state.fileBrowserState.currentPath);
        renderFileList();

        // Restore selected file indicator
        if (state.fileBrowserState.selectedFile) {
            document.getElementById('file-browser-load-btn').disabled = false;
            document.getElementById('fb-selected-file').style.display = 'flex';
            const displayPath = previousSource.type === 'local'
                ? state.fileBrowserState.selectedFile.path
                : previousSource.name + '/' + state.fileBrowserState.selectedFile.path;
            document.getElementById('fb-selected-name').textContent = displayPath;
        } else {
            document.getElementById('file-browser-load-btn').disabled = true;
            document.getElementById('fb-selected-file').style.display = 'none';
        }
    } else if (previousSource) {
        // Have a source but no entries yet — trigger full load
        srcSelect.value = previousSource.id;
        onSourceSelected();
    } else if (state.fileBrowserState.mode === 'extension-dataset' && state.extensionDatasetId) {
        const match = state.fileBrowserState.sources.find(s => s.id === state.extensionDatasetId);
        if (match) {
            srcSelect.value = match.id;
            onSourceSelected();
        }
    } else if (state.extensionNetAppVolumeId) {
        // NetApp deeplink: preselect the source matching the URL's
        // netAppVolumeId. resolveNetAppDeeplink in script.js has
        // already added the volume to the source list when needed.
        const match = state.fileBrowserState.sources.find(
            s => s.type === 'netapp' && s.volumeId === state.extensionNetAppVolumeId
        );
        if (match) {
            srcSelect.value = match.id;
            onSourceSelected();
        }
    }
}

function closeFileBrowserModal() {
    state.fileBrowserState.isOpen = false;
    closeModal(document.getElementById('file-browser-modal-overlay'));
}

function populateSourceDropdown() {
    const select = document.getElementById('fb-source-select');
    select.innerHTML = '';

    const placeholder = document.createElement('option');
    placeholder.value = '';
    placeholder.textContent = '-- Select data --';
    select.appendChild(placeholder);

    if (state.fileBrowserState.mode === 'local') {
        // Local mode: single "Local Files" entry
        const opt = document.createElement('option');
        opt.value = '__local__';
        opt.textContent = 'Local Files';
        select.appendChild(opt);
        // If only one source, auto-select
        if (state.fileBrowserState.sources.length === 1) {
            select.value = '__local__';
            onSourceSelected();
        }
    } else {
        // Extension mode: optgroups for Datasets and NetApp Volumes
        const datasets = state.fileBrowserState.sources.filter(s => s.type === 'dataset');
        const netapps = state.fileBrowserState.sources.filter(s => s.type === 'netapp');

        if (datasets.length > 0) {
            const group = document.createElement('optgroup');
            group.label = 'Datasets';
            datasets.forEach(ds => {
                const opt = document.createElement('option');
                opt.value = ds.id;
                opt.textContent = ds.name;
                opt.dataset.sourceType = 'dataset';
                group.appendChild(opt);
            });
            select.appendChild(group);
        }

        if (netapps.length > 0) {
            const group = document.createElement('optgroup');
            group.label = 'NetApp Volumes';
            netapps.forEach(vol => {
                const opt = document.createElement('option');
                opt.value = vol.id;
                opt.textContent = vol.name;
                opt.dataset.sourceType = 'netapp';
                group.appendChild(opt);
            });
            select.appendChild(group);
        }

        // Auto-select if only one source
        const allSources = [...datasets, ...netapps];
        if (allSources.length === 1) {
            select.value = allSources[0].id;
            onSourceSelected();
        }
    }
}

async function onSourceSelected() {
    const select = document.getElementById('fb-source-select');
    const sourceId = select.value;
    if (!sourceId) return;

    const source = state.fileBrowserState.sources.find(s => s.id === sourceId);
    if (!source) return;

    state.fileBrowserState.selectedSource = source;
    state.fileBrowserState.selectedFile = null;
    state.fileBrowserState.snapshots = [];
    state.fileBrowserState.selectedSnapshot = null;
    allFilesCache = null; // Invalidate search cache on source change
    state.fileBrowserState.activeSnapshotId = null;
    state.fileBrowserState.currentPath = '';
    state.fileBrowserState.entries = [];
    document.getElementById('file-browser-load-btn').disabled = true;
    document.getElementById('fb-selected-file').style.display = 'none';

    if (source.type === 'local') {
        // Local mode: hide snapshot selector, show files directly
        document.getElementById('fb-snapshot-group').style.display = 'none';
        loadLocalFiles();
    } else {
        // Extension mode: fetch snapshots
        document.getElementById('fb-snapshot-group').style.display = '';
        const snapshotSelect = document.getElementById('fb-snapshot-select');
        snapshotSelect.innerHTML = '<option value="">Loading snapshots...</option>';

        try {
            const sourceType = source.type; // 'dataset' or 'netapp'
            const data = await fetchJson(apiUrl(`snapshots/${sourceType}/${encodeURIComponent(source.id)}`));

            state.fileBrowserState.snapshots = data.snapshots || [];

            // For datasets, find the active (readWrite) snapshot
            if (sourceType === 'dataset') {
                const rwSnap = state.fileBrowserState.snapshots.find(s => s.isReadWrite);
                state.fileBrowserState.activeSnapshotId = rwSnap ? rwSnap.id : null;
            }

            populateSnapshotDropdown();
        } catch (error) {
            console.error('Error fetching snapshots:', error);
            snapshotSelect.innerHTML = '<option value="">Failed to load snapshots</option>';
            // Fall back: try loading files without snapshot info
            if (source.type === 'netapp') {
                loadNetAppFilesForBrowser(source.volumeKey || source.id);
            }
        }
    }
}

function rebuildSnapshotDropdownOnly() {
    // Rebuild the snapshot dropdown HTML and restore the selected value,
    // but do NOT trigger onSnapshotSelected (avoids clearing selectedFile & re-fetching files)
    const select = document.getElementById('fb-snapshot-select');
    select.innerHTML = '';

    state.fileBrowserState.snapshots.forEach(snap => {
        const option = document.createElement('option');
        option.value = snap.id;
        if (snap.isLatest) {
            option.textContent = 'Latest (current data)';
        } else {
            const ts = snap.creationTime || snap.createdAt;
            const dateStr = ts ? new Date(typeof ts === 'number' ? ts : Date.parse(ts)).toLocaleDateString() : '';
            const currentLabel = snap.id === state.fileBrowserState.activeSnapshotId ? ' (Current)' : '';
            const rwLabel = snap.isReadWrite ? ' (Current)' : '';
            const label = currentLabel || rwLabel;
            option.textContent = `Version ${snap.version}${label}${dateStr ? ' \u2014 ' + dateStr : ''}`;
            if (snap.description) option.title = snap.description;
        }
        select.appendChild(option);
    });

    // Restore the previously selected snapshot
    if (state.fileBrowserState.selectedSnapshot) {
        select.value = state.fileBrowserState.selectedSnapshot.id;
    }
}

function populateSnapshotDropdown() {
    const select = document.getElementById('fb-snapshot-select');
    select.innerHTML = '';

    if (state.fileBrowserState.snapshots.length === 0) {
        select.innerHTML = '<option value="">No snapshots available</option>';
        // For NetApp, still try loading files from current volume
        if (state.fileBrowserState.selectedSource && state.fileBrowserState.selectedSource.type === 'netapp') {
            loadNetAppFilesForBrowser(state.fileBrowserState.selectedSource.volumeKey || state.fileBrowserState.selectedSource.id);
        }
        return;
    }

    state.fileBrowserState.snapshots.forEach(snap => {
        const option = document.createElement('option');
        option.value = snap.id;

        if (snap.isLatest) {
            // Synthetic "latest" entry for current NetApp volume state
            option.textContent = 'Latest (current data)';
        } else {
            const ts = snap.creationTime || snap.createdAt;
            const dateStr = ts ? new Date(typeof ts === 'number' ? ts : Date.parse(ts)).toLocaleDateString() : '';
            const currentLabel = snap.id === state.fileBrowserState.activeSnapshotId ? ' (Current)' : '';
            const rwLabel = snap.isReadWrite ? ' (Current)' : '';
            const label = currentLabel || rwLabel;
            option.textContent = `Version ${snap.version}${label}${dateStr ? ' \u2014 ' + dateStr : ''}`;
            if (snap.description) option.title = snap.description;
        }
        select.appendChild(option);
    });

    // Pre-select: state.extensionSnapshotId if it matches, else active, else first
    const preselect = state.extensionSnapshotId
        && state.fileBrowserState.snapshots.find(s => s.id === state.extensionSnapshotId)
        ? state.extensionSnapshotId
        : (state.fileBrowserState.activeSnapshotId || (state.fileBrowserState.snapshots[0] && state.fileBrowserState.snapshots[0].id));

    if (preselect) {
        select.value = preselect;
    }

    onSnapshotSelected();
}

async function onSnapshotSelected() {
    const select = document.getElementById('fb-snapshot-select');
    const snapshotId = select.value;
    if (!snapshotId) return;

    state.fileBrowserState.selectedSnapshot = state.fileBrowserState.snapshots.find(s => s.id === snapshotId);
    state.fileBrowserState.currentPath = '';
    state.fileBrowserState.selectedFile = null;
    document.getElementById('file-browser-load-btn').disabled = true;
    document.getElementById('fb-selected-file').style.display = 'none';

    const source = state.fileBrowserState.selectedSource;
    if (source && source.type === 'dataset') {
        await loadSnapshotFiles(snapshotId, '');
    } else if (source && source.type === 'netapp') {
        await loadNetAppFilesForBrowser(source.volumeKey || source.id);
    }
}

async function loadSnapshotFiles(snapshotId, path) {
    const fileList = document.getElementById('fb-file-list');
    fileList.innerHTML = '<div class="fb-loading"><div class="spinner-small"></div> Loading files...</div>';
    state.fileBrowserState.loading = true;

    try {
        let url = apiUrl(`snapshot/${encodeURIComponent(snapshotId)}/files`);
        if (path) url += '?path=' + encodeURIComponent(path);

        const data = await fetchJson(url);

        if (data.error) {
            fileList.innerHTML = `<div class="fb-error">${data.error}</div>`;
            return;
        }

        state.fileBrowserState.entries = data.entries || [];
        state.fileBrowserState.currentPath = path;
        updateBreadcrumb(path);
        renderFileList();
    } catch (error) {
        console.error('Error loading snapshot files:', error);
        fileList.innerHTML = '<div class="fb-error">Failed to load files</div>';
    } finally {
        state.fileBrowserState.loading = false;
    }
}

async function loadNetAppFilesForBrowser(volumeKey) {
    const fileList = document.getElementById('fb-file-list');
    fileList.innerHTML = '<div class="fb-loading"><div class="spinner-small"></div> Loading files...</div>';
    state.fileBrowserState.loading = true;

    try {
        const path = state.fileBrowserState.currentPath;
        const params = new URLSearchParams();
        if (path) params.set('path', path);
        // Pass snapshot version to get snapshot-specific files
        const snap = state.fileBrowserState.selectedSnapshot;
        if (snap && !snap.isLatest && snap.version !== undefined) {
            params.set('snapshotVersion', String(snap.version));
        }
        let url = apiUrl(`netapp-volume/${encodeURIComponent(volumeKey)}/files`);
        const qs = params.toString();
        if (qs) url += '?' + qs;

        const data = await fetchJson(url);

        if (data.error) {
            fileList.innerHTML = `<div class="fb-error">${data.error}</div>`;
            return;
        }

        state.fileBrowserState.entries = data.entries || [];
        state.fileBrowserState.currentPath = path;
        updateBreadcrumb(path);
        renderFileList();
    } catch (error) {
        console.error('Error loading NetApp files:', error);
        fileList.innerHTML = '<div class="fb-error">Failed to load files</div>';
    } finally {
        state.fileBrowserState.loading = false;
    }
}

function loadLocalFiles() {
    const data = state.cachedDatasetListResponse;
    if (!data) return;

    const datasets = data.datasets || [];
    // Build entries from flat file paths with folder grouping
    const seen = new Set();
    const entries = [];
    const prefix = state.fileBrowserState.currentPath ? state.fileBrowserState.currentPath + '/' : '';

    datasets.forEach(fpath => {
        if (prefix && !fpath.startsWith(prefix)) return;
        const relative = fpath.substring(prefix.length);
        const parts = relative.split('/');
        if (parts.length === 1) {
            entries.push({ name: parts[0], isDir: false, fileName: parts[0], size: '', path: fpath });
        } else {
            const dir = parts[0];
            if (!seen.has(dir)) {
                seen.add(dir);
                entries.push({ name: dir, isDir: true, fileName: dir, size: '', path: prefix + dir });
            }
        }
    });

    entries.sort((a, b) => (a.isDir === b.isDir ? a.name.localeCompare(b.name) : a.isDir ? -1 : 1));
    state.fileBrowserState.entries = entries;
    updateBreadcrumb(state.fileBrowserState.currentPath);
    renderFileList();
}

function updateBreadcrumb(path) {
    const bc = document.getElementById('fb-breadcrumb');
    bc.innerHTML = '';

    const root = document.createElement('span');
    root.className = 'fb-breadcrumb-item';
    root.textContent = 'Root';
    root.addEventListener('click', () => navigateToPath(''));
    bc.appendChild(root);

    if (path) {
        const parts = path.split('/');
        let accumulated = '';
        parts.forEach((part, i) => {
            const sep = document.createElement('span');
            sep.className = 'fb-breadcrumb-sep';
            sep.textContent = '/';
            bc.appendChild(sep);

            accumulated += (accumulated ? '/' : '') + part;
            const span = document.createElement('span');
            span.className = 'fb-breadcrumb-item';
            span.textContent = part;
            if (i < parts.length - 1) {
                const navPath = accumulated;
                span.addEventListener('click', () => navigateToPath(navPath));
            } else {
                span.classList.add('fb-breadcrumb-current');
            }
            bc.appendChild(span);
        });
    }
}

function navigateToPath(path) {
    state.fileBrowserState.currentPath = path;
    state.fileBrowserState.selectedFile = null;
    state.fileBrowserState.isSearchResult = false;
    document.getElementById('file-browser-load-btn').disabled = true;
    document.getElementById('fb-selected-file').style.display = 'none';

    const source = state.fileBrowserState.selectedSource;
    if (!source) return;

    if (source.type === 'local') {
        loadLocalFiles();
    } else if (source.type === 'dataset' && state.fileBrowserState.selectedSnapshot) {
        loadSnapshotFiles(state.fileBrowserState.selectedSnapshot.id, path);
    } else if (source.type === 'netapp') {
        loadNetAppFilesForBrowser(source.volumeKey || source.id);
    }
}

function formatFileSize(bytes) {
    if (!bytes || bytes === '' || isNaN(bytes)) return '';
    const num = Number(bytes);
    if (num < 1024) return num + ' B';
    if (num < 1024 * 1024) return (num / 1024).toFixed(1) + ' KB';
    if (num < 1024 * 1024 * 1024) return (num / (1024 * 1024)).toFixed(1) + ' MB';
    return (num / (1024 * 1024 * 1024)).toFixed(1) + ' GB';
}

async function performFileSearch(query) {
    const source = state.fileBrowserState.selectedSource;
    if (!source) return;

    const fileList = document.getElementById('fb-file-list');
    const q = query.toLowerCase();

    // Local mode: just filter the cached dataset list
    if (source.type === 'local') {
        const data = state.cachedDatasetListResponse;
        state.fileBrowserState.entries = (data && data.datasets || [])
            .filter(f => f.split('/').pop().toLowerCase().includes(q))
            .map(f => ({ name: f.split('/').pop(), isDir: false, fileName: f.split('/').pop(), size: '', path: f }));
        state.fileBrowserState.isSearchResult = true;
        renderFileList();
        return;
    }

    // Build a cache key so we know when to re-fetch
    const snapId = state.fileBrowserState.selectedSnapshot ? state.fileBrowserState.selectedSnapshot.id : '';
    const cacheKey = `${source.id}:${snapId}`;

    // If cache is fresh, filter it immediately
    if (allFilesCache && allFilesCacheKey === cacheKey) {
        state.fileBrowserState.entries = allFilesCache.filter(e => e.name.toLowerCase().includes(q));
        state.fileBrowserState.isSearchResult = true;
        renderFileList();
        return;
    }

    // Need to build the cache: walk all directories
    fileList.innerHTML = '<div class="fb-loading"><div class="spinner-small"></div> Indexing files...</div>';

    try {
        const allFiles = [];
        await _walkDirectory('', allFiles);
        allFilesCache = allFiles;
        allFilesCacheKey = cacheKey;

        state.fileBrowserState.entries = allFiles.filter(e => e.name.toLowerCase().includes(q));
        state.fileBrowserState.isSearchResult = true;
        renderFileList();
    } catch (error) {
        console.error('Search indexing error:', error);
        fileList.innerHTML = '<div class="fb-error">Search failed</div>';
    }
}

async function _walkDirectory(path, results) {
    const source = state.fileBrowserState.selectedSource;
    const snap = state.fileBrowserState.selectedSnapshot;
    let url;
    if (source.type === 'dataset' && snap) {
        url = apiUrl(`snapshot/${encodeURIComponent(snap.id)}/files`);
        if (path) url += '?path=' + encodeURIComponent(path);
    } else if (source.type === 'netapp') {
        const params = new URLSearchParams();
        if (path) params.set('path', path);
        if (snap && !snap.isLatest && snap.version !== undefined) {
            params.set('snapshotVersion', String(snap.version));
        }
        const qs = params.toString();
        url = apiUrl(`netapp-volume/${encodeURIComponent(source.volumeKey || source.id)}/files`) + (qs ? '?' + qs : '');
    } else {
        return;
    }

    const data = await fetchJson(url);
    const entries = data.entries || [];

    const subdirs = [];
    for (const entry of entries) {
        if (entry.isDir) {
            subdirs.push(entry.path);
        } else {
            results.push(entry);
        }
    }

    // Recurse into subdirectories (parallel)
    await Promise.all(subdirs.map(dir => _walkDirectory(dir, results)));
}

function renderFileList() {
    const fileList = document.getElementById('fb-file-list');
    const entries = state.fileBrowserState.entries;
    const isSearch = state.fileBrowserState.isSearchResult;

    if (entries.length === 0) {
        fileList.innerHTML = '<div class="fb-empty-state">No files found</div>';
        return;
    }

    fileList.innerHTML = '';

    // Add ".." entry if not at root and not in search mode
    if (state.fileBrowserState.currentPath && !isSearch) {
        const upRow = document.createElement('div');
        upRow.className = 'fb-file-row fb-dir';
        upRow.innerHTML = '<span class="fb-file-icon">\u2B11</span><span class="fb-file-name">..</span><span class="fb-file-size"></span>';
        upRow.addEventListener('click', () => {
            const parts = state.fileBrowserState.currentPath.split('/');
            parts.pop();
            navigateToPath(parts.join('/'));
        });
        fileList.appendChild(upRow);
    }

    entries.forEach(entry => {
        const row = document.createElement('div');
        row.className = 'fb-file-row';

        if (entry.isDir) {
            row.classList.add('fb-dir');
            row.setAttribute('data-testid', 'fb-dir-item');
            row.innerHTML = `<span class="fb-file-icon">\uD83D\uDCC1</span><span class="fb-file-name">${escapeHtml(entry.name)}</span><span class="fb-file-size"></span>`;
            row.addEventListener('click', () => navigateToPath(entry.path));
        } else {
            // In search results, show the folder path so user knows where the file is
            let nameHtml = escapeHtml(entry.name);
            if (isSearch && entry.path && entry.path.includes('/')) {
                const folder = entry.path.substring(0, entry.path.lastIndexOf('/'));
                nameHtml = `${escapeHtml(entry.name)}<span class="fb-file-folder">${escapeHtml(folder)}</span>`;
            }
            row.setAttribute('data-testid', 'fb-file-item');
            row.setAttribute('data-fb-name', entry.name);
            row.innerHTML = `<span class="fb-file-icon">\uD83D\uDCC4</span><span class="fb-file-name">${nameHtml}</span><span class="fb-file-size">${formatFileSize(entry.size)}</span>`;
            row.addEventListener('click', () => selectFile(entry));

            if (state.fileBrowserState.selectedFile && state.fileBrowserState.selectedFile.path === entry.path) {
                row.classList.add('selected');
            }
        }

        fileList.appendChild(row);
    });
}

function selectFile(entry) {
    state.fileBrowserState.selectedFile = entry;
    document.getElementById('file-browser-load-btn').disabled = false;
    document.getElementById('fb-selected-file').style.display = 'flex';

    // Build display path
    const source = state.fileBrowserState.selectedSource;
    const displayPath = source ? source.name + '/' + entry.path : entry.path;
    document.getElementById('fb-selected-name').textContent = displayPath;

    // Re-render to update selection highlight
    renderFileList();
}

function loadFileFromBrowser() {
    const file = state.fileBrowserState.selectedFile;
    const source = state.fileBrowserState.selectedSource;
    if (!file || !source) return;

    closeFileBrowserModal();

    // Build display name and load body
    let displayName;
    const loadBody = {};

    if (source.type === 'local') {
        displayName = file.path;
        loadBody.dataset = displayName;
    } else if (source.type === 'netapp') {
        displayName = source.name + '/' + file.fileName;
        // For nested paths, use the full path after the volume name
        const pathParts = file.path.split('/');
        const fileNameInVol = pathParts.length > 0 ? file.path : file.fileName;
        displayName = source.name + '/' + fileNameInVol;
        loadBody.dataset = displayName;
        loadBody.sourceType = 'netapp';
        loadBody.volumeKey = source.volumeKey || source.id;
        if (source.volumeId) loadBody.volumeId = source.volumeId;
        const snap = state.fileBrowserState.selectedSnapshot;
        if (snap && !snap.isLatest) {
            // snapshotVersion (int) pins the SDK read; snapshotId (UUID) is
            // what governance attachments are keyed on — we need both.
            if (snap.version !== undefined && snap.version !== null) {
                loadBody.snapshotVersion = snap.version;
            }
            if (snap.id) {
                loadBody.snapshotId = snap.id;
            }
        }
    } else {
        // Dataset source
        displayName = source.name + '/' + file.path;
        loadBody.dataset = displayName;
        loadBody.datasetId = source.id;

        const snap = state.fileBrowserState.selectedSnapshot;
        if (snap && snap.id !== state.fileBrowserState.activeSnapshotId) {
            // Non-active snapshot: use snapshot-specific download
            loadBody.snapshotId = snap.id;
            // Update state.extensionSnapshotId so permalink reflects this
            state.extensionSnapshotId = snap.id;
        }
    }

    performDatasetLoadFn(displayName, loadBody);
}
