"""Browser coverage for bare App launches that redirect into Domino."""

import json
import re
from urllib.parse import urlparse

import pytest

pytest.importorskip("playwright.sync_api")

from playwright.sync_api import expect  # noqa: E402

from .static_ui_fixtures import chat_ui_static_url  # noqa: E402

pytestmark = pytest.mark.mocked_integration


def test_app_subdomain_launch_redirect_uses_top_level_origin(page, chat_ui_static_url):
    parsed_static_url = urlparse(chat_ui_static_url)
    app_url = f"{parsed_static_url.scheme}://app.localhost:{parsed_static_url.port}"
    redirect_path = (
        "/u/owner/project/extension"
        "?mountPointType=projectSidebar&extensionId=extension-1&projectId=project-1"
    )
    expected_url = f"{parsed_static_url.scheme}://localhost:{parsed_static_url.port}{redirect_path}"

    page.route(
        "**/launch-context",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps({"available": True, "redirectPath": redirect_path}),
        ),
    )
    page.route(
        re.compile(r".*/u/owner/project/extension(?:\?.*)?$"),
        lambda route: route.fulfill(status=200, content_type="text/html", body="Redirected"),
    )

    page.goto(app_url)

    expect(page).to_have_url(expected_url, timeout=5_000)
