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

const HUMAN_DATE_FORMATTER = new Intl.DateTimeFormat('en-US', {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
    timeZone: 'UTC',
});

const HUMAN_DATETIME_FORMATTER = new Intl.DateTimeFormat('en-US', {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
    timeZone: 'UTC',
});

const MONTH_NAME_PATTERN = /\b(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:tember)?|sept|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\b/i;

function parseDate9(value) {
    const match = String(value).trim().match(/^(\d{1,2})([A-Za-z]{3})(\d{2}|\d{4})$/);
    if (!match) return null;

    const monthIndex = {
        jan: 0,
        feb: 1,
        mar: 2,
        apr: 3,
        may: 4,
        jun: 5,
        jul: 6,
        aug: 7,
        sep: 8,
        oct: 9,
        nov: 10,
        dec: 11,
    }[match[2].toLowerCase()];
    if (monthIndex === undefined) return null;

    const day = Number(match[1]);
    const rawYear = Number(match[3]);
    const year = rawYear < 100 ? rawYear + (rawYear >= 50 ? 1900 : 2000) : rawYear;
    const timestamp = Date.UTC(year, monthIndex, day);
    const parsed = new Date(timestamp);

    if (
        parsed.getUTCFullYear() !== year ||
        parsed.getUTCMonth() !== monthIndex ||
        parsed.getUTCDate() !== day
    ) {
        return null;
    }

    return timestamp;
}

function validatedUtcTimestamp(year, monthIndex, day) {
    const timestamp = Date.UTC(year, monthIndex, day);
    const parsed = new Date(timestamp);

    if (
        parsed.getUTCFullYear() !== year ||
        parsed.getUTCMonth() !== monthIndex ||
        parsed.getUTCDate() !== day
    ) {
        return null;
    }

    return timestamp;
}

function normalizeYear(year) {
    return year < 100 ? year + (year >= 50 ? 1900 : 2000) : year;
}

function parseDateOnly(value) {
    const patterns = [
        {
            regex: /^(\d{4})-(\d{1,2})-(\d{1,2})$/,
            parts: match => [Number(match[1]), Number(match[2]) - 1, Number(match[3])],
        },
        {
            regex: /^(\d{4})\/(\d{1,2})\/(\d{1,2})$/,
            parts: match => [Number(match[1]), Number(match[2]) - 1, Number(match[3])],
        },
        {
            regex: /^(\d{1,2})\/(\d{1,2})\/(\d{2}|\d{4})$/,
            parts: match => [normalizeYear(Number(match[3])), Number(match[1]) - 1, Number(match[2])],
        },
    ];

    for (const pattern of patterns) {
        const match = value.match(pattern.regex);
        if (!match) continue;

        const [year, monthIndex, day] = pattern.parts(match);
        return { matched: true, timestamp: validatedUtcTimestamp(year, monthIndex, day) };
    }

    return { matched: false, timestamp: null };
}

function hasTimeComponent(value) {
    if (value instanceof Date) {
        return (
            value.getUTCHours() !== 0 ||
            value.getUTCMinutes() !== 0 ||
            value.getUTCSeconds() !== 0 ||
            value.getUTCMilliseconds() !== 0
        );
    }

    return /(?:T|\s)\d{1,2}:\d{2}/.test(String(value));
}

function parseDateLikeValue(value) {
    if (value instanceof Date && !Number.isNaN(value.getTime())) {
        return value.getTime();
    }

    if (typeof value !== 'string') return null;

    const trimmed = value.trim();
    if (!trimmed) return null;
    if (MONTH_NAME_PATTERN.test(trimmed)) return null;

    const date9Timestamp = parseDate9(trimmed);
    if (date9Timestamp !== null) return date9Timestamp;

    const dateOnly = parseDateOnly(trimmed);
    if (dateOnly.matched) return dateOnly.timestamp;

    const datePatterns = [
        /^\d{4}-\d{1,2}-\d{1,2}(?:[T\s]\d{1,2}:\d{2}(?::\d{2}(?:\.\d{1,3})?)?(?:Z|[+-]\d{2}:?\d{2})?)?$/,
        /^\d{4}\/\d{1,2}\/\d{1,2}(?:\s+\d{1,2}:\d{2}(?::\d{2})?)?$/,
        /^\d{1,2}\/\d{1,2}\/\d{2,4}(?:\s+\d{1,2}:\d{2}(?::\d{2})?)?$/,
    ];
    if (!datePatterns.some(pattern => pattern.test(trimmed))) return null;

    const timestamp = Date.parse(trimmed);
    return Number.isNaN(timestamp) ? null : timestamp;
}

export function formatDateForDisplay(value) {
    const timestamp = parseDateLikeValue(value);
    if (timestamp === null) return value;

    const formatter = hasTimeComponent(value) ? HUMAN_DATETIME_FORMATTER : HUMAN_DATE_FORMATTER;
    return formatter.format(new Date(timestamp));
}

function normalizeDateLabels(labels) {
    return Array.isArray(labels) ? labels.map(formatDateForDisplay) : labels;
}

function normalizeHistogramBins(bins) {
    const categories = [];
    for (let i = 0; i < bins.length - 1; i++) {
        const start = formatDateForDisplay(bins[i]);
        const end = formatDateForDisplay(bins[i + 1]);
        categories.push(
            typeof start === 'number' && typeof end === 'number'
                ? `${start.toFixed(1)}-${end.toFixed(1)}`
                : `${start} - ${end}`,
        );
    }
    return categories;
}

function normalizeDateXPoint(point) {
    if (Array.isArray(point) && point.length >= 2) {
        const timestamp = parseDateLikeValue(point[0]);
        if (timestamp !== null) {
            return { point: [timestamp, ...point.slice(1)], hasDateX: true };
        }
        return { point, hasDateX: false };
    }

    if (point && typeof point === 'object' && Object.prototype.hasOwnProperty.call(point, 'x')) {
        const timestamp = parseDateLikeValue(point.x);
        if (timestamp !== null) {
            return { point: { ...point, x: timestamp }, hasDateX: true };
        }
    }

    return { point, hasDateX: false };
}

function normalizeDateXPoints(points) {
    let hasDateX = false;
    const normalizedPoints = Array.isArray(points)
        ? points.map(point => {
            const normalized = normalizeDateXPoint(point);
            hasDateX = hasDateX || normalized.hasDateX;
            return normalized.point;
        })
        : points;

    return { points: normalizedPoints, hasDateX };
}

function normalizeDateXSeries(series) {
    let hasDateX = false;
    const normalizedSeries = Array.isArray(series)
        ? series.map(item => {
            const normalized = normalizeDateXPoints(item.data);
            hasDateX = hasDateX || normalized.hasDateX;
            return { ...item, data: normalized.points };
        })
        : series;

    return { series: normalizedSeries, hasDateX };
}

function dateXAxisOptions(baseOptions = {}) {
    return {
        ...baseOptions,
        type: 'datetime',
        labels: {
            ...(baseOptions.labels || {}),
            formatter() {
                return formatDateForDisplay(new Date(this.value));
            },
        },
    };
}

export function renderBarChart(containerId, title, data) {
    Highcharts.chart(containerId, {
        chart: { type: 'column' },
        title: { text: title },
        xAxis: {
            categories: normalizeDateLabels(data.categories),
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
    const normalized = normalizeDateXPoints(data.points);

    Highcharts.chart(containerId, {
        chart: { type: 'scatter', zoomType: 'xy' },
        title: { text: title },
        xAxis: normalized.hasDateX
            ? dateXAxisOptions({ title: { text: data.xLabel } })
            : { title: { text: data.xLabel } },
        yAxis: {
            title: { text: data.yLabel }
        },
        series: [{
            name: `${data.xLabel} vs ${data.yLabel}`,
            data: normalized.points
        }],
        credits: { enabled: false }
    });
}

export function renderLineChart(containerId, title, data) {
    const normalized = normalizeDateXSeries(data.series);
    const xAxis = data.categories
        ? { categories: normalizeDateLabels(data.categories) }
        : (normalized.hasDateX ? dateXAxisOptions() : {});

    Highcharts.chart(containerId, {
        chart: { type: 'line' },
        title: { text: title },
        xAxis,
        yAxis: {
            title: { text: data.yAxisTitle || 'Value' }
        },
        series: normalized.series,
        credits: { enabled: false }
    });
}

export function renderPieChart(containerId, title, data) {
    const pieData = data.categories.map((cat, idx) => ({
        name: formatDateForDisplay(cat),
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
    const categories = normalizeHistogramBins(data.bins);

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
    const heatmapData = [];
    for (let i = 0; i < data.features.length; i++) {
        for (let j = 0; j < data.features.length; j++) {
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
                enabled: true,
                color: '#000000',
                format: '{point.value:.2f}'
            }
        }],
        credits: { enabled: false }
    });
}

export function renderGroupedBarChart(containerId, title, data) {
    Highcharts.chart(containerId, {
        chart: { type: 'column' },
        title: { text: title },
        xAxis: {
            categories: normalizeDateLabels(data.categories)
        },
        yAxis: {
            title: { text: data.yAxisTitle || 'Value' }
        },
        series: data.series,
        credits: { enabled: false }
    });
}
