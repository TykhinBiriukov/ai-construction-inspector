# Operator console

The FastAPI service now serves its console at `/`. There is no separate frontend
build, CDN, paid API, or CORS configuration for this same-origin UI. JavaScript,
CSS and HTML are included in the Python package.

## Available operations

- Authenticate with the server's operator bearer token. The token is held in
  memory only; reload or **Lock workspace** clears it. Use HTTPS outside localhost.
- Inspect server, configured device presence, Geometry mode and Supabase setup.
  An external Geometry URL indicates configuration, not successful inference.
- Create capture-only assignments, upload an opaque reference BIM, then assign a
  capture to an existing inspection. Reference uploads remain unvalidated.
- Choose live, recorded or synthetic capture provenance. Synthetic reference
  models have a separate checkbox. Create a rescan using an existing inspection
  and parent session ID.
- Browse sessions (including pending folders and terminal states), auto-refresh
  every five seconds, inspect errors and Geometry JSON, export a session record,
  and download an inspection's original BIM.

The new operator-only `GET /v1/operator/overview?limit=30&before=<order>` returns
project-scoped sessions, newest first, with `next_before` pagination and the latest
100 inspections. It exposes configuration flags and identifiers, never tokens.
The device credential cannot access this route. The console uses the existing
mutation endpoints and does not impersonate device acknowledgements.

Mutation retries reuse an in-memory request ID and frozen request contents. The
form remains locked after an uncertain response until retry or explicit discard.
Before closing a pending tab, reconcile the session/inspection list: the server
may already have accepted the request. Operation state is not persisted by the
browser across reloads. There is no cancel/restart-server endpoint in the current
API; process restart/configuration stays with the deployment operator.

LLM execution, BIM comparison, certified verdicts and a 3D viewer are not
implemented. Geometry results marked `analysis_origin=mock` are synthetic.

## Local run

From `ai_inspector/`, create `.env` from `.env.example`, enter distinct random
operator/device tokens and the existing integration configuration. Then:

```sh
uv venv .venv
uv pip install --python .venv/bin/python -r requirements.lock
uv pip install --python .venv/bin/python --no-deps -e .
.venv/bin/python -m uvicorn ai_inspector.main:app --host 127.0.0.1 --port 8000
```

Open `http://127.0.0.1:8000`. `requirements.lock` pins the dependencies verified on
28 October 2026. The Docker image uses Python 3.14.8. Run one worker/one replica
because the database and local artifact store are tied to one persistent volume.

## Container deployment with durable data

The build context is `ai_inspector/`. `.dockerignore` excludes `.env`, data,
fixtures, credentials, and everything outside source/package metadata.

```sh
docker compose up -d --build
docker compose logs --tail=50 inspector
```

The compose service binds only to loopback and preserves `/data` in the named
`inspector-data` volume. Do not remove that volume. Front it with an HTTPS reverse
proxy and expose only HTTPS (plus restricted SSH) in the cloud firewall. Back up
SQLite with its backup API and the `models/` directory together; copying a live
SQLite file without its WAL is unsafe.

For the real phone workflow set all existing Supabase secrets in the server's
`.env`, configure the device in the phone app and set `INSPECTOR_PUBLIC_BASE_URL`
to the final stable HTTPS origin. Preserve separate operator/device/Geometry
credentials. Do not put secrets in Git, a public image, frontend source, or docs.
For live Geometry, configure its base URL and both directional service tokens;
otherwise accepted work stays queued. Never label that as live reconstruction.

### Temporary Mac HTTPS hosting

On 28 October 2026, Oleg selected Mac hosting while Oracle account setup is blocked.
The macOS helper runs Inspector on loopback and publishes it through a Cloudflare
Quick Tunnel. Install the official Darwin arm64 `cloudflared` at
`tools/cloudflared` (ignored by Git). Version 2026.10.5 was downloaded from
Cloudflare's official GitHub release and its published SHA-256 was verified.

```sh
.venv/bin/python scripts/host_mac.py start
.venv/bin/python scripts/host_mac.py status
.venv/bin/python scripts/host_mac.py restart
.venv/bin/python scripts/host_mac.py stop
```

The supervisor detaches from the terminal, retains logs under `.local/mac-host/`,
and holds an idle-sleep prevention assertion only while hosting. Manual sleep,
closing the laptop lid, losing network access, or shutting down still interrupts
access. It does not install a login item or change system sleep preferences.
The `restart` command reloads the backend configuration while retaining the tunnel URL.
The temporary hostname changes when the tunnel restarts. A normal stop preserves
all data. After a crash, inspect `status` and logs before restarting.

On this Mac the initial `.env` and credentials were newly created with owner-only
permissions; `INSPECTOR_DATA_DIR=.local/mac-host-data` is separate from UI test
fixtures. The first verified database was empty. Supabase Storage was subsequently configured and verified; external Geometry
remains unconfigured. Session metadata stays in local SQLite. Tokens are retained across restarts; never replace
them with the earlier preview tokens. The operator token and URL are in the
ignored `.local/web-connection.json`, and the separate device configuration is in
`.local/ios-connection.json`. These files are not served over HTTP. The login page
is public; project records require a valid operator token.

Cloudflare reference: https://developers.cloudflare.com/tunnel/get-started/quick-tunnels/

Deployment verified on 28 October 2026 after explicit publication approval:
public HTTPS UI/assets and health, authenticated operator access, rejection of
unauthenticated project access, and credential-file paths returning 404. A browser
created the clearly labelled `https-smoke-test` synthetic session, then reloaded,
signed in again and reopened it. Its serialized Session matches the persisted
SQLite record. The supervisor remains running in the background. Read the current
URL from `status` or `.local/connection-public.json`; it is intentionally not a
fixed hostname. A subsequent real phone capture uploaded successfully to private Supabase Storage,
was accepted and sealed, and reached `queued_geometry`. External Geometry remains
unconfigured; no real reconstruction was verified.

## Verification

```sh
.venv/bin/python -m unittest discover -s tests -q
node --check src/ai_inspector/web/app.js
```

121 tests passed, including operator/device role boundaries, project-scoped
pagination, pending-folder visibility and reopen against persisted SQLite.
Browser QA verified login, unauthorized response, capture creation, session detail,
reload/relogin with saved sessions, and switching the BIM upload form. A real phone upload was accepted after Supabase configuration. Live Geometry
was not exercised. Docker execution was unavailable
because the local Docker daemon was not running; container build remains unverified.

### Abort a session

The Capture sessions table has an **Abort** action for sessions that are not
completed, failed or already cancelled. Confirming abort marks capture and
processing as cancelled, revokes queued/in-flight Inspector work leases and
notifies the device through the existing event stream. Files, inspection records,
verified source inventory and existing results are preserved. Pending completion
receipts become rejected with `session_cancelled`. Already dispatched external
Geometry jobs or previously issued upload URLs cannot be recalled by this action.

`POST /v1/sessions/{session_id}/abort` requires the operator token. Repeating it
is safe and returns the cancelled session without emitting another revision.
