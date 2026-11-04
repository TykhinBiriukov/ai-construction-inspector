# AI Inspector — contributor guide

AI Inspector automates construction quality control and progress monitoring through a coordinated multi-agent workflow. It combines site capture, geometric comparison, visual inspection and evidence verification to help teams make timely, traceable decisions about completed work.

The product’s core strength is the combination of deterministic measurements, specialist AI agents and source-linked verification within one inspection workflow.

## Repository map

- `ai_inspector/`: orchestration, API, persistence and web interface.
- `geometry-engine/`: RGB-D reconstruction, segmentation and scan-to-IFC comparison.
- `drawing_photo_service/`: staged drawing/photo inspection.
- `progress4d_service/`: survey processing and construction-progress tracking.
- `localization_service/`: capture localization and coverage.
- `ios/`: iPhone capture client.
- `demo/`: portable prepared and full Geometry walkthroughs.

Read the root README, then the affected component's source and setup guide. Use [agent-chat.md](ai_inspector/docs/agent-chat.md) for orchestration contracts and [offline-checks.yml](.github/workflows/offline-checks.yml) for the maintained CI commands.

## Working on changes

Inspect the current branch, status and diff before editing. Preserve unrelated work and keep changes focused. Update the existing component documentation when interfaces or setup change.

Use existing adapters, session services and workflow queues. Preserve request IDs, idempotency, authorization scopes, persisted command bodies and ownership fencing. Validate model-selected tool arguments and permissions on the server.

Bob is the orchestrator; domain inspectors own specialist tasks. Geometry Inspector delegates evidence review to Geometry Verifier, while Geometry Engine performs computation. Keep these roles distinct internally; present one AI Inspector to product users.

## Data and evidence

Preserve units, coordinate frames, source IDs, timestamps and hashes across processing stages. Derive measurements from geometry and calibrated capture data; use models for interpretation and evidence review.

Keep prepared results, test fixtures and fresh inference identifiable. Preserve the provenance of historical results and the distinction between object correspondence, observed differences and verified findings. Changes to saved-result formats must remain compatible with existing captures and replay data.

Keep credentials, private captures, signed URLs, local environments and generated caches out of commits. Use configuration and secret injection for service access.

## Verification

Run the relevant checks for the changed component with its documented dependencies. The offline CI uses Python 3.12 and Node 24:

- Inspector: `python -B -m unittest discover -s ai_inspector/tests -v`
- Web/gateway: `node --test ai_inspector/tests/web/demo-comparison.test.cjs ai_inspector/cloudflare/frontend/index.test.mjs`
- Localization: `python -B -m unittest discover -s localization_service/tests -v`
- Geometry: use the targeted test commands in the offline CI workflow and the component's test suite.

For the prepared demo, run `python3 demo/run.py --mode prepared`, then `python3 demo/run.py --verify-output demo-output`. Follow [demo/README.md](demo/README.md) for local archives, fresh output directories and full-mode setup.

For UI changes, verify the affected user flow and save/reopen behavior. Documentation-only changes require content and link checks. Report the checks performed and their outcomes. Keep temporary outputs outside tracked source files.

## Publication

Commit and publish only within the user's authorized scope. Before pushing, fetch the target branch, inspect divergence and preserve teammates' commits. Use ordinary pushes; obtain explicit approval for history rewrites, deployment changes and new paid resources.
