# E2E smoke test

One Playwright test that walks the whole app. It is the load-bearing piece of
the suite — if it goes green, none of the major user-facing features are
silently broken.

## Run it

```
pip install -r requirements-dev.txt
playwright install chromium
make test-e2e
```

The fixture in `tests/e2e/conftest.py` will:

1. Copy `tests/fixtures/sample.csv` into `datasets/_e2e_sample.csv` so the file
   browser can see it.
2. Start Flask (port 8888) and MCP (port 3333) via `start_servers.sh`.
3. Wait for both to respond on `/`.
4. Tear everything down (kill the process group, remove the fixture copy) at
   session end — even if the test fails.

If port 3333 or 8888 is already in use, the test is skipped rather than
fighting the existing servers.

## Updating selectors

All selectors target `data-testid` attributes. The full inventory is listed
at the top of `test_smoke.py`. When you add a feature that lives somewhere in
the app the smoke test doesn't already walk, add:

1. A `data-testid` on the new DOM element(s) in `chat_ui/index.html` (or
   wherever the markup is emitted).
2. A step in `test_smoke.py` that exercises the path.

Do not switch selectors to CSS classes or text content — those break
whenever copy or layout shifts.
