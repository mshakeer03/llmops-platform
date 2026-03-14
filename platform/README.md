# LLMOps Platform

Self-hosted, air-gapped LLM operations platform for regulated enterprise customers.

## Repository Layout

```
platform/
├── api/               FastAPI backend — model registry, engine lifecycle, auth, audit
├── ui/                React admin console
├── infra/             Prometheus / Grafana configs and provisioning
├── docker-compose.yml Consolidated stack (8 services)
└── .env.example       Environment variable reference
```

## Quick Start — Full Stack (Podman / Docker)

```bash
cd platform

# 1. Create your .env (edit passwords as needed)
cp .env.example .env

# 2. Build images and start all services
podman-compose up --build -d          # or: docker compose up --build -d

# 3. Open the admin console
open http://localhost:3000
# Login: admin@llmops.local / changeme

# 4. Watch logs
podman-compose logs -f api ui

# 5. Stop everything
podman-compose down
```

| Service    | URL                       | Notes                                        |
|------------|---------------------------|----------------------------------------------|
| UI         | http://localhost:3000     | React admin console (nginx)                  |
| API        | http://localhost:8001     | FastAPI + Swagger `/docs`                    |
| LiteLLM    | http://localhost:4000     | Proxy + UI at `/ui` (admin / `LITELLM_MASTER_KEY`) |
| OpenWebUI  | http://localhost:5000     | Chat playground backed by LiteLLM            |
| MLflow     | http://localhost:5001     | Experiment tracking                          |
| Prometheus | http://localhost:9090     | Metrics scraper                              |
| Grafana    | http://localhost:3002     | Dashboards (admin / llmops-dev)              |
| PostgreSQL | localhost:5433            | `llmops` DB (platform) + `litellm` DB (proxy) |

---

## Model Lifecycle

The platform enforces a governance-first lifecycle. Every model must pass through the approval and evaluation gates before serving traffic. The lifecycle is fully reversible in the opposite order.

### Forward Path (go-live)

```
┌─────────────────────────────────────────────────────────────────────────────┐
│  STEP  │  ACTION                             │  STATUS (after)              │
├────────┼─────────────────────────────────────┼──────────────────────────────┤
│   1    │  Download weights (HF Manager)      │  — (cached on disk)          │
│   2    │  Register model                     │  pending                     │
│   3    │  Request approval                   │  pending_approval            │
│   4    │  Admin approves                     │  approved                    │
│   5    │  Run evaluation harness             │  approved + last_eval_passed │
│   6    │  Activate (PATCH status → active)   │  active ✓                    │
│   7    │  Start vLLM engine                  │  engine: starting → running  │
└─────────────────────────────────────────────────────────────────────────────┘
```

> **Admin shortcut:** An admin can transition directly `pending → active`, bypassing steps 3–6 (useful for trusted internal models or dev environments). The evaluation gate at step 5 blocks activation if `last_eval_passed = false`.

### Reverse Path (retirement)

```
┌─────────────────────────────────────────────────────────────────────────────┐
│  STEP  │  ACTION                             │  STATUS (after)              │
├────────┼─────────────────────────────────────┼──────────────────────────────┤
│   7    │  Stop vLLM engine                   │  engine: stopped             │
│   6    │  Retire model (PATCH → retired)     │  retired                     │
│        │  (auto-handled by De-register if    │                              │
│        │   no engines running)               │                              │
│   5–2  │  De-register (soft-delete record)   │  deleted_at set, hidden      │
│   1    │  Delete HF cache (free disk)        │  — (weights removed)         │
└─────────────────────────────────────────────────────────────────────────────┘
```

> **De-register guard:** De-registering a model with active/starting engines returns 409. Stop all engines for that model first. If no engines are running, de-register auto-retires and soft-deletes in one step.

### Status State Machine

```
pending ──► pending_approval ──► approved ──► active ──► retired
   │                                │
   └──► active (admin bypass)       └──► rejected ──► pending (re-submit)
```

---

## Local Development (hot-reload)

```bash
# Terminal 1 — API with auto-reload
cd platform/api && uvicorn app.main:app --reload --port 8001

# Terminal 2 — UI dev server (proxies /v1 to localhost:8001)
cd platform/ui && npm run dev
```

## Phase 0 Bash Prototype

The original bash tooling remains fully functional and is the fastest demo vehicle:

```bash
# Model downloads
./hf-model-manager.sh

# Engine management
./vllm-engine-manager.sh
```

## Further Reading

| Document | Purpose |
|---|---|
| [IMPLEMENTATION_PLAN.md](../IMPLEMENTATION_PLAN.md) | Phased roadmap and task status |
| [PLATFORM_ARCHITECTURE.md](../PLATFORM_ARCHITECTURE.md) | Architecture diagrams and technology rationale |
| [DEPLOYMENT.md](../DEPLOYMENT.md) | Production deployment guide (Kubernetes, cloud, on-prem) |
