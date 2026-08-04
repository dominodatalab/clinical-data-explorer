# DOM-78293 Plan: Auto-refresh Evicted User DataFrames

## Problem

Jira: https://dominodatalab.atlassian.net/browse/DOM-78293

When a user's loaded DataFrame is evicted from the MCP DataFrame cache, later requests that need a DataFrame can fail with a confusing "dataset missing" style error. The desired UX is that requests continue to work when the app still knows which dataset the user was working with and the incoming request has valid Domino passthrough credentials.

Important constraint: do not store user credentials long term in memory. Any refresh/reload must use the `Authorization` header from the request that is currently trying to perform work.

## Current Code Facts

- The backend endpoint that triggers the data-load queue is `POST /dataset/load`.
- `backend/routes/data.py::load_dataset` builds a `DatasetLoadRequest`, including the current request's `Authorization` header, then calls `DatasetLoadRequestQueue.submit_and_wait(...)`.
- `DatasetLoadRequestQueue` stores the authorization header only in the queued request entry and passes it to the file-size resolver / loader while processing.
- `get_session_id()` should resolve to the requesting user's ID; the current implementation already does this through `get_current_user()['id']`.
- MCP stores per-user loaded dataset state in `mcp_server/session.py::_sessions`.
- MCP caches actual DataFrames in `mcp_server/dataframe_cache.py` as an LRU cache.
- MCP `get_current_df()` currently reloads from `LoadedDataEntry.file_snapshot_path` when the DataFrame cache entry is missing.
- That local reload works only if the concrete file path still exists. Domino dataset and NetApp files are downloaded into temp/cache paths, and those files can also expire or disappear.
- Existing backend routes that proxy MCP calls and require an active DataFrame include:
  - `GET /dataset/data`
  - `GET /dataset/metadata`
  - `POST /table/data`
  - `GET /table/column_values/<column>`
  - `POST /table/summary`
  - `GET /table/column_stats/<column>`
  - `POST /table/expression_filter`
  - `GET /table/expression_samples`
  - chart routes in `backend/routes/charts.py`
  - chat/tool calls may indirectly require MCP DataFrame access too.

## Feasibility Of The Proposed Design

The core idea is feasible: every backend request already arrives with the user's passthrough auth, and the existing queue already knows how to reload all supported source types when given a complete `DatasetLoadRequest`.

The missing piece is not credentials. The missing piece is a durable, non-secret load descriptor that can recreate the user's current dataset:

- `dataset`
- `projectId` or `datasetId`
- `snapshotId`
- `sourceType`
- `volumeKey`
- `volumeId`
- `snapshotVersion`

That descriptor can be stored long term because it is not a credential. On refresh, combine the stored descriptor with the current request's `Authorization` header and call the existing queue path.

## Counterexamples / Design Risks

1. Backend asks MCP to refresh, MCP calls backend `/dataset/load`

   This matches the verbal flow, but it creates a circular dependency and can be hard to reason about. MCP would need to know the backend URL, call back into Flask, forward auth, and avoid recursively triggering another ensure/refresh. It also makes timeout behavior more fragile because a data request would block on backend -> MCP -> backend -> MCP.

2. MCP reloads directly from stored file path

   This is the current behavior and is good for local files, but it does not solve expired downloaded files. If `/tmp/domino_api_datasets/...` has been cleaned up, MCP has no Domino source metadata or credential context to re-download the file.

3. Store credentials with the session

   This would make refresh easy, but violates the requirement. It also creates unnecessary blast radius if process memory or logs are exposed.

4. Require the frontend to retry `/dataset/load` after a failure

   This avoids backend complexity, but users still see failed requests, and every DataFrame-consuming endpoint would need consistent client-side retry/error handling. Chat/tool calls are especially awkward because the failed MCP access can happen deeper in a backend workflow.

5. Refresh on every request unconditionally

   This preserves UX but is too expensive for large datasets and would churn memory/queue capacity. We only need to reload when the MCP session metadata exists but the DataFrame is no longer present or the concrete file path cannot be read.

## Alternatives

### Alternative A: Backend-side ensure before MCP proxy calls

Add a backend helper, for example `ensure_current_dataframe_loaded()`, used by every backend route that is about to call an MCP endpoint requiring a DataFrame.

Flow:

1. Incoming backend request arrives with current `Authorization`.
2. Backend identifies the user/session via existing `get_session_id()`.
3. Backend asks MCP whether the current session has a usable cached DataFrame.
4. If yes, continue with the original MCP request.
5. If no, backend rebuilds a `DatasetLoadRequest` from stored non-secret load context plus the current request's `Authorization`.
6. Backend calls the existing queue path, same as `POST /dataset/load`.
7. If refresh succeeds, continue with the original MCP request.
8. If refresh fails, return a clear error.

Pros:

- Reuses the existing load queue and memory admission logic.
- Keeps credentials request-scoped.
- Avoids MCP calling back into backend.
- Centralizes the retry/refresh behavior in backend route helpers.
- Easier to unit test with existing `tests/unit/test_data_routes.py` patterns.

Cons:

- Backend needs to persist a non-secret load descriptor per session.
- Every DataFrame-consuming backend route must call the helper, use middleware, or use a shared MCP proxy wrapper.
- If backend process memory restarts, refresh context is lost and the user must load the dataset again.

### Alternative B: MCP-side refresh endpoint, backend triggers it

Add `POST /dataframe/refresh-if-needed` to MCP. Backend calls it before MCP DataFrame work. MCP determines whether it has the DataFrame and, if not, calls a backend refresh endpoint that triggers the queue.

Pros:

- Puts cache-miss detection closest to the cache.
- Backend route code can make a simple preflight call.

Cons:

- Introduces backend/MCP circular calls.
- Requires MCP to know backend URL and request shape.
- More failure modes and timeout nesting.
- Must guard carefully against recursive refresh.

### Alternative C: Frontend retries the active dataset load after refresh failure

Teach the frontend to catch a missing-dataframe response, call `POST /dataset/load` again using its existing UI state, then retry the original request.

Pros:

- Avoids backend session descriptor storage.
- Keeps credentials request-scoped.

Cons:

- More frontend surface area and request-specific retry behavior.
- Chat/tool requests and internal backend workflows need extra care.
- Users still experience a failed request before recovery.

## Recommendation

Use Alternative A.

The implementation should store only non-secret load context per user and refresh through a factored backend service function that shares the internals of `POST /dataset/load`. The refresh should use the current request's `Authorization` header, but it should not call the public `/dataset/load` endpoint as an HTTP request and should not clear chat history.

Avoid MCP calling backend. MCP should expose enough session/cache status for backend to make the decision, but backend should own reload orchestration because it already owns Domino source resolution and queue admission.

Use backend middleware for the preflight refresh if the middleware is explicitly limited to known DataFrame-dependent backend routes. Do not run it globally for every backend request.

## Proposed Implementation Plan

1. Add a non-secret per-user load context store in backend.

   Suggested module: `backend/services/current_dataset_context.py`

   Shape:

   ```python
   @dataclass(frozen=True)
   class CurrentDatasetContext:
       dataset: str
       project_id: str | None = None
       dataset_id: str | None = None
       snapshot_id: str | None = None
       source_type: str | None = None
       volume_key: str | None = None
       volume_id: str | None = None
       snapshot_version: str | int | None = None
       resolved_file_snapshot_path: str | None = None
   ```

   Requirements:

   - Key the cache by requesting user ID.
   - Do not include authorization headers or tokens.
   - Implement the store as an LRU cache with TTL.
   - Make the TTL configurable by environment variable.
   - Default the TTL to 24 hours.
   - This TTL is separate from the downloaded file cache TTL. Do not increase or couple to `DATA_FILE_CACHE_EXPIRATION_SECONDS`.
   - The context cache must not expire just because MCP evicts the session; it has its own expiration policy.

2. Save the load context after successful `POST /dataset/load`.

   In `backend/routes/data.py::load_dataset`, after resolving the target and successfully loading/reusing the DataFrame, store the request metadata plus `target.file_snapshot_path`.

   Important: preserve current behavior where switching datasets clears chat history.

3. Use `GET /dataframe/current-session` as the MCP DataFrame status endpoint.

   Extend the existing endpoint rather than adding a new endpoint. It should distinguish:

   - no session
   - session exists but DataFrame evicted
   - session exists and DataFrame cached

   The response must keep the existing `dataset` key so current consumers do not need to change.

   Suggested response:

   ```json
   {
     "dataset": "/tmp/domino_api_datasets/...",
     "loaded": true,
     "cache_hit": false
   }
   ```

   `dataset` should remain the active session's cached dataset path when a session exists, even if the DataFrame cache entry is missing. Do not add `file_snapshot_path` or create `/dataframe/current-session/status`.

4. Factor the dataset load route internals into a backend service function.

   Suggested helper:

   ```python
   def load_dataset_from_request_context(
       load_request: DatasetLoadRequest,
       *,
       clear_chat_history: bool,
   ):
       ...
   ```

   `POST /dataset/load` should call this with `clear_chat_history=True` for normal user-initiated loads. Auto-refresh should call the same internals with `clear_chat_history=False`.

5. Add backend `ensure_current_dataframe_loaded()`.

   Suggested behavior:

   - Read `session_id = get_session_id()`.
   - Treat `session_id` as the requesting user's ID and use it as the context-cache key.
   - Call MCP `GET /dataframe/current-session` with the current request's auth.
   - If `loaded && cache_hit`, return.
   - If MCP has no loaded session and backend has no stored context, raise a normal "No dataset loaded" error.
   - If backend has stored context, build a fresh `DatasetLoadRequest` using the stored non-secret context and `request.headers.get("Authorization")`.
   - Call `resolve_dataset_load_target(...)`.
   - If the stored context is a local filesystem dataset, do nothing and let existing MCP direct disk reload behavior handle it.
   - If MCP reports same target path but `cache_hit=false`, call the factored dataset-load service function with `clear_chat_history=False`. Do not call MCP direct reload for Domino/NetApp sources because the cached file may be gone.
   - On success, return.

6. Run the ensure helper through backend route tagging.

   Middleware can work well here as long as it is narrowly scoped. Avoid raw path matching where possible. Prefer tagging routes that require a DataFrame, then using a `before_request` hook to check the matched endpoint's view function metadata.

   Suggested pattern:

   ```python
   def requires_current_dataframe(view):
       view.requires_current_dataframe = True
       return view

   @bp.before_app_request
   def ensure_dataframe_for_tagged_routes():
       view = current_app.view_functions.get(request.endpoint)
       if not getattr(view, "requires_current_dataframe", False):
           return None
       return ensure_current_dataframe_loaded()
   ```

   This gives us an opt-in allowlist by decorator. For blacklisting, either do not tag the route or add a second attribute such as `skip_dataframe_refresh = True` if a route is in a tagged blueprint but should be skipped.

   Blueprint-specific hooks can reduce scope further. For example, the data/chart/chat blueprints can each register a `before_request`, but the actual decision should still be based on route tagging so `/dataset/load` and non-DataFrame routes remain skipped.

   Include:

   - `get_dataset_metadata`
   - `get_dataset_data`
   - `get_table_data`
   - `get_column_values`
   - `get_table_summary`
   - `get_column_stats`
   - `expression_filter`
   - `get_expression_samples`
   - backend chart routes
   - chat route or chat-agent MCP tool entrypoint, if chat can reach DataFrame tools without going through the data routes.

   Exclude:

   - `POST /dataset/load`, because the user may be intentionally loading a different DataFrame.
   - dataset discovery and browsing routes such as `GET /datasets`, `/snapshots/*`, `/snapshot/*/files`, and `/netapp-volume/*/files`.
   - static asset routes.
   - governance routes that do not need the active DataFrame.
   - health/status endpoints.
   - MCP status checks and eviction-related backend calls.

   Route-tagging pros:

   - One central enforcement point.
   - Lower chance of accidentally matching discovery/static routes.
   - The requirement lives next to each route definition.
   - DataFrame-consuming routes stay cleaner.

   Route-tagging cons:

   - New DataFrame-consuming routes need to remember the decorator.
   - Hidden pre-route work can surprise future maintainers unless tests document the tags.
   - Decorator order should be tested once so Flask registers the annotated function correctly.

   Recommendation: use route tagging with a `before_request` hook, plus unit tests proving tagged routes refresh and `/dataset/load`, discovery routes, static routes, and untagged routes are skipped.

7. Prevent refresh loops.

   The ensure helper must not run before:

   - `POST /dataset/load`
   - MCP status checks
   - stale eviction endpoints
   - current-session eviction

   If a refresh attempt fails, return that error rather than retrying recursively.

8. Improve error messages.

   If there is no stored context, return:

   `No dataset is currently loaded. Please load a dataset first.`

   If refresh fails due auth:

   `Your session no longer has access to this dataset. Refresh the page or load the dataset again.`

   If refresh fails due file missing/deleted:

   `This dataset file could not be refreshed because the source file no longer exists or is no longer accessible.`

9. Add tests.

   Backend unit tests:

   - `/dataset/load` stores non-secret load context and excludes authorization.
   - The context cache is LRU + TTL-backed, keyed by user ID, and defaults to 24 hours.
   - The context cache TTL is configurable and separate from file cache TTL.
   - Middleware calls ensure before a DataFrame-consuming route.
   - Middleware skips `POST /dataset/load` so user-initiated dataset switching is unaffected.
   - Middleware skips discovery/static routes.
   - If MCP status is cache hit, no queue call occurs.
   - If MCP status is loaded but cache miss, backend refreshes via the factored dataset-load function using the current request Authorization.
   - Refresh preserves source metadata for dataset snapshots and NetApp.
   - Refresh does not clear chat history.
   - Local filesystem datasets are not auto-refreshed by the backend helper.
   - Refresh auth failure returns a clear error.
   - Queue-full and data-too-large errors still map to 429/413.

   MCP tests:

   - `/dataframe/current-session` distinguishes no session, cache hit, and cache miss.
   - Existing consumers that read the `dataset` field continue to work.

   Integration/contract-ish tests:

   - Simulate successful dataset load.
   - Evict/remove MCP DataFrame cache entry.
   - Call `/table/data`.
   - Assert backend reloads and original table request succeeds.

## Open Questions

- Should the route tag decorator be placed before or after `@bp.route(...)`? Validate with a focused unit test and use the order that preserves the marker on `current_app.view_functions[request.endpoint]`.
