# Northflank cloud deployment

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
  --output /private/path/to/runtime.json
```

The administrator URI is read from `INSPECTOR_DATABASE_URL` in that local file;
it must explicitly configure TLS. The generated JSON is mode0600 and must remain
outside Git. Never copy the administrator URI into the hosting environment.
The script refuses to overwrite an existing role or credential file.

Set these runtime secrets/settings from the generated file:
- `LOCALIZATION_SERVICE_TOKEN`: independent random secret, >=32ASCII characters.
  Inspector sends it as Bearer to the internal provisioning endpoint. Never give it to the phone.
- `LOCALIZATION_DATABASE_URL`: restricted role, Supabase pooler with TLS.
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

## Dynamic BIM handoff

No model is preloaded. Phone calls `POST /v1/sessions/{id}/localization` on Inspector.
Inspector authenticates the assigned device, reads the pinned BIM and posts the
binding plus `ifc_base64` to `POST /internal/localization/sessions` using its service
secret. Localization checks SHA256, parses the IFC and pins the session/model.
The response contains a session-specific token; Inspector adds the public endpoint.
The phone uses that token in X-Localization-Code only for its session routes:
GET /v1/localization/sessions/{id}/config, POST start/observations/pose/finish, GET state.
The old global config and phone-side session creation are not exposed in cloud mode.

Inspector settings: INSPECTOR_LOCALIZATION_BASE_URL (HTTPS) and
INSPECTOR_LOCALIZATION_SERVICE_TOKEN (same service secret as Localization).
The session token is deterministic, isolated by session, and valid until service-key
rotation; this prototype does not implement immediate token revocation on abort.
BIM storage is capped at50MiB and per-file IFC at8MiB. Existing evidence budget100MiB.

Current importer: one axis-aligned rectangular room, four walls, one floor, one
space and one door/opening on min-X, metre units. New compatible IFC versions are
accepted per session; whole buildings, other door walls and rotated rooms remain
unsupported. This does not implement the Inspector LLM instruction-ownership
handoff or a drift monitor.

## Verification

29October: restricted role/schema created and live PostgreSQL access checked;
Inspector schema access denied. No BIM preloaded.24 local tests passed, including
two model bindings, restart/retry recovery and commit failure. Inspector adapter
has two tests covering authentication/scope, transfer and invalid upstream binding.
Cloud deployed at https://p01--localization-service--p65k2lfwxlhl.code.run
(Northflank Default Free, one0.1vCPU/256MB instance). Public health200 and two
synthetic IFCs5x4m/6x3.5m passed the live Inspector handoff, including session-token
isolation. Readiness probe /healthz passes. iOS unsigned build succeeds but is not
installed in this cloud verification. No real photographs/private IFC were uploaded;
real-phone geometry accuracy/performance remain unverified.
