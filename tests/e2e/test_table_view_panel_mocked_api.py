"""Playwright checks for table-view side panel behavior using mocked APIs."""
import functools
import json
import re
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

import pytest

pytest.importorskip("playwright.sync_api")

from playwright.sync_api import expect  # noqa: E402

pytestmark = pytest.mark.mocked_integration

REPO_ROOT = Path(__file__).resolve().parents[2]
SAMPLE_DATASET = "sample.csv"
SAMPLE_COLUMNS = ["subject_id", "treatment"]
VISIBLE_CLASS_RE = re.compile(r"\bvisible\b")


@pytest.fixture
def chat_ui_static_url(free_tcp_port):
    handler = functools.partial(
        SimpleHTTPRequestHandler,
        directory=str(REPO_ROOT / "chat_ui"),
    )
    server = ThreadingHTTPServer(("127.0.0.1", free_tcp_port), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{free_tcp_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _ok(body):
    return 200, body


def _dataset_list_response():
    return {
        "datasets": [SAMPLE_DATASET],
        "dataset_info": [],
        "netapp_files": [],
        "netapp_volumes": [],
    }


def _load_response():
    return {
        "columns": SAMPLE_COLUMNS,
        "numeric_columns": [],
        "categorical_columns": ["treatment"],
        "date_columns": [],
        "column_types": {"subject_id": "string", "treatment": "categorical"},
        "num_rows": 1,
    }


def _table_data_response():
    return {
        "data": [{"subject_id": "SUBJ-001", "treatment": "Placebo"}],
        "page": 1,
        "page_size": 100,
        "total_pages": 1,
        "filtered_rows": 1,
        "unfiltered_rows": 1,
    }


def _summary_response():
    return {
        "total_rows": 1,
        "unfiltered_rows": 1,
        "columns": SAMPLE_COLUMNS,
        "missing_values": {
            "missing_percentage": 0,
            "total_missing_cells": 0,
            "columns_with_most_missing": [],
            "by_column": {},
        },
    }


def _column_stats_response():
    return {"unique_count": 1}


def _install_api_routes(page):
    responses = {
        "datasets": _ok(_dataset_list_response()),
        "column_labels": _ok({"available": False, "labels": {}}),
        "chat/status": _ok({"configured": False}),
        "dataset/load": _ok(_load_response()),
        "table/data": _ok(_table_data_response()),
        "table/summary": _ok(_summary_response()),
        "table/column_stats/subject_id": _ok(_column_stats_response()),
        "table/column_values/subject_id": _ok({"values": ["SUBJ-001"], "total_unique": 1}),
        "table/column_stats/treatment": _ok(_column_stats_response()),
        "table/column_values/treatment": _ok({"values": ["Placebo"], "total_unique": 1}),
        "governance/attachment-overviews": _ok({"data": []}),
    }

    def handler(route):
        parsed = urlparse(route.request.url)
        path = parsed.path.lstrip("/")
        response = responses.get(path)
        if response is None:
            route.continue_()
            return

        status, body = response
        route.fulfill(
            status=status,
            content_type="application/json",
            body=json.dumps(body),
        )

    page.route("**/*", handler)


def _open_local_file_browser(page):
    page.locator('[data-testid="browse-files-button"]').click()
    expect(page.locator("#file-browser-modal-overlay")).to_have_class(
        VISIBLE_CLASS_RE, timeout=2_000
    )


def _load_local_dataset(page):
    _open_local_file_browser(page)
    page.locator(f'[data-testid="fb-file-item"][data-fb-name="{SAMPLE_DATASET}"]').click()
    page.locator('[data-testid="fb-load-btn"]').click()
    expect(page.locator('[data-testid="data-row"]').first).to_be_visible(timeout=5_000)


def test_summary_search_without_matches_shows_empty_state(page, chat_ui_static_url):
    _install_api_routes(page)

    page.goto(chat_ui_static_url)
    expect(page.locator('[data-testid="browse-files-button"]')).to_be_visible(timeout=5_000)
    _load_local_dataset(page)

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
