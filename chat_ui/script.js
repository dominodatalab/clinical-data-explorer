import { state } from './core/state.js';
import { apiUrl } from './core/api.js';
import { loadColumnLabels } from './modules/column-labels.js';
import { checkGovernanceBundles, createFinding } from './modules/governance.js';
import { initFileBrowser, openFileBrowserModal } from './modules/file-browser.js';
import { initFilters, renderActiveFilters } from './modules/filters.js';
import { initExploreCharts, initializeExploreTab, resetExploreCharts } from './modules/explore-charts.js';
import { initChat, displayMessage, checkChatStatus } from './modules/chat.js';
import {
    tableState,
    initTableView,
    loadTableData,
    loadSummaryData,
    clearSelectedRow,
    invalidateSummaryStats,
    initializeTableView,
    generatePermalink,
    renderTable,
    populateDistinctColumnSelector,
    updateMissingValuesCard,
    updateDistinctValuesCard,
    renderRowDetailsTab,
    resortSummaryStatsForLabels,
} from './modules/table-view.js';

document.addEventListener('DOMContentLoaded', () => {
    // Chat tab DOM lookups, send/clear/keypress wiring, status probe,
    // displayMessage, thinking animation, renderChart and the 8
    // type-specific renderers all live in modules/chat.js
    // (plan box 4.4f / P14). We import initChat/displayMessage/
    // checkChatStatus at the top of this file and call initChat()
    // below — see the early-call to initChat() right after this block.

    // Snapshot/source identity of the most recently loaded file.
    // generatePermalink() uses this to encode the exact NetApp/dataset snapshot
    // a permalink should resolve back to — the file browser listing alone only
    // reflects the *latest* snapshot.

    // Extension mode: when accessed with ?projectId=X, use Domino API for dataset access

    // Dataset file context mode: when opened via "Open with..." on a specific file
    // URL params: ?mountPointType=datasetFileContext&datasetId=X&datasetSnapshotId=Y&filePath=Z

    // ===== FILE BROWSER STATE =====
    
    // Column metadata from /dataset/load - used to initialize UI without fetching all data

    // ===== UI STATE =====
    
    // Chat configuration status
    
    
    // Loading state for individual components

    // Column label mapping state
    // Initialize state.useLabels from checkbox state (defaults to false/unchecked)

    // DOM elements for label toggle
    const useLabelsCheckbox = document.getElementById('use-labels-checkbox');

    // ===== GOVERNANCE STATE =====
    // (DOM lookups, functions, and event listeners moved to modules/governance.js
    // in plan §4 / step 4.4b. The finding-submit-btn click handler stays in this
    // file because it bridges form input → generatePermalink (now imported from
    // modules/table-view.js) → governance.createFinding.)

    // Finding submit handler: bridges form input → permalink → createFinding
    // (which lives in modules/governance.js). All other governance event
    // wiring lives in modules/governance.js.
    document.getElementById('finding-submit-btn').addEventListener('click', async () => {
        const findingApprovalSelect = document.getElementById('finding-approval');
        const name = document.getElementById('finding-name').value.trim();
        const severity = document.getElementById('finding-severity').value;
        const approverValue = document.getElementById('finding-approver').value;
        const assigneeValue = document.getElementById('finding-assignee').value;
        const approvalId = findingApprovalSelect.value;
        const description = document.getElementById('finding-description').value.trim();
        const dueDate = document.getElementById('finding-due-date').value;

        // Validation
        if (!name) {
            alert('Please enter a finding name');
            return;
        }
        if (!severity) {
            alert('Please select a severity');
            return;
        }
        if (!approverValue) {
            alert('Please select an approver');
            return;
        }
        if (!assigneeValue) {
            alert('Please select an assignee');
            return;
        }
        // Note: approvalId/evidence is optional per user feedback

        // Parse approver and assignee from JSON values
        let approver, assignee;
        try {
            approver = JSON.parse(approverValue);
            assignee = JSON.parse(assigneeValue);
        } catch (e) {
            alert('Invalid approver or assignee selection');
            return;
        }

        // Get evidence ID from selected approval
        const selectedOption = findingApprovalSelect.options[findingApprovalSelect.selectedIndex];
        const evidenceId = selectedOption.dataset.evidenceId;

        await createFinding({
            name,
            severity,
            approver,
            assignee,
            approvalId,
            evidenceId,
            description,
            dueDate
        }, generatePermalink());
    });


    // Initialize state.useLabels from checkbox state on page load
    if (useLabelsCheckbox) {
        state.useLabels = useLabelsCheckbox.checked;
        
        // Handle toggle checkbox change. The cross-module call sequence below
        // is preserved from pre-extraction (table render → explore re-init →
        // filter chips → table-side card refreshes → stats resort) so the
        // observable refresh order matches exactly. summaryStatsState lives
        // inside modules/table-view.js now; resortSummaryStatsForLabels()
        // wraps the previously-inline 4 lines that touched it.
        useLabelsCheckbox.addEventListener('change', () => {
            state.useLabels = useLabelsCheckbox.checked;
            if (state.columnMetadata) {
                if (tableState.lastData) {
                    renderTable(tableState.lastData);
                }
                initializeExploreTab();
                renderActiveFilters();
                populateDistinctColumnSelector(tableState.columns);
                updateMissingValuesCard();
                updateDistinctValuesCard();
                renderRowDetailsTab();
                resortSummaryStatsForLabels();
            }
        });
    }

    // Initialize chat module (DOM refs, send/clear listeners, empty
    // state tabs, welcome message). Must run before any code path that
    // calls displayMessage(), but displayMessage() also lazy-resolves
    // chatBox so the early-error edge cases stay safe.
    initChat();

    // Initialize the table view module (DOM refs, row-click delegation,
    // pagination/sidebar/right-panel/summary-cards/stats event wiring).
    // Calls parsePermalinkFromUrl() internally — must run BEFORE
    // loadDatasets() below so that tableState.pendingDataset and
    // tableState.pendingLoadContext are populated by the time
    // loadDatasets's async .then callback inspects them. Both this and
    // loadDatasets execute inside the same synchronous DOMContentLoaded
    // tick, so any .then callback always sees the post-init tableState.
    initTableView();

    // Wire up the filters module. Must run after initTableView() (so the
    // tableState reference identity is stable for both modules) and is
    // injected with the same tableState + reload primitives so chip apply
    // / clear / expression-filter actions trigger the table-view reloads.
    initFilters({ tableState, loadTableData, loadSummaryData });

    // Tab switching
    const tabButtons = document.querySelectorAll('.tab-button');
    const tabContents = document.querySelectorAll('.tab-content');

    tabButtons.forEach(button => {
        button.addEventListener('click', () => {
            const tabName = button.getAttribute('data-tab');
            
            // Update active states
            tabButtons.forEach(btn => btn.classList.remove('active'));
            tabContents.forEach(content => content.classList.remove('active'));
            
            button.classList.add('active');
            document.getElementById(`${tabName}-tab`).classList.add('active');

            // If switching to table tab with column metadata, ensure it's initialized
            if (tabName === 'table' && state.currentDataset && state.columnMetadata && typeof initializeTableView === 'function') {
                if (typeof tableState !== 'undefined' && (!tableState.lastData || tableState.lastData.length === 0)) {
                    initializeTableView();
                }
            }
            
            // If switching to chat tab, check if chat is configured
            if (tabName === 'chat') {
                checkChatStatus();
            }
        });
    });

    // Load available datasets and column labels on page load
    loadDatasets();
    loadColumnLabels();

    // Wire up the file browser modal. We pass performDatasetLoad in because
    // it's the central single-source dataset-load fn that lives in this
    // DOMContentLoaded scope (function-declaration hoisting makes it visible
    // here even though it's defined further down). It will move out when
    // dataset loading itself is extracted in a later refactor pass.
    initFileBrowser({ performDatasetLoad });

    function loadDatasets() {
        let datasetsUrl;
        if (state.extensionDatasetId) {
            datasetsUrl = apiUrl('datasets') + '?datasetId=' + encodeURIComponent(state.extensionDatasetId);
            if (state.extensionSnapshotId) {
                datasetsUrl += '&snapshotId=' + encodeURIComponent(state.extensionSnapshotId);
            }
        } else if (state.extensionProjectId) {
            datasetsUrl = apiUrl('datasets') + '?projectId=' + encodeURIComponent(state.extensionProjectId);
        } else {
            datasetsUrl = apiUrl('datasets');
        }

        fetch(datasetsUrl)
            .then(response => {
                if (response.status === 401 || response.status === 403) {
                    return response.json().then(data => {
                        throw { authError: true, message: data.error || 'Authentication required' };
                    });
                }
                return response.json();
            })
            .then(data => {
                state.cachedDatasetListResponse = data;

                // Determine mode
                if (state.extensionDatasetId) {
                    state.fileBrowserState.mode = 'extension-dataset';
                } else if (state.extensionProjectId) {
                    state.fileBrowserState.mode = 'extension-project';
                } else {
                    state.fileBrowserState.mode = 'local';
                }

                // Build sources list from response
                buildSourcesList(data);

                const allValues = [
                    ...(data.datasets || []),
                    ...(data.netapp_files || []).map(nf => nf.display_name)
                ];

                // Auto-load for file context mode (state.extensionFilePath)
                if (state.extensionFilePath && !tableState.pendingDataset) {
                    const match = allValues.find(d => d.endsWith('/' + state.extensionFilePath));
                    if (match) {
                        tableState.pendingDataset = match;
                    }
                }

                // Auto-load pending dataset from URL permalink or file context.
                // When a snapshot-specific load context is present we bypass the
                // allValues gate — the file may only exist in an older snapshot.
                const hasSnapshotContext = !!tableState.pendingLoadContext;
                if (tableState.pendingDataset && (allValues.includes(tableState.pendingDataset) || hasSnapshotContext)) {
                    state.selectedDataset = tableState.pendingDataset;
                    const pendingDs = tableState.pendingDataset;
                    tableState.pendingDataset = null;
                    setTimeout(() => {
                        autoLoadDataset(pendingDs, data);
                    }, 100);
                } else if (allValues.length === 0) {
                    const emptyMessage = document.getElementById('table-empty-message');
                    const tableEmptyState = document.getElementById('table-empty-state');
                    if (emptyMessage) {
                        emptyMessage.textContent = state.extensionDatasetId
                            ? 'No supported data files found in this dataset. Supported formats: CSV, Parquet, SAS.'
                            : state.extensionProjectId
                                ? 'No supported data files found in this project. Supported formats: CSV, Parquet, SAS.'
                                : 'No datasets available. Add CSV files to the datasets folder to get started.';
                    }
                    if (tableEmptyState) tableEmptyState.classList.remove('hidden');
                } else if (state.extensionProjectId && !state.extensionFilePath) {
                    // Project mode without specific file: auto-open the file browser
                    setTimeout(() => openFileBrowserModal(), 200);
                }
            })
            .catch(error => {
                console.error('Error loading datasets:', error);
                if (error && error.authError) {
                    const emptyMessage = document.getElementById('table-empty-message');
                    const tableEmptyState = document.getElementById('table-empty-state');
                    if (emptyMessage) emptyMessage.textContent = error.message;
                    if (tableEmptyState) tableEmptyState.classList.remove('hidden');
                } else {
                    displayMessage('Error loading datasets. Make sure the server is running.', 'system');
                }
            });
    }

    function buildSourcesList(data) {
        const sources = [];
        const datasetInfo = data.dataset_info || [];
        const netappFiles = data.netapp_files || [];

        if (state.fileBrowserState.mode === 'local') {
            // Local mode: treat each file path as a source entry
            // Group by top-level folder
            const datasets = data.datasets || [];
            if (datasets.length > 0) {
                sources.push({ id: '__local__', name: 'Local Files', type: 'local' });
            }
        } else {
            // Extension mode: build from dataset_info and netapp_files
            const seenDatasets = new Set();
            for (const ds of datasetInfo) {
                if (!seenDatasets.has(ds.id)) {
                    seenDatasets.add(ds.id);
                    sources.push({ id: ds.id, name: ds.name, type: 'dataset' });
                }
            }

            // Build unique volumes from netapp_files
            const seenVolumes = new Set();
            for (const nf of netappFiles) {
                if (!seenVolumes.has(nf.volume_key)) {
                    seenVolumes.add(nf.volume_key);
                    sources.push({
                        id: nf.volume_key,
                        name: nf.volume_name || nf.display_name.split('/')[0],
                        type: 'netapp',
                        volumeKey: nf.volume_key,
                        volumeId: nf.volume_id || '',
                    });
                }
            }
        }

        state.fileBrowserState.sources = sources;
    }

    function autoLoadDataset(datasetName, data) {
        // Honor a snapshot-specific load context embedded by a permalink — the
        // file may not appear in `data.netapp_files` (latest snapshot only).
        const pctx = tableState.pendingLoadContext;
        tableState.pendingLoadContext = null;
        if (pctx && pctx.sourceType === 'netapp' && pctx.volumeKey) {
            const loadBody = {
                dataset: datasetName,
                sourceType: 'netapp',
                volumeKey: pctx.volumeKey,
            };
            if (pctx.volumeId) loadBody.volumeId = pctx.volumeId;
            if (pctx.snapshotId) loadBody.snapshotId = pctx.snapshotId;
            if (pctx.snapshotVersion != null) loadBody.snapshotVersion = pctx.snapshotVersion;
            return performDatasetLoad(datasetName, loadBody);
        }
        if (pctx && pctx.sourceType === 'dataset' && pctx.datasetId) {
            const loadBody = { dataset: datasetName, datasetId: pctx.datasetId };
            if (pctx.snapshotId) loadBody.snapshotId = pctx.snapshotId;
            return performDatasetLoad(datasetName, loadBody);
        }

        // Determine source info for the auto-loaded dataset
        const netappFiles = data.netapp_files || [];
        const netappMatch = netappFiles.find(nf => nf.display_name === datasetName);

        const loadBody = { dataset: datasetName };
        if (netappMatch) {
            loadBody.sourceType = 'netapp';
            loadBody.volumeKey = netappMatch.volume_key;
        } else if (state.extensionDatasetId) {
            loadBody.datasetId = state.extensionDatasetId;
            if (state.extensionSnapshotId) {
                loadBody.snapshotId = state.extensionSnapshotId;
            }
        } else if (state.extensionProjectId) {
            loadBody.projectId = state.extensionProjectId;
        }

        performDatasetLoad(datasetName, loadBody);
    }

    function performDatasetLoad(datasetName, loadBody) {
        const isDatasetSwitch = state.currentDataset && state.currentDataset !== datasetName;

        showLoadingBanner(`Loading ${datasetName}...`);
        const browseBtn = document.getElementById('browse-files-button');
        browseBtn.disabled = true;

        fetch(apiUrl('dataset/load'), {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(loadBody),
        })
        .then(response => response.json().then(data => ({ status: response.status, data })))
        .then(({ status, data }) => {
            if (data.error) {
                const prefix = (status === 401 || status === 403) ? 'Access denied: ' : 'Error loading dataset: ';
                displayMessage(`${prefix}${data.error}`, 'system');
            } else {
                state.currentDataset = datasetName;
                state.selectedDataset = datasetName;
                state.currentFilter = null;
                clearSelectedRow();
                invalidateSummaryStats();

                // Update the current dataset label
                document.getElementById('current-dataset-label').textContent = datasetName;

                state.columnMetadata = {
                    columns: data.columns || [],
                    numeric_columns: data.numeric_columns || [],
                    categorical_columns: data.categorical_columns || [],
                    date_columns: data.date_columns || [],
                    column_types: data.column_types || {},
                    num_rows: data.num_rows || 0
                };

                console.log('Column metadata loaded:', state.columnMetadata);

                if (typeof tableState !== 'undefined') {
                    if (isDatasetSwitch) {
                        tableState.filters = [];
                        tableState.expressionFilter = null;
                        tableState.pinnedColumns = [];
                        tableState.currentPage = 1;
                        tableState.sortColumn = null;
                        tableState.sortDirection = 'asc';
                    }
                }

                resetExploreCharts();

                displayMessage(`Successfully loaded dataset: ${datasetName} (${state.columnMetadata.num_rows.toLocaleString()} rows). You can now ask questions about this data!`, 'system');

                initializeExploreTab();
                initializeTableView();

                // Build governance context from backend response + loadBody.
                // The backend echoes sourceType/datasetId/snapshotId/volumeId/
                // snapshotVersion and governanceFilename (basename stripped of
                // the source prefix); we fall back to loadBody for each field.
                const govCtx = {
                    sourceType: data.sourceType || loadBody.sourceType || null,
                    filename: data.governanceFilename || null,
                    datasetId: data.datasetId || loadBody.datasetId || null,
                    snapshotId: data.snapshotId || loadBody.snapshotId || null,
                    volumeId: data.volumeId || loadBody.volumeId || null,
                    snapshotVersion: data.snapshotVersion != null ? data.snapshotVersion : (loadBody.snapshotVersion != null ? loadBody.snapshotVersion : null),
                };
                checkGovernanceBundles(govCtx);

                // Snapshot info needed to rebuild a permalink that resolves to
                // this exact file + snapshot (not just the volume).
                state.lastLoadContext = {
                    sourceType: govCtx.sourceType,
                    datasetName: datasetName,
                    datasetId: govCtx.datasetId,
                    snapshotId: govCtx.snapshotId,
                    snapshotVersion: govCtx.snapshotVersion,
                    volumeId: govCtx.volumeId,
                    volumeKey: loadBody.volumeKey || null,
                };
            }
        })
        .catch(error => {
            console.error('Error:', error);
            displayMessage('Error loading dataset. Make sure the server is running.', 'system');
        })
        .finally(() => {
            browseBtn.disabled = false;
            hideLoadingBanner();
        });
    }

    
    // Loading banner functions
    function showLoadingBanner(message) {
        const banner = document.getElementById('loading-banner');
        const text = document.getElementById('loading-banner-text');
        if (banner && text) {
            text.textContent = message || 'Loading...';
            banner.classList.add('visible');
        }
    }
    
    function hideLoadingBanner() {
        const banner = document.getElementById('loading-banner');
        if (banner) {
            banner.classList.remove('visible');
        }
    }
    
    // ===== TABLE VIEW =====
    // The table-view UX (state, DOM refs, render pipeline, pagination,
    // sorting, column pin/reorder/resize, row details, summary cards,
    // summary stats with lazy fetch, right-panel resizing, permalink
    // generation/copy/parse) all moved to modules/table-view.js in plan
    // box 4.4g / P14b. The module is wired up via initTableView() above
    // (called BEFORE loadDatasets() so URL permalink parsing has
    // populated tableState before any auto-load fires).

    initExploreCharts();
});