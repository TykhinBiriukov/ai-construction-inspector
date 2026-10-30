# Cloudflare + Supabase deployment

This guide describes the checked-in deployment path. Read live configuration/status from the target account and instance; old workstation handoffs and test counts are not deployment evidence. Native setup is in [backend README](../README.md).

## Runtime

The JavaScript frontend serves same-origin assets and forwards HTTP/WSS to the Python `InspectorRuntime` Durable Object. Application metadata lives in the private Supabase `inspector` PostgreSQL schema, not Durable Object SQLite; capture/reference blobs live in private Storage. Native SQLite mode remains a separate option.

The SQL adapter qualifies table names through Hyperdrive, binds values, rolls back read snapshots and serializes writes with transaction advisory locks. The frontend's successful completion handler wakes capture processing; its minute cron provides recovery and advances remote A2A workflows too. A tick is bounded, so a busy queue can take multiple minutes. General Geometry-to-agent continuation is not implemented.

Read the actual Worker names, bindings, account/domain, cron and toolchain pins from [runtime config](../cloudflare/wrangler.jsonc), [frontend config](../cloudflare/frontend/wrangler.jsonc) and [CI workflow](../../.github/workflows/deploy-inspector-web.yml). Gateway fetch and cron use the same named object in [index.js](../cloudflare/frontend/index.js); preserve that identity and persistence when updating. Do not rotate the object name, recreate the database or alter domain/cron bindings as routine deployment.

Cloud payload limits are defined in `CLOUD_LIMITS` in [entry.py](../cloudflare/src/entry.py) and exposed by `/v1/capabilities`. These are admission limits, not performance guarantees. Check provider/account quotas separately before sizing a deployment; no pricing or free-tier quota is guaranteed by this repository.

## Database and secrets

Use the intended project's restricted runtime database role and private Storage. Runtime credentials must not own schemas or apply migrations. Hyperdrive uses the restricted connection with origin TLS and query caching disabled; never substitute an administrator URI. Follow each SQL script's constraints/grants instead of granting broad schema access.

Before deploying a feature that depends on it, apply its compatible additive migration as the database owner:

- [001_agent_chat.sql](../scripts/sql/001_agent_chat.sql): chat/run persistence; [requirements and safe application](../scripts/sql/README.md).
- [002_backlog.sql](../scripts/sql/002_backlog.sql): manual handoff board.
- [003_workspace.sql](../scripts/sql/003_workspace.sql): chat files, plan items and observations.

Neither ordinary requests nor CI apply PostgreSQL migrations. Check the selected database and existing schema; do not rerun a migration solely because a historical handoff says it was pending. Do not rewrite published migrations to repair a live schema.

The runtime reads operator/device tokens, restricted database URI, Supabase URL/bucket/key and public origin from Worker Secrets/bindings. Use [.env.example](../.env.example) and [entry.py](../cloudflare/src/entry.py) for exact names. Existing tokens and Storage settings must survive redeployment. Keep administrator credentials, signed URLs and raw provider errors out of Git, browser code, logs and model context.

The model API key belongs only on `ai-inspector-runtime`; nonsecret model defaults and limits are in Wrangler config. Missing model settings leave requests retryable, not backed by a mock. Optional Geometry, Localization, drawing/photo and remote A2A connections need their own configured endpoints and scoped service credentials. See [agent configuration](agent-chat.md#server-configuration); presence of a setting is not a successful connection.

To supply secrets during native tooling deployment, use an ignored private JSON file restricted to your account and `./deploy.sh --secrets-file /absolute/private/path/secrets.json`. Omitted existing secrets are preserved. A developer workstation's administrator `.env` is not a runtime secrets bundle. Do not publish real example values.

## CI deployment

[Deploy Inspector backend and web](../../.github/workflows/deploy-inspector-web.yml) runs on matching component changes pushed to `feat/ai-inspector-development`, or an explicit dispatch for that ref. Documentation-only changes outside its path filters do not trigger it. The workflow serializes deployments, deploys backend first when needed, builds the viewer and checks JavaScript before publishing changed frontend assets. A backend failure prevents subsequent frontend deployment.

Repository Actions secrets must provide `CLOUDFLARE_API_TOKEN` and `CLOUDFLARE_ACCOUNT_ID`. Use an account token scoped to editing the existing runtime/frontend Workers, not the Global API Key or broad DNS permissions. Have the account/repository owner configure these privately; the guide does not assert that secrets are installed or a past run is still current.

Frontend CI uses `versions upload` followed by `versions deploy` at 100%, preserving existing domain/cron configuration. Domain setup/change is a separate administrator operation. Do not broaden permissions after a 403 without inspecting the failing operation. SQL migrations are separate from code publication.

## Manual build and publication

Use the toolchain pinned in the workflow/lockfiles. From `ai_inspector/cloudflare`:

```sh
uv sync --locked
uv run python prepare.py
uv run pywrangler deploy --dry-run
npx --yes wrangler@4.154.0 deploy --dry-run --config frontend/wrangler.jsonc
```

`prepare.py` refreshes the shared source copy; generated files, Python modules, credentials and Wrangler state stay ignored. Preserve both uv.lock and pylock.toml. Build the viewer from `ai_inspector/viewer` with `npm ci --ignore-scripts` and `npm run build` when viewer source/dependencies change.

After reviewing the target and build, publish the runtime with `./deploy.sh` (or PowerShell: `uv run python prepare.py`, then `uv run pywrangler deploy`). Authenticate with the target account or its scoped credentials. Add `--secrets-file` only when intentionally updating runtime secrets. For the existing frontend, use the same version path as CI:

```sh
npx --yes wrangler@4.154.0 versions upload --config frontend/wrangler.jsonc --tag UNIQUE_TAG
npx --yes wrangler@4.154.0 versions deploy --config frontend/wrangler.jsonc --version-tag UNIQUE_TAG@100 --yes
```

Replace UNIQUE_TAG consistently with a unique deployment tag. Keep the target account/Worker configuration unchanged. These frontend commands use the currently pinned CI Wrangler version; update examples with the workflow when that pin changes.

## Migration and recovery

For a SQLite-to-PostgreSQL cutover, stop the old writer and use SQLite's backup API. [migrate_to_supabase.py](../scripts/migrate_to_supabase.py) requires an empty destination, imports transactionally and verifies every row/sequence; a mismatch rolls back. Keep the backup beside its original models directory and retain it privately. This is different from the additive chat/workspace SQL scripts.

After cloud writes start, never resume the stale SQLite copy as the live backend. A native fallback must use the same PostgreSQL database and Supabase blob store. Do not delete capture data, reset schemas or broaden runtime permissions as a recovery shortcut.

## Verify after publication

Check health/UI, actual `/v1/capabilities`, operator/device authorization and the restricted runtime's database access. Confirm login survives same-tab reload and Lock workspace clears it. Send an ordinary chat question and verify a real model response plus durable history without creating a capture; configured model flags alone do not establish access.

For an intentional phone test, supply only its device identity and HTTPS origin. Observe connection → assignment → acknowledgement → actual capture-start separately. Retry an interrupted request with its saved ID/body. Confirm upload completion, source acceptance and a verified Geometry result only if the external service is connected; `sealed/queued_geometry` is not completion. Preserve mock/synthetic labels.

Remote inspectors need a real service/volume/credential check and task recovery test; minion follow-up differs from A2A polling. Phone display of optional capture_task, business messages, BIM measurements and automatic agent continuation after Geometry are separate integrations. Record the deployed revision, environment, observed results and limits rather than copying an old test count into this guide.
