# Web deployment from GitHub

Target: existing Cloudflare Worker `ai-inspector`, domain `inspector.campuscompile.eu`.
Use Cloudflare Workers Builds → GitHub. Repository:
`TykhinBiriukov/ai-construction-inspector`.

Configuration:

- Production branch: `feat/ai-inspector-development`.
- Root directory: `ai_inspector/cloudflare/frontend`.
- Build command: leave empty (plain JS/static assets).
- Deploy command: `npx --yes wrangler@4.154.0 deploy`.
- Disable non-production branch/preview builds.
- If configuring build watch paths, include `ai_inspector/src/ai_inspector/web/**`
  and `ai_inspector/cloudflare/frontend/**`; assets live outside the build root.

This publishes the web UI and JS gateway only. It preserves the private Python
runtime, Hyperdrive and runtime Secrets. Backend changes still require a separate
runtime deployment first when new API endpoints are needed. SQL migrations are
not automatic. Do not add operator, device, Meta or Supabase secrets to the
frontend build. Keep the existing Durable Object name and binding from index.js.

The repository owner may need to install/approve the Cloudflare Workers and Pages
GitHub App for this repository. The collaborator account OlegKulikov09 has push
permission, but no repository admin permission. Once connected, contributors
with push permission to the production branch can deploy web changes by pushing;
they do not need a Cloudflare login. Treat that branch as production code.

Status on 28 October 2026: setup initiated, GitHub sign-in/integration approval
pending; automatic deployment is NOT yet enabled. Manual frontend deployment
remains available with the same deploy command from the root directory above.

Operator login uses sessionStorage scoped to the site and tab. Reloads retain the
login; explicit logout or HTTP 401 clears it. It is not a server-issued session
cookie and does not promise persistence after closing the browser. Never log or
commit tokens. Browsers that block sessionStorage fall back to manual login.
