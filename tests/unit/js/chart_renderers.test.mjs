import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { test } from 'node:test';
import vm from 'node:vm';

const chartRenderersSourcePath = new URL('../../../chat_ui/modules/chart-renderers.js', import.meta.url);
const chartRenderersSource = await readFile(chartRenderersSourcePath, 'utf8');

function plain(value) {
    return JSON.parse(JSON.stringify(value));
}

async function loadChartRenderersModule() {
    const chartCalls = [];
    const context = vm.createContext({
        console: { error() {}, log() {}, warn() {} },
        Date,
        Intl,
        Number,
        Object,
        RegExp,
        String,
        Highcharts: {
            chart(containerId, options) {
                chartCalls.push({ containerId, options });
            },
            getOptions() {
                return { colors: ['#7cb5ec'] };
            },
        },
    });

    const module = new vm.SourceTextModule(chartRenderersSource, {
        context,
        identifier: String(chartRenderersSourcePath),
    });
    await module.link(() => {
        throw new Error('chart-renderers.js should not import other modules');
    });
    await module.evaluate();

    return { namespace: module.namespace, chartCalls };
}

test('formatDateForDisplay returns human-readable labels for common clinical dates', async () => {
    const { namespace } = await loadChartRenderersModule();

    assert.equal(namespace.formatDateForDisplay('2013-02-04'), 'Feb 4, 2013');
    assert.equal(namespace.formatDateForDisplay('04/16/2013'), 'Apr 16, 2013');
    assert.equal(namespace.formatDateForDisplay('18DEC2012'), 'Dec 18, 2012');
    assert.equal(namespace.formatDateForDisplay('Placebo'), 'Placebo');
});

test('bar and grouped bar charts format date-like category labels', async () => {
    const { namespace, chartCalls } = await loadChartRenderersModule();

    namespace.renderBarChart('bar-chart', 'Starts', {
        categories: ['2013-01-19', '2013-02-04'],
        values: [4, 7],
        yAxisTitle: 'Events',
    });
    namespace.renderGroupedBarChart('grouped-chart', 'By Start Date', {
        categories: ['18DEC2012', '2013-01-12'],
        series: [{ name: 'Mild', data: [1, 2] }],
    });

    assert.deepEqual(plain(chartCalls[0].options.xAxis.categories), ['Jan 19, 2013', 'Feb 4, 2013']);
    assert.deepEqual(plain(chartCalls[1].options.xAxis.categories), ['Dec 18, 2012', 'Jan 12, 2013']);
});

test('scatter charts convert date-like x values to a datetime axis', async () => {
    const { namespace, chartCalls } = await loadChartRenderersModule();

    namespace.renderScatterChart('scatter-chart', 'Events Over Time', {
        xLabel: 'Start Date',
        yLabel: 'Events',
        points: [
            ['2013-01-19', 4],
            ['2013-02-04', 7],
        ],
    });

    const options = chartCalls[0].options;
    assert.equal(options.xAxis.type, 'datetime');
    assert.equal(options.xAxis.labels.formatter.call({ value: Date.UTC(2013, 0, 19) }), 'Jan 19, 2013');
    assert.deepEqual(plain(options.series[0].data), [
        [Date.UTC(2013, 0, 19), 4],
        [Date.UTC(2013, 1, 4), 7],
    ]);
});

test('line charts format date categories and preserve numeric series values', async () => {
    const { namespace, chartCalls } = await loadChartRenderersModule();

    namespace.renderLineChart('line-chart', 'Events by Date', {
        categories: ['2013-01-19', '2013-02-04'],
        series: [{ name: 'Events', data: [4, 7] }],
        yAxisTitle: 'Events',
    });

    const options = chartCalls[0].options;
    assert.deepEqual(plain(options.xAxis.categories), ['Jan 19, 2013', 'Feb 4, 2013']);
    assert.deepEqual(plain(options.series), [{ name: 'Events', data: [4, 7] }]);
});

test('histogram bins can be rendered when the bins are date-like strings', async () => {
    const { namespace, chartCalls } = await loadChartRenderersModule();

    namespace.renderHistogram('histogram-chart', 'Start Date Distribution', {
        feature: 'Start Date',
        bins: ['2013-01-01', '2013-02-01', '2013-03-01'],
        counts: [3, 5],
    });

    assert.deepEqual(plain(chartCalls[0].options.xAxis.categories), [
        'Jan 1, 2013 - Feb 1, 2013',
        'Feb 1, 2013 - Mar 1, 2013',
    ]);
});
