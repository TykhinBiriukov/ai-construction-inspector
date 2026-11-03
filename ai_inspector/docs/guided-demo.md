# Guided recorded demo and dictation

## Reviewer quick start

1. Open [the hosted workspace](https://inspector.campuscompile.eu/demo). The configured public demo creates a visitor session; no operator token, device or local service setup is needed for the prepared walkthrough.
2. Choose **Compare the 3D model with site photos**. Select an object and a frame, then inspect the model, original photograph, mask/overlay and saved measurements.
3. Choose **Check installation against the drawing**. Compare the supplied drawing with both photographs and inspect the prepared observations and marked regions. These observations are curated, not fresh model verdicts.
4. Choose **Review construction progress**. Select a week and inspect its saved model and observations. The seventh scene is prepared visualization.
5. Reopen a result from the conversation. Follow-up chat uses the configured model; the scenario buttons themselves select retained results rather than performing fresh reconstruction or inspection.

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

## Automated HTTP checks

For a script-based availability check, send a descriptive application User-Agent:

```sh
curl -fL -A 'AI-Inspector-Repository-Review/1.0' https://inspector.campuscompile.eu/demo
```

Cloudflare can reject the default Python `urllib` User-Agent with HTTP 403 / error 1010 (Browser Integrity Check). On 3 November 2026, the default urllib request was rejected while curl, a browser, and the descriptive application User-Agent received HTTP 200. A browser identity is not required. See [Cloudflare error 1010](https://developers.cloudflare.com/support/troubleshooting/http-status-codes/cloudflare-1xxx-errors/error-1010/). HTTP 200 verifies page availability only; use the walkthrough above to inspect a result.

## Self-hosting the prepared walkthrough

Set `INSPECTOR_SAVED_DEMO_DIR=/demo-assets` to enable the three guided scenarios.
The read-only mount comes from `/opt/inspector-backend/demo-assets` and contains:

- `room.ifc`, `week-06-photo.jpg` (the initial file list);
- `window-demo/`: supplied photos/drawing and curated `observations.json`;
- `geometry-demo/`: sanitized result/asset manifests, images, IFC, scan and
  `comparison.json.gz` from the supplied three-model viewer;
- `timeline.json`, `progress-assets.json`, `progress/`: exported seven-week
  result and its immutable artifacts.

The current startup check requires `geometry-demo/result.json` and `timeline.json` under the configured directory. `Saved demo dataset is incomplete` means one of those runtime files is missing or the directory/mount is wrong; it does not mean the public evidence release is absent. The remaining assets listed above are also needed by scenario execution. A source clone or an extracted evidence archive alone does not configure this mount.

Runtime assets are deployed separately. Compact evidence records are committed to Git, and the selected real-room input/model archive is available in the evidence release. The original remote
result with signed URLs, credentials and local databases are excluded. Raw captures are excluded from the runtime demo mount; the selected capture is preserved in the prepared evidence archive.
All asset routes check operator access and chat ownership. Demo stages are
simulated; selecting a scenario reads existing results. Follow-up questions use
the configured chat model. Disable the directory setting to restore normal mode.

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
