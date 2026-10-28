# Agent chat database migration

[001_agent_chat.sql](001_agent_chat.sql) adds chat persistence to the **existing**
Inspector PostgreSQL database. The file is prepared locally; preparing it does
not execute it in Supabase. Live application is a separate deployment step.

## Requirements

- Select the Supabase project used by the existing Inspector deployment.
- Execute as the schema owner/admin, normally through that project's SQL Editor.
  The runtime's restricted credentials cannot create these objects and must stay
  restricted. No administrator password belongs in a Worker Secret.
- The private `inspector` schema, `inspections`, `sessions`, `receipts`, and the
  restricted `inspector_app` role must already exist. This is not base setup and
  does not create roles or change passwords.

## Changes

The migration creates `inspector.chats`, `inspector.agent_runs`, their constraints,
the run identity sequence and the history index. Shapes match the canonical
[backend schema](../../src/ai_inspector/storage.py). It checks existing column
types, nullability, identity and primary/unique/foreign-key constraints before
accepting previously created tables. Compatible repeats preserve existing rows
and sequence progress. Incompatible tables or custom row security require review;
the migration aborts rather than rewriting data or disabling that protection.

On these two tables it grants `inspector_app` SELECT/INSERT/UPDATE, and on the run
sequence USAGE/SELECT. It grants schema USAGE, not CREATE. It removes table,
column and sequence permissions for PUBLIC/anon/authenticated on these new
objects. Inherited browser access or excess runtime privileges cause rollback.
It does not alter capture data, existing capture-table grants, Storage policies,
model keys, Worker configuration or deployed code.

All changes use one transaction and the Inspector adapters' existing advisory
write lock. Lock and statement timeouts bound waiting. No DROP TABLE, row DELETE,
TRUNCATE, sequence reset, role creation or data migration is performed.

## Applying after approval

1. Confirm the intended Supabase project and administrator identity.
2. Open a new query in SQL Editor and paste the **complete** SQL file.
3. Run it as one script. The final read-only result should contain `chats` and
   `agent_runs`, each with `can_read`, `can_insert`, `can_update` set to true.
4. If it fails, inspect the error before retrying. If the client retains an
   aborted transaction, issue `ROLLBACK;` in that same session first. Do not run
   selected grants independently or remove schema/permission checks to bypass
   an error. Resolve incompatible schema/role memberships explicitly.

A successful migration prepares persistence only. Workers HTTP transport,
Meta credentials, deployment and a real phone test remain separate checks.
See [Cloudflare preparation](../../docs/cloudflare-deployment.md#agent-integration-local-preparation).

## Local verification

The optional companion `local-checks/inspector/` suite runs this file against an
isolated PostgreSQL/WASM engine (PGlite 0.5.13). It checks runtime operations,
idempotency/foreign keys, least privilege, public/browser and column grants,
inherited roles, repeat preservation, canonical schema compatibility and atomic
rollback on wrong project/role/schema. It does not load `.env`, contact Supabase,
or establish the hosted database's version, permissions or state.

From the companion workspace root:

```powershell
.\local-checks\inspector\run-tests.ps1 -Pattern test_agent_migration.py
```
