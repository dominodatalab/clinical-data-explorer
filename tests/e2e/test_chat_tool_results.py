"""Chat result cards for tools whose output is hidden from the AI."""
import json

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
