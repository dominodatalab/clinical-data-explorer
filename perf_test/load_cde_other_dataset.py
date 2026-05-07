#!/usr/bin/env python3
"""Drive CDE through Domino UI login for practitioner quick-start projects.

The purpose of this script is to exercise the scaling of the app by loading large dataset files through the UI.
Review the sizing environment variables before running. Run with appropriately large datasets.

Setup the requirements and then run the test. I recommend watching the deployment through k8s and watching the logs of
the individual pods' run containers.

Requirements for the default workflow:
  1. Create the clinical-data-explorer extension and name it "CDE"
  2. Create users named pract1 - pract10 with the same password for each of them
  3. Create an "other" dataset for each user in their quick-start project
  4. Upload a file to each dataset

Default workflow:
  1. Log in as pract1..pract10 through the Domino UI.
  2. Open each user's quick-start project.
  3. Follow the CDE project side-nav extension link.
  4. Click Browse Files.
  5. Select the "other" dataset source.
  6. Select the largest listed file.
  7. Click Load File and record the /dataset/load response.

Usage:
  uv run ./load_cde_other_dataset.py --allow-load-failures --password <pract users' password>
"""

from __future__ import annotations

import argparse
from datetime import datetime
import json
import os
import re
import sys
import time
from pathlib import Path
from urllib.parse import urljoin

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright


DEFAULT_BASE_URL = "https://nio2tst124524.engineering-dev.domino.tech/"
DEFAULT_USERS = [f"pract{i}" for i in range(1, 11)]
DEFAULT_PASSWORD_ENV = "PRACT_PASSWORD"
LOAD_TIMEOUT_MS = 300_000
DEFAULT_SCREENSHOT_DIR = "perf_test/screenshots"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Log in through Domino UI and load the largest file from a CDE dataset source.",
    )
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--users", nargs="+", default=DEFAULT_USERS)
    parser.add_argument("--password-env", default=DEFAULT_PASSWORD_ENV)
    parser.add_argument(
        "--password",
        default=None,
        help="Password for all users. Prefer setting PRACT_PASSWORD instead.",
    )
    parser.add_argument("--source-label", default="other")
    parser.add_argument("--output", default=None, help="Optional path for JSON results.")
    parser.add_argument(
        "--screenshot-dir",
        default=DEFAULT_SCREENSHOT_DIR,
        help="Directory for screenshots captured on automation or load failures.",
    )
    parser.add_argument("--headed", action="store_true", help="Run browser headed.")
    parser.add_argument(
        "--no-click-load",
        action="store_true",
        help="Stop after selecting the largest file; do not click Load File.",
    )
    parser.add_argument(
        "--allow-load-failures",
        action="store_true",
        help="Exit 0 even if /dataset/load returns an error.",
    )
    return parser.parse_args()


def bytes_from_size_text(text: str) -> float:
    match = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*(B|KB|MB|GB)", text)
    if not match:
        return -1
    value = float(match.group(1))
    unit = match.group(2)
    return value * {"B": 1, "KB": 1024, "MB": 1024**2, "GB": 1024**3}[unit]


def safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip("-") or "screenshot"


def capture_screenshot(page, screenshot_dir: str, user: str, reason: str) -> str | None:
    timestamp = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    filename = f"{safe_name(user)}-{timestamp}-{safe_name(reason)}.png"
    path = Path(screenshot_dir) / filename
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(path), full_page=True)
        return str(path)
    except Exception as exc:
        print(f"[{user}] could not capture screenshot for {reason}: {exc}", flush=True)
        return None


def login(page, base_url: str, user: str, password: str) -> None:
    page.goto(base_url, wait_until="domcontentloaded", timeout=60_000)
    page.locator("#username").wait_for(timeout=30_000)
    page.fill("#username", user)
    page.fill("#password", password)
    page.click("#kc-login")
    page.wait_for_url("**/home**", timeout=45_000)
    page.get_by_text("Welcome,", exact=False).wait_for(timeout=30_000)


def open_quick_start(page, base_url: str) -> tuple[str | None, str]:
    quick_start = page.get_by_role("link", name="quick-start").first
    quick_start.wait_for(timeout=30_000)
    quick_start_href = quick_start.get_attribute("href")
    quick_start.click()
    page.wait_for_url("**/quick-start/overview", timeout=45_000)

    cde_link = page.locator('a[aria-label="Extension link for CDE"]').first
    cde_link.wait_for(timeout=45_000)
    cde_href = cde_link.get_attribute("href")
    if not cde_href:
        raise RuntimeError("CDE side-nav extension link had no href")
    return quick_start_href, urljoin(base_url, cde_href)


def authorize_extension_if_needed(page) -> bool:
    try:
        page.get_by_text("Permissions request").wait_for(timeout=5_000)
    except PlaywrightTimeoutError:
        return False

    try:
        page.get_by_label("Remember this decision for 30 days").check(timeout=5_000)
    except Exception:
        page.locator('input[type="checkbox"]').first.check(timeout=5_000)

    page.get_by_role("button", name="Authorize").click(timeout=10_000)
    try:
        page.wait_for_load_state("domcontentloaded", timeout=60_000)
    except PlaywrightTimeoutError:
        pass
    return True


def open_cde_from_sidebar_link(page, cde_url: str) -> str:
    page.goto(cde_url, wait_until="domcontentloaded", timeout=60_000)
    authorize_extension_if_needed(page)
    page.wait_for_url("**/apps/**", timeout=60_000)
    page.locator('[data-testid="browse-files-button"]').wait_for(timeout=60_000)
    return page.url


def open_file_browser_by_button(page) -> None:
    # CDE project mode auto-opens the picker. Close and reopen via the requested
    # Browse Files button so this script exercises that exact UI path.
    try:
        if page.locator("#file-browser-modal-overlay.visible").is_visible(timeout=2_000):
            page.locator('[data-testid="fb-cancel-btn"]').click(timeout=5_000)
            page.wait_for_function(
                "() => !document.querySelector('#file-browser-modal-overlay')?.classList.contains('visible')",
                timeout=10_000,
            )
    except Exception:
        pass

    page.locator('[data-testid="browse-files-button"]').click(timeout=15_000)
    page.locator('[data-testid="fb-source-select"]').wait_for(timeout=30_000)


def select_source_and_biggest_file(page, source_label: str) -> dict:
    source = page.locator('[data-testid="fb-source-select"]')
    page.wait_for_function(
        """(label) => [...document.querySelectorAll('#fb-source-select option')]
            .some((option) => option.textContent.trim() === label)""",
        arg=source_label,
        timeout=60_000,
    )
    source.select_option(label=source_label)
    page.wait_for_function(
        """(label) => document.querySelector('#fb-source-select')
            ?.selectedOptions[0]?.textContent.trim() === label""",
        arg=source_label,
        timeout=15_000,
    )
    page.wait_for_function(
        """() => document.querySelectorAll('[data-testid="fb-file-item"]').length > 0
            || /No files|Failed/.test(document.querySelector('#fb-file-list')?.innerText || '')""",
        timeout=90_000,
    )

    files = page.locator('[data-testid="fb-file-item"]').evaluate_all(
        """(els) => els.map((el, index) => ({
            index,
            text: el.innerText,
            name: el.getAttribute('data-fb-name'),
        }))"""
    )
    if not files:
        file_list = page.locator("#fb-file-list").inner_text(timeout=5_000)
        raise RuntimeError(f"No files appeared for source {source_label!r}: {file_list}")

    for file in files:
        file["parsedBytes"] = bytes_from_size_text(file["text"])
        lines = file["text"].split("\n")
        file["sizeLabel"] = lines[-1].strip() if lines else ""

    biggest = max(files, key=lambda file: file["parsedBytes"])
    page.locator('[data-testid="fb-file-item"]').nth(biggest["index"]).click(timeout=15_000)
    page.wait_for_function(
        """() => !document.querySelector('[data-testid="fb-load-btn"]')?.disabled""",
        timeout=10_000,
    )

    return {
        "source": source.evaluate(
            """(select) => ({
                value: select.value,
                text: select.selectedOptions[0] ? select.selectedOptions[0].textContent : '',
            })"""
        ),
        "snapshot": page.locator("#fb-snapshot-select").evaluate(
            """(select) => ({
                value: select.value,
                text: select.selectedOptions[0] ? select.selectedOptions[0].textContent : '',
            })"""
        ),
        "files": files,
        "selected": page.locator("#fb-selected-name").inner_text(timeout=10_000),
        "biggest": biggest,
    }


def click_load_and_wait(page) -> dict:
    started = time.monotonic()
    with page.expect_response(
        lambda response: "/dataset/load" in response.url and response.request.method == "POST",
        timeout=LOAD_TIMEOUT_MS,
    ) as response_context:
        page.locator('[data-testid="fb-load-btn"]').click(timeout=15_000)

    response = response_context.value
    elapsed = round(time.monotonic() - started, 1)
    try:
        body_text = response.text()
    except Exception as exc:
        body_text = f"<could not read response body: {exc}>"

    try:
        body_json = json.loads(body_text)
    except Exception:
        body_json = None

    page.wait_for_timeout(2_000)
    return {
        "status": response.status,
        "ok": response.ok,
        "elapsedSeconds": elapsed,
        "bodyText": body_text[:2000],
        "bodyJson": body_json,
        "currentDatasetLabel": page.locator("#current-dataset-label").inner_text(timeout=5_000),
        "visibleBodyStart": (page.locator("body").inner_text(timeout=5_000) or "")[:2000],
    }


def run_for_user(browser, args: argparse.Namespace, user: str, password: str) -> dict:
    started = time.monotonic()
    context = browser.new_context(ignore_https_errors=True, viewport={"width": 1440, "height": 1000})
    page = context.new_page()
    http_errors: list[str] = []

    def capture_http_error(response) -> None:
        if response.status < 400:
            return
        if "login-status-iframe" in response.url:
            return
        if args.base_url.rstrip("/") not in response.url:
            return
        http_errors.append(f"HTTP {response.status} {response.url}")

    page.on("response", capture_http_error)

    try:
        print(f"[{user}] login", flush=True)
        login(page, args.base_url, user, password)

        print(f"[{user}] open quick-start", flush=True)
        quick_start_href, cde_url = open_quick_start(page, args.base_url)

        print(f"[{user}] open CDE extension", flush=True)
        final_cde_url = open_cde_from_sidebar_link(page, cde_url)

        print(f"[{user}] click Browse Files", flush=True)
        open_file_browser_by_button(page)

        print(f"[{user}] select {args.source_label} and biggest file", flush=True)
        selection = select_source_and_biggest_file(page, args.source_label)

        load = None
        status = "selected"
        if not args.no_click_load:
            print(
                f"[{user}] click Load File for {selection['selected']} "
                f"({selection['biggest']['sizeLabel']})",
                flush=True,
            )
            load = click_load_and_wait(page)
            status = (
                "loaded"
                if load["ok"] and load.get("bodyJson") and not load["bodyJson"].get("error")
                else "load_failed"
            )
            body_json = load.get("bodyJson") or {}
            summary = (
                body_json.get("error")
                or body_json.get("message")
                or load["bodyText"][:180].replace("\n", " ")
            )
            print(
                f"[{user}] load response HTTP {load['status']} "
                f"after {load['elapsedSeconds']}s: {summary}",
                flush=True,
            )
            if status == "load_failed":
                load["screenshot"] = capture_screenshot(
                    page,
                    args.screenshot_dir,
                    user,
                    f"load-failed-http-{load['status']}",
                )
        else:
            print(f"[{user}] selected {selection['selected']}", flush=True)

        return {
            "user": user,
            "status": status,
            "quickStartHref": quick_start_href,
            "cdeExtensionHref": cde_url,
            "cdeAppUrl": final_cde_url,
            "selected": selection["selected"],
            "biggestName": selection["biggest"]["name"],
            "biggestSize": selection["biggest"]["sizeLabel"],
            "sourceId": selection["source"]["value"],
            "snapshot": selection["snapshot"]["text"],
            "load": load,
            "elapsedSeconds": round(time.monotonic() - started, 1),
            "httpErrors": http_errors[:10],
        }
    except Exception as exc:
        try:
            body = (page.locator("body").inner_text(timeout=5_000) or "")[:2000]
        except Exception:
            body = ""
        screenshot = capture_screenshot(page, args.screenshot_dir, user, "automation-failed")
        print(f"[{user}] FAILED after {round(time.monotonic() - started, 1)}s: {exc}", flush=True)
        return {
            "user": user,
            "status": "automation_failed",
            "error": str(exc),
            "screenshot": screenshot,
            "elapsedSeconds": round(time.monotonic() - started, 1),
            "url": page.url,
            "body": body,
            "httpErrors": http_errors[:10],
        }
    finally:
        context.close()


def main() -> int:
    args = parse_args()
    password = args.password or os.environ.get(args.password_env)
    if not password:
        print(
            f"Set {args.password_env}=... or pass --password.",
            file=sys.stderr,
        )
        return 2

    results = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=not args.headed)
        try:
            for user in args.users:
                results.append(run_for_user(browser, args, user, password))
        finally:
            browser.close()

    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")

    print("RESULTS_JSON_START", flush=True)
    print(json.dumps(results, indent=2), flush=True)
    print("RESULTS_JSON_END", flush=True)

    failed = [result for result in results if result["status"] == "automation_failed"]
    if not args.no_click_load and not args.allow_load_failures:
        failed.extend(result for result in results if result["status"] == "load_failed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
