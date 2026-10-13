"""Chat tool settings page: catalog, modes, saving, schema forms and previews (stubbed API)."""
import json
import re
from urllib.parse import urlparse

import pytest

pytest.importorskip("playwright.sync_api")

from playwright.sync_api import expect  # noqa: E402

from .static_ui_fixtures import REPO_ROOT, chat_ui_static_url  # noqa: E402,F401

pytestmark = pytest.mark.mocked_integration

PAGE_HTML = REPO_ROOT / "chat_ui" / "admin" / "chat-tools.html"
INJECTION = "<img src=x onerror=\"window.__xss=true\">"


def _tool(name, title, mode="enabled", properties=None, required=None, **extra):
    return {
        "name": name,
        "title": title,
        "description": f"{title}\n\nDescription of **{name}**.\n\n<script>window.__xss=true</script>",
        "input_schema": {"type": "object", "properties": properties or {}, **({"required": required} if required else {})},
        "declared_output_example": None,
        "example_args": None,
        "mode": mode,
        "note": None,
        "is_default": False,
        "is_new": False,
        **extra,
    }


def _initial_state():
    return {
        "tools": [
            _tool(
                "get_group_analysis", "Get Group Analysis",
                properties={
                    "feature": {"type": "string", "title": "feature", "description": "Numeric feature"},
                    "group_by": {"type": "string", "title": "group_by"},
                    "bins": {"type": "integer", "default": 30},
                    "ratio": {"type": "number"},
                    "flag": {"type": "boolean"},
                    "syntax": {"type": "string", "enum": ["sas", "r"]},
                    "features": {"anyOf": [{"type": "array", "items": {"type": "string"}}, {"type": "null"}]},
                    "filter": {"anyOf": [{"type": "object", "properties": {"column": {"type": "string"}}, "required": ["column"]}, {"type": "null"}]},
                },
                required=["feature", "group_by"],
                declared_output_example=[{"group": "A", "mean": 1}],
                example_args={"feature": "AGE", "group_by": "ARM"},
            ),
            _tool("get_dataset_head", "Get Dataset Head", mode="user_only", properties={"rows": {"type": "integer", "default": 5}}),
            _tool("get_dataset_data", "Get Dataset Data", mode="disabled"),
            _tool("brand_new_tool", "Brand New Tool", is_default=True, is_new=True),
        ],
        "stale": [{"name": "removed_tool", "mode": "disabled", "note": None}],
        "policy": {"revision": 3, "default_mode": "enabled", "updated_at": None, "updated_by": None, "change_log": []},
        "policy_error": None,
        "catalog_error": None,
        "store": {"durable": True, "path": "/mnt/data/chat-tool-settings/chat_tool_policy.json", "error": None},
        "sample": {"label": "Sample: CDISC pilot ADSL (36 subjects), for previewing tool output only."},
    }


class AdminStub:
    """Stubbed admin API with just enough behavior: save, conflict, previews."""

    def __init__(self):
        self.state = _initial_state()
        self.saves = []
        self.resets = []
        self.previews = []
        self.conflict_next = False
        self.save_status = None
        self.preview_results = {}
        self.sample_error = False

    def handle(self, route):
        request = route.request
        path = urlparse(request.url).path
        method = request.method

        def respond(body, status=200):
            route.fulfill(status=status, content_type="application/json", body=json.dumps(body))

        if path.endswith("/admin/chat-tools") and method == "GET":
            route.fulfill(status=200, content_type="text/html", body=PAGE_HTML.read_text())
        elif path.endswith("/admin/api/chat-tools") and method == "GET":
            respond(self.state)
        elif path.endswith("/admin/api/chat-tools/policy") and method == "PUT":
            body = request.post_data_json
            self.saves.append(body)
            if self.save_status:
                respond({"error": "The settings dataset is not mounted", "code": "store_unavailable"}, self.save_status)
            elif self.conflict_next or body["expected_revision"] != self.state["policy"]["revision"]:
                respond({"error": "Someone else saved; reload to see their changes.", "code": "conflict"}, 409)
            else:
                self._apply(body)
                respond({"policy": self.state["policy"]})
        elif path.endswith("/admin/api/chat-tools/policy/reset") and method == "POST":
            self.resets.append(request.post_data_json)
            self.state["policy_error"] = None
            self.state["policy"]["revision"] = 1
            respond({"policy": self.state["policy"]})
        elif path.endswith("/preview/dataset") and method == "POST":
            if self.sample_error:
                respond({"error": "Could not load the preview sample: mcp down"}, 502)
            else:
                respond({"columns": ["AGE", "ARM", "BMIBL", "SEX"], "numeric_columns": ["AGE"], "categorical_columns": ["ARM"],
                         "date_columns": [], "num_rows": 36, "label": self.state["sample"]["label"]})
        elif path.endswith("/preview") and method == "POST":
            name = path.split("/")[-2]
            self.previews.append((name, request.post_data_json))
            respond(self.preview_results.get(name, {
                "content": [{"ARM": "Placebo", "AGE": 75.2}, {"ARM": "Xanomeline", "AGE": 74.4}],
                "content_text": "[{\"ARM\": \"Placebo\", \"AGE\": 75.2}]", "size_bytes": 123, "approx_tokens": 30, "elapsed_ms": 12,
            }))
        else:
            route.continue_()

    def _apply(self, body):
        by_name = {tool["name"]: tool for tool in self.state["tools"]}
        for name, entry in body["tools"].items():
            if name in by_name:
                by_name[name].update(mode=entry["mode"], note=entry.get("note"), is_default=False, is_new=False)
        self.state["stale"] = [s for s in self.state["stale"] if s["name"] in body["tools"]]
        self.state["policy"] = {**self.state["policy"], "revision": body["expected_revision"] + 1, "default_mode": body["default_mode"]}


@pytest.fixture
def admin(page, chat_ui_static_url):
    stub = AdminStub()
    page.route("**/admin/**", stub.handle)
    stub.url = f"{chat_ui_static_url}/admin/chat-tools"
    stub.page = page
    return stub


def _open(admin):
    admin.page.goto(admin.url)
    expect(admin.page.locator('[data-testid="admin-tool-row"]').first).to_be_visible(timeout=10_000)
    return admin.page


def _row(page, name):
    return page.locator(f'[data-testid="admin-tool-row"][data-tool-name="{name}"]')


def _set_mode(page, name, value):
    _row(page, name).locator(f'[data-testid="mode-{value}"]').check(force=True)


# ----- catalog -----

def test_lists_every_tool_from_the_catalog_with_modes_and_badges(admin):
    page = _open(admin)

    rows = page.locator('[data-testid="admin-tool-row"]')
    expect(rows).to_have_count(4)
    expect(rows.first.locator(".tool-title")).to_have_text("Get Group Analysis")
    expect(rows.first.locator("code")).to_have_text("get_group_analysis")
    expect(_row(page, "get_dataset_head").locator('[data-testid="mode-user_only"]')).to_be_checked()
    expect(_row(page, "get_dataset_data").locator('[data-testid="mode-disabled"]')).to_be_checked()
    expect(_row(page, "get_group_analysis").locator('[data-testid="mode-enabled"]')).to_be_checked()
    expect(_row(page, "brand_new_tool").locator('[data-testid="new-badge"]')).to_have_text("New, using default")
    # the segmented control uses the user-facing labels
    expect(_row(page, "get_dataset_head").locator(".mode-option span")).to_have_text(["On", "Hidden from AI", "Off"])


def test_search_and_filter_chips(admin):
    page = _open(admin)

    page.locator('[data-testid="tool-search"]').fill("head")
    expect(page.locator('[data-testid="admin-tool-row"]')).to_have_count(1)
    page.locator('[data-testid="tool-search"]').fill("")

    page.locator('[data-testid="filter-user_only"]').click()
    expect(page.locator('[data-testid="admin-tool-row"]')).to_have_count(1)
    page.locator('[data-testid="filter-new"]').click()
    expect(_row(page, "brand_new_tool")).to_be_visible()
    page.locator('[data-testid="filter-disabled"]').click()
    expect(_row(page, "get_dataset_data")).to_be_visible()
    page.locator('[data-testid="filter-all"]').click()
    expect(page.locator('[data-testid="admin-tool-row"]')).to_have_count(4)
    expect(page.locator('[data-testid="filter-disabled"]')).to_contain_text("(1)")


def test_stale_entries_are_listed_and_can_be_removed(admin):
    page = _open(admin)

    expect(page.locator('[data-testid="stale-row"]')).to_have_count(1)
    expect(page.locator('[data-testid="stale-row"]')).to_contain_text("removed_tool")
    page.locator('[data-testid="delete-stale"]').click()

    expect(page.locator('[data-testid="stale-row"]')).to_have_count(0)
    expect(page.locator('[data-testid="unsaved-count"]')).to_have_text("Unsaved changes (1)")
    page.locator('[data-testid="save-button"]').click()
    expect(page.locator('[data-testid="banner-success"]')).to_be_visible()
    assert "removed_tool" not in admin.saves[0]["tools"]


# ----- saving -----

def test_changing_modes_shows_the_unsaved_bar_and_saves_the_whole_map(admin):
    page = _open(admin)
    expect(page.locator('[data-testid="unsaved-bar"]')).to_be_hidden()

    _set_mode(page, "get_group_analysis", "user_only")
    _set_mode(page, "get_dataset_data", "enabled")

    expect(page.locator('[data-testid="unsaved-count"]')).to_have_text("Unsaved changes (2)")
    page.locator('[data-testid="unsaved-count"]').click()
    expect(page.locator("#unsaved-list")).to_contain_text("Get Group Analysis: On → Hidden from AI")
    page.locator('[data-testid="save-button"]').click()

    expect(page.locator('[data-testid="banner-success"]')).to_contain_text("Saved")
    expect(page.locator('[data-testid="unsaved-bar"]')).to_be_hidden()
    body = admin.saves[0]
    assert body["expected_revision"] == 3
    assert body["default_mode"] == "enabled"
    assert body["tools"]["get_group_analysis"] == {"mode": "user_only"}
    assert body["tools"]["get_dataset_data"] == {"mode": "enabled"}
    assert body["tools"]["brand_new_tool"] == {"mode": "enabled"}  # saving acknowledges every listed tool
    expect(_row(page, "brand_new_tool").locator('[data-testid="new-badge"]')).to_have_count(0)


def test_discard_restores_the_server_state(admin):
    page = _open(admin)
    _set_mode(page, "get_group_analysis", "disabled")
    page.locator('[data-testid="default-mode-select"]').select_option("disabled")

    page.locator('[data-testid="discard-button"]').click()

    expect(page.locator('[data-testid="unsaved-bar"]')).to_be_hidden()
    expect(_row(page, "get_group_analysis").locator('[data-testid="mode-enabled"]')).to_be_checked()
    expect(page.locator('[data-testid="default-mode-select"]')).to_have_value("enabled")
    assert admin.saves == []


def test_default_mode_for_new_tools_is_saved(admin):
    page = _open(admin)

    page.locator('[data-testid="default-mode-select"]').select_option("disabled")
    page.locator('[data-testid="save-button"]').click()

    expect(page.locator('[data-testid="banner-success"]')).to_be_visible()
    assert admin.saves[0]["default_mode"] == "disabled"


def test_note_is_saved_with_the_tool(admin):
    page = _open(admin)
    page.locator('[data-testid="tool-note"]').fill("  contains raw rows  ")

    page.locator('[data-testid="save-button"]').click()

    expect(page.locator('[data-testid="banner-success"]')).to_be_visible()
    assert admin.saves[0]["tools"]["get_group_analysis"] == {"mode": "enabled", "note": "contains raw rows"}


def test_conflict_tells_the_admin_to_reload_and_keeps_their_draft(admin):
    page = _open(admin)
    _set_mode(page, "get_group_analysis", "disabled")
    admin.conflict_next = True

    page.locator('[data-testid="save-button"]').click()

    expect(page.locator('[data-testid="banner-error"]')).to_contain_text("Someone else saved; reload to see their changes.")
    expect(page.locator('[data-testid="unsaved-count"]')).to_have_text("Unsaved changes (1)")
    admin.conflict_next = False
    page.locator('[data-testid="reload-button"]').click()
    expect(page.locator('[data-testid="unsaved-bar"]')).to_be_hidden()
    expect(_row(page, "get_group_analysis").locator('[data-testid="mode-enabled"]')).to_be_checked()


def test_unwritable_store_disables_saving_and_names_the_problem(admin):
    admin.state["store"] = {
        "durable": False,
        "path": "/mnt/data/chat-tool-settings/chat_tool_policy.json",
        "error": "The settings dataset 'chat-tool-settings' is not mounted (expected /mnt/data/chat-tool-settings/chat_tool_policy.json).",
    }
    page = _open(admin)

    expect(page.locator('[data-testid="banner-store"]')).to_contain_text("chat-tool-settings")
    expect(page.locator('[data-testid="banner-store"]')).to_contain_text("/mnt/data/chat-tool-settings/chat_tool_policy.json")
    _set_mode(page, "get_group_analysis", "disabled")
    expect(page.locator('[data-testid="save-button"]')).to_be_disabled()


def test_unreadable_policy_offers_reset_to_defaults(admin):
    admin.state["policy_error"] = "The chat tool policy file /x is invalid: boom"
    admin.state["policy"]["revision"] = None
    page = _open(admin)
    page.once("dialog", lambda dialog: dialog.accept())

    expect(page.locator('[data-testid="banner-policy"]')).to_contain_text("unreadable")
    page.locator('[data-testid="reset-button"]').click()

    expect(page.locator('[data-testid="banner-success"]')).to_contain_text("reset")
    assert len(admin.resets) == 1
    expect(page.locator('[data-testid="banner-policy"]')).to_have_count(0)


def test_mcp_outage_shows_a_banner_and_blocks_saving(admin):
    admin.state.update(tools=[], stale=[], catalog_error="Could not connect to the MCP server: refused")
    admin.page.goto(admin.url)

    expect(admin.page.locator('[data-testid="banner-catalog"]')).to_contain_text("Could not connect to the MCP server")
    expect(admin.page.locator('[data-testid="save-button"]')).to_be_disabled()


def test_forbidden_users_see_the_restriction_not_the_page(admin, page):
    page.unroute("**/admin/**")

    def deny(route):
        url = route.request.url
        if url.endswith("/admin/chat-tools"):
            route.fulfill(status=200, content_type="text/html", body=PAGE_HTML.read_text())
        elif "/admin/api/" not in url:
            route.continue_()
        else:
            route.fulfill(
                status=403, content_type="application/json",
                body=json.dumps({"error": "Chat tool settings are restricted to editors of the extension's project"}),
            )

    page.route("**/admin/**", deny)
    page.goto(admin.url)

    expect(page.locator('[data-testid="banner-fatal"]')).to_contain_text("restricted to editors of the extension's project")
    expect(page.locator("#admin-layout")).to_be_hidden()


# ----- detail, schema forms, previews -----

def test_detail_shows_description_parameters_and_declared_output(admin):
    page = _open(admin)
    page.evaluate("window.__xss = false")

    expect(page.locator('[data-testid="tool-description"] strong')).to_have_text("get_group_analysis")
    assert page.locator('[data-testid="tool-description"] script').count() == 0  # sanitized markdown
    assert page.evaluate("window.__xss") is False
    rows = page.locator('[data-testid="param-table"] tbody tr')
    names = rows.locator("td.param-name").all_inner_texts()
    assert names == ["feature", "group_by", "bins", "ratio", "flag", "syntax", "features", "filter", "filter.column"]
    first = rows.first.locator("td")
    expect(first.nth(1)).to_have_text("string")
    expect(first.nth(2)).to_have_text("Yes")
    expect(first.nth(4)).to_have_text("Numeric feature")
    expect(rows.nth(2).locator("td").nth(3)).to_have_text("30")
    expect(rows.nth(6).locator("td").nth(1)).to_have_text("array<string> | null")
    expect(page.locator("#detail-pane .pre-block").first).to_contain_text('"group": "A"')


def test_selecting_another_tool_updates_the_detail_panel(admin):
    page = _open(admin)

    _row(page, "get_dataset_head").locator('[data-testid="select-tool"]').click()

    expect(page.locator("#detail-pane .detail-header h2")).to_have_text("Get Dataset Head")
    expect(page.locator('[data-testid="param-table"] td.param-name')).to_have_text(["rows"])
    expect(_row(page, "get_dataset_head")).to_have_attribute("aria-current", "true")


def test_try_it_form_is_built_from_the_schema_with_examples_and_column_hints(admin):
    page = _open(admin)
    expect(page.locator('[data-testid="try-it-form"]')).to_be_visible()
    expect(page.locator("#detail-pane")).to_contain_text("Sample: CDISC pilot ADSL")

    expect(page.locator('[data-testid="arg-feature"]')).to_have_value("AGE")       # from example_args
    expect(page.locator('[data-testid="arg-group_by"]')).to_have_value("ARM")
    expect(page.locator('[data-testid="arg-bins"]')).to_have_value("30")           # schema default
    assert page.locator('[data-testid="arg-flag"]').evaluate("el => el.tagName") == "SELECT"
    assert page.locator('[data-testid="arg-syntax"]').evaluate("el => el.tagName") == "SELECT"
    assert page.locator('[data-testid="arg-filter"]').evaluate("el => el.tagName") == "TEXTAREA"  # JSON fallback
    # column-looking string params get a datalist of the sample's columns
    list_id = page.locator('[data-testid="arg-feature"]').get_attribute("list")
    options = page.locator(f"datalist#{list_id} option").evaluate_all("els => els.map(e => e.value)")
    assert options == ["AGE", "ARM", "BMIBL", "SEX"]
    assert page.locator('[data-testid="arg-bins"]').get_attribute("list") is None


def test_run_sends_typed_args_and_renders_the_output_with_size_and_tokens(admin):
    page = _open(admin)
    page.locator('[data-testid="arg-ratio"]').fill("0.5")
    page.locator('[data-testid="arg-flag"]').select_option("true")
    page.locator('[data-testid="arg-features"]').fill("AGE, BMIBL")
    page.locator('[data-testid="arg-filter"]').fill('{"column": "ARM"}')

    page.locator('[data-testid="run-button"]').click()

    expect(page.locator('[data-testid="output-meta"]')).to_contain_text("123 B", timeout=5_000)
    expect(page.locator('[data-testid="output-meta"]')).to_contain_text("about 30 tokens")
    expect(page.locator('[data-testid="output-meta"]')).to_contain_text("12 ms")
    expect(page.locator('[data-testid="output-body"] th')).to_have_text(["ARM", "AGE"])
    name, body = admin.previews[0]
    assert name == "get_group_analysis"
    assert body["args"] == {
        "feature": "AGE", "group_by": "ARM", "bins": 30, "ratio": 0.5, "flag": True,
        "features": ["AGE", "BMIBL"], "filter": {"column": "ARM"},
    }
    # the raw toggle shows exactly the text the model would receive
    page.locator('[data-testid="raw-toggle"]').check()
    expect(page.locator('[data-testid="output-body"] pre')).to_have_text('[{"ARM": "Placebo", "AGE": 75.2}]')
    page.locator('[data-testid="raw-toggle"]').uncheck()
    expect(page.locator('[data-testid="output-body"] table')).to_be_visible()


def test_run_validates_required_fields_and_json_before_calling_the_server(admin):
    page = _open(admin)
    page.locator('[data-testid="arg-feature"]').fill("")
    page.locator('[data-testid="arg-filter"]').fill("{oops")
    page.locator('[data-testid="arg-bins"]').fill("1.5")

    page.locator('[data-testid="run-button"]').click()

    expect(page.locator(".field-error")).to_have_text([
        "feature is required", "bins must be a whole number", "filter must be valid JSON",
    ])
    assert admin.previews == []


def test_tool_errors_are_shown_with_the_full_text(admin):
    admin.preview_results["get_group_analysis"] = {
        "content": None, "content_text": None, "size_bytes": 0, "approx_tokens": 0, "elapsed_ms": 4,
        "error": "Error calling x. Status code: 404. Response: {\"detail\":\"Feature 'nope' not found\"}",
    }
    page = _open(admin)

    page.locator('[data-testid="run-button"]').click()

    expect(page.locator('[data-testid="output-error"]')).to_contain_text("Feature 'nope' not found", timeout=5_000)


def test_large_outputs_get_a_context_warning(admin):
    admin.preview_results["get_group_analysis"] = {
        "content": [{"a": 1}], "content_text": "[{\"a\": 1}]", "size_bytes": 4_000_000, "approx_tokens": 1_000_000, "elapsed_ms": 900,
    }
    page = _open(admin)

    page.locator('[data-testid="run-button"]').click()

    expect(page.locator('[data-testid="output-meta"]')).to_contain_text("3.8 MB", timeout=5_000)
    expect(page.locator(".output-warning")).to_contain_text("Large output")


def test_output_values_are_rendered_as_text(admin):
    admin.preview_results["get_group_analysis"] = {
        "content": [{"note": INJECTION}], "content_text": INJECTION, "size_bytes": 10, "approx_tokens": 2, "elapsed_ms": 1,
    }
    page = _open(admin)
    page.evaluate("window.__xss = false")

    page.locator('[data-testid="run-button"]').click()

    expect(page.locator('[data-testid="output-body"] td')).to_have_text(INJECTION, timeout=5_000)
    assert page.locator('[data-testid="output-body"] img').count() == 0
    assert page.evaluate("window.__xss") is False


def test_last_arguments_are_remembered_for_the_session(admin):
    page = _open(admin)
    page.locator('[data-testid="arg-feature"]').fill("BMIBL")
    page.locator('[data-testid="run-button"]').click()
    expect(page.locator('[data-testid="output-meta"]')).to_be_visible(timeout=5_000)

    page.reload()

    expect(page.locator('[data-testid="arg-feature"]')).to_have_value("BMIBL", timeout=10_000)


def test_args_survive_switching_between_tools(admin):
    page = _open(admin)
    page.locator('[data-testid="arg-feature"]').fill("SEX")

    _row(page, "get_dataset_head").locator('[data-testid="select-tool"]').click()
    _row(page, "get_group_analysis").locator('[data-testid="select-tool"]').click()

    expect(page.locator('[data-testid="arg-feature"]')).to_have_value("SEX")


def test_preview_sample_failure_disables_running_but_not_settings(admin):
    admin.sample_error = True
    page = _open(admin)

    expect(page.locator('[data-testid="banner-sample"]')).to_contain_text("Previews are unavailable", timeout=5_000)
    expect(page.locator('[data-testid="run-button"]')).to_be_disabled()
    _set_mode(page, "get_group_analysis", "disabled")
    expect(page.locator('[data-testid="save-button"]')).to_be_enabled()


def test_api_calls_are_relative_to_the_app_root_not_the_admin_url(admin):
    seen = []
    admin.page.on("request", lambda request: seen.append(urlparse(request.url).path) if "/admin/api/" in request.url else None)

    _open(admin)

    assert seen[0] == "/admin/api/chat-tools"
    assert not any("/admin/chat-tools/admin" in path for path in seen)
