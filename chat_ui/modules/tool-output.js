// Shared renderer for the raw output of a data tool.
//
// Used by both the admin "Try it" preview (chat_ui/admin/chat-tools.js) and the
// chat result cards for tools whose output is hidden from the AI
// (modules/chat.js), so an admin sees exactly what a user will see.
//
// Rendering rules:
//   - array of objects  -> table (first MAX_TABLE_ROWS rows, with a note)
//   - flat object       -> key/value table
//   - nested value      -> collapsible JSON tree
//   - string and scalar -> <pre>
//
// Safety: every value is inserted with `textContent`. Tool output (data values,
// column names, anything) is never passed to `innerHTML`.
//
// `classifyOutput` is a pure function (unit-tested without a DOM);
// `renderToolOutput` takes the document as an option so it can also be tested
// against a minimal fake DOM.

export const MAX_TABLE_ROWS = 200;
export const MAX_TREE_CHILDREN = 200;
const MAX_TREE_DEPTH = 8;
const MAX_CELL_CHARS = 500;

function isPlainObject(value) {
    return value !== null && typeof value === 'object' && !Array.isArray(value);
}

function isScalar(value) {
    return value === null || ['string', 'number', 'boolean'].includes(typeof value);
}

export function classifyOutput(value) {
    if (Array.isArray(value)) {
        return value.length > 0 && value.every(isPlainObject) ? 'table' : 'tree';
    }
    if (isPlainObject(value)) {
        const values = Object.values(value);
        return values.length > 0 && values.every(isScalar) ? 'kv' : 'tree';
    }
    return 'pre';
}

export function formatScalar(value) {
    if (value === null) return 'null';
    if (value === undefined) return '';
    if (typeof value === 'object') {
        let text;
        try {
            text = JSON.stringify(value);
        } catch {
            text = String(value);
        }
        return text.length > MAX_CELL_CHARS ? `${text.slice(0, MAX_CELL_CHARS)}…` : text;
    }
    const text = String(value);
    return text.length > MAX_CELL_CHARS ? `${text.slice(0, MAX_CELL_CHARS)}…` : text;
}

function element(doc, tag, className, text) {
    const el = doc.createElement(tag);
    if (className) el.className = className;
    if (text !== undefined) el.textContent = text;
    return el;
}

function renderTable(doc, rows, maxRows) {
    const wrapper = element(doc, 'div', 'tool-output-table-wrap');
    const shown = rows.slice(0, maxRows);
    const columns = [];
    shown.forEach(row => {
        Object.keys(row).forEach(key => {
            if (!columns.includes(key)) columns.push(key);
        });
    });

    const table = element(doc, 'table', 'tool-output-table');
    const head = element(doc, 'tr');
    columns.forEach(column => head.appendChild(element(doc, 'th', '', column)));
    table.appendChild(element(doc, 'thead')).appendChild(head);

    const body = element(doc, 'tbody');
    shown.forEach(row => {
        const tr = element(doc, 'tr');
        columns.forEach(column => {
            const cell = element(doc, 'td', '', formatScalar(row[column]));
            if (typeof row[column] === 'number') cell.className = 'tool-output-number';
            tr.appendChild(cell);
        });
        body.appendChild(tr);
    });
    table.appendChild(body);
    wrapper.appendChild(table);

    if (rows.length > shown.length) {
        wrapper.appendChild(element(
            doc, 'div', 'tool-output-note', `Showing ${shown.length} of ${rows.length} rows`,
        ));
    }
    return wrapper;
}

function renderKeyValue(doc, object) {
    const table = element(doc, 'table', 'tool-output-table tool-output-kv');
    const body = element(doc, 'tbody');
    Object.entries(object).forEach(([key, value]) => {
        const tr = element(doc, 'tr');
        tr.appendChild(element(doc, 'th', '', key));
        tr.appendChild(element(doc, 'td', '', formatScalar(value)));
        body.appendChild(tr);
    });
    table.appendChild(body);
    return table;
}

function describeContainer(value) {
    if (Array.isArray(value)) return `[${value.length} item${value.length === 1 ? '' : 's'}]`;
    const count = Object.keys(value).length;
    return `{${count} key${count === 1 ? '' : 's'}}`;
}

function renderTreeNode(doc, label, value, depth) {
    if (isScalar(value) || depth >= MAX_TREE_DEPTH) {
        const line = element(doc, 'div', 'tool-output-leaf');
        if (label !== null) line.appendChild(element(doc, 'span', 'tool-output-key', `${label}: `));
        line.appendChild(element(doc, 'span', 'tool-output-value', formatScalar(value)));
        return line;
    }

    const details = element(doc, 'details', 'tool-output-node');
    if (depth < 1) details.open = true;
    const summary = element(doc, 'summary');
    if (label !== null) summary.appendChild(element(doc, 'span', 'tool-output-key', `${label}: `));
    summary.appendChild(element(doc, 'span', 'tool-output-summary', describeContainer(value)));
    details.appendChild(summary);

    const children = element(doc, 'div', 'tool-output-children');
    const entries = Array.isArray(value)
        ? value.map((item, index) => [String(index), item])
        : Object.entries(value);
    entries.slice(0, MAX_TREE_CHILDREN).forEach(([key, child]) => {
        children.appendChild(renderTreeNode(doc, key, child, depth + 1));
    });
    if (entries.length > MAX_TREE_CHILDREN) {
        children.appendChild(element(
            doc, 'div', 'tool-output-note', `… ${entries.length - MAX_TREE_CHILDREN} more`,
        ));
    }
    details.appendChild(children);
    return details;
}

export function renderToolOutput(value, { doc = document, maxRows = MAX_TABLE_ROWS } = {}) {
    const kind = classifyOutput(value);
    const container = element(doc, 'div', `tool-output tool-output-${kind}`);
    container.setAttribute('data-kind', kind);

    if (kind === 'table') {
        container.appendChild(renderTable(doc, value, maxRows));
    } else if (kind === 'kv') {
        container.appendChild(renderKeyValue(doc, value));
    } else if (kind === 'tree') {
        container.appendChild(renderTreeNode(doc, null, value, 0));
    } else {
        container.appendChild(element(doc, 'pre', 'tool-output-pre', value === undefined ? '' : String(value)));
    }
    return container;
}

// The text a model would receive for this output: JSON for structured values.
export function outputAsText(value) {
    if (typeof value === 'string') return value;
    try {
        return JSON.stringify(value, null, 2);
    } catch {
        return String(value);
    }
}
