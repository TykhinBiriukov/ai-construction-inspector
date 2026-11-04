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
`main`, `update.sh` and the supplied systemd timer. Build
before restart, then check authenticated health before recording the revision.
Keep the previous image for rollback. Docker cache is disposable; authoritative
files/history remain in Supabase. The old Python Worker stays available for
rollback and must not be auto-deployed by the frontend workflow in native mode.

The updater fetches and checks out new commits but skips build/restart when the
diff from the **actually deployed revision** contains only `demo/`, root
`README.md`/`.gitignore`, `docs/reproducible-setup.md`, or this deployment
README/updater script. It leaves `deployed-revision` unchanged so health continues
to identify the running image. Runtime code, Docker/Compose configuration,
bundled input manifests, dependencies and unknown paths still trigger rollout.
Comparing with the running revision also preserves pending runtime changes when
a later commit changes documentation only.

When first installing this filter on a host running the old updater, stop only
`inspector-backend-update.timer`, wait for its oneshot service to be inactive,
fetch/check out the new main under `/opt/inspector-backend/update.lock`, then start
the timer again. Do not stop the backend container. Run the updated oneshot and
verify the skip message, unchanged container ID/start time and deployed revision.

The Docker build also prepares the example inputs in `/opt/inspector-demo-inputs`
and sets `INSPECTOR_SAVED_DEMO_INPUTS_DIR`. It uses the checked-in Progress4D
inputs and the Geometry release archive pinned by the evidence manifest. The
preparer verifies source hashes and packages the IFC, source/export manifests,
seven weekly photos and download-only archives. GitHub release access is needed
during this cached build step; requests do not download external assets at runtime.
A failed preparation stops the build, leaving the current container running.

The existing `/demo-assets` mount remains the recorded-result library. No local
developer fixtures need to be copied to that mount. An operator-authored
`scenario-inputs.json` there overrides the corresponding bundled examples.
