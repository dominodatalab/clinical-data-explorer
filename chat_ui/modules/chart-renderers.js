// Highcharts type-specific renderers used by the chat tab to embed
// agent-replied charts (modules/chat.js dispatches to these from its
// `renderChart` function).
//
// Each function takes (containerId, title, data) and constructs a
// Highcharts chart in the matching DOM element. The `data` shape is
// the per-type payload the agent's `[CHART_DATA]` block emits — see
// `backend/prompts/chat_system_prompt.md` for the contract.
//
// Eight renderers, all named exports:
//   - renderBarChart, renderScatterChart, renderLineChart, renderPieChart,
//     renderHistogram, renderBoxplot, renderHeatmap, renderGroupedBarChart
//
// These functions intentionally remain separate from the explore-tab's
// own renderers in `modules/explore-charts.js`. Despite the plan's §4
// "consolidate render functions used by both `explore-charts` and
// `chat`" guidance, the two surfaces never actually shared
// implementations — the explore tab fetches pre-aggregated server-side
// chart payloads (`/chart/bar_aggregation`, `/chart/histogram`,
// `/chart/time_series`, `/chart/xy_data`) and renders bespoke
// configurations with click handlers, color themes, and same-column
// optimization paths that the chat tab does not need. Forcing a single
// shared renderer would require either duplicating the chat-side
// simplicity inside the explore-tab call sites (regression risk) or
// pushing explore-tab complexity into chat (behavior change). Keeping
// them separate matches the actual code shape.
//
// No state-, api-, or DOM-helper imports — each renderer is a pure
// Highcharts.chart() invocation against its container.

const MAX_HEATMAP_FEATURES = 30;
const HEATMAP_DATA_LABEL_FEATURE_LIMIT = 12;

export function renderBarChart(containerId, title, data) {
    Highcharts.chart(containerId, {
        chart: { type: 'column' },
        title: { text: title },
        xAxis: {
            categories: data.categories,
            title: { text: data.xAxisTitle || '' }
        },
        yAxis: {
            title: { text: data.yAxisTitle || 'Value' }
        },
        legend: { enabled: false },
        series: [{
            name: data.yAxisTitle || 'Value',
            data: data.values,
            colorByPoint: true
        }],
        credits: { enabled: false }
    });
}

export function renderScatterChart(containerId, title, data) {
    Highcharts.chart(containerId, {
        chart: { type: 'scatter', zoomType: 'xy' },
        title: { text: title },
        xAxis: {
            title: { text: data.xLabel }
        },
        yAxis: {
            title: { text: data.yLabel }
        },
        series: [{
            name: `${data.xLabel} vs ${data.yLabel}`,
            data: data.points
        }],
        credits: { enabled: false }
    });
}

export function renderLineChart(containerId, title, data) {
    Highcharts.chart(containerId, {
        chart: { type: 'line' },
        title: { text: title },
        xAxis: {
            categories: data.categories
        },
        yAxis: {
            title: { text: data.yAxisTitle || 'Value' }
        },
        series: data.series,
        credits: { enabled: false }
    });
}

export function renderPieChart(containerId, title, data) {
    const pieData = data.categories.map((cat, idx) => ({
        name: cat,
        y: data.values[idx]
    }));

    Highcharts.chart(containerId, {
        chart: { type: 'pie' },
        title: { text: title },
        series: [{
            name: 'Value',
            data: pieData
        }],
        plotOptions: {
            pie: {
                allowPointSelect: true,
                cursor: 'pointer',
                dataLabels: {
                    enabled: true,
                    format: '<b>{point.name}</b>: {point.percentage:.1f}%'
                }
            }
        },
        credits: { enabled: false }
    });
}

export function renderHistogram(containerId, title, data) {
    const categories = [];
    for (let i = 0; i < data.bins.length - 1; i++) {
        categories.push(`${data.bins[i].toFixed(1)}-${data.bins[i + 1].toFixed(1)}`);
    }

    Highcharts.chart(containerId, {
        chart: { type: 'column' },
        title: { text: title },
        xAxis: {
            categories: categories,
            title: { text: data.feature }
        },
        yAxis: {
            title: { text: 'Frequency' }
        },
        legend: { enabled: false },
        series: [{
            name: 'Count',
            data: data.counts,
            color: '#7cb5ec'
        }],
        credits: { enabled: false }
    });
}

export function renderBoxplot(containerId, title, data) {
    Highcharts.chart(containerId, {
        chart: { type: 'boxplot' },
        title: { text: title },
        xAxis: {
            categories: [data.feature]
        },
        yAxis: {
            title: { text: 'Value' }
        },
        series: [{
            name: 'Distribution',
            data: [[data.min, data.q1, data.median, data.q3, data.max]]
        }, {
            name: 'Outliers',
            type: 'scatter',
            data: data.outliers.map(val => [0, val]),
            marker: {
                fillColor: 'white',
                lineWidth: 1,
                lineColor: Highcharts.getOptions().colors[0]
            }
        }],
        credits: { enabled: false }
    });
}

export function renderHeatmap(containerId, title, data) {
    const featureCount = validateHeatmapData(data);
    const heatmapData = [];
    for (let i = 0; i < featureCount; i++) {
        for (let j = 0; j < featureCount; j++) {
            heatmapData.push([j, i, data.matrix[i][j]]);
        }
    }

    Highcharts.chart(containerId, {
        chart: { type: 'heatmap' },
        title: { text: title },
        xAxis: {
            categories: data.features,
            opposite: true
        },
        yAxis: {
            categories: data.features,
            title: null,
            reversed: true
        },
        colorAxis: {
            min: -1,
            max: 1,
            stops: [
                [0, '#3060cf'],
                [0.5, '#fffbbc'],
                [1, '#c4463a']
            ]
        },
        legend: {
            align: 'right',
            layout: 'vertical',
            margin: 0,
            verticalAlign: 'top',
            y: 25,
            symbolHeight: 280
        },
        series: [{
            name: 'Correlation',
            borderWidth: 1,
            data: heatmapData,
            dataLabels: {
                enabled: featureCount <= HEATMAP_DATA_LABEL_FEATURE_LIMIT,
                color: '#000000',
                format: '{point.value:.2f}'
            }
        }],
        credits: { enabled: false }
    });
}

function validateHeatmapData(data) {
    if (!data || !Array.isArray(data.features) || data.features.length === 0) {
        throw new Error('Heatmap data must include a non-empty features list.');
    }

    const featureCount = data.features.length;
    if (featureCount > MAX_HEATMAP_FEATURES) {
        throw new Error(`Heatmap has too many features to render safely. Maximum: ${MAX_HEATMAP_FEATURES}.`);
    }

    if (!Array.isArray(data.matrix) || data.matrix.length !== featureCount) {
        throw new Error('Heatmap matrix must match the feature count.');
    }

    for (const row of data.matrix) {
        if (!Array.isArray(row) || row.length !== featureCount) {
            throw new Error('Heatmap matrix must be square.');
        }

        for (const value of row) {
            if (typeof value !== 'number' || !Number.isFinite(value)) {
                throw new Error('Heatmap matrix values must be finite numbers.');
            }
        }
    }

    return featureCount;
}

export function renderGroupedBarChart(containerId, title, data) {
    Highcharts.chart(containerId, {
        chart: { type: 'column' },
        title: { text: title },
        xAxis: {
            categories: data.categories
        },
        yAxis: {
            title: { text: data.yAxisTitle || 'Value' }
        },
        series: data.series,
        credits: { enabled: false }
    });
}
