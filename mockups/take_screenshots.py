"""One-off screenshot script for the redesigned header bar.

Serves chat_ui/ as static files (same pattern as tests/e2e/test_smoke.py's
chat_ui_static_url fixture), opens it in a headless Chromium via Playwright,
and uses page.evaluate() to puppet the DOM into the four states we care
about. Backend isn't needed — initial fetch failures (loadDatasets etc.)
log to console but don't block render.

States captured (all clip to the tab-navigation row only):
  01-empty.png            — initial load, ghost-CTA file pill
  02-ungoverned.png       — dataset loaded, "Not Governed" chip, switch off
  03-governed-off.png     — dataset loaded, green badge + Finding, switch off
  04-governed-on.png      — same as 03 with Friendly Names switch on

Run:
    .venv/bin/python mockups/take_screenshots.py

Output: mockups/screenshots/*.png
"""
from __future__ import annotations

import functools
import socket
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from playwright.sync_api import sync_playwright

REPO_ROOT = Path(__file__).resolve().parent.parent
CHAT_UI = REPO_ROOT / "chat_ui"
OUT_DIR = REPO_ROOT / "mockups" / "screenshots"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _serve_chat_ui(port: int):
    handler = functools.partial(SimpleHTTPRequestHandler, directory=str(CHAT_UI))
    server = ThreadingHTTPServer(("127.0.0.1", port), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


# DOM-puppeteering snippet. We poke the same fields that script.js /
# modules/governance.js / modules/column-labels.js would normally set when a
# dataset loads — no backend round-trip.
# Each state is a dict of "what should be visible / set" so it's easy to skim.
_SET_STATE_JS = r"""
({ state }) => {
    const browse = document.getElementById('browse-files-button');
    const label = document.getElementById('current-dataset-label');
    const govIndicator = document.getElementById('governance-indicator');
    const govBadge = document.getElementById('governance-badge');
    const govBadgePrefix = document.getElementById('governance-badge-prefix');
    const govBadgeName = document.getElementById('governance-badge-bundle-name');
    const govCaret = document.getElementById('governance-badge-caret');
    const createFinding = document.getElementById('create-finding-btn');
    const labelToggle = document.getElementById('label-toggle-container');
    const useLabelsCheckbox = document.getElementById('use-labels-checkbox');

    // Reset all the toggleable bits to their initial-HTML state so consecutive
    // calls don't bleed visual state across screenshots.
    browse.classList.add('empty');
    label.textContent = 'Choose a data file…';
    govIndicator.style.display = 'none';
    govIndicator.classList.remove('no-governance');
    govBadgePrefix.textContent = 'Governed';
    govBadgeName.textContent = '';
    govBadgeName.style.display = '';
    govCaret.style.display = 'none';
    createFinding.style.display = 'none';
    labelToggle.style.display = 'none';
    useLabelsCheckbox.checked = false;

    if (state === 'empty') {
        // nothing more to do — initial state
    } else if (state === 'ungoverned') {
        browse.classList.remove('empty');
        label.textContent = 'long-ass-dataset-name-goes-here.csv';
        govIndicator.style.display = '';
        govIndicator.classList.add('no-governance');
        govBadgePrefix.textContent = 'Not Governed';
        govBadgeName.style.display = 'none';
        labelToggle.style.display = 'flex';
    } else if (state === 'governed-off' || state === 'governed-on') {
        browse.classList.remove('empty');
        label.textContent = 'adsl.sas7bdat';
        govIndicator.style.display = '';
        govBadgePrefix.textContent = 'Governed';
        govBadgeName.textContent = 'CDISC Pilot Bundle';
        govBadgeName.style.display = '';
        govCaret.style.display = '';
        createFinding.style.display = '';
        labelToggle.style.display = 'flex';
        if (state === 'governed-on') {
            useLabelsCheckbox.checked = true;
        }
    }
}
"""


STATES = [
    ("01-empty.png", "empty", "Initial load — ghost-CTA file pill, no governance, no toggle."),
    ("02-ungoverned.png", "ungoverned", "Dataset loaded, ungoverned — quiet pill + neutral 'Not Governed' chip + switch (off)."),
    ("03-governed-off.png", "governed-off", "Governed dataset, Friendly Names off — green badge + Finding button + switch (off)."),
    ("04-governed-on.png", "governed-on", "Governed dataset, Friendly Names on — switch slid right, track purple."),
]


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    port = _free_port()
    server, thread = _serve_chat_ui(port)
    url = f"http://127.0.0.1:{port}/index.html"
    print(f"Serving {CHAT_UI} at {url}")

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            context = browser.new_context(
                viewport={"width": 1400, "height": 600},
                device_scale_factor=2,  # retina-quality screenshots
            )
            page = context.new_page()
            page.goto(url)
            # Wait for the header to be present so we don't race the deferred
            # script.js module evaluation.
            page.wait_for_selector("#browse-files-button", state="attached")
            # Give deferred-module script.js a beat to finish wiring listeners
            # / firing the initial loadDatasets fetch (which will fail without
            # a backend — that's expected and harmless to the visual).
            page.wait_for_timeout(400)

            tab_nav = page.locator(".tab-navigation")

            for filename, state, caption in STATES:
                page.evaluate(_SET_STATE_JS, {"state": state})
                page.wait_for_timeout(150)  # let the CSS transitions settle
                out = OUT_DIR / filename
                tab_nav.screenshot(path=str(out))
                print(f"  · {filename}  — {caption}")

            browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    print(f"\nWrote {len(STATES)} screenshot(s) to {OUT_DIR}")


if __name__ == "__main__":
    main()
