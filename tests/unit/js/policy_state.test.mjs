import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { test } from 'node:test';
import vm from 'node:vm';

const sourcePath = new URL('../../../chat_ui/admin/policy-state.js', import.meta.url);
const source = await readFile(sourcePath, 'utf8');

// Objects built inside the vm context have a different Object.prototype, which
// strict deep equality rejects; compare their JSON form instead.
const plain = value => JSON.parse(JSON.stringify(value));
const same = (actual, expected) => assert.deepStrictEqual(plain(actual), plain(expected));

async function load() {
    const module = new vm.SourceTextModule(source, {
        context: vm.createContext({ Set }),
        identifier: String(sourcePath),
    });
    await module.link(() => {
        throw new Error('policy-state.js should not import other modules');
    });
    await module.evaluate();
    return module.namespace;
}

function server(overrides = {}) {
    return {
        tools: [
            { name: 'tool_a', title: 'Tool A', description: 'Mean of things', mode: 'enabled', note: null, is_default: false, is_new: false },
            { name: 'tool_b', title: 'Tool B', description: 'Raw rows', mode: 'user_only', note: 'review', is_default: false, is_new: false },
            { name: 'tool_c', title: 'Tool C', description: 'Whole dataset', mode: 'disabled', note: null, is_default: true, is_new: true },
        ],
        stale: [{ name: 'old_tool', mode: 'disabled', note: null }],
        policy: { revision: 4, default_mode: 'enabled' },
        ...overrides,
    };
}

test('modes have the labels the issue asks for', async () => {
    const { MODES, modeLabel, isValidMode } = await load();

    same(MODES.map(mode => [mode.value, mode.label]), [
        ['enabled', 'On'],
        ['user_only', 'Hidden from AI'],
        ['disabled', 'Off'],
    ]);
    assert.equal(modeLabel('user_only'), 'Hidden from AI');
    assert.equal(modeLabel('weird'), 'weird');
    assert.equal(isValidMode('disabled'), true);
    assert.equal(isValidMode('maybe'), false);
});

test('a fresh draft equals the server state: no pending changes', async () => {
    const { createDraft, pendingChanges } = await load();
    const state = server();

    const draft = createDraft(state);

    same(draft.modes, { tool_a: 'enabled', tool_b: 'user_only', tool_c: 'disabled' });
    same(pendingChanges(state, draft), []);
});

test('pendingChanges lists mode, default, note and stale-removal changes', async () => {
    const { createDraft, pendingChanges } = await load();
    const state = server();
    const draft = createDraft(state);

    draft.modes.tool_a = 'user_only';
    draft.defaultMode = 'disabled';
    draft.notes.tool_b = 'changed note';
    draft.removedStale.add('old_tool');

    const changes = pendingChanges(state, draft);

    same(changes.map(change => change.key).sort(), ['(default)', 'old_tool', 'tool_a', 'tool_b']);
    same(changes.find(change => change.key === 'tool_a'), { key: 'tool_a', label: 'Tool A', from: 'enabled', to: 'user_only' });
    assert.equal(changes.find(change => change.key === 'tool_b').note, true);
    assert.equal(changes.find(change => change.key === 'old_tool').removed, true);
});

test('changing a mode back removes it from the pending list', async () => {
    const { createDraft, pendingChanges } = await load();
    const state = server();
    const draft = createDraft(state);

    draft.modes.tool_a = 'disabled';
    assert.equal(pendingChanges(state, draft).length, 1);
    draft.modes.tool_a = 'enabled';
    assert.equal(pendingChanges(state, draft).length, 0);
});

test('buildSaveBody sends the whole map with the expected revision', async () => {
    const { createDraft, buildSaveBody } = await load();
    const state = server();
    const draft = createDraft(state);
    draft.modes.tool_a = 'user_only';
    draft.notes.tool_a = '  needs review  ';
    draft.defaultMode = 'disabled';

    same(buildSaveBody(state, draft), {
        expected_revision: 4,
        default_mode: 'disabled',
        tools: {
            old_tool: { mode: 'disabled' },
            tool_a: { mode: 'user_only', note: 'needs review' },
            tool_b: { mode: 'user_only', note: 'review' },
            tool_c: { mode: 'disabled' },
        },
    });
});

test('saving writes every tool explicitly so "new" only means added later', async () => {
    const { createDraft, buildSaveBody } = await load();
    const state = server();

    const body = buildSaveBody(state, createDraft(state));

    same(Object.keys(body.tools).sort(), ['old_tool', 'tool_a', 'tool_b', 'tool_c']);
});

test('removed stale entries are dropped from the save body', async () => {
    const { createDraft, buildSaveBody } = await load();
    const state = server();
    const draft = createDraft(state);
    draft.removedStale.add('old_tool');

    assert.equal('old_tool' in buildSaveBody(state, draft).tools, false);
});

test('visibleTools filters by search text and by mode/new', async () => {
    const { createDraft, visibleTools } = await load();
    const state = server();
    const draft = createDraft(state);
    const names = options => visibleTools(state, draft, options).map(tool => tool.name);

    same(names({}), ['tool_a', 'tool_b', 'tool_c']);
    same(names({ search: 'RAW' }), ['tool_b']);
    same(names({ search: 'tool_c' }), ['tool_c']);
    same(names({ filter: 'user_only' }), ['tool_b']);
    same(names({ filter: 'disabled' }), ['tool_c']);
    same(names({ filter: 'new' }), ['tool_c']);
    same(names({ filter: 'stale' }), []);
    draft.modes.tool_a = 'disabled';
    same(names({ filter: 'disabled' }), ['tool_a', 'tool_c']); // follows the draft, not the server
});

test('filterCounts follows the draft and ignores removed stale entries', async () => {
    const { createDraft, filterCounts } = await load();
    const state = server();
    const draft = createDraft(state);

    same(filterCounts(state, draft), { all: 3, enabled: 1, user_only: 1, disabled: 1, new: 1, stale: 1 });
    draft.modes.tool_a = 'user_only';
    draft.removedStale.add('old_tool');
    same(filterCounts(state, draft), { all: 3, enabled: 0, user_only: 2, disabled: 1, new: 1, stale: 0 });
});
