// Shared API helpers for the Data Explorer frontend.
//
// Owns API-related frontend helpers:
//   1. `getBaseUrl()` — derive the root path the page was served from so
//      relative API calls work both locally (`/`) and behind a Domino-style
//      reverse proxy (`/<workspace-prefix>/<deployment-id>/`).
//   2. `apiUrl(endpoint)` — prefix a relative endpoint with that base URL.
//   3. `fetchWithStatusCheck(input, init)` — shared response-level wrapper
//      around `fetch()` that returns the original Response but rejects when
//      the response status is > 399.
//   4. `getApiErrorMessage(error, fallback)` — extract `error`, `message`,
//      and `description` fields from failed API responses for UI messages.
//   5. `fetchJson(input, init)` — convenience wrapper for the common
//      "GET/POST then `.json()`" pattern. It builds on
//      `fetchWithStatusCheck`, so non-2xx API responses reject consistently.
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

export class HttpStatusError extends Error {
    constructor(response) {
        super(`HTTP ${response.status}`);
        this.name = 'HttpStatusError';
        this.response = response;
        this.status = response.status;
        this.statusText = response.statusText;
    }
}

export class ApiResponseError extends Error {
    constructor(data) {
        super(data && data.error ? data.error : 'API request failed');
        this.name = 'ApiResponseError';
        this.data = data;
    }
}

export function throwIfApiError(data) {
    if (data && data.error) {
        throw new ApiResponseError(data);
    }
    return data;
}

export async function getApiErrorPayload(error) {
    if (!error) return null;
    if (error.data) return error.data;
    if (!error.response || error.response.bodyUsed) return null;
    try {
        const data = await error.response.json();
        error.data = data;
        return data;
    } catch {
        return null;
    }
}

export async function getApiErrorMessage(error, fallback = 'Request failed') {
    const data = await getApiErrorPayload(error);
    const primary = (data && (data.error || data.message)) || (error && error.message) || fallback;
    const description = data && data.description;
    if (description && description !== primary) {
        return `${primary}: ${description}`;
    }
    return primary;
}

export async function fetchWithStatusCheck(input, init) {
    const response = await fetch(input, init);
    if (response.status > 399) {
        throw new HttpStatusError(response);
    }
    return response;
}

export async function fetchJson(input, init) {
    const response = await fetchWithStatusCheck(input, init);
    return await response.json();
}
