# Finish deploying the web agent to Cloudflare

Prepared 28 October 2026 for the teammate who owns Cloudflare access.
Branch: `feat/ai-inspector-development`. No new Workers version was published by
this workspace. Its owner cannot sign in to the Cloudflare account; deployment
must be completed by the account owner.

## What is ready

- Operator web chat, durable chat/run history, Meta Model API / `muse-spark-1.3`
  adapter and the predefined `session_create`, `session_get`, `device_status` tools.
  [API and retry contract](agent-chat.md).
- Cloud runtime reads model bindings and qualifies private chat SQL tables. The
  Workers-specific Fetch transport sets `redirect: manual`, bounds the complete
  request/response time and streams responses through the shared 1 MiB limit.
  It does not forward a Meta credential to a redirect destination.
- Non-secret model URL/ID/limits are in `cloudflare/wrangler.jsonc`. The Meta key
  must be a Secret on **`ai-inspector-runtime`**.
- **SQL already applied** to the existing configured Supabase project on
  28 October 2026: `inspector.chats`, `inspector.agent_runs`, their sequence,
  constraints/index and restricted grants. Both new tables were empty after
  application; row counts in all eleven old tables were unchanged.
  Effective grants passed on PostgreSQL 17.11. A restricted-role connection
  through Hyperdrive still needs live verification.

The executed [migration](../scripts/sql/001_agent_chat.sql) has SHA-256
`d9b1fd3ec291cfae02d5b6ffda0fd2d86ebcf078ec32ac0f90e2322bdc7747a3`.
For a different database, follow its [requirements and application notes](../scripts/sql/README.md).
This is an additive chat migration, not a fresh SQLite-to-Supabase import.

## Pull and configure

From an existing checkout, inspect local changes, then update this branch using
the team's normal workflow. For an unchanged checkout of this branch:

```sh
git pull --ff-only origin feat/ai-inspector-development
cd ai_inspector/cloudflare
uv sync --locked
uv run pywrangler login
```

Use the account already selected by the checked-in Wrangler configuration and
the existing Hyperdrive binding. Keep the deployed restricted `inspector_app`
database connection and all existing operator/device/Storage secrets. The native
workspace's ignored `.env` may contain administrator database credentials used
for migrations; it is not a deployment secrets file.

Obtain the Meta API key privately from the project owner. It is absent from Git;
the public `.env.example` deliberately leaves it blank. Create a private JSON file
outside the checkout, accessible only to your account, containing only:

```json
{"INSPECTOR_META_API_KEY": "<Meta key supplied privately>"}
```

Use the real value in that private file. Keep these non-secret runtime variables
from the checked-in configuration:

| Name | Value |
|---|---|
| `INSPECTOR_META_BASE_URL` | `https://api.meta.ai/v1` |
| `INSPECTOR_META_MODEL` | `muse-spark-1.3` |
| `INSPECTOR_META_TIMEOUT_S` | `30` |
| `INSPECTOR_AGENT_MAX_STEPS` | `6` |

## Build, review, publish

With Node, uv and Python 3.14 available, first build without publishing:

```sh
uv run python prepare.py
uv run pywrangler deploy --dry-run --outdir /absolute/path/outside/checkout/runtime-build
npx --yes wrangler@4.154.0 deploy --dry-run --config frontend/wrangler.jsonc --outdir /absolute/path/outside/checkout/frontend-build
```

After reviewing the builds, publish the runtime and then the frontend, which
contains the updated chat UI:

```sh
./deploy.sh --secrets-file /absolute/private/path/meta-secrets.json
npx --yes wrangler@4.154.0 deploy --config frontend/wrangler.jsonc
```

On PowerShell, replace `./deploy.sh ...` with:

```powershell
uv run python prepare.py
uv run pywrangler deploy --secrets-file 'C:\private\meta-secrets.json'
```

The secrets file updates only the Meta key; existing Worker Secrets are preserved.
This lets code and the key be deployed together. Cloudflare documents that a
standalone `wrangler secret put` immediately creates and deploys a Worker version;
use the combined deployment above for this handoff.
[Secret deployment behavior](https://developers.cloudflare.com/workers/configuration/secrets/).

Windows note: the Pyodide executable launcher failed when our isolated interpreter
was installed under a path containing spaces. The checked-in lockfiles were kept
unchanged. Local verification used the interpreter's `python.bat` explicitly with
`uv pip install -r pylock.toml --preview-features pylock --only-binary :all:
--target python_modules --python <path-to-python.bat>`, then Wrangler 4.154.0.
The teammate's existing working Mac/cloud build setup can use the normal flow.

## Verify after publication

1. Open `https://inspector.campuscompile.eu`, authenticate with the operator token,
   and verify `/v1/capabilities`: `agent_chat` and `agent_model_configured` are true.
   The latter confirms settings exist; it does not confirm Meta account access.
2. In Ask Inspector, send an ordinary question that does not request a capture.
   Confirm a real Meta reply and saved chat history after reload. This checks the
   model account and the restricted runtime's access to the new tables.
3. For an interrupted message, retry its saved request ID/text. A retry must reuse
   the run/session, rather than create a duplicate assignment.
4. For an intentional capture test, connect the iPhone using HTTPS origin
   `https://inspector.campuscompile.eu`, its device ID and device token. iOS builds
   WSS automatically. Confirm online → assignment → acknowledgement → capture-start;
   an assignment alone does not prove phone capture started.
5. The backend sends optional `capture_task.text` with source `operator_request`.
   The inspected iOS branch does not yet display that new field. Implement its
   display separately if task text must be visible on the phone.

External Geometry uses a different inspected service contract and is not completed
by this agent deployment. BIM parsing, phone business messages and automatic model
continuation after Geometry also remain future increments. See the existing
[cloud runtime/deployment guide](cloudflare-deployment.md).

## Checks performed before handoff

- Full companion suite: **150 tests / 35.218 s / OK, skipped=4** (146 executed;
  three Mac-only and one live native PostgreSQL test skipped).
- Real local workerd/Python/WASM: **13 synthetic scenarios passed**, including
  application bootstrap, a text reply/tool call, redirects with no destination
  contact, rate limits, empty/malformed/oversize/truncated responses, header/body
  timeouts and missing credentials. No real Meta, phone or database calls here.
- Both Workers passed production-source `--dry-run` builds; runtime bundle gzip
  **2898.54 KiB**, frontend script gzip **0.64 KiB**, plus static assets.
  Three gateway JavaScript tests and web JavaScript syntax check passed.
- The live Supabase schema/effective grants above were verified separately.

By the workspace owner's instruction, assistant verification tests, fixtures,
tool installations and reports stay in the companion `local-checks/inspector/`
outside Git. They are not part of this pull. A clone without that companion cannot
re-run this external suite; use the build and live acceptance steps above.
