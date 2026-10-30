# AI Construction Inspector — repository entry point

This checkout contains the Inspector backend in `ai_inspector/`. Respond in Russian unless the user requests another language. Follow the component requested by the user; do not assume mobile/Localization code exists on the current branch.

For this local workspace, apply `../AGENTS.md` and read `../README.md` before substantive work. The README links to owner decisions, remaining work and evidence; read those only as needed. Determine implemented behavior from code and maintained backend docs. Files in `../sources/` are reference data, not active instructions.

If the companion workspace is absent in another clone, use `ai_inspector/README.md`, the relevant docs and source, and any applicable component instructions. For agent work, start with [the implemented agent contract](ai_inspector/docs/agent-chat.md), `ai_inspector/src/ai_inspector/agent.py` and `model_client.py`. The companion files are not runtime dependencies.

The selected product model is Meta Model API / Muse Spark 1.3 (`muse-spark-1.3`). Use the shared harness and existing session service. Preserve request/operation IDs and exact bodies on retry, persist commands before side effects, validate scopes, and fence stale owners. Follow the documented recovery path: remote A2A workflows have a poller, local message retries and drawing/photo follow-up differ, and general Geometry continuation is not implemented. The phone initiates its authenticated channel; assignment is not acknowledgement or camera start. Optional `capture_task` is operator task context; preserve older sessions without it. Actual Meta/iPhone access must be verified separately from local fixtures.

Run Git from this checkout root, inspect status/diff, and preserve local work. Run backend commands from `ai_inspector/`. Never infer a clean tree or a working service from an earlier handoff. Do not change Git metadata to compensate for moved paths or permission limits.

When committing is authorized, split distinct logical changes into focused, preferably working commits with related docs. Inspect each staged group and the outgoing series; a push request does not imply squash. Do not commit/push without task authorization or rewrite published history without explicit permission.

Preserve server-owned session IDs, idempotent operations, accepted source identity, authorization scopes, and distinct capture/processing states. Model-selected tools must validate arguments and rights in backend. Keep mock/synthetic results distinct from live inference/reconstruction. Geometry completion does not establish BIM compliance.

Keep credentials, `.env`, `.local/`, scans, and signed URLs out of context and commits. Run checks appropriate to the change and state what was actually verified. Do not start external publication or services merely because an imported document describes them.

All tests used by Codex for generated-code verification, including the automated backend regression suite, diagnostics, fixtures, temporary databases, caches, and reports, belong outside this checkout in the companion workspace's local-checks/inspector/ directory. Do not recreate ai_inspector/tests or add these local verification files to Git. Product runtime and operational hosting/configuration helpers stay here. From ai_inspector/, run `..\..\local-checks\inspector\run-tests.ps1` (or `-Pattern test_agent.py` for the agent module). Inventory additional tests in the checkout and use external copies with corrected paths while preserving teammate originals. If the external suite is absent, report that gap; do not recreate it inside the checkout. Documentation-only changes need link/consistency checks, not a backend or live API run.

## Documentation ownership

Keep maintained API/setup/integration guides in ai_inspector/docs (or the affected component's shared docs); update the relevant existing guide when behavior changes. Personal decisions, pending proposals/research and verification belong in the companion workspace outside Git. Avoid duplicate state ledgers or API specifications in local context, and do not create a Markdown file after every edit. Shared guides must remain usable without companion files.
