"""Chat result cards for tools whose output is hidden from the AI."""
import json
from pathlib import Path

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

HEAD_CARD = {
    "tool_call_id": "call-1",
    "tool_name": "get_dataset_head_dataset_head_get",
    "title": "Get Dataset Head",
    "args": {"rows": 2},
    "content": [
        {"subject_id": "SUBJ-001", "treatment": "Placebo", "note": "<img src=x onerror=\"window.__xss=true\">"},
        {"subject_id": "SUBJ-002", "treatment": "DrugA", "note": None},
    ],
    "truncated": False,
    "size_bytes": 200,
}
STATS_CARD = {
    "tool_call_id": "call-2",
    "tool_name": "get_column_stats_table_column_stats__column__get",
    "title": "Get Column Stats",
    "args": {},
    "content": {"unique_count": 3, "mean": 41.5},
    "truncated": False,
    "size_bytes": 40,
}
ERROR_CARD = {
    "tool_call_id": "call-3",
    "tool_name": "get_feature_stats_feature_stats_get",
    "title": "Get Feature Stats",
    "args": {"features": "nope"},
    "is_error": True,
    "error": "Error calling x. Status code: 404. Response: {\"detail\":\"Feature 'nope' not found\"}",
}
BADGE_TOOLTIP = "The assistant ran this tool but cannot see its output. Only you can see this."
REAL_OUTPUTS = json.loads(
    (Path(__file__).resolve().parents[1] / "fixtures" / "tool_outputs.json").read_text()
)["outputs"]


def _card(name, tool_call_id="call-9", **extra):
    """A result card carrying a tool's real output from tests/fixtures/tool_outputs.json."""
    entry = REAL_OUTPUTS[name]
    return {
        "tool_call_id": tool_call_id,
        "tool_name": entry["tool"],
        "title": name,
        "args": entry["args"],
        "content": entry["output"],
        "truncated": False,
        "size_bytes": 500,
        **extra,
    }


def _open_chat(page, url, overrides):
    responses = install_api_routes(page, {"chat/status": ok({"configured": True}), **overrides})
    page.goto(url)
    load_local_dataset(page)
    page.locator('[data-testid="tab-chat"]').click()
    expect(page.locator('[data-testid="chat-input"]')).to_be_visible(timeout=5_000)
    return responses


def _send(page, text):
    page.locator('[data-testid="chat-input"]').fill(text)
    page.locator("#send-button").click()


def test_hidden_result_card_renders_before_the_agent_bubble(page, chat_ui_static_url):
    _open_chat(page, chat_ui_static_url, {
        "chat/history": ok({"messages": []}),
        "chat": ok({
            "response": "The result is displayed above.",
            "charts": [],
            "user_only_results": [HEAD_CARD, STATS_CARD],
        }),
    })

    _send(page, "show me the first rows")

    cards = page.locator('[data-testid="tool-result-card"]')
    expect(cards).to_have_count(2, timeout=5_000)
    expect(cards.first.locator(".tool-result-title")).to_have_text("Get Dataset Head")
    expect(cards.first.locator('[data-testid="tool-result-badge"]')).to_contain_text("Hidden from AI")
    expect(cards.first.locator('[data-testid="tool-result-badge"]')).to_have_attribute("aria-label", BADGE_TOOLTIP)
    expect(cards.first.locator('[data-testid="tool-result-badge"]')).to_have_attribute("title", BADGE_TOOLTIP)
    expect(cards.first.locator("table th")).to_have_text(["subject_id", "treatment", "note"])
    expect(cards.first.locator("tbody tr")).to_have_count(2)
    expect(cards.nth(1).locator("table.tool-output-kv")).to_contain_text("unique_count")

    # Order in the chat log: user prompt, both cards, then the agent's reply.
    order = page.evaluate(
        """() => Array.from(document.querySelectorAll('#chat-box > .message'))
            .map(el => el.dataset.sender || (el.classList.contains('tool-result-message') ? 'tool_result' : 'other'))
            .filter(s => s !== 'system')"""
    )
    assert order == ["user", "tool_result", "tool_result", "agent"]


def test_card_arguments_are_collapsible_and_values_render_as_text(page, chat_ui_static_url):
    _open_chat(page, chat_ui_static_url, {
        "chat/history": ok({"messages": []}),
        "chat": ok({"response": "ok", "charts": [], "user_only_results": [HEAD_CARD]}),
    })
    page.evaluate("window.__xss = false")

    _send(page, "head")

    card = page.locator('[data-testid="tool-result-card"]').first
    expect(card).to_be_visible(timeout=5_000)
    args = card.locator("details.tool-result-args")
    expect(args).not_to_have_attribute("open", "")
    args.locator("summary").click()
    expect(args.locator("pre")).to_contain_text('"rows": 2')
    # data values are text, never markup
    expect(card.locator("td").filter(has_text="onerror")).to_have_count(1)
    assert card.locator("img").count() == 0
    assert page.evaluate("window.__xss") is False


def test_failed_hidden_call_card_shows_the_real_error(page, chat_ui_static_url):
    _open_chat(page, chat_ui_static_url, {
        "chat/history": ok({"messages": []}),
        "chat": ok({"response": "That failed.", "charts": [], "user_only_results": [ERROR_CARD]}),
    })

    _send(page, "stats")

    card = page.locator('[data-testid="tool-result-card"]')
    expect(card).to_have_count(1, timeout=5_000)
    expect(card.locator(".tool-result-failed")).to_have_text("Tool failed")
    expect(card.locator(".tool-result-error-text")).to_contain_text("Feature 'nope' not found")
    expect(card.locator('[data-testid="tool-result-badge"]')).to_be_visible()


def test_truncated_result_shows_a_notice(page, chat_ui_static_url):
    truncated = {
        **HEAD_CARD,
        "truncated": True,
        "size_bytes": 900_000,
        "content": {"preview": [{"a": 1}], "truncated": True, "size_bytes": 900_000},
    }
    _open_chat(page, chat_ui_static_url, {
        "chat/history": ok({"messages": []}),
        "chat": ok({"response": "ok", "charts": [], "user_only_results": [truncated]}),
    })

    _send(page, "big")

    card = page.locator('[data-testid="tool-result-card"]')
    expect(card.locator(".tool-output-note")).to_contain_text("too large to keep in full (878.9 KB)", timeout=5_000)
    expect(card.locator("tbody tr")).to_have_count(1)


def test_policy_notice_is_shown_as_a_system_message(page, chat_ui_static_url):
    _open_chat(page, chat_ui_static_url, {
        "chat/history": ok({"messages": []}),
        "chat": ok({
            "response": "Without tools.",
            "charts": [],
            "user_only_results": [],
            "notice": "Data tools are temporarily unavailable: the chat tool settings could not be loaded.",
        }),
    })

    _send(page, "hi")

    expect(
        page.locator('[data-testid="chat-message"][data-sender="system"]').filter(has_text="Data tools are temporarily unavailable")
    ).to_be_visible(timeout=5_000)
    expect(page.locator('[data-testid="chat-message"][data-sender="agent"]').last).to_contain_text("Without tools.")


def test_history_reload_shows_cards_between_prompt_and_reply(page, chat_ui_static_url):
    history = [
        {"sender": "user", "text": "show the head"},
        {"sender": "tool_result", **HEAD_CARD},
        {"sender": "agent", "text": "It is displayed above.", "charts": []},
    ]
    install_api_routes(page, {"chat/status": ok({"configured": True}), "chat/history": ok({"messages": history})})
    page.goto(chat_ui_static_url)

    page.locator('[data-testid="tab-chat"]').click()

    card = page.locator('[data-testid="tool-result-card"]')
    expect(card).to_have_count(1, timeout=5_000)
    expect(card.locator('[data-testid="tool-result-badge"]')).to_contain_text("Hidden from AI")
    order = page.evaluate(
        """() => Array.from(document.querySelectorAll('#chat-box > .message'))
            .map(el => el.dataset.sender || (el.classList.contains('tool-result-message') ? 'tool_result' : 'other'))"""
    )
    assert order == ["user", "tool_result", "agent"]


def test_settings_link_is_shown_only_to_admins(page, chat_ui_static_url):
    _open_chat(page, chat_ui_static_url, {
        "chat/history": ok({"messages": []}),
        "admin/api/chat-tools/me": ok({"is_admin": True}),
    })

    link = page.locator('[data-testid="chat-tool-settings-link"]')
    expect(link).to_be_visible(timeout=5_000)
    expect(link).to_have_text("Chat tool settings")
    # built from the same base URL as the API, so it works inside the Domino iframe
    expect(link).to_have_attribute("href", "/admin/chat-tools")


def test_settings_link_stays_hidden_for_non_admins_and_on_errors(page, chat_ui_static_url):
    _open_chat(page, chat_ui_static_url, {
        "chat/history": ok({"messages": []}),
        "admin/api/chat-tools/me": ok({"is_admin": False}),
    })
    expect(page.locator('[data-testid="chat-tool-settings-link"]')).to_be_hidden()

    page.unroute("**/*")
    install_api_routes(page, {
        "chat/status": ok({"configured": True}),
        "chat/history": ok({"messages": []}),
        "admin/api/chat-tools/me": (500, {"error": "boom"}),
    })
    page.reload()
    page.locator('[data-testid="tab-chat"]').click()
    expect(page.locator('[data-testid="chat-tool-settings-link"]')).to_be_hidden()


# ----- charts in result cards -----

def test_chartable_output_is_drawn_as_a_chart_first_with_the_table_one_click_away(page, chat_ui_static_url):
    card_data = _card("get_feature_comparison_feature_comparison_get")
    _open_chat(page, chat_ui_static_url, {
        "chat/history": ok({"messages": []}),
        "chat": ok({"response": "Displayed above.", "charts": [], "user_only_results": [card_data]}),
    })

    _send(page, "compare height and weight")

    card = page.locator('[data-testid="tool-result-card"]')
    chart = card.locator('.tool-output-chart[data-chart-type="scatter"]')
    expect(chart.locator(".highcharts-container")).to_be_visible(timeout=5_000)
    expect(chart.locator(".highcharts-xaxis .highcharts-axis-title")).to_have_text("HEIGHTBL")
    expect(chart.locator(".highcharts-yaxis .highcharts-axis-title")).to_have_text("WEIGHTBL")
    expect(chart.locator(".highcharts-markers .highcharts-point")).to_have_count(8)
    expect(card.locator(".tool-output-summary-strip")).to_contain_text("HEIGHTBL")
    expect(card.locator(".tool-output-table-view")).to_be_hidden()

    card.get_by_role("button", name="Table").click()

    expect(chart).to_be_hidden()
    expect(card.locator(".tool-output-table-view th")).to_have_text(["HEIGHTBL", "WEIGHTBL"])
    expect(card.locator(".tool-output-table-view tbody tr")).to_have_count(8)
    expect(card.get_by_role("button", name="Table")).to_have_attribute("aria-pressed", "true")


def test_chart_card_fills_the_row_like_an_agent_chart(page, chat_ui_static_url):
    card_data = _card("get_feature_comparison_feature_comparison_get")
    _open_chat(page, chat_ui_static_url, {
        "chat/history": ok({"messages": []}),
        "chat": ok({"response": "Displayed above.", "charts": [], "user_only_results": [card_data]}),
    })

    _send(page, "compare height and weight")

    card = page.locator('[data-testid="tool-result-card"]')
    expect(card.locator(".highcharts-container")).to_be_visible(timeout=5_000)
    card_width = card.evaluate("el => el.getBoundingClientRect().width")
    row_width = page.locator("#chat-box").evaluate("el => el.clientWidth")
    chart_width = card.locator(".tool-output-chart").evaluate("el => el.getBoundingClientRect().width")
    # Capped at 92% of the row by max-width; the old shrink-to-fit card was a fraction of that.
    assert card_width > row_width * 0.8, (card_width, row_width)
    assert chart_width > row_width * 0.7, (chart_width, row_width)


def test_chart_labels_show_data_as_written_and_never_as_markup(page, chat_ui_static_url):
    labels = ["<65", "<b>bold</b>", "<img src=x onerror=\"window.__xss=true\">", "A & B"]
    card_data = {
        **HEAD_CARD,
        "title": "Get Bar Chart Data",
        "content": {"category_column": "AGEGR1", "chart_data": [{"label": label, "value": i + 1} for i, label in enumerate(labels)]},
    }
    _open_chat(page, chat_ui_static_url, {
        "chat/history": ok({"messages": []}),
        "chat": ok({"response": "ok", "charts": [], "user_only_results": [card_data]}),
    })
    page.evaluate("window.__xss = false")

    _send(page, "bars")

    chart = page.locator('[data-testid="tool-result-card"] .tool-output-chart[data-chart-type="bar"]')
    expect(chart.locator(".highcharts-container")).to_be_visible(timeout=5_000)
    shown = chart.locator(".highcharts-xaxis-labels text").evaluate_all("els => els.map(el => el.textContent)")
    assert shown[:2] == ["<65", "<b>bold</b>"]
    assert shown[2].startswith("<img src=x")  # long labels may be shortened with an ellipsis
    assert shown[3] == "A & B"
    assert chart.locator("img, b").count() == 0
    assert page.evaluate("window.__xss") is False


def test_truncated_chart_says_it_shows_only_the_part_kept(page, chat_ui_static_url):
    preview = _card("get_histogram_data_chart_histogram_post")["content"]
    card_data = _card(
        "get_histogram_data_chart_histogram_post",
        truncated=True,
        size_bytes=900_000,
        content={"preview": preview, "truncated": True, "size_bytes": 900_000},
    )
    _open_chat(page, chat_ui_static_url, {
        "chat/history": ok({"messages": []}),
        "chat": ok({"response": "ok", "charts": [], "user_only_results": [card_data]}),
    })

    _send(page, "histogram")

    card = page.locator('[data-testid="tool-result-card"]')
    expect(card.locator('.tool-output-chart[data-chart-type="histogram"] .highcharts-container')).to_be_visible(timeout=5_000)
    expect(card.locator(".tool-result-body > .tool-output-note")).to_have_text(
        "This output was too large to keep in full (878.9 KB). Showing the first part. Charts are drawn from that part only."
    )


def test_chart_from_history_fills_the_card_once_the_chat_tab_is_shown(page, chat_ui_static_url):
    """History loads while the chat tab is hidden; the chart must resize when it becomes visible."""
    history = [
        {"sender": "user", "text": "correlations"},
        {"sender": "tool_result", **_card("get_correlation_matrix_correlation_matrix_get")},
    ]
    install_api_routes(page, {"chat/status": ok({"configured": True}), "chat/history": ok({"messages": history})})
    page.goto(chat_ui_static_url)
    # The chart is drawn while the chat tab is still hidden, so its box has no width yet.
    page.wait_for_function(
        """() => {
            const box = document.querySelector('.tool-output-chart[data-chart-type="heatmap"]');
            return box && box.querySelector('svg.highcharts-root') && box.clientWidth === 0;
        }""",
        timeout=5_000,
    )

    page.locator('[data-testid="tab-chat"]').click()

    chart = page.locator('[data-testid="tool-result-card"] .tool-output-chart[data-chart-type="heatmap"]')
    expect(chart.locator(".highcharts-container")).to_be_visible(timeout=5_000)
    page.wait_for_function(
        """() => {
            const box = document.querySelector('.tool-output-chart[data-chart-type="heatmap"]');
            const svg = box && box.querySelector('svg.highcharts-root');
            return svg && box.clientWidth > 0 && Math.abs(svg.getBoundingClientRect().width - box.clientWidth) <= 2;
        }""",
        timeout=5_000,
    )

