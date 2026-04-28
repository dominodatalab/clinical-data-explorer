// Explore tab — chart UX (bar / histogram / XY / time series / scatter).
//
// Owns:
//   - The category bar chart (left column) and its server-side aggregation.
//   - The main chart area's two plot modes:
//       * Histogram (numeric or categorical column distribution).
//       * XY (X-axis vs Y-axis, dispatching to time-series for date X
//         columns and to xy_data for everything else).
//   - The plot-mode tab toggle (Histogram / XY).
//   - The category-bar click-to-filter behavior — clicking a bar toggles
//     `state.currentFilter`, which is shared with the histogram /
//     main-chart endpoints so they re-fetch under that filter.
//   - The collapsible left-column control panel (Categories list).
//   - Re-populating every dropdown (category, aggregation, histogram
//     column / bin count, X-axis, Y-axis) from `state.columnMetadata`.
//
// Three exports:
//   - `initExploreCharts()` — call once from script.js's DOMContentLoaded
//     callback. Wires the "collapse left column" toggle (mirrors the
//     pre-extraction one-shot wiring at the top of the EXPLORE TAB
//     section). Module-private `leftPanelCollapsed` was previously a
//     `let` at script.js's top scope; it's feature-local to explore and
//     now lives here.
//   - `initializeExploreTab()` — re-populates every explore dropdown
//     from `state.columnMetadata`, restores any previously-selected
//     values, swaps in the per-control change listeners (with the
//     remove-then-add pattern that survives multiple dataset loads),
//     and applies the current plot-mode UI state. Called from script.js
//     when (a) a new dataset finishes loading and (b) the use-labels
//     checkbox toggles (so dropdown text re-renders with the right
//     friendly names).
//   - `resetExploreCharts()` — destroys both Highcharts instances,
//     resets the chart containers to their empty placeholders, clears
//     `state.currentFilter`, and resets the dropdown selections.
//     Called from script.js's dataset-load flow before a new dataset
//     hydrates.
//
// All other helpers — including the per-chart-type render functions,
// the click-to-filter `handleBarClick` flow, and the `effectiveBar
// ChartRequestFilter` / `exploreBarPointColor` / `update
// ExploreBarChartHighlightOnly` trio that powers the same-column
// highlight-only optimization — are module-private. They're closely
// coupled to each other and have no callers outside the explore tab,
// so flattening them into the module's surface area is unnecessary.
//
// `loadDatasetData` and `showExploreMessage` are dead code — neither
// is called from anywhere post-P0 (the only caller of `loadDatasetData`
// disappeared with the dead-code sweep, and `showExploreMessage` was
// only called by `loadDatasetData`). Kept here per ground rule #5;
// REFACTOR_PLAN.md §4's grep-anchor table earmarked `loadDatasetData`
// for `modules/table-view.js`, but functionally it's an explore-tab
// helper that calls `initializeExploreTab`, so colocating with the
// other explore helpers minimizes import churn until a future cleanup
// pass drops both. Same situation for `renderTimeSeriesChart`,
// `renderScatterOrAreaChart`, `computeAggregation`, `createNumericBins`,
// and `createTopCategories` — all five are explicitly marked as legacy
// in their pre-extraction docstrings ("Legacy function - kept for
// reference but no longer used (server-side aggregation now)") and
// stay private to this module.
//
// All chart instance state lives on the shared `state` singleton
// (`state.barChartInstance`, `state.mainChartInstance`,
// `state.currentFilter`, `state.currentPlotMode`, `state.column
// Metadata`) per the P10 migration. Reads + writes go through the
// imported `state` reference so the live binding reaches every other
// module that touches the same fields.

import { state } from '../core/state.js';
import { apiUrl, fetchJson } from '../core/api.js';
import { getDisplayName } from './column-labels.js';

// Module-private state
let leftPanelCollapsed = false;

export function initExploreCharts() {
    // Left panel collapse/expand toggle
    const leftColumnToggle = document.getElementById('left-column-toggle');
    const exploreLeftColumn = document.getElementById('explore-left-column');

    if (leftColumnToggle && exploreLeftColumn) {
        leftColumnToggle.addEventListener('click', () => {
            leftPanelCollapsed = !leftPanelCollapsed;
            exploreLeftColumn.classList.toggle('collapsed', leftPanelCollapsed);
            leftColumnToggle.classList.toggle('collapsed', leftPanelCollapsed);
            leftColumnToggle.title = leftPanelCollapsed ? 'Expand categories panel' : 'Collapse categories panel';

            // Reflow any active charts after the transition
            setTimeout(() => {
                if (state.barChartInstance) state.barChartInstance.reflow();
                if (state.mainChartInstance) state.mainChartInstance.reflow();
            }, 350);
        });
    }
}

export function resetExploreCharts() {
    // Destroy existing chart instances
    if (state.barChartInstance) {
        state.barChartInstance.destroy();
        state.barChartInstance = null;
    }
    if (state.mainChartInstance) {
        state.mainChartInstance.destroy();
        state.mainChartInstance = null;
    }

    // Reset chart containers to empty state
    const barChartEl = document.getElementById('bar-chart');
    const mainChartEl = document.getElementById('main-chart');

    if (barChartEl) {
        barChartEl.innerHTML = '<div class="chart-placeholder">Select a category to view chart</div>';
    }
    if (mainChartEl) {
        mainChartEl.innerHTML = '<div class="chart-placeholder">Select X and Y axes to view chart</div>';
    }

    // Reset the explore filter
    state.currentFilter = null;

    // Reset dropdown selections
    const categorySelect = document.getElementById('category-select');
    const xAxisSelect = document.getElementById('x-axis-select');
    const yAxisSelect = document.getElementById('y-axis-select');

    if (categorySelect) categorySelect.value = '';
    if (xAxisSelect) xAxisSelect.value = '';
    if (yAxisSelect) yAxisSelect.value = '';
}

// Dead — see module docstring. Was the pre-server-side-aggregation entry
// point. Left in place per ground rule #5.
function loadDatasetData() {
    if (!state.currentDataset) {
        console.log('No dataset loaded');
        showExploreMessage('Please load a dataset first to use the Explore tab.');
        return;
    }

    fetch(apiUrl('dataset/data'))
        .then(response => {
            if (!response.ok) {
                return response.json().then(data => {
                    throw new Error(data.error || 'Failed to load dataset data');
                });
            }
            return response.json();
        })
        .then(data => {
            if (data.error) {
                console.error('Error loading dataset data:', data.error);
                showExploreMessage(`Error: ${data.error}`);
            } else {
                initializeExploreTab();
            }
        })
        .catch(error => {
            console.error('Error fetching dataset data:', error);
            showExploreMessage(`Error loading data: ${error.message}. Make sure the MCP server is running and a dataset is loaded.`);
        });
}

// Dead — only called by loadDatasetData (also dead).
function showExploreMessage(message) {
    const barChart = document.getElementById('bar-chart');
    const mainChart = document.getElementById('main-chart');

    const messageHtml = `<div style="display: flex; align-items: center; justify-content: center; height: 100%; color: var(--color-text-textsecondary); font-style: italic; padding: 20px; text-align: center;">${message}</div>`;

    if (barChart) barChart.innerHTML = messageHtml;
    if (mainChart) mainChart.innerHTML = messageHtml;
}

export function initializeExploreTab() {
    // Now uses state.columnMetadata from /dataset/load instead of waiting for full data
    if (!state.columnMetadata) return;

    // Populate category selector (non-numeric columns) with human-readable labels, sorted alphabetically by display name
    const categorySelect = document.getElementById('category-select');
    const savedCategory = categorySelect.value;
    categorySelect.innerHTML = '<option value="">Select a category...</option>';
    [...state.columnMetadata.categorical_columns].sort((a, b) =>
        getDisplayName(a).toLowerCase().localeCompare(getDisplayName(b).toLowerCase())
    ).forEach(col => {
        const option = document.createElement('option');
        option.value = col;
        const displayName = getDisplayName(col);
        option.textContent = displayName !== col ? `${displayName} (${col})` : col;
        categorySelect.appendChild(option);
    });
    if (savedCategory && state.columnMetadata.categorical_columns.includes(savedCategory)) {
        categorySelect.value = savedCategory;
    }

    // Show/hide category empty state based on whether categorical columns exist
    const categoryEmptyState = document.getElementById('category-empty-state');
    const barChartEl = document.getElementById('bar-chart');
    const controlsEl = document.querySelector('#explore-left-column .controls');
    const hasCategorical = state.columnMetadata.categorical_columns && state.columnMetadata.categorical_columns.length > 0;
    if (categoryEmptyState) {
        if (hasCategorical) {
            categoryEmptyState.classList.add('hidden');
            if (barChartEl) barChartEl.style.display = '';
            if (controlsEl) controlsEl.style.display = '';
        } else {
            categoryEmptyState.classList.remove('hidden');
            if (barChartEl) barChartEl.style.display = 'none';
            if (controlsEl) controlsEl.style.display = 'none';
        }
    }

    // Populate aggregation selector based on available numeric columns
    updateAggregationOptions();

    // Populate histogram column selector grouped by Numeric / Categorical
    const histogramColSelect = document.getElementById('histogram-column-select');
    const savedHistCol = histogramColSelect.value;
    histogramColSelect.innerHTML = '<option value="">Select a column...</option>';

    const sortedNumeric = [...state.columnMetadata.numeric_columns].sort((a, b) =>
        getDisplayName(a).toLowerCase().localeCompare(getDisplayName(b).toLowerCase())
    );
    const sortedCategorical = [...state.columnMetadata.categorical_columns].sort((a, b) =>
        getDisplayName(a).toLowerCase().localeCompare(getDisplayName(b).toLowerCase())
    );

    if (sortedNumeric.length > 0) {
        const numGroup = document.createElement('optgroup');
        numGroup.label = 'Numeric';
        sortedNumeric.forEach(col => {
            const displayName = getDisplayName(col);
            const option = document.createElement('option');
            option.value = col;
            option.textContent = displayName !== col ? `${displayName} (${col})` : col;
            numGroup.appendChild(option);
        });
        histogramColSelect.appendChild(numGroup);
    }

    if (sortedCategorical.length > 0) {
        const catGroup = document.createElement('optgroup');
        catGroup.label = 'Categorical';
        sortedCategorical.forEach(col => {
            const displayName = getDisplayName(col);
            const option = document.createElement('option');
            option.value = col;
            option.textContent = displayName !== col ? `${displayName} (${col})` : col;
            catGroup.appendChild(option);
        });
        histogramColSelect.appendChild(catGroup);
    }

    if (savedHistCol && state.columnMetadata.columns.includes(savedHistCol)) {
        histogramColSelect.value = savedHistCol;
    }

    // Populate X and Y axis selectors with human-readable labels
    const xAxisSelect = document.getElementById('x-axis-select');
    const yAxisSelect = document.getElementById('y-axis-select');
    const savedX = xAxisSelect.value;
    const savedY = yAxisSelect.value;

    xAxisSelect.innerHTML = '<option value="">Select X-axis...</option>';
    yAxisSelect.innerHTML = '<option value="">Select Y-axis...</option>';

    // Add all columns to X-axis selector, sorted alphabetically by display name
    [...state.columnMetadata.columns].sort((a, b) =>
        getDisplayName(a).toLowerCase().localeCompare(getDisplayName(b).toLowerCase())
    ).forEach(col => {
        const displayName = getDisplayName(col);
        const optText = displayName !== col ? `${displayName} (${col})` : col;

        const xOption = document.createElement('option');
        xOption.value = col;
        xOption.textContent = optText;
        xAxisSelect.appendChild(xOption);
    });

    // Add numeric columns to Y-axis selector, sorted alphabetically by display name
    [...state.columnMetadata.numeric_columns].sort((a, b) =>
        getDisplayName(a).toLowerCase().localeCompare(getDisplayName(b).toLowerCase())
    ).forEach(col => {
        const displayName = getDisplayName(col);
        const optText = displayName !== col ? `${displayName} (${col})` : col;

        const yOption = document.createElement('option');
        yOption.value = col;
        yOption.textContent = optText;
        yAxisSelect.appendChild(yOption);
    });

    // Restore saved values
    if (savedX && state.columnMetadata.columns.includes(savedX)) xAxisSelect.value = savedX;
    if (savedY && state.columnMetadata.numeric_columns.includes(savedY)) yAxisSelect.value = savedY;

    // Add event listeners (remove first to avoid duplicates)
    categorySelect.removeEventListener('change', updateBarChart);
    categorySelect.addEventListener('change', updateBarChart);
    document.getElementById('aggregation-select').removeEventListener('change', updateBarChart);
    document.getElementById('aggregation-select').addEventListener('change', updateBarChart);
    xAxisSelect.removeEventListener('change', updateMainChart);
    xAxisSelect.addEventListener('change', updateMainChart);
    yAxisSelect.removeEventListener('change', updateMainChart);
    yAxisSelect.addEventListener('change', updateMainChart);
    document.getElementById('chart-aggregation-select').removeEventListener('change', updateMainChart);
    document.getElementById('chart-aggregation-select').addEventListener('change', updateMainChart);

    // Histogram event listeners
    histogramColSelect.removeEventListener('change', updateHistogramChart);
    histogramColSelect.addEventListener('change', updateHistogramChart);
    const histogramBinsSelect = document.getElementById('histogram-bins-select');
    histogramBinsSelect.removeEventListener('change', updateHistogramChart);
    histogramBinsSelect.addEventListener('change', updateHistogramChart);

    // Plot mode toggle listeners
    initPlotModeToggle();

    // Apply the current plot mode UI state
    applyPlotMode(state.currentPlotMode);
}

function initPlotModeToggle() {
    const histogramTab = document.getElementById('histogram-mode-tab');
    const xyTab = document.getElementById('xy-mode-tab');

    // Remove old listeners by cloning
    const newHistTab = histogramTab.cloneNode(true);
    const newXyTab = xyTab.cloneNode(true);
    histogramTab.parentNode.replaceChild(newHistTab, histogramTab);
    xyTab.parentNode.replaceChild(newXyTab, xyTab);

    newHistTab.addEventListener('click', () => switchPlotMode('histogram'));
    newXyTab.addEventListener('click', () => switchPlotMode('xy'));
}

function switchPlotMode(mode) {
    if (mode === state.currentPlotMode) return;
    state.currentPlotMode = mode;
    applyPlotMode(mode);
}

function applyPlotMode(mode) {
    const histogramTab = document.getElementById('histogram-mode-tab');
    const xyTab = document.getElementById('xy-mode-tab');
    const histogramControls = document.getElementById('histogram-controls');
    const xyControls = document.getElementById('xy-controls');

    if (mode === 'histogram') {
        histogramTab.classList.add('active');
        xyTab.classList.remove('active');
        histogramControls.classList.remove('hidden');
        xyControls.classList.add('hidden');
        // Trigger histogram chart if a column is selected
        updateHistogramChart();
    } else {
        histogramTab.classList.remove('active');
        xyTab.classList.add('active');
        histogramControls.classList.add('hidden');
        xyControls.classList.remove('hidden');
        // Trigger XY chart if axes are selected
        updateMainChart();
    }
}

function updateAggregationOptions() {
    if (!state.columnMetadata) return;

    const aggregationSelect = document.getElementById('aggregation-select');
    const savedValue = aggregationSelect.value;
    aggregationSelect.innerHTML = '<option value="count">Count</option>';

    // Add numeric columns as aggregation options with human-readable labels
    state.columnMetadata.numeric_columns.forEach(col => {
        const displayName = getDisplayName(col);
        ['mean', 'sum', 'min', 'max'].forEach(agg => {
            const option = document.createElement('option');
            option.value = `${agg}:${col}`;
            option.textContent = `${agg.charAt(0).toUpperCase() + agg.slice(1)} of ${displayName}`;
            aggregationSelect.appendChild(option);
        });
    });

    // Restore saved value if it still exists
    if (savedValue) {
        const optExists = Array.from(aggregationSelect.options).some(opt => opt.value === savedValue);
        if (optExists) aggregationSelect.value = savedValue;
    }
}

/** Filter shape sent to bar_aggregation when it would change bar counts (not same-column drill). */
function effectiveBarChartRequestFilter(filter, categoryColumn) {
    if (!filter || filter.column === categoryColumn) return null;
    return { column: filter.column, value: String(filter.value) };
}

function exploreBarPointColor(categoryColumn, categories, idx) {
    const activeColor = '#543FDE';
    const defaultColor = '#7cb5ec';
    const dimmedColor = '#c8ddf0';
    const isFilterOnThisCategory = state.currentFilter && state.currentFilter.column === categoryColumn;
    if (!isFilterOnThisCategory) return defaultColor;
    return String(categories[idx]) === String(state.currentFilter.value) ? activeColor : dimmedColor;
}

/** Same-column bar selection only changes highlight, not series data — update in place, no fetch. */
function updateExploreBarChartHighlightOnly(categoryColumn) {
    if (!state.barChartInstance || !state.barChartInstance.series || !state.barChartInstance.series[0]) {
        updateBarChart();
        return;
    }
    const series = state.barChartInstance.series[0];
    const cats = state.barChartInstance.xAxis[0].categories.map(c => String(c));
    series.points.forEach((point, idx) => {
        const color = exploreBarPointColor(categoryColumn, cats, idx);
        point.update({ color }, false, false);
    });
    state.barChartInstance.redraw(false);
}

function updateBarChart() {
    const category = document.getElementById('category-select').value;
    const aggregation = document.getElementById('aggregation-select').value;

    if (!category || !state.columnMetadata) return;

    // Show loading state
    const barChartEl = document.getElementById('bar-chart');
    barChartEl.innerHTML = '<div class="chart-loading">Loading chart...</div>';

    // Build request for server-side aggregation
    const requestBody = {
        category_column: category,
        aggregation: aggregation,
        limit: 20
    };

    // Only send the filter when it's on a *different* column than the
    // bar chart's own category. When the filter matches the category we
    // keep all bars and just highlight the selected one visually.
    const eff = effectiveBarChartRequestFilter(state.currentFilter, category);
    if (eff) {
        requestBody.filter = eff;
    }

    fetchJson(apiUrl('chart/bar_aggregation'), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(requestBody)
    })
    .then(data => {
        if (data.error) {
            console.error('Bar chart error:', data.error);
            barChartEl.innerHTML = `<div class="chart-error">Error: ${data.error}</div>`;
            return;
        }

        // Convert server response to chart format
        // Server returns: { chart_data: [{label, value}, ...] }
        const chartData = data.chart_data.map(d => [d.label, d.value]);
        renderExploreBarChart(chartData, category, aggregation);
    })
    .catch(error => {
        console.error('Error fetching bar chart data:', error);
        barChartEl.innerHTML = `<div class="chart-error">Error loading chart</div>`;
    });
}

function renderExploreBarChart(data, category, aggregation) {
    const categories = data.map(d => String(d[0]));
    const values = data.map(d => d[1]);

    // Create title based on aggregation with human-readable labels
    const categoryDisplay = getDisplayName(category);
    let title = `${categoryDisplay}`;
    if (aggregation !== 'count') {
        const [aggType, aggColumn] = aggregation.split(':');
        const aggColDisplay = getDisplayName(aggColumn);
        title += ` (${aggType} of ${aggColDisplay})`;
    } else {
        title += ' (Count)';
    }

    const isFilterOnThisCategory = state.currentFilter && state.currentFilter.column === category;

    if (state.currentFilter && !isFilterOnThisCategory) {
        const filterColDisplay = getDisplayName(state.currentFilter.column);
        title += ` [Filtered by ${filterColDisplay}=${state.currentFilter.value}]`;
    }

    // Destroy existing chart
    if (state.barChartInstance) {
        state.barChartInstance.destroy();
    }

    // Get y-axis label based on aggregation
    let yAxisLabel = 'Count';
    let seriesName = 'Count';
    if (aggregation !== 'count') {
        const [aggType, aggColumn] = aggregation.split(':');
        const aggColDisplay = getDisplayName(aggColumn);
        yAxisLabel = `${aggType.charAt(0).toUpperCase() + aggType.slice(1)} of ${aggColDisplay}`;
        seriesName = yAxisLabel;
    }

    state.barChartInstance = Highcharts.chart('bar-chart', {
        chart: {
            type: 'bar',
            animation: false,
            events: {
                load: function() {
                    const series = this.series[0];
                    series.points.forEach((point, index) => {
                        point.update({
                            events: {
                                click: function() {
                                    handleBarClick(categories[index]);
                                }
                            },
                            cursor: 'pointer'
                        }, false);
                    });
                    this.redraw(false);
                }
            }
        },
        title: {
            text: title,
            style: { fontSize: '14px' }
        },
        xAxis: {
            categories: categories,
            title: { text: null }
        },
        yAxis: {
            title: { text: yAxisLabel }
        },
        legend: { enabled: false },
        plotOptions: {
            bar: {
                cursor: 'pointer',
                dataLabels: {
                    enabled: true,
                    format: '{point.y:.1f}'
                }
            },
            series: { animation: false }
        },
        series: [{
            name: seriesName,
            animation: false,
            data: values.map((val, idx) => ({
                y: val,
                color: exploreBarPointColor(category, categories, idx)
            })),
            colorByPoint: false
        }],
        credits: { enabled: false }
    });
}

function handleBarClick(categoryValue) {
    const category = document.getElementById('category-select').value;
    const barReqBefore = JSON.stringify(effectiveBarChartRequestFilter(state.currentFilter, category));

    // Toggle filter
    if (state.currentFilter && state.currentFilter.column === category && state.currentFilter.value == categoryValue) {
        // Remove filter
        state.currentFilter = null;
    } else {
        // Apply filter
        state.currentFilter = {
            column: category,
            value: categoryValue
        };
    }

    const barReqAfter = JSON.stringify(effectiveBarChartRequestFilter(state.currentFilter, category));
    if (barReqBefore === barReqAfter) {
        updateExploreBarChartHighlightOnly(category);
    } else {
        updateBarChart();
    }

    if (state.currentPlotMode === 'histogram') {
        updateHistogramChart();
    } else {
        updateMainChart();
    }
}

function updateHistogramChart() {
    const column = document.getElementById('histogram-column-select').value;
    const bins = parseInt(document.getElementById('histogram-bins-select').value, 10);

    if (!column || !state.columnMetadata) return;

    // Show loading state
    const mainChartEl = document.getElementById('main-chart');
    mainChartEl.innerHTML = '<div class="chart-loading">Loading chart...</div>';

    const requestBody = {
        column: column,
        bins: bins
    };

    // Add filter if active
    if (state.currentFilter) {
        requestBody.filter = {
            column: state.currentFilter.column,
            value: String(state.currentFilter.value)
        };
    }

    fetchJson(apiUrl('chart/histogram'), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(requestBody)
    })
    .then(data => {
        if (data.error) {
            console.error('Histogram error:', data.error);
            mainChartEl.innerHTML = `<div class="chart-error">Error: ${data.error}</div>`;
            return;
        }
        renderHistogramChart(data);
    })
    .catch(error => {
        console.error('Error fetching histogram data:', error);
        mainChartEl.innerHTML = `<div class="chart-error">Error loading chart</div>`;
    });
}

function renderHistogramChart(data) {
    const colDisplay = getDisplayName(data.column);
    let title = `Distribution of ${colDisplay}`;
    if (state.currentFilter) {
        const filterColDisplay = getDisplayName(state.currentFilter.column);
        title += ` [Filtered by ${filterColDisplay}=${state.currentFilter.value}]`;
    }

    if (state.mainChartInstance) {
        state.mainChartInstance.destroy();
    }

    if (data.is_numeric) {
        // Numeric histogram — proper contiguous bins with no gaps
        const chartData = data.chart_data.map(d => ({
            x: d.bin_start,
            x2: d.bin_end,
            y: d.count
        }));

        // Calculate point range from first bin width
        const binWidth = data.chart_data.length > 0
            ? data.chart_data[0].bin_end - data.chart_data[0].bin_start
            : 1;

        let subtitle = `n=${data.total_count}`;
        if (data.stats) {
            const s = data.stats;
            subtitle += ` · mean=${s.mean.toFixed(2)} · median=${s.median.toFixed(2)} · std=${s.std.toFixed(2)}`;
        }

        state.mainChartInstance = Highcharts.chart('main-chart', {
            chart: { type: 'column' },
            title: { text: title },
            subtitle: { text: subtitle, style: { fontSize: '11px', color: '#8F8FA3' } },
            xAxis: {
                title: { text: colDisplay },
                // No categories — continuous numeric axis
            },
            yAxis: {
                title: { text: 'Frequency' },
                min: 0
            },
            legend: { enabled: false },
            tooltip: {
                formatter: function() {
                    const d = data.chart_data[this.point.index];
                    return `<b>${colDisplay}</b><br/>` +
                        `Range: ${d.bin_start.toFixed(2)} – ${d.bin_end.toFixed(2)}<br/>` +
                        `Count: <b>${d.count}</b>`;
                }
            },
            plotOptions: {
                column: {
                    pointPadding: 0,
                    groupPadding: 0,
                    borderWidth: 1,
                    borderColor: '#ffffff',
                    pointPlacement: 'between',
                    shadow: false
                },
                series: {
                    pointStart: data.chart_data.length > 0 ? data.chart_data[0].bin_start : 0,
                    pointInterval: binWidth
                }
            },
            series: [{
                name: 'Frequency',
                data: data.chart_data.map(d => d.count),
                color: '#543FDE'
            }],
            credits: { enabled: false }
        });
    } else {
        // Categorical histogram — value counts as bars
        const categories = data.chart_data.map(d => d.label);
        const values = data.chart_data.map(d => d.count);

        let subtitle = `n=${data.total_count} · ${data.unique_count} unique values`;

        state.mainChartInstance = Highcharts.chart('main-chart', {
            chart: { type: 'bar' },
            title: { text: title },
            subtitle: { text: subtitle, style: { fontSize: '11px', color: '#8F8FA3' } },
            xAxis: {
                categories: categories,
                title: { text: null }
            },
            yAxis: {
                title: { text: 'Count' },
                min: 0
            },
            legend: { enabled: false },
            plotOptions: {
                bar: {
                    dataLabels: {
                        enabled: true,
                        format: '{point.y}'
                    }
                }
            },
            series: [{
                name: 'Count',
                data: values,
                color: '#543FDE'
            }],
            credits: { enabled: false }
        });
    }
}

function updateMainChart() {
    const xAxis = document.getElementById('x-axis-select').value;
    const yAxis = document.getElementById('y-axis-select').value;
    const aggregationType = document.getElementById('chart-aggregation-select').value;

    if (!xAxis || !yAxis || !state.columnMetadata) return;

    // Show loading state
    const mainChartEl = document.getElementById('main-chart');
    mainChartEl.innerHTML = '<div class="chart-loading">Loading chart...</div>';

    // Check if x-axis is a date column
    const isTimeSeries = state.columnMetadata.date_columns.includes(xAxis);

    if (isTimeSeries) {
        // Use time series endpoint
        fetchTimeSeriesChart(xAxis, yAxis, aggregationType);
    } else {
        // Use XY data endpoint
        fetchXYChart(xAxis, yAxis, aggregationType);
    }
}

function fetchTimeSeriesChart(xAxis, yAxis, aggregationType) {
    const requestBody = {
        date_column: xAxis,
        value_column: yAxis,
        aggregation: aggregationType,
        num_buckets: 50
    };

    if (state.currentFilter) {
        requestBody.filter = {
            column: state.currentFilter.column,
            value: String(state.currentFilter.value)
        };
    }

    fetchJson(apiUrl('chart/time_series'), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(requestBody)
    })
    .then(data => {
        if (data.error) {
            console.error('Time series chart error:', data.error);
            document.getElementById('main-chart').innerHTML = `<div class="chart-error">Error: ${data.error}</div>`;
            return;
        }

        renderTimeSeriesChartFromServer(data.chart_data, xAxis, yAxis, aggregationType);
    })
    .catch(error => {
        console.error('Error fetching time series data:', error);
        document.getElementById('main-chart').innerHTML = `<div class="chart-error">Error loading chart</div>`;
    });
}

function fetchXYChart(xAxis, yAxis, aggregationType) {
    const requestBody = {
        x_column: xAxis,
        y_column: yAxis,
        aggregation: aggregationType,
        max_points: 1000,
        num_buckets: 50
    };

    if (state.currentFilter) {
        requestBody.filter = {
            column: state.currentFilter.column,
            value: String(state.currentFilter.value)
        };
    }

    fetchJson(apiUrl('chart/xy_data'), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(requestBody)
    })
    .then(data => {
        if (data.error) {
            console.error('XY chart error:', data.error);
            document.getElementById('main-chart').innerHTML = `<div class="chart-error">Error: ${data.error}</div>`;
            return;
        }

        renderXYChartFromServer(data, xAxis, yAxis, aggregationType);
    })
    .catch(error => {
        console.error('Error fetching XY chart data:', error);
        document.getElementById('main-chart').innerHTML = `<div class="chart-error">Error loading chart</div>`;
    });
}

function renderTimeSeriesChartFromServer(chartData, xAxis, yAxis, aggregationType) {
    // Convert server data to Highcharts format
    const aggregatedData = chartData.map(d => [Date.parse(d.x), d.y]).filter(d => !isNaN(d[1]));

    const xAxisDisplay = getDisplayName(xAxis);
    const yAxisDisplay = getDisplayName(yAxis);
    let title = `${yAxisDisplay} over ${xAxisDisplay} (${aggregationType})`;
    if (state.currentFilter) {
        const filterColDisplay = getDisplayName(state.currentFilter.column);
        title += ` [Filtered by ${filterColDisplay}=${state.currentFilter.value}]`;
    }

    if (state.mainChartInstance) {
        state.mainChartInstance.destroy();
    }

    state.mainChartInstance = Highcharts.chart('main-chart', {
        chart: { type: 'area' },
        title: { text: title },
        xAxis: {
            type: 'datetime',
            title: { text: xAxisDisplay }
        },
        yAxis: {
            title: { text: yAxisDisplay }
        },
        series: [{
            name: yAxisDisplay,
            data: aggregatedData,
            fillOpacity: 0.3
        }],
        credits: { enabled: false }
    });
}

function renderXYChartFromServer(data, xAxis, yAxis, aggregationType) {
    const xAxisDisplay = getDisplayName(xAxis);
    const yAxisDisplay = getDisplayName(yAxis);
    let title = `${yAxisDisplay} vs ${xAxisDisplay}`;
    if (aggregationType !== 'none') {
        title += ` (${aggregationType})`;
    }
    if (state.currentFilter) {
        const filterColDisplay = getDisplayName(state.currentFilter.column);
        title += ` [Filtered by ${filterColDisplay}=${state.currentFilter.value}]`;
    }

    let subtitle = '';
    if (data.sampled) {
        subtitle = `(Showing ${data.chart_data.length} of ${data.total_points} points)`;
    }

    if (state.mainChartInstance) {
        state.mainChartInstance.destroy();
    }

    const isNumericX = state.columnMetadata.numeric_columns.includes(xAxis);
    const chartType = data.chart_type === 'scatter' ? 'scatter' : (isNumericX ? 'area' : 'column');

    let chartConfig;

    if (chartType === 'scatter') {
        // Scatter plot
        const scatterData = data.chart_data.map(d => [d.x, d.y]);
        chartConfig = {
            chart: { type: 'scatter', zoomType: 'xy' },
            title: { text: title },
            subtitle: { text: subtitle },
            xAxis: {
                title: { text: xAxisDisplay }
            },
            yAxis: {
                title: { text: yAxisDisplay }
            },
            plotOptions: {
                scatter: {
                    marker: { radius: 3, opacity: 0.5 }
                }
            },
            series: [{
                name: yAxisDisplay,
                data: scatterData
            }],
            credits: { enabled: false }
        };
    } else if (chartType === 'area') {
        // Numeric X - area chart
        const areaData = data.chart_data.map(d => [d.x, d.y]).sort((a, b) => a[0] - b[0]);
        chartConfig = {
            chart: { type: 'area' },
            title: { text: title },
            subtitle: { text: subtitle },
            xAxis: {
                title: { text: xAxisDisplay }
            },
            yAxis: {
                title: { text: yAxisDisplay }
            },
            series: [{
                name: yAxisDisplay,
                data: areaData,
                fillOpacity: 0.3
            }],
            credits: { enabled: false }
        };
    } else {
        // Categorical X - column chart
        const categories = data.chart_data.map(d => String(d.x));
        const values = data.chart_data.map(d => d.y);
        chartConfig = {
            chart: { type: 'column' },
            title: { text: title },
            subtitle: { text: subtitle },
            xAxis: {
                categories: categories,
                title: { text: xAxisDisplay }
            },
            yAxis: {
                title: { text: yAxisDisplay }
            },
            series: [{
                name: yAxisDisplay,
                data: values
            }],
            credits: { enabled: false }
        };
    }

    state.mainChartInstance = Highcharts.chart('main-chart', chartConfig);
}

// Legacy — see module docstring. Kept here per ground rule #5.
function renderTimeSeriesChart(data, xAxis, yAxis, aggregationType) {
    // Group by date and aggregate
    const grouped = {};

    data.forEach(row => {
        const dateVal = row[xAxis];
        const numVal = parseFloat(row[yAxis]);

        if (dateVal && !isNaN(numVal)) {
            if (!grouped[dateVal]) {
                grouped[dateVal] = [];
            }
            grouped[dateVal].push(numVal);
        }
    });

    // Sort by date and aggregate
    const sortedDates = Object.keys(grouped).sort();
    const aggregatedData = sortedDates.map(date => {
        const values = grouped[date];
        let aggregatedValue;

        if (aggregationType === 'mean') {
            aggregatedValue = values.reduce((a, b) => a + b, 0) / values.length;
        } else if (aggregationType === 'sum') {
            aggregatedValue = values.reduce((a, b) => a + b, 0);
        } else if (aggregationType === 'count') {
            aggregatedValue = values.length;
        } else if (aggregationType === 'min') {
            aggregatedValue = Math.min(...values);
        } else if (aggregationType === 'max') {
            aggregatedValue = Math.max(...values);
        }

        return [Date.parse(date), aggregatedValue];
    }).filter(d => !isNaN(d[1]));

    const xAxisDisplay = getDisplayName(xAxis);
    const yAxisDisplay = getDisplayName(yAxis);
    let title = `${yAxisDisplay} over ${xAxisDisplay} (${aggregationType})`;
    if (state.currentFilter) {
        const filterColDisplay = getDisplayName(state.currentFilter.column);
        title += ` [Filtered by ${filterColDisplay}=${state.currentFilter.value}]`;
    }

    if (state.mainChartInstance) {
        state.mainChartInstance.destroy();
    }

    state.mainChartInstance = Highcharts.chart('main-chart', {
        chart: { type: 'area' },
        title: { text: title },
        xAxis: {
            type: 'datetime',
            title: { text: xAxisDisplay }
        },
        yAxis: {
            title: { text: yAxisDisplay }
        },
        series: [{
            name: yAxisDisplay,
            data: aggregatedData,
            fillOpacity: 0.3
        }],
        credits: { enabled: false }
    });
}

// Legacy function - kept for reference but no longer used (server-side aggregation now)
function renderScatterOrAreaChart(data, xAxis, yAxis, aggregationType) {
    const isNumericX = state.columnMetadata ? state.columnMetadata.numeric_columns.includes(xAxis) : false;

    // First, collect all x-axis values to determine if bucketing is needed
    const xValues = [];
    const dataPoints = [];

    data.forEach(row => {
        const xVal = row[xAxis];
        const yVal = parseFloat(row[yAxis]);

        if (xVal !== null && xVal !== undefined && !isNaN(yVal)) {
            if (isNumericX) {
                const xNum = parseFloat(xVal);
                if (!isNaN(xNum)) {
                    xValues.push(xNum);
                    dataPoints.push({ x: xNum, y: yVal });
                }
            } else {
                xValues.push(xVal);
                dataPoints.push({ x: xVal, y: yVal });
            }
        }
    });

    const uniqueXCount = new Set(xValues).size;
    const maxBuckets = 30; // Maximum number of buckets/categories to display

    let aggregatedData;
    let categories = null;
    let chartType;
    let subtitle = '';

    if (isNumericX && uniqueXCount > maxBuckets) {
        // Numeric x-axis with too many values - create bins
        const result = createNumericBins(dataPoints, maxBuckets, aggregationType);
        aggregatedData = result.data;
        categories = result.categories;
        chartType = 'column';
        subtitle = `(Grouped into ${result.categories.length} bins)`;
    } else if (!isNumericX && uniqueXCount > maxBuckets) {
        // Categorical x-axis with too many values - show top N
        const result = createTopCategories(dataPoints, maxBuckets, aggregationType);
        aggregatedData = result.data;
        categories = result.categories;
        chartType = 'column';
        subtitle = `(Showing top ${maxBuckets} categories)`;
    } else {
        // Normal grouping - not too many values
        const grouped = {};

        dataPoints.forEach(point => {
            const xKey = String(point.x);
            if (!grouped[xKey]) {
                grouped[xKey] = [];
            }
            grouped[xKey].push(point.y);
        });

        aggregatedData = Object.entries(grouped).map(([x, values]) => {
            const aggregatedValue = computeAggregation(values, aggregationType);
            const xNum = parseFloat(x);
            return [isNaN(xNum) ? x : xNum, aggregatedValue];
        });

        // Sort by x-axis if numeric
        if (isNumericX) {
            aggregatedData.sort((a, b) => a[0] - b[0]);
            chartType = 'area';
        } else {
            chartType = 'column';
            categories = aggregatedData.map(d => String(d[0]));
        }
    }

    const xAxisDisplay = getDisplayName(xAxis);
    const yAxisDisplay = getDisplayName(yAxis);
    let title = `${yAxisDisplay} by ${xAxisDisplay} (${aggregationType})`;
    if (subtitle) {
        title += ` ${subtitle}`;
    }
    if (state.currentFilter) {
        const filterColDisplay = getDisplayName(state.currentFilter.column);
        title += ` [Filtered by ${filterColDisplay}=${state.currentFilter.value}]`;
    }

    if (state.mainChartInstance) {
        state.mainChartInstance.destroy();
    }

    const chartConfig = {
        chart: { type: chartType },
        title: { text: title },
        xAxis: categories ? {
            categories: categories,
            title: { text: xAxisDisplay },
            labels: {
                rotation: categories.length > 15 ? -45 : 0,
                style: {
                    fontSize: categories.length > 20 ? '10px' : '11px'
                }
            }
        } : {
            title: { text: xAxisDisplay }
        },
        yAxis: {
            title: { text: yAxisDisplay }
        },
        series: [{
            name: yAxisDisplay,
            data: categories ? aggregatedData : aggregatedData,
            fillOpacity: chartType === 'area' ? 0.3 : undefined
        }],
        credits: { enabled: false },
        plotOptions: {
            column: {
                pointPadding: 0.1,
                groupPadding: 0.05
            }
        }
    };

    state.mainChartInstance = Highcharts.chart('main-chart', chartConfig);
}

function computeAggregation(values, aggregationType) {
    if (aggregationType === 'mean') {
        return values.reduce((a, b) => a + b, 0) / values.length;
    } else if (aggregationType === 'sum') {
        return values.reduce((a, b) => a + b, 0);
    } else if (aggregationType === 'count') {
        return values.length;
    } else if (aggregationType === 'min') {
        return Math.min(...values);
    } else if (aggregationType === 'max') {
        return Math.max(...values);
    }
    return 0;
}

function createNumericBins(dataPoints, maxBins, aggregationType) {
    const xValues = dataPoints.map(p => p.x);
    const min = Math.min(...xValues);
    const max = Math.max(...xValues);
    const range = max - min;

    // Determine nice bin size
    const rawBinSize = range / maxBins;
    const magnitude = Math.pow(10, Math.floor(Math.log10(rawBinSize)));
    const normalizedSize = rawBinSize / magnitude;

    let niceBinSize;
    if (normalizedSize <= 1) niceBinSize = 1 * magnitude;
    else if (normalizedSize <= 2) niceBinSize = 2 * magnitude;
    else if (normalizedSize <= 5) niceBinSize = 5 * magnitude;
    else niceBinSize = 10 * magnitude;

    const binStart = Math.floor(min / niceBinSize) * niceBinSize;
    const binEnd = Math.ceil(max / niceBinSize) * niceBinSize;
    const numBins = Math.ceil((binEnd - binStart) / niceBinSize);

    // Create bins
    const bins = [];
    for (let i = 0; i < numBins; i++) {
        const binMin = binStart + i * niceBinSize;
        const binMax = binMin + niceBinSize;
        bins.push({
            min: binMin,
            max: binMax,
            label: `${binMin.toFixed(1)}-${binMax.toFixed(1)}`,
            values: []
        });
    }

    // Assign data points to bins
    dataPoints.forEach(point => {
        const binIndex = Math.min(
            Math.floor((point.x - binStart) / niceBinSize),
            numBins - 1
        );
        if (binIndex >= 0 && binIndex < bins.length) {
            bins[binIndex].values.push(point.y);
        }
    });

    // Compute aggregations for each bin
    const data = [];
    const categories = [];

    bins.forEach(bin => {
        if (bin.values.length > 0) {
            categories.push(bin.label);
            data.push(computeAggregation(bin.values, aggregationType));
        }
    });

    return { data, categories };
}

function createTopCategories(dataPoints, topN, aggregationType) {
    // Group by category
    const grouped = {};

    dataPoints.forEach(point => {
        const category = String(point.x);
        if (!grouped[category]) {
            grouped[category] = [];
        }
        grouped[category].push(point.y);
    });

    // Compute aggregations and sort by value
    const aggregated = Object.entries(grouped).map(([category, values]) => ({
        category: category,
        value: computeAggregation(values, aggregationType),
        count: values.length
    }));

    // Sort by value (descending) and take top N
    aggregated.sort((a, b) => b.value - a.value);
    const topCategories = aggregated.slice(0, topN);

    const data = topCategories.map(item => item.value);
    const categories = topCategories.map(item => item.category);

    return { data, categories };
}
