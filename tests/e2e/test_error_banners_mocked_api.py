"""Playwright checks for non-chat UI error banners using mocked API responses."""

import pytest

pytest.importorskip("playwright.sync_api")

from playwright.sync_api import expect  # noqa: E402

from .static_ui_fixtures import (  # noqa: E402
    VISIBLE_CLASS_RE,
    SAMPLE_DATASET,
    attachment_overviews_response,
    chat_ui_static_url,
    error,
    extension_dataset_list_response,
    install_api_routes,
    load_local_dataset,
    load_response,
    ok,
    open_local_file_browser,
    summary_response,
)

pytestmark = pytest.mark.mocked_integration


def _expect_banner(page, message):
    expect(page.locator("#error-banner")).to_have_class(VISIBLE_CLASS_RE, timeout=5_000)
    expect(page.locator("#error-banner-text")).to_contain_text(message, timeout=5_000)


def _dismiss_banner(page):
    page.locator("#error-banner-close").click()
    expect(page.locator("#error-banner")).not_to_have_class(VISIBLE_CLASS_RE, timeout=2_000)


def test_page_load_error_banners_from_mocked_api(page, chat_ui_static_url):
    responses = install_api_routes(page)

    page.goto(f"{chat_ui_static_url}?filters=%7Bbad")
    _expect_banner(page, "Failed to parse filters from URL")

    _dismiss_banner(page)
    responses["column_labels"] = error("Column labels exploded")
    page.reload()
    _expect_banner(page, "Error loading column labels: Column labels exploded")

    responses["column_labels"] = ok({"available": False, "labels": {}})
    responses["datasets"] = error("Dataset list exploded")
    page.reload()
    _expect_banner(page, "Error loading datasets: Dataset list exploded")


def test_error_banner_body_click_does_not_dismiss(page, chat_ui_static_url):
    install_api_routes(page)

    page.goto(f"{chat_ui_static_url}?filters=%7Bbad")
    _expect_banner(page, "Failed to parse filters from URL")

    page.locator("#error-banner-text").click()
    expect(page.locator("#error-banner")).to_have_class(VISIBLE_CLASS_RE, timeout=2_000)

    _dismiss_banner(page)


def test_user_triggered_error_banners_from_mocked_api(page, chat_ui_static_url):
    responses = install_api_routes(page)

    page.goto(chat_ui_static_url)
    expect(page.locator('[data-testid="browse-files-button"]')).to_be_visible(timeout=5_000)

    responses["dataset/load"] = error("Dataset load exploded")
    open_local_file_browser(page)
    page.locator(f'[data-testid="fb-file-item"][data-fb-name="{SAMPLE_DATASET}"]').click()
    page.locator('[data-testid="fb-load-btn"]').click()
    _expect_banner(page, "Error loading dataset: Dataset load exploded")

    _dismiss_banner(page)
    responses["dataset/load"] = ok(load_response())
    load_local_dataset(page)

    responses["table/column_values/treatment"] = error("Autocomplete exploded")
    page.locator('[data-testid="add-filter-btn"]').click()
    expect(page.locator("#filter-modal-overlay")).to_have_class(
        VISIBLE_CLASS_RE, timeout=2_000
    )
    page.locator('[data-testid="filter-column-select"]').select_option("treatment")
    page.locator('[data-testid="filter-value-input"]').fill("Pla")
    _expect_banner(page, "Autocomplete error: Autocomplete exploded")

    _dismiss_banner(page)
    responses["table/column_values/subject_id"] = error("Distinct values exploded")
    page.locator("#filter-modal-overlay").click(position={"x": 5, "y": 5})
    page.locator("#sidebar-section-select").select_option("distinct")
    _expect_banner(page, "Error loading distinct values: Distinct values exploded")


def test_summary_and_snapshot_error_banners_from_mocked_api(page, chat_ui_static_url):
    responses = install_api_routes(page, {
        "table/summary": error("Summary exploded"),
    })

    page.goto(chat_ui_static_url)
    expect(page.locator('[data-testid="browse-files-button"]')).to_be_visible(timeout=5_000)
    open_local_file_browser(page)
    page.locator(f'[data-testid="fb-file-item"][data-fb-name="{SAMPLE_DATASET}"]').click()
    page.locator('[data-testid="fb-load-btn"]').click()
    _expect_banner(page, "Error loading summary: Summary exploded")

    responses["table/summary"] = ok(summary_response())
    responses["datasets"] = ok(extension_dataset_list_response())
    responses["snapshots/dataset/ds-1"] = error("Snapshots exploded")
    page.goto(f"{chat_ui_static_url}?datasetId=ds-1")
    page.locator('[data-testid="browse-files-button"]').click()
    _expect_banner(page, "Error loading snapshots: Snapshots exploded")


@pytest.mark.parametrize(
    ("overrides", "expected_message"),
    [
        (
            {"governance/attachment-overviews": error("Governance lookup exploded")},
            "Error checking governance bundles: Governance lookup exploded",
        ),
        (
            {
                "governance/attachment-overviews": ok(attachment_overviews_response()),
                "governance/bundles/bundle-1/stages": error("Stages exploded"),
            },
            "Error loading governance bundle stages: Stages exploded",
        ),
        (
            {
                "governance/attachment-overviews": ok(attachment_overviews_response()),
                "governance/project-collaborators": error("Collaborators exploded"),
            },
            "Error loading project collaborators: Collaborators exploded",
        ),
        (
            {
                "governance/attachment-overviews": ok(attachment_overviews_response()),
                "governance/current-user": error("Current user exploded"),
            },
            "Error loading current governance user: Current user exploded",
        ),
    ],
)
def test_governance_error_banners_from_mocked_api(
    page, chat_ui_static_url, overrides, expected_message
):
    install_api_routes(page, {
        "dataset/load": ok(load_response(include_governance_context=True)),
        **overrides,
    })

    page.goto(chat_ui_static_url)
    expect(page.locator('[data-testid="browse-files-button"]')).to_be_visible(timeout=5_000)
    load_local_dataset(page)
    _expect_banner(page, expected_message)
