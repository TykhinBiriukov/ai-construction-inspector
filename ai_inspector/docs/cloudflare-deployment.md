# Cloudflare gateway + native backend + Supabase

This guide describes the checked-in deployment configuration. The frontend runs on Cloudflare; the Python backend runs on the existing GCP VM. PostgreSQL metadata and private capture/reference blobs remain in Supabase. Read live health and configuration from the target deployment when verifying an installation.

## Runtime

The [JavaScript gateway](../cloudflare/frontend/index.js) serves assets and forwards HTTP/WebSocket traffic to `INSPECTOR_BACKEND_ORIGIN`, set in the [frontend configuration](../cloudflare/frontend/wrangler.jsonc). It replaces any incoming gateway credential with its own server-side secret. Operator, browser and device authorization remain enforced by the backend.

Native capture and A2A pollers own durable processing. With the backend origin configured, the gateway's old Durable Object cron and completion-triggered queue are disabled. Native setup, network boundaries, persistent volumes and rollout commands are documented in [the GCP backend guide](../deploy/gcp/README.md). Local development uses [the backend README](../README.md).

The Python Worker remains a rollback implementation, not the active backend deployment path. Removing the backend origin changes routing; stop the native queue before an intentional rollback to avoid two active processors. Preserve database, Storage and service identities. Runtime admission limits are exposed by `/v1/capabilities`; do not use the old Worker's `CLOUD_LIMITS` as the native configuration.

## Database and secrets

Use the restricted runtime PostgreSQL role and private Supabase Storage. Runtime credentials must not own schemas or apply migrations. The native app uses `PostgresDatabase(initialize=False)`; its configuration comes from a private environment file, not Worker bindings. See [.env.example](../.env.example) for setting names and the [GCP guide](../deploy/gcp/README.md) for the private runtime file location.

Apply compatible additive migrations separately as the database owner:

- [001_agent_chat.sql](../scripts/sql/001_agent_chat.sql): chat/run persistence.
- [002_backlog.sql](../scripts/sql/002_backlog.sql): manual handoff board.
- [003_workspace.sql](../scripts/sql/003_workspace.sql): files, plans and observations.

Follow the [SQL application instructions](../scripts/sql/README.md). Ordinary requests and CI do not apply migrations. Preserve existing data and grants; do not rewrite published migrations or substitute an administrator database URI.

Operator/device tokens, model keys, database and Storage credentials, specialist-service credentials and optional Langfuse settings belong to the native backend environment. The public gateway needs its dedicated gateway secret; it must not receive model or database credentials. Keep secrets, signed URLs and private runtime files out of Git and browser code. See [agent configuration](agent-chat.md#server-configuration) and [observability configuration](agent-chat.md#langfuse-cloud-traces-prompts-and-evaluation).

## CI deployment

[Check Inspector and deploy web](../../.github/workflows/deploy-inspector-web.yml) runs for matching component paths pushed to `main`, or manual dispatch. Maintained offline checks gate the frontend job. The job builds and publishes frontend assets only when its component-change detection requires them. It does not deploy the Python Worker or the native backend.

Actions requires `CLOUDFLARE_API_TOKEN` and `CLOUDFLARE_ACCOUNT_ID`, scoped to the existing frontend Worker. Frontend publication uses `versions upload` and `versions deploy` at 100%, preserving domain configuration. Toolchain versions and exact commands are maintained in the workflow.

The GCP backend deploys independently through `inspector-backend-update.timer`, polling `main`. Its [updater](../deploy/gcp/update.sh) builds before restart and records the revision only after an authenticated health check. Its narrow skip list does not exclude every documentation path: edits under `ai_inspector/docs/` can trigger a backend rollout. GitHub Actions checks are not a gate for that independent timer.

## Build and publication

For frontend changes, build the viewer from `ai_inspector/viewer` with `npm ci --ignore-scripts` and `npm run build`. Use the checked-in workflow for publication to the existing Worker. For native backend setup or recovery, follow the [GCP deployment guide](../deploy/gcp/README.md); do not run the legacy Python Worker deploy script as a native-backend update.

Changes to domains, credentials, database schemas and service provisioning are separate operations from publishing code. A workstation environment file does not configure a deployed service.

## Recovery

Keep the previous native image for rollback. Authoritative records and files remain in Supabase; never resume an old SQLite snapshot as a live writer after cloud writes have started. A native replacement must use the same intended PostgreSQL database and private blob store.

For a deliberate SQLite-to-PostgreSQL migration, [migrate_to_supabase.py](../scripts/migrate_to_supabase.py) requires an empty destination, imports transactionally and verifies rows/sequences. This is separate from applying additive feature migrations and is not needed for an ordinary rollout.

## Verify after publication

Check authenticated `/deployment-health` for the native revision and database access, then verify UI availability, `/v1/capabilities`, browser/operator/device scopes and saved chat recovery. Configured model flags alone do not establish a successful provider call.

Capture verification must distinguish assignment, device acknowledgement, actual capture, accepted source and completed Geometry processing. Confirm a scoped result and its source identities; `sealed/queued_geometry` is not completion. Specialist services need their own health and recovery checks. Record the revision and observed outcome rather than copying historical test counts.
