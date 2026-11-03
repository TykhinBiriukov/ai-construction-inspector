# 4D progress viewer integration

The assistant reply's **Open progress and weekly models** button opens the existing weekly viewer in an
Inspector document panel, with a full-screen dialog option. It retains surfaces,
project overlays, stage filters and the sticky week selector. The standalone viewer also remains
available. No service credential or short-lived project URL reaches the browser.

## Configure an existing result

Run [Progress4D](../../progress4d_service/README.md) separately with durable storage,
its native CPU dependencies and a dedicated service token. Submit surveys with its
CLI or server-side client; the orchestrator tool reads existing results and does not
start reconstruction or introduce a new upload workflow.

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

## Orchestrator-driven output

Users stay in one conversation: attach documents, describe the task, then ask
follow-up questions. There is no manual demo-mode selector. The supplied Inspector
logo remains in the workspace header.

When the chat has a server-owned monitoring project binding, the orchestrator is
offered `progress4d_result`. It reads the saved timeline and returns compact task
states, dates and provenance, plus a typed `progress4d` viewer reference. The
persisted tool result creates an **Open progress and weekly models** card in the
assistant reply and opens the viewer beside the conversation. A **Full screen**
action retains the larger dialog view. Reopening the chat restores the card. The viewer uses the same
chat authorization; the model cannot supply a URL, secret or arbitrary project ID.

This tool reads an existing report; it does not download folder links or process
new uploads. Its output explicitly retains `new_analysis=false` and source
provenance. A later installation question uses the existing drawing/photo or
geometry workflow, or asks for missing inputs. Progress is not an installation
verdict. Manual demo mappings from the earlier prototype are no longer used.
