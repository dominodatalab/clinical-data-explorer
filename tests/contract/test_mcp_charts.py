"""Contract tests for server-side chart aggregation routes."""
from pathlib import Path

import numpy as np
import pandas as pd
import pytest


SAMPLE_CSV = Path(__file__).resolve().parents[1] / "fixtures" / "sample.csv"
NUMERIC_AGGREGATIONS = ("mean", "sum", "min", "max")


def _sample_df():
    return pd.read_csv(SAMPLE_CSV)


def _chart_values_by_key(chart_data, key_name="label", value_name="value"):
    return {item[key_name]: item[value_name] for item in chart_data}


def _assert_series_matches_chart(series, chart_values):
    assert set(chart_values) == {str(key) for key in series.index}
    for key, value in series.items():
        assert chart_values[str(key)] == pytest.approx(value)


def test_bar_aggregation_groups_by_categorical_column(mcp_client):
    df = _sample_df()
    expected = df["treatment"].value_counts()

    resp = mcp_client.post(
        "/chart/bar_aggregation",
        json={"category_column": "treatment", "aggregation": "count", "limit": 20},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["aggregation"] == "count"
    assert body["total_categories"] == df["treatment"].nunique()
    _assert_series_matches_chart(expected, _chart_values_by_key(body["chart_data"]))


@pytest.mark.parametrize("aggregation", NUMERIC_AGGREGATIONS)
def test_bar_aggregation_applies_numeric_aggregation_by_category(mcp_client, aggregation):
    df = _sample_df()
    expected = getattr(df.groupby("treatment")["age"], aggregation)()

    resp = mcp_client.post(
        "/chart/bar_aggregation",
        json={
            "category_column": "treatment",
            "aggregation": f"{aggregation}:age",
            "limit": 20,
        },
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["aggregation"] == f"{aggregation}:age"
    _assert_series_matches_chart(expected, _chart_values_by_key(body["chart_data"]))


def test_bar_aggregation_defaults_unknown_method_to_mean(mcp_client):
    resp = mcp_client.post(
        "/chart/bar_aggregation",
        json={"category_column": "treatment", "aggregation": "median:age", "limit": 20},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["aggregation"] == "mean:age"
    assert body["chart_data"]


def test_xy_chart_defaults_unknown_aggregation_to_mean(mcp_client):
    resp = mcp_client.post(
        "/chart/xy_data",
        json={"x_column": "treatment", "y_column": "age", "aggregation": "median"},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["aggregation"] == "mean"
    assert body["chart_type"] == "area"
    assert body["chart_data"]


def test_xy_chart_returns_unaggregated_scatter_points(mcp_client):
    df = _sample_df()
    expected = [
        {"x": float(row.subject_id), "y": float(row.age)}
        for row in df.itertuples(index=False)
    ]

    resp = mcp_client.post(
        "/chart/xy_data",
        json={
            "x_column": "subject_id",
            "y_column": "age",
            "aggregation": "none",
            "max_points": len(expected),
        },
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["chart_type"] == "scatter"
    assert body["sampled"] is False
    assert body["total_points"] == len(expected)
    assert body["chart_data"] == expected


@pytest.mark.parametrize("aggregation", NUMERIC_AGGREGATIONS)
def test_xy_chart_applies_numeric_aggregation_by_categorical_x(mcp_client, aggregation):
    df = _sample_df()
    expected = getattr(df.groupby("treatment")["age"], aggregation)()

    resp = mcp_client.post(
        "/chart/xy_data",
        json={"x_column": "treatment", "y_column": "age", "aggregation": aggregation},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["chart_type"] == "area"
    assert body["aggregation"] == aggregation
    _assert_series_matches_chart(
        expected,
        _chart_values_by_key(body["chart_data"], key_name="x", value_name="y"),
    )


def _expected_xy_numeric_buckets(df, aggregation, num_buckets):
    x_values = df["subject_id"]
    y_values = df["age"]
    valid_mask = x_values.notna() & y_values.notna()
    x_values = x_values[valid_mask]
    y_values = y_values[valid_mask]

    buckets = np.linspace(x_values.min(), x_values.max(), num_buckets + 1)
    bucket_labels = [
        (buckets[i] + buckets[i + 1]) / 2
        for i in range(len(buckets) - 1)
    ]
    bucket_indices = np.digitize(x_values, buckets[1:-1])
    agg_result = getattr(
        pd.DataFrame({"bucket": bucket_indices, "y": y_values}).groupby("bucket")["y"],
        aggregation,
    )()

    return [
        {"x": float(bucket_labels[bucket_idx]), "y": float(agg_result[bucket_idx])}
        for bucket_idx in range(len(bucket_labels))
        if bucket_idx in agg_result.index and pd.notna(agg_result[bucket_idx])
    ]


@pytest.mark.parametrize("aggregation", NUMERIC_AGGREGATIONS)
def test_xy_chart_applies_numeric_aggregation_by_numeric_x_bucket(mcp_client, aggregation):
    df = _sample_df()
    expected = _expected_xy_numeric_buckets(df, aggregation, num_buckets=5)

    resp = mcp_client.post(
        "/chart/xy_data",
        json={
            "x_column": "subject_id",
            "y_column": "age",
            "aggregation": aggregation,
            "num_buckets": 5,
        },
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["chart_type"] == "area"
    assert body["aggregation"] == aggregation
    assert len(body["chart_data"]) == len(expected)
    for actual, expected_point in zip(body["chart_data"], expected):
        assert actual["x"] == pytest.approx(expected_point["x"])
        assert actual["y"] == pytest.approx(expected_point["y"])


def test_time_series_defaults_unknown_aggregation_to_mean(mcp_client):
    resp = mcp_client.post(
        "/chart/time_series",
        json={"date_column": "visit_date", "value_column": "age", "aggregation": "median"},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["aggregation"] == "mean"
    assert body["chart_type"] == "time_series"
    assert body["chart_data"]


def _expected_time_series_buckets(df, aggregation, num_buckets):
    dates = pd.to_datetime(df["visit_date"], errors="coerce")
    values = df["age"]
    valid_mask = dates.notna() & values.notna()
    dates = dates[valid_mask]
    values = values[valid_mask]

    time_buckets = pd.cut(dates, bins=num_buckets, labels=False)
    bucket_dates = pd.date_range(dates.min(), dates.max(), periods=num_buckets + 1)
    bucket_centers = [
        bucket_dates[i] + (bucket_dates[i + 1] - bucket_dates[i]) / 2
        for i in range(len(bucket_dates) - 1)
    ]
    agg_result = getattr(
        pd.DataFrame({"bucket": time_buckets, "value": values}).groupby("bucket")["value"],
        aggregation,
    )()

    return [
        {"x": center.isoformat(), "y": float(agg_result[bucket_idx])}
        for bucket_idx, center in enumerate(bucket_centers)
        if bucket_idx in agg_result.index and pd.notna(agg_result[bucket_idx])
    ]


@pytest.mark.parametrize("aggregation", NUMERIC_AGGREGATIONS + ("count",))
def test_time_series_applies_aggregation_by_time_bucket(mcp_client, aggregation):
    df = _sample_df()
    expected = _expected_time_series_buckets(df, aggregation, num_buckets=4)

    resp = mcp_client.post(
        "/chart/time_series",
        json={
            "date_column": "visit_date",
            "value_column": "age",
            "aggregation": aggregation,
            "num_buckets": 4,
        },
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["chart_type"] == "time_series"
    assert body["aggregation"] == aggregation
    assert len(body["chart_data"]) == len(expected)
    for actual, expected_point in zip(body["chart_data"], expected):
        assert actual["x"] == expected_point["x"]
        assert actual["y"] == pytest.approx(expected_point["y"])


def test_numeric_histogram_returns_expected_bins_counts_and_stats(mcp_client):
    df = _sample_df()
    col_data = df["age"].dropna()
    counts, bin_edges = np.histogram(col_data, bins=10)

    resp = mcp_client.post("/chart/histogram", json={"column": "age", "bins": 10})

    assert resp.status_code == 200
    body = resp.json()
    assert body["chart_type"] == "histogram"
    assert body["is_numeric"] is True
    assert body["total_count"] == len(col_data)
    assert [item["count"] for item in body["chart_data"]] == counts.tolist()
    for idx, item in enumerate(body["chart_data"]):
        assert item["bin_start"] == pytest.approx(bin_edges[idx])
        assert item["bin_end"] == pytest.approx(bin_edges[idx + 1])
    assert body["stats"] == {
        "mean": pytest.approx(col_data.mean()),
        "median": pytest.approx(col_data.median()),
        "std": pytest.approx(col_data.std()),
        "min": pytest.approx(col_data.min()),
        "max": pytest.approx(col_data.max()),
    }


def test_categorical_histogram_returns_expected_value_counts(mcp_client):
    df = _sample_df()
    expected = df["treatment"].astype(str).value_counts().head(50)

    resp = mcp_client.post("/chart/histogram", json={"column": "treatment"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["chart_type"] == "categorical_histogram"
    assert body["is_numeric"] is False
    assert body["total_count"] == len(df["treatment"].dropna())
    assert body["unique_count"] == df["treatment"].nunique()
    _assert_series_matches_chart(
        expected,
        _chart_values_by_key(body["chart_data"], value_name="count"),
    )


@pytest.mark.parametrize(
    ("path", "payload"),
    [
        (
            "/chart/bar_aggregation",
            {"category_column": "treatment", "aggregation": "count"},
        ),
        (
            "/chart/xy_data",
            {"x_column": "treatment", "y_column": "age", "aggregation": "mean"},
        ),
        (
            "/chart/time_series",
            {"date_column": "visit_date", "value_column": "age", "aggregation": "mean"},
        ),
        (
            "/chart/histogram",
            {"column": "age"},
        ),
    ],
)
def test_chart_endpoints_reject_unknown_filter_column(mcp_client, path, payload):
    payload["filter"] = {"column": "missing_filter_column", "value": "x"}

    resp = mcp_client.post(path, json=payload)

    assert resp.status_code == 400
    assert resp.json()["detail"] == "Filter column 'missing_filter_column' not found"
