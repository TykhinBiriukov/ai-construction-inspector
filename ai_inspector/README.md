# AI Inspector backend

Inspector owns chat/agent execution, capture sessions, source validation and Geometry handoff. The main agent uses Meta Model API / Muse Spark 1.3, predefined tools and registered inspector services. Geometry completion alone is not an inspection verdict. Code and this repository's guides describe implemented behavior; the [team API proposal](../docs/iphone-geometry-bim-inspector-api-v0.1.md) is a draft, not a released contract.

## Start locally

Python 3.12+ is required. From this directory in PowerShell, create the environment if missing:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\python.exe -m uvicorn ai_inspector.main:app --host 127.0.0.1 --port 8000
```

Create an ignored `.env` from [.env.example](.env.example) if needed. Set distinct random operator/device tokens; preserve existing credentials and data on restart. Settings load from the working directory's `.env`, with process environment taking precedence. The example lists configuration names, not working credentials. A fresh clone is not preconfigured.

Open `/` for the operational console or `/ui/demo.html` for the document/chat workspace. HTTP request schemas are generated at `/docs` and `/openapi.json`; [`api.py`](src/ai_inspector/api.py) and its Pydantic models are authoritative for fields and limits. Check `/v1/capabilities` for the running instance's limits/configuration; configuration is not a successful service call.

## Integration guides

| Concern | Guide |
|---|---|
| Agent roles, registration, A2A and recovery | [Agent runtime](docs/agent-chat.md) |
| Operator UI, chat/files, intake and capture creation | [Web integration](docs/web-integration.md) |
| Device authentication, assignment, capture/upload and reconnect | [iPhone integration](docs/ios-integration.md) |
| Single-ZIP capture layout | [ZIP format](docs/zip-capture.md) |
| Accepted sources and Geometry job/status/result | [Geometry handoff](docs/capture-to-geometry.md) |
| Cloud architecture, migrations, secrets and CI/manual deployment | [Cloud deployment](docs/cloudflare-deployment.md) |
| Independent photo/drawing analysis | [Minion service](../drawing_photo_service/README.md) |
| Viewer dependency build and licenses | [Viewer](viewer/README.md) |

## Persistence and optional hosting

Native development defaults to SQLite and local immutable BIM blobs under `INSPECTOR_DATA_DIR`; preserve the database and models together. `INSPECTOR_DATABASE_URL` selects PostgreSQL; `INSPECTOR_BIM_STORAGE=supabase` selects private remote blobs. Do not reuse an old SQLite snapshot as a live writer after switching to cloud persistence.

[compose.yaml](compose.yaml) and [Dockerfile](Dockerfile) provide a native single-process deployment: from this directory run `docker compose up -d --build`, supply `.env`, keep the `inspector-data` volume, and terminate public HTTPS at a reverse proxy. SQLite mode needs its persistent volume and one writer deployment. Back up SQLite through its backup API with the models directory; copying a live database without its WAL is unsafe.

The optional [Mac hosting helper](scripts/host_mac.py) supports `start/status/restart/stop`; [start-remote-test.ps1](scripts/start-remote-test.ps1) is a temporary tunnel workflow. Use these only when a temporary test host is intended. Read any current private connection files from that host; the repository does not establish that a process or tunnel is running.

## Verification

The optional companion workspace keeps the full Codex regression suite, fixtures and reports outside this checkout at `local-checks/inspector/`; its `run-tests.ps1` imports this backend. If present, run it from the companion root. A plain clone may not contain these files. Tracked teammate tests in this repository are only a subset; do not describe their discovery command or historical counts as a full current suite. Verification data and new assistant tests stay outside Git, per [AGENTS](../AGENTS.md).

Live model access, a real phone, external Geometry and deployed database permissions require their own integration checks. Durable sessions, typed schemas and passing local fixtures do not establish those results.
