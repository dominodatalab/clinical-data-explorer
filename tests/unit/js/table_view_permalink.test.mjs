import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { test } from 'node:test';
import vm from 'node:vm';

const tableViewSourcePath = new URL('../../../chat_ui/modules/table-view.js', import.meta.url);
const tableViewSource = await readFile(tableViewSourcePath, 'utf8');

async function loadTableViewModule({
    href = 'https://example.test/apps/cde/?projectId=proj-1&row=stale-row',
    stateOverrides = {},
} = {}) {
    const state = {
        currentDataset: null,
        extensionDatasetId: null,
        extensionSnapshotId: null,
        extensionFilePath: null,
        extensionProjectId: null,
        lastLoadContext: null,
        ...stateOverrides,
    };
    const context = vm.createContext({
        console: { error() {}, log() {}, warn() {} },
        URL,
        URLSearchParams,
        window: { location: new URL(href) },
        __state: state,
    });

    const stubModules = new Map([
        ['../core/state.js', new vm.SyntheticModule(['state'], function () {
            this.setExport('state', state);
        }, { context })],
        ['../core/api.js', new vm.SyntheticModule([
            'apiUrl',
            'fetchJson',
            'fetchWithStatusCheck',
            'getApiErrorMessage',
            'throwIfApiError',
        ], function () {
            this.setExport('apiUrl', endpoint => `/${endpoint}`);
            this.setExport('fetchJson', async () => ({}));
            this.setExport('fetchWithStatusCheck', async () => ({ json: async () => ({}) }));
            this.setExport('getApiErrorMessage', async () => 'Request failed');
            this.setExport('throwIfApiError', data => data);
        }, { context })],
        ['../core/dom.js', new vm.SyntheticModule(['escapeHtml', 'showToast'], function () {
            this.setExport('escapeHtml', value => String(value));
            this.setExport('showToast', () => {});
        }, { context })],
        ['../core/error-banner.js', new vm.SyntheticModule(['showErrorBanner'], function () {
            this.setExport('showErrorBanner', () => {});
        }, { context })],
        ['./column-labels.js', new vm.SyntheticModule(['getDisplayName', 'getDisplayNameWithOriginal'], function () {
            this.setExport('getDisplayName', value => value);
            this.setExport('getDisplayNameWithOriginal', value => value);
        }, { context })],
        ['./filters.js', new vm.SyntheticModule(['renderActiveFilters'], function () {
            this.setExport('renderActiveFilters', () => {});
        }, { context })],
    ]);

    const module = new vm.SourceTextModule(tableViewSource, {
        context,
        identifier: String(tableViewSourcePath),
    });
    await module.link(specifier => {
        const stub = stubModules.get(specifier);
        if (!stub) {
            throw new Error(`Unexpected import: ${specifier}`);
        }
        return stub;
    });
    await module.evaluate();
    return { namespace: module.namespace, state };
}

test('buildPermalinkUrl preserves Domino project dataset identity for copied links', async () => {
    const { namespace, state } = await loadTableViewModule({
        href: 'https://example.test/apps/cde/?projectId=proj-1&row=stale-row',
        stateOverrides: {
            currentDataset: 'AE SAS 7 BDAT/ae.sas7bdat',
            extensionProjectId: 'proj-1',
            lastLoadContext: {
                sourceType: 'dataset',
                datasetName: 'AE SAS 7 BDAT/ae.sas7bdat',
                datasetId: 'dataset-1',
                snapshotId: 'snapshot-1',
            },
        },
    });
    namespace.tableState.filters = [{ column: 'USUBJID', operator: 'equals', value: '01' }];
    namespace.tableState.expressionFilter = { expression: 'AGE > 18', syntax: 'sas' };

    const url = namespace.buildPermalinkUrl();

    assert.equal(url.searchParams.get('dataset'), state.currentDataset);
    assert.equal(url.searchParams.get('projectId'), 'proj-1');
    assert.equal(url.searchParams.get('loadDatasetId'), 'dataset-1');
    assert.equal(url.searchParams.get('snapshotId'), 'snapshot-1');
    assert.equal(url.searchParams.get('expr'), 'AGE > 18');
    assert.equal(url.searchParams.get('exprSyntax'), 'sas');
    assert.equal(url.searchParams.has('row'), false);
    assert.deepEqual(JSON.parse(url.searchParams.get('filters')), namespace.tableState.filters);
});

test('buildPermalinkUrl preserves dataset identity even when source type is absent', async () => {
    const { namespace } = await loadTableViewModule({
        href: 'https://example.test/apps/cde/?projectId=proj-1',
        stateOverrides: {
            currentDataset: 'Clinical_Data/clinical.csv',
            extensionProjectId: 'proj-1',
            lastLoadContext: {
                datasetName: 'Clinical_Data/clinical.csv',
                datasetId: 'dataset-clinical',
                snapshotId: 'snapshot-clinical',
            },
        },
    });

    const url = namespace.buildPermalinkUrl();

    assert.equal(url.searchParams.get('dataset'), 'Clinical_Data/clinical.csv');
    assert.equal(url.searchParams.get('projectId'), 'proj-1');
    assert.equal(url.searchParams.get('loadDatasetId'), 'dataset-clinical');
    assert.equal(url.searchParams.get('snapshotId'), 'snapshot-clinical');
});

test('buildPermalinkUrl preserves dataset file context params', async () => {
    const { namespace } = await loadTableViewModule({
        href: 'https://example.test/apps/cde/?mountPointType=datasetFileContext&datasetId=dataset-1&datasetSnapshotId=snapshot-1&filePath=nested%2Fae.csv',
        stateOverrides: {
            currentDataset: 'AE/nested/ae.csv',
            extensionDatasetId: 'dataset-1',
            extensionSnapshotId: 'snapshot-1',
            extensionFilePath: 'nested/ae.csv',
        },
    });

    const url = namespace.buildPermalinkUrl();

    assert.equal(url.searchParams.get('dataset'), 'AE/nested/ae.csv');
    assert.equal(url.searchParams.get('mountPointType'), 'datasetFileContext');
    assert.equal(url.searchParams.get('datasetId'), 'dataset-1');
    assert.equal(url.searchParams.get('datasetSnapshotId'), 'snapshot-1');
    assert.equal(url.searchParams.get('filePath'), 'nested/ae.csv');
});

test('buildPermalinkUrl preserves NetApp volume metadata for copied links', async () => {
    const { namespace } = await loadTableViewModule({
        href: 'https://example.test/apps/cde/?projectId=proj-1',
        stateOverrides: {
            currentDataset: 'Safety Volume/reports/adlb.csv',
            extensionProjectId: 'proj-1',
            lastLoadContext: {
                datasetName: 'Safety Volume/reports/adlb.csv',
                volumeKey: 'netapp-volume-Safety-123',
                volumeId: 'volume-123',
                snapshotId: 'snapshot-123',
                snapshotVersion: 7,
            },
        },
    });

    const url = namespace.buildPermalinkUrl();

    assert.equal(url.searchParams.get('dataset'), 'Safety Volume/reports/adlb.csv');
    assert.equal(url.searchParams.get('projectId'), 'proj-1');
    assert.equal(url.searchParams.get('volumeKey'), 'netapp-volume-Safety-123');
    assert.equal(url.searchParams.get('volumeId'), 'volume-123');
    assert.equal(url.searchParams.get('snapshotId'), 'snapshot-123');
    assert.equal(url.searchParams.get('snapshotVersion'), '7');
    assert.equal(url.searchParams.has('loadDatasetId'), false);
});
