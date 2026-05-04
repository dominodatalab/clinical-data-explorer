// Shared state singleton for the Data Explorer frontend.
//
// Holds every top-level mutable variable that previously lived as a `let`
// declaration inside script.js's DOMContentLoaded callback. Field names are
// preserved exactly from their original `let` declarations so callers move
// over via a mechanical `name` -> `state.name` rename with no further
// behavior change. New fields will be added here as later refactor prompts
// extract feature modules out of the still-monolithic script.js.
//
// IMPORTANT: every consumer must import the same `state` object — do not
// destructure primitive fields into local variables (that would snapshot the
// value at import time and lose the live binding). Object-typed fields can
// be aliased locally because the alias still references the same object.

export const state = {
    // ===== DATASET / LOAD CONTEXT =====
    currentDataset: null,
    selectedDataset: null,

    // Snapshot/source identity of the most recently loaded file.
    // generatePermalink() uses this to encode the exact NetApp/dataset
    // snapshot a permalink should resolve back to — the file browser
    // listing alone only reflects the *latest* snapshot.
    lastLoadContext: null,

    // Extension mode: when accessed with ?projectId=X, use Domino API for
    // dataset access. URL params are read once at module load (same timing
    // as the original `let` initializers, which ran on DOMContentLoaded).
    extensionProjectId: new URLSearchParams(window.location.search).get('projectId') || null,

    // Dataset file context mode: when opened via "Open with..." on a
    // specific file. URL params:
    //   ?mountPointType=datasetFileContext&datasetId=X&datasetSnapshotId=Y&filePath=Z
    extensionDatasetId: new URLSearchParams(window.location.search).get('datasetId') || null,
    extensionSnapshotId: new URLSearchParams(window.location.search).get('datasetSnapshotId') || null,
    extensionFilePath: new URLSearchParams(window.location.search).get('filePath') || null,

    // NetApp deeplink mode. Two shapes:
    //   ?mountPointType=netAppVolume&netAppVolumeId=<uuid>&projectId=Z
    //     -> open the file browser scoped to that volume.
    //   ?mountPointType=netAppVolumeFileContext&netAppVolumeId=<uuid>
    //     &netAppVolumeSnapshotId=<latest|uuid>&filePath=<path>&projectId=Z
    //     -> auto-load the file from that snapshot (or the r/w head if
    //        the snapshot id is the synthetic 'latest').
    // mountPointType is also kept around so permalink generation can
    // round-trip the original deeplink.
    extensionMountPointType: new URLSearchParams(window.location.search).get('mountPointType') || null,
    extensionNetAppVolumeId: new URLSearchParams(window.location.search).get('netAppVolumeId') || null,
    extensionNetAppVolumeSnapshotId: new URLSearchParams(window.location.search).get('netAppVolumeSnapshotId') || null,

    // ===== FILE BROWSER STATE =====
    cachedDatasetListResponse: null,
    fileBrowserState: {
        isOpen: false,
        mode: null,            // 'local' | 'extension-project' | 'extension-dataset'
        sources: [],           // [{id, name, type:'dataset'|'netapp'|'local', volumeKey?, volumeId?}]
        selectedSource: null,
        snapshots: [],
        selectedSnapshot: null,
        activeSnapshotId: null,
        currentPath: '',
        entries: [],
        selectedFile: null,    // {name, path, fileName, sourceType, volumeKey?, snapshotId?}
        searchQuery: '',
        isSearchResult: false,
        loading: false,
        // True once the deeplink URL hints (snapshot id, file path) have
        // been applied to the modal once. Without this flag, every later
        // snapshot change or modal reopen would yank the user back to the
        // URL's original snapshot/folder/file. The hints are only useful
        // for the first browser-open after a deeplink load — after that
        // the user's manual choices should win.
        deeplinkConsumed: false,
    },

    // Column metadata from /dataset/load - used to initialize UI without
    // fetching all data. Shape:
    // { columns, numeric_columns, categorical_columns, date_columns,
    //   column_types, num_rows }
    columnMetadata: null,

    // ===== UI STATE =====
    selectedSidebarTab: 'missing',
    selectedRow: null,
    selectedRowIndex: null, // row index within current page
    selectedRowId: null,    // stable identifier for refresh/reload
    selectedRowContextKey: null,
    rightPanelWidthPx: null,

    // Chat configuration status
    chatStatus: {
        configured: null, // null = not checked, true = configured, false = not configured
        checked: false,
    },

    currentFilter: null, // { column: 'category_name', value: 'category_value' }
    barChartInstance: null,
    mainChartInstance: null,
    currentPlotMode: 'histogram', // 'histogram' or 'xy'

    // Loading state for individual components
    loadingState: {
        tableData: false,
        summaryCards: false,
        barChart: false,
        mainChart: false,
    },

    // ===== COLUMN LABEL STATE =====
    columnLabels: {}, // { column_name: human_readable_label }
    labelsAvailable: false,
    // Toggle state for showing human-readable labels (defaults to false /
    // unchecked to match the checkbox initial state).
    useLabels: false,

    // ===== GOVERNANCE STATE =====
    governanceState: {
        available: false,
        bundles: [],              // All bundles containing this dataset
        selectedBundle: null,     // Currently selected bundle
        bundleStages: [],         // Stages for the bundle
        bundleApprovals: [],      // Approvals for creating findings (at bundle level)
        designatedApprovers: [],  // Approvers designated for the bundle
        projectCollaborators: [], // All project collaborators for assignee selection
        policyVersionId: '',      // Required for creating findings
        currentStage: '',         // Current stage name
        currentUser: null,
    },
};
