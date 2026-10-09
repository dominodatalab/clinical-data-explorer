"""Chat bubble rendering: agent Markdown, sanitizing, and literal error bubbles."""

import pytest

pytest.importorskip("playwright.sync_api")

from playwright.sync_api import expect  # noqa: E402

from .static_ui_fixtures import chat_ui_static_url  # noqa: E402,F401

pytestmark = pytest.mark.mocked_integration


def _render(page, url, text):
    page.goto(url)
    expect(page.locator("#chat-box")).to_be_attached(timeout=15_000)
    return page.evaluate(
        """async (text) => {
            const { renderMarkdown } = await import('/modules/markdown.js');
            return renderMarkdown(text);
        }""",
        text,
    )


def test_placeholder_in_angle_brackets_is_not_swallowed(page, chat_ui_static_url):
    html = _render(page, chat_ui_static_url, "Use <column_name> and a < b > c")
    assert "&lt;column_name&gt;" in html
    assert "a &lt; b &gt; c" in html


def test_links_are_hardened(page, chat_ui_static_url):
    html = _render(
        page,
        chat_ui_static_url,
        "[web](https://example.com) [mail](mailto:a@b.co) [rel](/internal)",
    )
    assert 'href="https://example.com" target="_blank" rel="noopener noreferrer"' in html
    assert 'href="mailto:a@b.co"' in html
    assert 'href="/internal"' not in html


@pytest.mark.parametrize("payload", [
    "<img src=x onerror=\"window.__xss=true\">",
    "<svg onload=\"window.__xss=true\"></svg>",
    "[x](data:text/html,<script>window.__xss=true</script>)",
])
def test_xss_payloads_render_inert(page, chat_ui_static_url, payload):
    page.goto(chat_ui_static_url)
    expect(page.locator("#chat-box")).to_be_attached(timeout=15_000)
    result = page.evaluate(
        """async (payload) => {
            window.__xss = false;
            const { displayMessage } = await import('/modules/chat.js');
            displayMessage(payload, 'agent');
            const box = document.querySelector('#chat-box .agent-message:last-of-type');
            return {
                active: box.querySelectorAll('img, svg, script, iframe').length,
                dataHref: box.querySelectorAll('a[href^="data:"]').length,
                xss: window.__xss,
            };
        }""",
        payload,
    )
    assert result == {"active": 0, "dataHref": 0, "xss": False}


def test_falls_back_to_escaped_text_when_libraries_are_missing(page, chat_ui_static_url):
    page.goto(chat_ui_static_url)
    expect(page.locator("#chat-box")).to_be_attached(timeout=15_000)
    html = page.evaluate(
        """async () => {
            window.marked = undefined;
            window.DOMPurify = undefined;
            const { renderMarkdown } = await import('/modules/markdown.js');
            return renderMarkdown('**a** <b>x</b>\\nline2');
        }"""
    )
    assert html == "**a** &lt;b&gt;x&lt;/b&gt;<br>line2"


def test_error_and_system_bubbles_are_literal_and_selectable(page, chat_ui_static_url):
    """Errors are no longer `.agent-message`; `data-sender` is the stable hook."""
    page.goto(chat_ui_static_url)
    expect(page.locator("#chat-box")).to_be_attached(timeout=15_000)
    page.evaluate(
        """async () => {
            const { displayMessage } = await import('/modules/chat.js');
            displayMessage('Error: **boom** <b>x</b>', 'error');
            displayMessage('ok **bold**', 'agent');
        }"""
    )
    error_bubble = page.locator('[data-testid="chat-message"][data-sender="error"]').last
    expect(error_bubble).to_have_text("Error: **boom** <b>x</b>")
    expect(error_bubble).to_have_class("message error-message")
    expect(page.locator('[data-testid="chat-message"][data-sender="agent"] strong').last).to_have_text("bold")
