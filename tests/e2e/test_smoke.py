"""Single Playwright smoke test that walks the whole app.

Selectors target data-testid attributes. The full inventory of testids this
test depends on is listed here so future engineers know what to keep stable
when moving DOM around:

  tab-table, tab-chat, tab-explore
  current-dataset-label, browse-files-button
  fb-source-select, fb-file-list, fb-file-item, fb-load-btn, fb-cancel-btn
  table-row-info, table-body, data-row, next-page-btn
  add-filter-btn, filter-column-select, filter-operator-select,
    filter-value-input, filter-apply-btn, active-filters
  expression-filter-btn, expression-input, expression-apply-btn
  chat-input
  histogram-column-select, main-chart, bar-category-select, bar-chart

The fixture `_e2e_sample.csv` is copied into datasets/ by the session fixture.
"""
import re

import pytest

pytest.importorskip("playwright.sync_api")

from playwright.sync_api import expect  # noqa: E402

# Matches the `.visible` class set by core/modals.js's openModal() — used
# below as a fast-fail signal that a modal-open click actually opened its
# overlay (regressed in P14b/4.4g when initFilters got accidentally deleted
# along with the table-view block; the e2e suite caught it but only via a
# 30s child-element timeout deep in step 4 — the explicit `.visible` checks
# below fail in 2s and point straight at the wiring bug).
_VISIBLE_CLASS_RE = re.compile(r"\bvisible\b")

FIXTURE_NAME = "_e2e_sample.csv"


def _pick_file(page, name):
    """Click a file row in the file browser by its data-fb-name attribute."""
    page.locator(f'[data-testid="fb-file-item"][data-fb-name="{name}"]').click()


def test_app_smoke(live_servers, page):
    flask_url = live_servers["flask_url"]

    # --- 1. Navigate ---
    page.goto(flask_url)
    expect(page.locator('[data-testid="browse-files-button"]')).to_be_visible(timeout=15_000)

    # --- 2. Load dataset via file browser ---
    page.locator('[data-testid="browse-files-button"]').click()
    source_select = page.locator('[data-testid="fb-source-select"]')
    expect(source_select).to_be_visible()
    # In local mode there's a single "Local Files" source; it auto-selects.
    # Wait for the file list to populate, then pick our fixture.
    file_item = page.locator(f'[data-testid="fb-file-item"][data-fb-name="{FIXTURE_NAME}"]')
    expect(file_item).to_be_visible(timeout=10_000)
    file_item.click()
    page.locator('[data-testid="fb-load-btn"]').click()

    # Table should render at least one row.
    expect(page.locator('[data-testid="data-row"]').first).to_be_visible(timeout=15_000)

    # --- 3. Pagination ---
    # Force a page size smaller than the fixture (100 rows) so pagination is
    # exercisable regardless of the UI's default page size.
    page.locator('#page-size-selector').select_option('25')
    expect(page.locator('[data-testid="next-page-btn"]')).to_be_enabled(timeout=10_000)
    first_row_p1 = page.locator('[data-testid="data-row"]').first.text_content()
    page.locator('[data-testid="next-page-btn"]').click()
    # Wait for the table to re-render a different first row.
    page.wait_for_function(
        "(prev) => {"
        "  const row = document.querySelector('[data-testid=\"data-row\"]');"
        "  return row && row.textContent !== prev;"
        "}",
        arg=first_row_p1,
        timeout=10_000,
    )

    # --- 4. Simple filter on the treatment column ---
    page.locator('[data-testid="add-filter-btn"]').click()
    expect(page.locator('#filter-modal-overlay')).to_have_class(
        _VISIBLE_CLASS_RE, timeout=2_000
    )
    page.locator('[data-testid="filter-column-select"]').select_option("treatment")
    page.locator('[data-testid="filter-operator-select"]').select_option("is")
    page.locator('[data-testid="filter-value-input"]').fill("Placebo")
    page.locator('[data-testid="filter-apply-btn"]').click()
    # A filter chip should appear in #active-filters.
    expect(page.locator('[data-testid="active-filters"] .filter-chip').first).to_be_visible(timeout=10_000)

    # --- 5. Expression filter (SAS syntax is the default tab) ---
    page.locator('[data-testid="expression-filter-btn"]').click()
    expect(page.locator('#expression-modal-overlay')).to_have_class(
        _VISIBLE_CLASS_RE, timeout=2_000
    )
    page.locator('[data-testid="expression-input"]').fill("age > 40")
    page.locator('[data-testid="expression-apply-btn"]').click()
    # Table re-renders under the new filter — just wait for a data row to still be present.
    expect(page.locator('[data-testid="data-row"]').first).to_be_visible(timeout=10_000)

    # --- 6. Chat tab ---
    page.locator('[data-testid="tab-chat"]').click()
    # Chat input is only rendered when chat is configured. If the env has no
    # LLM_API_KEY, the empty state is shown instead — accept either.
    chat_or_empty = page.locator(
        '[data-testid="chat-input"], #chat-empty-state'
    )
    expect(chat_or_empty.first).to_be_visible(timeout=10_000)

    # --- 7. Explore tab — histogram ---
    page.locator('[data-testid="tab-explore"]').click()
    page.locator('[data-testid="histogram-column-select"]').select_option("age")
    # Highcharts renders an <svg> inside the container.
    expect(page.locator('[data-testid="main-chart"] svg')).to_be_visible(timeout=15_000)

    # --- 8. Bar chart by categorical column ---
    page.locator('[data-testid="bar-category-select"]').select_option("treatment")
    expect(page.locator('[data-testid="bar-chart"] svg')).to_be_visible(timeout=15_000)

    # --- 9. File browser modal open/close ---
    page.locator('[data-testid="tab-table"]').click()
    page.locator('[data-testid="browse-files-button"]').click()
    expect(page.locator('#file-browser-modal-overlay')).to_have_class(
        _VISIBLE_CLASS_RE, timeout=2_000
    )
    expect(page.locator('[data-testid="fb-source-select"]')).to_be_visible()
    page.locator('[data-testid="fb-cancel-btn"]').click()
    expect(page.locator('[data-testid="fb-source-select"]')).not_to_be_visible(timeout=5_000)

    # --- 10. Row details sidebar ---
    page.locator('[data-testid="data-row"]').first.click()
    page.locator('#sidebar-section-select').select_option("row-details")
    expect(page.locator('#row-details-body')).to_contain_text("subject_id", timeout=5_000)


def test_modal_wiring(live_servers, page):
    """Every top-level modal-open button must toggle its overlay's `.visible` class.

    Catches missing initFn() calls in script.js's DOMContentLoaded — the
    failure mode that surfaced in P14b/4.4g when initFilters got accidentally
    deleted along with the table-view block. The full-walk smoke test above
    eventually catches that class of regression too, but only via a 30s
    child-element timeout buried deep in step 4. This test fires three
    independent open-then-dismiss cycles in well under 2s each so failures
    point straight at the wiring bug.

    Bonus regression coverage: the dismissal step clicks the overlay backdrop
    rather than the cancel button, exercising `attachOverlayDismiss()` from
    `core/modals.js` (extracted in P14b/4.6) at the same time. Position
    (5, 5) targets the top-left corner of the overlay, which is always
    backdrop on a centered modal — never inner modal content.
    """
    page.goto(live_servers["flask_url"])
    expect(page.locator('[data-testid="browse-files-button"]')).to_be_visible(timeout=15_000)

    cases = [
        ("browse-files-button", "file-browser-modal-overlay"),
        ("add-filter-btn", "filter-modal-overlay"),
        ("expression-filter-btn", "expression-modal-overlay"),
    ]
    for btn_testid, modal_id in cases:
        page.locator(f'[data-testid="{btn_testid}"]').click()
        expect(page.locator(f'#{modal_id}')).to_have_class(
            _VISIBLE_CLASS_RE, timeout=2_000
        )
        # Click the overlay backdrop (top-left corner) to dismiss. This
        # exercises core/modals.js's attachOverlayDismiss() — the
        # `e.target === overlayEl` check must pass for backdrop clicks
        # but reject inner-modal-content clicks.
        page.locator(f'#{modal_id}').click(position={"x": 5, "y": 5})
        expect(page.locator(f'#{modal_id}')).not_to_have_class(
            _VISIBLE_CLASS_RE, timeout=2_000
        )
