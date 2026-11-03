# 4D progress viewer integration

The workspace's **4D progress** button opens the existing weekly viewer in an
Inspector dialog. It retains surfaces, project overlays, stage filters, the sticky
week selector, evidence and report download. The standalone viewer also remains
available. No service credential or short-lived project URL reaches the browser.

## Configure an existing result

Run [Progress4D](../../progress4d_service/README.md) separately with durable storage,
its native CPU dependencies and a dedicated service token. Submit surveys with its
CLI or server-side client; this integration reads existing results and does not
start reconstruction, add an LLM tool, or introduce a new upload workflow.

Configure the native Inspector backend:

- `INSPECTOR_PROGRESS4D_BASE_URL`: trusted service base URL; HTTPS or loopback HTTP.
- `INSPECTOR_PROGRESS4D_SERVICE_TOKEN`: the dedicated token, distinct from Inspector
  operator/device/other service credentials. Supply through private environment.
- `INSPECTOR_PROGRESS4D_CHAT_PROJECTS_JSON`: JSON object mapping an existing
  Inspector chat UUID to its Progress4D project UUID. Both keys and values must be
  canonical UUIDs. Example shape: `{"<chat UUID>":"<4D project UUID>"}`.

The mapping is server-owned. Requests first validate operator access to the chat;
clients cannot substitute an arbitrary service project. The bridge only forwards
GET timeline/report and bounded artifact requests. It preserves HTTP byte ranges
for large GLBs, rejects redirects and does not forward upstream authorization errors
or credentials. No mapping gives a clear “No 4D result is linked” message; disabled
configuration gives “4D service is not configured yet.” Existing workflows remain
available in both cases.

The iframe loads from `/ui/progress4d/viewer.html`; reads use
`/v1/chats/{chat_id}/progress4d/data/...` with the tab's existing operator session.
Only this viewer permits same-origin framing. External framing stays prohibited.
Closing the dialog, changing conversation or logging out clears the iframe.
Reloading the workspace and reopening the result uses existing login and fresh
server-side authorization, without expiring 4D links.

## Build and publish

The canonical viewer sources live in `progress4d_service/web/`. From the repository
root, after `npm ci --ignore-scripts` in `ai_inspector/viewer`, run:

```sh
node progress4d_service/web/build-surface.mjs
```

This builds the Three.js IIFE and copies the viewer plus license notices into
`ai_inspector/src/ai_inspector/web/progress4d/`. Commit both outputs. Inspector's
static distribution and Python package include these assets. Inspector does not
import COLMAP, IfcOpenShell or other heavy service dependencies.

A Git push does not install native dependencies, copy survey data or configure the
service on GCP. Use a separate service process and persistent data directory; leave
`PROGRESS4D_LOCAL_DEMO_PROJECT` unset in hosted environments. The existing Inspector
native deployment can reach a same-VM service via its configured private endpoint;
container loopback refers to the container, so use a private network arrangement
with HTTPS unless both processes actually share loopback.

Synthetic visualization provenance and inconclusive analysis remain in the service
JSON. The embedded UI consumes the same results as the standalone viewer and does
not promote a synthetic scene to photographic evidence.

## One workspace, three demo modes

The existing workspace header has a **Mode** selector. The same selector is present
inside the model and 4D dialogs, so switching does not require closing a viewer.
The existing document panel, chat, files, evidence and model viewers are preserved.
The supplied Inspector logo is served locally as `/ui/inspector-logo.jpeg`.

Set `INSPECTOR_DEMO_MODE_CHATS_JSON` to a server-owned JSON map with keys `geometry`,
`drawing` and `monitoring` and distinct existing Inspector chat UUIDs as values.
The monitoring chat must also appear in `INSPECTOR_PROGRESS4D_CHAT_PROJECTS_JSON`.
Use a geometry chat with an accepted saved model result and a drawing chat with a
saved drawing/photo comparison. No demo data is created or analysis launched by
selecting a mode. The authenticated `/v1/demo-modes` response exposes only chats the
operator may access. Unconfigured modes show a connection message instead of
opening another demo. Normal conversation selection remains available.

Selecting a mode switches to its saved conversation and opens the existing result:
geometry model viewer, drawing comparison and document, or 4D viewer. **Open demo**
reopens it after closing. Reload retains the conversation and selected mode through
the existing workspace persistence. Unsaved-message confirmation is preserved.
