import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { test } from 'node:test';
import vm from 'node:vm';

const sourcePath = new URL('../../../chat_ui/modules/tool-output.js', import.meta.url);
const source = await readFile(sourcePath, 'utf8');

async function loadModule() {
    const context = vm.createContext({ console });
    const module = new vm.SourceTextModule(source, { context, identifier: String(sourcePath) });
    await module.link(() => {
        throw new Error('tool-output.js should not import other modules');
    });
    await module.evaluate();
    return module.namespace;
}

// Minimal DOM: enough for the renderer, and `innerHTML` is a hard failure because
// tool output must only ever be inserted as text.
class FakeElement {
    constructor(tagName) {
        this.tagName = tagName;
        this.children = [];
        this.attributes = {};
        this.className = '';
        this.open = false;
        this._text = '';
    }

    appendChild(child) {
        this.children.push(child);
        return child;
    }

    setAttribute(name, value) {
        this.attributes[name] = String(value);
    }

    set textContent(value) {
        this._text = String(value);
        this.children = [];
    }

    get textContent() {
        return this._text + this.children.map(child => child.textContent).join('');
    }

    set innerHTML(_value) {
        throw new Error('innerHTML must never be used for tool output');
    }

    find(predicate, found = []) {
        if (predicate(this)) found.push(this);
        this.children.forEach(child => child.find(predicate, found));
        return found;
    }

    byTag(tag) {
        return this.find(el => el.tagName === tag);
    }
}

const fakeDocument = { createElement: tag => new FakeElement(tag) };

async function render(value, options = {}) {
    const { renderToolOutput } = await loadModule();
    return renderToolOutput(value, { doc: fakeDocument, ...options });
}

test('classifyOutput picks table, key/value, tree or pre', async () => {
    const { classifyOutput } = await loadModule();

    assert.equal(classifyOutput([{ a: 1 }, { a: 2 }]), 'table');
    assert.equal(classifyOutput({ mean: 1.5, name: 'x', ok: true, none: null }), 'kv');
    assert.equal(classifyOutput({ a: { b: 1 } }), 'tree');
    assert.equal(classifyOutput({ rows: [1, 2] }), 'tree');
    assert.equal(classifyOutput([1, 2, 3]), 'tree');
    assert.equal(classifyOutput([{ a: 1 }, 5]), 'tree');
    assert.equal(classifyOutput([]), 'tree');
    assert.equal(classifyOutput({}), 'tree');
    assert.equal(classifyOutput('plain text'), 'pre');
    assert.equal(classifyOutput(42), 'pre');
    assert.equal(classifyOutput(null), 'pre');
});

test('arrays of objects render as a table with the union of columns', async () => {
    const out = await render([{ a: 1, b: 'x' }, { a: 2, c: null }]);

    assert.equal(out.attributes['data-kind'], 'table');
    const headers = out.byTag('th').map(el => el.textContent);
    assert.deepEqual(headers, ['a', 'b', 'c']);
    const cells = out.byTag('tbody')[0].byTag('tr').map(tr => tr.byTag('td').map(td => td.textContent));
    assert.deepEqual(cells, [['1', 'x', ''], ['2', '', 'null']]);
});

test('tables are limited to 200 rows with a "showing" note', async () => {
    const rows = Array.from({ length: 250 }, (_, i) => ({ i }));

    const out = await render(rows);

    assert.equal(out.byTag('tbody')[0].byTag('tr').length, 200);
    assert.match(out.textContent, /Showing 200 of 250 rows/);
});

test('small tables have no truncation note', async () => {
    const out = await render([{ i: 1 }]);

    assert.doesNotMatch(out.textContent, /Showing/);
});

test('flat objects render as a key/value table', async () => {
    const out = await render({ mean: 3.5, label: 'age' });

    assert.equal(out.attributes['data-kind'], 'kv');
    const rows = out.byTag('tr').map(tr => [tr.byTag('th')[0].textContent, tr.byTag('td')[0].textContent]);
    assert.deepEqual(rows, [['mean', '3.5'], ['label', 'age']]);
});

test('nested values render as a collapsible tree, root open and children closed', async () => {
    const out = await render({ stats: { mean: 1, tags: ['a', 'b'] }, n: 2 });

    assert.equal(out.attributes['data-kind'], 'tree');
    const nodes = out.byTag('details');
    assert.ok(nodes.length >= 2);
    assert.equal(nodes[0].open, true);
    assert.equal(nodes[1].open, false);
    assert.match(out.textContent, /\{2 keys\}/);
    assert.match(out.textContent, /\[2 items\]/);
    assert.match(out.textContent, /mean: 1/);
});

test('strings and scalars render in a <pre> without truncation', async () => {
    const long = 'x'.repeat(2000);

    const out = await render(long);

    assert.equal(out.attributes['data-kind'], 'pre');
    assert.equal(out.byTag('pre')[0].textContent, long);
    assert.equal((await render(42)).byTag('pre')[0].textContent, '42');
});

test('values are inserted as text: markup in data is never interpreted', async () => {
    const payload = '<img src=x onerror="alert(1)">';
    const cases = [
        [{ [payload]: payload }],               // table: header and cell
        { [payload]: payload },                 // key/value
        { nested: { [payload]: [payload] } },   // tree
        payload,                                // pre
    ];

    for (const value of cases) {
        const out = await render(value); // the fake DOM throws if innerHTML is touched
        assert.ok(out.textContent.includes(payload));
        assert.equal(out.find(el => el.tagName === 'img').length, 0);
    }
});

test('very long cell values are shortened', async () => {
    const out = await render([{ blob: 'y'.repeat(2000) }]);

    const cell = out.byTag('td')[0].textContent;
    assert.ok(cell.length <= 501);
    assert.ok(cell.endsWith('…'));
});

test('outputAsText is the JSON a model would receive', async () => {
    const { outputAsText } = await loadModule();

    assert.equal(outputAsText('abc'), 'abc');
    assert.equal(outputAsText({ a: 1 }), '{\n  "a": 1\n}');
});
