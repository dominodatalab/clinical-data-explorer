// Shared API helpers for the Data Explorer frontend.
//
// Owns API-related frontend helpers:
//   1. `getBaseUrl()` — derive the root path the page was served from so
//      relative API calls work both locally (`/`) and behind a Domino-style
//      reverse proxy (`/<workspace-prefix>/<deployment-id>/`).
//   2. `apiUrl(endpoint)` — prefix a relative endpoint with that base URL.
//   3. `fetchWithStatusCheck(input, init)` — shared response-level wrapper
//      around `fetch()` that returns the original Response, reloads the UI on
//      302 redirects, and rejects on HTTP error statuses.
//   4. `getApiErrorMessage(error, fallback)` — extract `error`, `message`,
//      and `description` fields from failed API responses for UI messages.
//   5. `fetchJson(input, init)` — convenience wrapper for the common
//      "GET/POST then `.json()`" pattern. It rejects on HTTP errors and on
//      successful JSON payloads that still contain an `error` field.
//
// `BASE_URL` is computed once at module load so every module shares the same
// API prefix.

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

export class ApiError extends Error {
    constructor(message = 'API request failed', { response = null, data = null } = {}) {
        super(message);
        this.name = 'ApiError';
        this.response = response;
        this.data = data;
        if (response) {
            this.status = response.status;
            this.statusText = response.statusText;
        }
    }
}

export function throwIfApiError(data) {
    if (data && data.error) {
        throw new ApiError(data.error, { data });
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

function getHeader(response, name) {
    if (!response.headers || typeof response.headers.get !== 'function') {
        return null;
    }
    return response.headers.get(name);
}

function reloadUi(location) {
    if (typeof window.location.assign === 'function') {
        window.location.assign(location);
    } else {
        window.location.href = location;
    }
}

export async function fetchWithStatusCheck(input, init) {
    const response = await fetch(input, init);
    if (response.status === 302) {
        const location = getHeader(response, 'Location');
        if (location) {
            reloadUi(location);
            throw new ApiError(`Redirecting to ${location}`, { response });
        }
        throw new ApiError('HTTP 302', { response });
    }
    if (response.status >= 400) {
        throw new ApiError(`HTTP ${response.status}`, { response });
    }
    return response;
}

export async function fetchJson(input, init) {
    const response = await fetchWithStatusCheck(input, init);
    return throwIfApiError(await response.json());
}
