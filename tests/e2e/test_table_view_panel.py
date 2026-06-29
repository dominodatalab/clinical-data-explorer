"""Playwright checks for table-view side panel behavior."""

import pytest

pytest.importorskip("playwright.sync_api")

from playwright.sync_api import expect  # noqa: E402

from .static_ui_fixtures import (  # noqa: E402
    chat_ui_static_url,
    install_api_routes,
    load_local_dataset,
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
