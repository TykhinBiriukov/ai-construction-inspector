# IFC and site-capture discrepancy review contract

**Status as of 31 October 2026:** code for two separate A2A agents and the v1 contract for reviewing supplied candidates is implemented. Bob remains the existing orchestrator. Geometry Inspector collects pinned chat assets through tools, a separate Geometry Verifier returns decisions, and Geometry Inspector assembles validated JSON. The original supplied text is retained separately without changes. Native factories and local synthetic checks do not establish deployment, live Meta/Geometry/iPhone access or verified inspection accuracy. The actual schemas are in [discrepancy.py](../src/ai_inspector/discrepancy.py) and each configured service's public GET /contracts; this guide describes responsibilities, setup and limitations.

A coding-agent brief prepared from the discussion. Here, “contract” means data-exchange rules and component responsibilities.

**Update, 2 November 2026:** the Inspector backend can construct this input server-side from accepted Geometry compact v2 and automatically continue an explicitly requested review. Configuration, supported sources, limits and APIs are in [Geometry integration](capture-to-geometry.md#downstream-discrepancy-review). Detection and measurements remain Geometry's responsibility; converting its report does not certify their accuracy. The previous manual candidates/assets submission path remains available.

## 1. Objective

Review suspected discrepancies between the IFC model and site capture through two separate agents. Geometry Inspector receives Bob's task, invokes the required permitted tools and prepares a candidate/evidence package. Geometry Verifier assesses that package, determines whether each candidate is supported and returns structured decisions to Geometry Inspector. Geometry Inspector validates and assembles the final JSON report for Bob. Another component formats the report for people.

Distinguish a detected candidate from a confirmed issue. A candidate's presence in the input JSON does not establish that a construction discrepancy exists.

## 2. Confirmed decisions

Clarified by the owner after discussion:

1. **Bob is the main orchestrator and chat agent:** communicates with the user and invokes tools or direct subordinate inspectors. All agents at the level immediately below Bob are named inspectors for their respective domains.
2. **Geometry Inspector is a separate inspection-management agent:** collects evidence through tools, manages task steps, invokes Geometry Verifier and produces the final JSON for Bob.
3. **Geometry Verifier is Geometry Inspector's sub-agent, not an inspector:** assesses supplied candidates and evidence, returning decisions and requests for missing data to its inspector. Initial candidate detection happens outside this analytical review.
4. **Separate services and state:** Geometry Inspector and Geometry Verifier have their own instructions, permissions, bounded loops, histories and task states; they can scale and deploy independently.
5. **Structured inter-agent data:** the task, evidence package, review decisions and final report have separate JSON contracts. Another component handles human-readable formatting.

## 3. Roles and interaction

Naming hierarchy: level 1 is **Bob, the main orchestrator**; level 2 comprises **domain inspectors**; level 3 comprises **their sub-agents**, where present. Geometry Inspector owns building-geometry inspection and BIM comparison. Geometry Verifier is its verification sub-agent. Geometry Engine is a computation service; its completion does not replace a domain assessment.

This is an internal development and execution hierarchy. Users interact with one **AI Inspector**: chat and ordinary progress displays do not list Bob, inspectors, the verifier or handoffs between them. They show the work, results, limitations and required clarifications. Technical contracts and history retain service IDs and provenance.

External interaction:

```text
User ↔ Bob
               ├─ invokes tools and other inspectors for their respective tasks
               └─ delegates a task to Geometry Inspector
                    ├─ invokes the required tools and obtains evidence/measurements
                    ├─ sends a candidate package to Geometry Verifier
                    │    └─ receives JSON decisions for each candidate
                    └─ validates and assembles final JSON → Bob

Result JSON → separate report-formatting component
```

- **Bob** is the main orchestrator and user-facing conversation interface. It selects and invokes the required tools or specific inspectors, supplies context and materials, receives results and forwards missing-data requests to the user.
- **Geometry Inspector** manages the domain task: receives supplied candidates, selects the required tools, prepares evidence, delegates analysis to Geometry Verifier and returns final JSON to Bob. When more materials are requested, it takes an allowed next step or forwards the request to the user through Bob.
- **Geometry Verifier** is Geometry Inspector's sub-agent: receives the prepared package, checks object classification and support for discrepancies, and returns all decisions, explanations, evidence references, limitations and data requests to its inspector. It owns the candidate's domain status; Geometry Inspector must not silently rewrite an accepted decision while assembling the report.
- **Report-formatting component** uses the review JSON to prepare a human-readable presentation. Its format and implementation are outside this contract.

The minimum target chain has three agent roles: the existing Bob plus two separate new agents, Geometry Inspector and Geometry Verifier. Geometry, backend JSON validation and report formatting do not introduce additional mandatory agent roles. Geometry Inspector may eventually route tasks to different registered Geometry Verifier profiles; routing, fan-out and aggregation rules will require a separate contract. This separation does not permit unrestricted recursive delegation.

The discussion also mentioned an agent for checking construction details. This may be another domain inspector; its specific task and contract are not defined here.

## 4. Three discrepancy categories

These are data categories, not sequential execution stages.

| Proposed JSON key | Meaning | Required check |
| --- | --- | --- |
| `ifc_only` | An object exists in IFC but was not detected in the capture | Whether the materials support its absence and the capture is sufficient for that conclusion |
| `scan_only` | An object was detected in the capture but is absent from IFC | Whether the object was classified correctly and the lack of a model correspondence is supported |
| `geometry_mismatch` | An object is matched but its position or geometry differs | What discrepancy was found and whether the materials and measurements support it |

For `geometry_mismatch`, distinguish:

- object displacement;
- differences in shape or dimensions;
- both types of discrepancy together.

These are refinements within the third category, not additional primary categories.

Retain the original reason each candidate entered review. Report the agent's decision basis and supporting materials separately. The basis means a verifiable explanation of the conclusion, not the model's internal reasoning.

## 5. Input materials

### 5.1. Shared project materials

Supplied as completed outputs of earlier processing and reusable across cases:

- the reconstructed point cloud as a shared file;
- category segmentation of that cloud;
- the available IFC model, if already in the system.

The discussion did not require resending IFC to Geometry Verifier. If a particular decision needs IFC information, include it in the candidate data or provide access to the existing model.

Point-cloud reconstruction and initial segmentation are not required within this contract.

### 5.2. Per-case materials

Each candidate must be linked to its own materials:

- discrepancy category and detection reason;
- object information and location;
- one or more photographs;
- the corresponding segmentation fragment or a reference to a selected region of the shared segmentation;
- additional LiDAR capture or a local point-cloud fragment, when available;
- for geometric discrepancies, supplied measurements, displacement direction and magnitude, and the dimensions being compared.

Geometry Inspector selects the required tools and prepares useful materials from those available. Geometry Verifier receives permitted previews/excerpts and computed data, not just filenames. Additional LiDAR capture remains optional.

Geometry visualization may help explain a discrepancy, but producing a 3D model was not agreed as a mandatory output.

## 6. Review rules

Geometry Inspector first assembles the package and pins its versions/sources. Geometry Verifier then performs these steps:

1. Read the candidate category and original detection reason.
2. Examine the linked photographs, segmentation, measurements and other available package materials.
3. Check whether the object classification is supported. For example, whether an object labelled as a door matches what the photograph shows.
4. Assess the suspected discrepancy within the available evidence.
5. Record the decision, a brief justification and references to the materials used.
6. Return JSON review results and requests for missing data to Geometry Inspector.

Geometry Inspector validates format, case/source bindings and result completeness, assembles the final JSON and returns it to Bob. A contract error requires a corrected result; insufficient evidence requires a new linked package. A final domain status changes only through a newly accepted review result, not during JSON formatting. Backend code performs deterministic schema validation and serialization without another agent.

**Example from the discussion:** the system creates a candidate stating “a door was found in the capture but is absent from IFC.” The photograph shows that the segmented object is not a door. Reject that candidate as a segmentation error rather than listing it as a confirmed construction discrepancy.

**Incomplete data:** Geometry Verifier retains uncertainty and describes what is missing in `data_requests`. Geometry Inspector forwards the request to the user through Bob. New data produces a new linked invocation with a new request_id and immutable package. Insufficient data neither confirms nor rejects a candidate.

## 7. Data structure v1

Implemented Pydantic models are in [discrepancy.py](../src/ai_inspector/discrepancy.py). GET `/contracts` returns the service-specific JSON Schema; the native operator API also includes InspectionRequest in OpenAPI. Unknown fields, duplicate JSON members in A2A/results and nonnumeric/infinite values are rejected. Do not change the wire schema or profile responsibilities under an already pinned version.

### Inter-agent messages

| Direction | Model / kind | Contents |
|---|---|---|
| Bob → Geometry Inspector | `InspectionRequest` / `inspection_request` | request_id, project_id, task, source bindings, assets and cases |
| Geometry Inspector → Geometry Verifier | `EvidencePackage` / `evidence_package` | inspection, request_sha256 and the complete list of read evidence, including explicit limitations for assets that cannot be inspected |
| Geometry Verifier → Geometry Inspector | `ReviewResult` / `review_result` | request_id, project_id, package_sha256, assessment_scope and cases |
| Geometry Inspector → Bob | `InspectionReport` / `inspection_report` | chat_id/task, request/package/review hashes, reviewer_task_id, source, unchanged cases, case_context, assets/asset_status and selected supplied measurements |

Every payload has schema_version `1.0`. A2A remains the transport envelope with a schema_version `0.1` domain extension. Hashes use canonical JSON of the normalized model/frozen request, not a temporary URL. Request/operation/task IDs across different hops are not interchangeable.

### Input data

`InspectionRequest.assets` contains asset_id, file_id, SHA-256 and kind: photo, segmentation, point_cloud, ifc, measurement or reference. A file_id resolves only to a previously uploaded, available file in the same chat. Shared files are not duplicated per case.

Each candidate has case_id, detection_reason, object_type, optional ifc_id/segment_id, location, evidence_ids, measurements, mismatch_types and reference_facts. Arrays `ifc_only`, `scan_only`, `geometry_mismatch` are explicit. One request contains 1–12 unique cases and 1–6 assets. There is no separate candidate-detection algorithm here: earlier processing supplies them.

`source` retains available session/manifest/Geometry job/revision/model/hash/segmentation bindings. A session-bound request is checked against the current chat's accepted source; workspace uploads remain user-supplied materials, not automatically proven frames from that capture. A capture/job/revision binding cannot be claimed without a session. origin distinguishes operator_supplied, recorded, synthetic and geometry. Server-side Geometry import requires matching chat/session/job/source/result-manifest bindings and file hash; a user upload cannot assign itself origin=geometry. IFC GUIDs containing `$` retain every character. For Geometry Engine, geometry_revision is Geometry Inspector's acceptance revision because upstream provides no revision of its own. Assessment scope is `supplied_evidence_review`, not certification of the algorithms or entire project. Separate Inspector/verifier services must use the updated contract; changes to a published profile require a version increase as specified in agent-chat.

`Measurement` contains measurement_id, parameter, observed/expected, unit (m/rad), error interval, acceptance_interval, extent, algorithm/version, source_asset_ids and, where needed, coordinate_frame_id/requires_alignment/alignment_verified. The last flag is supplied upstream evidence, not alignment calculated by the model. No tolerances are assigned by default.

Confirming a numerical `geometry_mismatch` requires the whole target, known interval/acceptance_interval, an interval entirely outside tolerance, the required alignment and an exact Measurement match in the read `MeasurementEvidence` file: schema_version=1.0, kind=measurement_evidence, measurements=[...]. The JSON file is hash-pinned; validation checks the integrity and structure of supplied numbers, not the accuracy of the algorithm that produced them. Plain text containing a number or caller metadata alone cannot replace this numerical source. The full MeasurementEvidence JSON Schema is included in the EvidencePackage schemas.

`comparison_basis=visual_shape` permits qualitative shape review only with mismatch_types=[shape_or_dimensions], an available photograph and reference; measurement_ids is empty for that conclusion. This visual assessment does not produce dimensions or displacement magnitudes. The default is comparison_basis=metric.

### Output cases

Each row retains case_id/detection_reason and includes status, decision_reason, evidence_used, measurement_ids, limitations and data_requests. References are limited to available evidence for the same case and IDs of supplied measurements. The model does not return new numbers: Geometry Inspector copies selected original Measurement objects into the report. needs_more_data requires a limitation and a specific data request. Rejected cases are retained.

| status | Meaning |
|---|---|
| confirmed | The materials support the specific claimed discrepancy within the agreed scope |
| rejected | The particular candidate is rejected with a reason, such as incorrect classification |
| needs_more_data | Evidence is insufficient to reach either conclusion |

`ifc_only` confirmation requires a reported observation of the expected region and supporting reference evidence; `scan_only` requires a reported checked reference scope and supporting evidence. These inputs remain supplied evidence; the system does not claim to have independently calculated coverage or IFC completeness. An interval crossing the tolerance boundary, a visible fragment or unknown uncertainty cannot confirm a numerical violation.

Example ReviewResult (fictional IDs/hash, not a real inspection):

```json
{
  "schema_version": "1.0",
  "kind": "review_result",
  "request_id": "11111111-1111-4111-8111-111111111111",
  "project_id": "example-project",
  "package_sha256": "0000000000000000000000000000000000000000000000000000000000000000",
  "assessment_scope": "supplied_evidence_review",
  "cases": {
    "ifc_only": [],
    "scan_only": [{
      "case_id": "case-001",
      "detection_reason": "Segmentation identified a door; no IFC correspondence was found.",
      "status": "rejected",
      "decision_reason": "The supplied photograph shows that the segment was classified incorrectly.",
      "evidence_used": ["photo-001", "segment-001"],
      "measurement_ids": [],
      "limitations": [],
      "data_requests": []
    }],
    "geometry_mismatch": []
  }
}
```

InspectionReport retains these decisions, the original source and proof bindings. case_context retains the claimed object_type, ifc_id/segment_id, location, mismatch_types and original evidence_ids; this is source context, not hidden object reclassification. assets contains scoped file IDs/hashes; asset_status describes materials that are actually accessible or cannot be inspected, and their limitations. chat_id lets the formatter resolve references through the authorized file API. The measurements field may contain a supplied measurement of a visible fragment for diagnostics; this does not promote it to a verified whole-object dimension. Markdown/PDF/HTML formatting is outside these agents.

## 8. Geometry data

The data must explain exactly what differs and by how much:

- the model and capture values being compared;
- measurement units;
- for displacement, magnitude and direction with an explicit coordinate-system reference;
- for shape or dimensional differences, the specific parameters that differ;
- the measurement source and a reference to the relevant object or region.

Do not invent missing dimensions, displacement direction or coordinate systems. If the information is insufficient for interpretation, record the limitation and request clarification.

The discussion did not define permitted construction deviations or decision thresholds. Do not introduce arbitrary tolerances. A required threshold must come from project requirements or remain explicitly unspecified.

## 9. Handoff to the formatting component

The review JSON must contain enough information for another component to prepare:

1. The project and source materials used.
2. A summary of confirmed, rejected and unresolved cases.
3. Confirmed discrepancies grouped into the three categories.
4. For each confirmed case: object, location, detection reason, review conclusion, supporting materials and supplied geometric measurements.
5. Rejected candidates with rejection reasons.
6. Cases with insufficient data and specific requests for additional materials.

This list describes the information to retain in JSON. Producing a readable report and choosing its format belong to the formatting component.

## 10. Contract acceptance criteria

- All three categories are present; geometric subtypes remain within `geometry_mismatch`.
- Every input case has a traceable result with the same `case_id`.
- Shared materials are available without resending them for each case.
- Photographs, segments and measurements are linked to their corresponding cases.
- Object-recognition errors can result in rejection of a candidate with an explanation.
- Insufficient data remains a separate state.
- Geometric conclusions include the measurement source, units and required coordinate reference, or explicitly state what is missing.
- Geometry Inspector collects supplied candidates and evidence through tools; Geometry Verifier analyzes the prepared package. Initial discrepancy detection is outside this analytical review.
- Bob invokes Geometry Inspector, and Geometry Inspector invokes a separate Geometry Verifier; user requests pass through Bob.
- Geometry Verifier returns JSON to Geometry Inspector; Geometry Inspector returns validated final JSON to Bob and retains rejected/needs_more_data decisions without deleting them.
- Geometry Inspector and Geometry Verifier have independent instructions, permissions, bounded execution and task state; retries/restarts of either service do not duplicate tasks or accepted results.
- Another component formats the report.

## 11. Scope of this task

This document defines the exchange contract and review behavior. It does not define the initial discrepancy-detection algorithm, segmentation tools, a specific stack, a cloud viewer interface or mandatory 3D-model generation. Those decisions cannot be treated as agreed from this discussion.

## 12. Alignment with Geometry Inspector's architecture

### 12.1. Contract placement and roles

In code, Bob corresponds to the existing `OrchestratorAgent` ([agent.py](../src/ai_inspector/agent.py)); `orchestrator` remains its technical identity. The name Bob does not require renaming Python classes, saved runs or A2A identities. Bob's user dialogue is not restricted to JSON. “JSON only” applies to the domain inspector's substantive result.

Geometry Inspector and Geometry Verifier connect as separate versioned profiles/services on the shared harness, each with its own instructions, permissions, bounded loop, history and task store/worker. Bob manages the user workflow, Geometry Inspector manages evidence collection/review, and Geometry Verifier makes the analytical decision. The existing Meta/Muse Spark model adapter is retained; no new capture pipeline is introduced.

Only the explicitly configured Geometry Inspector → registered discrepancy_verifier hop is implemented. Generic specialists still cannot delegate; Geometry Verifier also has no tools/delegation. Server configuration selects the endpoint, version and token reference. The concrete child operation, exact A2A body and package checkpoint persist before IO; child state remains in its own task store. Geometry Inspector releases its lease between polls, does not call the model while waiting and accepts results only with valid task/context/request/source bindings. A changed saved registration holds an unfinished task until restoration.

The contract begins **after initial candidate detection**. Earlier processing supplies `ifc_only`, `scan_only`, `geometry_mismatch` and associated data; this document does not assign its specific owner or algorithm. Geometry reconstruction/segmentation alone does not establish implemented candidate detection or IFC comparison. The profile need not reconstruct the cloud, segment the entire project again or search for discrepancies outside the supplied set.

This document does not replace the existing [capture → Geometry contract](capture-to-geometry.md), session/source/job ownership or recovery. It does not introduce previously proposed report/comparison HTTP routes, mandatory reconstruction-only/element-checks profiles, additional algorithms or tolerances from the old draft. These are not automatically accepted.

### 12.2. Meaning of decisions

The three candidate categories are independent of the three review decisions. The geometric subtype—displacement, shape/dimensions or both—remains within `geometry_mismatch`. The category and original `detection_reason` are retained; the inspector's explanation is separate. An incorrectly assigned category must not silently move to another array: the result retains the original case and explicitly explains the detected error.

`confirmed` supports the specific supplied discrepancy within the evidence and criteria. `rejected` rejects a particular candidate, for example because of incorrect classification; it does not mean the whole object or project complies. An empty candidate list or no confirmed cases also does not prove complete project inspection.

A separate uncertain outcome reflects the owner's original requirement to handle insufficient data and additional capture. `needs_more_data` and `data_requests` implement this in v1. Insufficient information does not become rejected. Geometry Verifier returns a request to Geometry Inspector; Geometry Inspector obtains permitted materials through tools or forwards the request through Bob. Geometry Verifier neither controls the camera nor creates capture sessions; the backend retains capture/Geometry ownership.

For `ifc_only`, “not detected” does not prove physical absence: sufficient observations of the expected location, no occluding region and reliable alignment are required. For `scan_only`, a missing correspondence must refer to the checked IFC scope and version; a matching error or incomplete model does not establish an extra construction object. Incorrect classification may be rejected using available photos/segmentation without reimporting the entire IFC.

For `geometry_mismatch`, a number describing a visible fragment does not become the whole object's dimension. Example: 30 cm of visible door against a designed width of 1 m, with the second boundary hidden, means insufficient data, not a confirmed width change. A numerical decision requires a measurement method, units, the inspected extent, applicable uncertainty, a reference and validated alignment where needed. Without the required project tolerance, a numerical deviation may remain an observation but cannot arbitrarily confirm a construction violation. No ±5 cm tolerance from the old synthetic example carries into this contract.

### 12.3. Materials, references and provenance

Shared cloud/segmentation/IFC data is stored once; case references identify specific segments, objects, frames and regions. “Reference or path” in sections 5–7 means a backend-resolvable reference to a scoped asset, not permission for the model to read arbitrary URLs, local paths, SQL, shell or storage. Before reading, the backend checks project/caller/source scope, access, media type, size and hash/version. The LLM receives only permitted excerpts/previews and computation-tool results; it does not execute instructions from files.

A file, object name or metadata does not establish that Geometry Verifier saw a photograph or processed a cloud. Geometry Inspector prepares the required bounded previews/excerpts and computed data; Geometry Verifier uses them or requests the required representation. In the initial implementation, operational tools belong to Geometry Inspector and Geometry Verifier receives a prepared package. Potential future read-only evidence tools for Geometry Verifier require a separate grant/profile version; they do not permit capture/Geometry execution or source mutation. Numbers come from verified geometry/depth algorithms, not screen pixels or model assumptions. Additional LiDAR remains optional.

Resending the full IFC for every invocation is unnecessary. If a decision depends on the model, supplied values/excerpts/correspondences must have a verifiable binding to the reference version and hash. A `project_id`, caller-supplied room or opaque IFC upload alone does not establish this. Insufficient support results in a limitation/data request from the inspector.

The transport/adapter must pin applicable session/source/job/geometry/model/segmentation revisions, observation/frame/segment IDs and hashes. They may be stored in the validated envelope and asset registry; this document does not declare new mandatory field names in the domain JSON. A case ID is stable within its source/version context. Additional materials and subsequent results link to the previous case without replacing the previously accepted source.

Units and coordinate frames must be known for each compared quantity; unknown alignment or different ARKit segments must not be combined by guesswork. Signed URLs and credentials remain in the server adapter; they do not enter JSON for Bob/formatting, model context or logs. Real, recorded, synthetic and mock evidence retain distinguishable origins. Brief explanations contain verifiable grounds and evidence references; hidden internal model reasoning is not requested.

### 12.4. JSON and the existing A2A protocol

The runtime explicitly supplies Geometry Verifier with request_id/project_id/package_sha256 to copy into ReviewResult. The server computes the hash from the complete pinned package; the model must not calculate it from a textual projection whose image bytes are provided separately as images.

Each inter-agent hop in section 7 carries its own structured JSON payload. ReviewResult and InspectionReport are each one JSON object without Markdown fences or surrounding prose. Valid JSON text can travel inside the existing A2A text Artifact; this does not require immediately switching the wire protocol to an unsupported DataPart or claiming `application/json` is an available output mode. The A2A envelope remains the transport wrapper; Geometry Inspector accepts ReviewResult and Bob accepts InspectionReport. Tools return their agreed results and need not produce a full InspectionReport.

Validation occurs **before accepting a completed domain report** at the verifier, Geometry Inspector and Bob: JSON syntax and v1 schema, all three arrays, exactly one result per input case ID, original category and detection_reason, permitted decision, and references to case assets that are actually available. Unknown/foreign/duplicate/missing cases are contract errors. An incomplete/oversized response must not be silently truncated; section 13 describes limits and splitting.

Geometry Inspector retains the original package and accepted ReviewResult; its final InspectionReport references that result and retains all confirmed/rejected/uncertain cases, explanations, evidence, limitations and data requests. Removing unnecessary fields from a projection does not alter source data; rejected candidates remain traceable. On inconsistency, Geometry Inspector rejects the result as a contract error or requests a new review rather than rewriting the status itself. The formatter likewise adds no measurements and does not change accepted statuses. Model-generated JSON alone is not an independently verified numerical measurement or compliance certification.

A technical model error, asset-access failure or JSON parsing failure is an execution/contract error. It is not disguised as an object's needs_more_data outcome and does not prompt recapture without an actual capture limitation. If one case completes and another needs data, their results remain separate rather than collapsing the review into one overall flag.

### 12.5. Identity, retries and additional capture

A2A request/operation/task IDs and the frozen body/hash of every hop are retained. Bob stores the identity and accepted outcome of Geometry Inspector's task; Geometry Inspector owns its workflow and stores the identity, input hash and accepted outcome of Geometry Verifier's task. Geometry Verifier's active checkpoint stays in its independent task store, is not copied into its parent and does not depend on a live Geometry Inspector process. A specialized Geometry Inspector client implements this relationship; generic specialists do not gain this access. Case IDs do not replace task/operation IDs. Transport retries reuse the original ID and exact body; an unknown network outcome does not create a second task. Old/cancelled results cannot apply to changed source/model revisions. Completed JSON is evidence, not authorization for another side effect.

A user response containing additional data is a new linked request with new request/operation IDs, the previous case context and a newly pinned evidence version; the old request body and completed history are not rewritten. Current remote A2A does not support interactive messages into an existing task, so waiting for data must not be simulated that way. A successful ReviewResult with uncertain cases may complete Geometry Verifier's current task. In v1 the package is immutable: Geometry Inspector completes one task with final JSON containing data_requests; after new materials arrive, Bob creates a new linked Geometry Inspector task and a new request_id. Existing tasks are not automatically enriched with new inputs. Policy/budgets bound these iterations and waits rather than allowing endless agent exchanges. The implemented Geometry continuation is limited to explicitly registered review of an accepted result; a general mechanism for waiting for and applying additional user answers remains future work.

Cancelling Geometry Inspector's task fences its lease and acceptance of late results; an already running Geometry Verifier task may finish in its own store. Cascading remote cancellation is not promised; capture/Geometry is not cancelled by it.

Additional capture uses the existing capture flow only on an explicit user request. An open session continues; after sealing, a new linked session is created. Offline/restart does not cancel the original session. An inspector's `data_requests` alone does not start the camera, upload or a new Geometry job.

### 12.6. Implemented boundary and remaining dependencies

| Component | State |
|---|---|
| Bob | Existing OrchestratorAgent; typed request / selected candidate JSON, remote routing and accepted-report validation |
| Geometry Inspector | DiscrepancyInspectorService on the shared harness, its own SQLite task store/worker, inspection_evidence and review_candidates |
| Geometry Verifier | VerificationAgentService on the shared harness with a separate store/worker; JSON validation/repair, no tools or further delegation |
| Evidence | Same-chat immutable upload references/hashes or server-side import of an accepted Geometry result, bounded image previews/text and typed supplied measurements; access through a separate scoped backend token |
| Case outcomes | 1:1 validation of case IDs/categories/reasons/available evidence, fragment/uncertainty/tolerance guards, deterministic final JSON assembly |
| Recovery | Frozen parent/child requests, identical admission retry, independent restart, polling without model turns, deadlines/attempt limits and cancellation fencing |
| Geometry/candidate discovery | Inspector-only adapter for the existing /inspections API, evidence pinning and candidate assembly from compact v2; no new IFC parser, detector, measurement or alignment algorithm |
| Continuation, formatting and additional capture | Explicitly requested Geometry review continues through the queue to Bob's aggregate reply; a human-readable formatter and general user-input workflow remain future work |
| Live deployment/accuracy | Require separate service configuration, live Meta/evidence integration and domain acceptance |

Code: [agents](../src/ai_inspector/discrepancy_agents.py), [contracts](../src/ai_inspector/discrepancy.py), [evidence adapter](../src/ai_inspector/discrepancy_evidence.py), [shared harness](../src/ai_inspector/agent_runtime.py), [A2A service](../src/ai_inspector/a2a_service.py), [Bob](../src/ai_inspector/agent.py). Local fixtures do not verify construction accuracy, real SAM/Geometry/Meta availability or deployment.

## 13. Configuration and usage

### Native services

The two services use separate persistent INSPECTOR_AGENT_DATA_DIR locations and inbound secrets. From ai_inspector, in separate configured processes:

```powershell
.\.venv\Scripts\python.exe -B -m uvicorn ai_inspector.discrepancy_agents:create_verifier_app_from_env --factory --host 127.0.0.1 --port 8102
.\.venv\Scripts\python.exe -B -m uvicorn ai_inspector.discrepancy_agents:create_inspector_app_from_env --factory --host 127.0.0.1 --port 8101
```

For both processes, set INSPECTOR_AGENT_PUBLIC_URL, INSPECTOR_AGENT_DATA_DIR, INSPECTOR_AGENT_CALLERS_JSON and the existing INSPECTOR_META_BASE_URL/MODEL/API_KEY/TIMEOUT_S. Production public URLs use HTTPS. INSPECTOR_AGENT_PROFILE_JSON is optional for these two factories: defaults are version=3, IDs discrepancy_inspector/discrepancy_verifier and the appropriate kind/instructions/tools/budgets. A custom profile's kind must match its factory; changed responsibilities require a new version. The generic factory retains its previous contract.

INSPECTOR_AGENT_CALLERS_JSON is a private array of client_id/project_id/token. Bob is Geometry Inspector's caller; Geometry Inspector is Geometry Verifier's caller. Token values belong in Secrets, not documents or model payloads. They must differ from each other and from backend-tool, operator/device/model credentials. The app/phone does not receive these keys.

Only Geometry Inspector additionally receives INSPECTOR_AGENT_BACKEND_URL, INSPECTOR_AGENT_BACKEND_TOKEN and INSPECTOR_VERIFIER_JSON. Its backend token matches the tool_token_env value granted to this Geometry Inspector in Bob's registry. Example registration of Geometry Verifier in Geometry Inspector:

```json
{
  "inspector_id": "discrepancy_verifier",
  "kind": "discrepancy_verifier",
  "version": "3",
  "description": "Geometry Verifier: review supplied discrepancy candidates",
  "base_url": "https://verifier.example",
  "token_env": "INSPECTOR_VERIFIER_TOKEN"
}
```

INSPECTOR_VERIFIER_TOKEN is a separate Secret held only by Geometry Inspector; its value matches the inbound caller token at Geometry Verifier. Geometry Verifier does not receive the backend-tool token and cannot read other assets or delegate further.

Register only Geometry Inspector in Bob's INSPECTOR_REMOTE_AGENTS_JSON:

```json
[
  {
    "inspector_id": "discrepancy_inspector",
    "kind": "discrepancy_inspector",
    "version": "3",
    "description": "Geometry Inspector: collect evidence and obtain a Geometry Verifier review",
    "base_url": "https://inspector.example",
    "token_env": "INSPECTOR_TASK_TOKEN",
    "tool_token_env": "INSPECTOR_TOOL_TOKEN",
    "allowed_tools": ["inspection_evidence"]
  }
]
```

INSPECTOR_TASK_TOKEN matches the inbound secret for caller Bob at Geometry Inspector. INSPECTOR_TOOL_TOKEN is the granted backend-tool secret. Geometry Verifier itself is not registered for direct invocation by Bob. Example URLs describe configuration, not published services. The default registry remains empty; code does not automatically create deployments/Secrets.

### Entry from chat and API

Operator POST `/v1/chats/{chat_id}/messages` accepts an optional inspection_request containing InspectionRequest v1. The message body and typed request persist before model/tool IO; a changed body with the same message ID conflicts. A candidate batch's separate request_id likewise cannot be reused with different content in the same chat.

The existing Files UI can upload compact JSON with kind=inspection_request; select it **and all referenced evidence files** for the message. The complete JSON must fit in the saved, untruncated text excerpt, typically up to 16k characters. It is parsed/pinned but does not execute instructions or force Bob to start review without a user task. Multiple batch files or unselected evidence require clarification; other JSON types remain ordinary attachments.

For larger batches, submit the typed field through the API or explicitly split the scope. Input is limited to 12 cases/6 assets, EvidencePackage to 1.6 MB, A2A to 2 MiB, and the complete report to 16000 characters; nothing is silently truncated. Text assets are limited to 128k bytes with an excerpt up to 12000 characters; opaque IFC/point clouds are metadata-only and require derived representations.

Photos: sources up to 8 MB and 20 MP, full-image previews up to 768px and 190k JPEG bytes, with EXIF transpose/resize identified; previews are not measurement scales. Native preprocessing uses Pillow, also available through the optional `evidence` extra; without it, only an already bounded JPEG is supported and other images receive an explicit limitation. Install the extra through ordinary process setup if needed; this task used an existing Pillow installation. Signed storage URLs are not sent to models.

Geometry Inspector invokes only inspection_evidence(asset_id) and review_candidates(). Each asset retains its source ID/hash and actually available content/limitation. New capture assets must be explicitly represented in permitted chat evidence; automatic import of frames/Geometry measurements from arbitrary folders is not implemented. The existing Supabase blob adapter reads pinned uploads rather than discovering an entire folder/bucket.

By default, Geometry Inspector has 12 model turns and Geometry Verifier has 3, including up to two JSON repairs within the overall budget. Waiting for Geometry Verifier is limited to 120 polls or 10 minutes and does not call the model again. Transient transport retries reuse the original ID/body, current server credential reference and bounded retry budget. A completed ReviewResult is not recomputed. Technical failure/timeout/invalid JSON remains a technical error, not an automatic recapture request.

An accepted InspectionReport is available to the operator in inspection_report on the chat run/history; Bob receives the JSON as the delegation result and can explain it to the user. The human-readable formatter remains a separate component. Subsequent data requires a new candidate request_id and message; previous cases, evidence hashes and results remain in history.

### Names and compatibility

The two agents' public names are **Geometry Inspector** and **Geometry Verifier**. Geometry Service remains a separate computation API. Technical IDs/kind `discrepancy_inspector` and `discrepancy_verifier`, Python class/factory names and environment variables are retained: the naming change does not alter routing or task identities. A2A cards show the new names with the existing skill IDs.

Default profiles use version `3`: their instructions explicitly describe Geometry compact 2.0—units/sign, null, quality, processing tolerance distinct from project acceptance tolerance, partial surface statistics and linked RGB/mask/overlay. For an initial setup, use version `3` in both registrations as shown above. Evidence mapping and preservation of earlier reviews are described in the [Geometry guide](capture-to-geometry.md#compact-result-20--agent-evidence). Already started tasks must retain their previous profile/version until completion: registration changes do not rewrite frozen tasks and may hold them until their saved configuration is restored. Domain JSON schemas remain version `1.0`.


### Google Cloud deployment — 2 November 2026

Geometry Inspector and Geometry Verifier are hosted as separate containers on
the Geometry Engine VM, with separate persistent SQLite task volumes:

- Geometry Inspector: `https://35.205.36.182.sslip.io/geometry-inspector`
- Geometry Verifier: `https://35.205.36.182.sslip.io/geometry-verifier`

Append `/.well-known/agent-card.json`, `/contracts` or `/docs` for discovery and
schemas. Task endpoints require the caller's dedicated Bearer token plus
`A2A-Version: 1.0` and `A2A-Extensions: urn:ai-inspector:delegation:v1`. Credentials
are server-only and distinct for Bob→Inspector, Inspector→Verifier and the
Inspector→Bob evidence tool. The initial deployment used profile version `2`,
project `demo-building`. Current code defaults to version `3`; check both live
agent cards and both caller registrations before changing routing. The deployment
registry and VM environment are independently managed, so publishing code alone
does not synchronize their versions or prove a complete inspection run.

The Cloudflare deployment registry includes only Geometry Inspector, with the
`inspection_evidence` tool grant. `INSPECTOR_TASK_TOKEN` and `INSPECTOR_TOOL_TOKEN`
are Workers Secrets; the verifier token is held only by the two specialist
services. Do not put credentials in this guide or the registry.

The VM polls `feat/ai-inspector-development` every two minutes for these agents;
Geometry Engine separately follows `geometry-engine`. Deployment configuration
is managed on the VM, outside this branch. The IP-based hostname is provisional:
check it after a VM stop/start. Hosting does not certify reconstruction accuracy
or establish a complete live inspection run.
