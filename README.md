# AI Construction Inspector

**A powerful multi-agent system for construction quality control and progress monitoring.**

**Delegate the evidence-gathering and analysis work of construction inspection to a coordinated AI system.** AI Construction Inspector connects people on site, project documents and specialized agents in one ambitious, practical product: identify discrepancies, review completed work and give managers structured evidence for the next decision.

**One business workflow, versatile inspection capabilities.** Drawing/photo analysis, scan-to-BIM comparison and progress review serve the same objective: control the quality and monitor the execution of construction work. Their different inputs let teams choose the evidence that fits the task, from a drawing and photograph to calibrated depth captures or repeated surveys.

**Sophisticated engineering behind a simple working interface.** The conversational workspace combines visual reasoning, metric reconstruction and a hierarchy of inspection agents. Users can submit project files, dictate a request and inspect findings beside their source evidence. The iPhone client adds assigned RGB-D capture for depth-based inspection; teams using the hosted workspace do not deploy its internal services.

**[Open the web demo](https://inspector.campuscompile.eu/demo)** · [Three-scenario walkthrough](ai_inspector/docs/guided-demo.md#reviewer-quick-start) · [Explore the evidence](docs/evidence/2026-11-03/README.md) · [Run the components](docs/reproducible-setup.md)

| Project | Details |
|---|---|
| Event | [Meta Global AI Developer Hackathon](https://cerebralvalley.ai/e/meta-ai-developer-hackathon) · Track 2 |
| Hackathon dates | 26 October–4 November 2026 |
| Team development period | 27 October–3 November 2026 (implementation history in this repository) |
| Meta models | **Muse Spark 1.3 · SAM 3.1 · Muse Voice Transcribe 1.0** |
| Creators | Tykhin Biriukov, Egor Pichugin and Oleg Kulikov |

[Demo walkthrough](#demo-walkthrough) · [Problem and value](#problem-and-value) · [Measured results](#measured-results) · [Architecture](#architecture) · [Meta models](#how-we-use-meta-models) · [Service details](#service-details) · [Approach comparisons](#approach-comparisons)

## Demo walkthrough

[Open AI Inspector](https://inspector.campuscompile.eu/demo) and select a scenario. Prepared inputs and saved computations make the product immediately explorable in the browser, without a phone or local installation.

| Choose | Explore | Product value |
|---|---|---|
| **Check installation against the drawing** | Drawing, site photograph and linked findings | Review the installation against its design requirements |
| **Compare the 3D model with site photos** | IFC model, reconstructed geometry, measurements and source images | Investigate differences in their spatial and visual context |
| **Review construction progress** | Dated observations and a seven-week walkthrough | Follow the relationship between planned work and observed progress |

The [reviewer quick start](ai_inspector/docs/guided-demo.md#reviewer-quick-start) provides the steps and expected output. Scenario selection opens retained results; fresh analysis and model follow-up questions use their respective processing paths.

For new work, the entry point matches the task: **drawing + photo** for visual inspection, **RGB-D capture + IFC** for metric comparison, and **dated photo surveys + scheduled IFC** for progress analysis. An existing calibrated RGB-D session can be reprocessed without another phone capture. The [public real-room package](https://github.com/TykhinBiriukov/ai-construction-inspector/releases/tag/evidence-2026-11-03) includes 99 observations, their 396 capture files, a pinned IFC, meshes and processing manifests.

## Problem and value

**Control work while it is happening, and put actionable evidence in the hands of the people making decisions.** A specialist-led site check can involve scheduling, travel and coordination before the inspection even begins. Meanwhile, managers need to decide whether work can advance, an installation needs correction, or a specialist should investigate further.

**AI Inspector is designed around a more responsive, agent-assisted workflow:** a person already on site supplies observations, the system processes the evidence, and a manager reviews the findings. Inspection requests, capture assignments, model analysis and evidence validation connect field observations to that decision. The commercial value is clear: reduce the wait for information, focus specialist attention where it matters and help teams address discrepancies before they affect subsequent work.

| Step | People and agents working together | Value for the construction team |
|---|---|---|
| Define the check | A manager supplies the task and relevant design documents | A clear inspection objective tied to the project |
| Collect site evidence | A person on site supplies photographs or uses the assigned iPhone capture session | Evidence collection can start with someone already at the location |
| Process and validate | Specialized workflows compare observations with requirements or geometry; model review and deterministic checks validate the findings | Structured analysis with traceable supporting evidence |
| Make the next decision | The manager reviews findings and decides on progression, correction or specialist involvement | An informed handoff between inspection and project execution |
| Repeat as work advances | New observations reuse applicable requirements and feed subsequent reviews | Consistent follow-up throughout the work |

PlanGrid and FMI's *Construction Disconnected* study estimated that poor project data and communication accounted for **$31.3 billion in U.S. construction rework in 2018**, or **48% of rework costs**, based on a survey of 599 industry participants. This quantifies the economic problem AI Inspector addresses. [Study, printed pages 4 and 19–20](https://www.autodesk.com/blogs/construction/wp-content/uploads/2023/03/Construction_Disconnected.pdf).

![2018 U.S. construction rework attribution: poor project data 22%, poor communication 26%, other factors 52%.](docs/assets/rework-context.svg)

*Source: [PlanGrid/FMI, Construction Disconnected (2018), printed page 20](https://www.autodesk.com/blogs/construction/wp-content/uploads/2023/03/Construction_Disconnected.pdf). Industry rework-cost estimates.*

| Who uses it | The job | What AI Inspector brings together |
|---|---|---|
| Quality-control engineer | Check installation against the design | Drawing-derived requirements, photo assessment and reviewed findings |
| Acceptance engineer | Explain and revisit a discrepancy | IFC element identities, measurements, original photographs and source references |
| Site engineer | Document the physical condition of a room | iPhone RGB-D capture, metric reconstruction and model comparison |
| Project manager | Review progress across repeated surveys | Dated evidence, scheduled IFC elements and a shared progress viewer |

**A compelling commercial opportunity: repeated inspection across the construction lifecycle.** The same project generates new photographs, design revisions, capture sessions and progress reviews. Reusable requirements and persistent evidence make that repetition valuable: teams can build on earlier work while preserving the source of each finding. Contractors and inspection teams are natural buyers because these workflows sit directly inside quality control, acceptance and project delivery. The business audience spans building contractors, renovation teams and infrastructure contractors: the shared task is documenting work, comparing it with project requirements and coordinating the next decision. Teams select the inspection capability and evidence format appropriate to each task.

**Accessible capture, powerful processing, one elegant workspace.** Drawing/photo inspection starts with documents and a photograph. Depth inspection adds a LiDAR-equipped iPhone and its calibrated observations. The product combines familiar collection tools with advanced geometry and AI review behind a single interface.

**Grounded in valuable, firsthand construction experience.** Egor Pichugin worked as a quality-control engineer performing site inspections. Oleg Kulikov worked in engineering management and acceptance of renovation and major refurbishment. Tykhin Biriukov worked on solar-panel installation. Their experience shapes the product around the questions practitioners actually ask: what needs attention, where is the evidence, and what should be checked next?

## Measured results

The implementation is accompanied by **real-room processing records, numerical comparisons and reproducible experiment artifacts**. These concrete results showcase the depth of engineering behind the product.

| Measurement | Recorded result | Evidence |
|---|---|---|
| Projection consistency on the real-room capture | Mean frame-median depth residual **10.99 → 7.93 mm (27.8% reduction)** on the same 68 accepted frames | [Paired real-capture analysis](docs/evidence/2026-11-03/workflow-evolution.md#2-paired-projection-comparison-on-the-real-capture) |
| Camera-pose recovery in 12 generated room cases with known ground truth | Mean translation error **70.16 → 5.07 mm (92.8% reduction)** | [Controlled numerical comparison](docs/evidence/2026-11-03/controlled-comparison.md) |
| Correct wall-side correspondence in 18 controlled cases | Incorrect displacement measurements **9/18 → 0/18** | [Wall-correspondence comparison](docs/evidence/2026-11-03/controlled-comparison.md) |
| Reuse of unchanged Meta segmentation inputs | Retained run records **462 SAM cache hits and 0 misses** | [Real-room run records](docs/evidence/2026-11-03/README.md) |

The real-room Geometry run integrates **99/99 RGB-D frames** and produces **469,270 surface points and 896,924 mesh triangles**, with source identities preserved through reconstruction, segmentation and comparison. The public result includes six object candidates and five observed surfaces. The [evidence guide](docs/evidence/2026-11-03/README.md) connects the capture, model provenance, processing history and findings; the [download guide](docs/evidence/2026-11-03/data-access.md) provides versioned records and integrity checks.

The drawing/photo evidence includes recorded live-model analyses, a same-input comparison of shared-context and staged workflows, and three runs with reusable frozen requirements. Progress4D includes six dated development evaluation records and a saved seven-week walkthrough. [Inspect the cases and reports](docs/evidence/2026-11-03/README.md).

## Architecture

**One cohesive product experience, a powerful multi-agent architecture underneath.** Bob, the main orchestrator, routes tasks through scoped tools and registered domain inspectors. Geometry Inspector owns the inspection task and delegates evidence validation to its own Geometry Verifier. Each agent has a separate context, instructions, tools and execution budget.

**Thoughtfully layered verification is built into the architecture.** The Geometry Verifier reviews pinned candidate evidence. Drawing/Photo's staged workflow uses a fresh model context to review the draft against both original images and fixed requirements. Deterministic checks validate identities, measurements, evidence references and schemas. This combines specialized model reasoning with explicit computational checks.

```mermaid
flowchart TB
    subgraph Inspector["AI Inspector — orchestration and user workspace"]
        Bob["Main orchestrator · Bob<br/>Muse Spark 1.3 · task planning and tool selection"]
        User["User input<br/>Task, drawings, photographs and IFC"]
        Runtime["Inspector runtime<br/>Sessions, durable tasks and pinned evidence"]
        Phone["iPhone capture client<br/>RGB-D, calibration and camera poses"]
        Bob <-->|"Input and findings through the web workspace"| User
        Bob <-->|"Scoped tools and workflow state"| Runtime
        Runtime -->|"Assign capture task"| Phone
        Phone -->|"Captured data via signed uploads"| Runtime
    end

    subgraph Agents["Agent delegation — independent contexts and scoped responsibilities"]
        GI["Geometry Inspector · domain sub-agent<br/>Muse Spark 1.3 · evidence-based inspection"]
        GV["Geometry Verifier · validation sub-agent<br/>Muse Spark 1.3 · independent evidence review"]
        GI -->|"Delegate verification"| GV
        GV -->|"Typed decision or request for more evidence"| GI
    end

    subgraph Services["Specialized services — computation and model workflows"]
        Geometry["Geometry Engine · computation service<br/>RGB-D reconstruction → SAM 3.1 masks → IFC comparison"]
        Drawing["Drawing / Photo · analysis service<br/>Muse Spark 1.3"]
        Checklist["Staged workflow: drawing requirements"]
        Assessment["Photo assessment against fixed requirements"]
        Review["Fresh-context review<br/>Both original images and draft findings"]
        Progress["Progress4D · monitoring service<br/>Dated surveys + scheduled IFC · Muse Spark 1.3"]
        Loc["Localization · spatial service<br/>Capture / IFC alignment"]
        Drawing -->|"When staged workflow is selected"| Checklist
        Checklist --> Assessment --> Review
    end

    Bob -->|"Delegate registered A2A task"| GI
    GI -->|"Validated inspection report"| Bob
    Runtime -->|"Scoped, pinned evidence"| GI
    Runtime -->|"Capture worker dispatches durable job"| Geometry
    Geometry -->|"Committed measurements and evidence"| Runtime
    Bob <-->|"Drawing / photo tool: task and report"| Drawing
    Bob <-->|"Progress tool: read mapped, processed results"| Progress
    Runtime <-->|"Pinned IFC, session and spatial state"| Loc
    Phone -->|"Session-scoped observations"| Loc

    classDef orchestrator fill:#dbeafe,stroke:#2563eb,stroke-width:2px;
    classDef agent fill:#ede9fe,stroke:#7c3aed;
    classDef service fill:#f0fdf4,stroke:#16a34a;
    class Bob orchestrator;
    class GI,GV agent;
    class Geometry,Drawing,Progress,Loc service;
```

The Inspector runtime owns capture assignments, uploads and durable computation jobs. Geometry Engine, Drawing/Photo, Progress4D and Localization are specialized services with explicit contracts; Geometry Inspector and Geometry Verifier form the agent delegation hierarchy. Heavy reconstruction runs outside the chat request, and committed results return to the inspection workflow. Source for all components is collected on `main`, with component setup and deployment documented in their guides.

### Thoughtful engineering built around the workflow

| Engineering choice | Why it matters | Implementation to inspect |
|---|---|---|
| Durable commands before side effects | A lost connection can be reconciled using the same operation instead of creating duplicate work | [Agent harness](ai_inspector/src/ai_inspector/agent_runtime.py), [orchestrator](ai_inspector/src/ai_inspector/agent.py) |
| Explicit ownership and fenced leases | An expired worker cannot publish over the current owner after a restart | [Capture workflow](ai_inspector/src/ai_inspector/workflow.py), [Geometry API/worker](geometry-engine/geometry_engine_api) |
| Typed model output and predefined tools | Model suggestions are checked against schemas, permissions and task scope before execution | [Model client](ai_inspector/src/ai_inspector/model_client.py), [inspector registry](ai_inspector/src/ai_inspector/inspectors.py) |
| Immutable inputs and content-addressed evidence | A finding can be traced to a specific drawing, capture, IFC and successful processing attempt | [Geometry handoff](ai_inspector/docs/capture-to-geometry.md), [workspace](ai_inspector/src/ai_inspector/workspace.py) |
| Separate computation and interpretation | Geometry produces numerical evidence; language models explain and review bounded evidence | [Geometry pipeline](geometry-engine/geometry_engine/pipeline.py), [review contract](ai_inspector/docs/ifc-discrepancy-review.md) |
| Small, versioned service boundaries | Components can use different native dependencies and independent persistence without coupling their lifecycles | [A2A protocol](ai_inspector/src/ai_inspector/a2a_protocol.py), [component setup](docs/reproducible-setup.md) |
| Evidence-rich presentation | Results retain source provenance, measurement context and visibility information | [Geometry viewer contract](ai_inspector/docs/capture-to-geometry.md#inspection-results-viewer), [drawing/photo schemas](drawing_photo_service/models.py) |
| Metadata-only observability | Latency, attempts, usage and prompt versions can be examined without automatically exporting source images or chat text | [Observability](ai_inspector/src/ai_inspector/observability.py), [Langfuse guide](ai_inspector/docs/agent-chat.md#langfuse-cloud-traces-prompts-and-evaluation) |

These mechanisms give the product a durable foundation: operations retain their identity across retries, completed computations can be recovered, and findings remain connected to the evidence that produced them.

### What makes this inspection system technically distinctive

**Powerful visual intelligence, grounded in structured requirements and measurable evidence.** The system gives VLM analysis an explicit inspection process: drawing-derived checklists, distinct assessment and review contexts, numerical geometry, and deterministic validation of report structure and source bindings. Geometry Inspector adds a dedicated Verifier agent to its evidence-review hierarchy.

- **Stable requirements across repeated checks.** Drawing/Photo extracts the design requirements before assessing the observation, then reuses their identities when a new photograph arrives.
- **Focused reasoning and a fresh review.** The staged workflow separates requirement extraction, photo assessment and review into **three model contexts**. Recorded runs reuse **one 11-item checklist across three analyses**.
- **Semantic perception connected to metric geometry.** SAM masks are projected and fused using depth evidence; numerical code calculates geometric comparisons, while Spark agents interpret and review the findings.
- **Findings an engineer can investigate.** Source images, evidence regions, IFC identities and measurement bindings connect conclusions to their supporting material.

[Drawing/photo workflow and recorded comparisons](docs/evidence/2026-11-03/README.md#2-drawingphoto-from-joint-context-to-frozen-requirements) · [Agent verification contract](ai_inspector/docs/ifc-discrepancy-review.md)

## How we use Meta models

**Deeply integrated Meta intelligence powers the product's reasoning, perception and voice interaction.** Each integration has a concrete responsibility in the inspection workflow.

| Component | Model and interface | Model responsibility | Responsibility retained by code |
|---|---|---|---|
| Main orchestrator and Geometry review agents | `muse-spark-1.3`, Meta Model API Chat Completions | Understand the task, select permitted tools, inspect supplied evidence and explain accepted results | Authorization, bounded execution, request identity, numerical bindings and final report validation |
| Drawing/photo analysis | `muse-spark-1.3`, images, structured output and `crop_region` | Extract design requirements, inspect the corresponding photograph and review findings | Source-role restrictions, crop coordinate mapping, checklist identity and verdict-schema checks |
| Geometry segmentation | `sam-3.1`, Meta Model API Responses and `meta-sam-parser` | Produce masks for walls, floors, ceilings, windows, window frames and doors | RGB-D reconstruction, visible-triangle projection, fusion, rigid registration and measurements |
| Progress4D visual evidence | `muse-spark-1.3`, image input and JSON schema | Assess product evidence from selected views and distinguish visibility/material/installation claims | Camera reconstruction, IFC registration, visibility gates, temporal aggregation and forecast rules |
| Voice input | `muse-voice-transcribe-1.0`, Meta Model API `/asr/transcribe` | Turn a spoken inspection request into text | Push-to-talk capture, mono PCM WAV conversion, authentication, audio validation and insertion into an editable message draft |

The [shared agent harness](ai_inspector/src/ai_inspector/agent_runtime.py) manages durable model requests and tool execution. The [Meta adapter](ai_inspector/src/ai_inspector/model_client.py) validates returned messages. The [SAM adapter](geometry-engine/geometry_engine/segmentation/sam_backend.py) uses hosted inference and the official decoder; metric reconstruction and comparison run on CPU. The [voice adapter](ai_inspector/src/ai_inspector/voice.py) turns push-to-talk speech into an editable draft, with audio handled in memory. Model interfaces: [Meta model catalog](https://dev.meta.ai/docs/models) · [SAM API](https://dev.meta.ai/docs/sam/overview).

## Service details

### Inspector: a cohesive, capable engineering workspace

The web workspace brings chat, drawings, photographs, IFC models and review artifacts together with Three.js, web-ifc and PDF.js. The backend owns persistent sessions, capture assignments, processing operations and agent execution. Typed artifacts make findings explorable alongside their source material. Durable commands, request identities and fenced task ownership preserve continuity across long-running work and reconnects.

[Backend and setup](ai_inspector/README.md) · [Agent contract](ai_inspector/docs/agent-chat.md) · [API](ai_inspector/src/ai_inspector/api.py) · [Web integration](ai_inspector/docs/web-integration.md) · [Viewer](ai_inspector/viewer/README.md)

### Geometry Inspector and Geometry Verifier: focused expertise and rigorous review

Geometry Inspector owns the geometry/BIM inspection task. Its Geometry Verifier receives a bounded evidence package and returns a typed review decision. Both operate as independent A2A services with their own task stores and workers. Source hashes, candidate identities and measurement bindings connect the agent's conclusion to the computation that supports it; the orchestrator validates the accepted report before presenting it.

[Roles, schemas and setup](ai_inspector/docs/ifc-discrepancy-review.md) · [Agent services](ai_inspector/src/ai_inspector/discrepancy_agents.py) · [Report schemas](ai_inspector/src/ai_inspector/discrepancy.py) · [Evidence adapter](ai_inspector/src/ai_inspector/discrepancy_evidence.py)

### Geometry Engine: sophisticated metric reconstruction meets Meta perception

Geometry Engine reconstructs observed surfaces from RGB-D frames with **Open3D TSDF and original ARKit camera poses**. Meta SAM segments structural elements in selected RGB views. Depth-supported projection and multiview fusion connect those masks to the mesh; observed contours provide opening candidates. Rigid IFC registration and geometric comparison produce dimensions, displacements and surface differences.

The design combines **semantic understanding with numerical measurement**. The mesh preserves the observed geometry; registration uses a rigid transform with scale fixed to one. Dominant normals, yaw hypotheses and robust point-to-plane ICP support alignment. Projection-camera adjustment improves mask placement against the fixed mesh.

**Efficient reuse is engineered into the pipeline.** Exact-input SAM caching reuses unchanged masks across geometry settings and IFC comparisons. Each completed stage publishes a manifest with content hashes, allowing workers to restore verified computations after a restart. Reconstruction, segmentation, comparison and viewer export remain separately traceable.

[Service and setup](geometry-engine/README.md) · [Reconstruction](geometry-engine/geometry_engine/reconstruction/service.py) · [Registration](geometry-engine/geometry_engine/comparison/bim_registration.py) · [Mask fusion](geometry-engine/geometry_engine/segmentation/mask_fusion.py) · [Opening contours](geometry-engine/geometry_engine/openings/mesh_boundaries.py) · [Inspector handoff](ai_inspector/docs/capture-to-geometry.md)

### Drawing/Photo: structured reasoning, reusable requirements and a fresh review

The service compares a drawing or PDF page with a site photograph for the user's inspection task. Its **staged workflow** separates three responsibilities: extract requirements from the drawing, assess the photograph against those fixed requirements, then review the findings in a fresh context with both originals. Stable requirement IDs and source bindings make the checklist reusable for subsequent photographs.

Requirements cover geometry, material, product, measurement, installation and performance. Evidence regions connect design claims and site observations. Named grid views and server-side crop mapping keep image references tied to the original pixels. The shared-context `legacy` workflow and the staged workflow are both implemented. The checked-in Cloudflare configuration and reproducible setup guide select **`staged`**, bringing checklist-based assessment and fresh-context review into that configuration.

[Service and API](drawing_photo_service/README.md) · [Legacy engine](drawing_photo_service/engine.py) · [Staged requirements and prompts](drawing_photo_service/staged.py) · [Evidence grounding](drawing_photo_service/grounding.py) · [Persistence](drawing_photo_service/app.py) · [Inspector adapter](ai_inspector/src/ai_inspector/drawing_photo.py)

### Progress4D: a richer view of construction progress

Progress4D combines overlapping photo surveys, an IFC work schedule and Meta visual assessment. PyCOLMAP reconstructs camera poses and sparse geometry; the configurable OpenMVS CPU path adds a dense textured surface. Registration connects observations to the model, while selected views supply product-level evidence for Muse Spark. Deterministic temporal logic produces a timeline, counts, activity and conditional forecasts.

The progress viewer brings these dated results into the Inspector workspace through a scoped bridge. Versioned evidence cards, source dates and separate material/installation fields make the reasoning behind each status inspectable.

[Service, supported inputs and setup](progress4d_service/README.md) · [Pipeline](progress4d_service/pipeline.py) · [Surface adapter](progress4d_service/surface.py) · [Visual assessment](progress4d_service/perception.py) · [Temporal logic](progress4d_service/temporal.py) · [Inspector integration](ai_inspector/docs/progress4d.md)

### iPhone capture and Localization: bring physical observations into the workflow

The SwiftUI/ARKit client captures RGB imagery and, on LiDAR-equipped devices, depth and confidence with camera intrinsics, poses and frame identities. Inspector assigns the session; the phone uploads through scoped signed permissions. Keychain credentials and persistent retry journals support secure, recoverable capture delivery.

Localization provides session-bound spatial observations and capture/IFC alignment using depth backprojection, gravity-aligned plane fitting and rigid registration. Its IFC room model establishes the coordinate frame for supported capture workflows.

[iOS setup](ios/README.md) · [Server integration](docs/inspector-server-integration.md) · [Mobile integration](docs/mobile-integration-status.md) · [Capture format](ai_inspector/docs/zip-capture.md) · [Localization setup](localization_service/deploy/README.md) · [Cloud API](localization_service/cloud_server.py) · [Registration](localization_service/registration.py) · [Inspector adapter](ai_inspector/src/ai_inspector/localization.py) · [Localization experiments](localization_service/README.md)

## Approach comparisons

**The implementation reflects careful refinement and substantial technical depth.** The project records alternative approaches, same-input comparisons and numerical experiments alongside the code.

| Area | Implemented refinement | Why it matters |
|---|---|---|
| Geometry reconstruction | Original ARKit poses with TSDF; projection-only camera adjustment | Preserves the metric reconstruction while refining semantic projection |
| Structural evidence | Depth-supported multiview mask fusion and mesh contours | Connects Meta semantics to observed surfaces and opening geometry |
| Drawing/photo reasoning | Separate requirement extraction, assessment and final review | Reuses design requirements while reviewing each photograph in its own context |
| Repeated computations | Exact-input SAM cache and durable stage manifests | Reuses matching inference and recovers completed work |
| Progress evidence | Larger independent view cards with distinct material/installation fields | Gives visual assessment richer evidence and structured outputs |

The [workflow evolution study](docs/evidence/2026-11-03/workflow-evolution.md) links historical implementations and paired real-room diagnostics. The [controlled comparison](docs/evidence/2026-11-03/controlled-comparison.md) includes generated ground truth, per-case records and four figures. The [drawing/photo comparison](docs/evidence/2026-11-03/README.md#same-input-legacystaged-run) records model calls, processing time and findings on the same inputs.

The numerical foundations include [volumetric depth fusion](https://graphics.stanford.edu/papers/volrange/), [Open3D TSDF](https://www.open3d.org/docs/latest/tutorial/t_reconstruction_system/integration.html), [robust point-to-plane alignment](https://www.open3d.org/docs/latest/tutorial/pipelines/robust_kernels.html) and depth/overlap grouping adapted from [Open3DIS](https://arxiv.org/abs/2312.10671). Progress reconstruction uses [COLMAP](https://colmap.github.io/), [OpenCV StereoSGBM](https://docs.opencv.org/4.x/d2/d85/classcv_1_1StereoSGBM.html) and [OpenMVS](https://github.com/cdcseacave/openMVS/blob/v2.4.0/README.md). Project-specific adapters, evidence contracts, registration and review workflows connect these methods into the product. [Method notices and upstream references](geometry-engine/THIRD_PARTY_NOTICES.md).

## Reproducible setup

For the product walkthrough, [open the hosted demo](https://inspector.campuscompile.eu/demo). For development and new processing, the [step-by-step setup guide](docs/reproducible-setup.md) explains environments, credentials, startup order and workflow inputs.

| Workflow | Guide |
|---|---|
| Workspace and Meta-powered chat | [Inspector setup](ai_inspector/README.md) |
| Drawing/photo analysis | [Drawing/photo service](drawing_photo_service/README.md) |
| RGB-D reconstruction and IFC comparison | [Geometry API and worker](geometry-engine/README.md) |
| Geometry agent review | [Inspector and Verifier](ai_inspector/docs/ifc-discrepancy-review.md) |
| Photo-based progress monitoring | [Progress4D](progress4d_service/README.md) |
| Phone capture | [iOS](ios/README.md) |
| Spatial integration | [Localization](localization_service/deploy/README.md) |

The [evidence guide](docs/evidence/2026-11-03/README.md) connects source data, summaries and manifests. The [data access guide](docs/evidence/2026-11-03/data-access.md) provides versioned public packages and integrity checks.

## Verification

Component tests exercise deterministic behavior and service contracts. The [evidence cases](docs/evidence/2026-11-03/README.md) document recorded model runs and numerical experiments with source identities and reproduction protocols. Generated `/docs`, `/openapi.json` and `/contracts` describe the service schemas. [Backend verification workflow](ai_inspector/README.md#verification).

## Component and license references

The implementation uses existing numerical and visualization libraries alongside Meta models. Component dependencies and notices are authoritative: [Geometry dependencies](geometry-engine/pyproject.toml) and [third-party notices](geometry-engine/THIRD_PARTY_NOTICES.md), [Inspector dependencies](ai_inspector/pyproject.toml), [document viewer licenses](ai_inspector/viewer/README.md), [drawing/photo dependencies](drawing_photo_service/requirements.lock), and [Progress4D native dependencies and OpenMVS AGPLv3 notice](progress4d_service/README.md#native-surface-reconstruction). This README does not grant a different license to those dependencies.
