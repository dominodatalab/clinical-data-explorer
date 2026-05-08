"""Parsing and validation for chart specs returned by the chat agent."""

import json
import logging
import math
import re

logger = logging.getLogger(__name__)

MAX_CHARTS_PER_RESPONSE = 4
MAX_HEATMAP_FEATURES = 30

_CHART_PATTERN = re.compile(r"\[CHART_DATA\](.*?)\[/CHART_DATA\]", re.DOTALL)


class ChartSpecValidationError(ValueError):
    pass


def parse_chart_response(response_text: str) -> tuple[str, list[dict]]:
    """Extract valid chart specs and strip chart blocks from response text."""
    charts = []

    for match in _CHART_PATTERN.finditer(response_text):
        chart_json = match.group(1).strip()

        if len(charts) >= MAX_CHARTS_PER_RESPONSE:
            logger.warning("Skipping chart data: response exceeded %s charts", MAX_CHARTS_PER_RESPONSE)
            continue

        try:
            chart_spec = json.loads(chart_json)
            validate_chart_spec(chart_spec)
            charts.append(chart_spec)
            logger.debug("Successfully parsed chart: %s", chart_spec.get("type", "unknown"))
        except json.JSONDecodeError as e:
            logger.warning("Failed to parse chart data: %s", e)
            logger.warning("Chart JSON that failed: %s", chart_json[:200])
        except ChartSpecValidationError as e:
            logger.warning("Rejected chart data: %s", e)

    clean_text = _CHART_PATTERN.sub("", response_text).strip()
    return clean_text, charts


def validate_chart_spec(chart_spec):
    if not isinstance(chart_spec, dict):
        raise ChartSpecValidationError("chart spec must be an object")

    chart_type = chart_spec.get("type")
    if not isinstance(chart_type, str) or not chart_type:
        raise ChartSpecValidationError("chart spec must include a type")

    data = chart_spec.get("data")
    if not isinstance(data, dict):
        raise ChartSpecValidationError("chart spec data must be an object")

    if chart_type == "heatmap":
        _validate_heatmap_data(data)


def _validate_heatmap_data(data: dict):
    features = data.get("features")
    matrix = data.get("matrix")

    if not isinstance(features, list) or not features:
        raise ChartSpecValidationError("heatmap features must be a non-empty list")

    if not all(isinstance(feature, str) and feature for feature in features):
        raise ChartSpecValidationError("heatmap features must be non-empty strings")

    feature_count = len(features)
    if feature_count > MAX_HEATMAP_FEATURES:
        raise ChartSpecValidationError(
            f"heatmap has {feature_count} features; max is {MAX_HEATMAP_FEATURES}"
        )

    if not isinstance(matrix, list) or len(matrix) != feature_count:
        raise ChartSpecValidationError("heatmap matrix must match feature count")

    for row in matrix:
        if not isinstance(row, list) or len(row) != feature_count:
            raise ChartSpecValidationError("heatmap matrix must be square")

        for value in row:
            if not _is_finite_number(value):
                raise ChartSpecValidationError("heatmap matrix values must be finite numbers")


def _is_finite_number(value) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )
