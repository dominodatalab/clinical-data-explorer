// Table view module — owns the entire data-table UX in the Data Explorer
// frontend. By far the largest single feature module by line count
// (extracted in plan box 4.4g / P14b — landed last per plan §4 step 4
// "smallest first").
//
// Owns:
//   - Table state (`tableState`): pagination, sort, filters, pinned /
//     reordered columns, column widths, expression filter, last data
//     snapshot, pending-row / pending-load-context fields parsed from
//     URL permalinks.
//   - Permalink machinery: `generatePermalink()` (read-only — used by
//     the finding-submit-btn handler in script.js), `copyPermalink()`
//     (clipboard copy with fallback for older browsers), and
//     `parsePermalinkFromUrl()` (reads dataset / filters / page / sort /
//     row / expression-filter / snapshot-context query params at init).
//   - Table render pipeline: `renderTable(data)` builds header + body
//     with column pin/reorder/resize support and sort indicators; row
//     click delegation lives on `tableBody` (one stable listener that
//     survives rerenders).
//   - Pagination controls (first/prev/next/last buttons, page input,
//     page-size selector).
//   - Right-side summary panel:
//       * Sidebar tab switching (`setSidebarTab` — distinct / stats /
//         row-details), backed by a dropdown selector.
//       * Missing-values card (with a per-column drill-down view that
//         doubles as "click to add an `is_missing` filter for this
//         column").
//       * Distinct-values card (lazy fetch via `/table/column_values` +
//         `/table/column_stats` parallel call).
//       * Summary-stats table — virtualized, lazy per-column fetch
//         with batch + concurrency limit + LRU cache keyed on
//         (dataset, filters, expression, sort) tuple. Includes a
//         500-column "wide-table" guardrail that requires explicit
//         load-first-50 confirmation before auto-fetching.
//       * Right-panel resize handle (drag to resize, double-click to
//         collapse / expand the entire summary panel; persists width
//         to localStorage under `dataExplorer.rightPanelWidthPx`).
//   - Row-details tab on the right panel (renders the selected row's
//     fields as a vertical key/value table; preserves the table view's
//     final column order so pin/reorder/labels-toggle settings carry
//     through).
//   - Selected-row plumbing: stable row identifier (5-column key,
//     base64-encoded), find-by-identifier across pages, auto-restore
//     from URL ?row=<id>&rowIdx=<hint> permalinks, and selection
//     preservation across paging/sort/filter changes when the row is
//     still visible.
//   - Column-management UX: pin / unpin (stays at the left edge),
//     drag-to-reorder, drag-to-resize, three-state sort cycling
//     (asc → desc → unsorted).
//
// Exports (12):
//   - `tableState` — the shared mutable singleton. Other modules
//     (`modules/filters.js` via `initFilters({ tableState, ... })`)
//     and script.js (`performDatasetLoad`'s reset on dataset switch,
//     and `loadDatasets`' read of `pendingDataset`/`pendingLoadContext`
//     populated by `parsePermalinkFromUrl()`) depend on this. Object
//     identity matters — every reader must use the same reference.
//   - `initTableView()` — one-shot from script.js's DOMContentLoaded.
//     Caches DOM refs, runs `parsePermalinkFromUrl()` (must complete
//     before `loadDatasets()` fires its .then so the auto-load branch
//     sees the parsed pendingDataset / pendingLoadContext), wires every
//     table-view event listener (pagination, sidebar tabs, right-panel
//     resize, summary cards, stats search/scroll, copy-permalink, row
//     click delegation), and seeds the summary-stats panel visibility.
//   - `loadTableData()`, `loadSummaryData()` — reload primitives,
//     called by initFilters and by `initializeTableView` /
//     `performDatasetLoad`.
//   - `initializeTableView()` — called from `performDatasetLoad` after
//     `state.columnMetadata` lands. Seeds `tableState.columns` /
//     `numericColumns` / `columnOrder` / `pinnedColumns`, then kicks
//     loadTableData + loadSummaryData and re-renders active filters.
//   - `clearSelectedRow()`, `invalidateSummaryStats()` — both called by
//     `performDatasetLoad` on dataset switch.
//   - `generatePermalink()` — used by script.js's `finding-submit-btn`
//     click handler to embed the current view URL into the finding
//     payload.
//   - `renderTable(data)`, `populateDistinctColumnSelector(columns)`,
//     `updateMissingValuesCard()`, `updateDistinctValuesCard()`,
//     `renderRowDetailsTab()`, `resortSummaryStatsForLabels()` — used
//     by script.js's use-labels-toggle handler to refresh every
//     table-view-side derived display when the labels toggle flips.
//     The handler stays in script.js because it interleaves table-view
//     refresh calls with `initializeExploreTab()` (explore module) and
//     `renderActiveFilters()` (filters module); preserving the exact
//     interleaving order satisfies ground rule #2.
//
// Module-private state (not exported):
//   - `summaryStatsExpanded`, `rowClickHintShown`, `draggedColumn`,
//     `resizeState` — the four feature-local lets P10 deferred for
//     this module's eventual extraction. Now closed out.
//   - `summaryStatsState` (the lazy-fetch / virtualization state
//     machine) and `columnStatsCacheByRequestKey` (the LRU cache) —
//     both internal to the summary-stats subsystem.
//   - `RIGHT_PANEL_WIDTH_LS_KEY`, `RIGHT_PANEL_MIN_PX`,
//     `STATS_CACHE_MAX_KEYS` — constants.
//
// Per ground rule #2, every behavior is preserved verbatim:
//   - Same URL query-param shape (`filters`, `dataset`, `page`, `sort`,
//     `sortDir`, `row`, `rowIdx`, `expr`, `exprSyntax`, `volumeKey`,
//     `volumeId`, `snapshotId`, `snapshotVersion`, `loadDatasetId`).
//   - Same localStorage key (`dataExplorer.rightPanelWidthPx`).
//   - Same row-identifier hash (5-column key, btoa-encoded, 32-char
//     prefix, with the unicode-safe `unescape(encodeURIComponent(...))`
//     dance + the bit-twiddle fallback for older browsers).
//   - Same `/table/data` vs `/table/expression_filter` endpoint dispatch
//     (the latter only when `tableState.expressionFilter` is set).
//   - Same wide-table threshold (500 columns triggers the load-first-50
//     guardrail) and same batch/concurrency tuning (25 cols/batch, 4
//     concurrent fetches, scroll debounce 120ms).

import { state } from '../core/state.js';
import { apiUrl, fetchJson } from '../core/api.js';
import { escapeHtml, showToast } from '../core/dom.js';
import { getDisplayName, getDisplayNameWithOriginal } from './column-labels.js';
import { renderActiveFilters } from './filters.js';

// ===== Shared mutable singleton =====
export const tableState = {
    currentPage: 1,
    pageSize: 100,
    totalPages: 1,
    totalRows: 0,
    filters: [],
    sortColumn: null,
    sortDirection: 'asc',
    pinnedColumns: [],
    columnOrder: [],
    columns: [],
    numericColumns: [],
    summaryData: null,
    columnWidths: {},
    lastData: null,
    selectedRowIndex: null,  // Index within current page data
    pendingRowId: null,      // Row ID from URL to open after data loads
    pendingRowIdx: null,     // Row index hint from URL
    // Expression filter state
    expressionFilter: null    // { expression: string, syntax: 'sas'|'r'|'python' }
};

// ===== Module-private mutable state =====
let summaryStatsExpanded = true;
let rowClickHintShown = false;
let draggedColumn = null;
let resizeState = {
    isResizing: false,
    startX: 0,
    startWidth: 0,
    column: null,
    th: null
};

// ===== Constants =====
const RIGHT_PANEL_WIDTH_LS_KEY = 'dataExplorer.rightPanelWidthPx';
const RIGHT_PANEL_MIN_PX = 280;

// ===== Summary stats lazy-fetch state =====
// requestKey -> Map(col -> stats), capped via LRU to avoid unbounded growth
const columnStatsCacheByRequestKey = new Map();
const STATS_CACHE_MAX_KEYS = 6;

const summaryStatsState = {
    requestKey: null,
    baseColumns: [],
    viewColumns: [],
    renderedCount: 0,
    fetchCursor: 0,
    batchSize: 25,
    statsByColumn: new Map(), // col -> stats
    attemptedCols: new Set(),
    loadingCols: new Set(),
    errorCols: new Set(),
    activeFetchToken: 0,
    filterText: '',
    allowWideAutoLoad: false,
    isFetching: false,
    scrollDebounceTimer: null
};

// ===== Cached DOM refs (populated in initTableView) =====
let tableBody, tableHeader, tableWrapper, tableEmptyState;
let summaryStatsToggleBtn, summaryStatsPanel, sidebarSectionSelect, rightPanelResizeHandle;
let rowDetailsBody;
let statsTableContainer, statsSearchInput, statsLoadInitialBtn;

// ===== Permalink machinery =====

// Build a permalink to the current view. Used by script.js's
// finding-submit-btn click handler (passed into governance.createFinding)
// and shares its URL-building shape with copyPermalink — but they're
// kept as separate functions because copyPermalink also writes to the
// clipboard and clears the row param.
export function generatePermalink() {
    const url = new URL(window.location.href);

    if (state.currentDataset) {
        url.searchParams.set('dataset', state.currentDataset);
    }

    if (tableState.filters.length > 0) {
        url.searchParams.set('filters', JSON.stringify(tableState.filters));
    } else {
        url.searchParams.delete('filters');
    }

    // Preserve extension params so permalinks work within extension context
    if (state.extensionDatasetId) {
        url.searchParams.set('datasetId', state.extensionDatasetId);
        if (state.extensionSnapshotId) {
            url.searchParams.set('datasetSnapshotId', state.extensionSnapshotId);
        }
        if (state.extensionFilePath) {
            url.searchParams.set('filePath', state.extensionFilePath);
        }
        const mountPointType = new URLSearchParams(window.location.search).get('mountPointType');
        if (mountPointType) {
            url.searchParams.set('mountPointType', mountPointType);
        }
    } else if (state.extensionProjectId) {
        url.searchParams.set('projectId', state.extensionProjectId);
    }

    // Embed snapshot/source identity of the currently loaded file so the
    // receiver can reload the *same* snapshot — the /datasets listing only
    // reflects the latest snapshot, so display_name alone isn't enough.
    ['volumeKey', 'volumeId', 'snapshotId', 'snapshotVersion', 'loadDatasetId'].forEach(k => url.searchParams.delete(k));
    if (state.lastLoadContext && state.lastLoadContext.sourceType === 'netapp') {
        if (state.lastLoadContext.volumeKey) url.searchParams.set('volumeKey', state.lastLoadContext.volumeKey);
        if (state.lastLoadContext.volumeId) url.searchParams.set('volumeId', state.lastLoadContext.volumeId);
        if (state.lastLoadContext.snapshotId) url.searchParams.set('snapshotId', state.lastLoadContext.snapshotId);
        if (state.lastLoadContext.snapshotVersion != null) url.searchParams.set('snapshotVersion', String(state.lastLoadContext.snapshotVersion));
    } else if (state.lastLoadContext && state.lastLoadContext.sourceType === 'dataset' && !state.extensionDatasetId) {
        // In-app dataset navigation (no extension dataset context) — carry ids explicitly.
        if (state.lastLoadContext.datasetId) url.searchParams.set('loadDatasetId', state.lastLoadContext.datasetId);
        if (state.lastLoadContext.snapshotId) url.searchParams.set('snapshotId', state.lastLoadContext.snapshotId);
    }

    url.searchParams.delete('row');
    return url.toString();
}

function copyPermalink() {
    const url = new URL(window.location.href);
    
    // Include dataset name
    if (state.currentDataset) {
        url.searchParams.set('dataset', state.currentDataset);
    } else {
        url.searchParams.delete('dataset');
    }
    
    // Include filters
    if (tableState.filters.length > 0) {
        url.searchParams.set('filters', JSON.stringify(tableState.filters));
    } else {
        url.searchParams.delete('filters');
    }
    
    // Include expression filter
    if (tableState.expressionFilter) {
        url.searchParams.set('expr', tableState.expressionFilter.expression);
        url.searchParams.set('exprSyntax', tableState.expressionFilter.syntax);
    } else {
        url.searchParams.delete('expr');
        url.searchParams.delete('exprSyntax');
    }
    
    // Clear any row parameter - this is for table view, not row detail
    url.searchParams.delete('row');

    navigator.clipboard.writeText(url.toString()).then(() => {
        showToast('Link copied to clipboard!');
    }).catch(() => {
        // Fallback for older browsers
        const textArea = document.createElement('textarea');
        textArea.value = url.toString();
        document.body.appendChild(textArea);
        textArea.select();
        document.execCommand('copy');
        document.body.removeChild(textArea);
        showToast('Link copied to clipboard!');
    });
}

// Parse filters and dataset from URL on page load
function parsePermalinkFromUrl() {
    const params = new URLSearchParams(window.location.search);
    
    // Parse filters
    const filtersParam = params.get('filters');
    if (filtersParam) {
        try {
            tableState.filters = JSON.parse(decodeURIComponent(filtersParam));
        } catch (e) {
            console.error('Failed to parse filters from URL:', e);
        }
    }
    
    // Parse dataset - will be loaded after datasets list is fetched
    const datasetParam = params.get('dataset');
    if (datasetParam) {
        tableState.pendingDataset = decodeURIComponent(datasetParam);
    }

    // Parse snapshot/source identity embedded by generatePermalink so the
    // auto-loader can target the exact snapshot (not just latest).
    const volumeKeyParam = params.get('volumeKey');
    const loadDatasetIdParam = params.get('loadDatasetId');
    const snapshotIdParam = params.get('snapshotId');
    const snapshotVersionParam = params.get('snapshotVersion');
    if (volumeKeyParam) {
        tableState.pendingLoadContext = {
            sourceType: 'netapp',
            volumeKey: volumeKeyParam,
            volumeId: params.get('volumeId') || null,
            snapshotId: snapshotIdParam || null,
            snapshotVersion: snapshotVersionParam != null && snapshotVersionParam !== ''
                ? parseInt(snapshotVersionParam, 10)
                : null,
        };
    } else if (loadDatasetIdParam) {
        tableState.pendingLoadContext = {
            sourceType: 'dataset',
            datasetId: loadDatasetIdParam,
            snapshotId: snapshotIdParam || null,
        };
    }
    
    // Parse page number
    const pageParam = params.get('page');
    if (pageParam) {
        const page = parseInt(pageParam, 10);
        if (!isNaN(page) && page > 0) {
            tableState.currentPage = page;
        }
    }
    
    // Parse sort settings
    const sortParam = params.get('sort');
    const sortDirParam = params.get('sortDir');
    if (sortParam) {
        tableState.sortColumn = sortParam;
        tableState.sortDirection = sortDirParam === 'desc' ? 'desc' : 'asc';
    }
    
    // Parse row ID for direct linking to a specific row
    const rowParam = params.get('row');
    if (rowParam) {
        tableState.pendingRowId = rowParam;
    }
    
    // Parse row index hint (which row on the page)
    const rowIdxParam = params.get('rowIdx');
    if (rowIdxParam) {
        const idx = parseInt(rowIdxParam, 10);
        if (!isNaN(idx) && idx >= 0) {
            tableState.pendingRowIdx = idx;
        }
    }
    
    // Parse expression filter from URL
    const exprParam = params.get('expr');
    const exprSyntaxParam = params.get('exprSyntax');
    if (exprParam) {
        const syntax = exprSyntaxParam || 'sas';  // Default to SAS if not specified
        if (['sas', 'r', 'python'].includes(syntax)) {
            tableState.expressionFilter = {
                expression: decodeURIComponent(exprParam),
                syntax: syntax
            };
        }
    }
}

// ===== Sidebar / summary stats panel =====

function toggleSummaryStats() {
    summaryStatsExpanded = !summaryStatsExpanded;
    summaryStatsPanel.classList.toggle('collapsed', !summaryStatsExpanded);
    summaryStatsToggleBtn.classList.toggle('active', summaryStatsExpanded);

    const resizeHandle = document.getElementById('right-panel-resize-handle');
    if (resizeHandle) {
        resizeHandle.classList.toggle('panel-collapsed', !summaryStatsExpanded);
        resizeHandle.title = summaryStatsExpanded
            ? 'Drag to resize \u00b7 Double-click to collapse'
            : 'Double-click to expand';
    }
}

function ensureDefaultSelectedColumn(selectId) {
    const select = document.getElementById(selectId);
    if (!select || select.value) return;
    const firstColumnOption = Array.from(select.options).find(opt => opt.value);
    if (firstColumnOption) {
        select.value = firstColumnOption.value;
    }
}

function setSidebarTab(tabId, { ensureDefaults = true } = {}) {
    // Cancel in-flight stats fetches when leaving Summary Stats
    if (state.selectedSidebarTab === 'stats' && tabId !== 'stats') {
        cancelSummaryStatsFetches();
    }

    state.selectedSidebarTab = tabId;

    const sidebarContent = document.querySelector('.summary-sidebar-tab-content');
    if (sidebarContent) {
        sidebarContent.classList.toggle('stats-mode', tabId === 'stats');
    }

    // Sync dropdown
    if (sidebarSectionSelect) {
        sidebarSectionSelect.value = tabId;
    }

    document.querySelectorAll('.summary-sidebar-pane').forEach(p => {
        p.classList.toggle('active', p.id === `sidebar-pane-${tabId}`);
    });

    if (ensureDefaults) {
        if (tabId === 'distinct') {
            ensureDefaultSelectedColumn('distinct-column-selector');
            updateDistinctValuesCard();
        } else if (tabId === 'stats') {
            renderSummaryStatsTable();
            if (state.currentDataset) {
                ensureSummaryStatsInitialLoad();
            }
        } else if (tabId === 'row-details') {
            renderRowDetailsTab();
        }
    }
}

// ===== Right panel resizing =====

function getRightPanelMaxPx() {
    const container = document.querySelector('.table-view-container');
    if (!container) return RIGHT_PANEL_MIN_PX;
    return Math.floor(container.getBoundingClientRect().width * 0.55);
}

function clampRightPanelWidth(px) {
    const maxPx = getRightPanelMaxPx();
    const next = Math.max(RIGHT_PANEL_MIN_PX, Math.min(px, maxPx));
    return Number.isFinite(next) ? next : RIGHT_PANEL_MIN_PX;
}

function applyRightPanelWidth(px, { persist = false } = {}) {
    if (!summaryStatsPanel) return;
    const next = clampRightPanelWidth(px);
    state.rightPanelWidthPx = next;
    summaryStatsPanel.style.setProperty('--right-panel-width', `${next}px`);
    if (persist) {
        try {
            localStorage.setItem(RIGHT_PANEL_WIDTH_LS_KEY, String(next));
        } catch {}
    }
}

function initRightPanelWidth() {
    let stored = null;
    try {
        stored = parseInt(localStorage.getItem(RIGHT_PANEL_WIDTH_LS_KEY) || '', 10);
    } catch {}

    if (Number.isFinite(stored) && stored > 0) {
        applyRightPanelWidth(stored, { persist: false });
    } else if (summaryStatsPanel) {
        applyRightPanelWidth(summaryStatsPanel.getBoundingClientRect().width || 320, { persist: false });
    }
}

// ===== Row details tab =====

export function renderRowDetailsTab() {
    if (!rowDetailsBody) return;

    if (!state.selectedRow) {
        rowDetailsBody.innerHTML = `
            <div class="row-details-empty-state">
                <div class="empty-title">No row selected</div>
                <div>No row selected. Click a row in the table to see details here.</div>
            </div>
        `;
        return;
    }

    const rowIsVisibleOnPage = state.selectedRowId && tableState.lastData && findRowByIdentifier(state.selectedRowId, tableState.lastData) >= 0;
    const visibilityNote = rowIsVisibleOnPage ? '' : `
        <div class="row-details-empty-state row-details-warning">
            <div class="empty-title">Selected row not visible</div>
            <div>The selected row is not visible on this page. Change pages or adjust sorting/filters to find it again.</div>
        </div>
    `;

    // Get final column order (same as table view - pinned first, then rest)
    const finalOrder = getFinalColumnOrder();
    let html = '<table class="row-detail-table">';

    finalOrder.forEach(col => {
        const value = state.selectedRow[col];
        const displayInfo = getDisplayNameWithOriginal(col);
        const isNumeric = tableState.numericColumns.includes(col);

        // Column header (name/label)
        const thContent = displayInfo.hasLabel
            ? `<span class="col-label">${displayInfo.display}</span><span class="col-name">${displayInfo.original}</span>`
            : `<span class="col-label">${displayInfo.display}</span>`;

        // Cell value
        let tdClass = '';
        let tdContent;
        if (value === null || value === undefined || value === '') {
            tdClass = 'null-value';
            tdContent = 'null';
        } else {
            tdContent = String(value);
            if (isNumeric) tdClass = 'numeric';
        }

        html += `<tr><th>${thContent}</th><td class="${tdClass}">${escapeHtml(tdContent)}</td></tr>`;
    });

    html += '</table>';
    rowDetailsBody.innerHTML = visibilityNote + html;
}

function highlightSelectedRow(rowIndex) {
    // Remove previous selection
    tableBody.querySelectorAll('tr.selected').forEach(tr => tr.classList.remove('selected'));

    // Add selection to current row
    const rows = tableBody.querySelectorAll('tr');
    if (rows[rowIndex]) {
        rows[rowIndex].classList.add('selected');
    }
}

function setSelectedRow(rowIndex, rowData, { switchToDetails = true } = {}) {
    if (!rowData) return;
    state.selectedRowIndex = rowIndex;
    state.selectedRow = (rowData && typeof rowData === 'object') ? { ...rowData } : rowData;
    state.selectedRowId = createRowIdentifier(rowData);
    state.selectedRowContextKey = getStatsRequestKey();

    tableState.selectedRowIndex = rowIndex;
    highlightSelectedRow(rowIndex);

    if (switchToDetails) {
        // Auto-open the right panel when a row is clicked.
        if (!summaryStatsExpanded) {
            toggleSummaryStats();
        }
        setSidebarTab('row-details', { ensureDefaults: true });
        // Force immediate rerender even if the tab is already active
        renderRowDetailsTab();
    } else {
        renderRowDetailsTab();
    }
}

export function clearSelectedRow() {
    state.selectedRowIndex = null;
    state.selectedRow = null;
    state.selectedRowId = null;
    state.selectedRowContextKey = null;
    tableState.selectedRowIndex = null;
    tableBody.querySelectorAll('tr.selected').forEach(tr => tr.classList.remove('selected'));
    if (state.selectedSidebarTab === 'row-details') {
        renderRowDetailsTab();
    }
}

// (Row permalink copy / modal navigation removed — Row Details is now a tab)

function createRowIdentifier(rowData) {
    // Create a hash-like identifier from the row's values
    // Use the original column order (not affected by pinning/reordering) for consistency
    // This ensures the same row can be found when someone else opens the permalink
    const columns = tableState.columns.length > 0 ? tableState.columns : Object.keys(rowData);
    const keyValues = columns.slice(0, Math.min(5, columns.length))
        .map(col => `${col}:${rowData[col] ?? ''}`)
        .join('|');
    
    // Create a simple hash using encodeURIComponent to handle unicode safely
    try {
        return btoa(unescape(encodeURIComponent(keyValues))).replace(/[=+/]/g, '').substring(0, 32);
    } catch (e) {
        // Fallback for any encoding issues - use a simpler approach
        console.warn('Error creating row identifier:', e);
        return String(keyValues).split('').reduce((a, b) => ((a << 5) - a + b.charCodeAt(0)) | 0, 0).toString(36);
    }
}

function findRowByIdentifier(rowId, data) {
    // Try to find a row that matches the identifier
    // Use the original column order (same as createRowIdentifier)
    if (!data || data.length === 0) return -1;
    
    const columns = tableState.columns.length > 0 ? tableState.columns : Object.keys(data[0]);
    const keyCols = columns.slice(0, Math.min(5, columns.length));
    
    for (let i = 0; i < data.length; i++) {
        const rowData = data[i];
        const keyValues = keyCols
            .map(col => `${col}:${rowData[col] ?? ''}`)
            .join('|');
        
        let hash;
        try {
            hash = btoa(unescape(encodeURIComponent(keyValues))).replace(/[=+/]/g, '').substring(0, 32);
        } catch (e) {
            hash = String(keyValues).split('').reduce((a, b) => ((a << 5) - a + b.charCodeAt(0)) | 0, 0).toString(36);
        }
        
        if (hash === rowId) {
            return i;
        }
    }
    return -1;
}

// Show hint on first table load (unless we're loading a row permalink)
function showRowClickHint() {
    // Don't show hint if we're opening from a row permalink
    if (rowClickHintShown || tableState.pendingRowId) return;
    rowClickHintShown = true;
    
    const hint = document.createElement('div');
    hint.className = 'row-click-hint';
    hint.textContent = 'Tip: Click on any row to view details';
    document.body.appendChild(hint);
    
    setTimeout(() => {
        hint.remove();
    }, 4000);
}

// ===== Pagination =====

function goToPage(page) {
    if (page < 1 || page > tableState.totalPages) return;
    tableState.currentPage = page;
    loadTableData();
}

function updatePagination() {
    document.getElementById('page-input').value = tableState.currentPage;
    document.getElementById('total-pages').textContent = tableState.totalPages;
    
    document.getElementById('first-page-btn').disabled = tableState.currentPage <= 1;
    document.getElementById('prev-page-btn').disabled = tableState.currentPage <= 1;
    document.getElementById('next-page-btn').disabled = tableState.currentPage >= tableState.totalPages;
    document.getElementById('last-page-btn').disabled = tableState.currentPage >= tableState.totalPages;

    const start = (tableState.currentPage - 1) * tableState.pageSize + 1;
    const end = Math.min(tableState.currentPage * tableState.pageSize, tableState.totalRows);
    document.getElementById('table-row-info').textContent = 
        tableState.totalRows > 0 
            ? `Showing ${start}-${end} of ${tableState.totalRows} rows` 
            : 'No rows to display';
}

// ===== Summary cards =====

export function updateMissingValuesCard() {
    if (!tableState.summaryData) return;
    
    const view = document.getElementById('missing-view-selector').value;
    const missing = tableState.summaryData.missing_values;
    const valueEl = document.getElementById('missing-values-value');
    const listEl = document.getElementById('missing-per-column-list');
    const cardEl = document.getElementById('missing-values-card');
    
    // Clear the per-column list by default
    listEl.innerHTML = '';
    cardEl.classList.remove('summary-card-expanded');
    
    if (view === 'percentage') {
        valueEl.textContent = missing.missing_percentage.toFixed(1) + '%';
    } else if (view === 'total') {
        valueEl.textContent = missing.total_missing_cells.toLocaleString();
    } else if (view === 'by_column') {
        if (missing.columns_with_most_missing.length > 0) {
            const [col, count] = missing.columns_with_most_missing[0];
            valueEl.textContent = `${col}: ${count}`;
        } else {
            valueEl.textContent = 'None';
        }
    } else if (view === 'count_per_column') {
        // Show total as the main value
        valueEl.textContent = missing.total_missing_cells.toLocaleString() + ' total';
        cardEl.classList.add('summary-card-expanded');
        
        // Build per-column breakdown
        if (missing.by_column && Object.keys(missing.by_column).length > 0) {
            // Sort columns by missing count descending
            const sortedColumns = Object.entries(missing.by_column)
                .sort((a, b) => b[1] - a[1]);
            
            let html = '<div class="missing-per-column-grid">';
            sortedColumns.forEach(([col, count]) => {
                const displayName = getDisplayName(col);
                const colLabel = displayName !== col ? displayName : col;
                const percentage = tableState.summaryData.total_rows > 0 
                    ? ((count / tableState.summaryData.total_rows) * 100).toFixed(1)
                    : 0;
                const hasWarning = count > 0;
                const filterIcon = hasWarning ? '<span class="missing-filter-icon" title="Filter to show missing rows"><svg viewBox="0 0 24 24" width="10" height="10" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polygon points="22 3 2 3 10 12.46 10 19 14 21 14 12.46 22 3"></polygon></svg></span>' : '';
                html += `<div class="missing-column-item${hasWarning ? ' has-missing' : ''}" title="${escapeHtml(col)}: ${count.toLocaleString()} missing (${percentage}%)" ${hasWarning ? `data-column="${escapeHtml(col)}"` : ''}>
                    <span class="missing-col-name">${escapeHtml(colLabel)}</span>
                    <span class="missing-col-count">${count.toLocaleString()}${filterIcon}</span>
                </div>`;
            });
            html += '</div>';
            listEl.innerHTML = html;
            
            // Add click handlers for items with missing values
            listEl.querySelectorAll('.missing-column-item.has-missing').forEach(item => {
                item.addEventListener('click', () => {
                    const col = item.dataset.column;
                    if (!col) return;
                    
                    // Check if an "is_missing" filter already exists for this column
                    const alreadyFiltered = tableState.filters.some(
                        f => f.column === col && f.operator === 'is_missing'
                    );
                    if (alreadyFiltered) return;
                    
                    tableState.filters.push({ column: col, operator: 'is_missing', value: null });
                    tableState.currentPage = 1;
                    renderActiveFilters();
                    loadTableData();
                    loadSummaryData();
                });
            });
        } else {
            listEl.innerHTML = '<span class="missing-per-column-empty">No columns</span>';
        }
    }
}

export async function updateDistinctValuesCard() {
    const column = document.getElementById('distinct-column-selector').value;
    const valueEl = document.getElementById('distinct-values-value');
    const listEl = document.getElementById('distinct-values-list');
    
    if (!column) {
        valueEl.textContent = '-';
        listEl.innerHTML = '';
        return;
    }

    try {
        const filtersParam = JSON.stringify(tableState.filters);
        
        // Build URLs with query parameters using URLSearchParams
        const statsParams = new URLSearchParams();
        statsParams.set('filters', filtersParam);
        if (tableState.expressionFilter && tableState.expressionFilter.expression) {
            statsParams.set('expression', tableState.expressionFilter.expression);
            statsParams.set('syntax', tableState.expressionFilter.syntax);
        }
        const statsUrl = `table/column_stats/${encodeURIComponent(column)}?${statsParams.toString()}`;
        
        const valuesParams = new URLSearchParams();
        valuesParams.set('limit', '10');
        valuesParams.set('filters', filtersParam);
        if (tableState.expressionFilter && tableState.expressionFilter.expression) {
            valuesParams.set('expression', tableState.expressionFilter.expression);
            valuesParams.set('syntax', tableState.expressionFilter.syntax);
        }
        const valuesUrl = `table/column_values/${encodeURIComponent(column)}?${valuesParams.toString()}`;
        
        // Fetch both stats and values in parallel
        const [statsData, valuesData] = await Promise.all([
            fetchJson(apiUrl(statsUrl)),
            fetchJson(apiUrl(valuesUrl))
        ]);
        
        // Show count
        valueEl.textContent = statsData.unique_count.toLocaleString();
        
        // Show top values
        if (valuesData.values && valuesData.values.length > 0) {
            const maxToShow = 10;
            const values = valuesData.values.slice(0, maxToShow);
            const totalUnique = valuesData.total_unique || statsData.unique_count;
            
            let html = '<div class="distinct-values-items">';
            values.forEach(val => {
                const displayVal = val.length > 25 ? val.substring(0, 22) + '...' : val;
                html += `<span class="distinct-value-item" title="${escapeHtml(val)}">${escapeHtml(displayVal)}</span>`;
            });
            
            if (totalUnique > maxToShow) {
                html += `<span class="distinct-value-more">+${(totalUnique - maxToShow).toLocaleString()} more</span>`;
            }
            html += '</div>';
            listEl.innerHTML = html;
        } else {
            listEl.innerHTML = '<span class="distinct-value-empty">No values</span>';
        }
    } catch (e) {
        console.error('Error getting distinct values:', e);
        valueEl.textContent = 'Error';
        listEl.innerHTML = '';
    }
}

// ===== SUMMARY STATS TABLE (per-column, lazy fetch) =====

function getStatsRequestKey() {
    return JSON.stringify({
        dataset: state.currentDataset,
        filters: tableState.filters,
        expression: tableState.expressionFilter?.expression || null,
        syntax: tableState.expressionFilter?.syntax || null,
        sort_column: tableState.sortColumn || null,
        sort_direction: tableState.sortDirection || null,
        // If server-side sampling is introduced later, include it here to keep caching correct.
        sampling: null
    });
}

function touchStatsCacheKey(requestKey) {
    if (!requestKey) return;
    const existing = columnStatsCacheByRequestKey.get(requestKey);
    if (!existing) return;
    // Move to most-recently-used
    columnStatsCacheByRequestKey.delete(requestKey);
    columnStatsCacheByRequestKey.set(requestKey, existing);
}

function getOrCreateStatsCacheMap(requestKey) {
    if (!requestKey) return null;
    let cacheMap = columnStatsCacheByRequestKey.get(requestKey);
    if (cacheMap) {
        touchStatsCacheKey(requestKey);
        return cacheMap;
    }
    cacheMap = new Map();
    columnStatsCacheByRequestKey.set(requestKey, cacheMap);
    // Evict least-recently-used requestKeys
    while (columnStatsCacheByRequestKey.size > STATS_CACHE_MAX_KEYS) {
        const oldestKey = columnStatsCacheByRequestKey.keys().next().value;
        columnStatsCacheByRequestKey.delete(oldestKey);
    }
    return cacheMap;
}

export function invalidateSummaryStats() {
    summaryStatsState.requestKey = null;
    summaryStatsState.renderedCount = 0;
    summaryStatsState.fetchCursor = 0;
    summaryStatsState.statsByColumn.clear();
    summaryStatsState.attemptedCols.clear();
    summaryStatsState.loadingCols.clear();
    summaryStatsState.errorCols.clear();
    summaryStatsState.isFetching = false;
    summaryStatsState.activeFetchToken++;
}

function cancelSummaryStatsFetches() {
    summaryStatsState.loadingCols.clear();
    summaryStatsState.isFetching = false;
    if (summaryStatsState.scrollDebounceTimer) {
        clearTimeout(summaryStatsState.scrollDebounceTimer);
        summaryStatsState.scrollDebounceTimer = null;
    }
    summaryStatsState.activeFetchToken++;
}

function formatStatNumber(value) {
    if (value === null || value === undefined || value === '') return '–';
    if (typeof value !== 'number' || Number.isNaN(value)) return '–';
    return Number.isInteger(value) ? value.toLocaleString() : value.toFixed(2);
}

async function fetchColumnStatsForTable(column, fetchToken) {
    const requestKey = getStatsRequestKey();
    const cachedForKey = columnStatsCacheByRequestKey.get(requestKey);
    if (cachedForKey && cachedForKey.has(column)) {
        touchStatsCacheKey(requestKey);
        return cachedForKey.get(column);
    }

    const filtersParam = JSON.stringify(tableState.filters);
    const params = new URLSearchParams();
    params.set('filters', filtersParam);
    if (tableState.expressionFilter && tableState.expressionFilter.expression) {
        params.set('expression', tableState.expressionFilter.expression);
        params.set('syntax', tableState.expressionFilter.syntax);
    }
    const url = `table/column_stats/${encodeURIComponent(column)}?${params.toString()}`;

    const response = await fetch(apiUrl(url));
    if (!response.ok) {
        const responseText = await response.text();
        console.error(`column_stats request failed (${response.status}) for ${column}:`, responseText);
        throw new Error(`HTTP ${response.status}`);
    }
    const data = await response.json();
    if (fetchToken !== summaryStatsState.activeFetchToken) return null;
    if (data && data.error) throw new Error(data.error);

    // Cache by request context + column
    try {
        const cacheMap = getOrCreateStatsCacheMap(requestKey);
        if (cacheMap) cacheMap.set(column, data);
    } catch {}

    return data;
}

function updateSummaryStatsViewColumns() {
    const q = (summaryStatsState.filterText || '').trim().toLowerCase();
    if (!q) {
        summaryStatsState.viewColumns = [...summaryStatsState.baseColumns];
        return;
    }
    summaryStatsState.viewColumns = summaryStatsState.baseColumns.filter(col => {
        const display = getDisplayName(col).toLowerCase();
        return display.includes(q) || col.toLowerCase().includes(q);
    });
}

function getStatsInitialRenderCount() {
    if (!statsTableContainer) return summaryStatsState.batchSize;
    const h = statsTableContainer.clientHeight || 240;
    const approxRowHeight = 34;
    const approxHeaderAndPadding = 60;
    const visible = Math.max(8, Math.ceil((h - approxHeaderAndPadding) / approxRowHeight));
    return Math.min(visible + 10, summaryStatsState.batchSize);
}

async function loadSummaryStatsBatch(fetchToken) {
    if (summaryStatsState.isFetching) return;
    summaryStatsState.isFetching = true;

    const start = summaryStatsState.fetchCursor;
    const maxDesired = Math.min(summaryStatsState.renderedCount, summaryStatsState.viewColumns.length);
    const end = Math.min(start + summaryStatsState.batchSize, maxDesired);
    if (start >= end) {
        summaryStatsState.isFetching = false;
        return;
    }

    renderSummaryStatsTable(); // render placeholders immediately

    const toFetch = summaryStatsState.viewColumns
        .slice(start, end)
        .filter(col => !summaryStatsState.attemptedCols.has(col));
    if (toFetch.length === 0) {
        summaryStatsState.fetchCursor = end;
        summaryStatsState.isFetching = false;
        return;
    }

    const concurrency = 4;
    let idx = 0;

    async function worker() {
        while (idx < toFetch.length) {
            const col = toFetch[idx++];
            if (fetchToken !== summaryStatsState.activeFetchToken) return;
            summaryStatsState.loadingCols.add(col);
            try {
                const stats = await fetchColumnStatsForTable(col, fetchToken);
                if (!stats) return;
                summaryStatsState.statsByColumn.set(col, stats);
                summaryStatsState.errorCols.delete(col);
            } catch (e) {
                console.error('Error fetching stats for', col, e);
                summaryStatsState.errorCols.add(col);
            } finally {
                summaryStatsState.loadingCols.delete(col);
                summaryStatsState.attemptedCols.add(col);
                renderSummaryStatsTable();
            }
        }
    }

    await Promise.all(Array.from({ length: concurrency }, () => worker()));
    summaryStatsState.fetchCursor = end;
    summaryStatsState.isFetching = false;
}

function renderSummaryStatsTable() {
    if (!statsTableContainer) return;

    if (!state.currentDataset || !state.columnMetadata) {
        if (statsLoadInitialBtn) statsLoadInitialBtn.style.display = 'none';
        statsTableContainer.innerHTML = `
            <div class="stats-table-loading">
                <span class="muted">Load a dataset to view summary statistics.</span>
            </div>
        `;
        return;
    }

    const requestKey = getStatsRequestKey();
    if (summaryStatsState.requestKey !== requestKey) {
        summaryStatsState.requestKey = requestKey;
        summaryStatsState.baseColumns = [...(tableState.columns || [])];
        // Sort by display name so it matches the rest of the UI
        summaryStatsState.baseColumns.sort((a, b) => getDisplayName(a).toLowerCase().localeCompare(getDisplayName(b).toLowerCase()));
        updateSummaryStatsViewColumns();
        summaryStatsState.renderedCount = 0;
        summaryStatsState.statsByColumn.clear();
        summaryStatsState.loadingCols.clear();
        summaryStatsState.errorCols.clear();
        summaryStatsState.allowWideAutoLoad = false;
    }

    if (summaryStatsState.baseColumns.length === 0) {
        if (statsLoadInitialBtn) statsLoadInitialBtn.style.display = 'none';
        statsTableContainer.innerHTML = `<div class="stats-table-loading"><span class="muted">No columns available.</span></div>`;
        return;
    }

    // Guardrail for very wide tables: require search or explicit load
    const WIDE_COLUMN_THRESHOLD = 500;
    const isWide = summaryStatsState.baseColumns.length >= WIDE_COLUMN_THRESHOLD;
    const hasSearch = (summaryStatsState.filterText || '').trim().length > 0;
    if (isWide && !hasSearch && !summaryStatsState.allowWideAutoLoad) {
        if (statsLoadInitialBtn) {
            statsLoadInitialBtn.style.display = 'inline-flex';
            statsLoadInitialBtn.textContent = 'Load first 50';
        }
        statsTableContainer.innerHTML = `
            <div class="stats-table-loading">
                <span class="muted">This dataset has ${summaryStatsState.baseColumns.length.toLocaleString()} columns.</span>
                <span class="muted">Search to narrow variables, or load the first 50 columns.</span>
            </div>
        `;
        return;
    } else if (statsLoadInitialBtn) {
        statsLoadInitialBtn.style.display = 'none';
    }

    const visibleCols = summaryStatsState.viewColumns.slice(0, summaryStatsState.renderedCount);

    let html = '';
    if (summaryStatsState.loadingCols.size > 0 && summaryStatsState.statsByColumn.size === 0) {
        html += `<div class="stats-table-loading"><span class="spinner-small"></span><span>Loading summary statistics...</span></div>`;
    }
    if (visibleCols.length === 0) {
        html += `<div class="stats-table-loading"><span class="spinner-small"></span><span>Preparing summary statistics...</span></div>`;
        statsTableContainer.innerHTML = html;
        return;
    }

    html += `
        <table class="stats-table" aria-label="Summary statistics by column">
            <thead>
                <tr>
                    <th>Variable</th>
                    <th class="numeric">Mean</th>
                    <th class="numeric">Median</th>
                    <th class="numeric">Min</th>
                    <th class="numeric">Max</th>
                    <th class="numeric">Count (non-null)</th>
                </tr>
            </thead>
            <tbody>
    `;

    visibleCols.forEach(col => {
        const displayInfo = getDisplayNameWithOriginal(col);
        const label = displayInfo.hasLabel
            ? `${displayInfo.display} (${displayInfo.original})`
            : displayInfo.display;

        const stats = summaryStatsState.statsByColumn.get(col);
        const isLoading = summaryStatsState.loadingCols.has(col);
        const isError = summaryStatsState.errorCols.has(col);

        let mean = '–', median = '–', min = '–', max = '–', count = '–';
        let countTitle = '';

        if (isError) {
            count = 'Error';
        } else if (stats) {
            const numeric = !!stats.is_numeric;
            count = typeof stats.non_null_count === 'number' ? stats.non_null_count.toLocaleString() : '–';
            if (typeof stats.total_count === 'number' && typeof stats.null_count === 'number') {
                countTitle = `Non-null: ${stats.non_null_count?.toLocaleString?.() ?? stats.non_null_count} | Null: ${stats.null_count.toLocaleString()} | Total: ${stats.total_count.toLocaleString()}`;
            } else if (typeof stats.total_count === 'number') {
                countTitle = `Total rows (after filters): ${stats.total_count.toLocaleString()}`;
            }
            if (numeric) {
                mean = formatStatNumber(stats.mean);
                median = formatStatNumber(stats.median); // may not exist; shows '–'
                min = formatStatNumber(stats.min);
                max = formatStatNumber(stats.max);
            }
        } else if (isLoading) {
            count = '…';
        }

        html += `
            <tr>
                <td title="${escapeHtml(label)}">${escapeHtml(label)}</td>
                <td class="numeric muted">${escapeHtml(mean)}</td>
                <td class="numeric muted">${escapeHtml(median)}</td>
                <td class="numeric muted">${escapeHtml(min)}</td>
                <td class="numeric muted">${escapeHtml(max)}</td>
                <td class="numeric" title="${escapeHtml(countTitle)}">${escapeHtml(count)}</td>
            </tr>
        `;
    });

    html += '</tbody></table>';
    statsTableContainer.innerHTML = html;
}

function ensureSummaryStatsInitialLoad() {
    updateSummaryStatsViewColumns();
    summaryStatsState.fetchCursor = 0;
    // Render first so any key-change detection (which resets renderedCount
    // to 0) is absorbed before we initialize the count.
    renderSummaryStatsTable();
    if (summaryStatsState.renderedCount === 0 && summaryStatsState.viewColumns.length > 0) {
        summaryStatsState.renderedCount = Math.min(getStatsInitialRenderCount(), summaryStatsState.viewColumns.length);
        renderSummaryStatsTable();
    }
    const token = summaryStatsState.activeFetchToken;
    loadSummaryStatsBatch(token);
}

// Re-sort the summary stats column list and re-render. Called from
// script.js's use-labels-toggle handler so the stats table follows the
// new display ordering.
export function resortSummaryStatsForLabels() {
    if (summaryStatsState.baseColumns.length > 0) {
        summaryStatsState.baseColumns.sort((a, b) =>
            getDisplayName(a).toLowerCase().localeCompare(getDisplayName(b).toLowerCase())
        );
        updateSummaryStatsViewColumns();
        renderSummaryStatsTable();
    }
}

// ===== Column management (pin / drag-reorder / resize / sort) =====

function togglePinColumn(column) {
    const idx = tableState.pinnedColumns.indexOf(column);
    if (idx >= 0) {
        tableState.pinnedColumns.splice(idx, 1);
    } else {
        tableState.pinnedColumns.push(column);
    }
    renderTable(tableState.lastData || []);
}

function handleDragStart(e, column) {
    draggedColumn = column;
    e.target.closest('th').classList.add('dragging');
    e.dataTransfer.effectAllowed = 'move';
}

function handleDragOver(e, column) {
    e.preventDefault();
    e.dataTransfer.dropEffect = 'move';
    
    // Remove drag-over class from all headers
    document.querySelectorAll('.data-table th').forEach(th => th.classList.remove('drag-over'));
    
    // Add to current target
    e.target.closest('th').classList.add('drag-over');
}

function handleDrop(e, targetColumn) {
    e.preventDefault();
    
    if (draggedColumn && draggedColumn !== targetColumn) {
        const order = [...tableState.columnOrder];
        const fromIdx = order.indexOf(draggedColumn);
        const toIdx = order.indexOf(targetColumn);
        
        if (fromIdx >= 0 && toIdx >= 0) {
            order.splice(fromIdx, 1);
            order.splice(toIdx, 0, draggedColumn);
            tableState.columnOrder = order;
            renderTable(tableState.lastData || []);
        }
    }
    
    document.querySelectorAll('.data-table th').forEach(th => {
        th.classList.remove('dragging', 'drag-over');
    });
    draggedColumn = null;
}

function handleDragEnd(e) {
    document.querySelectorAll('.data-table th').forEach(th => {
        th.classList.remove('dragging', 'drag-over');
    });
    draggedColumn = null;
}

function startResize(e, column, th) {
    e.preventDefault();
    e.stopPropagation();
    
    resizeState.isResizing = true;
    resizeState.startX = e.pageX;
    resizeState.startWidth = th.offsetWidth;
    resizeState.column = column;
    resizeState.th = th;
    
    th.classList.add('resizing');
    tableWrapper.classList.add('resizing');
    
    document.addEventListener('mousemove', handleResize);
    document.addEventListener('mouseup', stopResize);
}

function handleResize(e) {
    if (!resizeState.isResizing) return;
    
    const diff = e.pageX - resizeState.startX;
    const newWidth = Math.max(80, resizeState.startWidth + diff);
    
    resizeState.th.style.width = newWidth + 'px';
    resizeState.th.style.minWidth = newWidth + 'px';
    
    // Also update the corresponding column in body
    const colIndex = Array.from(resizeState.th.parentNode.children).indexOf(resizeState.th);
    tableBody.querySelectorAll('tr').forEach(row => {
        const td = row.children[colIndex];
        if (td) {
            td.style.width = newWidth + 'px';
            td.style.minWidth = newWidth + 'px';
        }
    });
    
    // Store the width in tableState for persistence
    if (!tableState.columnWidths) tableState.columnWidths = {};
    tableState.columnWidths[resizeState.column] = newWidth;
}

function stopResize() {
    if (resizeState.th) {
        resizeState.th.classList.remove('resizing');
    }
    tableWrapper.classList.remove('resizing');
    
    resizeState.isResizing = false;
    resizeState.th = null;
    resizeState.column = null;
    
    document.removeEventListener('mousemove', handleResize);
    document.removeEventListener('mouseup', stopResize);
    
    // Recalculate pinned column positions after resize
    if (tableState.pinnedColumns.length > 0) {
        updatePinnedColumnPositions();
    }
}

function updatePinnedColumnPositions() {
    const headerCells = tableHeader.querySelectorAll('th');
    const finalOrder = getFinalColumnOrder();
    
    let leftPos = 0;
    const leftPositions = {};
    
    headerCells.forEach((th, idx) => {
        const col = finalOrder[idx];
        if (tableState.pinnedColumns.includes(col)) {
            leftPositions[col] = leftPos;
            th.style.left = leftPos + 'px';
            leftPos += th.offsetWidth;
        }
    });

    // Update body cells
    tableBody.querySelectorAll('tr').forEach(tr => {
        const cells = tr.querySelectorAll('td');
        cells.forEach((td, idx) => {
            const col = finalOrder[idx];
            if (tableState.pinnedColumns.includes(col)) {
                td.style.left = leftPositions[col] + 'px';
            }
        });
    });
}

function getFinalColumnOrder() {
    let orderedColumns = tableState.columnOrder.length > 0 
        ? [...tableState.columnOrder] 
        : [...tableState.columns];
    
    const pinnedCols = tableState.pinnedColumns.filter(c => orderedColumns.includes(c));
    const unpinnedCols = orderedColumns.filter(c => !pinnedCols.includes(c));
    return [...pinnedCols, ...unpinnedCols];
}

function getSortIcon(column) {
    if (tableState.sortColumn === column) {
        return tableState.sortDirection === 'asc' ? '↑' : '↓';
    }
    return '⇅';
}

function sortByColumn(column) {
    if (tableState.sortColumn === column) {
        if (tableState.sortDirection === 'asc') {
            tableState.sortDirection = 'desc';
        } else if (tableState.sortDirection === 'desc') {
            // Third click removes sort
            tableState.sortColumn = null;
            tableState.sortDirection = 'asc';
        }
    } else {
        tableState.sortColumn = column;
        tableState.sortDirection = 'asc';
    }
    
    tableState.currentPage = 1;
    loadTableData();
}

// ===== Main data loading =====

export async function loadTableData() {
    if (!state.currentDataset) {
        tableEmptyState.classList.remove('hidden');
        tableBody.innerHTML = '';
        tableHeader.innerHTML = '';
        return Promise.resolve();
    }

    // Keep selected row across reloads when possible; we'll reconcile after data returns

    // Show loading state in table
    tableBody.innerHTML = '<tr class="table-loading-row"><td colspan="100"><div class="spinner-small" style="display:inline-block;vertical-align:middle;margin-right:8px;width:16px;height:16px;border:2px solid #d6d6d6;border-top-color:#543fde;border-radius:50%;animation:spin 0.8s linear infinite;"></div>Loading table data...</td></tr>';
    tableEmptyState.classList.add('hidden');

    try {
        // Build request body - common fields for both endpoints
        const requestBody = {
            page: tableState.currentPage,
            page_size: tableState.pageSize,
            filters: tableState.filters,
            sort_column: tableState.sortColumn,
            sort_direction: tableState.sortDirection
        };

        // Choose endpoint based on whether expression filter is active
        let endpoint = 'table/data';
        if (tableState.expressionFilter) {
            endpoint = 'table/expression_filter';
            requestBody.expression = tableState.expressionFilter.expression;
            requestBody.syntax = tableState.expressionFilter.syntax;
        }

        const data = await fetchJson(apiUrl(endpoint), {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(requestBody)
        });

        if (data.error) {
            console.error('Table data error:', data.error);
            tableBody.innerHTML = `<tr class="table-loading-row"><td colspan="100">Error: ${data.error}</td></tr>`;
            return;
        }

        tableState.totalRows = data.filtered_rows;
        tableState.totalPages = data.total_pages;
        tableState.currentPage = data.page;
        tableState.lastData = data.data;

        // Update filtered indicator
        const filteredIndicator = document.getElementById('filtered-indicator');
        if (filteredIndicator) {
            if (data.filtered_rows !== data.unfiltered_rows) {
                filteredIndicator.textContent = `(filtered from ${data.unfiltered_rows.toLocaleString()})`;
            } else {
                filteredIndicator.textContent = '';
            }
        }

        renderTable(data.data);
        updatePagination();

        // Show row click hint on first load (only once)
        if (data.data && data.data.length > 0) {
            showRowClickHint();
        }

        // Check if we have a pending row to open from URL permalink
        if (tableState.pendingRowId && data.data && data.data.length > 0) {
            let rowIndex = -1;
            
            // First, try the row index hint if available (most reliable if page/sort unchanged)
            if (tableState.pendingRowIdx !== null && tableState.pendingRowIdx < data.data.length) {
                // Verify this row matches the identifier
                const hintedRow = data.data[tableState.pendingRowIdx];
                const hintedRowId = createRowIdentifier(hintedRow);
                if (hintedRowId === tableState.pendingRowId) {
                    rowIndex = tableState.pendingRowIdx;
                }
            }
            
            // Fall back to searching by identifier
            if (rowIndex < 0) {
                rowIndex = findRowByIdentifier(tableState.pendingRowId, data.data);
            }
            
            if (rowIndex >= 0) {
                // Small delay to ensure table is fully rendered
                setTimeout(() => {
                    setSelectedRow(rowIndex, data.data[rowIndex], { switchToDetails: true });
                }, 100);
            } else {
                console.warn('Could not find row from permalink. The data may have changed.');
            }
            
            // Clear pending row state after attempting to open
            tableState.pendingRowId = null;
            tableState.pendingRowIdx = null;
        }
        // Preserve selection across paging/sorting when possible; clear if filters/expr changed and row is not visible
        else if (state.selectedRowId && data.data && data.data.length > 0) {
            const idx = findRowByIdentifier(state.selectedRowId, data.data);
            if (idx >= 0) {
                // Update row data from latest response and re-highlight
                setSelectedRow(idx, data.data[idx], { switchToDetails: false });
            } else {
                const contextChanged = state.selectedRowContextKey && state.selectedRowContextKey !== getStatsRequestKey();
                if (contextChanged) {
                    clearSelectedRow();
                } else {
                    // Keep details (user may be paging), but remove row highlight
                    tableBody.querySelectorAll('tr.selected').forEach(tr => tr.classList.remove('selected'));
                    if (state.selectedSidebarTab === 'row-details') renderRowDetailsTab();
                }
            }
        }

        return Promise.resolve();

    } catch (e) {
        console.error('Error loading table data:', e);
        tableBody.innerHTML = `<tr class="table-loading-row"><td colspan="100">Error loading data</td></tr>`;
        return Promise.reject(e);
    }
}

export async function loadSummaryData() {
    if (!state.currentDataset) return;

    // Show loading state in summary cards
    document.getElementById('missing-values-value').textContent = '...';
    document.getElementById('distinct-values-value').textContent = '...';
    if (statsTableContainer && state.selectedSidebarTab === 'stats') {
        statsTableContainer.innerHTML = `<div class="stats-table-loading"><span class="spinner-small"></span><span>Loading summary statistics...</span></div>`;
    }

    try {
        const requestBody = { 
            filters: tableState.filters,
            expression: tableState.expressionFilter?.expression,
            syntax: tableState.expressionFilter?.syntax
        };
        const data = await fetchJson(apiUrl('table/summary'), {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(requestBody)
        });

        if (data.error) {
            console.error('Summary data error:', data.error);
            return;
        }

        tableState.summaryData = data;
        
        // Update filtered indicator
        const filteredIndicator = document.getElementById('filtered-indicator');
        if (filteredIndicator) {
            if (data.total_rows !== data.unfiltered_rows) {
                filteredIndicator.textContent = `(filtered from ${data.unfiltered_rows.toLocaleString()})`;
            } else {
                filteredIndicator.textContent = '';
            }
        }

        // Update missing values card
        updateMissingValuesCard();

        // Populate distinct-values column selector
        populateDistinctColumnSelector(data.columns);

        // If these tabs are currently open, ensure a default column is selected
        if (state.selectedSidebarTab === 'distinct') {
            ensureDefaultSelectedColumn('distinct-column-selector');
        }

        // Refresh distinct values and stats cards (they depend on current filters)
        updateDistinctValuesCard();
        // Refresh summary stats table (it depends on current filters)
        invalidateSummaryStats();
        if (state.selectedSidebarTab === 'stats') {
            ensureSummaryStatsInitialLoad();
        }

    } catch (e) {
        console.error('Error loading summary:', e);
    }
}

export function populateDistinctColumnSelector(columns) {
    const distinctSelect = document.getElementById('distinct-column-selector');
    if (!distinctSelect) return;

    // Save current value
    const distinctVal = distinctSelect.value;

    distinctSelect.innerHTML = '<option value="">Select column...</option>';

    // Sort columns alphabetically by display name (case-insensitive) so sort respects friendly names toggle
    const sortedColumns = [...columns].sort((a, b) =>
        getDisplayName(a).toLowerCase().localeCompare(getDisplayName(b).toLowerCase())
    );

    sortedColumns.forEach(col => {
        const displayName = getDisplayName(col);
        const optText = displayName !== col ? `${displayName} (${col})` : col;
        const opt = document.createElement('option');
        opt.value = col;
        opt.textContent = optText;
        distinctSelect.appendChild(opt);
    });

    // Restore value if it still exists
    if (columns.includes(distinctVal)) distinctSelect.value = distinctVal;
}

export function renderTable(data) {
    const emptyMessage = document.getElementById('table-empty-message');
    
    if (!data || data.length === 0) {
        tableHeader.innerHTML = '';
        tableBody.innerHTML = '';
        
        // Set context-aware empty message
        if (!state.currentDataset) {
            emptyMessage.textContent = 'Load a dataset to view data';
        } else if (tableState.filters.length > 0 || tableState.expressionFilter) {
            emptyMessage.textContent = 'No rows match the current filters';
        } else {
            emptyMessage.textContent = 'No data available';
        }
        
        tableEmptyState.classList.remove('hidden');
        return;
    }

    tableEmptyState.classList.add('hidden');

    // Get ordered columns (pinned first, then rest in order)
    let orderedColumns;
    if (tableState.columnOrder.length > 0) {
        orderedColumns = [...tableState.columnOrder];
    } else {
        orderedColumns = Object.keys(data[0]);
        tableState.columnOrder = orderedColumns;
    }

    // Ensure pinned columns come first
    const pinnedCols = tableState.pinnedColumns.filter(c => orderedColumns.includes(c));
    const unpinnedCols = orderedColumns.filter(c => !pinnedCols.includes(c));
    const finalOrder = [...pinnedCols, ...unpinnedCols];

    // Render header
    tableHeader.innerHTML = '';
    const headerRow = document.createElement('tr');
    
    // Track cumulative width for pinned columns
    let pinnedLeftOffset = 0;
    const pinnedOffsets = {};
    
    finalOrder.forEach((col, idx) => {
        const th = document.createElement('th');
        const isPinned = tableState.pinnedColumns.includes(col);
        const isLastPinned = isPinned && idx === pinnedCols.length - 1;
        
        if (isPinned) {
            th.classList.add('pinned');
            th.style.left = pinnedLeftOffset + 'px';
            pinnedOffsets[col] = pinnedLeftOffset;
            if (isLastPinned) {
                th.classList.add('pinned-last');
            }
        }
        
        // Sort indicator
        th.classList.add('sortable');
        if (tableState.sortColumn === col) {
            th.classList.add(tableState.sortDirection === 'asc' ? 'sort-asc' : 'sort-desc');
        }

        // Apply saved column width if exists
        if (tableState.columnWidths && tableState.columnWidths[col]) {
            th.style.width = tableState.columnWidths[col] + 'px';
            th.style.minWidth = tableState.columnWidths[col] + 'px';
        }

        const colDisplay = getDisplayNameWithOriginal(col);
        const headerText = colDisplay.hasLabel 
            ? `<span class="col-with-label"><span>${colDisplay.display}</span><span class="col-original">${colDisplay.original}</span></span>`
            : `<span>${colDisplay.display}</span>`;
        const tooltipText = colDisplay.hasLabel ? `${colDisplay.display} (${colDisplay.original})` : col;

        th.innerHTML = `
            <div class="th-content">
                <span class="drag-handle" draggable="true">⋮⋮</span>
                <span class="th-text" title="${tooltipText}">${headerText}</span>
                <span class="th-sort-icon">${getSortIcon(col)}</span>
                <div class="th-actions">
                    <button class="th-action-btn ${isPinned ? 'pinned' : ''}" title="${isPinned ? 'Unpin' : 'Pin'} column">📌</button>
                </div>
            </div>
            <div class="resize-handle"></div>
        `;

        // Event listeners for sorting
        th.querySelector('.th-text').addEventListener('click', () => sortByColumn(col));
        th.querySelector('.th-sort-icon').addEventListener('click', () => sortByColumn(col));
        
        // Pin button
        th.querySelector('.th-action-btn').addEventListener('click', (e) => {
            e.stopPropagation();
            togglePinColumn(col);
        });

        // Drag events for reordering
        const handle = th.querySelector('.drag-handle');
        handle.addEventListener('dragstart', (e) => handleDragStart(e, col));
        handle.addEventListener('dragend', handleDragEnd);
        th.addEventListener('dragover', (e) => handleDragOver(e, col));
        th.addEventListener('drop', (e) => handleDrop(e, col));

        // Resize handle
        const resizeHandle = th.querySelector('.resize-handle');
        resizeHandle.addEventListener('mousedown', (e) => startResize(e, col, th));

        headerRow.appendChild(th);
    });
    
    tableHeader.appendChild(headerRow);

    // Calculate actual widths of pinned columns after rendering
    const headerCells = headerRow.querySelectorAll('th');
    pinnedLeftOffset = 0;
    headerCells.forEach((th, idx) => {
        const col = finalOrder[idx];
        if (tableState.pinnedColumns.includes(col)) {
            pinnedOffsets[col] = pinnedLeftOffset;
            th.style.left = pinnedLeftOffset + 'px';
            pinnedLeftOffset += th.offsetWidth;
        }
    });

    // Render body
    tableBody.innerHTML = '';
    data.forEach((row, rowIndex) => {
        const tr = document.createElement('tr');
        tr.dataset.rowIndex = String(rowIndex);
        tr.setAttribute('data-testid', 'data-row');
        
        finalOrder.forEach((col, idx) => {
            const td = document.createElement('td');
            const value = row[col];
            const isPinned = tableState.pinnedColumns.includes(col);
            const isLastPinned = isPinned && idx === pinnedCols.length - 1;
            
            if (isPinned) {
                td.classList.add('pinned');
                td.style.left = (pinnedOffsets[col] || 0) + 'px';
                if (isLastPinned) {
                    td.classList.add('pinned-last');
                }
            }

            // Apply saved column width if exists
            if (tableState.columnWidths && tableState.columnWidths[col]) {
                td.style.width = tableState.columnWidths[col] + 'px';
                td.style.minWidth = tableState.columnWidths[col] + 'px';
            }
            
            if (value === null || value === undefined || value === '') {
                td.textContent = 'null';
                td.classList.add('null-value');
            } else {
                td.textContent = String(value);
                td.title = String(value);
                
                // Right-align numeric values
                if (tableState.numericColumns.includes(col)) {
                    td.classList.add('numeric');
                }
            }
            
            tr.appendChild(td);
        });
        
        tableBody.appendChild(tr);
    });

    // Update body cell left positions based on actual header widths
    setTimeout(() => {
        const headerCells = tableHeader.querySelectorAll('th');
        let leftPos = 0;
        const leftPositions = {};
        
        headerCells.forEach((th, idx) => {
            const col = finalOrder[idx];
            if (tableState.pinnedColumns.includes(col)) {
                leftPositions[col] = leftPos;
                th.style.left = leftPos + 'px';
                leftPos += th.offsetWidth;
            }
        });

        // Update body cells
        tableBody.querySelectorAll('tr').forEach(tr => {
            const cells = tr.querySelectorAll('td');
            cells.forEach((td, idx) => {
                const col = finalOrder[idx];
                if (tableState.pinnedColumns.includes(col)) {
                    td.style.left = leftPositions[col] + 'px';
                }
            });
        });
    }, 0);
}

// Initialize table view when dataset is loaded.
// Uses state.columnMetadata from /dataset/load instead of waiting for full data.
export function initializeTableView() {
    if (!state.columnMetadata) return;

    tableState.columns = state.columnMetadata.columns || [];
    tableState.numericColumns = state.columnMetadata.numeric_columns || [];
    tableState.columnOrder = [...tableState.columns];
    // Don't reset pinned columns if already set
    if (tableState.pinnedColumns.length > 0) {
        // Filter to only include columns that exist
        tableState.pinnedColumns = tableState.pinnedColumns.filter(c => tableState.columns.includes(c));
    }

    // Load table data first (fastest - just 100 rows)
    loadTableData();
    
    // Load summary data separately (may take slightly longer)
    loadSummaryData();
    
    renderActiveFilters();
}

// ===== Init wiring (one-shot from script.js DOMContentLoaded) =====

// Must be called BEFORE script.js's loadDatasets() so that
// parsePermalinkFromUrl() has populated tableState.pendingDataset /
// pendingLoadContext before loadDatasets's .then callback reads them.
// (loadDatasets is async; the synchronous setup all completes before
// any .then resolves, so the order between initTableView and
// loadDatasets within the same DOMContentLoaded callback is what
// matters.)
export function initTableView() {
    // Cache DOM refs
    tableBody = document.getElementById('table-body');
    tableHeader = document.getElementById('table-header');
    tableWrapper = document.getElementById('table-wrapper');
    tableEmptyState = document.getElementById('table-empty-state');
    summaryStatsToggleBtn = document.getElementById('summary-stats-toggle-btn');
    summaryStatsPanel = document.getElementById('summary-stats-panel');
    sidebarSectionSelect = document.getElementById('sidebar-section-select');
    rightPanelResizeHandle = document.getElementById('right-panel-resize-handle');
    rowDetailsBody = document.getElementById('row-details-body');
    statsTableContainer = document.getElementById('stats-table-container');
    statsSearchInput = document.getElementById('stats-search-input');
    statsLoadInitialBtn = document.getElementById('stats-load-initial-btn');

    // Stable delegated row click handling survives table rerenders.
    tableBody.addEventListener('click', (e) => {
        const rowEl = e.target.closest('tr[data-row-index]');
        if (!rowEl || !tableBody.contains(rowEl)) return;
        if (window.getSelection().toString().length > 0) return;
        const rowIndex = parseInt(rowEl.dataset.rowIndex, 10);
        if (Number.isNaN(rowIndex) || !tableState.lastData || !tableState.lastData[rowIndex]) return;
        setSelectedRow(rowIndex, tableState.lastData[rowIndex], { switchToDetails: true });
    });

    // Initialize tableState from URL (must happen before async loadDatasets resolves)
    parsePermalinkFromUrl();

    // Start summary stats sidebar visible (not collapsed)
    summaryStatsPanel.classList.remove('collapsed');
    summaryStatsToggleBtn.classList.add('active');

    // Toolbar event listener
    summaryStatsToggleBtn.addEventListener('click', toggleSummaryStats);

    if (sidebarSectionSelect) {
        sidebarSectionSelect.addEventListener('change', () => {
            setSidebarTab(sidebarSectionSelect.value, { ensureDefaults: true });
        });
    }

    // Right panel width init + resize handle
    initRightPanelWidth();

    if (rightPanelResizeHandle) {
        let isResizing = false;

        const onPointerMove = (e) => {
            if (!isResizing) return;
            const container = document.querySelector('.table-view-container');
            if (!container) return;
            const rect = container.getBoundingClientRect();
            const newWidth = rect.right - e.clientX;
            applyRightPanelWidth(newWidth, { persist: false });
        };

        const onPointerUp = () => {
            if (!isResizing) return;
            isResizing = false;
            rightPanelResizeHandle.classList.remove('resizing');
            document.body.classList.remove('resizing');
            applyRightPanelWidth(state.rightPanelWidthPx || 320, { persist: true });
            window.removeEventListener('pointermove', onPointerMove);
        };

        rightPanelResizeHandle.addEventListener('pointerdown', (e) => {
            if (!summaryStatsExpanded) return;
            e.preventDefault();
            isResizing = true;
            rightPanelResizeHandle.classList.add('resizing');
            document.body.classList.add('resizing');
            window.addEventListener('pointermove', onPointerMove);
            window.addEventListener('pointerup', onPointerUp, { once: true });
        });
    }

    window.addEventListener('resize', () => {
        if (state.rightPanelWidthPx) {
            applyRightPanelWidth(state.rightPanelWidthPx, { persist: false });
        }
    });

    // Resize handle double-click to toggle panel
    if (rightPanelResizeHandle) {
        rightPanelResizeHandle.addEventListener('dblclick', toggleSummaryStats);
    }

    // Copy permalink button
    document.getElementById('copy-permalink-btn').addEventListener('click', copyPermalink);

    // Pagination Event Listeners
    document.getElementById('first-page-btn').addEventListener('click', () => goToPage(1));
    document.getElementById('prev-page-btn').addEventListener('click', () => goToPage(tableState.currentPage - 1));
    document.getElementById('next-page-btn').addEventListener('click', () => goToPage(tableState.currentPage + 1));
    document.getElementById('last-page-btn').addEventListener('click', () => goToPage(tableState.totalPages));
    
    document.getElementById('page-input').addEventListener('change', (e) => {
        const page = parseInt(e.target.value, 10);
        if (page >= 1 && page <= tableState.totalPages) {
            goToPage(page);
        } else {
            e.target.value = tableState.currentPage;
        }
    });

    document.getElementById('page-size-selector').addEventListener('change', (e) => {
        tableState.pageSize = parseInt(e.target.value, 10);
        tableState.currentPage = 1;
        loadTableData();
    });

    // Summary Cards Event Listeners
    document.getElementById('missing-view-selector').addEventListener('change', updateMissingValuesCard);
    document.getElementById('distinct-column-selector').addEventListener('change', updateDistinctValuesCard);

    // Stats table scroll/search/load-initial buttons
    if (statsTableContainer) {
        statsTableContainer.addEventListener('scroll', () => {
            if (state.selectedSidebarTab !== 'stats') return;
            const nearBottom = statsTableContainer.scrollTop + statsTableContainer.clientHeight >= statsTableContainer.scrollHeight - 120;
            if (!nearBottom) return;
            // Backpressure + debounce: fast scrolling should not enqueue many calls
            if (summaryStatsState.scrollDebounceTimer) return;
            summaryStatsState.scrollDebounceTimer = setTimeout(() => {
                summaryStatsState.scrollDebounceTimer = null;
                if (state.selectedSidebarTab !== 'stats') return;
                if (summaryStatsState.renderedCount >= summaryStatsState.viewColumns.length) return;
                // Increase visible window, then fetch only what's needed
                summaryStatsState.renderedCount = Math.min(
                    summaryStatsState.renderedCount + summaryStatsState.batchSize,
                    summaryStatsState.viewColumns.length
                );
                renderSummaryStatsTable();
                const token = summaryStatsState.activeFetchToken;
                loadSummaryStatsBatch(token);
            }, 120);
        });
    }

    if (statsSearchInput) {
        statsSearchInput.addEventListener('input', () => {
            summaryStatsState.filterText = statsSearchInput.value || '';
            summaryStatsState.renderedCount = 0;
            summaryStatsState.fetchCursor = 0;
            summaryStatsState.attemptedCols.clear();
            updateSummaryStatsViewColumns();
            renderSummaryStatsTable();
            // Only auto-fetch if we have a dataset and either not-wide or user narrowed via search
            if (state.currentDataset && (summaryStatsState.baseColumns.length < 500 || summaryStatsState.filterText.trim().length > 0)) {
                ensureSummaryStatsInitialLoad();
            }
        });
    }

    if (statsLoadInitialBtn) {
        statsLoadInitialBtn.addEventListener('click', () => {
            summaryStatsState.allowWideAutoLoad = true;
            summaryStatsState.renderedCount = 0;
            summaryStatsState.fetchCursor = 0;
            summaryStatsState.attemptedCols.clear();
            ensureSummaryStatsInitialLoad();
        });
    }
}
