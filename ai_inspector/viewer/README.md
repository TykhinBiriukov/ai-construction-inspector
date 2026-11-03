# Document viewer

Local browser rendering with pinned Three.js, web-ifc and PDF.js. No external
viewer service. Run `npm ci --ignore-scripts && npm run build` here; GitHub Actions
also builds this directory. Output is committed in ../src/ai_inspector/web/vendor.

- Three.js (MIT): https://github.com/mrdoob/three
- web-ifc (MPL-2.0): https://github.com/ThatOpen/engine_web-ifc/tree/0.0.78
- PDF.js (Apache-2.0): https://github.com/mozilla/pdf.js

Bundled license notices and full upstream licenses ship beside the assets.
Unmodified upstream package sources are available from the npm versions in
package-lock.json. Project integration source is viewer.js.

`Viewer` remains the document-selection component. `GeometryModel` shares its
IFC renderer, lifecycle and controls, and adds bounded PLY mesh/point-cloud
previews. IFC review markers and focus resolve exact GlobalIds in the reference
model. Separate scan sources do not imply a verified coordinate transform.

The inspection shell lives in `web/geometry-viewer.js` and
`web/geometry-viewer.css`. Its API and evidence constraints are documented in
[capture-to-geometry](../docs/capture-to-geometry.md#inspection-results-viewer).
