// Shared API helpers for the Data Explorer frontend.
//
// Owns API-related frontend helpers:
//   1. `getBaseUrl()` — derive the root path the page was served from so
//      relative API calls work both locally (`/`) and behind a Domino-style
//      reverse proxy (`/<workspace-prefix>/<deployment-id>/`).
//   2. `apiUrl(endpoint)` — prefix a relative endpoint with that base URL.
//   3. `fetchWithStatusCheck(input, init)` — shared response-level wrapper
//      around `fetch()` that returns the original Response, refreshes the UI
//      on manual redirects, and rejects on HTTP error statuses.
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

export const DATAFRAME_EXPIRED_CODE = 'DATAFRAME_EXPIRED';
export const DATAFRAME_EXPIRED_EVENT = 'dataframe-expired';

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

class HandledApiResponse {
    constructor(response, data) {
        this.response = response;
        this.data = markUserVisibleHandled(data);
        this.userVisibleHandled = true;
        this.status = response.status;
        this.statusText = response.statusText;
    }

    async json() {
        return this.data;
    }
}

export function isUserVisibleHandledResult(value) {
    return !!(value && value.userVisibleHandled);
}

function markUserVisibleHandled(data) {
    if (!data || typeof data !== 'object') {
        return { userVisibleHandled: true };
    }
    data.userVisibleHandled = true;
    return data;
}

export function throwIfApiError(data) {
    if (isUserVisibleHandledResult(data)) {
        return data;
    }
    if (data && data.error) {
        const error = new ApiError(data.error, { data });
        handleApiErrorPayload(error, data);
        throw error;
    }
    return data;
}

export async function getApiErrorPayload(error) {
    if (!error) return null;
    if (error.data) {
        handleApiErrorPayload(error, error.data);
        return error.data;
    }
    if (!error.response || error.response.bodyUsed) return null;
    try {
        const data = await error.response.json();
        error.data = data;
        handleApiErrorPayload(error, data);
        return data;
    } catch {
        return null;
    }
}

function handleApiErrorPayload(error, data) {
    if (!data || data.code !== DATAFRAME_EXPIRED_CODE || error.dataframeExpiredEventDispatched) {
        return;
    }
    error.userVisibleHandled = true;
    error.dataframeExpiredEventDispatched = true;
    if (typeof window !== 'undefined' && typeof window.dispatchEvent === 'function' && typeof CustomEvent === 'function') {
        window.dispatchEvent(new CustomEvent(DATAFRAME_EXPIRED_EVENT, { detail: { error, data } }));
    }
}

async function attachApiErrorPayload(error) {
    if (!error.response || error.response.bodyUsed) return error;
    try {
        const data = await error.response.json();
        error.data = data;
        handleApiErrorPayload(error, data);
    } catch {
        // Keep the original HTTP error when the response body is not JSON.
    }
    return error;
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

function refreshUi() {
    if (typeof window === 'undefined' || !window.location || typeof window.location.reload !== 'function') {
        return;
    }
    window.location.reload();
}

export async function fetchWithStatusCheck(input, init) {
    const response = await fetch(input, { ...init, redirect: 'manual' });
    if (response.type === 'opaqueredirect') {
        refreshUi();
        throw new ApiError('Redirecting', { response });
    }
    if (response.status === 302) {
        throw new ApiError('HTTP 302', { response });
    }
    if (response.status >= 400) {
        const error = await attachApiErrorPayload(new ApiError(`HTTP ${response.status}`, { response }));
        if (error.userVisibleHandled) {
            return new HandledApiResponse(response, error.data);
        }
        throw error;
    }
    return response;
}

export async function fetchJson(input, init) {
    const response = await fetchWithStatusCheck(input, init);
    const data = await response.json();
    if (isUserVisibleHandledResult(response)) {
        return markUserVisibleHandled(data);
    }
    return throwIfApiError(data);
}
