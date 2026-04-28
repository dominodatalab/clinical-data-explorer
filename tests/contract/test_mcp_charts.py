"""Contract test: bar chart aggregation.

One chart endpoint is enough at this layer — the E2E covers rendering across
the other chart modes. Here we only care that groupby-on-server produces the
expected label/value pairs.
"""


def test_bar_aggregation_groups_by_categorical_column(mcp_client):
    resp = mcp_client.post(
        "/chart/bar_aggregation",
        json={"category_column": "treatment", "aggregation": "count", "limit": 20},
    )

    assert resp.status_code == 200
    body = resp.json()
    labels = {item["label"] for item in body["chart_data"]}
    # Fixture has 5 treatment values; endpoint should surface all of them.
    assert labels == {"Placebo", "DrugA", "DrugB", "DrugC", "Control"}
    total = sum(item["value"] for item in body["chart_data"])
    assert total == 100
