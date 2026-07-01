"""Mocked static UI checks for the file browser modal."""

import pytest

pytest.importorskip("playwright.sync_api")

from playwright.sync_api import expect  # noqa: E402

from .static_ui_fixtures import (  # noqa: E402
    VISIBLE_CLASS_RE,
    chat_ui_static_url,
    extension_dataset_list_response,
    extension_netapp_volume_list_response,
    install_api_routes,
    ok,
)

pytestmark = pytest.mark.mocked_integration


def test_project_dataset_sources_include_owner_in_modal_label(page, chat_ui_static_url):
    install_api_routes(page, {
        "datasets": ok(extension_dataset_list_response()),
        "snapshots/dataset/ds-1": ok({"snapshots": []}),
    })

    page.goto(f"{chat_ui_static_url}?projectId=project-1")
    expect(page.locator('[data-testid="browse-files-button"]')).to_be_visible(timeout=5_000)

    page.locator('[data-testid="browse-files-button"]').click()
    expect(page.locator("#file-browser-modal-overlay")).to_have_class(
        VISIBLE_CLASS_RE, timeout=2_000
    )
    expect(page.locator('#fb-source-select option[value="ds-1"]')).to_have_text(
        "Dataset Owner/Clinical Dataset"
    )


def test_project_netapp_volume_sources_include_project_in_modal_label(page, chat_ui_static_url):
    install_api_routes(page, {
        "datasets": ok(extension_netapp_volume_list_response()),
        "snapshots/netapp/netapp-volume-Safety-Volume-nv-1": ok({"snapshots": []}),
    })

    page.goto(f"{chat_ui_static_url}?projectId=project-1")
    expect(page.locator('[data-testid="browse-files-button"]')).to_be_visible(timeout=5_000)

    page.locator('[data-testid="browse-files-button"]').click()
    expect(page.locator("#file-browser-modal-overlay")).to_have_class(
        VISIBLE_CLASS_RE, timeout=2_000
    )
    expect(page.locator('#fb-source-select option[value="netapp-volume-Safety-Volume-nv-1"]')).to_have_text(
        "Safety Project/Safety Volume"
    )
