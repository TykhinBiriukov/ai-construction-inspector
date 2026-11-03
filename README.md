# AI Construction Inspector

**Turn site photographs, depth captures and building plans into traceable inspection findings.**

AI Construction Inspector helps a construction team connect what was designed with what can actually be observed on site. In one workspace, a user can compare a photograph with a drawing, review a scan against a building information model (BIM), and explore construction progress over time. Findings stay connected to their source evidence; missing visibility remains a reason to gather more evidence.

| Project | Details |
|---|---|
| Event | [Meta Global AI Developer Hackathon](https://cerebralvalley.ai/e/meta-ai-developer-hackathon) |
| Track | Track 2 |
| Hackathon dates | 26 October–4 November 2026, as published by the organizer |
| Team development period | 27 October–3 November 2026 (implementation history in this repository) |
| Web demo | [Open AI Inspector](https://inspector.campuscompile.eu/demo) — explore three guided scenarios with prepared data; no phone or local installation needed for the walkthrough |
| Creators | Tykhin Biriukov, Egor Pichugin and Oleg Kulikov |
| Meta models in the implementation | Muse Spark 1.3, SAM 3.1 and Muse Voice Transcribe 1.0 |

The team knows this problem from work in construction. **Egor Pichugin** worked as a quality-control engineer and regularly inspected work on construction sites. **Oleg Kulikov** held an engineering management role and worked on acceptance of renovation and major refurbishment of premises. **Tykhin Biriukov** worked on solar-panel installation. That experience shaped the project around practical questions: which element needs attention, what evidence supports the finding, and what should be checked next?

[Demo walkthrough](#demo-walkthrough) · [Problem and value](#problem-and-value) · [Architecture](#architecture) · [Meta models](#how-we-use-meta-models) · [Service details](#service-details) · [Approach comparisons](#approach-comparisons) · [Reproducible setup](docs/reproducible-setup.md)

## Demo walkthrough

[Open the web demo](https://inspector.campuscompile.eu/demo), then choose **Compare the 3D model with site photos**, **Check installation against the drawing**, or **Review construction progress**. Each scenario opens prepared evidence and a retained result in the same workspace. The walkthrough demonstrates the user flow from source evidence to a reviewable result; it reuses saved computations rather than rerunning the full pipeline. For the exact steps and expected output, see the [reviewer quick start](ai_inspector/docs/guided-demo.md#reviewer-quick-start).

For a new inspection, the user works with project documents and photographs in one web workspace; the iPhone client adds RGB-D capture when depth is needed. Drawing/photo comparison does not require LiDAR, and a hosted user does not need to deploy the backend services.

This system works with more than ordinary chat attachments. A scan comparison needs RGB photographs, LiDAR depth and confidence, camera calibration and poses, capture metadata, and an IFC reference model. Progress monitoring needs overlapping dated surveys and a model with a work schedule. A visitor should be able to explore the product without first obtaining a compatible device, access to a construction site and its design files.

The demo uses prepared inputs and saved computation results. Start with the [versioned evidence cases](docs/evidence/2026-11-03/README.md): the primary case is a completed Geometry reconstruction from **99 real-room RGB-D observations**, with public numerical reports, source hashes and image-reference identities. The [evidence download guide](docs/evidence/2026-11-03/data-access.md) links the detailed experiment records and verification instructions. The complete [IFC/capture/mesh archive is available in the evidence release](https://github.com/TykhinBiriukov/ai-construction-inspector/releases/tag/evidence-2026-11-03).

| Case | Available evidence | What it establishes |
|---|---|---|
| Real-room scan versus BIM | Public comparison reports and hashes; downloadable archive holds 396 capture files, pinned IFC, point cloud, meshes and stage manifests | Completed Geometry computation; 6 object candidates, 5 observed surfaces and traceable source frames |
| Photograph versus drawing | Same-input legacy/staged reports; three frozen-checklist runs with audit diagnostics and evidence regions | Historical live model analyses, requirement reuse and retained uncertainty |
| Construction progress | Six dated development evaluation records and the saved seven-week walkthrough | Six dated monitoring records and a prepared seventh visualization scene |

The [case guide](docs/evidence/2026-11-03/README.md) connects source provenance, saved results, experiment records and reproduction instructions.

For the interactive workspace, follow the [guided demo](ai_inspector/docs/guided-demo.md). Selecting a saved scenario reads retained results; it does not perform a new reconstruction. Model follow-up questions and fresh analysis are separate paths. A reviewer can inspect the release package without a running service or model credentials.

## Problem and value

Inspection requires someone to reconcile drawings, revisions, models, site conditions and photographs. Fragmented evidence makes defects harder to identify, explain and revisit. Repeated manual comparison also consumes engineering time that could be spent resolving the actual issue.

PlanGrid and FMI's *Construction Disconnected* study estimated that poor project data and communication accounted for **$31.3 billion in U.S. construction rework in 2018**, or **48% of rework costs**. The study surveyed 599 industry participants. These historical industry estimates describe the scale of the problem. [Study, printed pages 4 and 19–20](https://www.autodesk.com/blogs/construction/wp-content/uploads/2023/03/Construction_Disconnected.pdf).

![2018 U.S. construction rework attribution: poor project data 22%, poor communication 26%, other factors 52%. These are industry estimates, not AI Inspector performance.](docs/assets/rework-context.svg)

*Source: [PlanGrid/FMI, Construction Disconnected (2018), printed page 20](https://www.autodesk.com/blogs/construction/wp-content/uploads/2023/03/Construction_Disconnected.pdf). The categories share one denominator: attributed U.S. rework costs.*

Professional mobile mapping already addresses reality capture. For example, [NavVis VLX 3](https://www.navvis.com/vlx-3) is a wearable system with LiDAR and cameras, while [Leica Pegasus:Backpack](https://leica-geosystems.com/ja-jp/about-us/news-room/customer-magazine/reporter-79/capturing-reality-on-the-go) illustrates backpack-based mapping. Our direction is to make evidence collection and inspection more accessible through LiDAR-equipped consumer devices and, later, autonomous capture equipment.

| Intended value | Mechanism in this project | How to evaluate it on real projects |
|---|---|---|
| Reduce repetitive comparison work | Drawing-derived checklists, reusable requirements and element-linked evidence | Engineer time per accepted finding, including correction and review |
| Make discrepancies easier to investigate | Source hashes, IFC identities, photos and measurements remain linked | Time to locate and verify the source of a finding |
| Avoid unsupported certainty | Visibility, correspondence and quality limits stay explicit | False findings, missed findings and useful requests for additional evidence |
| Make repeated surveys useful | Dated observations and a shared progress viewer | Agreement with independently reviewed site records |

The evidence cases demonstrate implemented workflows for locating source evidence, reusing inspection requirements and reviewing linked results.

## Architecture

Users interact with one **AI Inspector**. Internally, conversation, durable workflow ownership, numerical computation and evidence review have separate responsibilities.

**AI validation is implemented.** Geometry Inspector delegates candidate decisions to a separate Geometry Verifier agent, which reviews the pinned evidence and can request more data or reject an unsupported candidate. The staged Drawing/Photo workflow also reviews the draft against both original images while keeping the drawing-derived requirements fixed. Deterministic validation checks report identities, evidence references and the required schema. The model reviewers and deterministic checks form complementary validation layers.

```mermaid
flowchart TB
    User[User] --> Web[Inspector web workspace]
    Web --> Backend[Inspector API and durable session services]
    Backend <--> Bob[Main orchestrator: Bob]
    Bob <--> Spark[Meta Muse Spark 1.3]
    Phone[iPhone RGB-D capture] -->|Authenticated channel and upload completion| Backend
    Phone -->|Scoped signed uploads| Capture[(Private capture storage)]
    Backend -->|Validate and pin complete source plus IFC| Queue[Capture and review worker]
    Capture -->|Verified source reads| Queue
    Queue -->|One durable inspection operation| Geometry[Geometry Engine]
    Geometry <--> SAM[Meta SAM 3.1]
    Geometry --> Results[(Committed geometry artifacts)]
    Results -->|Verified manifests and bounded evidence| Queue
    Queue -->|Validated evidence batches| Bob
    Bob -->|Registered A2A task| GI[Geometry Inspector]
    GI -->|Scoped evidence read| Backend
    GI -->|Fixed A2A review hop| GV[Geometry Verifier]
    GI <--> Spark
    GV <--> Spark
    Bob -->|Predefined HTTP tool| Drawing[Drawing and photo service]
    Drawing <--> Spark
    Backend -->|Pinned IFC and session binding| Loc[Localization Service]
    Phone -->|Session-scoped spatial observations| Loc
    Surveys[Dated photo surveys plus scheduled IFC] --> Progress[Progress4D service]
    Progress <--> Spark
    Backend -->|Read existing mapped result| Progress
```

Arrows show the implemented service interfaces. Heavy reconstruction runs outside the chat request. Source code for all components is collected on `main`; deployment branches, configuration and credentials remain independently managed.

### Design choices and their purpose

| Engineering choice | Why it matters | Implementation to inspect |
|---|---|---|
| Durable commands before side effects | A lost connection can be reconciled using the same operation instead of creating duplicate work | [Agent harness](ai_inspector/src/ai_inspector/agent_runtime.py), [orchestrator](ai_inspector/src/ai_inspector/agent.py) |
| Explicit ownership and fenced leases | An expired worker cannot publish over the current owner after a restart | [Capture workflow](ai_inspector/src/ai_inspector/workflow.py), [Geometry API/worker](geometry-engine/geometry_engine_api) |
| Typed model output and predefined tools | Model suggestions are checked against schemas, permissions and task scope before execution | [Model client](ai_inspector/src/ai_inspector/model_client.py), [inspector registry](ai_inspector/src/ai_inspector/inspectors.py) |
| Immutable inputs and content-addressed evidence | A finding can be traced to a specific drawing, capture, IFC and successful processing attempt | [Geometry handoff](ai_inspector/docs/capture-to-geometry.md), [workspace](ai_inspector/src/ai_inspector/workspace.py) |
| Separate computation and interpretation | Geometry produces numerical evidence; language models explain and review bounded evidence | [Geometry pipeline](geometry-engine/geometry_engine/pipeline.py), [review contract](ai_inspector/docs/ifc-discrepancy-review.md) |
| Small, versioned service boundaries | Components can use different native dependencies and independent persistence without coupling their lifecycles | [A2A protocol](ai_inspector/src/ai_inspector/a2a_protocol.py), [component setup](docs/reproducible-setup.md) |
| Evidence-aware presentation | Unknowns, partial visibility and synthetic origin survive into the user's result | [Geometry viewer contract](ai_inspector/docs/capture-to-geometry.md#inspection-results-viewer), [drawing/photo schemas](drawing_photo_service/models.py) |
| Metadata-only observability | Latency, attempts, usage and prompt versions can be examined without automatically exporting source images or chat text | [Observability](ai_inspector/src/ai_inspector/observability.py), [Langfuse guide](ai_inspector/docs/agent-chat.md#langfuse-cloud-traces-prompts-and-evaluation) |

These choices address concrete failure modes: mobile disconnects, long reconstruction jobs, uncertain network outcomes and malformed model output. Durable ownership, idempotent operations and versioned evidence make failures recoverable and findings auditable.

## How we use Meta models

Meta models are part of the actual processing paths, including orchestration, visual interpretation, segmentation and speech-to-text. The interfaces below are documented in Meta's [model catalog](https://dev.meta.ai/docs/models) and [SAM 3.1 API guide](https://dev.meta.ai/docs/sam/overview); the table identifies the capabilities this project actually uses.

| Component | Model and interface | Model responsibility | Responsibility retained by code |
|---|---|---|---|
| Main orchestrator and Geometry review agents | `muse-spark-1.3`, Meta Model API Chat Completions | Understand the task, select permitted tools, inspect supplied evidence and explain accepted results | Authorization, bounded execution, request identity, numerical bindings and final report validation |
| Drawing/photo analysis | `muse-spark-1.3`, images, structured output and `crop_region` | Extract design requirements, inspect the corresponding photograph and review findings | Source-role restrictions, crop coordinate mapping, checklist identity and verdict-schema checks |
| Geometry segmentation | `sam-3.1`, Meta Model API Responses and `meta-sam-parser` | Produce masks for walls, floors, ceilings, windows, window frames and doors | RGB-D reconstruction, visible-triangle projection, fusion, rigid registration and measurements |
| Progress4D visual evidence | `muse-spark-1.3`, image input and JSON schema | Assess product evidence from selected views and distinguish visibility/material/installation claims | Camera reconstruction, IFC registration, visibility gates, temporal aggregation and forecast rules |
| Voice input | `muse-voice-transcribe-1.0`, Meta Model API `/asr/transcribe` | Turn a spoken inspection request into text | Push-to-talk capture, mono PCM WAV conversion, authentication, audio validation and insertion into an editable message draft |

The [shared harness](ai_inspector/src/ai_inspector/agent_runtime.py) freezes each model request before network I/O; the [Meta adapter](ai_inspector/src/ai_inspector/model_client.py) validates returned messages and exposes safe errors. The [SAM adapter](geometry-engine/geometry_engine/segmentation/sam_backend.py) uses hosted inference with bounded responses and the official decoder; no local SAM weights or NVIDIA GPU are required for that path. See also the [drawing/photo client](drawing_photo_service/engine.py), [Progress4D perception](progress4d_service/perception.py), and the [voice transcription adapter](ai_inspector/src/ai_inspector/voice.py).

Models operate through scoped, predefined tools. Numerical geometry comes from the reconstruction and comparison services; Muse Spark reviews and explains the resulting evidence. Voice transcription converts speech into an editable draft, with audio handled in memory.

## Service details

### 1. Inspector backend, orchestrator and web workspace

**Role.** Own user chats, capture assignments, accepted sources, Geometry operations and the presentation of results. Native execution uses FastAPI/Pydantic with SQLite by default and optional PostgreSQL/private remote storage; a separate Cloudflare deployment path is provided. The browser renders IFC, PDF and images using Three.js, web-ifc and PDF.js. Push-to-talk dictation uses Muse Voice Transcribe 1.0 to insert spoken requests into an editable draft before sending.

**Agent execution.** Bob is the internal main orchestrator. Each registered inspector has its own instructions, permitted tools, step budget, state and history through `AgentHarness`. Direct tools cover capture/session status, saved Geometry results, drawing/photo comparison and, for a mapped project, saved Progress4D results. Registration and selected files determine which tools are offered. The model cannot register arbitrary services or choose service endpoints.

**Recovery.** Chat/run history, pending model requests and planned tool commands are durable. Exact request IDs and bodies distinguish retry from new work. A completed request replays its result; an uncertain request keeps its operation identity. Model `tool_call_id` is not the backend operation ID. At most two invalid-argument repair attempts are allowed within the model-turn budget, and rejected calls never execute.

**Capture lifecycle.** The server generates session IDs. Assignment, device presence, acknowledgement, camera start, accepted source and Geometry completion are separate facts. A tab closing or phone going offline does not cancel the session. A new explicitly requested capture after sealing creates a linked session. Upload safety and source validation precede dispatch; only the existing capture worker creates the initial Geometry operation.

**User workspace.** Files, selections, plans and observations are scoped to the caller/chat. Originals and accepted history retain their identity. The viewer links geometry findings to IFC elements and image evidence; opening it does not trigger analysis. Typed response artifacts are rendered as data, not model-supplied executable HTML. Manual work-plan confirmation remains an operator action.

**Inspect:** [backend setup](ai_inspector/README.md) · [agent contract](ai_inspector/docs/agent-chat.md) · [API](ai_inspector/src/ai_inspector/api.py) · [web contract](ai_inspector/docs/web-integration.md) · [viewer](ai_inspector/viewer/README.md).

### 2. Geometry Inspector and Geometry Verifier

**Role.** Geometry Inspector owns the geometry/BIM inspection task. Geometry Verifier is its narrower verification sub-agent, not another inspector directly reporting to Bob. Both run as independent native A2A HTTP+JSON services with their own task stores and workers.

**Evidence flow.** The capture worker validates compact Geometry results and prepares bounded candidate batches. Geometry Inspector can read only the pinned evidence granted to its task and can delegate only to its configured verifier. The verifier is tool-free. It returns a typed decision over the supplied candidates; deterministic code checks case IDs, source/evidence hashes and measurement bindings before assembling the report. Bob checks the accepted report again before using it.

**Versioning.** Default profiles are version `3`; new Geometry reviews use evidence projection `2` over compact result `2.0`. Legacy requests retain their original evidence and replay behavior. Bob→Inspector registration, Inspector→Verifier registration and live agent cards must agree independently.

**Recovery and limits.** Separate persistent task stores, frozen child requests, bounded polling and lease fencing permit remote work to continue without the browser. Default model budgets are 12 turns for Geometry Inspector and 3 for the verifier. Waiting for the verifier is bounded by 120 polls or 10 minutes.

**Inspect:** [roles, schemas, bindings and native setup](ai_inspector/docs/ifc-discrepancy-review.md) · [service factories](ai_inspector/src/ai_inspector/discrepancy_agents.py) · [report schemas](ai_inspector/src/ai_inspector/discrepancy.py) · [evidence adapter](ai_inspector/src/ai_inspector/discrepancy_evidence.py).

### 3. Geometry Engine

**Role.** Reconstruct metric observed geometry from RGB-D capture and compare it with an IFC design. This is a computation service; it does not make the final inspection decision.

```mermaid
flowchart LR
    A[RGB-D and original ARKit poses] --> B[CPU TSDF reconstruction]
    B --> C[Mesh cleaning and frame selection]
    C --> D[Hosted Meta SAM masks]
    D --> E[Depth-supported projection and multiview fusion]
    E --> F[Observed opening contours]
    F --> G[Rigid IFC registration]
    G --> H[Surface and opening differences]
    H --> I[Compact 2.0 evidence and optional viewer]
```

**Geometry.** Open3D TSDF integrates depth using the original ARKit poses. The default voxel size is 0.02 m and truncation is 0.08 m for TSDF integration. Camera adjustment for projecting masks leaves mesh vertices fixed. Masks are projected onto visible, depth-supported triangles and fused across views; opening candidates come from mesh contours and their wall interfaces.

**Registration and comparison.** Dominant normals and four yaw hypotheses initialize robust point-to-plane ICP with a Tukey loss. Registration is rigid with scale fixed to one: the scan is not resized or reshaped to match the design. Observable directions, gravity agreement and competing poses contribute to rejection of weak or ambiguous alignment. The registration path targets predominantly orthogonal rooms. Outputs include matched IFC IDs, dimensions, displacement and partial surface deviations.

**Research basis and project adaptation.**

| Method and primary source | How this implementation uses it |
|---|---|
| Volumetric depth fusion: [Curless and Levoy, SIGGRAPH 1996](https://graphics.stanford.edu/papers/volrange/), [Open3D TSDF integration](https://www.open3d.org/docs/latest/tutorial/t_reconstruction_system/integration.html) | Open3D's CPU voxel integration with ARKit input adaptation, confidence filtering and source provenance |
| Robust point-to-plane alignment: [Open3D robust kernels](https://www.open3d.org/docs/latest/tutorial/pipelines/robust_kernels.html) | Tukey weighting reduces outlier influence; project code adds bounded yaw hypotheses and alignment rejection checks |
| Cross-view mask grouping: [Open3DIS, CVPR 2024](https://arxiv.org/abs/2312.10671) | Adapts depth consistency and region-overlap grouping to CPU mesh-face areas, with per-frame vote caps and semantic conflict checks; does not run the full learned Open3DIS pipeline |

Exact upstream code references and licenses are retained in [Geometry method notices](geometry-engine/THIRD_PARTY_NOTICES.md).

**Durability.** `POST /inspections` returns `202` after persisting the job. PostgreSQL owns jobs, stage attempts and leases; large artifacts live in Storage. Each successful stage commits an immutable manifest with hashes and sizes. A restarted worker restores verified completed stages into a fresh workspace. Transient failures have bounded retries; failed or partial uploads do not become checkpoints. Viewer export is separate and can reuse completed computations.

**Efficiency.** SAM inference is cached by exact image, prompt, model/decoder identity and preprocessing version. Geometry settings or a different IFC can reuse unchanged 2D masks while recomputing dependent stages. Each prompt is cached independently, including verified empty results. A new comparison uses a new operation identity and retains previous results.

**Output meaning.** Compact `2.0` lengths are metres and differences are `scan − IFC`. Unknown measurements stay `null`. Unobserved and unavailable-reference objects remain distinct from absence or successful matches. Processing tolerance, compared area and quality flags accompany each result. The Geometry API operates behind a trusted single-tenant service boundary.

**Inspect:** [service/API/setup](geometry-engine/README.md) · [reconstruction](geometry-engine/geometry_engine/reconstruction/service.py) · [registration](geometry-engine/geometry_engine/comparison/bim_registration.py) · [mask fusion](geometry-engine/geometry_engine/segmentation/mask_fusion.py) · [opening contours](geometry-engine/geometry_engine/openings/mesh_boundaries.py) · [Inspector acceptance](ai_inspector/docs/capture-to-geometry.md).

### 4. Drawing/photo analysis service

**Role.** Compare one explicitly supplied 2D drawing or PDF page with one site photograph for a concrete user task. Inputs are selected images or rendered PDF pages, paired with the user's inspection task.

**Two implemented workflows.** `legacy` analyzes both sources in one conversation and reviews evidence crops in that history. `staged` separates requirement extraction, photo assessment and final review into fresh model contexts:

```mermaid
flowchart LR
    subgraph Legacy[Legacy: shared model history]
        direction TB
        LInputs[Drawing and current photo] --> LAnalysis[Joint analysis]
        LAnalysis --> LReview[Evidence crop review in the same history]
        LReview --> LReport[Findings and evidence]
    end
    subgraph Staged[Staged: separate model contexts]
        direction TB
        Drawing[Drawing only] --> Checklist[Requirement extraction]
        Checklist --> Snapshot[Validated reusable checklist]
        Snapshot --> Photo[Checklist and current photo assessment]
        Photo --> Review[Fresh review with both originals and draft]
        Review --> Report[Findings by stable requirement ID]
        Saved[Compatible saved checklist] -.->|New photo: skip extraction| Photo
    end
```

**Checklist as a source artifact.** The actual schema is [`Checklist` / `ChecklistItem`](drawing_photo_service/staged.py). It permits up to 12 atomic requirements with stable `R01`-style IDs. Photo and review phases must preserve each requirement's ID, category, criterion and design claim exactly.

| Category | What a requirement may establish | Evidence boundary |
|---|---|---|
| `geometry` | Visible presence, count, position or relationships | Visual similarity does not identify a material or manufacturer |
| `material` | An explicitly specified material class | Drawing annotation and appropriate observable evidence are required |
| `product` | A specified manufacturer, model or grade | Similar shape alone is insufficient |
| `measurement` | A dimensioned quantity with units | A photograph without a reliable scale cannot verify it |
| `installation` | A specified process or interface | Concealed workmanship may be unverifiable in the supplied view |
| `performance` | A specified functional/test property | Appearance alone cannot replace the relevant test evidence |

**Illustrative checklist: window detail.** Assume a drawing explicitly shows the following requirements. Each row in this illustrative example assesses one property:

| ID / category | Requirement extracted from the drawing | Evidence in the current photo | Result |
|---|---|---|---|
| `R01` / `geometry` | A continuous strip spans the lower frame-to-wall interface | The corresponding strip is visible continuously across the entire required span | `match` for visible continuity only |
| `R02` / `material` | The strip is specified as a waterproof membrane | The strip is visible but its composition cannot be identified | `inconclusive`; appearance does not verify material |
| `R03` / `installation` | The membrane is bonded behind the frame | The bonded interface is concealed | `inconclusive`; concealed is not missing |
| `R04` / `measurement` | The overlap meets the dimension printed on the drawing | The photo has no reliable scale | `inconclusive`; request measurement evidence |

A `mismatch` requires a visible contradiction at the corresponding location. The saved source artifact contains each requirement's ID, category, criterion, design claim, drawing evidence regions and limitations; the [schema and phase prompts](drawing_photo_service/staged.py) define the complete format. The snapshot binds the drawing hash, page, dimensions, task context and named views. A subsequent photo can reuse it without inheriting earlier photo observations. The Inspector adapter selects a compatible checklist from the same chat; changing the scope requires an explicit new checklist.

**Grounding and uncertainty.** Models refer to named A–J/1–10 grid views. Server code maps crop references back to the original image rather than trusting model-generated exact pixel coordinates. A finding carries design and photo claims, correspondence, visibility, evidence regions, verdict, limitations and questions. Uncertain correspondence or inadequate visibility cannot produce a conclusive verdict. A fresh model context performs the final review against both source images and the frozen requirements.

**Execution.** Both modes use a 240-second budget checked between model calls and at most 9 model calls; staged allows at most 3 per phase, including crop/repair calls. Requests, originals, reports and audit are persisted in SQLite or a separate PostgreSQL schema. Exact replay retains the task ID/body. Async admission and durable Inspector polling connect the service to the chat workflow; the [service guide](drawing_photo_service/README.md) documents execution states and recovery.

**Inspect:** [service and API](drawing_photo_service/README.md) · [legacy engine](drawing_photo_service/engine.py) · [staged prompts and checklist](drawing_photo_service/staged.py) · [grounding](drawing_photo_service/grounding.py) · [admission/persistence](drawing_photo_service/app.py) · [Inspector adapter](ai_inspector/src/ai_inspector/drawing_photo.py).

### 5. Construction monitoring / Progress4D

**Role.** Convert dated overlapping photo surveys into reconstructed views and product-level progress evidence against an IFC work schedule. This is a separate photo-only path; it does not require the LiDAR Geometry pipeline.

**Processing.** PyCOLMAP reconstructs camera poses and sparse points. Permanent IFC walls/slab provide metric registration; scheduled products are excluded from alignment targets. The configurable OpenMVS CPU path creates a dense mesh with photographic textures. IFC geometry is not substituted for observed surfaces. Selected view evidence is assessed by Muse Spark, then deterministic temporal logic produces the saved timeline, counts, activity and conditional forecasts.

**Scope.** One axis-aligned rectangular room; 8–100 overlapping upright photographs from one camera with fixed zoom and equal dimensions; timezone-aware dates; one IFC schedule with explicit tasks, products and finish-start dependencies without lag. Quantities count identified products, not measured areas or volumes. Hidden work retains its evidence date and uncertainty. Forecasts based on assumptions are labeled separately from rates computed from fully observed counts.

**Variants.** The original path produces sparse/stereo points using PyCOLMAP and OpenCV StereoSGBM. With `PROGRESS4D_OPENMVS_BIN`, the service runs OpenMVS 2.4.0 for dense reconstruction and textured GLB output. Evidence version `2` uses independent, larger two-view cards, frontal-view ranking and separate material/installation fields.

**Method references.** [COLMAP's documentation and research references](https://colmap.github.io/) describe the camera/sparse reconstruction foundation. [OpenCV StereoSGBM](https://docs.opencv.org/4.x/d2/d85/classcv_1_1StereoSGBM.html) documents the stereo matcher used by the point path; [OpenMVS 2.4.0](https://github.com/cdcseacave/openMVS/blob/v2.4.0/README.md) documents dense reconstruction and texturing from calibrated cameras and sparse points. IFC registration, product evidence, temporal aggregation and the chat bridge are project-specific layers.

**Durability and integration.** A SQLite queue persists immutable submissions and attempt history; one API process owns a data directory and manages reconstruction child processes. The native Inspector `progress4d_result` tool reads an already processed project bound to that chat by server configuration. Its same-origin bridge checks access and forwards bounded artifacts without exposing service credentials. The native backend provides the documented Progress4D integration.

**Inspect:** [service, capture constraints and native setup](progress4d_service/README.md) · [pipeline](progress4d_service/pipeline.py) · [surface adapter](progress4d_service/surface.py) · [visual assessment](progress4d_service/perception.py) · [temporal logic](progress4d_service/temporal.py) · [chat/viewer bridge](ai_inspector/docs/progress4d.md).

### 6. iPhone capture application

**Role.** Acquire observations and deliver them with their calibration and identity. The SwiftUI/ARKit app targets iOS 17. RGB-only observations are possible on iPhone 11; depth-dependent workflows require a compatible LiDAR device.

Each observation retains RGB, depth/confidence when available, frame metadata, camera intrinsics and pose. Independent ARKit segments preserve tracking context. The phone initiates its authenticated channel, receives an assignment, and uploads through scoped signed permissions. Device credentials stay in Keychain. Retry journals retain exact operation identity, and upload failure does not delete local originals.

Inspector owns assigned inspection sessions; a separate standalone mode supports capture testing. The app builds with macOS/Xcode and uses a physical iPhone for ARKit capture.

**Inspect:** [iOS setup](ios/README.md) · [server integration](docs/inspector-server-integration.md) · [implementation and pending checks](docs/mobile-integration-status.md) · [capture format](ai_inspector/docs/zip-capture.md).

### 7. Localization Service

**Role.** Estimate the device/capture position relative to a supported room and provide spatial evidence. Inspector hands off the pinned IFC and session binding through a dedicated server credential; the phone receives a session-scoped token for spatial observations and poses. The cloud implementation persists state under its own restricted PostgreSQL role/schema.

**Supported solver.** A restricted IFC importer accepts an axis-aligned rectangular room with four walls, a floor and a space in metre units, including supported door/window openings. The selected entry door establishes the room frame. Depth backprojection, gravity-aligned plane fitting and bounded rigid registration use observed geometry. Coverage records depth support and observation provenance.

**Inspect:** [current cloud protocol and constraints](localization_service/deploy/README.md) · [cloud API](localization_service/cloud_server.py) · [registration](localization_service/registration.py) · [Inspector adapter](ai_inspector/src/ai_inspector/localization.py) · [original lab experiments](localization_service/README.md).

## Approach comparisons

The project combines implementation with exploration of alternative pipelines. The following tables compare **mechanisms and tradeoffs** for the stated inputs and recovery needs. Linked studies provide numerical results and reproduction protocols.

### Geometry: moving from experiments to the current measurement path

| Area | Earlier exploration described in project history | Current implemented path | Engineering purpose |
|---|---|---|---|
| Reconstruction | Pose refinement, ICP/pose-graph experiments | Original ARKit poses plus TSDF | Preserves recorded camera estimates |
| Structural representation | Wall labels with plane-fitting experiments | Mesh labels with visible, depth-supported multiview fusion | Retains observed irregular geometry and per-view depth support |
| Opening boundaries | VLM selection, numbered image lines and manual-window experiments | Mesh contours and wall-interface support followed by IFC matching | Bases dimensions on geometry with contour and alignment quality gates |
| Repeated work | Local stage execution/resume | Durable stage manifests and exact-input SAM cache | Reuses matching inference results and restores completed stages after worker restart |

The [workflow evolution study](docs/evidence/2026-11-03/workflow-evolution.md) links the actual historical commits and reports a paired projection result on the real-room case: mean per-frame median residual fell from **10.99 to 7.93 mm on the same 68 accepted frames**, with 9/77 views rejected. This measures projection consistency against the retained mesh. See the study for the protocol, complete per-frame data and numerical ground-truth experiments. The analysis runs on GCP, separately from the live services.

A [controlled numerical comparison with four figures](docs/evidence/2026-11-03/controlled-comparison.md) adds known ground truth: in 12 varied room cases, mean camera translation error falls from **70.16 to 5.07 mm** with the current policy; in 18 two-sided-wall cases, camera-side matching reduces incorrect displacement measurements from **9/18 to 0/18**. The full 26-case pose study also shows where ungated correction is more accurate and where the current policy rejects uncertain or excessive corrections. The controlled experiments use generated geometry with known ground truth.

Historical methods and implementation references are documented in the [Geometry README](geometry-engine/README.md), [method notices](geometry-engine/THIRD_PARTY_NOTICES.md) and [workflow evolution study](docs/evidence/2026-11-03/workflow-evolution.md).

### Drawing/photo: shared history versus staged requirements

| Dimension | `legacy` | `staged` |
|---|---|---|
| Initial model context | Drawing and photo together | Drawing alone builds requirements |
| Photo judgment | Developed in the shared history | Fresh context receives the frozen checklist and current photo |
| Final review | Same history plus evidence crops | Fresh context with both original sources and the draft |
| Repeat photo | Repeats the joint workflow | Can reuse the bound checklist and skip requirement extraction |
| Requirement categories | General finding structure | Stable IDs and six explicit property categories |
| Execution structure | Shared analysis history | Separate stages, structured handoffs and schema checks |

The improvement claimed is **explicit separation and reuse of requirements**. The configured Inspector default remains `legacy` unless `INSPECTOR_DRAWING_PHOTO_WORKFLOW=staged` is set.

### Progress4D: geometry output and evidence resolution

```mermaid
flowchart LR
    Photos[Overlapping survey photos] --> SfM[PyCOLMAP camera and point reconstruction]
    SfM --> Points[Legacy: sparse and stereo points]
    SfM --> Dense[Configured: OpenMVS dense surface and texture]
    Points --> Evidence[Selected product views]
    Dense --> Evidence
    Evidence --> V1[Version 1: compact contact sheets]
    Evidence --> V2[Version 2: independent larger cards]
    V1 --> Timeline[Validated states and dated timeline]
    V2 --> Timeline
```

| Choice | Earlier/default option | Configured option | What changes |
|---|---|---|---|
| Reconstruction output | Sparse/StereoSGBM points | OpenMVS dense textured surface | A more detailed observed surface; native binaries and additional CPU work are required |
| Two-view evidence | Two 320×320 view cells; up to 12 products/request | Version 2: two 640×940 cells; up to 4 products/request | Larger independent evidence cards and smaller batches, with additional image/context cost |
| Judgment structure | Presence state and reason | Separate observed surface, material verification and installation state | Makes unsupported material/installation claims easier to identify |

OpenMVS preserves observed coverage with hole closing and artificial tower points disabled. Versioned configuration, input hashes and synthetic/site labels accompany comparisons.

### Recorded comparisons

The [saved same-input drawing/photo comparison](docs/evidence/2026-11-03/README.md#same-input-legacystaged-run) supplies per-run time, model calls and judgments. Geometry studies include paired real-room projection diagnostics and controlled numerical cases. Input identities, model/prompt versions and detailed records are linked from the [evidence guide](docs/evidence/2026-11-03/README.md).

## Reproducible setup

Start with the [step-by-step setup guide](docs/reproducible-setup.md). It covers source revision, prerequisites, separate environments, where each key belongs, startup order, local ports, expected checks and the inputs needed to exercise each workflow.

| Goal | Start here | Additional requirements |
|---|---|---|
| Open the workspace and use chat | [Inspector](ai_inspector/README.md) | Python 3.12+, operator/device configuration and a working Meta key for chat |
| Compare a drawing and photo | [Drawing/photo](drawing_photo_service/README.md) | Separate minion process/environment, service token, Meta access, selected input pair |
| Process a fresh RGB-D capture | [Geometry](geometry-engine/README.md) | Complete capture, pinned IFC, private Storage, PostgreSQL, hosted SAM access and API/worker |
| Review Geometry evidence | [Two review agents](ai_inspector/docs/ifc-discrepancy-review.md) | Separate task stores, caller credentials, matching profile registrations and Meta access |
| Explore construction progress | [Progress4D](progress4d_service/README.md) | Scheduled IFC, dated overlapping photos, native dependencies; OpenMVS for textured output |
| Capture on a phone | [iOS](ios/README.md) | macOS/Xcode, real compatible iPhone, configured Inspector and upload storage |
| Use the restricted spatial solver | [Localization](localization_service/deploy/README.md) | Supported IFC, service/database setup and session-bound phone integration |

Each service has its own environment and scoped credentials. The [Geometry evidence guide](docs/evidence/2026-11-03/README.md) links compact summaries and manifests; the [data access guide](docs/evidence/2026-11-03/data-access.md) lists the versioned Release packages and their contents. Deployment configuration is documented in the component guides.

## Verification

Source and maintained contracts are linked above. Generated `/docs`, `/openapi.json` and `/contracts` describe the service schemas. Component tests cover deterministic behavior and service contracts; the [evidence cases](docs/evidence/2026-11-03/README.md) document recorded model runs and numerical experiments. See [backend verification](ai_inspector/README.md#verification) for the test workflow.

## Component and license references

The implementation uses existing numerical and visualization libraries alongside Meta models. Component dependencies and notices are authoritative: [Geometry dependencies](geometry-engine/pyproject.toml) and [third-party notices](geometry-engine/THIRD_PARTY_NOTICES.md), [Inspector dependencies](ai_inspector/pyproject.toml), [document viewer licenses](ai_inspector/viewer/README.md), [drawing/photo dependencies](drawing_photo_service/requirements.lock), and [Progress4D native dependencies and OpenMVS AGPLv3 notice](progress4d_service/README.md#native-surface-reconstruction). This README does not grant a different license to those dependencies.
