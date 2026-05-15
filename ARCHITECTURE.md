# Clinical Data Explorer Architecture

This document describes the architectural decisions in Clinical Data
Explorer.

## System Shape

Clinical Data Explorer runs as a Domino App or Extension with three main
services:

```text
Browser UI
  -> Flask backend
  -> MCP server
```

The browser UI is the single-page app in `chat_ui/`. The Flask backend is the
service created by `backend.app` and exposed through the top-level `app.py`
entrypoint. It serves the UI, manages the browser session, handles
Domino-facing work, and proxies analysis requests. The MCP server is the
service created by `mcp_server.app` and exposed through the top-level
`data_analysis_mcp.py` entrypoint. It owns loaded dataset state and performs
DataFrame-heavy operations such as filtering, summaries, and chart aggregation.

## Architectural Decisions

### Dataset State

When a user loads a dataset, the Flask backend arranges access to the file and the
MCP server reads it into an in-memory DataFrame.

Table and chart workflows then ask the MCP server for the specific
page, summary, filtered result, or chart aggregate they need. This avoids
sending dataset files to the browser. This also means that the memory on the pod is taken up
by datasets, so the app needs to have a large amount of memory resources and autoscaling may be helpful.

### Browser Sessions Have App Session IDs

The app creates a session ID for the user's browser session and stores it in the
app's signed session cookie. That ID is forwarded from the Flask backend to the
MCP server on analysis requests.

The session ID is the key that connects a browser session to:

- the dataset loaded for that browser session
- the user's analysis requests against that dataset
- the chat context for that browser session

The session ID is app-local. It is not a Domino user ID, and it is not intended
as a durable account identifier. This design choice does make it so that the app
doesn't work well with two browser tabs using two datasets at the same time.

### Domino APIs Are Used Only From The Backend

The browser does not call Domino APIs directly to avoid exposing secrets needed for auth.
The MCP server also does not own Domino API access. Domino-facing work is centralized in the Flask
backend.

The Flask backend uses Domino APIs when it needs to:

- list project datasets and dataset files
- browse or load dataset snapshots
- discover and read NetApp volume files
- check whether a loaded file is attached to a governance bundle
- create governance findings
- resolve current-user and collaborator information for governance workflows

### Authorization and Identity

When the app runs as a Domino Extension with identity propagation enabled, the viewing user's
Bearer token is used to authorize API calls to Domino. The user is not used to manage RBAC for anything
local to the app. The session ID is the closest thing to the user ID which is used for identifying state
that belongs to a user.

### Dataset Loads Are Queued

Dataset loading is expensive because it includes file downloading,
file download and conversion into a DataFrame.

For that reason, dataset load requests go through a bounded in-memory FIFO
queue before they enter the load path. This removes the possibility of the app crashing
because of processing large files simultaneously. The queue serializes load processing
within one app process.

properties of the queue:

- it protects memory and download pressure inside a pod
- it gives users a clear capacity error when the process is already saturated
- it is not a durable job queue
- it is not shared across app pods or independent worker processes

### Caches Are Process-Local

The app uses several in-memory caches:

- loaded DataFrames in the MCP server, to provide a database-like experience
- browser-session metadata that maps session IDs to loaded datasets with a last used base expiration
- temporary downloaded file metadata used for cleanup after loading a file
- chat message history per session
- small browser-side UI caches for file browsing and summary stats

These caches are not shared between separate backend processes, MCP server processes, or app pods.

That decision has operational consequences:

- production app worker count should be `1` until state is moved to shared storage
- we rely on sticky sessions when autoscaling is turned on, so that state for each user stays on a single pod
- cache and session limits must be sized for expected concurrent usage

## Dataset Load Flow

At a high level, loading a dataset works like this:

```text
Browser
  asks to load a dataset

Flask backend
  attaches the browser session ID
  queues the load request
  resolves the file source
  uses Domino APIs if the file is Domino-backed
  downloads or locates the file
  sends the file path to the MCP server

MCP server
  reads the file into a DataFrame
  caches the DataFrame
  maps the browser session ID to the loaded dataset

Flask backend
  clears chat history for that browser session
  returns dataset metadata to the UI

Browser
  initializes table, charts, filters, and governance status
```

The load path supports local files, Domino datasets, dataset snapshots, dataset
file deeplinks, and NetApp volume files. Snapshot identity is preserved where
possible so the app can reload or govern the exact file revision the user chose.

## Analysis Flow

After a dataset is loaded, most user interactions follow the same pattern:

```text
Browser
  sends table, filter, chart, summary, or chat request

Flask backend
  forwards the request with the browser session ID

MCP server
  finds the DataFrame for that session
  performs the requested operation
  returns only the relevant result

Browser
  renders the page, chart, summary, or chat response
```

This keeps large data processing on the server and keeps browser payloads
focused on the current interaction.

## Governance Flow

Governance checks depend on file identity, not just display name. When a Domino
or NetApp-backed file is loaded, the app keeps enough source context to identify
the file and snapshot that were actually loaded.

The browser uses that context through the Flask backend to check for matching
governance bundles. If the file is governed, the UI can create a finding. The
Flask backend submits the finding to Domino Governance using the user's
propagated identity.

This keeps governance actions auditable as Domino user actions and avoids
treating local display names as sufficient governance identity.

## Permalinks And Deep Links

The app stores view state in URLs so users can share or return to a specific
view. Links can include the loaded dataset, filters, expression filters, page
state, row hints, and source identity for Domino or NetApp-backed files.

For snapshot-backed data, links preserve enough source identity to target the
same file revision rather than silently falling back to whatever file is latest
when the link is opened.

## Operational Notes

The current design favors a simple, self-contained app pod over a distributed
state architecture. That is appropriate for the app's current shape, but it
means capacity planning matters.

Important operational assumptions:

- keep Flask backend and MCP server worker counts aligned with the process-local
  state model
- size memory for loaded DataFrames, not just raw source files
- configure dataset size limits according to the hardware tier
- treat queues and caches as per-process safeguards, not global cluster
  coordination
- use sticky routing where possible for horizontally scaled deployments

If the app needs higher scale later, the main architectural change would be to
externalize shared state: dataset load coordination, session-to-dataset
metadata, chat history, and possibly cached analysis artifacts.
