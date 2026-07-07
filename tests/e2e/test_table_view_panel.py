"""Playwright checks for table-view side panel behavior."""

import pytest

pytest.importorskip("playwright.sync_api")

from playwright.sync_api import expect  # noqa: E402

from .static_ui_fixtures import (  # noqa: E402
    chat_ui_static_url,
    install_api_routes,
    load_local_dataset,
    ok,
)

pytestmark = pytest.mark.mocked_integration


def test_summary_search_without_matches_shows_empty_state(page, chat_ui_static_url):
    install_api_routes(page)

    page.goto(chat_ui_static_url)
    expect(page.locator('[data-testid="browse-files-button"]')).to_be_visible(timeout=5_000)
    load_local_dataset(page)

    page.locator("#sidebar-section-select").select_option("stats")
    expect(page.locator("#stats-search-input")).to_be_visible(timeout=5_000)
    page.locator("#stats-search-input").fill("abc123")

    expect(page.locator("#stats-table-container")).to_contain_text(
        "No matching variables found.",
        timeout=5_000,
    )
    expect(page.locator("#stats-table-container")).not_to_contain_text(
        "Preparing summary statistics",
        timeout=1_000,
    )


def test_filter_chip_label_does_not_add_quotes_around_value(page, chat_ui_static_url):
    install_api_routes(page)

    page.goto(chat_ui_static_url)
    expect(page.locator('[data-testid="browse-files-button"]')).to_be_visible(timeout=5_000)
    load_local_dataset(page)

    page.locator('[data-testid="add-filter-btn"]').click()
    expect(page.locator("#filter-modal-overlay")).to_be_visible(timeout=2_000)
    page.locator('[data-testid="filter-column-select"]').select_option("treatment")
    page.locator('[data-testid="filter-operator-select"]').select_option("is")
    page.locator('[data-testid="filter-value-input"]').fill("Placebo")
    page.locator('[data-testid="filter-apply-btn"]').click()

    filter_chip = page.locator('[data-testid="active-filters"] .filter-chip').first
    expect(filter_chip).to_contain_text("treatment = Placebo", timeout=5_000)
    expect(filter_chip).not_to_contain_text('"Placebo"', timeout=1_000)


def test_row_details_numeric_values_do_not_stack(page, chat_ui_static_url):
    numeric_columns = [
        "age",
        "bmi",
        "bp",
        "s1",
        "s2",
        "s3",
        "s4",
        "s5",
        "s6",
    ]
    row = {
        "age": 0.0380759064334241,
        "bmi": 0.0616962065186885,
        "bp": 0.0218723549949558,
        "s1": -0.0442234984244464,
        "s2": -0.0348207628376986,
        "s3": -0.0434008456520249,
        "s4": -0.00259226199818328,
        "s5": 0.0199084208763183,
        "s6": -0.0176461251598038,
    }
    install_api_routes(
        page,
        overrides={
            "dataset/load": ok({
                "columns": numeric_columns,
                "numeric_columns": numeric_columns,
                "categorical_columns": [],
                "date_columns": [],
                "column_types": {column: "numeric" for column in numeric_columns},
                "num_rows": 1,
            }),
            "table/data": ok({
                "data": [row],
                "page": 1,
                "page_size": 100,
                "total_pages": 1,
                "filtered_rows": 1,
                "unfiltered_rows": 1,
            }),
            "table/summary": ok({
                "total_rows": 1,
                "unfiltered_rows": 1,
                "columns": numeric_columns,
                "missing_values": {
                    "missing_percentage": 0,
                    "total_missing_cells": 0,
                    "columns_with_most_missing": [],
                    "by_column": {},
                },
            }),
        },
    )

    page.goto(chat_ui_static_url)
    expect(page.locator('[data-testid="browse-files-button"]')).to_be_visible(timeout=5_000)
    load_local_dataset(page)

    page.locator('[data-testid="data-row"]').first.click()
    numeric_detail = page.locator("#row-details-body .row-detail-table td.numeric").first
    expect(numeric_detail).to_be_visible(timeout=5_000)
    expect(numeric_detail).to_have_css("white-space", "nowrap")

    box = numeric_detail.bounding_box()
    assert box is not None
    assert box["width"] > 120
    assert box["height"] < 40
