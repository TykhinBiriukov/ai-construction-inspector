# AI Construction Inspector

One user-facing AI Inspector, with independently operated backend, mobile and computation components.

| Component | Source and setup |
|---|---|
| Inspector backend, orchestrator, agents and web workspace | [ai_inspector](ai_inspector/README.md) |
| Drawing/photo analysis and evidence review | [drawing_photo_service](drawing_photo_service/README.md) |
| RGB-D reconstruction and scan-versus-BIM computation | [Geometry Engine](geometry-engine/README.md) |
| iPhone capture application | [iOS](ios/README.md) |
| BIM-relative localization | [Localization Service](localization_service/README.md) |
| Photo/IFC progress monitoring and weekly 4D viewer | [Progress4D](progress4d_service/README.md) |

Components retain separate environments, credentials, state stores and deployment processes. Follow each component's setup guide; their dependency versions are not a single shared Python environment.

Backend API contracts live in [Inspector docs](ai_inspector/docs/agent-chat.md); mobile integration is described in the [phone/server guide](docs/inspector-server-integration.md). The [cross-service API proposal](docs/iphone-geometry-bim-inspector-api-v0.1.md) remains a draft, not a released runtime contract. The legacy SQL in supabase/ is not required by the selected signed-upload flow.

Consolidating source code in main does not change existing deployment source branches. Hosting and live integration remain separate operational steps.
