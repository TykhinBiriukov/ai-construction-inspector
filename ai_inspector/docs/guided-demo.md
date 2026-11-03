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

- `room.ifc`, `week-06-photo.jpg` (the initial file list);
- `window-demo/`: supplied photos/drawing and curated `observations.json`;
- `geometry-demo/`: sanitized result/asset manifests, images, IFC, scan and
  `comparison.json.gz` from the supplied three-model viewer;
- `timeline.json`, `progress-assets.json`, `progress/`: exported seven-week
  result and its immutable artifacts.

These assets are deployed separately, not committed to Git. The original remote
result with signed URLs, credentials, local databases and captures are excluded.
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
