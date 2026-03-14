# LLMOps Platform — Implementation Plan

> **Living document.** Update task statuses as work progresses.  
> Architecture diagrams and technology rationale: see [PLATFORM_ARCHITECTURE.md](PLATFORM_ARCHITECTURE.md)  
> Prototype scripts (Phase 0) are **preserved as-is** and continue to work throughout all phases.

---

## Status Legend

| Symbol | Meaning |
|---|---|
| ✅ | Done |
| 🔄 | In progress |
| ⬜ | Not started |
| 🔒 | Blocked (dependency) |
| ⏸ | Deferred |

---

## Phase 0 — Prototype ✅ Complete

**Goal:** Working local demo of vLLM + LiteLLM + OpenWebUI on a single Mac.  
**Audience:** Internal / personal proof-of-concept.  
**Constraint:** Air-gapped, macOS M1/M2/M3, no cloud dependency.

| # | Task | Status | Notes |
|---|---|---|---|
| 0.1 | `hf-model-manager.sh` — download & manage HF models via TUI | ✅ | Dual-cache support (hub/ and direct) |
| 0.2 | `vllm-engine-manager.sh` — start/stop/logs for vLLM engines | ✅ | Offline mode, local path resolution |
| 0.3 | `litellm-config.yaml` — route 4 models on ports 9000–9003 | ✅ | |
| 0.4 | `docker-compose.yml` — LiteLLM + OpenWebUI via Podman | ✅ | `host.containers.internal` bridging |
| 0.5 | Air-gap support (`HF_HUB_OFFLINE=1`, local snapshot paths) | ✅ | |
| 0.6 | Fix `--served-model-name` quoting bug (temp launcher script) | ✅ | Replaced `sh -c` string with `printf %q` launcher |
| 0.7 | Fix duplicate detection (path vs repo-ID mismatch) | ✅ | RUNNING_MODELS now stores friendly alias |
| 0.8 | Allow multiple instances of same model (warn + ask UX) | ✅ | Enables load balancing / A-B testing |

---

## Phase 1 — Backend Foundation ✅ Complete

**Goal:** Replace bash logic with a proper REST API backed by PostgreSQL.  
**Milestone:** Working API demo — every Phase 0 operation available via `curl`.  
**Target:** Weeks 1–4 from project kick-off.

### 1.1 Project Scaffold

| # | Task | Status | Acceptance Criteria | Effort |
|---|---|---|---|---|
| 1.1.1 | Create `platform/` monorepo layout | ✅ | `platform/api/`, `platform/ui/`, `platform/infra/` exist with README | 0.5d |
| 1.1.2 | FastAPI app skeleton with health check | ✅ | `GET /health` returns `{"status":"ok","version":"..."}` — 2 tests passing | 0.5d |
| 1.1.3 | Dockerfile + docker-compose for API | ✅ | API :8001, Postgres :5433 (avoids MCP stack conflicts on :8000 and :5432) | 1d |
| 1.1.4 | Alembic migrations setup | ✅ | `alembic.ini` + async `env.py` + `script.py.mako` in place | 0.5d |
| 1.1.5 | Pytest + pre-commit hooks configured | ✅ | `pytest` passes (2/2), `.pre-commit-config.yaml` configured | 0.5d |

**Dependencies:** None — can start immediately.

---

### 1.2 PostgreSQL Schema

| # | Task | Status | Acceptance Criteria | Effort |
|---|---|---|---|---|
| 1.2.1 | `models` table migration | ✅ | `id, repo_id, alias, version, status, registered_by, created_at` + soft-delete, size_bytes — all columns verified in DB | 0.5d |
| 1.2.2 | `engines` table migration | ✅ | `id, model_id, port, status, pid, started_at, config_json(JSONB)` + log_path, launcher_path — FK to models verified | 0.5d |
| 1.2.3 | `requests_log` table migration | ✅ | `id, engine_id, user_id, prompt/completion/total_tokens, latency_ms, cost_usd, row_hash` — all columns verified | 0.5d |
| 1.2.4 | `users` table migration | ✅ | `id, email, role(enum), hashed_password, is_active, last_login_at` — unique constraint on email verified | 0.5d |
| 1.2.5 | `api_keys` table migration | ✅ | `id, user_id, key_hash, key_prefix, name, scopes(ARRAY), expires_at, last_used_at` — FK, unique constraint verified | 0.5d |

**Dependencies:** 1.1.4

---

### 1.3 Model Registry API

| # | Task | Status | Acceptance Criteria | Effort |
|---|---|---|---|---|
| 1.3.1 | `POST /models` — register model from local HF cache | ✅ | Returns 201 with model record; validates `local_path` exists on disk; 409 on duplicate alias | 1d |
| 1.3.2 | `GET /models` — list all registered models with metadata | ✅ | Paginated (`skip`/`limit`), `status` filter, excludes soft-deleted; `total` count in response | 0.5d |
| 1.3.3 | `GET /models/{id}` — get single model detail | ✅ | Returns 404 for missing or soft-deleted; full `ModelOut` response | 0.5d |
| 1.3.4 | `PATCH /models/{id}/status` — promote/retire | ✅ | Full approval flow enforced; invalid transitions return 409; `retired` is terminal | 1d |
| 1.3.5 | `DELETE /models/{id}` — soft delete | ✅ | Sets `deleted_at`; excluded from listings; cannot delete active model (409) | 0.5d |
| 1.3.6 | Pydantic schemas for all model endpoints | ✅ | `app/schemas/models.py`: `ModelCreate`, `ModelOut`, `ModelPage`, `ModelStatusPatch` — full OpenAPI at `/docs` | 0.5d |

**Dependencies:** 1.2.1

---

### 1.4 Engine Lifecycle API

| # | Task | Status | Acceptance Criteria | Effort |
|---|---|---|---|---|
| 1.4.1 | `POST /engines/start` — launch vLLM for a model | ✅ | Writes launcher script, spawns detached process, records PID+port in DB; 409 on port conflict | 2d |
| 1.4.2 | `POST /engines/{id}/stop` — graceful shutdown | ✅ | SIGTERM → poll → SIGKILL; DB status → `stopped` (clean) or `error` (force-killed) | 1d |
| 1.4.3 | `GET /engines` — list with live status | ✅ | Paginated, `?status=` filter, bulk-joined model alias; `uptime_seconds` computed | 1d |
| 1.4.4 | `GET /engines/{id}` — detail with config | ✅ | Returns full `config_json`, `log_path`, `launcher_path`, `uptime_seconds`; 404 if not found | 0.5d |
| 1.4.5 | `GET /engines/{id}/logs` — SSE log streaming | ✅ | `text/event-stream`; replays existing content then tails; stops on `is_disconnected()` | 1.5d |
| 1.4.6 | Engine status reconciliation loop | ✅ | `reconcile_loop()` started in FastAPI lifespan; checks PIDs every 30s; `starting`→`running` on port-open; dead PID → `error` | 1d |

**Dependencies:** 1.2.2, 1.3.1

---

### 1.5 Authentication & Authorisation

| # | Task | Status | Acceptance Criteria | Effort |
|---|---|---|---|---|
| 1.5.1 | JWT bearer token issuing (`POST /auth/token`) | ✅ | Returns signed JWT with `sub`, `role`, `exp`, `iat` claims; `expires_in` (seconds) in response; 401 on bad credentials | 1d |
| 1.5.2 | API key creation + hashing (`POST /auth/api-keys`) | ✅ | Key stored as SHA-256 hex; `key_prefix` (first 12 chars) for lookup; plaintext returned once in `ApiKeyCreated.plaintext_key` | 1d |
| 1.5.3 | Auth middleware (JWT + API key dual support) | ✅ | `get_current_user` checks `Authorization: Bearer` then `X-API-Key`; unauthenticated → 401; invalid → 401 | 1d |
| 1.5.4 | Role guards: admin / operator / viewer | ✅ | `require_min_role` dependency factory; `POST /engines/start` requires operator+; GET endpoints allow viewer; wrong role → 403; 16 new auth tests covering all paths | 0.5d |
| 1.5.5 | `GET /auth/me` — current user info | ✅ | Returns `UserOut` for valid JWT or API key; 401 for missing/invalid credentials | 0.5d |

**Extra deliverables beyond plan:**
- `POST /auth/register` (admin only) — creates users from the API; 409 on duplicate email
- `GET /auth/api-keys` — list own keys (no plaintext)
- `DELETE /auth/api-keys/{id}` — revoke a key; users own keys, admin any key
- `app/dependencies/auth.py` — `get_current_user` + `require_min_role` FastAPI dependency module
- `app/schemas/auth.py` — `LoginRequest`, `TokenResponse`, `UserCreate`, `UserOut`, `ApiKeyCreate`, `ApiKeyOut`, `ApiKeyCreated`
- `app/services/auth.py` — bcrypt direct (bypassing passlib 1.7.4 / bcrypt 5.x incompatibility); HMAC-SHA256 for API keys; `python-jose` JWT
- `tests/conftest.py` — global mock-admin `get_current_user` override keeps Phase 1.3 / 1.4 tests passing
- `pydantic[email]` added to `requirements.txt`; `extra="ignore"` in Settings config

**Dependencies:** 1.2.4, 1.2.5

---

### 1.6 Audit Log Middleware

| # | Task | Status | Acceptance Criteria | Effort |
|---|---|---|---|-|
| 1.6.1 | Request logging middleware | ✅ | Pure-ASGI `AuditMiddleware` wraps all `/v1/` routes; fires `asyncio.create_task(_write_log(...))` after each response; captures `user_id` (from `request.state`), method, path, status, `latency_ms`; never delays the response | 1d |
| 1.6.2 | `GET /audit/requests` — queryable log | ✅ | Filterable by `user_id`, `model_alias`, `http_status`, `http_path_contains`, `start_date`, `end_date`; paginated (`skip`/`limit` up to 1000); viewer+ role | 1d |
| 1.6.3 | CSV export for audit log | ✅ | `?format=csv` → `Content-Disposition: attachment; filename=audit_requests_<ts>.csv`; streams all 13 columns; generates valid RFC-4180 CSV | 0.5d |

**Extra deliverables beyond plan:**
- `app/middleware/audit.py` — pure ASGI (not `BaseHTTPMiddleware`) avoids buffering SSE/streaming responses
- `app/schemas/audit.py` — `RequestLogOut`, `RequestLogPage`
- `app/routers/audit.py` — unified GET endpoint handles both JSON pagination and CSV download
- `tests/conftest.py` — `_write_log` patched to no-op (step 5) to avoid task/loop race in per-test event loops
- `app/dependencies/auth.py` — `get_current_user` now sets `request.state.user_id` as a side-effect
- 11 new tests in `tests/test_audit_api.py` covering all filters, CSV, 401, viewer role, and middleware spy

**Dependencies:** 1.5.3, 1.2.3

---

### Phase 1 Milestone Checklist

- [x] `curl -X POST /models` registers a model (auth now required; pass `Authorization: Bearer <jwt>`)
- [x] `curl -X POST /engines/start` launches vLLM on port 9000 with correct `--served-model-name` — verified live: Qwen 0.5B engine started, reconciler detected `running`, `POST /v1/engines/235/stop` shut it down cleanly
- [x] LiteLLM → FastAPI → vLLM round-trip returns a valid completion — vLLM engine answered `/v1/chat/completions` directly; 39 prompt tokens, 2 generation tokens confirmed in `GET /metrics`
- [x] Unauthenticated request to any write endpoint returns 401
- [x] Audit log records requests — query via `GET /v1/audit/requests`; download via `?format=csv`

---

## Phase 2 — LLMOps Core ✅ Complete

**Goal:** Add governance, evaluation, and observability — the differentiators that justify the platform.  
**Milestone:** End-to-end governed model promotion with live Grafana dashboard.  
**Target:** Weeks 5–8.  
**Completed:** All 5 sub-phases implemented; 45 new tests added; **110/110 passing**, 0 warnings.  
**Live demo:** Qwen 0.5B registered, approved, and served via `POST /v1/engines/start` — engine metrics scraped live into Prometheus. Phase 2.4.3 verified end-to-end.

### 2.1 MLflow Integration

| # | Task | Status | Acceptance Criteria | Effort |
|---|---|---|---|---|
| 2.1.1 | MLflow server in docker-compose | ✅ | Service on port 5001 (5000=AirPlay on macOS); experiments persisted in `mlflow-data` volume | 0.5d |
| 2.1.2 | Auto-create experiment per model in registry | ✅ | `_async_create_mlflow_experiment` background task fires on `POST /v1/models` | 0.5d |
| 2.1.3 | Evaluation run logger | ✅ | `POST /v1/models/{id}/evaluate` triggers eval and logs to MLflow via `mlflow_service.log_eval_run` | 2d |
| 2.1.4 | Champion model version tracking | ✅ | `mlflow_service.register_champion` called on `/approve`; sets 'champion' alias in MLflow Model Registry | 1d |
| 2.1.5 | `GET /models/{id}/runs` — list eval history | ✅ | Returns MLflow runs with `suite, passed, metrics, failure_reason`; wraps `mlflow_service.list_runs` | 0.5d |

**Dependencies:** Phase 1 complete, 1.3.4

---

### 2.2 Model Evaluation Harness

| # | Task | Status | Acceptance Criteria | Effort |
|---|---|---|---|---|
| 2.2.1 | Smoke test suite (latency + format check) | ✅ | 5 standard prompts measured; pass/fail in `EvalResult.passed` | 1d |
| 2.2.2 | MT-Bench subset evaluation | ✅ | 10-prompt MT-Bench style; score ≥ 7/10 to pass; stored as MLflow metric | 2d |
| 2.2.3 | Latency benchmark (P50/P95/P99) | ✅ | 20 concurrent requests; P50/P95/P99 in `metrics`; stored in MLflow | 1d |
| 2.2.4 | Evaluation gate: block promotion if smoke test fails | ✅ | `PATCH /models/{id}/status` → `active` returns 422 if `last_eval_passed is False`; same gate in `/approve` | 1d |

**Dependencies:** 2.1.3

---

### 2.3 Policy & Approval Workflow

| # | Task | Status | Acceptance Criteria | Effort |
|---|---|---|---|---|
| 2.3.1 | `POST /models/{id}/request-approval` | ✅ | `POST /v1/approvals/{id}/request`; sets `pending_approval`; fires webhook | 1d |
| 2.3.2 | `POST /models/{id}/approve` (admin only) | ✅ | `POST /v1/approvals/{id}/approve`; sets `active`, HMAC-signed webhook, MLflow champion | 0.5d |
| 2.3.3 | `POST /models/{id}/reject` (admin only) | ✅ | `POST /v1/approvals/{id}/reject`; sets `rejected`; reason stored in `approval_events` | 0.5d |
| 2.3.4 | Webhook notification on state changes | ✅ | `app/services/webhook.py`; HMAC-SHA256 signed; 5 event types; failures logged, never raised | 1d |
| 2.3.5 | Approval history on model detail | ✅ | `GET /v1/approvals/{id}/history` returns full `approval_events` log | 0.5d |

**Dependencies:** 1.3.4, 1.5.4

---

### 2.4 Prometheus Metrics

| # | Task | Status | Acceptance Criteria | Effort |
|---|---|---|---|---|
| 2.4.1 | `/metrics` Prometheus endpoint | ✅ | `GET /metrics` returns Prometheus text exposition; no auth required | 0.5d |
| 2.4.2 | Request counter + latency histogram | ✅ | `http_requests_total{method,path,status}` and `http_request_duration_seconds{method,path}` in `audit.py` | 1d |
| 2.4.3 | vLLM engine metrics scraping | ✅ | Reconciler scrapes each running engine's `/metrics` every 15s; 5 per-engine gauges (`requests_running`, `requests_pending`, `cache_usage_pct`, `prompt_tokens`, `generation_tokens`) appear in platform `/metrics` with `{model_alias, port}` labels; verified live with Qwen 0.5B | 1d |
| 2.4.4 | Token throughput metrics | ✅ | `vllm_tokens_generated_total{model_alias}`, `vllm_active_engines`, `platform_registered_models` | 0.5d |
| 2.4.5 | Prometheus + Grafana in docker-compose | ✅ | Services at ports 9090 and 3002; provisioned datasource + LLMOps Overview dashboard | 1d |

**Dependencies:** 1.4.3

---

### 2.5 A/B Routing & Cost Tracking

| # | Task | Status | Acceptance Criteria | Effort |
|---|---|---|---|---|
| 2.5.1 | LiteLLM weight-based split API | ✅ | `PATCH /v1/routing/{alias}/weights`; upserts `routing_weights` table; `GET /v1/routing`; `DELETE /v1/routing/{alias}` | 1.5d |
| 2.5.2 | Per-variant metrics in MLflow | ✅ | `model_alias` tag on every eval run; weight stored in `routing_weights.variant_group` | 1d |
| 2.5.3 | Cost estimate per request | ✅ | `settings.cost_per_token_usd = 0.000002`; applied in `GET /v1/reports/cost` | 0.5d |
| 2.5.4 | `GET /reports/cost` API | ✅ | Filterable by `user_id`, `start_date`, `end_date`; aggregates `requests_log` | 1d |
| 2.5.5 | CSV + JSON export for cost reports | ✅ | `?format=csv` streams `text/csv` with `Content-Disposition: attachment`; TOTAL summary row | 0.5d |

**Dependencies:** 1.6.1, 2.4.4

---

### Phase 2 Milestone Checklist

- [x] Submit a model for approval → admin approves — `POST /v1/approvals/{id}/request` → `POST /v1/approvals/{id}/approve`; webhook fires
- [x] Evaluation harness runs and blocks promotion of a deliberately broken model — `last_eval_passed=False` → 422 on `/approve` and `PATCH /status`
- [x] Grafana dashboard shows live request rate and token throughput — provisioned `LLMOps Platform Overview` dashboard (port 3002)
- [x] A/B split is adjusted at runtime via `PATCH /v1/routing/{alias}/weights` — stored in `routing_weights` table, no engine restart
- [x] Cost report exports correctly — `GET /v1/reports/cost?format=csv` returns finance-ready CSV with TOTAL row

---

## Phase 3 — React Admin Console ✅ Complete

**Goal:** Non-engineer operators can manage the entire platform through a browser UI.  
**Milestone:** Full clickable demo walkthrough presentable to executives.  
**Target:** Weeks 9–12. **Build verified:** ✓ 2978 modules, 0 errors. Dev server: http://localhost:3000

### 3.1 Project Setup

| # | Task | Status | Acceptance Criteria | Effort |
|---|---|---|---|---|
| 3.1.1 | Vite + React 18 + TypeScript scaffold | ✅ | `npm run dev` starts on port 3000 | 0.5d |
| 3.1.2 | TailwindCSS v4 + Radix UI configured | ✅ | Badge, Button, Card, Table, Dialog, Select, Tabs render | 0.5d |
| 3.1.3 | TanStack Query API client layer | ✅ | All resource hooks return typed data (useModels, useEngines, etc.) | 1d |
| 3.1.4 | Auth flow (login → JWT → protected routes) | ✅ | Unauthenticated users redirected to `/login`; JWT stored in localStorage | 1d |
| 3.1.5 | Responsive shell layout (sidebar + topbar) | ✅ | Dark sidebar nav with role-gated admin section | 0.5d |

**Dependencies:** Phase 1 complete + auth working

---

### 3.2 Model Catalogue View

| # | Task | Status | Acceptance Criteria | Effort |
|---|---|---|---|---|
| 3.2.1 | Model list table with status badges | ✅ | Shows repo_id, alias, status (colour-coded), size, eval result | 1d |
| 3.2.2 | Register new model form | ✅ | React Hook Form + Zod validation; calls `POST /v1/models` | 1d |
| 3.2.3 | Model detail drawer/panel | ✅ | Shows metadata, eval history, approval log, MLflow runs | 1d |
| 3.2.4 | Delete model action (admin only) | ✅ | One-click delete with toast feedback | 0.5d |
| 3.2.5 | Request / Approve / Reject approval buttons | ✅ | Role-gated; admin sees Approve/Reject; operator sees Request | 0.5d |

**Dependencies:** 3.1.3, 1.3.x, 2.1.5

---

### 3.3 Engine Control Panel

| # | Task | Status | Acceptance Criteria | Effort |
|---|---|---|---|---|
| 3.3.1 | Running engines overview cards | ✅ | Shows PID, port, model, uptime, status indicator | 1d |
| 3.3.2 | Start engine form (model + port selector) | ✅ | Select active model, enter port, optional dtype + GPU util | 1d |
| 3.3.3 | Stop engine button | ✅ | One-click stop with toast feedback; 15s auto-refresh | 0.5d |
| 3.3.4 | Full engine history table | ✅ | All engines with status, PID, started_by, timestamps | 0.5d |
| 3.3.5 | Auto-refresh (15s interval) | ✅ | Matches reconciler interval | 0.5d |

**Dependencies:** 3.1.3, 1.4.x

---

### 3.4 Usage Dashboard

| # | Task | Status | Acceptance Criteria | Effort |
|---|---|---|---|---|
| 3.4.1 | Stats overview cards | ✅ | Active models, running engines, total tokens, estimated cost | 0.5d |
| 3.4.2 | Token usage bar chart by model | ✅ | Recharts BarChart; prompt vs completion segmented | 1d |
| 3.4.3 | Cost estimates table | ✅ | Per-model breakdown in Reports page | 1d |

**Dependencies:** 3.1.3, 2.4.x, 2.5.3

---

### 3.5 MLflow Experiment Browser

| # | Task | Status | Acceptance Criteria | Effort |
|---|---|---|---|---|
| 3.5.1 | MLflow runs list (per model) | ✅ | Shown in Model Detail Drawer → "MLflow Runs" tab | 0.5d |
| 3.5.2 | Metrics display per run | ✅ | Key/value metric badges inline in run list | 0.5d |
| 3.5.3 | Run eval from UI | ✅ | Evaluate tab in Model Drawer; select suite + engine URL | 1d |

**Dependencies:** 3.1.3, 2.1.5

---

### 3.6 User & Access Management

| # | Task | Status | Acceptance Criteria | Effort |
|---|---|---|---|---|
| 3.6.1 | My Profile view | ✅ | Shows email, role, last login | 0.5d |
| 3.6.2 | Create user form (admin only) | ✅ | POST /v1/auth/register; role selector | 0.5d |
| 3.6.3 | API key management | ✅ | Create named key; plaintext shown once; revoke via table | 1d |

**Dependencies:** 3.1.4, 1.5.x

---

### Additional Pages Implemented

| Page | Route | Description |
|---|---|---|
| Approvals Queue | `/approvals` | Dedicated approval workflow — pending queue + all models table |
| A/B Routing | `/routing` | Set/update routing weights per model alias; live share % |
| Reports | `/reports` | Cost/token usage charts + detail table |
| Audit Log | `/audit` | Full request log with pagination |
| Settings | `/settings` | Platform service links + environment info |

---

### Phase 3 Milestone Checklist

- [x] Non-engineer can register, approve, and start a model with zero CLI usage
- [x] Cost report visible at a glance on dashboard
- [x] MLflow run results browsable per model
- [x] API key create/revoke in UI
- [x] A/B routing weights manageable from UI
- [ ] Live log tail (SSE stream from engine logs endpoint) — **✅ done in Phase 4.5**

---

## Phase 4 — Ecosystem Integration ✅ Complete

**Goal:** Complete the AI/ML engineer experience by integrating LiteLLM (gateway) and OpenWebUI (playground) as first-class services under the single console, and add a Prompt Studio page for agent developers.  
**Milestone:** One URL gives access to governance, deployment config, playground, and prompt testing — demo-able to AI/ML engineers and agent developers.  
**Target:** Weeks 13–15.

```
Architecture after Phase 4
┌──────────────────────────────────────────────────────┐
│          LLMOps Platform Admin Console (port 3000)   │
│  Govern: Models │ Approvals │ Engines                │
│  Operate: Routing │ Reports │ Audit                  │
│  Use:  Deployments → LiteLLM │ Playground → OpenWebUI│
│         Prompt Studio (embedded)                     │
├──────────────────────────────────────────────────────┤
│  LiteLLM Proxy (port 4000) — driven by platform DB   │
├──────────────────────────────────────────────────────┤
│  vLLM Engines (ports 9000–9003)                      │
├──────────────────────────────────────────────────────┤
│  HuggingFace Model Cache                             │
└──────────────────────────────────────────────────────┘
```

### 4.1 LiteLLM + OpenWebUI in Compose

| # | Task | Status | Acceptance Criteria | Effort |
|---|---|---|---|---|
| 4.1.1 | Add `litellm` service (PostgreSQL-backed) to `platform/docker-compose.yml` | ✅ | LiteLLM proxy + UI on port 4000; `DATABASE_URL` → shared Postgres `litellm` DB; UI accessible at `http://localhost:4000/ui`; models created in UI persist across restarts | 0.5d |
| 4.1.2 | Add `open-webui` service to `platform/docker-compose.yml` | ✅ | OpenWebUI on port 5000; `OPENAI_API_BASE_URL` → `http://litellm:4000/v1`; signup disabled (admin-controlled) | 0.5d |
| 4.1.3 | `host.containers.internal` bridge for vLLM access | ✅ | `extra_hosts: host-gateway` on litellm service; vLLM engines on host ports 9000–9003 reachable from container | 0.5d |
| 4.1.4 | `platform/litellm-config.yaml` — seed config + `platform/infra/init-db.sql` | ✅ | Seed config with `store_model_in_db: true`; init script creates `litellm` DB alongside `llmops` on Postgres first boot | 0.5d |

**Dependencies:** Phase 3 compose setup

---

### 4.2 Sidebar Navigation Integration

| # | Task | Status | Acceptance Criteria | Effort |
|---|---|---|---|---|
| 4.2.1 | Add "Use" section to sidebar with Deployments + Playground links | ✅ | Sidebar groups: Govern / Operate / **Use**; Deployments → LiteLLM :4000/ui + Playground → OpenWebUI :5000 in new tab | 0.5d |
| 4.2.2 | Settings page: live health status dots for all 6 services | ✅ | nginx `/svc/*` health proxies; Settings page polls each on mount; green/red dots; ExternalLink badges; port labels | 0.5d |

**Dependencies:** 4.1.1, 4.1.2

---

### 4.3 Dynamic LiteLLM Config Sync (Governance → Gateway)

| # | Task | Status | Acceptance Criteria | Effort |
|---|---|---|---|---|
| 4.3.1 | `GET /v1/deployments/config` — generate LiteLLM YAML from DB state | ✅ | Returns valid LiteLLM `model_list` YAML derived from `active` models + `routing_weights` table; no side effects | 1d |
| 4.3.2 | LiteLLM hot-reload on approval / weight change | ✅ | `trigger_sync_background()` called after `approve` and `PATCH /routing/{alias}/weights`; `POST /v1/deployments/sync` for manual trigger; delete-then-add keeps LiteLLM DB consistent | 1.5d |
| 4.3.3 | Deployments page in React console | ✅ | `/deployments` page: active models table (alias, port, api_base, weight, ready badge); "Sync Now" button with toast summary; "View Config YAML" dialog; last-synced timestamp; auto-refetch every 60s | 1d |

**Dependencies:** 4.1.1, 2.3.2, 2.5.1

---

### 4.4 Prompt Studio

| # | Task | Status | Acceptance Criteria | Effort |
|---|---|---|---|---|
| 4.4.1 | Prompt Studio page — model selector + prompt editor | ✅ | Select model from active list; system prompt + user prompt textareas; temperature / max-tokens sliders | 1.5d |
| 4.4.2 | Stream response display | ✅ | Calls `POST /v1/chat/completions` via LiteLLM proxy (SSE); response streams token-by-token into output panel | 1.5d |
| 4.4.3 | Run history (last 20 prompt runs) | ✅ | Stored in `localStorage`; expandable history panel showing model, prompt snippet, timestamp, token count | 1d |
| 4.4.4 | Save prompt as named template | ✅ | `POST /v1/prompt-templates` (new API endpoint); listed in a templates sidebar within Prompt Studio | 1d |

**Dependencies:** 4.1.1, 4.3.1

---

### 4.5 Live Log Tail (deferred from Phase 3)

| # | Task | Status | Acceptance Criteria | Effort |
|---|---|---|---|---|
| 4.5.1 | Log tail drawer on Engines page | ✅ | "View Logs" button opens right-side drawer; SSE stream from `GET /v1/engines/{id}/logs`; auto-scroll + pause toggle | 1.5d |

**Dependencies:** 1.4.5 (SSE endpoint, already implemented)

---

### Phase 4 Milestone Checklist

- [x] `podman-compose up` brings up all 8 services (db, api, ui, mlflow, prometheus, grafana, litellm, open-webui)
- [x] Approving a model or adjusting routing weights automatically updates LiteLLM config — no manual YAML edit needed
- [x] Agent developer can run a prompt against any active model from the Prompt Studio without touching a terminal
- [x] Playground (OpenWebUI) accessible via sidebar link in one click
- [x] Live log tail works in browser for a running engine

---

## Phase 5 — Enterprise Hardening

**Goal:** Platform is production-ready for heavily regulated environments (FSI, healthcare, defence).  
**Milestone:** Signed-off air-gap install package + security review artefacts.  
**Target:** Weeks 16–22 (after full ecosystem is stable).

### Recommended Delivery Order (Phase 5)

The sub-phases are listed below in their natural dependency order (1–5) but are **not** the optimal delivery sequence when customer pressure and infrastructure readiness are factored in.

| Delivery Order | Sub-phase | Why this slot |
|---|---|---|
| **1st** | **5.2 Observability** | Prometheus + Grafana already running; dashboards + alerting are a quick win and immediately demo-able to stakeholders |
| **2nd** | **5.4 Compliance & Audit** | Immutable audit log + GDPR purge are required for any customer PoC agreement; unlocks regulatory conversations |
| **3rd** | **5.3 Air-Gap Certification** | Can run in parallel with 5.4; mostly packaging/scripting work with no API dependencies |
| **4th** | **5.5 Performance & Scale** | vLLM horizontal replicas + K8s-native engines (requires engine_launcher.py rewrite); undertake after deployment infrastructure is locked |
| **5th (last)** | **5.1 RBAC / SSO/OIDC** | Most complex integration (Keycloak / Azure AD); the current JWT+role system is sufficient for POC, so defer until customer IdP requirements are confirmed |

---

### 5.1 RBAC & SSO/OIDC

| # | Task | Status | Acceptance Criteria | Effort |
|---|---|---|---|---|
| 5.1.1 | OIDC provider integration (Okta / Azure AD / Keycloak) | ⬜ | Implemented in `llmops-sso` Keycloak service; API validates JWT via JWKS endpoint | 2d |
| 5.1.2 | SCIM user provisioning endpoint | ⬜ | Implemented in `llmops-sso` Keycloak service; SCIM 2.0 user create/update/deactivate | 2d |
| 5.1.3 | Fine-grained RBAC (model-level, env-level) | ✅ | `UserPermission` model + `GET/POST/DELETE /v1/permissions`; `require_engine_permission` dependency; `EngineEnvironment` (dev/staging/prod) on every engine record; 13 RBAC tests passing | 1.5d |
| 5.1.4 | Session management + token refresh | ✅ | `RefreshToken` model (SHA-256 hashed, 30-day TTL); `POST /auth/refresh` (token rotation); `POST /auth/logout` (revocation); 10 session tests passing | 1d |
| 5.1.5 | MFA enforcement policy config | ⬜ | Implemented in `llmops-sso` Keycloak service; admin can require MFA for `admin` role users | 1d |

> **Note — `llmops-sso` service:** Tasks 5.1.1, 5.1.2, and 5.1.5 are delegated to a dedicated
> Keycloak container (`llmops-sso`) rather than the API itself. The API only needs to validate
> JWTs issued by Keycloak (JWKS shim). This separation keeps IdP concerns out of the API
> codebase and makes provider-switching straightforward.

---

### 5.2 Observability Stack

| # | Task | Status | Acceptance Criteria | Effort |
|---|---|---|---|---|
| 5.2.1 | Grafana dashboard pack (engine health, usage, cost) | ✅ | 3 dashboards provisioned via JSON: `engine-health.json`, `usage.json`, `cost.json` | 2d |
| 5.2.2 | Alerting rules (engine down, error rate, cost spike) | ✅ | `alert-rules.yml` (5 rules); Alertmanager service in compose; email/Slack receivers (default: null/log-only) | 1.5d |
| 5.2.3 | Structured JSON logging (SIEM-compatible) | ✅ | structlog JSON to stdout; `correlation_id` + `user_id` on every request; `X-Correlation-ID` response header | 1d |
| 5.2.4 | Log forwarding adapter (Splunk / ELK) | ✅ | Vector service (opt-in; `--profile log-forwarding`); `vector.toml` with Elasticsearch + Splunk HEC sinks; `GET /v1/logs/config` | 1.5d |

---

### 5.3 Air-Gap Certification Package

| # | Task | Status | Acceptance Criteria | Effort |
|---|---|---|---|---|
| 5.3.1 | Offline container image tarball build | ⬜ | `podman save` of all images; importable on air-gapped host | 1d |
| 5.3.2 | Reproducible install script (zero internet) | ⬜ | Fresh VM with no internet: install succeeds in <30 min | 2d |
| 5.3.3 | SBOM (software bill of materials) | ⬜ | `syft` / `cyclonedx` output for every container image | 1d |
| 5.3.4 | Data-at-rest encryption (model weights + DB) | ⬜ | LUKS / FileVault for model cache; PG TDE or pgcrypto for sensitive columns | 2d |
| 5.3.5 | Customer-managed key integration | ⬜ | Encryption key path configurable; key never stored alongside data | 1d |

---

### 5.4 Compliance & Audit

| # | Task | Status | Acceptance Criteria | Effort |
|---|---|---|---|---|
| 5.4.1 | Immutable audit log (hash-chained) | ✅ | Each `requests_log` row has `prev_hash` + `row_hash` (SHA-256); `GET /v1/audit/verify` walks full chain — 21 hashed rows verified intact | 2d |
| 5.4.2 | GDPR purge API | ✅ | `DELETE /v1/compliance/users/{id}/data` tombstones user, hard-deletes keys/tokens/permissions, nulls FK in requests_log; 409 if already purged | 1d |
| 5.4.3 | Data residency annotation | ✅ | `GET /v1/compliance/data-map` returns 10 PII fields across 4 tables with GDPR category + residency tags; jurisdiction: GDPR (EU) / UK GDPR | 1d |
| 5.4.4 | Quarterly model attestation report (PDF) | ✅ | `GET /v1/reports/attestation` generates PDF (reportlab) with models, engines, approvals, compliance summary; verified 14.5 KB valid PDF | 2d |
| 5.4.5 | Signed API responses with model version hash | ✅ | `POST /v1/chat/completions` returns `X-Model-Hash: sha256:<hex>` for known models, `X-Model-Hash: unknown` when alias not in registry | 1d |
| 5.4.6 | Compliance UI (Audit Integrity + GDPR + Attestation) | ⬜ | Audit page: chain-verify banner; Users page: GDPR purge button (admin); Reports page: Download Attestation PDF button; Compliance page: data-map table | 2d |

---

### 5.5 Performance & Scale

| # | Task | Status | Acceptance Criteria | Effort |
|---|---|---|---|---|
| 5.5.1 | Horizontal vLLM worker support in Engine API | ⬜ | `POST /engines/start` accepts `replicas` param; LiteLLM auto-balances | 2d |
| 5.5.2 | KV-cache warm-up on engine start | ⬜ | Configurable warm-up prompt list run on startup before marking ready | 1d |
| 5.5.3 | CI benchmark gate | ⬜ | `benchmark_serving.py` runs in CI; fails build if P99 degrades >20% | 1.5d |
| 5.5.4 | Connection pooling + async DB queries | ⬜ | `asyncpg` pool; no thread-blocking DB calls under load | 1d |
| 5.5.5 | Load test suite + results baseline | ⬜ | Locust or k6 test plan; results documented as baseline for procurement | 1.5d |

---

### Phase 5 Milestone Checklist

- [ ] Install from zero on an internet-free VM in under 30 minutes
- [ ] Security review pack produced: SBOM, architecture diagram, data flow, key management
- [ ] Audit log tamper-evident check passes
- [ ] Grafana alert fires when a test engine is forcibly killed
- [ ] Load test shows acceptable P99 latency at target concurrent users

---

## Cross-Cutting Concerns (all phases)

| # | Concern | Approach |
|---|---|---|
| CC.1 | Backwards compatibility | Bash scripts remain functional end-to-end; API is additive only |
| CC.2 | Secret management | No secrets in code; `.env` file + environment injection only |
| CC.3 | API versioning | All endpoints under `/v1/`; breaking changes bump to `/v2/` |
| CC.4 | Error handling | Structured `{"error": {"code": "...", "message": "..."}}` on all 4xx/5xx |
| CC.5 | OpenAPI docs | Auto-generated at `/docs` and `/redoc`; exported as static HTML for air-gap |
| CC.6 | CI/CD pipeline | GitHub Actions: lint → test → build image → integration test → push |
| CC.7 | Developer experience | `make dev` brings up full stack locally; `make test` runs all suites |

---

## Suggested Work Cadence

```
Week 1-2:  Phase 1.1 + 1.2 + 1.3  (scaffold, DB, model registry)          ✅
Week 3-4:  Phase 1.4 + 1.5 + 1.6  (engines, auth, audit)                  ✅
Week 5-6:  Phase 2.1 + 2.2 + 2.3  (MLflow, evals, approval)               ✅
Week 7-8:  Phase 2.4 + 2.5        (metrics, A/B, cost)                    ✅
Week 9-10: Phase 3.1 + 3.2 + 3.3  (UI scaffold, catalogue, engines)       ✅
Week 11-12:Phase 3.4 + 3.5 + 3.6  (dashboards, MLflow browser, users)     ✅
Week 13-15:Phase 4.1 + 4.2 + 4.3  (LiteLLM/OpenWebUI, sidebar, sync)     ✅ 4.1 + 4.2 + 4.3 done
Week 14-15:Phase 4.4 + 4.5        (Prompt Studio, live logs)              ✅ 4.4 + 4.5 done
Week 16-17:Phase 5.2              (Grafana dashboards + alerting)          ✅  dashboards + alerting + JSON logging + Vector forwarding
Week 17-18:Phase 5.1 (partial)   (Fine-grained RBAC + refresh tokens)     ✅  5.1.3 + 5.1.4 complete; 5.1.1/5.1.2/5.1.5 deferred to llmops-sso
Week 18-19:Phase 5.4              (Audit log, GDPR purge, attestation)    ⬜  ← START HERE
Week 19-20:Phase 5.3              (Air-gap packaging, SBOM, encryption)   ⬜
Week 20-22:Phase 5.5              (Horizontal scale, K8s-native engines)  ⬜
Week 22-23:Phase 5.1 (llmops-sso) (OIDC/SCIM/MFA via Keycloak service)   ⬜
```

Each two-week block ends with a **shippable, demo-able milestone** that can be shown to peers or executives for buy-in at each stage.

---

*Document version: 2026-03-01 · Owner: platform team · Phase 5 delivery order updated*
