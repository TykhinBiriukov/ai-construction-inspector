# AI Inspector iPhone — first capture prototype

Native SwiftUI / ARKit / RealityKit application. No external packages or paid APIs.
Open `AIInspector.xcodeproj`, select the `AIInspector` scheme and an iPhone simulator, then Run.
Deployment target: iOS 18.0. Built with Xcode 27.1.

Verified 2026-10-27: Debug builds succeed for both generic iOS Simulator (arm64/x86_64) and generic iOS device (arm64), with signing disabled. Storage checks pass. No simulator launch or physical-device run has taken place yet.

## Implemented

- Room screen inspired by Oleg's reference; generated 3D room, instruction card and evidence list.
- Simulator defaults to visibly synthetic mode. Its saved fixture is a plane at 2 m, **not** a scan of the displayed room.
- Physical iPhone defaults to camera mode. Start camera explicitly; capture one observation per button press.
- ARKit capture reads RGB, unsmoothed sceneDepth, confidence, intrinsics, pose and timestamp from the same ARFrame. Geometry processing stays on the backend.
- One outstanding save at a time. JPEG encoding and disk writes run off the main actor.
- Persisted session IDs, separate synthetic/device sessions, new coordinate segment on every camera restart. Normal tracking and a fresh frame are required to capture.
- Local observation folders committed atomically; checksums verified on reopen. Missing confidence encoded as null. No invented depth.
- Evidence detail, individual-file sharing, and Files app access to the entire session folder including manifest.
- Local demo instruction and observation linkage. The demo “Cannot access” response is explicitly local; it is not sent or queued.

## Not implemented / not validated

- No network upload, authentication, orchestrator polling, server analysis, completion endpoint or voice yet. Saved observations await a future uploader; the UI never counts them as uploaded.
- No real BIM file import yet; the displayed room is generated geometry with stable node names. No scan-to-BIM alignment, coverage or reconstruction on the phone.
- No automatic continuous capture or session-finalization UI. Current prototype reopens the most recent session per source, starting a fresh segment as needed.
- No physical-device capture/reprojection validation yet. Build success does not establish RGB-depth registration or measurement accuracy.
- No visual simulator check until the iOS runtime has finished downloading.
- A damaged committed file blocks session reopening with an error; repair/deletion UI is not implemented. Interrupted hidden staging directories are ignored, not treated as observations.

## Device run

Connect the consenting owner's LiDAR iPhone. In Xcode → Signing & Capabilities, choose your development team; change the bundle ID if needed. Select the phone, enable Developer Mode if prompted and Run. Grant camera permission in the app. Do not sign into your Apple account on a borrowed phone.

1. Start camera; slowly view nearby textured surfaces until tracking initializes.
2. Capture several overlapping observations; inspect the list and saved image.
3. Background/reopen, restart camera, capture again; verify a new segment ID.
4. Close/relaunch; verify existing observations remain.
5. Export `sessions/<session_id>` through Files to the backend developer.
6. Validate JPEG dimensions, depth byte count, intrinsics, axes and reprojection on real data before freezing the contract.

## Local checks

From the repository root:

```sh
xcrun swiftc -module-cache-path /tmp/inspector-swift-cache ios/AIInspector/Observation.swift ios/Checks/main.swift -o /tmp/inspector-checks
/tmp/inspector-checks
xcodebuild -project ios/AIInspector.xcodeproj -scheme AIInspector -sdk iphonesimulator -destination 'generic/platform=iOS Simulator' -derivedDataPath /tmp/ai-inspector-build CODE_SIGNING_ALLOWED=NO build
```

The Foundation checks cover SIMD column-to-row serialization, local reopen, instruction links, byte preservation including invalid depth, missing confidence, partial writes, invalid segments, truncated buffers, corruption and separation of synthetic/device sessions. They use dummy JPEG bytes to exercise storage only; they do not test camera encoding or UI.

ARKit reference: [Apple scene depth sample](https://developer.apple.com/documentation/arkit/displaying-a-point-cloud-using-scene-depth).
The local bundle format is defined by `AIInspector/Observation.swift`. The proposed [mobile ↔ orchestrator API contract](../docs/mobile-orchestrator-contract-v0.1.md) specifies endpoints, upload formats, instructions, retries and the team review checklist. It is pending agreement; no backend integration is claimed.
