"""Playwright checks for non-chat UI error banners using mocked API responses."""
import functools
import json
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import re
import threading
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


def _error(message, status=500):
    return status, {"error": message}


def _dataset_list_response():
    return {
        "datasets": [SAMPLE_DATASET],
        "dataset_info": [],
        "netapp_files": [],
        "netapp_volumes": [],
    }


def _extension_dataset_list_response():
    return {
        "datasets": [],
        "dataset_info": [{"id": "ds-1", "name": "Clinical Dataset"}],
        "netapp_files": [],
        "netapp_volumes": [],
    }


def _load_response(include_governance_context=False):
    response = {
        "columns": SAMPLE_COLUMNS,
        "numeric_columns": [],
        "categorical_columns": ["treatment"],
        "date_columns": [],
        "column_types": {"subject_id": "string", "treatment": "categorical"},
        "num_rows": 1,
    }
    if include_governance_context:
        response.update({
            "sourceType": "dataset",
            "governanceFilename": SAMPLE_DATASET,
            "datasetId": "ds-1",
            "snapshotId": "snap-1",
        })
    return response


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


def _column_values_response():
    return {"values": ["Placebo"], "total_unique": 1}


def _attachment_overviews_response():
    return {
        "data": [{
            "id": "attachment-1",
            "type": "DatasetSnapshotFile",
            "bundle": {
                "id": "bundle-1",
                "name": "Bundle One",
                "state": "Active",
                "projectOwner": "owner",
                "projectName": "project",
                "projectId": "project-1",
            },
        }]
    }


def _bundle_stages_response():
    return {
        "stages": [],
        "approvals": [],
        "designatedApprovers": [],
        "policyVersionId": "policy-1",
        "currentStage": "",
    }


def _install_api_routes(page, overrides=None):
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
        "table/column_values/treatment": _ok(_column_values_response()),
        "governance/attachment-overviews": _ok({"data": []}),
        "governance/bundles/bundle-1/stages": _ok(_bundle_stages_response()),
        "governance/project-collaborators": _ok({"collaborators": []}),
        "governance/current-user": _ok({}),
    }
    responses.update(overrides or {})

    def handler(route):
        parsed = urlparse(route.request.url)
        path = parsed.path.lstrip("/")
        response = responses.get(path)
        if response is None:
            route.continue_()
            return

        status, body = response() if callable(response) else response
        route.fulfill(
            status=status,
            content_type="application/json",
            body=json.dumps(body),
        )

    page.route("**/*", handler)
    return responses


def _expect_banner(page, message):
    expect(page.locator("#error-banner")).to_have_class(VISIBLE_CLASS_RE, timeout=5_000)
    expect(page.locator("#error-banner-text")).to_contain_text(message, timeout=5_000)


def _dismiss_banner(page):
    page.locator("#error-banner").click()
    expect(page.locator("#error-banner")).not_to_have_class(VISIBLE_CLASS_RE, timeout=2_000)


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


def test_page_load_error_banners_from_mocked_api(page, chat_ui_static_url):
    responses = _install_api_routes(page)

    page.goto(f"{chat_ui_static_url}?filters=%7Bbad")
    _expect_banner(page, "Failed to parse filters from URL")

    _dismiss_banner(page)
    responses["column_labels"] = _error("Column labels exploded")
    page.reload()
    _expect_banner(page, "Error loading column labels: Column labels exploded")

    responses["column_labels"] = _ok({"available": False, "labels": {}})
    responses["datasets"] = _error("Dataset list exploded")
    page.reload()
    _expect_banner(page, "Error loading datasets: Dataset list exploded")


def test_user_triggered_error_banners_from_mocked_api(page, chat_ui_static_url):
    responses = _install_api_routes(page)

    page.goto(chat_ui_static_url)
    expect(page.locator('[data-testid="browse-files-button"]')).to_be_visible(timeout=5_000)

    responses["dataset/load"] = _error("Dataset load exploded")
    _open_local_file_browser(page)
    page.locator(f'[data-testid="fb-file-item"][data-fb-name="{SAMPLE_DATASET}"]').click()
    page.locator('[data-testid="fb-load-btn"]').click()
    _expect_banner(page, "Error loading dataset: Dataset load exploded")

    _dismiss_banner(page)
    responses["dataset/load"] = _ok(_load_response())
    _load_local_dataset(page)

    responses["table/column_values/treatment"] = _error("Autocomplete exploded")
    page.locator('[data-testid="add-filter-btn"]').click()
    expect(page.locator("#filter-modal-overlay")).to_have_class(
        VISIBLE_CLASS_RE, timeout=2_000
    )
    page.locator('[data-testid="filter-column-select"]').select_option("treatment")
    page.locator('[data-testid="filter-value-input"]').fill("Pla")
    _expect_banner(page, "Autocomplete error: Autocomplete exploded")

    _dismiss_banner(page)
    responses["table/column_values/subject_id"] = _error("Distinct values exploded")
    page.locator("#filter-modal-overlay").click(position={"x": 5, "y": 5})
    page.locator("#sidebar-section-select").select_option("distinct")
    _expect_banner(page, "Error loading distinct values: Distinct values exploded")


def test_summary_and_snapshot_error_banners_from_mocked_api(page, chat_ui_static_url):
    responses = _install_api_routes(page, {
        "table/summary": _error("Summary exploded"),
    })

    page.goto(chat_ui_static_url)
    expect(page.locator('[data-testid="browse-files-button"]')).to_be_visible(timeout=5_000)
    _open_local_file_browser(page)
    page.locator(f'[data-testid="fb-file-item"][data-fb-name="{SAMPLE_DATASET}"]').click()
    page.locator('[data-testid="fb-load-btn"]').click()
    _expect_banner(page, "Error loading summary: Summary exploded")

    responses["table/summary"] = _ok(_summary_response())
    responses["datasets"] = _ok(_extension_dataset_list_response())
    responses["snapshots/dataset/ds-1"] = _error("Snapshots exploded")
    page.goto(f"{chat_ui_static_url}?datasetId=ds-1")
    page.locator('[data-testid="browse-files-button"]').click()
    _expect_banner(page, "Error loading snapshots: Snapshots exploded")


@pytest.mark.parametrize(
    ("overrides", "expected_message"),
    [
        (
            {"governance/attachment-overviews": _error("Governance lookup exploded")},
            "Error checking governance bundles: Governance lookup exploded",
        ),
        (
            {
                "governance/attachment-overviews": _ok(_attachment_overviews_response()),
                "governance/bundles/bundle-1/stages": _error("Stages exploded"),
            },
            "Error loading governance bundle stages: Stages exploded",
        ),
        (
            {
                "governance/attachment-overviews": _ok(_attachment_overviews_response()),
                "governance/project-collaborators": _error("Collaborators exploded"),
            },
            "Error loading project collaborators: Collaborators exploded",
        ),
        (
            {
                "governance/attachment-overviews": _ok(_attachment_overviews_response()),
                "governance/current-user": _error("Current user exploded"),
            },
            "Error loading current governance user: Current user exploded",
        ),
    ],
)
def test_governance_error_banners_from_mocked_api(
    page, chat_ui_static_url, overrides, expected_message
):
    _install_api_routes(page, {
        "dataset/load": _ok(_load_response(include_governance_context=True)),
        **overrides,
    })

    page.goto(chat_ui_static_url)
    expect(page.locator('[data-testid="browse-files-button"]')).to_be_visible(timeout=5_000)
    _load_local_dataset(page)
    _expect_banner(page, expected_message)
