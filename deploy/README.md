# LLMOps Platform — Deployment Scripts

Interactive deployment toolkit for on-prem and cloud environments.

## Quick Start

```bash
# Install dependencies (once)
pip install -r deploy/requirements.txt

# Launch the interactive menu
python deploy/master.py
```

The menu will guide you through selecting a **target** and an **action**.

![screenshot placeholder]

## Command-Line Flags

```bash
# Skip prompts — fully scripted deploy to Azure
python deploy/master.py --provider azure --action deploy --yes

# List all available targets
python deploy/master.py --list

# Build and push images only (no K8s changes)
python deploy/master.py --provider aws --action build

# Package all images for air-gap deployment
python deploy/master.py --action airgap
```

## Supported Targets

| Key      | Target                          | Notes                              |
|----------|---------------------------------|------------------------------------|
| `local`  | docker-compose / podman-compose | Single-machine dev/demo            |
| `azure`  | Azure AKS                       | ACR + Azure Files + optional GPU VM|
| `aws`    | AWS EKS                         | ECR + EFS + optional GPU EC2       |
| `gcp`    | GCP GKE                         | Artifact Registry + Filestore      |
| `onprem` | Bare-metal / OpenShift          | Any kubectl-accessible cluster     |

## Supported Actions

| Action     | Description                                                     |
|------------|-----------------------------------------------------------------|
| `deploy`   | Full cycle: build → push → apply K8s manifests → migrate DB    |
| `build`    | Build & push container images only                              |
| `up`       | Apply manifests only (skip image build; use existing images)    |
| `validate` | Post-deployment health check (pod status, API ping)             |
| `teardown` | **Destructive**: delete all platform resources                  |
| `airgap`   | Package all images + tar for fully offline install              |

## File Structure

```
deploy/
├── master.py           ← Entry point — rich TUI menu
├── requirements.txt    ← Python dependencies
├── README.md           ← This file
└── providers/
    ├── __init__.py
    ├── local.py        ← docker-compose / podman-compose
    ├── azure.py        ← Azure AKS
    ├── aws.py          ← AWS EKS
    ├── gcp.py          ← GCP GKE
    ├── onprem.py       ← Bare-metal / OpenShift
    └── _airgap.py      ← Shared air-gap packaging utility
```

## Prerequisites

Each provider has its own CLI prerequisites:

| Provider | Required CLIs                  | Auth command              |
|----------|--------------------------------|---------------------------|
| local    | docker / podman                | —                         |
| azure    | `az`, `kubectl`                | `az login`                |
| aws      | `aws`, `kubectl`, `eksctl`     | `aws configure`           |
| gcp      | `gcloud`, `kubectl`            | `gcloud auth login`       |
| onprem   | `kubectl`                      | Ensure `~/.kube/config`   |

## Environment Variables

The deploy scripts respect the following environment variables (can also be set in a `.env` file in the repo root):

```bash
HF_TOKEN=hf_...           # HuggingFace token for gated models (optional)
ENGINE_HOST=10.0.0.5      # GPU VM private IP (hybrid Option A)
```

## The K8s Manifests

Manifests live at `platform/k8s/`. The deploy scripts patch `<YOUR_REGISTRY>` with your actual registry URL at apply time. See [../docs/DEPLOYMENT.md](../docs/DEPLOYMENT.md) for the full manifest reference and cloud-specific storage class configuration.
