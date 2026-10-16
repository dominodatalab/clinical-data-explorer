// Shared renderer for the raw output of a data tool.
//
// Used by both the admin "Try it" preview (chat_ui/admin/chat-tools.js) and the
// chat result cards for tools whose output is hidden from the AI
// (modules/chat.js), so an admin sees exactly what a user will see.
//
// Two steps:
//   1. `planOutput(value)` (pure, unit-tested without a DOM) decides how any JSON
//      value is laid out, and which parts can also be drawn as a chart.
//   2. `renderToolOutput(value, opts)` builds the DOM for that plan.
//
// Layout rules. They go by the shape of the data, never by tool name, so a new
// tool gets them without changes here:
//   - array of objects        -> table (first MAX_TABLE_ROWS rows, with a note)
//   - array of scalar tuples  -> table without a header row
//   - array of scalars        -> chips
//   - object of scalars       -> key/value table
//   - object of flat objects  -> matrix table, one row per outer key
//   - any other object        -> summary strip of its scalar fields, then one
//                                section per nested field, each laid out by these
//                                same rules. When there is a main table, long
//                                secondary parts go under "More details".
//   - anything else nested    -> collapsible JSON tree
//   - string and scalar       -> <pre>
//
// Chart rules, also by shape. A part that matches is shown as a chart first, with
// a Chart/Table toggle:
//   - records with bin_start, bin_end, count, or an object with
//     parallel `bins` (n+1 edges) and `counts` (n)            -> histogram
//   - an object with numeric min, q1, median, q3, max         -> box plot
//   - a square object of objects with values in [-1, 1]       -> heatmap
//   - records with exactly two fields:
//       date + number                                         -> line
//       number + number                                       -> line if x increases, else scatter
//       text + number (up to MAX_CHART_CATEGORIES rows)       -> bar
//   - an object of numbers keyed by data values (not by
//     statistic names such as mean or max)                    -> bar
// A `chart_type` field next to the data ("scatter", "line", "area", ...) settles
// the line-or-scatter choice. Chart payloads use the shapes of
// modules/chart-renderers.js.
//
// Safety: every value is inserted with `textContent`. Tool output (data values,
// column names, anything) is never passed to `innerHTML`. Charts are drawn by the
// caller's `renderChart(element, spec)`. Highcharts reads markup in label text, so
// every string in a chart spec is HTML-escaped first (`escapeChartSpec`): a value
// like "<65" is shown as written. Without `renderChart`, only tables are shown.

export const MAX_TABLE_ROWS = 200;
export const MAX_TREE_CHILDREN = 200;
export const MAX_CHART_CATEGORIES = 50;
export const MAX_CHART_POINTS = 5000;
const MAX_TREE_DEPTH = 8;
const MAX_CELL_CHARS = 500;
const MAX_CHIPS = 40;
// Cells longer than this wrap; shorter ones stay on one line and the table scrolls sideways.
const LONG_CELL_CHARS = 40;
// With a main table, secondary parts bigger than this go under "More details".
const MAX_INLINE_SECONDARY = 10;

// Keys that name a statistic. An object keyed by these is a set of different
// measures (not comparable on one axis), so it is never drawn as a bar chart.
const STAT_KEYS = new Set([
    'count', 'mean', 'median', 'std', 'var', 'variance', 'min', 'max', 'sum', 'mode',
    'q1', 'q3', 'iqr', '25%', '50%', '75%', 'total', 'total_count', 'non_null_count',
    'null_count', 'unique_count', 'missing_values', 'unique_values',
]);
const ISO_DATE = /^\d{4}-\d{2}-\d{2}([T ]\d{2}:\d{2}(:\d{2}(\.\d+)?)?)?(Z|[+-]\d{2}:?\d{2})?$/;
const LINE_HINTS = ['line', 'area', 'time_series'];

function isPlainObject(value) {
    return value !== null && typeof value === 'object' && !Array.isArray(value);
}

function isScalar(value) {
    return value === null || ['string', 'number', 'boolean'].includes(typeof value);
}

function isNumber(value) {
    return typeof value === 'number' && Number.isFinite(value);
}

function isNumberOrNull(value) {
    return value === null || isNumber(value);
}

function isDateString(value) {
    return typeof value === 'string' && ISO_DATE.test(value);
}

function isRecords(value) {
    return Array.isArray(value) && value.length > 0 && value.every(isPlainObject);
}

function isScalarList(value) {
    return Array.isArray(value) && value.every(isScalar);
}

function isFlatObject(value) {
    return isPlainObject(value) && Object.keys(value).length > 0 && Object.values(value).every(isScalar);
}

function isNumberList(value) {
    return Array.isArray(value) && value.length > 0 && value.every(isNumber);
}

function recordColumns(rows) {
    const columns = [];
    rows.forEach(row => Object.keys(row).forEach(key => {
        if (!columns.includes(key)) columns.push(key);
    }));
    return columns;
}

// ---- charts -------------------------------------------------------------

// Sibling fields that name what a record field holds, e.g. `x_column: "HEIGHTBL"`.
const LABEL_HINTS = {
    x: ['date_column'],
    y: ['value_column'],
    label: ['category_column', 'column', 'feature'],
    value: ['aggregation'],
};

function axisLabel(key, context) {
    for (const hint of [`${key}_column`, ...(LABEL_HINTS[key] || [])]) {
        if (typeof context[hint] === 'string' && context[hint]) return context[hint];
    }
    return key;
}

function histogramChart(starts, ends, counts, context) {
    return {
        type: 'histogram',
        data: {
            feature: String(context.column ?? context.feature ?? ''),
            bins: [...starts, ends[ends.length - 1]],
            counts,
        },
    };
}

function lineChart(categories, rows, valueKey, context) {
    const label = axisLabel(valueKey, context);
    return {
        type: 'line',
        data: { categories, series: [{ name: label, data: rows.map(row => row[valueKey]) }], yAxisTitle: label },
    };
}

function chartForRecords(rows, context) {
    if (rows.length > MAX_CHART_POINTS) return null;
    const all = (key, check) => rows.every(row => check(row[key]));

    if (['bin_start', 'bin_end', 'count'].every(key => all(key, isNumber))) {
        return histogramChart(rows.map(r => r.bin_start), rows.map(r => r.bin_end), rows.map(r => r.count), context);
    }
    const columns = recordColumns(rows);
    if (columns.length !== 2) return null;
    const [a, b] = columns;
    const hint = typeof context.chart_type === 'string' ? context.chart_type.toLowerCase() : '';

    for (const [x, y] of [[a, b], [b, a]]) {
        if (all(x, isDateString) && all(y, isNumberOrNull)) {
            return lineChart(rows.map(row => row[x].replace('T00:00:00', '')), rows, y, context);
        }
    }
    if (all(a, isNumber) && all(b, isNumberOrNull)) {
        const increasing = rows.every((row, i) => i === 0 || row[a] > rows[i - 1][a]);
        if (LINE_HINTS.includes(hint) || (hint !== 'scatter' && increasing)) {
            return lineChart(rows.map(row => formatNumber(row[a])), rows, b, context);
        }
        return {
            type: 'scatter',
            data: {
                xLabel: axisLabel(a, context),
                yLabel: axisLabel(b, context),
                points: rows.filter(row => isNumber(row[b])).map(row => [row[a], row[b]]),
            },
        };
    }
    if (rows.length > MAX_CHART_CATEGORIES) return null;
    for (const [x, y] of [[a, b], [b, a]]) {
        if (all(x, value => typeof value === 'string') && all(y, isNumberOrNull)) {
            return {
                type: 'bar',
                data: {
                    categories: rows.map(row => row[x]),
                    values: rows.map(row => row[y]),
                    xAxisTitle: axisLabel(x, context),
                    yAxisTitle: axisLabel(y, context),
                },
            };
        }
    }
    return null;
}

function boxplotChart(object) {
    if (!['min', 'q1', 'median', 'q3', 'max'].every(key => isNumber(object[key]))) return null;
    return {
        type: 'boxplot',
        data: {
            feature: String(object.feature ?? object.column ?? ''),
            min: object.min,
            q1: object.q1,
            median: object.median,
            q3: object.q3,
            max: object.max,
            outliers: Array.isArray(object.outliers) ? object.outliers.filter(isNumber) : [],
        },
    };
}

function heatmapChart(object) {
    const features = Object.keys(object);
    if (features.length < 2 || features.length > MAX_CHART_CATEGORIES) return null;
    const square = features.every(row => Object.keys(object[row]).length === features.length
        && features.every(col => isNumber(object[row][col]) && object[row][col] >= -1 && object[row][col] <= 1));
    if (!square) return null;
    return { type: 'heatmap', data: { features, matrix: features.map(row => features.map(col => object[row][col])) } };
}

function scalarMapChart(object, context) {
    const entries = Object.entries(object);
    if (entries.length < 2 || entries.length > MAX_CHART_CATEGORIES) return null;
    if (!entries.every(([, value]) => isNumber(value))) return null;
    if (entries.some(([key]) => STAT_KEYS.has(key.toLowerCase()))) return null;
    return {
        type: 'bar',
        data: {
            categories: entries.map(([key]) => key),
            values: entries.map(([, value]) => value),
            xAxisTitle: String(context.feature ?? context.column ?? ''),
            yAxisTitle: '',
        },
    };
}

function escapeChartText(text) {
    return text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

// A copy of `spec` with every string HTML-escaped, for Highcharts' label markup.
export function escapeChartSpec(spec) {
    if (typeof spec === 'string') return escapeChartText(spec);
    if (Array.isArray(spec)) return spec.map(escapeChartSpec);
    if (isPlainObject(spec)) {
        return Object.fromEntries(Object.entries(spec).map(([key, value]) => [key, escapeChartSpec(value)]));
    }
    return spec;
}

// ---- plan ---------------------------------------------------------------
//
// Plan nodes:
//   { kind: 'table', rows, chart, headless }        array of records (or of tuples)
//   { kind: 'kv', entries, chart }                  object of scalars
//   { kind: 'matrix', columns, rows, chart }        object of flat objects; rows are { label, values }
//   { kind: 'list', items }                         array of scalars
//   { kind: 'sections', summary, parts, primary }   summary: [key, scalar] pairs;
//                                                   parts: { label, plan }; primary: index of the main part or -1
//   { kind: 'tree', value }
//   { kind: 'pre', text }
// `chart` is null or { type, data } for modules/chart-renderers.js.

// `bins` (n+1 edges) + `counts` (n) -> one list of {bin_start, bin_end, count} records.
function mergeParallelHistogram(object) {
    const { bins, counts, ...rest } = object;
    if (!isNumberList(bins) || !isNumberList(counts) || bins.length !== counts.length + 1) return object;
    return {
        ...rest,
        histogram: counts.map((count, i) => ({ bin_start: bins[i], bin_end: bins[i + 1], count })),
    };
}

function mainPart(parts) {
    const charted = parts.findIndex(part => part.plan.chart);
    if (charted >= 0) return charted;
    let primary = -1;
    let most = 0;
    parts.forEach((part, i) => {
        if (part.plan.kind === 'table' && part.plan.rows.length > most) {
            most = part.plan.rows.length;
            primary = i;
        }
    });
    return primary;
}

export function planOutput(value, context = {}) {
    if (Array.isArray(value)) {
        if (isRecords(value)) return { kind: 'table', rows: value, chart: chartForRecords(value, context), headless: false };
        if (isScalarList(value)) return { kind: 'list', items: value };
        if (value.every(item => isScalarList(item) && item.length > 0)) {
            // Tuples have no field names to label a chart's axes with, so they stay a table.
            const rows = value.map(item => Object.fromEntries(item.map((cell, i) => [String(i + 1), cell])));
            return { kind: 'table', rows, chart: null, headless: true };
        }
        return { kind: 'tree', value };
    }
    if (!isPlainObject(value)) return { kind: 'pre', text: value === undefined ? '' : String(value) };

    const object = mergeParallelHistogram(value);
    const entries = Object.entries(object);
    if (entries.length === 0) return { kind: 'tree', value };

    if (entries.every(([, v]) => isScalar(v))) {
        return { kind: 'kv', entries, chart: boxplotChart(object) || scalarMapChart(object, context) };
    }
    // A box plot's five numbers plus its list of outliers.
    if (entries.every(([, v]) => isScalar(v) || isScalarList(v)) && boxplotChart(object)) {
        return { kind: 'kv', entries, chart: boxplotChart(object) };
    }
    if (entries.length >= 2 && entries.every(([, v]) => isFlatObject(v))) {
        return {
            kind: 'matrix',
            columns: recordColumns(entries.map(([, inner]) => inner)),
            rows: entries.map(([label, values]) => ({ label, values })),
            chart: heatmapChart(object),
        };
    }

    const summary = entries.filter(([, v]) => isScalar(v));
    const childContext = { ...context, ...Object.fromEntries(summary) };
    const parts = entries
        .filter(([, v]) => !isScalar(v))
        .map(([label, child]) => ({ label, plan: planOutput(child, childContext) }));
    return { kind: 'sections', summary, parts, primary: mainPart(parts) };
}

function planSize(plan) {
    if (plan.kind === 'list') return plan.items.length;
    if (plan.kind === 'kv') return plan.entries.length;
    if (plan.kind === 'table' || plan.kind === 'matrix') return plan.rows.length;
    return Infinity;
}

// ---- formatting ---------------------------------------------------------

// At most 4 decimals; the exact value goes in the cell's tooltip.
export function formatNumber(value) {
    if (Number.isInteger(value)) return String(value);
    const abs = Math.abs(value);
    if (abs !== 0 && (abs < 0.001 || abs >= 1e15)) return value.toPrecision(4);
    return String(Number(value.toFixed(4)));
}

function clip(text) {
    return text.length > MAX_CELL_CHARS ? `${text.slice(0, MAX_CELL_CHARS)}…` : text;
}

export function formatScalar(value) {
    if (value === null) return 'null';
    if (value === undefined) return '';
    if (typeof value === 'number') return formatNumber(value);
    if (isScalarList(value)) return clip(value.map(formatScalar).join(', '));
    if (typeof value === 'object') {
        let text;
        try {
            text = JSON.stringify(value);
        } catch {
            text = String(value);
        }
        return clip(text);
    }
    return clip(String(value));
}

function humanize(key) {
    return String(key).replace(/_/g, ' ');
}

// ---- DOM ----------------------------------------------------------------

function element(doc, tag, className, text) {
    const el = doc.createElement(tag);
    if (className) el.className = className;
    if (text !== undefined) el.textContent = text;
    return el;
}

function valueCell(doc, tag, value) {
    const text = formatScalar(value);
    let className = '';
    if (typeof value === 'number') className = 'tool-output-number';
    else if (text.length > LONG_CELL_CHARS) className = 'tool-output-long';
    const cell = element(doc, tag, className, text);
    if (typeof value === 'number' && text !== String(value)) cell.setAttribute('title', String(value));
    return cell;
}

function renderTable(doc, rows, { maxRows, headless }) {
    const wrapper = element(doc, 'div', 'tool-output-table-wrap');
    const shown = rows.slice(0, maxRows);
    const columns = recordColumns(shown);

    const table = element(doc, 'table', 'tool-output-table');
    if (!headless) {
        const head = element(doc, 'tr');
        columns.forEach(column => head.appendChild(element(doc, 'th', '', column)));
        table.appendChild(element(doc, 'thead')).appendChild(head);
    }
    const body = element(doc, 'tbody');
    shown.forEach(row => {
        const tr = element(doc, 'tr');
        columns.forEach(column => tr.appendChild(valueCell(doc, 'td', row[column])));
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

function renderMatrix(doc, plan) {
    const wrapper = element(doc, 'div', 'tool-output-table-wrap');
    const table = element(doc, 'table', 'tool-output-table tool-output-matrix');
    const head = element(doc, 'tr');
    head.appendChild(element(doc, 'th', '', ''));
    plan.columns.forEach(column => head.appendChild(element(doc, 'th', '', column)));
    table.appendChild(element(doc, 'thead')).appendChild(head);
    const body = element(doc, 'tbody');
    plan.rows.forEach(({ label, values }) => {
        const tr = element(doc, 'tr');
        tr.appendChild(element(doc, 'th', 'tool-output-row-label', label));
        plan.columns.forEach(column => tr.appendChild(valueCell(doc, 'td', values[column])));
        body.appendChild(tr);
    });
    table.appendChild(body);
    wrapper.appendChild(table);
    return wrapper;
}

function renderKeyValue(doc, entries) {
    const table = element(doc, 'table', 'tool-output-table tool-output-kv');
    const body = element(doc, 'tbody');
    entries.forEach(([key, value]) => {
        const tr = element(doc, 'tr');
        tr.appendChild(element(doc, 'th', '', key));
        tr.appendChild(valueCell(doc, 'td', value));
        body.appendChild(tr);
    });
    table.appendChild(body);
    return table;
}

function renderList(doc, items) {
    const list = element(doc, 'ul', 'tool-output-chips');
    if (items.length === 0) {
        list.appendChild(element(doc, 'li', 'tool-output-empty', 'none'));
        return list;
    }
    items.slice(0, MAX_CHIPS).forEach(item => {
        list.appendChild(element(doc, 'li', 'tool-output-chip', formatScalar(item)));
    });
    if (items.length > MAX_CHIPS) {
        list.appendChild(element(doc, 'li', 'tool-output-note', `+${items.length - MAX_CHIPS} more`));
    }
    return list;
}

function renderSummary(doc, entries) {
    const strip = element(doc, 'dl', 'tool-output-summary-strip');
    entries.forEach(([key, value]) => {
        const field = element(doc, 'div', 'tool-output-field');
        field.appendChild(element(doc, 'dt', '', humanize(key)));
        field.appendChild(valueCell(doc, 'dd', value));
        strip.appendChild(field);
    });
    return strip;
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

// The chart, with a toggle to the table it was drawn from.
function withChart(doc, chart, tableView, opts) {
    if (!chart || !opts.renderChart) return tableView;
    const wrap = element(doc, 'div', 'tool-output-viz');
    const toggle = element(doc, 'div', 'tool-output-toggle');
    toggle.setAttribute('role', 'group');
    toggle.setAttribute('aria-label', 'Show as');
    const chartButton = element(doc, 'button', 'tool-output-toggle-button', 'Chart');
    const tableButton = element(doc, 'button', 'tool-output-toggle-button', 'Table');
    const chartView = element(doc, 'div', 'tool-output-chart');
    chartView.setAttribute('data-chart-type', chart.type);
    const tableWrap = element(doc, 'div', 'tool-output-table-view');
    tableWrap.appendChild(tableView);

    const show = showChart => {
        chartView.hidden = !showChart;
        tableWrap.hidden = showChart;
        chartButton.setAttribute('aria-pressed', String(showChart));
        tableButton.setAttribute('aria-pressed', String(!showChart));
    };
    [chartButton, tableButton].forEach(button => button.setAttribute('type', 'button'));
    chartButton.addEventListener('click', () => show(true));
    tableButton.addEventListener('click', () => show(false));
    toggle.appendChild(chartButton);
    toggle.appendChild(tableButton);
    wrap.appendChild(toggle);
    wrap.appendChild(chartView);
    wrap.appendChild(tableWrap);
    show(true);
    // Drawn after the caller has attached the output, so the chart can size itself.
    const spec = escapeChartSpec({ type: chart.type, title: '', data: chart.data });
    opts.schedule(() => opts.renderChart(chartView, spec));
    return wrap;
}

function renderPart(doc, part, opts) {
    const section = element(doc, 'section', 'tool-output-part');
    section.appendChild(element(doc, 'div', 'tool-output-part-label', humanize(part.label)));
    section.appendChild(renderPlan(doc, part.plan, opts));
    return section;
}

function isSecondary(plan, part, index) {
    if (plan.primary < 0 || index === plan.primary || part.plan.kind === 'table') return false;
    return part.plan.kind === 'list' || planSize(part.plan) > MAX_INLINE_SECONDARY;
}

function renderSections(doc, plan, opts) {
    const wrap = element(doc, 'div', 'tool-output-sections');
    if (plan.summary.length > 0) wrap.appendChild(renderSummary(doc, plan.summary));

    const more = [];
    plan.parts.forEach((part, index) => {
        if (isSecondary(plan, part, index)) more.push(part);
        else wrap.appendChild(renderPart(doc, part, opts));
    });
    if (more.length > 0) {
        const details = element(doc, 'details', 'tool-output-more');
        details.appendChild(element(
            doc, 'summary', '', `More details (${more.map(part => humanize(part.label)).join(', ')})`,
        ));
        more.forEach(part => details.appendChild(renderPart(doc, part, opts)));
        wrap.appendChild(details);
    }
    return wrap;
}

function renderPlan(doc, plan, opts) {
    switch (plan.kind) {
        case 'table':
            return withChart(doc, plan.chart, renderTable(doc, plan.rows, { maxRows: opts.maxRows, headless: plan.headless }), opts);
        case 'kv':
            return withChart(doc, plan.chart, renderKeyValue(doc, plan.entries), opts);
        case 'matrix':
            return withChart(doc, plan.chart, renderMatrix(doc, plan), opts);
        case 'list':
            return renderList(doc, plan.items);
        case 'sections':
            return renderSections(doc, plan, opts);
        case 'tree':
            return renderTreeNode(doc, null, plan.value, 0);
        default:
            return element(doc, 'pre', 'tool-output-pre', plan.text);
    }
}

function nextFrame(callback) {
    if (typeof requestAnimationFrame === 'function') requestAnimationFrame(callback);
    else setTimeout(callback, 0);
}

// `renderChart(element, spec)` draws a chart (modules/chart-renderers.js's
// `renderChartSpec`); leave it out to show tables only. `schedule` runs the draw
// once the output is attached (tests pass one that runs immediately).
export function renderToolOutput(value, {
    doc = document,
    maxRows = MAX_TABLE_ROWS,
    renderChart = null,
    schedule = nextFrame,
} = {}) {
    const plan = planOutput(value);
    const container = element(doc, 'div', `tool-output tool-output-${plan.kind}`);
    container.setAttribute('data-kind', plan.kind);
    container.appendChild(renderPlan(doc, plan, { maxRows, renderChart, schedule }));
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
