# Northflank cloud deployment (prepared, not yet deployed)

Build context: repository root. Dockerfile: `localization_service/deploy/Dockerfile`.
The adjacent ignore list excludes unrelated sources, secrets and local captures.
Run one instance, port8788, provider HTTPS, HTTP health check `/healthz`.
Disable automatic builds until the first deployment has been verified.

Northflank's actual free project offers 0.1shared vCPU/256MB. A persistent volume
is separately billed and must not be created. Local IFC import peaked at108MiB
on macOS; Linux container solver peak/latency remain unverified.

## Durable state without a paid volume

The runtime uses a dedicated `localization_app` PostgreSQL role and private
`localization` schema in the existing Supabase database. It has no Inspector
schema access, no storage-admin token and no database-administrator privileges.
An administrator runs `provision.py` once with explicit authorization:

```
python -m localization_service.deploy.provision \
  --admin-env /private/path/to/admin.env \
  --model /private/path/to/room.ifc \
  --output /private/path/to/runtime.json
```

The administrator URI is read from `INSPECTOR_DATABASE_URL` in that local file;
it must explicitly configure TLS. The generated JSON is mode0600 and must remain
outside Git. Never copy the administrator URI into the hosting environment.
The script refuses to overwrite an existing role or credential file.

Set these runtime secrets/settings from the generated file:
- `LOCALIZATION_SERVICE_TOKEN`: independent random secret, >=32ASCII characters.
  The iPhone already sends it in `X-Localization-Code` over HTTPS.
- `LOCALIZATION_DATABASE_URL`: restricted role, Supabase pooler with TLS.
- `LOCALIZATION_MODEL_SHA256`: privately provisioned model hash.
- `PORT=8788`, optional `LOCALIZATION_DATA_DIR=/data` (disposable local cache).

Session changes and associated evidence commit together before HTTP success.
Frame/depth/confidence bytes are durable; RGB is validated and its hash retained,
but the full photograph is not duplicated in this database. Original RGB capture
upload to Supabase remains the phone/Inspector's existing flow.
On a fresh container, evidence files are restored and local paths rebased,
including idempotency records. A transaction advisory lock covers overlapping
containers during rollout. This is not a high-availability or high-load design.
A100MiB aggregate evidence budget protects the shared free database; completed
sessions need an explicitly approved cleanup/archive procedure when it fills.

## Current verification and pending steps

29October: GitHub repo connected in Northflank, free project/card setup confirmed.
No service or paid disk created. Creating the DB role/schema and uploading the IFC
was blocked by automatic approval review; explicit user approval is pending.
Runtime credentials have NOT been generated or sent to Northflank.

Local synthetic tests cover restart with an empty cache, preserved alignment and
coverage, immutable retries, and rollback on storage failure. The database in those
tests is a SQLite double; live PostgreSQL permissions/transactions, container build,
public HTTPS and real-phone cloud behavior still require verification.

Current geometry limitations remain: one prepared rectangular room and known door,
not arbitrary IFC buildings. No Inspector control handoff/callback is added here.
