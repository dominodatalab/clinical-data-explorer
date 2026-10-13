// Chat tool settings page (served at /admin/chat-tools, separate from the SPA).
//
// Lets editors of the extension's project browse every data tool the chat agent can
// reach, try each one against a built-in clinical sample, and choose one of three
// access modes per tool. The tool list, descriptions and forms all come from the
// server's live MCP catalog; nothing about specific tools is hard-coded here.
//
// Every value from the server (tool names, descriptions, outputs) is inserted with
// `textContent`; the only HTML is sanitized Markdown for tool descriptions.

import { getBaseUrl, fetchJson, getApiErrorMessage, getApiErrorPayload } from '../core/api.js';
import { renderMarkdown } from '../modules/markdown.js';
import { renderToolOutput } from '../modules/tool-output.js';
import { buildArgs, fieldsFromSchema, initialValues, parameterRows } from './schema-form.js';
import {
    FILTERS,
    MODES,
    buildSaveBody,
    createDraft,
    filterCounts,
    modeLabel,
    pendingChanges,
    visibleTools,
} from './policy-state.js';

// API calls are relative to the app root, not to this page's /admin/chat-tools URL.
const BASE = getBaseUrl('admin/chat-tools');
const apiUrl = path => `${BASE}admin/api/chat-tools${path}`;

const LARGE_OUTPUT_TOKENS = 5000;
const ARGS_STORAGE_PREFIX = 'chatToolAdmin.args.';

const state = {
    server: null,
    draft: null,
    selected: null,
    search: '',
    filter: 'all',
    sample: { loaded: false, columns: [], label: '', error: null },
    values: {},       // tool name -> form text, kept while switching between tools
    run: {},          // tool name -> last preview result
    running: false,
    saving: false,
    notice: null,     // { kind, text, action? } from the last save/reset
};

const el = id => document.getElementById(id);

function make(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
}

// ----- storage (per tab; may be unavailable) -----

function loadSavedArgs(toolName) {
    try {
        return JSON.parse(window.sessionStorage.getItem(ARGS_STORAGE_PREFIX + toolName) || '{}') || {};
    } catch {
        return {};
    }
}

function saveArgs(toolName, args) {
    try {
        window.sessionStorage.setItem(ARGS_STORAGE_PREFIX + toolName, JSON.stringify(args));
    } catch {
        // Remembering the last arguments is a convenience only.
    }
}

// ----- API -----

async function errorText(error, fallback) {
    const payload = await getApiErrorPayload(error);
    return (payload && payload.error) || (await getApiErrorMessage(error, fallback));
}

async function sendJson(method, path, body) {
    return fetchJson(apiUrl(path), {
        method,
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body || {}),
    });
}

async function loadServerState() {
    state.server = await fetchJson(apiUrl(''));
    state.draft = createDraft(state.server);
    if (!state.server.tools.some(tool => tool.name === state.selected)) {
        state.selected = state.server.tools.length ? state.server.tools[0].name : null;
    }
}

async function loadSample() {
    try {
        const data = await sendJson('POST', '/preview/dataset');
        state.sample = { loaded: true, columns: data.columns || [], label: data.label || '', error: null };
    } catch (error) {
        state.sample = { loaded: false, columns: [], label: '', error: await errorText(error, 'Could not load the preview sample.') };
    }
    renderBanners();
    renderDetail();
}

// ----- derived state -----

const toolByName = name => (state.server ? state.server.tools.find(tool => tool.name === name) : null);
const changes = () => (state.server && state.draft ? pendingChanges(state.server, state.draft) : []);

function canSave() {
    const server = state.server;
    return Boolean(
        server
        && !state.saving
        && !server.policy_error
        && !server.catalog_error
        && server.store.durable
        && server.policy.revision !== null,
    );
}

// ----- banners -----

function banner(kind, text, action) {
    const node = make('div', `banner banner-${kind}`);
    node.setAttribute('role', kind === 'error' ? 'alert' : 'status');
    node.appendChild(make('p', '', text));
    if (action) {
        const button = make('button', 'btn-secondary', action.label);
        button.type = 'button';
        button.dataset.testid = action.testid;
        button.addEventListener('click', action.run);
        node.appendChild(button);
    }
    return node;
}

function renderBanners() {
    const root = el('banners');
    root.replaceChildren();
    const server = state.server;
    if (!server) return;

    if (!server.store.durable) {
        const banner_ = banner('error', `Changes can't be saved. ${server.store.error || ''}`.trim());
        banner_.dataset.testid = 'banner-store';
        root.appendChild(banner_);
    }
    if (server.policy_error) {
        const node = banner('error', `The saved settings are unreadable, so the chat assistant has no data tools until they are fixed. ${server.policy_error}`, {
            label: 'Reset to defaults',
            testid: 'reset-button',
            run: resetToDefaults,
        });
        node.dataset.testid = 'banner-policy';
        root.appendChild(node);
    }
    if (server.catalog_error) {
        const node = banner('error', `${server.catalog_error}. The tool list is unavailable and settings can't be changed until the MCP server is reachable.`, {
            label: 'Retry',
            testid: 'retry-button',
            run: reload,
        });
        node.dataset.testid = 'banner-catalog';
        root.appendChild(node);
    }
    if (state.sample.error) {
        const node = banner('warning', `${state.sample.error} Previews are unavailable.`);
        node.dataset.testid = 'banner-sample';
        root.appendChild(node);
    }
    if (state.notice) {
        const node = banner(state.notice.kind, state.notice.text, state.notice.action);
        node.dataset.testid = `banner-${state.notice.kind}`;
        root.appendChild(node);
    }
}

// ----- mode control -----

function buildModeControl(toolName, groupPrefix, title) {
    const group = make('fieldset', 'mode-control');
    group.setAttribute('role', 'radiogroup');
    group.setAttribute('aria-label', `Access mode for ${title}`);
    MODES.forEach(mode => {
        const label = make('label', 'mode-option');
        label.title = mode.description;
        const input = document.createElement('input');
        input.type = 'radio';
        input.name = `${groupPrefix}-${toolName}`;
        input.value = mode.value;
        input.checked = state.draft.modes[toolName] === mode.value;
        input.dataset.testid = `mode-${mode.value}`;
        input.dataset.tool = toolName;
        input.addEventListener('change', () => onModeChange(toolName, mode.value));
        label.appendChild(input);
        label.appendChild(make('span', '', mode.label));
        group.appendChild(label);
    });
    return group;
}

function onModeChange(toolName, mode) {
    state.draft.modes[toolName] = mode;
    document.querySelectorAll(`input[type="radio"][data-tool="${CSS.escape(toolName)}"]`).forEach(input => {
        input.checked = input.value === mode;
    });
    document.querySelectorAll('.tool-row').forEach(row => {
        if (row.dataset.toolName === toolName) row.dataset.mode = mode;
    });
    renderChips();
    renderUnsavedBar();
}

// ----- list -----

function renderChips() {
    const root = el('filter-chips');
    const counts = filterCounts(state.server, state.draft);
    root.replaceChildren();
    FILTERS.forEach(filter => {
        const chip = make('button', 'chip', `${filter.label} (${counts[filter.key]})`);
        chip.type = 'button';
        chip.dataset.testid = `filter-${filter.key}`;
        chip.setAttribute('aria-pressed', String(state.filter === filter.key));
        chip.addEventListener('click', () => {
            state.filter = filter.key;
            renderChips();
            renderList();
        });
        root.appendChild(chip);
    });
}

function renderList() {
    const list = el('tool-list');
    list.replaceChildren();
    const showingStale = state.filter === 'stale';
    const tools = visibleTools(state.server, state.draft, { search: state.search, filter: state.filter });

    tools.forEach(tool => {
        const row = make('li', 'tool-row');
        row.dataset.testid = 'admin-tool-row';
        row.dataset.toolName = tool.name;
        row.dataset.mode = state.draft.modes[tool.name];
        if (tool.name === state.selected) row.setAttribute('aria-current', 'true');

        const main = make('button', 'tool-row-main');
        main.type = 'button';
        main.dataset.testid = 'select-tool';
        main.appendChild(make('span', 'tool-title', tool.title));
        main.appendChild(make('code', 'tool-name', tool.name));
        if (tool.is_new) {
            const badge = make('span', 'badge', 'New, using default');
            badge.dataset.testid = 'new-badge';
            main.appendChild(badge);
        }
        main.addEventListener('click', () => selectTool(tool.name));
        row.appendChild(main);
        row.appendChild(buildModeControl(tool.name, 'mode-list', tool.title));
        list.appendChild(row);
    });

    el('empty-list').hidden = tools.length > 0 || showingStale;
    renderStale();
}

function renderStale() {
    const stale = (state.server.stale || []).filter(entry => !state.draft.removedStale.has(entry.name));
    el('stale-section').hidden = stale.length === 0;
    const list = el('stale-list');
    list.replaceChildren();
    stale.forEach(entry => {
        const row = make('li', 'stale-row');
        row.dataset.testid = 'stale-row';
        row.dataset.toolName = entry.name;
        const text = make('span');
        text.appendChild(make('code', '', entry.name));
        text.appendChild(make('span', 'muted', ` · ${modeLabel(entry.mode)} · Stale`));
        row.appendChild(text);
        const remove = make('button', 'btn-secondary', 'Remove');
        remove.type = 'button';
        remove.dataset.testid = 'delete-stale';
        remove.setAttribute('aria-label', `Remove stale setting for ${entry.name}`);
        remove.addEventListener('click', () => {
            state.draft.removedStale.add(entry.name);
            renderChips();
            renderStale();
            renderUnsavedBar();
        });
        row.appendChild(remove);
        list.appendChild(row);
    });
}

function selectTool(name) {
    state.selected = name;
    document.querySelectorAll('.tool-row').forEach(row => {
        if (row.dataset.toolName === name) row.setAttribute('aria-current', 'true');
        else row.removeAttribute('aria-current');
    });
    renderDetail();
}

// ----- detail -----

function section(title) {
    const wrapper = make('section');
    wrapper.appendChild(make('h2', '', title));
    return wrapper;
}

function renderParameters(tool) {
    const rows = parameterRows(tool.input_schema);
    const wrapper = section('Parameters');
    if (rows.length === 0) {
        wrapper.appendChild(make('p', 'muted', 'This tool takes no parameters.'));
        return wrapper;
    }
    const table = make('table', 'param-table');
    table.dataset.testid = 'param-table';
    const head = make('tr');
    ['Name', 'Type', 'Required', 'Default', 'Description'].forEach(label => head.appendChild(make('th', '', label)));
    table.appendChild(make('thead')).appendChild(head);
    const body = make('tbody');
    rows.forEach(row => {
        const tr = make('tr');
        const name = make('td', 'param-name', row.name);
        name.style.paddingLeft = `${8 + row.depth * 16}px`;
        tr.appendChild(name);
        tr.appendChild(make('td', '', row.type));
        tr.appendChild(make('td', '', row.required ? 'Yes' : 'No'));
        tr.appendChild(make('td', 'mono', row.default));
        tr.appendChild(make('td', '', row.description));
        body.appendChild(tr);
    });
    table.appendChild(body);
    wrapper.appendChild(table);
    return wrapper;
}

function renderDeclaredOutput(tool) {
    const wrapper = section('Declared output');
    if (tool.declared_output_example === null || tool.declared_output_example === undefined) {
        wrapper.appendChild(make('p', 'muted', 'No output schema is declared for this tool. Run it below to see a real result.'));
        return wrapper;
    }
    const example = typeof tool.declared_output_example === 'string'
        ? tool.declared_output_example
        : JSON.stringify(tool.declared_output_example, null, 2);
    wrapper.appendChild(make('pre', 'pre-block', example));
    return wrapper;
}

function currentValues(tool, fields) {
    if (!state.values[tool.name]) {
        state.values[tool.name] = initialValues(fields, loadSavedArgs(tool.name), tool.example_args || {});
    }
    return state.values[tool.name];
}

const columnsListId = tool => `columns-${tool.name}`;

// One shared <datalist> of the sample's columns per form (ids must be unique).
function buildColumnsDatalist(tool) {
    const datalist = document.createElement('datalist');
    datalist.id = columnsListId(tool);
    state.sample.columns.forEach(column => datalist.appendChild(Object.assign(document.createElement('option'), { value: column })));
    return datalist;
}

function buildField(tool, field, values) {
    const wrapper = make('div', 'field');
    const id = `field-${tool.name}-${field.name}`;
    const label = make('label', '', field.name);
    label.htmlFor = id;
    if (field.required) {
        const mark = make('span', 'required-mark', ' *');
        mark.setAttribute('aria-label', 'required');
        label.appendChild(mark);
    }
    wrapper.appendChild(label);

    let input;
    if (field.kind === 'enum' || field.kind === 'boolean') {
        input = document.createElement('select');
        const options = field.kind === 'boolean' ? ['true', 'false'] : field.enum.map(String);
        input.appendChild(Object.assign(document.createElement('option'), { value: '', textContent: field.required ? 'Choose…' : '(not set)' }));
        options.forEach(option => input.appendChild(Object.assign(document.createElement('option'), { value: option, textContent: option })));
    } else if (field.kind === 'json') {
        input = document.createElement('textarea');
        input.rows = 3;
        input.spellcheck = false;
        input.placeholder = 'JSON';
    } else {
        input = document.createElement('input');
        input.type = 'text';
        input.autocomplete = 'off';
        if (field.kind === 'number' || field.kind === 'integer') input.inputMode = 'decimal';
        if (field.kind === 'array') input.placeholder = 'comma separated';
        if (field.isColumn && state.sample.columns.length) {
            input.setAttribute('list', columnsListId(tool));
        }
    }
    input.id = id;
    input.name = field.name;
    input.value = values[field.name] ?? '';
    input.dataset.testid = `arg-${field.name}`;
    input.addEventListener('input', () => {
        values[field.name] = input.value;
        const error = wrapper.querySelector('.field-error');
        if (error) error.remove();
    });
    wrapper.insertBefore(input, null);

    const hint = [field.type, field.description].filter(Boolean).join(' · ');
    if (hint) wrapper.appendChild(make('span', 'hint', hint));
    return wrapper;
}

function renderTryIt(tool) {
    const wrapper = make('section', 'try-it');
    wrapper.appendChild(make('h2', '', 'Try it'));
    const note = state.sample.loaded
        ? state.sample.label
        : 'Runs against a built-in clinical sample, never your own data.';
    wrapper.appendChild(make('p', 'muted', note));

    const fields = fieldsFromSchema(tool.input_schema);
    const values = currentValues(tool, fields);
    const form = make('form', 'try-form');
    form.dataset.testid = 'try-it-form';
    form.noValidate = true;
    fields.forEach(field => form.appendChild(buildField(tool, field, values)));
    if (state.sample.columns.length && fields.some(field => field.isColumn)) {
        form.appendChild(buildColumnsDatalist(tool));
    }

    const actions = make('div', 'try-actions');
    const run = make('button', 'btn-primary', state.running ? 'Running…' : 'Run');
    run.type = 'submit';
    run.disabled = state.running || Boolean(state.sample.error);
    run.dataset.testid = 'run-button';
    actions.appendChild(run);
    if (tool.example_args) {
        const reset = make('button', 'btn-secondary', 'Use example');
        reset.type = 'button';
        reset.dataset.testid = 'use-example';
        reset.addEventListener('click', () => {
            delete state.values[tool.name];
            state.values[tool.name] = initialValues(fields, {}, tool.example_args);
            renderDetail();
        });
        actions.appendChild(reset);
    }
    form.appendChild(actions);
    form.addEventListener('submit', event => {
        event.preventDefault();
        runPreview(tool, fields, values, form);
    });
    wrapper.appendChild(form);

    const output = make('div', 'output-panel');
    output.dataset.testid = 'output-panel';
    output.id = 'output-panel';
    wrapper.appendChild(output);
    renderOutput(tool, output);
    return wrapper;
}

function formatBytes(bytes) {
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function renderOutput(tool, container) {
    container.replaceChildren();
    const result = state.run[tool.name];
    if (!result) return;

    if (result.error) {
        const box = make('div', 'output-error');
        box.dataset.testid = 'output-error';
        box.appendChild(make('strong', '', 'The tool returned an error'));
        box.appendChild(make('pre', 'pre-block', result.error));
        container.appendChild(box);
        return;
    }

    const meta = make('div', 'output-meta');
    meta.dataset.testid = 'output-meta';
    meta.appendChild(make('span', '', formatBytes(result.size_bytes)));
    meta.appendChild(make('span', '', `about ${result.approx_tokens.toLocaleString()} tokens`));
    meta.appendChild(make('span', '', `${result.elapsed_ms} ms`));
    if (result.approx_tokens >= LARGE_OUTPUT_TOKENS) {
        meta.appendChild(make('span', 'output-warning', 'Large output: this could use a lot of the AI model\'s context.'));
    }
    const toggleLabel = make('label');
    const toggle = document.createElement('input');
    toggle.type = 'checkbox';
    toggle.dataset.testid = 'raw-toggle';
    toggle.checked = Boolean(result.showRaw);
    toggleLabel.appendChild(toggle);
    toggleLabel.appendChild(document.createTextNode(' Raw (what the model receives)'));
    meta.appendChild(toggleLabel);
    container.appendChild(meta);

    const body = make('div');
    body.dataset.testid = 'output-body';
    const paint = () => {
        body.replaceChildren(result.showRaw
            ? make('pre', 'pre-block', result.content_text)
            : renderToolOutput(result.content));
    };
    toggle.addEventListener('change', () => {
        result.showRaw = toggle.checked;
        paint();
    });
    paint();
    container.appendChild(body);
}

async function runPreview(tool, fields, values, form) {
    form.querySelectorAll('.field-error').forEach(node => node.remove());
    const { args, errors } = buildArgs(fields, values);
    const names = Object.keys(errors);
    if (names.length) {
        names.forEach(name => {
            const field = form.querySelector(`[name="${CSS.escape(name)}"]`);
            if (field) field.closest('.field').appendChild(make('span', 'field-error', `${name} ${errors[name]}`));
        });
        const first = form.querySelector(`[name="${CSS.escape(names[0])}"]`);
        if (first) first.focus();
        return;
    }

    saveArgs(tool.name, args);
    state.running = true;
    renderDetail();
    try {
        state.run[tool.name] = await sendJson('POST', `/${encodeURIComponent(tool.name)}/preview`, { args });
    } catch (error) {
        state.run[tool.name] = {
            error: await errorText(error, 'Could not run the tool.'),
            size_bytes: 0,
            approx_tokens: 0,
            elapsed_ms: 0,
        };
    } finally {
        state.running = false;
    }
    renderDetail();
    const output = el('output-panel');
    if (output) output.scrollIntoView({ block: 'nearest' });
}

function renderDetail() {
    const pane = el('detail-pane');
    pane.replaceChildren();
    const tool = toolByName(state.selected);
    if (!tool) {
        pane.appendChild(make('p', 'muted', 'Select a tool to see its details and try it.'));
        return;
    }

    const header = make('div', 'detail-header');
    const titles = make('div');
    titles.appendChild(make('h2', '', tool.title));
    titles.appendChild(make('code', 'tool-name', tool.name));
    if (tool.is_new) titles.appendChild(make('span', 'badge', 'New, using default'));
    header.appendChild(titles);
    header.appendChild(buildModeControl(tool.name, 'mode-detail', tool.title));
    pane.appendChild(header);

    const noteRow = make('div', 'note-row');
    const noteLabel = make('label', '', 'Note');
    noteLabel.htmlFor = 'tool-note';
    const note = document.createElement('input');
    note.type = 'text';
    note.id = 'tool-note';
    note.maxLength = 500;
    note.placeholder = 'Optional note for other editors';
    note.dataset.testid = 'tool-note';
    note.value = state.draft.notes[tool.name] || '';
    note.addEventListener('input', () => {
        state.draft.notes[tool.name] = note.value;
        renderUnsavedBar();
    });
    noteRow.appendChild(noteLabel);
    noteRow.appendChild(note);
    pane.appendChild(noteRow);

    const description = section('Description');
    const body = make('div', 'detail-description markdown-body');
    body.dataset.testid = 'tool-description';
    body.innerHTML = renderMarkdown(tool.description || 'No description.');
    description.appendChild(body);
    pane.appendChild(description);

    pane.appendChild(renderParameters(tool));
    pane.appendChild(renderDeclaredOutput(tool));
    pane.appendChild(renderTryIt(tool));
}

// ----- unsaved bar, saving -----

function describeChange(change) {
    if (change.removed) return `Remove stale setting for ${change.label}`;
    if (change.note) return `${change.label}: note changed`;
    return `${change.label}: ${modeLabel(change.from)} → ${modeLabel(change.to)}`;
}

function renderUnsavedBar() {
    const pending = changes();
    const bar = el('unsaved-bar');
    bar.hidden = pending.length === 0;
    el('unsaved-count').textContent = `Unsaved changes (${pending.length})`;
    const list = el('unsaved-list');
    list.replaceChildren();
    pending.forEach(change => list.appendChild(make('li', '', describeChange(change))));
    const save = el('save-button');
    save.disabled = !canSave();
    save.textContent = state.saving ? 'Saving…' : 'Save';
    save.title = canSave() ? '' : 'Saving is unavailable while a banner above reports a problem';
}

async function save() {
    if (!canSave()) return;
    state.saving = true;
    renderUnsavedBar();
    try {
        await sendJson('PUT', '/policy', buildSaveBody(state.server, state.draft));
        state.notice = { kind: 'success', text: 'Saved. Changes apply to each user\'s next chat message.' };
        await reload(true);
    } catch (error) {
        const payload = await getApiErrorPayload(error);
        const text = await errorText(error, 'Could not save.');
        if (payload && payload.code === 'conflict') {
            state.notice = { kind: 'error', text, action: { label: 'Reload', testid: 'reload-button', run: () => reload() } };
        } else {
            state.notice = { kind: 'error', text };
        }
        renderBanners();
    } finally {
        state.saving = false;
        renderUnsavedBar();
    }
}

function discard() {
    state.draft = createDraft(state.server);
    state.notice = null;
    renderAll();
}

async function resetToDefaults() {
    if (!window.confirm('Reset all chat tool settings to the built-in defaults?')) return;
    try {
        await sendJson('POST', '/policy/reset', {});
        state.notice = { kind: 'success', text: 'Settings reset to the built-in defaults.' };
        await reload(true);
    } catch (error) {
        state.notice = { kind: 'error', text: await errorText(error, 'Could not reset the settings.') };
        renderBanners();
    }
}

// ----- boot -----

function renderAll() {
    renderBanners();
    renderChips();
    renderList();
    renderDetail();
    renderUnsavedBar();
    const select = el('default-mode-select');
    select.value = state.draft.defaultMode;
}

async function reload(keepNotice = false) {
    if (!keepNotice) state.notice = null;
    try {
        await loadServerState();
    } catch (error) {
        showFatal(await errorText(error, 'Could not load the settings.'), error);
        return;
    }
    el('admin-layout').hidden = false;
    renderAll();
}

function showFatal(message, error) {
    el('admin-layout').hidden = true;
    el('unsaved-bar').hidden = true;
    const root = el('banners');
    root.replaceChildren();
    const node = banner('error', error && error.status === 403 ? message : `Could not load the settings: ${message}`);
    node.dataset.testid = 'banner-fatal';
    root.appendChild(node);
}

function init() {
    const select = el('default-mode-select');
    MODES.forEach(mode => select.appendChild(Object.assign(document.createElement('option'), { value: mode.value, textContent: mode.label })));
    select.addEventListener('change', () => {
        state.draft.defaultMode = select.value;
        renderUnsavedBar();
    });
    el('tool-search').addEventListener('input', event => {
        state.search = event.target.value;
        renderList();
    });
    el('save-button').addEventListener('click', save);
    el('discard-button').addEventListener('click', discard);
    window.addEventListener('beforeunload', event => {
        if (changes().length) {
            event.preventDefault();
            event.returnValue = '';
        }
    });

    reload().then(() => {
        if (state.server && !state.server.catalog_error) loadSample();
    });
}

init();
