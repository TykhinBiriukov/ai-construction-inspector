# Guided examples and dictation

## Reviewer quick start

1. Open [the hosted workspace](https://inspector.campuscompile.eu/demo). The configured public demo creates a visitor session; no operator token, device or local service setup is needed for the prepared walkthrough. It has no iPhone connection and cannot assign a real capture.
2. In the left sidebar, choose **Model & site photos**, **Drawing & installation** or **Construction progress**. A details dialog explains the example and lets you inspect its source files; the model example opens `room.ifc` first.
3. For a **Prepared inputs** example, choose **Use these inputs** to attach its files and prepare the task. With a configured model, **Analyze these inputs** submits a new analysis request. Uploaded inputs alone do not establish a successful analysis.
4. For a recorded example, choose **Show example result**. Inspect the model and source images, switch between the drawing photographs or explore the saved weekly timeline. Window observations are curated; Progress4D uses rendered surveys and a prepared seventh scene.
5. Reopen a result from the conversation or use **Example details** to inspect its sources again. Follow-up chat uses the configured model. Recorded results and new input analysis are labelled separately.

For review without the hosted service, inspect the [compact records in Git and versioned evidence archives](../../docs/evidence/2026-11-03/data-access.md). The evidence release and the web demo runtime directory serve different purposes: an evidence archive is not automatically an installable `/demo-assets` directory.

For reviewer access without cloud credentials, use the [versioned real-room Geometry package and complementary cases](../../docs/evidence/2026-11-03/README.md). Public reports and the complete selected RGB-D capture and point cloud/mesh archive are available in the linked evidence release. The Geometry walkthrough originates in this real capture; the window observations are manually curated, and Progress4D uses rendered development surveys with a prepared seventh scene.

Saved-result viewing and fresh analysis are separate: the package proves retained computation artifacts, not a newly completed Inspector review. The case guide lists source hashes, measured outputs and acceptance gaps.

Production frontend deploys from `main` through `deploy-inspector-web.yml`.
The existing GCP backend polls `main` with `inspector-backend-update.timer`.
With `INSPECTOR_PUBLIC_DEMO_ENABLED=true`, the demo automatically issues a signed
24-hour visitor session. Each visitor has a separate local SQLite workspace under
the backend cache volume; production DB/storage/device credentials are not inherited.
No operator token is embedded in JavaScript or returned to visitors. Normal operator
API routes retain their original authentication. Guest API requests use the
`/public-demo` prefix; the same-origin viewer shares the guest session.

To start a real phone capture from `/demo`, choose **Sign in** and
enter the operator credential for the iPhone's project. An existing Control Room sign-in is reused automatically across open tabs in the same browser profile. The chat then uses the normal project API;
the existing `session_create` tool can assign capture to the registered phone.
The scope banner shows the shared project and device, and each new phone assignment shows its full session ID and
acknowledgement state. Existing guest conversations and their task IDs remain in
the separate demo workspace; repeat the capture request in the live chat.

## Automated HTTP checks

For a script-based availability check, send a descriptive application User-Agent:

```sh
curl -fL -A 'AI-Inspector-Repository-Review/1.0' https://inspector.campuscompile.eu/demo
```

Cloudflare can reject the default Python `urllib` User-Agent with HTTP 403 / error 1010 (Browser Integrity Check). On 3 November 2026, the default urllib request was rejected while curl, a browser, and the descriptive application User-Agent received HTTP 200. A browser identity is not required. See [Cloudflare error 1010](https://developers.cloudflare.com/support/troubleshooting/http-status-codes/cloudflare-1xxx-errors/error-1010/). HTTP 200 verifies page availability only; use the walkthrough above to inspect a result.

## Self-hosting the prepared walkthrough

Set `INSPECTOR_SAVED_DEMO_DIR=/demo-assets` to enable the three guided scenarios.
The read-only mount comes from `/opt/inspector-backend/demo-assets` and contains:

- `room.ifc`, `week-06-photo.jpg` (example reference files);
- `window-demo/`: supplied photos/drawing and curated `observations.json`;
- `geometry-demo/`: sanitized result/asset manifests, images, IFC, scan and
  `comparison.json.gz` from the supplied three-model viewer;
- `timeline.json`, `progress-assets.json`, `progress/`: exported seven-week
  result and its immutable artifacts.

A recorded-result library requires `geometry-demo/result.json`, `timeline.json`
and `window-demo/observations.json` under the configured directory. Alternatively,
an input-only library requires `scenario-inputs.json`, as described below.
`Saved demo dataset is incomplete` means neither configuration is complete or
the directory/mount is wrong; it does not mean the public evidence release is
absent. The remaining assets listed above are also needed by recorded scenario
execution. A source clone or an extracted evidence archive alone does not
configure this mount.

Runtime assets are deployed separately. Compact evidence records are committed to Git, and the selected real-room input/model archive is available in the evidence release. The original remote
result with signed URLs, credentials and local databases are excluded. Raw captures are excluded from the runtime demo mount; the selected capture is preserved in the prepared evidence archive.
Result asset routes check operator access and chat ownership. Follow-up questions
use the configured chat model. Disable the directory setting to restore normal mode.

The left sidebar presents three illustrated examples above Files, Tasks and Capture
Devices. Those workspace sections start collapsed and remain available for ordinary
work. The examples and red coachmark have been removed from the conversation area.
The welcome introduction is available through About demo; it does not open automatically.

Selecting an example opens a dismissible details dialog with its source files,
read-only previews/downloads, the steps to explore and the expected next action.
Browsing does not create a chat, attach documents or start an inspection. Source
previews are read from the configured example library, independently of the user's files.
The first listed source opens automatically. For Model & site photos this is
`room.ifc`; selecting another file switches the preview.
IFC models precede the other sources in every example, including Construction
progress. After adding prepared inputs, the workspace opens that model first
instead of the last uploaded photograph or metadata file.
The recorded library uses the IFC and source frame referenced by its Geometry result,
the drawing and two photos for Drawing, and the timeline and labelled references for
Progress. Input-only libraries list their actual available source files independently.
No example files are automatically uploaded when a chat is created.
Existing conversations retain their documents.

Examples distinguish prepared inputs from recorded results. For an input example,
Use these inputs uploads the listed attachable files through the ordinary scoped
workspace upload API and prepares its task in chat. When the server reports a
configured chat model, Analyze these inputs submits that task with the uploaded
file IDs through the normal analysis path. It does not use the recorded-demo
shortcut or include evaluator answers. Without model configuration it keeps the
files and draft ready, rather than substituting an answer. Model configuration
does not itself establish a successful analysis or available downstream services.

Recorded examples retain Show example result and the existing saved-result path;
this label does not claim fresh reconstruction or inspection. Repeated clicks
are disabled while uploads or requests are active, and pending requests retain
their existing retry identity. A completed request preserves the user's prior
draft and selected attachments. New chat returns to the ordinary empty conversation.

The central document area accepts dropped files through the same validation,
upload queue and retry identities as the circular + button. Dropped URLs are not
fetched and folders are not imported. A pending upload must be retried before a
new file set can replace it. Uploading files does not itself start an inspection.
Opening an IFC, PDF or image shows a single Fit View button instead of an expanded
Controls panel. It fits the current document and is hidden when the document is
cleared or a result viewer takes its place. Sidebar branding uses compact spacing
before the project label and example library.

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

The native Docker image builds a separate, immutable source library at
`/opt/inspector-demo-inputs` and sets `INSPECTOR_SAVED_DEMO_INPUTS_DIR` to that
directory. The library is loaded for both operator and isolated visitor examples;
it supplements the existing read-only recorded-result mount. An explicit
`scenario-inputs.json` in `INSPECTOR_SAVED_DEMO_DIR` takes precedence for the
scenarios it defines. A configured bundled library with missing files fails
startup rather than silently showing a partial list. Other installations can
set the same variable to a separately prepared input library.

Image preparation uses the published, hash-pinned Geometry evidence archive and
the repository's Progress4D inputs, without relying on a developer's local files.
The model example includes `room.ifc`, an original source frame, the capture
manifest, the export/result manifest and the full archive. Export metadata and
the archive are download-only; retained outputs are not analysis inputs.
Construction progress includes the project IFC, an original photograph from each
of seven weeks and a complete input-only ZIP. Source hashes are verified while
building the image; a missing or changed source stops the build before rollout.

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

The sidebar and welcome dialog use the bundled SVG Inspector logo.

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
