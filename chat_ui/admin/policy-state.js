// Draft/diff logic for the chat tool settings page. Pure functions, no DOM
// (unit-tested in tests/unit/js/policy_state.test.mjs).
//
// The server is the source of truth: the page edits a *draft* of the modes and only
// the Save button sends it, together with the revision it was based on.

export const MODES = [
    { value: 'enabled', label: 'On', description: 'The AI can call this tool and sees its output.' },
    { value: 'user_only', label: 'Hidden from AI', description: 'The AI can run this tool, but only you see its output.' },
    { value: 'disabled', label: 'Off', description: 'The AI cannot use this tool.' },
];

export function modeLabel(mode) {
    const found = MODES.find(candidate => candidate.value === mode);
    return found ? found.label : String(mode);
}

export function isValidMode(mode) {
    return MODES.some(candidate => candidate.value === mode);
}

// `server` is the response of GET /admin/api/chat-tools.
export function createDraft(server) {
    const modes = {};
    const notes = {};
    (server.tools || []).forEach(tool => {
        modes[tool.name] = tool.mode;
        notes[tool.name] = tool.note || '';
    });
    return {
        defaultMode: server.policy.default_mode,
        modes,
        notes,
        removedStale: new Set(),
    };
}

// A list of human-readable changes between the server state and the draft.
export function pendingChanges(server, draft) {
    const changes = [];
    if (draft.defaultMode !== server.policy.default_mode) {
        changes.push({
            key: '(default)',
            label: 'Default for new tools',
            from: server.policy.default_mode,
            to: draft.defaultMode,
        });
    }
    (server.tools || []).forEach(tool => {
        const mode = draft.modes[tool.name];
        const note = draft.notes[tool.name] || '';
        if (mode !== tool.mode) {
            changes.push({ key: tool.name, label: tool.title || tool.name, from: tool.mode, to: mode });
        } else if (note !== (tool.note || '')) {
            changes.push({ key: tool.name, label: tool.title || tool.name, note: true });
        }
    });
    (server.stale || []).forEach(stale => {
        if (draft.removedStale.has(stale.name)) {
            changes.push({ key: stale.name, label: stale.name, removed: true });
        }
    });
    return changes;
}

// Saving acknowledges every tool on the page: each one is written explicitly, so
// "New" later means "added after this was saved". Stale entries stay unless removed.
export function buildSaveBody(server, draft) {
    const tools = {};
    (server.stale || []).forEach(stale => {
        if (!draft.removedStale.has(stale.name)) {
            tools[stale.name] = withNote(stale.mode, stale.note);
        }
    });
    (server.tools || []).forEach(tool => {
        tools[tool.name] = withNote(draft.modes[tool.name], draft.notes[tool.name]);
    });
    return {
        expected_revision: server.policy.revision,
        default_mode: draft.defaultMode,
        tools,
    };
}

function withNote(mode, note) {
    const trimmed = (note || '').trim();
    return trimmed ? { mode, note: trimmed } : { mode };
}

// Filter chips: all | enabled | user_only | disabled | new | stale
export const FILTERS = [
    { key: 'all', label: 'All' },
    { key: 'enabled', label: 'On' },
    { key: 'user_only', label: 'Hidden from AI' },
    { key: 'disabled', label: 'Off' },
    { key: 'new', label: 'New' },
    { key: 'stale', label: 'Stale' },
];

export function visibleTools(server, draft, { search = '', filter = 'all' } = {}) {
    const needle = search.trim().toLowerCase();
    return (server.tools || []).filter(tool => {
        if (needle) {
            const haystack = `${tool.name} ${tool.title} ${tool.description}`.toLowerCase();
            if (!haystack.includes(needle)) return false;
        }
        if (filter === 'all') return true;
        if (filter === 'new') return tool.is_new;
        if (filter === 'stale') return false;
        return draft.modes[tool.name] === filter;
    });
}

export function filterCounts(server, draft) {
    const tools = server.tools || [];
    return {
        all: tools.length,
        enabled: tools.filter(tool => draft.modes[tool.name] === 'enabled').length,
        user_only: tools.filter(tool => draft.modes[tool.name] === 'user_only').length,
        disabled: tools.filter(tool => draft.modes[tool.name] === 'disabled').length,
        new: tools.filter(tool => tool.is_new).length,
        stale: (server.stale || []).filter(stale => !draft.removedStale.has(stale.name)).length,
    };
}
