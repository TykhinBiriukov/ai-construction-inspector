# Live BIM localization and capture coverage — proposal v0.1

28 October 2026. **Draft for Inspector, localization and iOS owners; not an implemented API.**
Oleg approved the product flow: start from a known BIM door, localize from room
geometry without a marker, then show camera direction and capture coverage live.
Endpoint names and schemas below are proposals requiring owner agreement.

Existing mobile protocols on the iOS branch: [server integration](inspector-server-integration.md),
[current transport and final ZIP](mobile-integration-status.md).

## 1. Scope and responsibilities

- iPhone collects RGB, optional LiDAR depth/confidence, camera calibration and pose;
  displays instructions and localization status. It does not align to BIM.
- Inspector owns assignment, authorization, early evidence validation/storage,
  Localization Service dispatch, durable state and operator/device delivery.
- Localization Service (proposed separate microservice) owns geometric localization,
  ambiguity checks, camera-to-BIM transformation and coverage calculation.
- Existing Geometry Service retains post-capture reconstruction and its current
  input contract. This proposal does not change its ZIP format or require it to
  process early observations. The current final Inspector ZIP flow also stays intact.
- Inspector is the LLM coordinator. Its API layer may authenticate and relay data,
  but pose updates and geometry calculations never require an LLM call.
  Localization Service authors and controls its own user-facing localization
  instructions. The API relays these unchanged; the Inspector LLM waits for
  localization completion and does not rewrite or approve each instruction.
- Viewer displays camera/frustum and coverage; inspection compliance is separate.
- First acceptance target: one real room, known door, LiDAR phone, real BIM geometry.
  RGB-only capture stays supported; RGB-only localization is not promised by this MVP.
- A stored BIM upload is insufficient: Localization Service needs parsed geometry, stable element
  IDs, units and a pinned model revision. Capture-only/unbound sessions cannot localize.

## 2. User flow

1. Inspector assigns an inspection task. Phone receives the assignment through the
   existing device channel and displays it; the user opens the camera. Notification
   delivery does not imply APNs support in the current Personal Team build.
2. After acknowledgement/capture-start, if the task has a usable pinned BIM model,
   control passes to Localization Service. It tells the user directly through the
   app: face the known door, show the floor, show the adjacent corner. Inspector
   waits; it does not generate intermediate localization instructions.
3. Phone uploads requested observations early through existing observation_files
   signed upload plans and declares evidence batches ready. The API validates/pins
   bytes and relays them without an LLM call. One observation is one photo/metadata.
4. Localization Service requests additional views until the alignment is validated.
   Ambiguous openings do not produce an arbitrarily chosen pose. It reports
   completion to both the app and Inspector, which resumes inspection guidance.
5. During inspection, Localization Service keeps processing camera telemetry and
   coverage in parallel. The same spatial state is available to the phone and
   Inspector UI; the LLM receives meaningful changes, not every camera pose.
6. If alignment is lost, pause the current Inspector instruction and let Localization
   Service guide recovery. Resume that instruction after validated recovery.
7. Without BIM, skip localization and continue ordinary Inspector-guided capture.
   A supplied but unprocessable BIM is an explicit blocked/unsupported state, not
   a silent no-BIM success. Final ZIP/completion remains unchanged.

### Instruction ownership and handoff (normative product flow)

The durable spatial snapshot contains `guidance`:

```json
{
  "phase":"localizing",
  "owner":"localization",
  "control_revision":3,
  "active_instruction_id":"55555555-5555-4555-8555-555555555555",
  "paused_inspector_instruction_id":null,
  "reason":"initial_alignment"
}
```

| Phase | Instruction owner | Transition |
|---|---|---|
| awaiting_camera | inspector | Camera starts: localizing with BIM, inspecting without BIM |
| localizing | localization | Validated alignment: inspecting; failure: blocked |
| inspecting | inspector | Lost alignment/reset with BIM: relocalizing |
| relocalizing | localization | Validated recovery: inspecting, resume paused task |
| blocked | localization | Explicit retry: localizing/relocalizing; cancellation: finished |
| finished | none | No further guidance; final capture lifecycle is authoritative |

Only one active instruction is shown. The API durably commits ownership changes
using expected `control_revision`; authenticated service callbacks include stable
operation ID, session/segment/model identity and alignment revision. Identical
replays are idempotent; stale/conflicting updates return 409. Only validated
Localization results can release the localization gate. Inspector cannot bypass it
by emitting another inspection instruction. Session cancellation takes precedence.

Instructions carry `source=localization|inspector`, `control_revision`, session and
segment scope, ID, text and status (active/paused/satisfied/cancelled). New Inspector
instructions cannot displace localization guidance; a paused instruction is restored
only if still applicable and not cancelled. The phone routes responses by source/ID;
transport through the shared API does not make the LLM the instruction author.

A `spatial.updated` event announces the committed snapshot revision to both consumers.
On reconnect, fetch authoritative state before showing instructions; do not replay
obsolete prompts. Background monitoring here means parallel work while the camera
and app are active, not guaranteed suspended-iOS execution. Failure keeps the gate
blocked with an explicit retry/cancel choice; raw capture may still be preserved
without claiming localization or completed BIM inspection.

## 3. Negotiation and pinned localization context

Proposed capability under GET /v1/capabilities:

```json
{"live_localization":{"protocol":"live_bim_v1","enabled":true,"pose_hz":5,"stale_after_ms":2000,"max_batch_observations":10,"rgb_only_localization":false}}
```

Missing/disabled capability means existing ZIP-only operation. Current device WSS
accepts only ping/pong; **do not send pose messages to the existing implementation**.
Server and client must both support the extension before enabling it.

Proposed new GET `/v1/sessions/{session_id}/spatial-state` returns a durable snapshot:
`protocol`, `session_id`, increasing `revision`, `context`, `alignments`,
`instructions`, `guidance`, `coverage_ref`. It is separate from the existing strict Session model.
Context (operator-created, pinned before capture):

```json
{
  "model_version":"bim-revision-7",
  "model_sha256":"MODEL_BYTES_SHA256",
  "room_id":"room-204",
  "entry_element_id":"BIM_DOOR_ID",
  "starting_side":"inside_room",
  "bim_frame_id":"bim-revision-7-local-meters",
  "units":"m",
  "coordinate_convention":"right_handed_y_up",
  "model_to_bim_transform_ref":"SERVER_OWNED_TRANSFORM_REF",
  "initial_instruction":"Stand inside the room facing the entry door. Capture the opening, floor and adjacent corner."
}
```

Strings in uppercase are placeholders, not valid production IDs/hashes. The server
must validate the door belongs to the selected room/revision. Normalize original
BIM axes, units and large survey offsets once on the backend; retain the reversible
source-to-working-frame transform. Viewer geometry uses this same working frame.
A revision/context change requires a new session, not silent rebinding.

## 4. Early evidence (new endpoint; existing upload plans)

Upload originals as described in the current iOS guide. Preserve frame schema 0.2,
original unrotated RGB, its calibration, optional depth/confidence, exact bytes and
hashes. No preview resizing in this first increment. Uploads use returned signed
URLs only; device auth is sent to Inspector, never forwarded to storage.

POST `/v1/sessions/{session_id}/evidence-batches`:

```json
{
  "protocol":"live_bim_v1",
  "batch_id":"11111111-1111-4111-8111-111111111111",
  "purpose":"localization",
  "instruction_id":null,
  "segment_id":"22222222-2222-4222-8222-222222222222",
  "observations":[{
    "observation_id":"33333333-3333-4333-8333-333333333333",
    "files":[
      {"name":"frame.json","object_key":"RETURNED_FRAME_KEY","byte_length":2048,"sha256":"ACTUAL_FRAME_SHA256"},
      {"name":"rgb.jpg","object_key":"RETURNED_RGB_KEY","byte_length":123456,"sha256":"ACTUAL_RGB_SHA256"}
    ]
  }]
}
```

Example inventory is abbreviated RGB-only. LiDAR observations must include their
actual depth/confidence file refs. `purpose`: localization or coverage. Each batch
has one segment; all IDs and context must agree with frame metadata. `instruction_id`
references a spatial instruction or null; do not rewrite immutable frame.json to
attach a later instruction. Files must belong to this session's upload plans/prefix.

202 response: `protocol`, `session_id`, `batch_id`, `status=validating`.
GET `/v1/sessions/{session_id}/evidence-batches/{batch_id}` returns the same identity,
status (`validating|queued|processing|completed|rejected|failed`), nullable `error`
and nullable `alignment_id`. 200 on identical POST replay; 409 on changed body with
the same ID. Failed transient processing retries the same batch, not new evidence.
File validation failure requires correction under new observation/batch IDs.

Inspector verifies byte counts/hashes, metadata and context, then pins immutable
copies before dispatch. Localization Service reads these verified sources through a service
adapter; the phone never calls Localization Service. Internal Localization Service route names remain its
owner's choice. Device telemetry is relayed by the Inspector API layer directly
to this service; only meaningful state changes reach the LLM coordinator. Job input includes batch/context/segment IDs, source refs, purpose,
prior alignment if any; job output follows sections 5 and 7.

Early accepted observations remain in the final ZIP with the **same IDs and bytes**.
Final validation must reject cross-transport hash conflicts and omitted accepted
evidence for this MVP. Hash-identical duplicates are one observation, not double
coverage. The server must enforce this explicitly; current mixing support alone
is not proof of this new rule. Final ZIP is still required and remains self-contained.
After submitted/sealed/cancelled, reject new evidence; delayed job results cannot
reopen the session or replace final results. Early processing is provisional.

## 5. Alignment result and coordinate contract

One independent alignment per `segment_id`. ARKit reset/restart never inherits the
previous segment transform automatically. All transforms use metres, 4x4 matrices
serialized as rows and column-vector multiplication:

```text
p_bim = T_bim_arkit * p_arkit
T_bim_camera = T_bim_arkit * T_arkit_camera
```

`T_arkit_camera` is the existing frame `T_world_camera`, not its inverse. ARKit
camera forward is local -Z. Derive the frustum from original image size/intrinsics;
handle camera-vs-image axes explicitly. Portrait UI rotation is not a change to
stored camera calibration. Rigid transforms only after BIM unit normalization:
finite values, last row [0,0,0,1], orthonormal rotation with determinant +1.

Each spatial snapshot alignment contains:

| Field | Meaning |
|---|---|
| alignment_id, revision | Stable ID and monotonically increasing revision |
| segment_id, bim_frame_id, model_version | Exact coordinate/model scope |
| status | pending, needs_more_evidence, localized, lost, failed, unsupported |
| T_bim_arkit | 4x4 or null; only localized authorizes live rendering |
| evidence_observation_ids | Observations used, not guessed IDs |
| quality | overlap_fraction, residual_rmse_m, ambiguity_margin (nullable), policy_version |
| valid_from_sequence | First telemetry sequence covered by this revision |
| analysis_origin | live, recorded, synthetic; independent of capture_origin |
| error | null or code/message/retryable/details |

Quality thresholds and ambiguity metric are owned/versioned by Localization Service and must
be measured on fixtures and a real room. No universal invented “95% confidence”.
Do not force structural deviations to match BIM through nonrigid fitting.
When an alignment is refined, recompute dependent provisional coverage; preserve
provenance. Older results cannot overwrite newer revisions.

Localization Service creates and delivers needs_more_evidence instructions directly
through the API, without Inspector LLM mediation. Persist a spatial instruction with
`source=localization`, `control_revision`, `instruction_id`,
`segment_id`, `text`, nullable `target_element_id`, `status` (active/satisfied/cancelled).
Batch submission links the response. Voice can later render the same text; no audio
transport is required now. This supplements the current first-case null instruction.

## 6. Camera telemetry

Proposed phone-to-Inspector message on the existing authenticated device WSS:

```json
{
  "type":"camera.pose",
  "protocol":"live_bim_v1",
  "session_id":"44444444-4444-4444-8444-444444444444",
  "segment_id":"22222222-2222-4222-8222-222222222222",
  "sequence":42,
  "frame_timestamp_s":123.45,
  "tracking_state":"normal",
  "tracking_reason":null,
  "T_arkit_camera":[[1,0,0,0],[0,1,0,0],[0,0,1,0],[0,0,0,1]],
  "intrinsics":[[1000,0,960],[0,1000,720],[0,0,1]],
  "image_size":[1920,1440]
}
```

Illustrative calibration, not device defaults. Timestamp is ARFrame monotonic
seconds, not UTC; sequence increases across reconnects within a segment. Persist
its counter or create a new segment if continuity is lost. Tracking enum:
normal/limited/not_available; matrix may be null when unavailable. Report degradation
immediately. Keep existing ping/pong heartbeat separately.

Start at negotiated 5 Hz; bounded latest-value queue, no backlog replay. Server
checks assigned device/session, capturing state, increasing sequence, finite matrices
and known segment. Phone may send pose before localization; viewer must not place
it into BIM until localized. Never interpret telemetry as a saved photograph.

Server records receipt time. Viewer marks camera stale after negotiated silence,
on disconnect or non-normal tracking. Do not compare phone monotonic time with
server UTC. Recovery after lost tracking requires validated alignment/relocalization;
no green “live” icon based only on reconnect.

GET spatial-state includes `latest_pose` with server receipt time and sequence;
latest_pose does not advance durable spatial revision. Initial operator UI can poll
this endpoint at 5 Hz while visible (project-scoped operator auth); this avoids
inventing an unimplemented browser WSS endpoint. Device gets its own scoped access.
Durable alignment/instruction/coverage changes emit `spatial.updated` through the
existing event envelope/cursor; event contains session_id and spatial_revision,
then clients fetch snapshot. Telemetry does not consume durable replay cursors.

## 7. Coverage semantics

Track surface patches, not whole-wall green flags. Proposed `coverage_ref` points
to a project-authorized artifact endpoint with artifact ID, SHA256 and revision.
Artifact has model_version, bim_frame_id, partition_revision and patches:
`patch_id`, `element_id`, BIM-space polygon/triangles, state, observation_ids,
alignment_id/revision, method, quality_policy_version, analysis_origin.
States: unseen, preliminary, verified_capture. Current camera frustum is a separate
transient overlay, never accumulated as capture coverage.

Preliminary means an uploaded saved observation projects onto the patch according
to BIM; actual occluders may be unknown. Verified_capture requires processed evidence,
visibility/occlusion checks, acceptable distance/angle/image quality and a validated
alignment. Depth agreement may help but missing/invalid depth is not confirmation.
For first LiDAR implementation, depth-unsupported patches stay preliminary.
“Verified” means capture coverage, never construction compliance.

No arbitrary coverage percentage without a defined observable surface denominator.
Store evidence links and allow coverage downgrades on alignment revision, conflicts
or invalid evidence. Partial occlusion must leave the hidden patch unverified.

## 8. Authorization, retry and lifecycle

All new HTTP routes use existing scoped device/operator authorization. Service
results require service auth; device cannot submit its own accepted alignment.
No arbitrary storage URLs from clients. Reuse existing limits and signed-upload
renewal; negotiate early batch/telemetry limits before enabling. Standard API errors
retain code/message/retryable/details. Persist batches and original bytes locally.
Offline: keep capturing, show spatial feedback unavailable, upload/reconcile later.
On background: stop live indication; no APNs/background-execution promise.
Final completion is independent of successful localization: capture can be preserved
as unlocalized. UI must not claim BIM coverage or compliance in that case.

## 9. Implementation sequence and acceptance

1. Owners agree this extension, door/model identity, quality policy and Localization Service job
   adapter. Backend advertises capability only after routes/handlers exist.
2. Backend early verification/pinning and spatial snapshot; iOS durable early uploads
   and localization status. Fixture job explicitly labelled synthetic.
3. Real known-door localization on LiDAR; targeted extra-evidence loop.
4. Telemetry + web camera/frustum; then preliminary and evidence-verified coverage.
5. Phone BIM viewer and RGB-only localization are later independent increments.

Acceptance checks before calling this live:
- Handoff: Inspector assignment -> Localization guidance -> Inspector inspection;
  loss pauses/resumes the same task. Duplicate/out-of-order callbacks and reconnect
  never display two owners or revive cancelled instructions. No BIM skips the gate;
  invalid supplied BIM blocks it explicitly.
- Known rigid transform fixture proves translation/rotation/direction and intrinsics;
  real doorway test checks projected geometry against visible landmarks.
- Symmetric door, wrong room, incomplete floor/wall evidence -> no false localization.
- ARKit reset, tracking loss, offline return, delayed/out-of-order results -> no stale
  transform applied to a new segment; stale camera is visibly marked.
- Repeat early upload/batch after timeout -> same source/job, no duplicates; unauthorized
  device, changed hashes, wrong prefix, sealed session -> explicit rejection.
- Early + final ZIP has identical IDs/hashes; no lost files or duplicate coverage.
- Occluded wall stays unverified; merely aiming without saving produces no coverage.
- RGB-only phone can finish existing ZIP flow without fabricated depth/localization.
- Old server/no capability continues existing workflow; live extension stays disabled.

This document does not implement or validate these features. Localization Service algorithm,
thresholds and real-room accuracy remain to be agreed and tested.
