"""Contract tests: expression filter + simple-filter operators.

We pick SAS syntax for the expression happy path since that's the default
syntax tab in the UI. The validation test covers the "unknown column" path,
which is the regression class we care about (bad input -> 4xx with useful
message, not 500).

The parameterized operator test covers the simple-filter UI's translation
into /table/data filter conditions. Each operator is its own pandas codepath
in apply_filters() — the test guarantees that codepath remains wired and
moves the row count in the expected direction. We don't assert exact counts
because that would couple to the fixture's row distribution; "shape, not
bytes" per TESTING_STRATEGY.md.
"""
import pytest


def test_expression_filter_reduces_row_count(mcp_client):
    resp = mcp_client.post(
        "/table/expression_filter",
        json={
            "expression": "treatment = 'Placebo'",
            "syntax": "sas",
            "page": 1,
            "page_size": 100,
        },
    )

    assert resp.status_code == 200
    body = resp.json()
    # Fixture has 20 Placebo rows; assert the filter actually reduced the set.
    assert body["total_rows"] < body["unfiltered_rows"]
    assert body["total_rows"] > 0


_EXPRESSION_SYNTAX_CASES = [
    ("sas", "treatment NOT IN ('Placebo', 'DrugB')", 64),
    ("sas", "age BETWEEN 30 AND 50", 31),
    ("sas", "treatment <> 'Placebo'", 80),
    ("sas", "weight_kg IS NULL", 7),
    ("sas", "weight_kg IS NOT NULL", 93),
    ("sas", "notes LIKE '%headache%'", 23),
    ("sas", "notes NOT LIKE '%headache%'", 77),
    ("r", '!str_detect(notes, "headache")', 77),
]


@pytest.mark.parametrize(
    ("syntax", "expression", "expected_rows"),
    _EXPRESSION_SYNTAX_CASES,
    ids=[
        "sas-not-in",
        "sas-between",
        "sas-not-equal-angle",
        "sas-is-null",
        "sas-is-not-null",
        "sas-like",
        "sas-not-like",
        "r-not-str-detect",
    ],
)
def test_expression_filter_supported_syntax_cases(mcp_client, syntax, expression, expected_rows):
    resp = mcp_client.post(
        "/table/expression_filter",
        json={
            "expression": expression,
            "syntax": syntax,
            "page": 1,
            "page_size": 100,
        },
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["unfiltered_rows"] == 100
    assert body["total_rows"] == expected_rows


def test_expression_filter_unknown_column_returns_validation_error(mcp_client):
    resp = mcp_client.post(
        "/table/expression_filter",
        json={"expression": "nonexistent_col > 10", "syntax": "sas"},
    )

    # Must be a 4xx, not a 500 — the contract is that bad input is reported
    # cleanly so the UI can render the message.
    assert resp.status_code == 400
    assert "nonexistent_col" in resp.json()["detail"].lower() or "unknown" in resp.json()["detail"].lower()


# Each tuple: (filter dict sent to /table/data, human-readable id used in the
# test name). One operator per row. We deliberately cover both string ops
# (is, is_not, contains) and numeric ops (gt, between) so a regression in
# either branch of apply_filters() surfaces.
_SIMPLE_FILTER_CASES = [
    ({"column": "treatment", "operator": "is", "value": "Placebo"}, "is"),
    ({"column": "treatment", "operator": "is_not", "value": "Placebo"}, "is_not"),
    ({"column": "notes", "operator": "contains", "value": "headache"}, "contains"),
    ({"column": "notes", "operator": "not_contains", "value": "headache"}, "not_contains"),
    ({"column": "age", "operator": "gt", "value": "50"}, "gt"),
    ({"column": "age", "operator": "lte", "value": "30"}, "lte"),
    ({"column": "age", "operator": "between", "value": "30", "value2": "50"}, "between"),
]


@pytest.mark.parametrize(
    "filter_dict",
    [case[0] for case in _SIMPLE_FILTER_CASES],
    ids=[case[1] for case in _SIMPLE_FILTER_CASES],
)
def test_simple_filter_operator_reduces_row_count(mcp_client, filter_dict):
    """Each operator must filter to a strict subset (not zero, not the whole set)."""
    resp = mcp_client.post(
        "/table/data",
        json={"page": 1, "page_size": 100, "filters": [filter_dict]},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["unfiltered_rows"] == 100
    # Strict-subset check: catches both "operator silently does nothing"
    # (total == unfiltered) and "operator wipes out everything" (total == 0).
    assert 0 < body["total_rows"] < body["unfiltered_rows"], (
        f"operator did not produce a strict subset: "
        f"total_rows={body['total_rows']}, unfiltered_rows={body['unfiltered_rows']}"
    )


def test_is_missing_filter_returns_only_missing_rows(mcp_client):
    """is_missing has a different shape than the value-based ops; test it separately.

    weight_kg has at least one NaN row in the fixture (subject_id=1 has no
    weight). The contract is that is_missing returns >0 and <total rows,
    AND that every returned row has weight_kg == None.
    """
    resp = mcp_client.post(
        "/table/data",
        json={
            "page": 1,
            "page_size": 100,
            "filters": [{"column": "weight_kg", "operator": "is_missing"}],
        },
    )

    assert resp.status_code == 200
    body = resp.json()
    assert 0 < body["total_rows"] < body["unfiltered_rows"]
    assert all(row["weight_kg"] is None for row in body["data"])
