# Cloudflare + Supabase deployment

Public service: https://inspector.campuscompile.eu

A small JavaScript Worker serves the UI and forwards HTTP/WebSocket requests to
a Python Durable Object. This uses the free SQLite-backed Durable Object class,
but application metadata is not stored in its SQLite database. Metadata
lives in the private `inspector` PostgreSQL schema in Supabase. Captures and reference
BIM objects live in private Supabase Storage. A Mac process or tunnel is not needed.
The Python server and SQLite mode remain available for local development.

## Runtime

- `cloudflare/frontend/index.js` serves assets and forwards requests to one named
  Durable Object. It holds no database/Storage secrets.
- `cloudflare/src/entry.py` runs the existing FastAPI contract through Workers ASGI
  in `InspectorRuntime`. Routes and validation schemas are compiled in the snapshot;
  ContextVars keep concurrent request settings/services separate. The Durable Object
  has a 30-second CPU budget, avoiding Workers Free's 10 ms HTTP budget for Python.
  An ordinary Python Worker proved unsuitable: CPU exhaustion could poison an
  isolate ([upstream issue](https://github.com/cloudflare/workerd/issues/7814)).
- `cloudflare/src/database.py` bridges asyncpg through Hyperdrive. SQL values stay
  bound parameters; table names are explicitly qualified because pooled connections
  cannot rely on `search_path`. Read snapshots are rolled back before closing.
- Writes take a PostgreSQL transaction advisory lock, preserving SQLite's serialized
  read/modify/write and idempotency behavior across replicas. This intentionally
  favors correctness over high write throughput.
- Successful completion requests immediately wake the Durable Object queue. The
  JavaScript frontend cron also runs one queued operation per minute as recovery. Processing is asynchronous;
  allow several minutes when other operations are queued. Worker leases/retries are
  stored in PostgreSQL. WebSocket event polling is every five seconds to bound
  database use; device heartbeat behavior is unchanged.
- Reference BIM bytes are stored by hash under `inspector-models/<project>/<sha256>`.
  Local files are disposable caches; remote bytes are checked on publication/reopen.
- Geometry is not configured in this deployment. Verified captures remain
  `sealed / queued_geometry`; this is not completed Geometry or LLM analysis.

## Secrets and access

The deployment uses the restricted `inspector_app` database role: schema USAGE,
SELECT/INSERT/UPDATE/DELETE on Inspector tables and USAGE/SELECT on their sequences.
It cannot create schema objects. `anon`, `authenticated` and PUBLIC have no access
to the private Inspector schema. The app continues to use distinct scoped operator,
device and Geometry tokens; no Supabase Auth user login is involved.

Hyperdrive stores the restricted pooler connection, with origin TLS required and
query caching disabled. Worker Secrets contain `INSPECTOR_DATABASE_URL` (the same
restricted URI), `INSPECTOR_OPERATOR_TOKEN`, `INSPECTOR_DEVICE_TOKEN`,
`SUPABASE_URL`, `SUPABASE_STORAGE_BUCKET`, `SUPABASE_SECRET_KEY`, and
`INSPECTOR_PUBLIC_BASE_URL`. Optional Geometry tokens/URL are secrets too.
Do not put any credential in Wrangler config, source control, logs, or the browser.
Only the operator token belongs in the operator login form; only the device token
belongs in the phone settings.

## Agent integration: local preparation

On 28 October 2026, the local runtime entry was updated to read
`INSPECTOR_META_BASE_URL`, `INSPECTOR_META_MODEL`, `INSPECTOR_META_API_KEY`,
`INSPECTOR_META_TIMEOUT_S` and `INSPECTOR_AGENT_MAX_STEPS` from Worker bindings.
`src/runtime_settings.py` supplies the shared Settings defaults and rejects invalid
binding types/numbers without echoing their values. The API key belongs in a
Secret on `ai-inspector-runtime`; other model/limit settings can be plain Variables.
Once the chat schema is available, missing model credentials leave chat runs
resumable as model_not_configured.

The Workers SQL adapter now qualifies `chats` and `agent_runs` in the private
`inspector` schema. This does not create those tables or grant database access.
The additive [agent chat SQL migration](../scripts/sql/001_agent_chat.sql) and its
[application notes](../scripts/sql/README.md) were tested against isolated
PostgreSQL/WASM, then applied to the configured Supabase project with the owner's
confirmation on 28 October 2026. Live PostgreSQL 17.11 contains both empty tables;
effective runtime/browser permissions passed and row counts of all eleven prior
Inspector tables were unchanged. The client connection used TLS. The available
administrator cannot SET ROLE inspector_app; permissions were checked through
catalog functions, not by connecting as the restricted role or through Hyperdrive.
Model transport now uses `src/model_transport.py`: Workers Fetch with manual
redirects, one request/response deadline and streamed response reading. This avoids
the vendored HTTPX Fetch transport's automatic redirects, which can forward
authorization headers. [Workers redirect behavior](https://developers.cloudflare.com/workers/runtime-apis/request/).
The model adapter and production entry bootstrap passed thirteen synthetic
scenarios in real local workerd/Python/WASM, including redirect refusal, timeout,
oversize and malformed responses. Both production-source Workers passed dry-run
builds; the full external suite passed 150 tests (146 executed, four skipped).
Restricted runtime connectivity, real Meta access and a new deployment remain
unverified. See [the agent contract](agent-chat.md).

The owner requested checkpoints between tasks. Stages 1 (settings/SQL adapter),
2 (SQL preparation/local verification), and 3 (hosted schema/effective grants)
are complete within the verification limits above. Stage 4 build/HTTP preparation
is complete locally. Cloudflare login was not completed; the workspace owner
clarified that the account belongs to a teammate and requested pushing the code
for that teammate to finish. Use [the deployment handoff](cloudflare-agent-handoff.md)
for pulling, private key configuration, publication and live acceptance.

## Deploy an update

From `ai_inspector/cloudflare`, with Node, Python 3.14 and uv >= 0.13.4 available:

```sh
uv sync --locked
uv run pywrangler login --device --browser=false
./deploy.sh
npx --yes wrangler@4.154.0 deploy --config frontend/wrangler.jsonc
```

The wrapper copies shared backend sources before packaging; generated source copies,
Python modules, local environment files and Wrangler state are ignored. Both
`uv.lock` (development) and `pylock.toml` (Workers/WASM wheels) are committed.
The runtime config identifies the account, existing Hyperdrive binding and the
SQLite-backed Durable Object class; it has no public URL. The frontend config
references that class and only the `inspector.campuscompile.eu` custom domain. The apex site and mail records are not
changed. Do not enable a paid plan to deploy this target.

For initial setup only, create an ignored mode-0600 JSON secrets file and pass it
with `./deploy.sh --secrets-file /absolute/path/to/secrets.json`. Later deploys keep
existing Worker Secrets. Never use the database administrator URI in the Worker.
Schema initialization/migration is a separate administrator operation; ordinary
runtime requests cannot change schema or grants.

## SQLite migration and recovery

Stop the old writer, create a consistent SQLite backup with SQLite's backup API,
then run from `ai_inspector` using an administrator URI in a local ignored `.env`:

```sh
.venv/bin/python scripts/migrate_to_supabase.py --source /path/to/backup.sqlite3
```

The destination must be empty. The migration preserves row ordering and sequences,
imports in one transaction, and checks every row by reading it back. PostgreSQL
`extra_float_digits=3` avoids text-protocol precision loss during this comparison.
A mismatch rolls back the database import. Existing source files are never deleted.
For reference BIM migration, keep the backup beside its original `models/` directory.
The real cutover backup is local, ignored and must not be committed.

After cloud writes start, do not resume the old SQLite copy as the live backend.
For a native-server fallback, use the same Supabase database and
`INSPECTOR_BIM_STORAGE=supabase`; otherwise new cloud sessions would disappear.

## Verified and bounded scope

On 28 October 2026, all 123 backend tests and 3 JavaScript gateway tests passed, including PostgreSQL concurrency,
idempotency, rollback, snapshot/reopen, remote BIM integrity. The cloud transport tests delivered a synthetic assignment over WSS,
acknowledged/started it, uploaded a ZIP directly to Supabase, and waited for the
cloud cron to verify and seal it with the Mac server stopped. This passed for
both a tiny fixture and a ZIP containing 1 MiB of synthetic payload. Synthetic BIM upload
and exact-byte download passed. Existing real iPhone session metadata was migrated;
a fresh capture from the real phone against this hostname still needs user testing.

Free tiers are bounded demo infrastructure, not an SLA. The public JavaScript
Worker has a 10 ms CPU budget and 100,000 requests/day. Python runs inside a
Durable Object with a 30-second CPU budget and 128 MB memory; Free includes
100,000 requests and 13,000 GB-s of duration per day. One object is used because
this application currently has one project/device. An open non-hibernating
WebSocket uses duration quota. Hyperdrive Free allows 100,000 origin statements/day. Supabase has its own storage,
database and egress quotas. Cloud deployment limits are deliberately lower than native development: reference
BIM 8 MiB, capture ZIP 16 MiB, unpacked ZIP 64 MiB, manifest 4 MiB, 4,000 entries.
Large captures and many connected devices need separate load testing; maximum
accepted sizes are not a performance guarantee. No paid upgrade or LLM API was enabled.

References: [Durable Object limits](https://developers.cloudflare.com/durable-objects/platform/limits/),
[Durable Object Free pricing](https://developers.cloudflare.com/durable-objects/platform/pricing/),
[Workers limits](https://developers.cloudflare.com/workers/platform/limits/),
[Hyperdrive pricing](https://developers.cloudflare.com/hyperdrive/platform/pricing/),
[Python cron handler](https://developers.cloudflare.com/workers/runtime-apis/handlers/scheduled/).
