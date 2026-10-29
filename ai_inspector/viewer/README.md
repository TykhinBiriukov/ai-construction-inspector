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
