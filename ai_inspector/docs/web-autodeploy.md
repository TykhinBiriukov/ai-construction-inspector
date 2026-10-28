# GitHub Actions: Inspector backend and web deployment

Workflow: `.github/workflows/deploy-inspector-web.yml`.
Targets: private `ai-inspector-runtime` and public `ai-inspector` at https://inspector.campuscompile.eu.

Pushes to `feat/ai-inspector-development` deploy changed components: Python/shared
source or cloud runtime/config/lockfiles trigger backend; web assets/gateway trigger
frontend. Workflow changes and manual runs deploy both, backend first. Pull requests and other
branches do not deploy. Deployments run sequentially. Node 24 and Wrangler
4.154.0 are selected explicitly. The workflow has read-only repository permission.

## One-time setup

1. In the owner's Cloudflare account, open **Manage Account → Account API Tokens
   → Create Token**. Use an account-owned token, not the Global API Key.
2. Name it `github-inspector-web`. Choose Workers permission scope
   **Specified Workers / Individual Workers**, select **ai-inspector** and **ai-inspector-runtime**,
   and role **Editor**. Do not grant access to all Workers.
   Alternatively the existing Worker's **Settings → Builds → API token for
   external CI/CD → Create token** opens the scoped creation flow; review the scope.
3. The domain is already connected. Do not add zone-wide DNS or Workers Routes
   Write for ordinary code updates. Changing domain connections is a separate
   administrative operation. Set an expiry appropriate for the project; replace
   the GitHub secret when rotating the token.
4. Transfer the token privately to Tykhin, who owns the repository, or have an
   authorized repository administrator enter it directly. Never put it in a
   commit, issue, chat message or workflow YAML.
5. In GitHub repository **Settings → Secrets and variables → Actions → New
   repository secret**, add:

   | Name | Value |
   | --- | --- |
   | `CLOUDFLARE_API_TOKEN` | The scoped Cloudflare token |
   | `CLOUDFLARE_ACCOUNT_ID` | `80238bcf5ce46836f5344aa9ee5c6463` |

No Meta, Supabase, operator or device credentials are needed by this workflow.
The Cloudflare GitHub App / Workers Builds connection is not required.

## First run and subsequent changes

After adding both secrets, go to **Actions → Deploy Inspector backend and web** and rerun
its initial failed run if it failed due to absent secrets. Otherwise push a real
web change to the production branch. `workflow_dispatch` is also defined, but
GitHub's Run workflow button generally requires the workflow to exist on the
repository's default branch; this project currently uses `main` as default.
If added to main for visibility, select `feat/ai-inspector-development` when
running it manually; the job deliberately skips other refs.

Inspect the deployment step for success and open the site. A missing-secret
failure is expected until setup is complete. Do not broaden token permissions
blindly after a 403; inspect the failing API operation first.

Backend uses Python 3.14, uv 0.13.6, locked dependencies and deploy.sh, which
refreshes the shared source copy before packaging. Existing Cloudflare Secrets
remain in place. If backend fails, frontend is not deployed. SQL migrations are
never run by this workflow; apply compatible additive migrations separately
before pushing dependent code. Keep the existing Durable Object
name/binding in index.js. Contributors who can change this production branch
can publish web code; protect the branch with reviews if desired.

Status: web CI verified on 28 October 2026 (run 40757139229). Both repository
secrets are installed. Backend CI added in 955bbd4; run 40757679080 successfully deployed backend
then frontend using the existing scoped token.

Operator login uses sessionStorage scoped to the site and tab. Reloads retain
login; logout or HTTP 401 clears it. Browsers blocking storage fall back to manual
login. Persistence after closing the browser is not promised.

References:
- https://developers.cloudflare.com/workers/ci-cd/external-cicd/github-actions/
- https://developers.cloudflare.com/workers/authorization/workers/

CI uses `versions upload` with a unique run/attempt tag, then `versions deploy`
at 100%. This preserves existing domain and cron triggers. A direct `wrangler
deploy` with the scoped token uploaded code but failed reconciling custom domains
(code 10084); domain changes must be performed separately by an administrator.
