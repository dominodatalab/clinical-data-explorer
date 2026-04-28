// Shared API helpers for the Data Explorer frontend.
//
// Owns three responsibilities:
//   1. `getBaseUrl()` — derive the root path the page was served from so
//      relative API calls work both locally (`/`) and behind a Domino-style
//      reverse proxy (`/<workspace-prefix>/<deployment-id>/`).
//   2. `apiUrl(endpoint)` — prefix a relative endpoint with that base URL.
//   3. `fetchJson(input, init)` — thin convenience wrapper around `fetch()`
//      that just returns parsed JSON. Use this for the common "GET/POST then
//      `.json()`" pattern. Callers that need to inspect `response.status`,
//      `response.ok`, or `response.text()` MUST keep using `fetch()` directly
//      (with `apiUrl(...)` for the URL); `fetchJson` deliberately does NOT
//      throw on non-2xx so its semantics match the existing call sites that
//      branch on a `data.error` field instead.
//
// `BASE_URL` is computed once at module load. The original code computed it
// inside the DOMContentLoaded callback and logged it; under ES module
// semantics modules execute before DOMContentLoaded fires, so the log line
// just appears slightly earlier in the console — the value is identical.

export function getBaseUrl() {
    let path = window.location.pathname;
    if (!path.endsWith('/')) {
        path += '/';
    }
    return path;
}

export const BASE_URL = getBaseUrl();
console.log('Base URL for API calls:', BASE_URL);

export function apiUrl(endpoint) {
    return BASE_URL + endpoint;
}

export async function fetchJson(input, init) {
    const response = await fetch(input, init);
    return await response.json();
}
