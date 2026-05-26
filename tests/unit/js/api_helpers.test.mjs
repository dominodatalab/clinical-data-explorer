import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { test } from 'node:test';
import vm from 'node:vm';

const apiSourcePath = new URL('../../../chat_ui/core/api.js', import.meta.url);
const apiSource = await readFile(apiSourcePath, 'utf8');

async function loadApiModule({
    pathname = '/',
    fetchImpl = async () => ({ status: 200 }),
    assignLocation = () => {},
} = {}) {
    const context = vm.createContext({
        console: { log() {} },
        fetch: fetchImpl,
        window: {
            location: {
                pathname,
                assign: assignLocation,
            },
        },
    });
    const module = new vm.SourceTextModule(apiSource, {
        context,
        identifier: String(apiSourcePath),
    });
    await module.link(() => {
        throw new Error('api.js should not import other modules');
    });
    await module.evaluate();
    return module.namespace;
}

function jsonResponse(status, data, extra = {}) {
    const headers = extra.headers || {};
    return {
        status,
        statusText: extra.statusText || '',
        bodyUsed: extra.bodyUsed || false,
        headers: {
            get(name) {
                const matchingKey = Object.keys(headers).find(key => key.toLowerCase() === name.toLowerCase());
                return matchingKey ? headers[matchingKey] : null;
            },
        },
        json: extra.json || (async () => data),
    };
}

test('getBaseUrl appends a trailing slash and apiUrl uses the module base URL', async () => {
    const api = await loadApiModule({ pathname: '/app/workspace' });

    assert.equal(api.getBaseUrl(), '/app/workspace/');
    assert.equal(api.BASE_URL, '/app/workspace/');
    assert.equal(api.apiUrl('chat/status'), '/app/workspace/chat/status');
});

test('apiUrl handles an app served at the domain root', async () => {
    const api = await loadApiModule({ pathname: '/' });

    assert.equal(api.getBaseUrl(), '/');
    assert.equal(api.apiUrl('datasets'), '/datasets');
});

test('ApiError carries response metadata and payload data', async () => {
    const api = await loadApiModule();
    const response = jsonResponse(503, { error: 'Unavailable' }, { statusText: 'Service Unavailable' });
    const error = new api.ApiError('Request failed', {
        response,
        data: { error: 'Unavailable' },
    });

    assert.equal(error.name, 'ApiError');
    assert.equal(error.message, 'Request failed');
    assert.equal(error.response, response);
    assert.deepEqual(error.data, { error: 'Unavailable' });
    assert.equal(error.status, 503);
    assert.equal(error.statusText, 'Service Unavailable');
});

test('throwIfApiError returns successful payloads and throws for API error payloads', async () => {
    const api = await loadApiModule();
    const payload = { ok: true };

    assert.equal(api.throwIfApiError(payload), payload);
    assert.throws(
        () => api.throwIfApiError({ error: 'Dataset unavailable', detail: 'missing' }),
        error => {
            assert.ok(error instanceof api.ApiError);
            assert.equal(error.message, 'Dataset unavailable');
            assert.deepEqual(error.data, { error: 'Dataset unavailable', detail: 'missing' });
            return true;
        },
    );
});

test('getApiErrorPayload returns existing data, parses response JSON, and caches it', async () => {
    const api = await loadApiModule();
    const existingData = { error: 'Already parsed' };

    assert.equal(await api.getApiErrorPayload({ data: existingData }), existingData);

    let parseCount = 0;
    const error = {
        response: jsonResponse(500, null, {
            json: async () => {
                parseCount += 1;
                return { error: 'Parsed from response' };
            },
        }),
    };

    assert.deepEqual(await api.getApiErrorPayload(error), { error: 'Parsed from response' });
    assert.equal(parseCount, 1);
    assert.deepEqual(await api.getApiErrorPayload(error), { error: 'Parsed from response' });
    assert.equal(parseCount, 1);
});

test('getApiErrorPayload returns null when no response body can be read', async () => {
    const api = await loadApiModule();

    assert.equal(await api.getApiErrorPayload(null), null);
    assert.equal(await api.getApiErrorPayload({ response: jsonResponse(500, {}, { bodyUsed: true }) }), null);
    assert.equal(
        await api.getApiErrorPayload({
            response: jsonResponse(500, null, {
                json: async () => {
                    throw new Error('not json');
                },
            }),
        }),
        null,
    );
});

test('getApiErrorMessage prefers API fields and appends distinct descriptions', async () => {
    const api = await loadApiModule();

    assert.equal(
        await api.getApiErrorMessage({
            data: {
                error: 'Could not load dataset',
                description: 'The dataset was deleted',
            },
        }),
        'Could not load dataset: The dataset was deleted',
    );
    assert.equal(
        await api.getApiErrorMessage({
            data: {
                message: 'Authentication required',
                description: 'Authentication required',
            },
        }),
        'Authentication required',
    );
    assert.equal(await api.getApiErrorMessage(new Error('Network failed'), 'Fallback'), 'Network failed');
    assert.equal(await api.getApiErrorMessage(null, 'Fallback'), 'Fallback');
});

test('fetchWithStatusCheck returns successful responses and throws ApiError on HTTP failures', async () => {
    const okResponse = jsonResponse(204, null);
    const api = await loadApiModule({
        fetchImpl: async (input, init) => {
            assert.equal(input, '/ok');
            assert.deepEqual(init, { method: 'POST' });
            return okResponse;
        },
    });

    assert.equal(await api.fetchWithStatusCheck('/ok', { method: 'POST' }), okResponse);

    const errorResponse = jsonResponse(404, { error: 'Not found' }, { statusText: 'Not Found' });
    const failingApi = await loadApiModule({
        fetchImpl: async () => errorResponse,
    });

    await assert.rejects(
        failingApi.fetchWithStatusCheck('/missing'),
        error => {
            assert.ok(error instanceof failingApi.ApiError);
            assert.equal(error.message, 'HTTP 404');
            assert.equal(error.response, errorResponse);
            assert.equal(error.status, 404);
            assert.equal(error.statusText, 'Not Found');
            return true;
        },
    );
});

test('fetchWithStatusCheck reloads the UI when a 302 response has a Location header', async () => {
    let assignedLocation = null;
    const redirectResponse = jsonResponse(302, null, {
        headers: { Location: '/login?next=/app/workspace/' },
    });
    const api = await loadApiModule({
        fetchImpl: async () => redirectResponse,
        assignLocation: location => {
            assignedLocation = location;
        },
    });

    await assert.rejects(
        api.fetchWithStatusCheck('/chat/status'),
        error => {
            assert.ok(error instanceof api.ApiError);
            assert.equal(error.message, 'Redirecting to /login?next=/app/workspace/');
            assert.equal(error.response, redirectResponse);
            assert.equal(error.status, 302);
            return true;
        },
    );
    assert.equal(assignedLocation, '/login?next=/app/workspace/');
});

test('fetchWithStatusCheck rejects 302 responses without a Location header', async () => {
    let assignedLocation = null;
    const redirectResponse = jsonResponse(302, null);
    const api = await loadApiModule({
        fetchImpl: async () => redirectResponse,
        assignLocation: location => {
            assignedLocation = location;
        },
    });

    await assert.rejects(
        api.fetchWithStatusCheck('/chat/status'),
        error => {
            assert.ok(error instanceof api.ApiError);
            assert.equal(error.message, 'HTTP 302');
            assert.equal(error.response, redirectResponse);
            assert.equal(error.status, 302);
            return true;
        },
    );
    assert.equal(assignedLocation, null);
});

test('fetchJson parses successful JSON and rejects API error payloads', async () => {
    const api = await loadApiModule({
        fetchImpl: async () => jsonResponse(200, { rows: [1, 2, 3] }),
    });

    assert.deepEqual(await api.fetchJson('/table/data'), { rows: [1, 2, 3] });

    const failingApi = await loadApiModule({
        fetchImpl: async () => jsonResponse(200, { error: 'Expression is invalid', detail: 'bad syntax' }),
    });

    await assert.rejects(
        failingApi.fetchJson('/table/expression_filter'),
        error => {
            assert.ok(error instanceof failingApi.ApiError);
            assert.equal(error.message, 'Expression is invalid');
            assert.deepEqual(error.data, { error: 'Expression is invalid', detail: 'bad syntax' });
            return true;
        },
    );
});

test('fetchJson HTTP failures can still expose backend JSON through getApiErrorMessage', async () => {
    const api = await loadApiModule({
        fetchImpl: async () => jsonResponse(500, {
            error: 'Error getting agent response',
            description: 'OpenAI quota exceeded',
        }),
    });

    try {
        await api.fetchJson('/chat');
        assert.fail('Expected fetchJson to reject');
    } catch (error) {
        assert.ok(error instanceof api.ApiError);
        assert.equal(
            await api.getApiErrorMessage(error, 'Request failed'),
            'Error getting agent response: OpenAI quota exceeded',
        );
    }
});
