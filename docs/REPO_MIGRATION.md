# Repository Migration Plan

## Overview

The workspace is currently a monorepo that contains two logically distinct things:

| Thing | Purpose |
|---|---|
| **Local demo scripts** | `hf-model-manager.sh`, `vllm-engine-manager.sh`, local vLLM build (`vllm/`) — terminal UI demo for stakeholder sessions on a MacBook Pro |
| **LLMOps Platform** | `platform/`, `deploy/`, `docs/` — the full-stack product (API + UI + 8 services + K8s deployment scripts) |

Splitting them into two repos allows the platform to have its own independent CI/CD, versioning, and contribution guidelines without being coupled to local demo tooling.

---

## Target State

### Repo A — `llmops-demo` (this repo, trimmed)

Local demo tools for Apple Silicon. Used for quick in-person demos without any server infrastructure.

```
llmops-demo/
├── hf-model-manager.sh       ← HF model download + cache TUI
├── vllm-engine-manager.sh    ← Local vLLM engine launcher TUI
├── vllm/                     ← vLLM source (submodule or copy)
├── vllm_env/                 ← local Python venv (gitignored)
├── Dockerfile.prebuilt       ← local prebuilt image
├── README.md                 ← updated: points to llmops-platform repo
└── start_vllm_metal.sh       ← engine launcher script
```

### Repo B — `llmops-platform` (new repo)

The full-stack enterprise LLMOps platform.

```
llmops-platform/
├── platform/               ← Full-stack: API + UI + 8 services
│   ├── api/
│   ├── ui/
│   ├── k8s/
│   └── helm/
├── deploy/                 ← Deployment CLI (master.py, providers/)
├── docs/                   ← Architecture, deployment, implementation docs
│   ├── DEPLOYMENT.md
│   ├── IMPLEMENTATION_PLAN.md
│   ├── PLATFORM_ARCHITECTURE.md
│   └── KAF-OnPrem-LLM-Advisory.md
├── docker-compose.yml      ← Local compose stack
├── Dockerfile              ← API Dockerfile (used by compose)
├── litellm-config.yaml
├── validate-setup.sh
├── README.md               ← Platform-focused README
└── .github/
    └── workflows/
        ├── ci.yml          ← lint + test + build (future)
        └── release.yml     ← image push on tag (future)
```

---

## Migration Steps

### Step 1 — Prepare the new repository

```bash
# Create a new directory and initialise a fresh git repo
mkdir ~/dev/llmops-platform
cd ~/dev/llmops-platform
git init
git remote add origin https://github.com/<your-org>/llmops-platform.git
```

### Step 2 — Copy platform content

```bash
MONO_ROOT="/Users/shakeer./vllm_poc"
NEW_REPO="$HOME/dev/llmops-platform"

# Core platform
cp -r "$MONO_ROOT/platform"          "$NEW_REPO/"
cp -r "$MONO_ROOT/deploy"            "$NEW_REPO/"
cp -r "$MONO_ROOT/docs"              "$NEW_REPO/"

# Top-level config files
cp "$MONO_ROOT/docker-compose.yml"   "$NEW_REPO/"
cp "$MONO_ROOT/Dockerfile"           "$NEW_REPO/"
cp "$MONO_ROOT/litellm-config.yaml"  "$NEW_REPO/"
cp "$MONO_ROOT/validate-setup.sh"    "$NEW_REPO/"
cp "$MONO_ROOT/README.md"            "$NEW_REPO/"
```

### Step 3 — Add .gitignore and .dockerignore

```bash
cat > "$NEW_REPO/.gitignore" << 'EOF'
# Python
__pycache__/
*.pyc
*.pyo
.venv/
venv/
env/
*.egg-info/
dist/
build/

# Node
node_modules/
platform/ui/dist/

# Secrets (NEVER commit)
.env
.env.*
!.env.example
platform/k8s/secrets/secrets.yaml

# Logs
*.log
vllm_logs/

# macOS
.DS_Store

# IDE
.vscode/settings.json
.idea/
EOF
```

### Step 4 — Initial commit

```bash
cd "$NEW_REPO"
git add .
git commit -m "chore: initial platform monorepo split (Phases 0-4 complete)"
git branch -M main
git push -u origin main
```

### Step 5 — Tag the first release

```bash
git tag -a v0.4.0 -m "Platform Phases 0-4: full-stack LLMOps with governance, evaluation and live engines"
git push origin v0.4.0
```

### Step 6 — Clean up the demo repo

In the original `vllm_poc` repo, remove the files that moved to the platform repo and update the README to link to the new repo:

```bash
cd "/Users/shakeer./vllm_poc"

# Remove files migrated to platform repo
rm -rf platform/ deploy/ docs/
rm docker-compose.yml Dockerfile litellm-config.yaml validate-setup.sh

# Update README to point to new repo
# Replace the README with a lean demo-focused one
```

Update `README.md` to contain:

```markdown
# LLMOps Demo Tools

Quick local demo of vLLM on Apple Silicon for stakeholder sessions.

## Scripts

- `hf-model-manager.sh` — download and cache HuggingFace models
- `vllm-engine-manager.sh` — start/stop local vLLM engines

## Platform

The full-stack LLMOps Platform (API, UI, K8s deployment, governance) has been
moved to its own repository:

👉 https://github.com/<your-org>/llmops-platform
```

---

## What Goes Where — Decision Table

| File / Folder | Demo Repo | Platform Repo | Notes |
|---|:---:|:---:|---|
| `hf-model-manager.sh` | ✅ | — | Local demo only |
| `vllm-engine-manager.sh` | ✅ | — | Local demo only |
| `start_vllm_metal.sh` | ✅ | — | macOS Metal |
| `Dockerfile.prebuilt` | ✅ | — | Prebuilt demo image |
| `vllm/` | ✅ | — | vLLM source for local build |
| `vllm_env/` | ✅ (gitignored) | — | Local venv |
| `platform/` | — | ✅ | Full-stack product |
| `deploy/` | — | ✅ | Deployment CLI |
| `docs/` | — | ✅ | Architecture & deployment docs |
| `docker-compose.yml` | — | ✅ | 8-service compose stack |
| `Dockerfile` | — | ✅ | API image build |
| `litellm-config.yaml` | — | ✅ | LiteLLM proxy config |
| `validate-setup.sh` | — | ✅ | Post-deploy health check |
| `README.md` | ✅ (trimmed) | ✅ (platform) | Both repos need a README |

---

## Recommended Git Branch Strategy (Platform Repo)

```
main          ← production-ready, tagged releases
develop       ← integration branch (merge PRs here first)
feature/*     ← feature branches (e.g. feature/phase-5-observability)
hotfix/*      ← urgent production fixes
```

---

*Document version: 2026-03-01*
