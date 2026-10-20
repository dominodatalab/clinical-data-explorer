import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { test } from 'node:test';
import vm from 'node:vm';

const sourcePath = new URL('../../../chat_ui/admin/schema-form.js', import.meta.url);
const source = await readFile(sourcePath, 'utf8');

// Objects built inside the vm context have a different Object.prototype, which
// strict deep equality rejects; compare their JSON form instead.
const plain = value => JSON.parse(JSON.stringify(value));
const same = (actual, expected) => assert.deepStrictEqual(plain(actual), plain(expected));

async function load() {
    const module = new vm.SourceTextModule(source, {
        context: vm.createContext({}),
        identifier: String(sourcePath),
    });
    await module.link(() => {
        throw new Error('schema-form.js should not import other modules');
    });
    await module.evaluate();
    return module.namespace;
}

// Shapes taken from what FastApiMCP produces for the real tools.
const FEATURE_STATS = {
    type: 'object',
    properties: {
        features: {
            anyOf: [{ type: 'array', items: { type: 'string' } }, { type: 'null' }],
            title: 'features',
            type: 'array',
        },
    },
};
const HISTOGRAM = {
    type: 'object',
    properties: {
        column: { type: 'string', title: 'column' },
        bins: { type: 'integer', title: 'bins', default: 30 },
        filter: {
            anyOf: [
                {
                    type: 'object',
                    properties: { column: { type: 'string' }, value: { type: 'string' } },
                    required: ['column', 'value'],
                    title: 'ChartFilterCondition',
                },
                { type: 'null' },
            ],
            title: 'filter',
            type: 'object',
        },
    },
    required: ['column'],
};
const HEAD = {
    type: 'object',
    properties: { rows: { type: 'integer', description: 'Number of rows to return', default: 5 } },
};

test('isColumnParam is a name heuristic', async () => {
    const { isColumnParam } = await load();

    for (const name of ['column', 'feature', 'feature1', 'target', 'group_by', 'category_column', 'x_col']) {
        assert.equal(isColumnParam(name), true, name);
    }
    for (const name of ['bins', 'rows', 'page_size', 'aggregation']) {
        assert.equal(isColumnParam(name), false, name);
    }
});

test('anyOf with null means optional and keeps the non-null variant', async () => {
    const { normalizeSchema, typeLabel } = await load();

    const normalized = normalizeSchema(FEATURE_STATS.properties.features, FEATURE_STATS);

    assert.equal(normalized.kind, 'array');
    assert.equal(normalized.nullable, true);
    assert.equal(typeLabel(normalized, FEATURE_STATS), 'array<string> | null');
});

test('type arrays containing null are optional too', async () => {
    const { normalizeSchema } = await load();

    const normalized = normalizeSchema({ type: ['integer', 'null'], default: null });

    assert.equal(normalized.kind, 'integer');
    assert.equal(normalized.nullable, true);
});

test('enums are recognised and labelled', async () => {
    const { normalizeSchema, typeLabel } = await load();

    const normalized = normalizeSchema({ type: 'string', enum: ['sas', 'r', 'python'] });

    assert.equal(normalized.kind, 'enum');
    assert.equal(typeLabel(normalized), 'enum (sas, r, python)');
});

test('$ref schemas resolve against the root, with sibling keys kept', async () => {
    const { normalizeSchema, parameterRows } = await load();
    const root = {
        type: 'object',
        properties: { filters: { $ref: '#/$defs/Filter', description: 'A filter' } },
        $defs: { Filter: { type: 'object', properties: { column: { type: 'string' } }, required: ['column'] } },
    };

    const normalized = normalizeSchema(root.properties.filters, root);
    assert.equal(normalized.kind, 'object');
    assert.equal(normalized.description, 'A filter');
    same(parameterRows(root).map(row => [row.name, row.depth, row.required]), [
        ['filters', 0, false],
        ['filters.column', 1, true],
    ]);
});

test('unresolvable refs and unknown shapes become "unknown", never throw', async () => {
    const { normalizeSchema, fieldsFromSchema } = await load();

    assert.equal(normalizeSchema({ $ref: '#/$defs/Missing' }, {}).kind, 'unknown');
    assert.equal(normalizeSchema({ anyOf: [{ type: 'string' }, { type: 'integer' }] }).kind, 'unknown');
    assert.equal(normalizeSchema(null).kind, 'unknown');
    same(fieldsFromSchema(undefined), []);
    same(fieldsFromSchema({}), []);
});

test('parameterRows lists name, type, required, default and description', async () => {
    const { parameterRows } = await load();

    same(parameterRows(HEAD), [
        { name: 'rows', depth: 0, type: 'integer', required: false, default: '5', description: 'Number of rows to return' },
    ]);
    const rows = parameterRows(HISTOGRAM);
    same(rows.map(row => [row.name, row.type, row.required, row.depth]), [
        ['column', 'string', true, 0],
        ['bins', 'integer', false, 0],
        ['filter', 'object | null', false, 0],
        ['filter.column', 'string', true, 1],
        ['filter.value', 'string', true, 1],
    ]);
});

test('parameterRows descends into arrays of objects', async () => {
    const { parameterRows } = await load();
    const schema = {
        type: 'object',
        properties: {
            filters: {
                type: 'array',
                items: { type: 'object', properties: { column: { type: 'string' }, operator: { type: 'string' } }, required: ['column'] },
            },
        },
    };

    same(parameterRows(schema).map(row => [row.name, row.type]), [
        ['filters', 'array<object>'],
        ['filters[].column', 'string'],
        ['filters[].operator', 'string'],
    ]);
});

test('fieldsFromSchema picks an editor per type, with a JSON fallback', async () => {
    const { fieldsFromSchema } = await load();
    const schema = {
        type: 'object',
        required: ['name'],
        properties: {
            name: { type: 'string' },
            size: { type: 'number' },
            count: { type: 'integer', default: 3 },
            flag: { type: 'boolean' },
            syntax: { type: 'string', enum: ['sas', 'r'] },
            tags: { type: 'array', items: { type: 'string' } },
            nums: { type: 'array', items: { type: 'integer' } },
            filters: { type: 'array', items: { type: 'object' } },
            nested: { type: 'object', properties: { a: { type: 'string' } } },
            weird: { anyOf: [{ type: 'string' }, { type: 'integer' }] },
            column: { type: 'string' },
        },
    };

    const fields = Object.fromEntries(fieldsFromSchema(schema).map(field => [field.name, field]));

    same(
        Object.fromEntries(Object.entries(fields).map(([name, field]) => [name, field.kind])),
        { name: 'string', size: 'number', count: 'integer', flag: 'boolean', syntax: 'enum', tags: 'array', nums: 'array', filters: 'json', nested: 'json', weird: 'json', column: 'string' },
    );
    assert.equal(fields.name.required, true);
    assert.equal(fields.count.default, 3);
    assert.equal(fields.nums.itemKind, 'integer');
    assert.equal(fields.column.isColumn, true);
    assert.equal(fields.name.isColumn, false);
    assert.equal(fields.tags.isColumn, false); // only plain string params get column hints
});

test('initialValues prefers saved args, then examples, then schema defaults', async () => {
    const { fieldsFromSchema, initialValues } = await load();
    const fields = fieldsFromSchema(HISTOGRAM);

    same(initialValues(fields), { column: '', bins: '30', filter: '' });
    same(initialValues(fields, {}, { column: 'AGE', bins: 10 }), { column: 'AGE', bins: '10', filter: '' });
    same(
        initialValues(fields, { column: 'BMIBL', filter: { column: 'ARM', value: 'Placebo' } }, { column: 'AGE' }),
        { column: 'BMIBL', bins: '30', filter: JSON.stringify({ column: 'ARM', value: 'Placebo' }, null, 2) },
    );
});

test('buildArgs converts types and omits empty optional fields', async () => {
    const { fieldsFromSchema, buildArgs } = await load();
    const fields = fieldsFromSchema({
        type: 'object',
        required: ['column'],
        properties: {
            column: { type: 'string' },
            bins: { type: 'integer' },
            ratio: { type: 'number' },
            flag: { type: 'boolean' },
            tags: { type: 'array', items: { type: 'string' } },
            nums: { type: 'array', items: { type: 'number' } },
            filter: { type: 'object' },
            syntax: { type: 'string', enum: ['sas', 'r'] },
            untouched: { type: 'string' },
        },
    });

    const { args, errors } = buildArgs(fields, {
        column: 'AGE',
        bins: ' 12 ',
        ratio: '0.5',
        flag: 'false',
        tags: 'a, b ,, c',
        nums: '1, 2.5',
        filter: '{"column": "ARM", "value": "Placebo"}',
        syntax: 'sas',
        untouched: '',
    });

    same(errors, {});
    same(args, {
        column: 'AGE',
        bins: 12,
        ratio: 0.5,
        flag: false,
        tags: ['a', 'b', 'c'],
        nums: [1, 2.5],
        filter: { column: 'ARM', value: 'Placebo' },
        syntax: 'sas',
    });
});

test('buildArgs reports missing required fields and invalid values', async () => {
    const { fieldsFromSchema, buildArgs } = await load();
    const fields = fieldsFromSchema({
        type: 'object',
        required: ['column'],
        properties: {
            column: { type: 'string' },
            bins: { type: 'integer' },
            ratio: { type: 'number' },
            nums: { type: 'array', items: { type: 'integer' } },
            filter: { type: 'object' },
        },
    });

    const { args, errors } = buildArgs(fields, { column: '  ', bins: '1.5', ratio: 'abc', nums: '1, x', filter: '{oops' });

    same(args, {});
    same(errors, {
        column: 'is required',
        bins: 'must be a whole number',
        ratio: 'must be a number',
        nums: 'each item must be a number',
        filter: 'must be valid JSON',
    });
});
