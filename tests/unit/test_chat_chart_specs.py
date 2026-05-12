import importlib
import json

import backend.services.chat_chart_specs as chat_chart_specs
from backend.services.chat_chart_specs import (
    MAX_CHARTS_PER_RESPONSE,
    MAX_HEATMAP_FEATURES,
    parse_chart_response,
)


def _chart_block(chart_spec):
    return f"[CHART_DATA]\n{json.dumps(chart_spec)}\n[/CHART_DATA]"


def _heatmap_spec(feature_count):
    features = [f"feature_{index}" for index in range(feature_count)]
    matrix = [[1 if row == col else 0 for col in range(feature_count)] for row in range(feature_count)]
    return {
        "type": "heatmap",
        "title": "Correlation",
        "data": {
            "features": features,
            "matrix": matrix,
        },
    }


def test_parse_chart_response_accepts_heatmap_at_feature_limit():
    chart_spec = _heatmap_spec(MAX_HEATMAP_FEATURES)

    text, charts = parse_chart_response(f"Here is the chart.\n{_chart_block(chart_spec)}")

    assert text == "Here is the chart."
    assert charts == [chart_spec]


def test_parse_chart_response_rejects_heatmap_over_feature_limit():
    chart_spec = _heatmap_spec(MAX_HEATMAP_FEATURES + 1)

    text, charts = parse_chart_response(f"Here is the chart.\n{_chart_block(chart_spec)}")

    assert text == "Here is the chart."
    assert charts == []


def test_parse_chart_response_rejects_non_square_heatmap():
    chart_spec = _heatmap_spec(2)
    chart_spec["data"]["matrix"] = [[1, 0]]

    text, charts = parse_chart_response(f"Here is the chart.\n{_chart_block(chart_spec)}")

    assert text == "Here is the chart."
    assert charts == []


def test_parse_chart_response_caps_charts_per_response():
    chart_spec = {
        "type": "bar",
        "title": "Counts",
        "data": {
            "categories": ["A"],
            "values": [1],
        },
    }
    response_text = "Charts\n" + "\n".join(
        _chart_block(chart_spec) for _ in range(MAX_CHARTS_PER_RESPONSE + 1)
    )

    text, charts = parse_chart_response(response_text)

    assert text == "Charts"
    assert charts == [chart_spec] * MAX_CHARTS_PER_RESPONSE


def test_max_charts_per_response_uses_environment_configuration(monkeypatch):
    monkeypatch.setenv("CHAT_MAX_CHARTS_PER_RESPONSE", "2")
    reloaded_module = importlib.reload(chat_chart_specs)

    try:
        chart_spec = {
            "type": "bar",
            "title": "Counts",
            "data": {
                "categories": ["A"],
                "values": [1],
            },
        }
        response_text = "Charts\n" + "\n".join(_chart_block(chart_spec) for _ in range(3))

        text, charts = reloaded_module.parse_chart_response(response_text)

        assert reloaded_module.MAX_CHARTS_PER_RESPONSE == 2
        assert isinstance(reloaded_module.MAX_CHARTS_PER_RESPONSE, int)
        assert text == "Charts"
        assert charts == [chart_spec] * 2
    finally:
        monkeypatch.delenv("CHAT_MAX_CHARTS_PER_RESPONSE", raising=False)
        importlib.reload(chat_chart_specs)
