import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { test } from 'node:test';
import vm from 'node:vm';

const sourcePath = new URL('../../../chat_ui/modules/tool-output.js', import.meta.url);
const source = await readFile(sourcePath, 'utf8');
const fixtures = JSON.parse(await readFile(new URL('../../fixtures/tool_outputs.json', import.meta.url), 'utf8')).outputs;

async function loadModule() {
    const context = vm.createContext({ console });
    const module = new vm.SourceTextModule(source, { context, identifier: String(sourcePath) });
    await module.link(() => {
        throw new Error('tool-output.js should not import other modules');
    });
    await module.evaluate();
    return module.namespace;
}

const mod = await loadModule();
// Plans come from another vm context; round-trip them so deepEqual compares plain values.
const plan = (value, context) => JSON.parse(JSON.stringify(mod.planOutput(value, context)));

// Minimal DOM: enough for the renderer, and `innerHTML` is a hard failure because
// tool output must only ever be inserted as text.
class FakeElement {
    constructor(tagName) {
        this.tagName = tagName;
        this.children = [];
        this.attributes = {};
        this.listeners = {};
        this.className = '';
        this.open = false;
        this.hidden = false;
        this._text = '';
    }

    appendChild(child) {
        this.children.push(child);
        return child;
    }

    setAttribute(name, value) {
        this.attributes[name] = String(value);
    }

    addEventListener(type, listener) {
        (this.listeners[type] ||= []).push(listener);
    }

    click() {
        (this.listeners.click || []).forEach(listener => listener());
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

    byClass(name) {
        return this.find(el => el.className.split(' ').includes(name));
    }
}

const fakeDocument = { createElement: tag => new FakeElement(tag) };

function render(value, options = {}) {
    return mod.renderToolOutput(value, { doc: fakeDocument, ...options });
}

// Renders with a chart callback that records what it was asked to draw.
function renderWithCharts(value) {
    const drawn = [];
    const out = render(value, {
        renderChart: (element, spec) => drawn.push({ element, spec }),
        schedule: callback => callback(),
    });
    return { out, drawn };
}

function chartTypes(node, found = []) {
    if (node.chart) found.push(node.chart.type);
    (node.parts || []).forEach(part => chartTypes(part.plan, found));
    return found;
}

// ----- layout -----

test('planOutput picks a layout for each basic shape', () => {
    assert.equal(plan([{ a: 1 }, { a: 2 }]).kind, 'table');
    assert.equal(plan({ mean: 1.5, name: 'x', ok: true, none: null }).kind, 'kv');
    assert.equal(plan({ a: { b: 1 }, c: { b: 2 } }).kind, 'matrix');
    assert.equal(plan({ rows: [1, 2] }).kind, 'sections');
    assert.equal(plan([1, 2, 3]).kind, 'list');
    assert.equal(plan([]).kind, 'list');
    assert.equal(plan([['a', 1], ['b', 2]]).kind, 'table');
    assert.equal(plan([{ a: 1 }, 5]).kind, 'tree');
    assert.equal(plan({}).kind, 'tree');
    assert.equal(plan('plain text').kind, 'pre');
    assert.equal(plan(42).kind, 'pre');
    assert.equal(plan(null).kind, 'pre');
});

test('an object with nested data becomes a summary strip plus one section per nested field', () => {
    const result = plan({
        page: 1,
        total_rows: 9,
        data: [{ id: 'a', age: 70, arm: 'P' }, { id: 'b', age: 80, arm: 'X' }],
        columns: ['id', 'age', 'arm'],
    });

    assert.equal(result.kind, 'sections');
    assert.deepEqual(result.summary, [['page', 1], ['total_rows', 9]]);
    assert.deepEqual(result.parts.map(part => [part.label, part.plan.kind]), [['data', 'table'], ['columns', 'list']]);
    assert.equal(result.primary, 0);
    assert.equal(result.parts[0].plan.chart, null);
});

test('an object of flat objects becomes a matrix with the union of inner keys as columns', () => {
    const result = plan({ AGE: { count: 36, mean: 76.1 }, BMIBL: { count: 36, max: 32.3 } });

    assert.equal(result.kind, 'matrix');
    assert.deepEqual(result.columns, ['count', 'mean', 'max']);
    assert.deepEqual(result.rows.map(row => row.label), ['AGE', 'BMIBL']);
    assert.equal(result.chart, null);
});

test('arrays of objects render as a table with the union of columns', () => {
    const out = render([{ a: 1, b: 'x' }, { a: 2, c: null }]);

    assert.equal(out.attributes['data-kind'], 'table');
    const headers = out.byTag('th').map(el => el.textContent);
    assert.deepEqual(headers, ['a', 'b', 'c']);
    const cells = out.byTag('tbody')[0].byTag('tr').map(tr => tr.byTag('td').map(td => td.textContent));
    assert.deepEqual(cells, [['1', 'x', ''], ['2', '', 'null']]);
});

test('tables are limited to 200 rows with a "showing" note', () => {
    const rows = Array.from({ length: 250 }, (_, i) => ({ i }));

    const out = render(rows);

    assert.equal(out.byTag('tbody')[0].byTag('tr').length, 200);
    assert.match(out.textContent, /Showing 200 of 250 rows/);
});

test('small tables have no truncation note', () => {
    const out = render([{ i: 1 }]);

    assert.doesNotMatch(out.textContent, /Showing/);
});

test('flat objects render as a key/value table', () => {
    const out = render({ mean: 3.5, label: 'age' });

    assert.equal(out.attributes['data-kind'], 'kv');
    const rows = out.byTag('tr').map(tr => [tr.byTag('th')[0].textContent, tr.byTag('td')[0].textContent]);
    assert.deepEqual(rows, [['mean', '3.5'], ['label', 'age']]);
});

test('sections render the summary strip, a labelled part per nested field, and chips for lists', () => {
    const out = render({ column: 'ARM', total_unique: 2, values: ['Placebo', 'Xanomeline'] });

    assert.equal(out.attributes['data-kind'], 'sections');
    const fields = out.byClass('tool-output-field').map(field => [field.byTag('dt')[0].textContent, field.byTag('dd')[0].textContent]);
    assert.deepEqual(fields, [['column', 'ARM'], ['total unique', '2']]);
    assert.deepEqual(out.byClass('tool-output-part-label').map(el => el.textContent), ['values']);
    assert.deepEqual(out.byClass('tool-output-chip').map(el => el.textContent), ['Placebo', 'Xanomeline']);
});

test('with a main table, lists and long secondary parts go under "More details"', () => {
    const types = Object.fromEntries(Array.from({ length: 12 }, (_, i) => [`c${i}`, 'str']));
    const out = render({
        num_rows: 2,
        data: [{ a: 1, b: 2, c: 3 }, { a: 4, b: 5, c: 6 }],
        columns: ['a', 'b', 'c'],
        column_types: types,
        stats: { rows: 2 },
    });

    const more = out.byClass('tool-output-more');
    assert.equal(more.length, 1);
    assert.equal(more[0].byTag('summary')[0].textContent, 'More details (columns, column types)');
    const inline = out.children[0].children.filter(child => child.tagName === 'section');
    assert.deepEqual(inline.map(section => section.children[0].textContent), ['data', 'stats']);
});

test('without a main table nothing is collapsed', () => {
    const out = render({ num_rows: 2, columns: ['a', 'b'], numeric_columns: ['a'] });

    assert.equal(out.byClass('tool-output-more').length, 0);
    assert.equal(out.byClass('tool-output-part').length, 2);
});

test('empty lists say "none"', () => {
    const out = render({ n: 1, date_columns: [] });

    assert.deepEqual(out.byClass('tool-output-empty').map(el => el.textContent), ['none']);
});

test('lists of tuples become a headerless table, never a chart', () => {
    assert.equal(plan([['DCSREAS', 12], ['AGE', 0]]).chart, null);
    const out = render([['DCSREAS', 12], ['AGE', 0]]);

    assert.equal(out.byTag('thead').length, 0);
    assert.deepEqual(out.byTag('td').map(td => td.textContent), ['DCSREAS', '12', 'AGE', '0']);
});

test('matrices put the outer keys in the first column', () => {
    const out = render({ Placebo: { count: 12, mean: 76.5 }, Xanomeline: { count: 12, mean: 75.25 } });

    assert.deepEqual(out.byTag('thead')[0].byTag('th').map(th => th.textContent), ['', 'count', 'mean']);
    const rows = out.byTag('tbody')[0].byTag('tr').map(tr => tr.children.map(cell => cell.textContent));
    assert.deepEqual(rows, [['Placebo', '12', '76.5'], ['Xanomeline', '12', '75.25']]);
});

test('anything else nested renders as a collapsible tree, root open and children closed', () => {
    const out = render([{ mean: 1, tags: ['a', 'b'] }, 2]);

    assert.equal(out.attributes['data-kind'], 'tree');
    const nodes = out.byTag('details');
    assert.ok(nodes.length >= 2);
    assert.equal(nodes[0].open, true);
    assert.equal(nodes[1].open, false);
    assert.match(out.textContent, /\[2 items\]/);
    assert.match(out.textContent, /\{2 keys\}/);
    assert.match(out.textContent, /mean: 1/);
});

test('strings and scalars render in a <pre> without truncation', () => {
    const long = 'x'.repeat(2000);

    const out = render(long);

    assert.equal(out.attributes['data-kind'], 'pre');
    assert.equal(out.byTag('pre')[0].textContent, long);
    assert.equal(render(42).byTag('pre')[0].textContent, '42');
});

// ----- formatting -----

test('numbers show at most 4 decimals and keep the exact value in the tooltip', () => {
    assert.equal(mod.formatNumber(76.13888888888889), '76.1389');
    assert.equal(mod.formatNumber(27.200000000000003), '27.2');
    assert.equal(mod.formatNumber(14040), '14040');
    assert.equal(mod.formatNumber(0.00012345), '0.0001234');
    assert.equal(mod.formatNumber(-0.5), '-0.5');

    const out = render([{ mean: 76.13888888888889, n: 36 }]);
    const [mean, n] = out.byTag('td');
    assert.equal(mean.textContent, '76.1389');
    assert.equal(mean.attributes.title, '76.13888888888889');
    assert.equal(mean.className, 'tool-output-number');
    assert.equal(n.attributes.title, undefined);
});

test('lists of values inside a cell are joined with commas', () => {
    assert.equal(mod.formatScalar(['Placebo', 'Xanomeline', null]), 'Placebo, Xanomeline, null');
    assert.equal(mod.formatScalar([{ a: 1 }]), '[{"a":1}]');
});

test('very long cell values are shortened, and long text cells may wrap', () => {
    const out = render([{ blob: 'y'.repeat(2000), short: 'ok' }]);

    const [blob, short] = out.byTag('td');
    assert.ok(blob.textContent.length <= 501);
    assert.ok(blob.textContent.endsWith('…'));
    assert.equal(blob.className, 'tool-output-long');
    assert.equal(short.className, '');
});

// ----- charts -----

test('records with bin_start, bin_end and count are a histogram named after the column', () => {
    const result = plan({
        column: 'AGE',
        chart_data: [{ bin_start: 57, bin_end: 62, count: 2 }, { bin_start: 62, bin_end: 67, count: 1 }],
    });

    assert.deepEqual(result.parts[0].plan.chart, {
        type: 'histogram', data: { feature: 'AGE', bins: [57, 62, 67], counts: [2, 1] },
    });
});

test('parallel bins and counts are merged into bin ranges and drawn as a histogram', () => {
    const result = plan({ counts: [2, 0, 4], bins: [57, 60, 63, 66], feature: 'AGE' });

    assert.deepEqual(result.summary, [['feature', 'AGE']]);
    assert.equal(result.parts[0].label, 'histogram');
    assert.deepEqual(result.parts[0].plan.rows[1], { bin_start: 60, bin_end: 63, count: 0 });
    assert.deepEqual(result.parts[0].plan.chart.data, { feature: 'AGE', bins: [57, 60, 63, 66], counts: [2, 0, 4] });
});

test('mismatched bins and counts are left as they are', () => {
    const result = plan({ counts: [2, 0], bins: [1, 2], feature: 'AGE' });

    assert.deepEqual(result.parts.map(part => part.label), ['counts', 'bins']);
    assert.deepEqual(chartTypes(result), []);
});

test('five-number summaries are a box plot, with outliers', () => {
    const result = plan({ feature: 'WEIGHTBL', min: 45.8, q1: 54.4, median: 66.9, q3: 77.15, max: 101.2, outliers: [130, 'x'] });

    assert.equal(result.kind, 'kv');
    assert.deepEqual(result.chart, {
        type: 'boxplot',
        data: { feature: 'WEIGHTBL', min: 45.8, q1: 54.4, median: 66.9, q3: 77.15, max: 101.2, outliers: [130] },
    });
});

test('a square matrix of values in [-1, 1] is a heatmap; other matrices are not', () => {
    const corr = { A: { A: 1, B: 0.4 }, B: { A: 0.4, B: 1 } };
    assert.deepEqual(plan(corr).chart, { type: 'heatmap', data: { features: ['A', 'B'], matrix: [[1, 0.4], [0.4, 1]] } });

    assert.equal(plan({ A: { A: 1, B: 2 }, B: { A: 2, B: 1 } }).chart, null);
    assert.equal(plan({ A: { x: 0.1, y: 0.2 }, B: { x: 0.3, y: 0.4 } }).chart, null);
});

test('two numeric fields are a scatter, labelled from x_column and y_column', () => {
    const result = plan({
        x_column: 'HEIGHTBL',
        y_column: 'WEIGHTBL',
        chart_data: [{ x: 170, y: 79 }, { x: 150, y: 50 }, { x: 160, y: null }],
    });

    assert.deepEqual(result.parts[0].plan.chart, {
        type: 'scatter', data: { xLabel: 'HEIGHTBL', yLabel: 'WEIGHTBL', points: [[170, 79], [150, 50]] },
    });
});

test('two numeric fields with x increasing are a line, unless chart_type says scatter', () => {
    const rows = [{ x: 57.3, y: 87.1 }, { x: 60.3, y: 75.3 }];

    const line = plan({ y_column: 'WEIGHTBL', chart_data: rows }).parts[0].plan.chart;
    assert.equal(line.type, 'line');
    assert.deepEqual(line.data, {
        categories: ['57.3', '60.3'], series: [{ name: 'WEIGHTBL', data: [87.1, 75.3] }], yAxisTitle: 'WEIGHTBL',
    });

    assert.equal(plan({ chart_type: 'scatter', chart_data: rows }).parts[0].plan.chart.type, 'scatter');
    const unordered = [{ x: 3, y: 1 }, { x: 1, y: 2 }];
    assert.equal(plan({ chart_type: 'area', chart_data: unordered }).parts[0].plan.chart.type, 'line');
});

test('a date field and a numeric field are a line over the dates', () => {
    const result = plan({
        value_column: 'AGE',
        chart_data: [{ x: '2013-01-01T00:00:00', y: 82 }, { x: '2013-01-27T10:19:12', y: 83.5 }],
    });

    assert.deepEqual(result.parts[0].plan.chart, {
        type: 'line',
        data: {
            categories: ['2013-01-01', '2013-01-27T10:19:12'],
            series: [{ name: 'AGE', data: [82, 83.5] }],
            yAxisTitle: 'AGE',
        },
    });
});

test('a text field and a numeric field are a bar chart, up to 50 rows', () => {
    const result = plan({
        category_column: 'ARM',
        aggregation: 'count',
        chart_data: [{ label: 'Placebo', value: 12 }, { label: 'Xanomeline', value: 24 }],
    });
    assert.deepEqual(result.parts[0].plan.chart, {
        type: 'bar',
        data: { categories: ['Placebo', 'Xanomeline'], values: [12, 24], xAxisTitle: 'ARM', yAxisTitle: 'count' },
    });

    const many = Array.from({ length: 51 }, (_, i) => ({ label: `c${i}`, value: i }));
    assert.equal(plan(many).chart, null);
});

test('rows with more than two fields are never charted', () => {
    assert.equal(plan([{ id: 'a', x: 1, y: 2 }, { id: 'b', x: 2, y: 3 }]).chart, null);
});

test('an object of numbers keyed by data values is a bar chart; statistics are not', () => {
    const counts = plan({ feature: 'RACE', value_counts: { WHITE: 29, BLACK: 7 } });
    assert.deepEqual(counts.parts[0].plan.chart, {
        type: 'bar', data: { categories: ['WHITE', 'BLACK'], values: [29, 7], xAxisTitle: 'RACE', yAxisTitle: '' },
    });

    assert.equal(plan({ mean: 76.1, median: 78, max: 87 }).chart, null);
    assert.equal(plan({ only: 1 }).chart, null);
    assert.equal(plan({ a: 1, b: 'x' }).chart, null);
});

test('escapeChartSpec escapes every string and leaves numbers alone', () => {
    const escaped = JSON.parse(JSON.stringify(mod.escapeChartSpec({
        type: 'bar',
        data: { categories: ['<65', 'A & B', '<img src=x>'], values: [1, null], xAxisTitle: '>80' },
    })));

    assert.deepEqual(escaped, {
        type: 'bar',
        data: { categories: ['&lt;65', 'A &amp; B', '&lt;img src=x&gt;'], values: [1, null], xAxisTitle: '&gt;80' },
    });
});

test('charts are shown first with a Chart/Table toggle and drawn with escaped text', () => {
    const { out, drawn } = renderWithCharts({ chart_data: [{ label: '<65', value: 3 }, { label: '>80', value: 5 }] });

    assert.equal(drawn.length, 1);
    const [{ element, spec }] = drawn;
    assert.equal(element.className, 'tool-output-chart');
    assert.equal(element.attributes['data-chart-type'], 'bar');
    assert.deepEqual(JSON.parse(JSON.stringify(spec.data.categories)), ['&lt;65', '&gt;80']);
    assert.equal(spec.title, '');

    const [chartButton, tableButton] = out.byClass('tool-output-toggle-button');
    const tableView = out.byClass('tool-output-table-view')[0];
    assert.equal(chartButton.attributes['aria-pressed'], 'true');
    assert.equal(element.hidden, false);
    assert.equal(tableView.hidden, true);
    // The table view still has the raw values as text.
    assert.deepEqual(tableView.byTag('td').map(td => td.textContent), ['<65', '3', '>80', '5']);

    tableButton.click();
    assert.equal(element.hidden, true);
    assert.equal(tableView.hidden, false);
    assert.equal(tableButton.attributes['aria-pressed'], 'true');
    chartButton.click();
    assert.equal(element.hidden, false);
});

test('without renderChart, chartable output is shown as a table only', () => {
    const out = render({ chart_data: [{ label: 'a', value: 3 }, { label: 'b', value: 5 }] });

    assert.equal(out.byClass('tool-output-toggle').length, 0);
    assert.equal(out.byClass('tool-output-chart').length, 0);
    assert.equal(out.byTag('table').length, 1);
});

test('charts are drawn through schedule, after the caller attaches the output', () => {
    const queued = [];
    const drawn = [];
    render([{ label: 'a', value: 1 }, { label: 'b', value: 2 }], {
        renderChart: () => drawn.push(1),
        schedule: callback => queued.push(callback),
    });

    assert.equal(drawn.length, 0);
    queued.forEach(callback => callback());
    assert.equal(drawn.length, 1);
});

// ----- every data tool, on its real output -----

// [layout of the whole output, chart types anywhere in it]
const EXPECTED = {
    get_dataset_head_dataset_head_get: ['table', []],
    get_feature_stats_feature_stats_get: ['table', []],
    get_column_stats_table_column_stats__column__get: ['kv', []],
    get_dataset_metadata_dataset_metadata_get: ['kv', []],
    get_target_correlation_correlation_target_get: ['kv', ['bar']],
    get_feature_comparison_feature_comparison_get: ['sections', ['scatter']],
    get_xy_chart_data_chart_xy_data_post: ['sections', ['scatter']],
    variant__xy_bucketed_numeric_x: ['sections', ['line']],
    variant__xy_categorical_x: ['sections', ['bar']],
    get_time_series_data_chart_time_series_post: ['sections', ['line']],
    get_histogram_data_chart_histogram_post: ['sections', ['histogram']],
    variant__histogram_categorical: ['sections', ['bar']],
    get_feature_histogram_feature_histogram__feature__get: ['sections', ['histogram']],
    get_feature_boxplot_feature_boxplot__feature__get: ['kv', ['boxplot']],
    variant__boxplot_with_outliers: ['kv', ['boxplot']],
    get_bar_chart_data_chart_bar_aggregation_post: ['sections', ['bar']],
    variant__bar_mean: ['sections', ['bar']],
    get_value_counts_feature_value_counts__feature__get: ['sections', ['bar']],
    variant__value_counts_arm: ['sections', ['bar']],
    get_correlation_matrix_correlation_matrix_get: ['matrix', ['heatmap']],
    get_dataset_description_dataset_describe_get: ['matrix', []],
    get_group_analysis_feature_group_analysis_get: ['sections', []],
    get_table_data_table_data_post: ['sections', []],
    expression_filter_table_expression_filter_post: ['sections', []],
    get_dataset_data_dataset_data_get: ['sections', []],
    get_dataset_info_dataset_info_get: ['sections', []],
    get_column_values_table_column_values__column__get: ['sections', []],
    get_expression_samples_table_expression_samples_get: ['sections', []],
    get_table_summary_table_summary_post: ['sections', ['bar']],
};

test('every data tool output in the fixture has an expected layout', () => {
    assert.deepEqual(Object.keys(EXPECTED).sort(), Object.keys(fixtures).sort());
});

for (const [name, [kind, charts]] of Object.entries(EXPECTED)) {
    test(`real output: ${name} -> ${kind}${charts.length ? ` + ${charts.join(', ')}` : ''}`, () => {
        const output = fixtures[name].output;
        const result = plan(output);

        assert.equal(result.kind, kind);
        assert.deepEqual(chartTypes(result), charts);
        const { out, drawn } = renderWithCharts(output);
        assert.equal(drawn.length, charts.length);
        // No raw JSON tree for any real tool output.
        assert.equal(out.byClass('tool-output-node').length, 0);
    });
}

test('real output: the row tables keep their paging fields in the summary strip', () => {
    const result = plan(fixtures.get_table_data_table_data_post.output);

    assert.deepEqual(result.summary.map(([key]) => key),
        ['page', 'page_size', 'total_rows', 'total_pages', 'filtered_rows', 'unfiltered_rows']);
    assert.equal(result.parts[result.primary].label, 'data');
});

test('real output: group analysis is a row per group', () => {
    const result = plan(fixtures.get_group_analysis_feature_group_analysis_get.output);

    assert.deepEqual(result.summary, [['feature', 'AGE'], ['grouped_by', 'ARM']]);
    const analysis = result.parts[0].plan;
    assert.equal(analysis.kind, 'matrix');
    assert.deepEqual(analysis.rows.map(row => row.label), ['Placebo', 'Xanomeline High Dose', 'Xanomeline Low Dose']);
    assert.deepEqual(analysis.columns, ['count', 'mean', 'median', 'std', 'min', 'max']);
});

// ----- safety -----

test('values are inserted as text: markup in data is never interpreted', () => {
    const payload = '<img src=x onerror="alert(1)">';
    const cases = [
        [{ [payload]: payload }],                               // table: header and cell
        { [payload]: payload },                                 // key/value
        { [payload]: payload, list: [payload] },                // summary strip and chips
        { [payload]: { [payload]: payload }, b: { [payload]: 1 } }, // matrix
        [{ nested: { [payload]: [payload] } }, 1],              // tree
        payload,                                                // pre
    ];

    for (const value of cases) {
        const { out } = renderWithCharts(value); // the fake DOM throws if innerHTML is touched
        assert.ok(out.textContent.includes(payload));
        assert.equal(out.find(el => el.tagName === 'img').length, 0);
    }
});

test('outputAsText is the JSON a model would receive', () => {
    assert.equal(mod.outputAsText('abc'), 'abc');
    assert.equal(mod.outputAsText({ a: 1 }), '{\n  "a": 1\n}');
});
