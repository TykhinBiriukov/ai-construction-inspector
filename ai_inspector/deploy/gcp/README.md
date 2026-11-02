# Native Inspector backend

Run one backend instance on the existing Geometry VM. PostgreSQL and Storage stay
in Supabase; no data migration. The app uses the restricted `inspector_app` role
with `PostgresDatabase(initialize=False)`, so startup never attempts schema changes.
Native capture and A2A pollers replace the Cloudflare Durable Object queue.

Build from repository root with this Dockerfile. Install private runtime.env at
`/opt/inspector-backend/secrets/runtime.env` (0600), using the existing backend
settings plus `INSPECTOR_BIM_STORAGE=supabase`, `INSPECTOR_DATA_DIR=/data` and a
separate `INSPECTOR_GATEWAY_TOKEN`. Start with `INSPECTOR_BACKGROUND_WORKER=false`
for read-only migration checks; enable it at cutover. Never commit the env file.
Only loopback port8200 is exposed. Caddy strips `/inspector-backend/` and proxies
to8200. All HTTP/WebSocket endpoints require the gateway secret in addition to
normal operator/device/tool authentication. `/deployment-health` checks database
access and reports the deployed revision; it also requires the gateway header.

Cloudflare frontend uses `INSPECTOR_BACKEND_ORIGIN` (HTTPS URL ending in
`/inspector-backend`) and the gateway Secret. It preserves request auth, streams
requests/responses, forwards WebSocket upgrades, replaces any caller gateway
header and refuses redirects. Native mode disables the old scheduled/poll-triggered
DO queue. Removing the origin restores the previous DO routing for rollback;
stop the native queue first. Assets and public domain remain in Cloudflare.

Git polling: checkout `/opt/inspector-backend/source`, branch
`feat/ai-inspector-development`, `update.sh` and the supplied systemd timer. Build
before restart, then check authenticated health before recording the revision.
Keep the previous image for rollback. Docker cache is disposable; authoritative
files/history remain in Supabase. The old Python Worker stays available for
rollback and must not be auto-deployed by the frontend workflow in native mode.
