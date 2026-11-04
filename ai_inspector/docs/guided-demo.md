# Guided recorded demo and dictation

Production frontend deploys from `main` through `deploy-inspector-web.yml`.
The existing GCP backend polls `main` with `inspector-backend-update.timer`.
With `INSPECTOR_PUBLIC_DEMO_ENABLED=true`, the demo automatically issues a signed
24-hour visitor session. Each visitor has a separate local SQLite workspace under
the backend cache volume; production DB/storage/device credentials are not inherited.
No operator token is embedded in JavaScript or returned to visitors. Normal operator
API routes retain their original authentication. Guest API requests use the
`/public-demo` prefix; the same-origin viewer shares the guest session.

Set `INSPECTOR_SAVED_DEMO_DIR=/demo-assets` to enable the three guided scenarios.
The read-only mount comes from `/opt/inspector-backend/demo-assets` and contains:

- `room.ifc`, `week-06-photo.jpg` (example reference files);
- `window-demo/`: supplied photos/drawing and curated `observations.json`;
- `geometry-demo/`: sanitized result/asset manifests, images, IFC, scan and
  `comparison.json.gz` from the supplied three-model viewer;
- `timeline.json`, `progress-assets.json`, `progress/`: exported seven-week
  result and its immutable artifacts.

These assets are deployed separately, not committed to Git. The original remote
result with signed URLs, credentials, local databases and captures are excluded.
No example files are automatically uploaded when a chat is created. Existing conversations retain their documents.
Result asset routes check operator access and chat ownership. Demo stages are
simulated; selecting a scenario reads existing results. Follow-up questions use
the configured chat model. Disable the directory setting to restore normal mode.

Authenticated `GET /v1/demo/scenarios` returns `{scenarios: [{id, title, notice,
mode, available, files: [{id, name, kind, url, description, attach, byte_length}]}]}`.
Input examples additionally include `description` and `prompt`. File URLs use
`/v1/demo/scenarios/{scenario}/assets/{asset}` with GET/HEAD and a fixed allowlist;
they do not accept filesystem paths. These read-only library routes require
operator or isolated visitor access and do not require or create a chat. The public
demo prefix admits only GET/HEAD for these previews. Chat-scoped result assets keep
their existing access checks. Responses are private and not cached.

An input-only library can be configured under `INSPECTOR_SAVED_DEMO_DIR` with
`scenario-inputs.json`, without recorded Geometry/timeline outputs. Its shape is
`{scenarios: [{id, title, description, prompt, notice, files: [{id, path,
description, attach}]}]}`. Scenario IDs are `geometry`, `drawing`, `progress`;
file paths are relative to that directory and confined to it. Inputs use a new
analysis task, not one of the exact recorded-result prompts. ZIP archives must
be download-only (`attach: false`). An example can list up to sixteen files,
with at most eight attachable files; the chat attachment limit is unchanged.
Never include expected answers or evaluation truth among model inputs. A missing
source set is marked unavailable, not replaced with reports or a prepared scene.
An input-only directory does not establish a connected Progress4D result.

The prepared Progress4D source package is published under
[`resources/progress4d/inputs`](https://github.com/TykhinBiriukov/ai-construction-inspector/tree/main/resources/progress4d/inputs).
Keep it separate from the sibling replay data. A compact input example can attach
`project-materials.ifc` and one original photograph for each of seven dated weeks;
the full input-only archive contains all 312 synthetic survey photographs, seven
capture manifests and the reference IFC. These rendered survey inputs are not
real-site evidence or inspection verdicts. Preserve the source dates and hashes.

Complete RGB-D archives remain separate from ordinary chat attachments. A reference
IFC, one RGB preview and a capture manifest do not constitute validated Geometry
intake or a completed comparison. Preserve capture identity and use the existing
capture/Geometry path when processing the complete source. Real, generated and
recorded material must keep its provenance labels.

Set `INSPECTOR_VOICE_ENABLED=true` to enable Meta Muse Voice Transcribe 1.0 with
the existing server-side Meta API key. Voice records up to 60 seconds, converts
browser audio to mono 24 kHz PCM WAV and calls `/v1/voice/transcribe`. The result
is appended to the editable draft, never automatically sent. Audio is held in
memory, not saved. Microphone permissions require HTTPS or localhost. A failed
transcription leaves the current draft unchanged. Switching chats cancels the
pending insertion. The API is disabled by default; provider billing applies.

Validation: `tests/test_voice.py` covers auth, WAV validation, provider contract
and safe errors. The production dictation path was checked using a short
synthetic spoken phrase, not a user's microphone recording. The recorded demo
was checked locally with all three scenarios and seven weekly artifacts.
