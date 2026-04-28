// Column label mapping helpers for the Data Explorer frontend.
//
// Owns three responsibilities:
//   1. `loadColumnLabels()` — fetch the optional column_labels.csv-derived map
//      from the backend, populate `state.columnLabels`/`state.labelsAvailable`,
//      and reveal the "Show friendly names" toggle when labels are available.
//   2. `getDisplayName(columnName)` — return either the human-friendly label
//      (when the user has the toggle on AND a label exists for the column) or
//      the raw column name. Pure read of `state` — no DOM, no fetch.
//   3. `getDisplayNameWithOriginal(columnName)` — same gate, but returns a
//      `{ display, original, hasLabel }` tuple used by table headers (and a
//      handful of summary/sidebar renders) that want to show the friendly
//      name as the primary label and the raw column as a subtitle.
//
// The `useLabelsCheckbox` change handler — which calls back into table /
// explore / filter / summary-stats renderers — intentionally stays in
// script.js for now: it touches code that hasn't been extracted yet and
// would otherwise force a circular import. It'll move with whichever
// module ends up owning the cross-cutting "rerender after toggle" sequence.
//
// DOM lookup happens at module load. JS modules are deferred under the HTML
// spec, so the document is fully parsed by the time this file evaluates —
// the same guarantee `script.js`'s `DOMContentLoaded` callback was using.

import { state } from '../core/state.js';
import { apiUrl, fetchJson } from '../core/api.js';

const labelToggleContainer = document.getElementById('label-toggle-container');

export function loadColumnLabels() {
    fetchJson(apiUrl('column_labels'))
        .then(data => {
            if (data.available && data.labels) {
                state.columnLabels = data.labels;
                state.labelsAvailable = true;
                labelToggleContainer.style.display = 'flex';
                console.log(`Loaded ${Object.keys(state.columnLabels).length} column labels`);
            } else {
                state.labelsAvailable = false;
                labelToggleContainer.style.display = 'none';
                console.log('Column labels not available');
            }
        })
        .catch(error => {
            console.error('Error loading column labels:', error);
            state.labelsAvailable = false;
            labelToggleContainer.style.display = 'none';
        });
}

export function getDisplayName(columnName) {
    if (state.useLabels && state.labelsAvailable && state.columnLabels[columnName]) {
        return state.columnLabels[columnName];
    }
    return columnName;
}

export function getDisplayNameWithOriginal(columnName) {
    if (state.useLabels && state.labelsAvailable && state.columnLabels[columnName]) {
        return {
            display: state.columnLabels[columnName],
            original: columnName,
            hasLabel: true
        };
    }
    return {
        display: columnName,
        original: columnName,
        hasLabel: false
    };
}
