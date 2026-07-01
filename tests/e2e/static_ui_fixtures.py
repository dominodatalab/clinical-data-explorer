"""Shared fixtures and API stubs for static Chat UI Playwright tests."""
import functools
import json
import re
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

import pytest

from playwright.sync_api import expect

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


def ok(body):
    return 200, body


def error(message, status=500):
    return status, {"error": message}


def dataset_list_response():
    return {
        "datasets": [SAMPLE_DATASET],
        "dataset_info": [],
        "netapp_files": [],
        "netapp_volumes": [],
    }


def extension_dataset_list_response():
    return {
        "datasets": [],
        "dataset_info": [{"id": "ds-1", "name": "Clinical Dataset", "owner_name": "Dataset Owner"}],
        "netapp_files": [],
        "netapp_volumes": [],
    }


def extension_netapp_volume_list_response():
    return {
        "datasets": [],
        "dataset_info": [],
        "netapp_files": [{
            "display_name": "Safety Volume/adsl.csv",
            "volume_key": "netapp-volume-Safety-Volume-nv-1",
            "volume_name": "Safety Volume",
            "volume_id": "nv-1",
            "project_name": "Safety Project",
        }],
        "netapp_volumes": [{
            "id": "nv-1",
            "name": "Safety Volume",
            "unique_name": "netapp-volume-Safety-Volume-nv-1",
            "project_name": "Safety Project",
        }],
    }


def load_response(include_governance_context=False):
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


def table_data_response():
    return {
        "data": [{"subject_id": "SUBJ-001", "treatment": "Placebo"}],
        "page": 1,
        "page_size": 100,
        "total_pages": 1,
        "filtered_rows": 1,
        "unfiltered_rows": 1,
    }


def summary_response():
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


def column_stats_response():
    return {"unique_count": 1}


def column_values_response():
    return {"values": ["Placebo"], "total_unique": 1}


def attachment_overviews_response():
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


def bundle_stages_response():
    return {
        "stages": [],
        "approvals": [],
        "designatedApprovers": [],
        "policyVersionId": "policy-1",
        "currentStage": "",
    }


def install_api_routes(page, overrides=None):
    responses = {
        "datasets": ok(dataset_list_response()),
        "column_labels": ok({"available": False, "labels": {}}),
        "chat/status": ok({"configured": False}),
        "dataset/load": ok(load_response()),
        "table/data": ok(table_data_response()),
        "table/summary": ok(summary_response()),
        "table/column_stats/subject_id": ok(column_stats_response()),
        "table/column_values/subject_id": ok({"values": ["SUBJ-001"], "total_unique": 1}),
        "table/column_stats/treatment": ok(column_stats_response()),
        "table/column_values/treatment": ok(column_values_response()),
        "governance/attachment-overviews": ok({"data": []}),
        "governance/bundles/bundle-1/stages": ok(bundle_stages_response()),
        "governance/project-collaborators": ok({"collaborators": []}),
        "governance/current-user": ok({}),
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


def open_local_file_browser(page):
    page.locator('[data-testid="browse-files-button"]').click()
    expect(page.locator("#file-browser-modal-overlay")).to_have_class(
        VISIBLE_CLASS_RE, timeout=2_000
    )


def load_local_dataset(page):
    open_local_file_browser(page)
    page.locator(f'[data-testid="fb-file-item"][data-fb-name="{SAMPLE_DATASET}"]').click()
    page.locator('[data-testid="fb-load-btn"]').click()
    expect(page.locator('[data-testid="data-row"]').first).to_be_visible(timeout=5_000)
