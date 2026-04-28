// Filter UX (simple filter modal + expression filter modal + chip rendering).
//
// Owns:
//   - "Add filter" modal: column/operator/value form, value autocomplete,
//     and the apply/cancel/close handlers.
//   - "Expression filter" modal: SAS/R/Python syntax tabs, expression
//     validation against /api/table/expression_filter, and pre-populate
//     when re-opening with an active expression.
//   - Active-filter chips area (regular filter chips + expression chip)
//     and remove handlers per chip / "Clear all".
//
// Two exports:
//   - `initFilters({ tableState, loadTableData, loadSummaryData })` —
//     call once from script.js's DOMContentLoaded callback. Caches the
//     injected dependencies and wires every filter / expression event
//     listener. Same shape as `initFileBrowser` from `modules/file-browser.js`
//     because:
//       * `tableState` is the const object owned by script.js's
//         DOMContentLoaded scope (will migrate with `modules/table-view.js`
//         in P14/4.4g). Filter functions read tableState.numericColumns/
//         columns and read+write tableState.filters / .expressionFilter /
//         .currentPage. Object passed by reference, so every read/write
//         continues to operate on the same underlying object.
//       * `loadTableData` / `loadSummaryData` are still defined in
//         script.js's DOMContentLoaded scope and reload the table view
//         after filters change. Per ground rule #2 we don't rewire the
//         post-filter reload pipeline; we just call back into script.js.
//   - `renderActiveFilters()` — also exported because it's called from
//     three places outside the filter region (use-labels checkbox handler,
//     missing-values per-column "Filter to NA" handler, and after the
//     table view (re)initializes).
//
// Module-private: every other filter / expression / autocomplete helper
// (`openFilterModal`, `applyFilter`, `removeFilter`, `clearAllFilters`,
// `formatOperator`, `updateOperatorOptions`, `fetchAutocomplete`,
// `renderAutocomplete`, `openExpressionModal`, `closeExpressionModal`,
// `clearExpressionInput`, `updateExpressionPlaceholder`,
// `showExpressionError`, `hideExpressionError`, `applyExpressionFilter`,
// `removeExpressionFilter`, `escapeHtmlForAttr`). Mutable module state
// (`autocompleteTimeout`, `currentExpressionSyntax`) was previously
// declared at the top of script.js's DOMContentLoaded scope; it's
// feature-local to filters and now lives here.
//
// `escapeHtmlForAttr` is unused (it was already orphaned before this
// extraction — see the P11 session log note in REFACTOR_PROGRESS.md).
// Kept here per ground rule #5 ("do not delete code you don't
// understand"); the P11 note anticipated it would land in this module.
// A future cleanup pass can drop it once we're sure nothing reaches it
// via dynamic dispatch.
//
// DOM lookups for the cached refs happen inside `initFilters` (not at
// module load) because the listeners they own also wire there — keeps
// caching adjacent to wiring. Other DOM lookups inside event handlers
// continue to use `document.getElementById(...)` per file-browser.js's
// pattern.

import { state } from '../core/state.js';
import { apiUrl, fetchJson } from '../core/api.js';
import { escapeHtml } from '../core/dom.js';
import { openModal, closeModal, attachOverlayDismiss } from '../core/modals.js';
import { getDisplayName } from './column-labels.js';

// Injected dependencies (populated at initFilters)
let tableStateRef = null;
let loadTableDataFn = null;
let loadSummaryDataFn = null;

// Module-private mutable state
let autocompleteTimeout = null;
let currentExpressionSyntax = 'sas';

// Cached DOM elements (populated at initFilters)
let filterModal, filterColumnSelect, filterOperatorSelect, filterValueInput,
    filterValue2Input, filterValue2Group, filterValueGroup, autocompleteDropdown;
let expressionModal, expressionInput, expressionError, expressionErrorMessage;

export function initFilters({ tableState, loadTableData, loadSummaryData }) {
    tableStateRef = tableState;
    loadTableDataFn = loadTableData;
    loadSummaryDataFn = loadSummaryData;

    // Filter Modal Elements
    filterModal = document.getElementById('filter-modal-overlay');
    filterColumnSelect = document.getElementById('filter-column-select');
    filterOperatorSelect = document.getElementById('filter-operator-select');
    filterValueInput = document.getElementById('filter-value-input');
    filterValue2Input = document.getElementById('filter-value2-input');
    filterValue2Group = document.getElementById('filter-value2-group');
    filterValueGroup = document.getElementById('filter-value-group');
    autocompleteDropdown = document.getElementById('autocomplete-dropdown');

    // Filter Modal Event Listeners
    document.getElementById('add-filter-btn').addEventListener('click', openFilterModal);
    document.getElementById('filter-modal-close').addEventListener('click', closeFilterModal);
    document.getElementById('filter-cancel-btn').addEventListener('click', closeFilterModal);
    document.getElementById('filter-apply-btn').addEventListener('click', applyFilter);
    document.getElementById('clear-filters-btn').addEventListener('click', clearAllFilters);

    attachOverlayDismiss(filterModal, closeFilterModal);

    filterOperatorSelect.addEventListener('change', () => {
        const op = filterOperatorSelect.value;
        const isMissingOp = op === 'is_missing' || op === 'is_not_missing';
        filterValue2Group.style.display = op === 'between' ? 'block' : 'none';
        filterValueGroup.style.display = isMissingOp ? 'none' : 'block';
    });

    filterValueInput.addEventListener('input', () => {
        clearTimeout(autocompleteTimeout);
        autocompleteTimeout = setTimeout(fetchAutocomplete, 200);
    });

    filterValueInput.addEventListener('focus', () => {
        if (filterValueInput.value.length > 0 || filterColumnSelect.value) {
            fetchAutocomplete();
        }
    });

    filterValueInput.addEventListener('blur', () => {
        setTimeout(() => {
            autocompleteDropdown.classList.remove('visible');
        }, 200);
    });

    filterColumnSelect.addEventListener('change', () => {
        filterValueInput.value = '';
        autocompleteDropdown.classList.remove('visible');

        // Update operators based on column type
        const column = filterColumnSelect.value;
        const isNumeric = tableStateRef.numericColumns.includes(column);
        updateOperatorOptions(isNumeric);
    });

    // Expression Filter Modal Elements
    expressionModal = document.getElementById('expression-modal-overlay');
    expressionInput = document.getElementById('expression-input');
    expressionError = document.getElementById('expression-error');
    expressionErrorMessage = document.getElementById('expression-error-message');

    // Expression Filter Event Listeners
    document.getElementById('expression-filter-btn').addEventListener('click', openExpressionModal);
    document.getElementById('expression-modal-close').addEventListener('click', closeExpressionModal);
    document.getElementById('expression-cancel-btn').addEventListener('click', closeExpressionModal);
    document.getElementById('expression-apply-btn').addEventListener('click', applyExpressionFilter);
    document.getElementById('expression-clear-btn').addEventListener('click', clearExpressionInput);

    attachOverlayDismiss(expressionModal, closeExpressionModal);

    // Syntax tab switching
    document.querySelectorAll('.syntax-tab').forEach(tab => {
        tab.addEventListener('click', () => {
            document.querySelectorAll('.syntax-tab').forEach(t => t.classList.remove('active'));
            tab.classList.add('active');
            currentExpressionSyntax = tab.dataset.syntax;
            updateExpressionPlaceholder();
        });
    });
}

function openExpressionModal() {
    openModal(expressionModal);
    hideExpressionError();

    // Pre-populate with existing expression if one is active
    if (tableStateRef.expressionFilter) {
        expressionInput.value = tableStateRef.expressionFilter.expression;
        currentExpressionSyntax = tableStateRef.expressionFilter.syntax;
        // Update active tab
        document.querySelectorAll('.syntax-tab').forEach(tab => {
            tab.classList.toggle('active', tab.dataset.syntax === currentExpressionSyntax);
        });
    }

    updateExpressionPlaceholder();
    expressionInput.focus();
}

function closeExpressionModal() {
    closeModal(expressionModal);
    hideExpressionError();
}

function clearExpressionInput() {
    expressionInput.value = '';
    hideExpressionError();
}

function updateExpressionPlaceholder() {
    // Generic but representative examples for each syntax
    const placeholders = {
        sas: "AGE > 65 AND COLUMN = 'value'\nCOLUMN IN ('val1', 'val2')\nCOLUMN IS NOT MISSING",
        r: 'AGE > 65 & COLUMN == "value"\nCOLUMN %in% c("val1", "val2")\n!is.na(COLUMN)',
        python: 'AGE > 65 & COLUMN == "value"\nCOLUMN.isin(["val1", "val2"])\nCOLUMN.notna()'
    };
    expressionInput.placeholder = placeholders[currentExpressionSyntax] || placeholders.sas;
}

function showExpressionError(message) {
    expressionError.style.display = 'flex';
    expressionErrorMessage.textContent = message;
}

function hideExpressionError() {
    expressionError.style.display = 'none';
    expressionErrorMessage.textContent = '';
}

async function applyExpressionFilter() {
    const expression = expressionInput.value.trim();

    if (!expression) {
        // Clear expression filter if input is empty
        tableStateRef.expressionFilter = null;
        closeExpressionModal();
        tableStateRef.currentPage = 1;
        renderActiveFilters();
        loadTableDataFn();
        loadSummaryDataFn();
        return;
    }

    hideExpressionError();

    // Test the expression by making a request
    try {
        const requestBody = {
            expression: expression,
            syntax: currentExpressionSyntax,
            page: 1,
            page_size: 10,  // Small page for validation
            filters: tableStateRef.filters
        };

        const data = await fetchJson(apiUrl('table/expression_filter'), {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(requestBody)
        });

        if (data.error) {
            showExpressionError(data.error);
            return;
        }

        // Expression is valid - save and apply
        tableStateRef.expressionFilter = {
            expression: expression,
            syntax: currentExpressionSyntax
        };

        closeExpressionModal();
        tableStateRef.currentPage = 1;
        renderActiveFilters();
        loadTableDataFn();
        loadSummaryDataFn();

    } catch (e) {
        console.error('Error validating expression:', e);
        showExpressionError('Failed to validate expression. Please try again.');
    }
}

function removeExpressionFilter() {
    tableStateRef.expressionFilter = null;
    tableStateRef.currentPage = 1;
    renderActiveFilters();
    loadTableDataFn();
    loadSummaryDataFn();
}

function updateOperatorOptions(isNumeric) {
    const ops = filterOperatorSelect.options;
    const numericOps = ['gt', 'gte', 'lt', 'lte', 'between'];

    for (let i = 0; i < ops.length; i++) {
        const op = ops[i].value;
        if (numericOps.includes(op)) {
            ops[i].disabled = !isNumeric;
        }
    }
}

async function fetchAutocomplete() {
    const column = filterColumnSelect.value;
    const search = filterValueInput.value;

    if (!column) {
        autocompleteDropdown.classList.remove('visible');
        return;
    }

    try {
        const data = await fetchJson(apiUrl(`table/column_values/${encodeURIComponent(column)}?search=${encodeURIComponent(search)}&limit=15`));

        if (data.values && data.values.length > 0) {
            renderAutocomplete(data.values, search);
        } else {
            autocompleteDropdown.classList.remove('visible');
        }
    } catch (e) {
        console.error('Autocomplete error:', e);
    }
}

function renderAutocomplete(values, search) {
    autocompleteDropdown.innerHTML = '';

    values.forEach(value => {
        const item = document.createElement('div');
        item.className = 'autocomplete-item';

        // Highlight matching text
        if (search) {
            const idx = value.toLowerCase().indexOf(search.toLowerCase());
            if (idx >= 0) {
                item.innerHTML =
                    value.substring(0, idx) +
                    '<mark>' + value.substring(idx, idx + search.length) + '</mark>' +
                    value.substring(idx + search.length);
            } else {
                item.textContent = value;
            }
        } else {
            item.textContent = value;
        }

        item.addEventListener('mousedown', () => {
            filterValueInput.value = value;
            autocompleteDropdown.classList.remove('visible');
        });

        autocompleteDropdown.appendChild(item);
    });

    autocompleteDropdown.classList.add('visible');
}

function openFilterModal() {
    // Populate column options with human-readable labels, sorted alphabetically by display name
    filterColumnSelect.innerHTML = '<option value="">Select column...</option>';
    [...tableStateRef.columns].sort((a, b) =>
        getDisplayName(a).toLowerCase().localeCompare(getDisplayName(b).toLowerCase())
    ).forEach(col => {
        const option = document.createElement('option');
        option.value = col;
        const displayName = getDisplayName(col);
        option.textContent = displayName !== col ? `${displayName} (${col})` : col;
        filterColumnSelect.appendChild(option);
    });

    // Reset form
    filterColumnSelect.value = '';
    filterOperatorSelect.value = 'is';
    filterValueInput.value = '';
    filterValue2Input.value = '';
    filterValue2Group.style.display = 'none';
    filterValueGroup.style.display = 'block';
    autocompleteDropdown.classList.remove('visible');

    openModal(filterModal);
}

function closeFilterModal() {
    closeModal(filterModal);
}

function applyFilter() {
    const column = filterColumnSelect.value;
    const operator = filterOperatorSelect.value;
    const value = filterValueInput.value;
    const value2 = filterValue2Input.value;

    if (!column) {
        alert('Please select a column');
        return;
    }

    // Missing value operators don't require a value
    const isMissingOp = operator === 'is_missing' || operator === 'is_not_missing';

    if (!value && !isMissingOp && operator !== 'between') {
        alert('Please enter a value');
        return;
    }

    if (operator === 'between' && (!value || !value2)) {
        alert('Please enter both values for between filter');
        return;
    }

    const filter = { column, operator, value: isMissingOp ? null : value };
    if (operator === 'between') {
        filter.value2 = value2;
    }

    tableStateRef.filters.push(filter);
    tableStateRef.currentPage = 1;

    closeFilterModal();
    renderActiveFilters();
    loadTableDataFn();
    loadSummaryDataFn();
}

function removeFilter(index) {
    tableStateRef.filters.splice(index, 1);
    tableStateRef.currentPage = 1;
    renderActiveFilters();
    loadTableDataFn();
    loadSummaryDataFn();
}

function clearAllFilters() {
    tableStateRef.filters = [];
    tableStateRef.expressionFilter = null;  // Also clear expression filter
    tableStateRef.currentPage = 1;
    renderActiveFilters();
    loadTableDataFn();
    loadSummaryDataFn();
}

export function renderActiveFilters() {
    const container = document.getElementById('active-filters');
    container.innerHTML = '';

    const hasFilters = tableStateRef.filters.length > 0;
    const hasExpression = tableStateRef.expressionFilter !== null;

    if (!hasFilters && !hasExpression) {
        return;
    }

    // Render expression filter chip first (if any)
    if (hasExpression) {
        const exprChip = document.createElement('div');
        exprChip.className = 'active-expression-chip';

        const syntaxLabels = { sas: 'SAS', r: 'R', python: 'Python' };
        const syntaxLabel = syntaxLabels[tableStateRef.expressionFilter.syntax] || 'Expression';

        exprChip.innerHTML = `
            <span class="expression-syntax-badge">${syntaxLabel}</span>
            <span class="expression-text" title="${escapeHtml(tableStateRef.expressionFilter.expression)}">${escapeHtml(tableStateRef.expressionFilter.expression)}</span>
            <button class="remove-expression" title="Remove expression filter">&times;</button>
        `;

        exprChip.querySelector('.remove-expression').addEventListener('click', removeExpressionFilter);
        // Click on chip to edit
        exprChip.querySelector('.expression-text').addEventListener('click', openExpressionModal);
        container.appendChild(exprChip);
    }

    // Render regular filter chips
    tableStateRef.filters.forEach((filter, index) => {
        const chip = document.createElement('div');
        chip.className = 'filter-chip';

        const displayCol = getDisplayName(filter.column);
        let text;
        if (filter.operator === 'is_missing' || filter.operator === 'is_not_missing') {
            text = `${displayCol} ${formatOperator(filter.operator)}`;
        } else if (filter.operator === 'between') {
            text = `${displayCol} between "${filter.value}" and "${filter.value2}"`;
        } else {
            text = `${displayCol} ${formatOperator(filter.operator)} "${filter.value}"`;
        }

        chip.innerHTML = `
            <span>${text}</span>
            <button class="filter-chip-remove" data-index="${index}">&times;</button>
        `;

        chip.querySelector('.filter-chip-remove').addEventListener('click', () => removeFilter(index));
        container.appendChild(chip);
    });
}

// Unused — see module docstring. Kept here per ground rule #5; the P11
// session log explicitly anticipated this helper landing here.
function escapeHtmlForAttr(text) {
    return text.replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/'/g, '&#39;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

function formatOperator(op) {
    const opNames = {
        'is': '=',
        'is_not': '≠',
        'contains': 'contains',
        'not_contains': 'not contains',
        'gt': '>',
        'gte': '≥',
        'lt': '<',
        'lte': '≤',
        'between': 'between',
        'is_missing': 'is missing',
        'is_not_missing': 'is not missing'
    };
    return opNames[op] || op;
}
