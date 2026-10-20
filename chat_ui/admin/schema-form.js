// Turns a tool's JSON Schema (`inputSchema` from the MCP `list_tools()` response)
// into a parameters table and a "Try it" form. Pure functions, no DOM, so they are
// unit-tested directly (tests/unit/js/schema_form.test.mjs).
//
// Nothing here knows about specific tools: new tools get forms automatically, and
// anything unrecognized falls back to a raw JSON field rather than breaking.

export const COLUMN_PARAM_PATTERN = /column|feature|target|group_by|_col/i;
const MAX_DEPTH = 4;

// Heuristic hint only: string parameters whose names look like column references get
// a <datalist> of the sample's columns. It is never required to match.
export function isColumnParam(name) {
    return COLUMN_PARAM_PATTERN.test(name);
}

export function resolveRef(schema, root) {
    let current = schema && typeof schema === 'object' ? schema : {};
    for (let guard = 0; guard < 10 && typeof current.$ref === 'string'; guard += 1) {
        let target = root;
        for (const part of current.$ref.replace(/^#\/?/, '').split('/').filter(Boolean)) {
            target = target && typeof target === 'object' ? target[decodeURIComponent(part)] : undefined;
        }
        if (!target || typeof target !== 'object') return {};
        const { $ref, ...siblings } = current;
        current = { ...target, ...siblings };
    }
    return current;
}

// Normalizes the many ways JSON Schema says "optional": `anyOf` with a null variant,
// or a `type` array containing "null". Returns { kind, nullable, ... } where kind is
// one of string | number | integer | boolean | enum | array | object | unknown.
export function normalizeSchema(schema, root = schema) {
    const resolved = resolveRef(schema, root);
    let base = resolved;
    let nullable = false;

    const variants = resolved.anyOf || resolved.oneOf;
    if (Array.isArray(variants)) {
        const resolvedVariants = variants.map(variant => resolveRef(variant, root));
        const nonNull = resolvedVariants.filter(variant => variant.type !== 'null');
        nullable = nonNull.length !== resolvedVariants.length;
        if (nonNull.length !== 1) {
            return { kind: 'unknown', nullable, raw: resolved, description: resolved.description };
        }
        base = { ...resolved, ...nonNull[0], description: resolved.description ?? nonNull[0].description };
    }

    let type = base.type;
    if (Array.isArray(type)) {
        const nonNullTypes = type.filter(t => t !== 'null');
        nullable = nullable || nonNullTypes.length !== type.length;
        type = nonNullTypes.length === 1 ? nonNullTypes[0] : undefined;
    }

    let kind = 'unknown';
    if (Array.isArray(base.enum)) kind = 'enum';
    else if (['string', 'number', 'integer', 'boolean', 'array', 'object'].includes(type)) kind = type;

    return {
        kind,
        nullable,
        enum: Array.isArray(base.enum) ? base.enum : undefined,
        items: base.items,
        properties: base.properties,
        required: Array.isArray(base.required) ? base.required : [],
        description: base.description,
        hasDefault: Object.prototype.hasOwnProperty.call(base, 'default'),
        default: base.default,
        raw: base,
    };
}

export function typeLabel(schema, root = schema, depth = 0) {
    const normalized = schema && schema.kind ? schema : normalizeSchema(schema, root);
    let label;
    switch (normalized.kind) {
        case 'enum':
            label = `enum (${normalized.enum.map(String).join(', ')})`;
            break;
        case 'array':
            label = depth >= MAX_DEPTH || !normalized.items
                ? 'array'
                : `array<${typeLabel(normalizeSchema(normalized.items, root), root, depth + 1)}>`;
            break;
        case 'unknown':
            label = 'any';
            break;
        default:
            label = normalized.kind;
    }
    return normalized.nullable ? `${label} | null` : label;
}

function formatDefault(normalized) {
    if (!normalized.hasDefault) return '';
    try {
        return JSON.stringify(normalized.default);
    } catch {
        return String(normalized.default);
    }
}

function collectRows(properties, required, root, depth, prefix, rows) {
    Object.entries(properties || {}).forEach(([name, propertySchema]) => {
        const normalized = normalizeSchema(propertySchema, root);
        const isRequired = required.includes(name);
        rows.push({
            name: prefix + name,
            depth,
            type: typeLabel(normalized, root),
            required: isRequired,
            default: formatDefault(normalized),
            description: normalized.description || '',
        });
        if (depth + 1 >= MAX_DEPTH) return;
        if (normalized.kind === 'object' && normalized.properties) {
            collectRows(normalized.properties, normalized.required, root, depth + 1, `${prefix}${name}.`, rows);
        } else if (normalized.kind === 'array' && normalized.items) {
            const items = normalizeSchema(normalized.items, root);
            if (items.kind === 'object' && items.properties) {
                collectRows(items.properties, items.required, root, depth + 1, `${prefix}${name}[].`, rows);
            }
        }
    });
}

// Flat rows (with a `depth` for indentation) for the parameters table.
export function parameterRows(inputSchema) {
    const root = inputSchema || {};
    const rows = [];
    collectRows(root.properties, Array.isArray(root.required) ? root.required : [], root, 0, '', rows);
    return rows;
}

const PRIMITIVE_ITEM_KINDS = ['string', 'number', 'integer'];

// One form field per top-level parameter. `kind` is how the form edits it:
// string | number | integer | boolean | enum | array (comma separated primitives) | json.
export function fieldsFromSchema(inputSchema) {
    const root = inputSchema || {};
    const required = Array.isArray(root.required) ? root.required : [];
    return Object.entries(root.properties || {}).map(([name, propertySchema]) => {
        const normalized = normalizeSchema(propertySchema, root);
        let kind = normalized.kind;
        let itemKind = null;
        if (kind === 'array') {
            itemKind = normalized.items ? normalizeSchema(normalized.items, root).kind : 'unknown';
            if (!PRIMITIVE_ITEM_KINDS.includes(itemKind)) kind = 'json';
        } else if (kind === 'object' || kind === 'unknown') {
            kind = 'json';
        }
        return {
            name,
            kind,
            itemKind,
            required: required.includes(name),
            nullable: normalized.nullable,
            enum: normalized.enum,
            hasDefault: normalized.hasDefault,
            default: normalized.default,
            description: normalized.description || '',
            type: typeLabel(normalized, root),
            isColumn: kind === 'string' && isColumnParam(name),
        };
    });
}

function valueToText(field, value) {
    if (value === undefined || value === null) return '';
    if (field.kind === 'json') return typeof value === 'string' ? value : JSON.stringify(value, null, 2);
    if (field.kind === 'array') return Array.isArray(value) ? value.join(', ') : String(value);
    if (field.kind === 'boolean') return value === true || value === 'true' ? 'true' : value === false || value === 'false' ? 'false' : '';
    return String(value);
}

// Initial text for each form control. Priority: last args used this session, then the
// tool's example args (preview_examples.json), then the schema default.
export function initialValues(fields, saved = {}, example = {}) {
    const values = {};
    fields.forEach(field => {
        if (Object.prototype.hasOwnProperty.call(saved, field.name)) {
            values[field.name] = valueToText(field, saved[field.name]);
        } else if (Object.prototype.hasOwnProperty.call(example, field.name)) {
            values[field.name] = valueToText(field, example[field.name]);
        } else if (field.hasDefault && field.default !== null) {
            values[field.name] = valueToText(field, field.default);
        } else {
            values[field.name] = '';
        }
    });
    return values;
}

function parseNumber(text, integer) {
    const trimmed = text.trim();
    if (!/^[-+]?(\d+\.?\d*|\.\d+)([eE][-+]?\d+)?$/.test(trimmed)) return { error: 'must be a number' };
    const number = Number(trimmed);
    if (integer && !Number.isInteger(number)) return { error: 'must be a whole number' };
    return { value: number };
}

// Form text -> tool arguments. Optional fields left empty are omitted (the tool's own
// default applies); required fields left empty are errors.
export function buildArgs(fields, values) {
    const args = {};
    const errors = {};
    fields.forEach(field => {
        const text = String(values[field.name] ?? '');
        if (text.trim() === '') {
            if (field.required) errors[field.name] = 'is required';
            return;
        }
        if (field.kind === 'number' || field.kind === 'integer') {
            const parsed = parseNumber(text, field.kind === 'integer');
            if (parsed.error) errors[field.name] = parsed.error;
            else args[field.name] = parsed.value;
        } else if (field.kind === 'boolean') {
            args[field.name] = text === 'true';
        } else if (field.kind === 'array') {
            const parts = text.split(',').map(part => part.trim()).filter(Boolean);
            const converted = parts.map(part => (field.itemKind === 'string' ? { value: part } : parseNumber(part, field.itemKind === 'integer')));
            const failed = converted.find(item => item.error);
            if (failed) errors[field.name] = `each item ${failed.error}`;
            else args[field.name] = converted.map(item => item.value);
        } else if (field.kind === 'json') {
            try {
                args[field.name] = JSON.parse(text);
            } catch {
                errors[field.name] = 'must be valid JSON';
            }
        } else {
            args[field.name] = text;
        }
    });
    return { args, errors };
}
