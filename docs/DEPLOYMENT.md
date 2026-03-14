# LLMOps Platform — Production Deployment Guide

> **Audience:** Infrastructure engineers deploying the platform to a customer environment (on-prem or cloud).  
> **Current build:** Phases 0–4 complete. Platform services: 8 containers. Inference engines: native GPU processes.

---

## Table of Contents

1. [Architecture Overview](#1-architecture-overview)
2. [Deployment Options](#2-deployment-options)
3. [Prerequisites](#3-prerequisites)
4. [Kubernetes Manifests Reference](#4-kubernetes-manifests-reference)
5. [Cloud Deployments](#5-cloud-deployments)
   - [Azure AKS](#51-azure-aks)
   - [AWS EKS](#52-aws-eks)
   - [GCP GKE](#53-gcp-gke)
6. [On-Premises Kubernetes (bare-metal / OpenShift)](#6-on-premises-kubernetes)
7. [Helm Chart Structure](#7-helm-chart-structure)
8. [Automated Deployment Script](#8-automated-deployment-script)
9. [Post-Deployment Validation](#9-post-deployment-validation)
10. [Air-Gap Deployment](#10-air-gap-deployment)
11. [Configuration Reference](#11-configuration-reference)
12. [Operational Runbook](#12-operational-runbook)

---

## 1. Architecture Overview

The platform has two distinct tiers that have different infrastructure requirements:

| Tier | Services | Compute | Storage |
|---|---|---|---|
| **Platform** | API, UI, PostgreSQL, LiteLLM, OpenWebUI, MLflow, Prometheus, Grafana | CPU (2–4 vCPU each) | SSD PVCs |
| **Inference** | vLLM engine(s) | GPU (NVIDIA A100/H100 or AMD MI300) | Model weights volume |

```
┌─────────────────── Kubernetes Cluster ────────────────────────────────────────┐
│                                                                               │
│  ┌─── Namespace: llmops-platform ───────────────────────────────────────────┐ │
│  │                                                                          │ │
│  │  [UI / nginx]  ──►  [API / FastAPI]  ──►  [PostgreSQL]                   │ │
│  │       │                    │                                             │ │
│  │       │              [LiteLLM Proxy]  ──►  [OpenWebUI]                   │ │
│  │       │                    │                                             │ │
│  │       │              [MLflow]  [Prometheus]  [Grafana]                   │ │
│  │       │                                                                  │ │
│  └───────┼──────────────────────────────────────────────────────────────────┘ │
│          │ /v1/chat/completions                                               │
│  ┌───────▼──── Namespace: llmops-engines (GPU node pool) ──────────────────┐  │
│  │                                                                         │  │
│  │  [vLLM: model-a :9000]  [vLLM: model-b :9001]  ...                      │  │
│  │   (each engine = 1 Pod, GPU resource request, exposed via Service)      │  │
│  │                                                                         │  │
│  └─────────────────────────────────────────────────────────────────────────┘  │
└───────────────────────────────────────────────────────────────────────────────┘
         │ public / internal ingress
  ┌──────▼──────┐
  │  Ingress /  │  (nginx-ingress or cloud LB)
  │  TLS term.  │
  └─────────────┘
```

### Service Port Map (internal cluster)

| Service | K8s Service Port | Protocol | Notes |
|---|---|---|---|
| `llmops-api` | 8001 | HTTP | FastAPI; healthcheck `/health` |
| `llmops-ui` | 3000 | HTTP | nginx SPA; proxies `/v1/` to API |
| `llmops-db` | 5432 | TCP | PostgreSQL 16 |
| `llmops-litellm` | 4000 | HTTP | OpenAI-compatible proxy |
| `llmops-openwebui` | 8080 | HTTP | Chat UI |
| `llmops-mlflow` | 5001 | HTTP | Experiment tracking |
| `llmops-prometheus` | 9090 | HTTP | Metrics scraper |
| `llmops-grafana` | 3000 | HTTP | Dashboards |
| `vllm-{alias}` | 8000 | HTTP | Per-engine; OpenAI-compatible |

---

## 2. Deployment Options

### Option A — Hybrid (Recommended for immediate production)

Platform services run in Kubernetes. vLLM inference engines run on a dedicated **GPU VM** (same host or separate server reachable over the internal network). The API manages engine processes via the `host-launcher` pattern.

**Why hybrid?**
- No code changes required — ships today
- Full GPU Metal/CUDA access without K8s GPU operator complexity
- Simpler for air-gapped environments where GPU driver management is mature
- Customer GPU VMs (e.g. Azure NCv3, AWS p3, bare-metal GPU server) slot directly in

```
  K8s Cluster (CPU)                 GPU VM
  ┌────────────────────┐            ┌───────────────────────────┐
  │  API, UI, DB, etc. │◄──────────►│  host-launcher :9001      │
  │                    │  HTTP/TCP  │  vLLM engines :9000-9003  │
  └────────────────────┘            │  HF model cache           │
                                    └───────────────────────────┘
```

**Requirements:** GPU VM must be reachable from the API pod (internal network / VPN). Set `ENGINE_HOST` env var to the GPU VM's internal IP or hostname.

### Option B — Full Kubernetes (Phase 5 target)

vLLM engines run as Kubernetes Deployments on a GPU node pool. The platform API creates/deletes K8s Deployments via the K8s API when engines are started/stopped. Requires Phase 5.5 engine controller work.

> This document covers **Option A** as the production-ready path, with notes on Option B where relevant.

---

## 3. Prerequisites

### All environments

| Tool | Version | Install |
|---|---|---|
| `kubectl` | ≥ 1.28 | https://kubernetes.io/docs/tasks/tools/ |
| `helm` | ≥ 3.14 | https://helm.sh/docs/intro/install/ |
| `podman` or `docker` | any | For building images |
| `openssl` | any | For generating secrets |

### Cloud CLIs (install the one for your cloud)

```bash
# Azure
az aks install-cli

# AWS
aws eks update-kubeconfig --region <region> --name <cluster>

# GCP
gcloud container clusters get-credentials <cluster> --region <region>
```

### Container Registry

The platform requires a private container registry reachable from the cluster. Supported registries:

| Cloud | Registry |
|---|---|
| Azure | Azure Container Registry (ACR) |
| AWS | Elastic Container Registry (ECR) |
| GCP | Artifact Registry |
| On-prem | Harbor, registry:2, Quay |

### Hardware requirements

**Platform services (K8s nodes):**

| Node pool | vCPU | Memory | Storage | Count |
|---|---|---|---|---|
| System | 4 | 8 GB | 50 GB SSD | 2 (HA) |
| Platform workloads | 4 | 16 GB | 100 GB SSD | 2–3 |

**Inference (GPU VM or GPU node pool):**

| Model size | GPU recommended | VRAM | System RAM |
|---|---|---|---|
| ≤ 7B params | NVIDIA A10 / AMD MI250 | 24 GB | 32 GB |
| 8–13B params | NVIDIA A100 40 GB | 40 GB | 64 GB |
| 30–70B params | NVIDIA A100 80 GB | 80 GB | 128 GB |
| 70B+ (fp16) | 2× A100 80 GB | 160 GB | 256 GB |

---

## 4. Kubernetes Manifests Reference

All manifests live in `platform/k8s/`. The structure:

```
platform/k8s/
├── namespace.yaml
├── secrets/
│   └── secrets.yaml           (generated — never committed)
├── platform/
│   ├── postgres.yaml
│   ├── api.yaml
│   ├── ui.yaml
│   ├── litellm.yaml
│   ├── openwebui.yaml
│   ├── mlflow.yaml
│   ├── prometheus.yaml
│   └── grafana.yaml
├── ingress/
│   └── ingress.yaml
└── engines/
    └── engine-template.yaml   (Option B only)
```

### 4.1 Namespace

```yaml
# k8s/namespace.yaml
apiVersion: v1
kind: Namespace
metadata:
  name: llmops
  labels:
    app.kubernetes.io/part-of: llmops-platform
```

### 4.2 Secrets (generate, never commit)

```bash
# Generate all secrets — run once per deployment
kubectl create secret generic llmops-secrets -n llmops \
  --from-literal=postgres-password="$(openssl rand -base64 24)" \
  --from-literal=jwt-secret="$(openssl rand -base64 48)" \
  --from-literal=litellm-master-key="sk-$(openssl rand -hex 24)" \
  --from-literal=hf-token="${HF_TOKEN:-}" \
  --dry-run=client -o yaml > platform/k8s/secrets/secrets.yaml

# Apply
kubectl apply -f platform/k8s/secrets/secrets.yaml
```

### 4.3 PostgreSQL StatefulSet

```yaml
# k8s/platform/postgres.yaml
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: postgres-data
  namespace: llmops
spec:
  accessModes: [ReadWriteOnce]
  storageClassName: managed-premium   # use 'gp3' on AWS, 'standard-rwo' on GCP
  resources:
    requests:
      storage: 20Gi
---
apiVersion: apps/v1
kind: StatefulSet
metadata:
  name: llmops-db
  namespace: llmops
spec:
  replicas: 1
  selector:
    matchLabels:
      app: llmops-db
  template:
    metadata:
      labels:
        app: llmops-db
    spec:
      containers:
      - name: postgres
        image: postgres:16-alpine
        env:
        - name: POSTGRES_DB
          value: llmops
        - name: POSTGRES_USER
          value: llmops
        - name: POSTGRES_PASSWORD
          valueFrom:
            secretKeyRef:
              name: llmops-secrets
              key: postgres-password
        - name: PGDATA
          value: /var/lib/postgresql/data/pgdata
        ports:
        - containerPort: 5432
        volumeMounts:
        - name: postgres-data
          mountPath: /var/lib/postgresql/data
        readinessProbe:
          exec:
            command: [pg_isready, -U, llmops]
          initialDelaySeconds: 5
          periodSeconds: 5
        resources:
          requests:
            cpu: 250m
            memory: 512Mi
          limits:
            cpu: 1
            memory: 2Gi
      volumes:
      - name: postgres-data
        persistentVolumeClaim:
          claimName: postgres-data
---
apiVersion: v1
kind: Service
metadata:
  name: llmops-db
  namespace: llmops
spec:
  selector:
    app: llmops-db
  ports:
  - port: 5432
    targetPort: 5432
  clusterIP: None   # headless for StatefulSet
```

### 4.4 API Deployment

```yaml
# k8s/platform/api.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: llmops-api
  namespace: llmops
spec:
  replicas: 2
  selector:
    matchLabels:
      app: llmops-api
  template:
    metadata:
      labels:
        app: llmops-api
    spec:
      containers:
      - name: api
        image: <YOUR_REGISTRY>/llmops-platform_api:latest
        ports:
        - containerPort: 8001
        env:
        - name: DATABASE_URL
          value: "postgresql+asyncpg://llmops:$(POSTGRES_PASSWORD)@llmops-db:5432/llmops"
        - name: POSTGRES_PASSWORD
          valueFrom:
            secretKeyRef:
              name: llmops-secrets
              key: postgres-password
        - name: SECRET_KEY
          valueFrom:
            secretKeyRef:
              name: llmops-secrets
              key: jwt-secret
        - name: ENGINE_HOST
          value: "<GPU_VM_INTERNAL_IP>"   # Option A hybrid
        - name: HF_TOKEN
          valueFrom:
            secretKeyRef:
              name: llmops-secrets
              key: hf-token
        - name: LITELLM_MASTER_KEY
          valueFrom:
            secretKeyRef:
              name: llmops-secrets
              key: litellm-master-key
        readinessProbe:
          httpGet:
            path: /health
            port: 8001
          initialDelaySeconds: 10
          periodSeconds: 10
        resources:
          requests:
            cpu: 500m
            memory: 512Mi
          limits:
            cpu: 2
            memory: 2Gi
        volumeMounts:
        - name: vllm-logs
          mountPath: /tmp/vllm_logs
        - name: hf-cache
          mountPath: /root/.cache/huggingface
          readOnly: true
      volumes:
      - name: vllm-logs
        persistentVolumeClaim:
          claimName: vllm-logs-pvc
      - name: hf-cache
        persistentVolumeClaim:
          claimName: hf-cache-pvc   # shared RWX PVC backed by NFS/Azure Files/EFS
---
apiVersion: v1
kind: Service
metadata:
  name: llmops-api
  namespace: llmops
spec:
  selector:
    app: llmops-api
  ports:
  - port: 8001
    targetPort: 8001
```

### 4.5 UI Deployment

```yaml
# k8s/platform/ui.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: llmops-ui
  namespace: llmops
spec:
  replicas: 2
  selector:
    matchLabels:
      app: llmops-ui
  template:
    metadata:
      labels:
        app: llmops-ui
    spec:
      containers:
      - name: ui
        image: <YOUR_REGISTRY>/llmops-platform_ui:latest
        ports:
        - containerPort: 3000
        readinessProbe:
          httpGet:
            path: /
            port: 3000
          initialDelaySeconds: 5
          periodSeconds: 10
        resources:
          requests:
            cpu: 100m
            memory: 128Mi
          limits:
            cpu: 500m
            memory: 256Mi
---
apiVersion: v1
kind: Service
metadata:
  name: llmops-ui
  namespace: llmops
spec:
  selector:
    app: llmops-ui
  ports:
  - port: 3000
    targetPort: 3000
```

### 4.6 Ingress

```yaml
# k8s/ingress/ingress.yaml
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: llmops-ingress
  namespace: llmops
  annotations:
    nginx.ingress.kubernetes.io/proxy-read-timeout: "3600"
    nginx.ingress.kubernetes.io/proxy-send-timeout: "3600"
    nginx.ingress.kubernetes.io/proxy-buffering: "off"       # SSE streams
    cert-manager.io/cluster-issuer: "letsencrypt-prod"       # or your CA
spec:
  ingressClassName: nginx
  tls:
  - hosts:
    - llmops.your-domain.com
    secretName: llmops-tls
  rules:
  - host: llmops.your-domain.com
    http:
      paths:
      - path: /
        pathType: Prefix
        backend:
          service:
            name: llmops-ui
            port:
              number: 3000
```

> For internal/on-prem with no public DNS, use a `LoadBalancer` service instead of Ingress and configure your corporate DNS.

---

## 5. Cloud Deployments

### 5.1 Azure AKS

#### Step 1 — Resource group and AKS cluster

```bash
LOCATION="eastus"
RG="rg-llmops-prod"
CLUSTER="aks-llmops-prod"
ACR="acrllmopsprod"

# Resource group
az group create --name $RG --location $LOCATION

# Container registry
az acr create --resource-group $RG --name $ACR --sku Standard --location $LOCATION

# AKS cluster (system + user node pools)
az aks create \
  --resource-group $RG \
  --name $CLUSTER \
  --node-count 2 \
  --node-vm-size Standard_D4s_v3 \
  --enable-addons monitoring \
  --generate-ssh-keys \
  --attach-acr $ACR

# GPU node pool (NC A100 v4 — skip if using hybrid Option A)
az aks nodepool add \
  --resource-group $RG \
  --cluster-name $CLUSTER \
  --name gpunodes \
  --node-count 1 \
  --node-vm-size Standard_NC24ads_A100_v4 \
  --node-taints sku=gpu:NoSchedule \
  --labels skutype=gpu

# Get credentials
az aks get-credentials --resource-group $RG --name $CLUSTER
```

#### Step 2 — Azure Files for shared volumes (HF cache + vLLM logs)

```bash
# Storage account for shared volumes
az storage account create \
  --name "stllmopsprod" \
  --resource-group $RG \
  --sku Standard_LRS \
  --kind StorageV2

# Create file shares
az storage share create --name hf-cache --account-name stllmopsprod
az storage share create --name vllm-logs --account-name stllmopsprod

# Create K8s secret for Azure Files
STORAGE_KEY=$(az storage account keys list \
  --resource-group $RG \
  --account-name stllmopsprod \
  --query '[0].value' -o tsv)

kubectl create secret generic azure-storage-secret -n llmops \
  --from-literal=azurestorageaccountname=stllmopsprod \
  --from-literal=azurestorageaccountkey="$STORAGE_KEY"
```

Persistent volume claims referencing Azure Files:

```yaml
# Azure-specific StorageClass (add to your manifests)
apiVersion: storage.k8s.io/v1
kind: StorageClass
metadata:
  name: azure-file-rwx
provisioner: file.csi.azure.com
parameters:
  skuName: Standard_LRS
  storageAccount: stllmopsprod
reclaimPolicy: Retain
volumeBindingMode: Immediate
mountOptions:
- dir_mode=0777
- file_mode=0777
---
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: hf-cache-pvc
  namespace: llmops
spec:
  accessModes: [ReadWriteMany]
  storageClassName: azure-file-rwx
  resources:
    requests:
      storage: 200Gi   # adjust based on model count/size
```

#### Step 3 — Build and push images

```bash
# Authenticate ACR
az acr login --name $ACR

# Build and push
REGISTRY="${ACR}.azurecr.io"

cd platform
podman build -t ${REGISTRY}/llmops-platform_api:latest ./api
podman build -t ${REGISTRY}/llmops-platform_ui:latest ./ui
podman push ${REGISTRY}/llmops-platform_api:latest
podman push ${REGISTRY}/llmops-platform_ui:latest
```

#### Step 4 — Deploy

```bash
# Update image references in manifests
sed -i "s|<YOUR_REGISTRY>|${REGISTRY}|g" platform/k8s/platform/*.yaml

kubectl apply -f platform/k8s/namespace.yaml
kubectl apply -f platform/k8s/secrets/secrets.yaml
kubectl apply -f platform/k8s/platform/
kubectl apply -f platform/k8s/ingress/ingress.yaml
```

#### Option A — GPU VM for vLLM (Azure)

```bash
# Dedicated GPU VM in same VNet as AKS
az vm create \
  --resource-group $RG \
  --name vm-llmops-gpu \
  --image Ubuntu2204 \
  --size Standard_NC24ads_A100_v4 \
  --vnet-name <AKS-VNET> \
  --subnet <AKS-SUBNET> \
  --public-ip-sku Standard \
  --admin-username azureuser \
  --ssh-key-values ~/.ssh/id_rsa.pub

# Install NVIDIA drivers + vLLM on the GPU VM
az vm run-command invoke \
  --resource-group $RG \
  --name vm-llmops-gpu \
  --command-id RunShellScript \
  --scripts "
    apt-get update && apt-get install -y nvidia-cuda-toolkit python3-pip
    pip3 install vllm
    # Start the host-launcher service
    curl -sSL https://raw.githubusercontent.com/your-org/llmops/main/platform/host-launcher/install.sh | bash
  "

# Get the GPU VM private IP and set ENGINE_HOST in the API deployment
GPU_VM_IP=$(az vm show -g $RG -n vm-llmops-gpu \
  --query privateIps -d --out tsv)
kubectl set env deployment/llmops-api -n llmops ENGINE_HOST=$GPU_VM_IP
```

---

### 5.2 AWS EKS

#### Step 1 — EKS cluster

```bash
REGION="us-east-1"
CLUSTER="eks-llmops-prod"
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)

# EKS cluster via eksctl (simplest approach)
cat > /tmp/eks-cluster.yaml << 'EOF'
apiVersion: eksctl.io/v1alpha5
kind: ClusterConfig
metadata:
  name: eks-llmops-prod
  region: us-east-1
  version: "1.29"

managedNodeGroups:
  - name: platform-nodes
    instanceType: m5.xlarge
    desiredCapacity: 3
    minSize: 2
    maxSize: 5
    iam:
      attachPolicyARNs:
        - arn:aws:iam::aws:policy/AmazonEKSWorkerNodePolicy
        - arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryReadOnly
        - arn:aws:iam::aws:policy/AmazonEFS_CSI_DriverPolicy

  # Option B only — remove if using hybrid VM approach
  - name: gpu-nodes
    instanceType: p3.2xlarge   # or p4d.24xlarge for A100
    desiredCapacity: 1
    minSize: 0
    maxSize: 4
    taints:
      - key: sku
        value: gpu
        effect: NoSchedule
    labels:
      skutype: gpu
EOF

eksctl create cluster -f /tmp/eks-cluster.yaml

# ECR repository
aws ecr create-repository --repository-name llmops-platform_api --region $REGION
aws ecr create-repository --repository-name llmops-platform_ui --region $REGION
```

#### Step 2 — EFS for shared volumes (HF cache)

```bash
# EFS filesystem
EFS_ID=$(aws efs create-file-system \
  --region $REGION \
  --performance-mode generalPurpose \
  --query 'FileSystemId' --output text)

# Mount targets in each AZ subnet (get subnet IDs from eks-cluster)
EKS_VPC=$(aws eks describe-cluster --name $CLUSTER --query cluster.resourcesVpcConfig.vpcId --output text)
for SUBNET in $(aws ec2 describe-subnets --filters Name=vpc-id,Values=$EKS_VPC \
    --query 'Subnets[?Tags[?Key==`kubernetes.io/role/internal-elb`]].SubnetId' --output text); do
  aws efs create-mount-target --file-system-id $EFS_ID --subnet-id $SUBNET
done

# Install EFS CSI driver
kubectl apply -k "github.com/kubernetes-sigs/aws-efs-csi-driver/deploy/kubernetes/overlays/stable/?ref=release-1.7"

# StorageClass (add to manifests)
cat <<EOF | kubectl apply -f -
apiVersion: storage.k8s.io/v1
kind: StorageClass
metadata:
  name: efs-rwx
provisioner: efs.csi.aws.com
parameters:
  provisioningMode: efs-ap
  fileSystemId: ${EFS_ID}
  directoryPerms: "700"
EOF
```

#### Step 3 — Build, push, deploy

```bash
REGISTRY="${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com"
aws ecr get-login-password --region $REGION | \
  docker login --username AWS --password-stdin $REGISTRY

cd platform
docker build -t ${REGISTRY}/llmops-platform_api:latest ./api
docker build -t ${REGISTRY}/llmops-platform_ui:latest ./ui
docker push ${REGISTRY}/llmops-platform_api:latest
docker push ${REGISTRY}/llmops-platform_ui:latest

sed -i "s|<YOUR_REGISTRY>|${REGISTRY}|g" platform/k8s/platform/*.yaml
kubectl apply -f platform/k8s/namespace.yaml
kubectl apply -f platform/k8s/secrets/secrets.yaml
kubectl apply -f platform/k8s/platform/
kubectl apply -f platform/k8s/ingress/ingress.yaml
```

---

### 5.3 GCP GKE

#### Step 1 — GKE cluster

```bash
PROJECT="my-llmops-project"
REGION="us-central1"
CLUSTER="gke-llmops-prod"
REGISTRY="us-central1-docker.pkg.dev/${PROJECT}/llmops"

gcloud config set project $PROJECT

gcloud container clusters create $CLUSTER \
  --region $REGION \
  --num-nodes 2 \
  --machine-type n2-standard-4 \
  --disk-size 100GB \
  --enable-autoscaling --min-nodes 2 --max-nodes 5 \
  --workload-pool=${PROJECT}.svc.id.goog

# GPU node pool (optional — skip for hybrid Option A)
gcloud container node-pools create gpu-pool \
  --cluster $CLUSTER \
  --region $REGION \
  --machine-type a2-highgpu-1g \
  --accelerator type=nvidia-tesla-a100,count=1 \
  --num-nodes 1 \
  --node-taints=sku=gpu:NoSchedule \
  --node-labels=skutype=gpu

gcloud container clusters get-credentials $CLUSTER --region $REGION
```

#### Step 2 — Artifact Registry + Filestore

```bash
# Container registry
gcloud artifacts repositories create llmops \
  --repository-format docker \
  --location us-central1

gcloud auth configure-docker us-central1-docker.pkg.dev

# Filestore for HF cache (NFS-backed RWX PVC)
gcloud filestore instances create llmops-nfs \
  --project $PROJECT \
  --location us-central1-a \
  --tier BASIC_HDD \
  --file-share name=hfcache,capacity=1TB \
  --network name=default
```

#### Step 3 — Build, push, deploy

```bash
cd platform
docker build -t ${REGISTRY}/llmops-platform_api:latest ./api
docker build -t ${REGISTRY}/llmops-platform_ui:latest ./ui
docker push ${REGISTRY}/llmops-platform_api:latest
docker push ${REGISTRY}/llmops-platform_ui:latest

sed -i "s|<YOUR_REGISTRY>|${REGISTRY}|g" platform/k8s/platform/*.yaml
kubectl apply -f platform/k8s/namespace.yaml
kubectl apply -f platform/k8s/secrets/secrets.yaml
kubectl apply -f platform/k8s/platform/
kubectl apply -f platform/k8s/ingress/ingress.yaml
```

---

## 6. On-Premises Kubernetes

### Option A: Bare-metal K8s (kubeadm)

```bash
# All nodes: install kubeadm, kubelet, kubectl
curl -fsSL https://pkgs.k8s.io/core:/stable:/v1.29/deb/Release.key \
  | gpg --dearmor -o /etc/apt/keyrings/kubernetes-apt-keyring.gpg
echo 'deb [signed-by=/etc/apt/keyrings/kubernetes-apt-keyring.gpg] https://pkgs.k8s.io/core:/stable:/v1.29/deb/ /' \
  | tee /etc/apt/sources.list.d/kubernetes.list
apt-get update && apt-get install -y kubelet kubeadm kubectl
apt-mark hold kubelet kubeadm kubectl

# Control plane (run once on master node)
kubeadm init --pod-network-cidr=10.244.0.0/16

# Copy kubeconfig
mkdir -p $HOME/.kube
cp /etc/kubernetes/admin.conf $HOME/.kube/config

# CNI (Flannel — or Calico for network policy)
kubectl apply -f https://raw.githubusercontent.com/flannel-io/flannel/master/Documentation/kube-flannel.yml

# Join worker nodes (use the token from kubeadm init output)
# kubeadm join <master-ip>:6443 --token <token> --discovery-token-ca-cert-hash sha256:<hash>
```

#### Storage (local-path or NFS)

```bash
# Local-path provisioner — simplest for single-node dev clusters
kubectl apply -f https://raw.githubusercontent.com/rancher/local-path-provisioner/v0.0.26/deploy/local-path-storage.yaml
kubectl patch storageclass local-path -p '{"metadata": {"annotations":{"storageclass.kubernetes.io/is-default-class":"true"}}}'

# NFS provisioner — for multi-node RWX volumes (HF cache, vLLM logs)
helm repo add nfs-subdir-external-provisioner https://kubernetes-sigs.github.io/nfs-subdir-external-provisioner/
helm install nfs-provisioner nfs-subdir-external-provisioner/nfs-subdir-external-provisioner \
  --set nfs.server=<NFS_SERVER_IP> \
  --set nfs.path=/exports/llmops \
  --set storageClass.name=nfs-rwx \
  --set storageClass.accessModes=ReadWriteMany
```

### Option B: Red Hat OpenShift

```bash
# OpenShift uses oc CLI; the manifests are compatible with minor adjustments
# 1. Remove runAsUser: 0 from containers (OpenShift enforces SCCs)
# 2. Add SCC anyuid if needed for PostgreSQL/nginx
oc adm policy add-scc-to-serviceaccount anyuid -z default -n llmops

# 3. Use OpenShift Routes instead of Ingress
oc expose svc llmops-ui -n llmops --hostname=llmops.apps.your-cluster.com
```

---

## 7. Helm Chart Structure

For repeatable, configurable deployments across environments, use the Helm chart in `platform/helm/`:

```
platform/helm/llmops-platform/
├── Chart.yaml
├── values.yaml              ← defaults (override per environment)
├── values.prod.yaml         ← production overrides
├── values.staging.yaml      ← staging overrides
└── templates/
    ├── _helpers.tpl
    ├── namespace.yaml
    ├── secrets.yaml
    ├── postgres/
    │   ├── statefulset.yaml
    │   ├── service.yaml
    │   └── pvc.yaml
    ├── api/
    │   ├── deployment.yaml
    │   ├── service.yaml
    │   └── configmap.yaml
    ├── ui/
    │   ├── deployment.yaml
    │   └── service.yaml
    ├── litellm/
    ├── openwebui/
    ├── mlflow/
    ├── prometheus/
    ├── grafana/
    └── ingress.yaml
```

**Key `values.yaml` sections:**

```yaml
# platform/helm/llmops-platform/values.yaml
global:
  registry: "your-registry.azurecr.io"
  namespace: llmops
  storageClass: managed-premium   # change per cloud

api:
  replicas: 2
  image: llmops-platform_api
  tag: latest
  engineHost: "gpu-vm.internal"   # Option A: GPU VM IP / hostname
  resources:
    requests: { cpu: 500m, memory: 512Mi }
    limits: { cpu: 2, memory: 2Gi }

ui:
  replicas: 2
  image: llmops-platform_ui
  tag: latest

postgres:
  storage: 20Gi
  resources:
    requests: { cpu: 250m, memory: 512Mi }

hfCache:
  storage: 200Gi
  storageClass: efs-rwx   # override with cloud-specific RWX class

ingress:
  enabled: true
  host: llmops.your-domain.com
  tlsSecret: llmops-tls
```

**Deploy with Helm:**

```bash
# Install / upgrade
helm upgrade --install llmops-platform platform/helm/llmops-platform \
  -f platform/helm/llmops-platform/values.prod.yaml \
  --namespace llmops \
  --create-namespace \
  --set global.registry=${REGISTRY} \
  --set api.engineHost=${GPU_VM_IP}

# Rollback
helm rollback llmops-platform 1 -n llmops

# Uninstall
helm uninstall llmops-platform -n llmops
```

---

## 8. Automated Deployment Script

`platform/scripts/deploy.sh` — a single script that handles the full deployment for any target environment:

```bash
#!/usr/bin/env bash
# platform/scripts/deploy.sh
# Usage: ./deploy.sh [azure|aws|gcp|onprem] [cluster-name] [registry-url]
set -euo pipefail

TARGET="${1:-azure}"
CLUSTER="${2:-llmops-prod}"
REGISTRY="${3:-}"
NAMESPACE="llmops"

log() { echo "[deploy] $*"; }

# ── 1. Build images ─────────────────────────────────────────────────────────
build_images() {
  log "Building UI (npm run build + Docker)..."
  cd platform/ui && npm ci && npm run build && cd -
  docker build -t "${REGISTRY}/llmops-platform_api:latest" platform/api
  docker build -t "${REGISTRY}/llmops-platform_ui:latest" platform/ui
  docker push "${REGISTRY}/llmops-platform_api:latest"
  docker push "${REGISTRY}/llmops-platform_ui:latest"
  log "Images pushed to ${REGISTRY}"
}

# ── 2. Generate secrets (idempotent) ────────────────────────────────────────
ensure_secrets() {
  if kubectl get secret llmops-secrets -n ${NAMESPACE} &>/dev/null; then
    log "Secrets already exist — skipping"
    return
  fi
  log "Generating secrets..."
  kubectl create secret generic llmops-secrets -n ${NAMESPACE} \
    --from-literal=postgres-password="$(openssl rand -base64 24)" \
    --from-literal=jwt-secret="$(openssl rand -base64 48)" \
    --from-literal=litellm-master-key="sk-$(openssl rand -hex 24)" \
    --from-literal=hf-token="${HF_TOKEN:-}"
}

# ── 3. Apply manifests ───────────────────────────────────────────────────────
deploy_platform() {
  log "Applying Kubernetes manifests..."
  sed "s|<YOUR_REGISTRY>|${REGISTRY}|g" platform/k8s/platform/*.yaml | kubectl apply -f -
  kubectl apply -f platform/k8s/ingress/ingress.yaml
}

# ── 4. Wait for rollout ──────────────────────────────────────────────────────
wait_for_rollout() {
  log "Waiting for rollouts..."
  for deploy in llmops-api llmops-ui; do
    kubectl rollout status deployment/${deploy} -n ${NAMESPACE} --timeout=5m
  done
  kubectl rollout status statefulset/llmops-db -n ${NAMESPACE} --timeout=5m
  log "All deployments ready"
}

# ── 5. Run migrations ────────────────────────────────────────────────────────
run_migrations() {
  log "Running Alembic migrations..."
  kubectl exec -n ${NAMESPACE} \
    $(kubectl get pod -n ${NAMESPACE} -l app=llmops-api -o jsonpath='{.items[0].metadata.name}') \
    -- alembic upgrade head
  log "Migrations complete"
}

# ── Main ─────────────────────────────────────────────────────────────────────
kubectl create namespace ${NAMESPACE} --dry-run=client -o yaml | kubectl apply -f -
build_images
ensure_secrets
deploy_platform
wait_for_rollout
run_migrations

log "✅ Deployment complete. UI: https://$(kubectl get ingress llmops-ingress -n ${NAMESPACE} -o jsonpath='{.spec.rules[0].host}')"
```

Make executable:
```bash
chmod +x platform/scripts/deploy.sh
./platform/scripts/deploy.sh azure aks-llmops-prod your-acr.azurecr.io
```

---

## 9. Post-Deployment Validation

Run this checklist after every deployment:

```bash
#!/usr/bin/env bash
# platform/scripts/validate.sh
NAMESPACE="llmops"
API_URL="https://llmops.your-domain.com"

echo "=== Pod status ==="
kubectl get pods -n $NAMESPACE

echo ""
echo "=== API health ==="
curl -sf "${API_URL}/health" | python3 -m json.tool

echo ""
echo "=== Login ==="
TOKEN=$(curl -sf -X POST "${API_URL}/v1/auth/token" \
  -H "Content-Type: application/json" \
  -d '{"email":"admin@llmops.local","password":"changeme"}' \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")
[ -n "$TOKEN" ] && echo "✅ Auth OK" || echo "❌ Auth FAILED"

echo ""
echo "=== Model registry ==="
curl -sf "${API_URL}/v1/models" \
  -H "Authorization: Bearer ${TOKEN}" | python3 -m json.tool

echo ""
echo "=== Engine list ==="
curl -sf "${API_URL}/v1/engines" \
  -H "Authorization: Bearer ${TOKEN}" | python3 -m json.tool

echo "=== Validation complete ==="
```

---

## 10. Air-Gap Deployment

For environments with no internet access:

### Step 1 — Pre-pull all images (on an internet-connected machine)

```bash
#!/usr/bin/env bash
# platform/scripts/airgap-save.sh
IMAGES=(
  "postgres:16-alpine"
  "nginx:1.27-alpine"
  "node:22-alpine"
  "ghcr.io/berriai/litellm:main-latest"
  "ghcr.io/open-webui/open-webui:main"
  "ghcr.io/mlflow/mlflow:v2.14.3"
  "prom/prometheus:v2.53.0"
  "grafana/grafana:11.1.0"
)

for img in "${IMAGES[@]}"; do
  echo "Pulling $img..."
  docker pull "$img"
done

# Build platform images
cd platform
docker build -t llmops-platform_api:latest ./api
docker build -t llmops-platform_ui:latest ./ui
IMAGES+=("llmops-platform_api:latest" "llmops-platform_ui:latest")

# Save all to tarball
echo "Saving tarball..."
docker save "${IMAGES[@]}" | gzip > llmops-images-$(date +%Y%m%d).tar.gz
echo "Done: llmops-images-$(date +%Y%m%d).tar.gz"
```

### Step 2 — Transfer and load (on the air-gapped node / registry)

```bash
# Transfer the tarball to the air-gapped environment
scp llmops-images-*.tar.gz airgap-host:/tmp/

# On the air-gapped host: load into local registry
ssh airgap-host "
  docker load < /tmp/llmops-images-*.tar.gz
  # Or push to internal registry:
  # for img in \$(docker load < /tmp/llmops-images-*.tar.gz | grep 'Loaded image:' | awk '{print \$3}'); do
  #   docker tag \$img registry.internal.corp/\$img
  #   docker push registry.internal.corp/\$img
  # done
"
```

### Step 3 — HF model cache

Pre-download all model weights on an internet-connected machine that has `huggingface-cli`:

```bash
# Download models for air-gap deployment
huggingface-cli download ibm-granite/granite-3.1-8b-instruct
huggingface-cli download Qwen/Qwen2.5-0.5B-Instruct

# Tar the cache
tar -czf hf-cache-$(date +%Y%m%d).tar.gz ~/.cache/huggingface/hub/

# On target: extract to the shared volume mount point
scp hf-cache-*.tar.gz airgap-host:/mnt/hf-cache/
ssh airgap-host "tar -xzf /mnt/hf-cache/hf-cache-*.tar.gz -C /mnt/hf-cache/"
```

### Step 4 — Deploy with HUB_OFFLINE flag

```bash
# Ensure API container has HF offline mode
kubectl set env deployment/llmops-api -n llmops \
  HF_HUB_OFFLINE=1 \
  HF_HUB_DISABLE_PROGRESS_BARS=1
```

---

## 11. Configuration Reference

All configurable values for the API container (set as environment variables or K8s Secrets):

| Variable | Required | Default | Description |
|---|---|---|---|
| `DATABASE_URL` | ✅ | — | PostgreSQL async URL |
| `SECRET_KEY` | ✅ | — | JWT signing secret (min 32 chars) |
| `LITELLM_MASTER_KEY` | ✅ | — | LiteLLM authentication key |
| `LITELLM_BASE_URL` | | `http://litellm:4000` | LiteLLM proxy URL |
| `ENGINE_HOST` | | `host.containers.internal` | Hostname for vLLM engines (GPU VM) |
| `ENGINE_LOG_DIR` | | `/tmp/vllm_logs` | Path for engine log files |
| `HF_HOME` | | `/root/.cache/huggingface` | HuggingFace cache directory |
| `HF_TOKEN` | | — | HF token for gated models |
| `HF_HUB_OFFLINE` | | `0` | Set `1` for air-gap |
| `MLFLOW_TRACKING_URI` | | `http://mlflow:5001` | MLflow server |
| `WEBHOOK_URL` | | — | Optional: Slack/Teams webhook for model events |
| `COST_PER_TOKEN_USD` | | `0.000002` | Token cost estimate for reports |
| `LOG_LEVEL` | | `INFO` | Logging verbosity |

---

## 12. Operational Runbook

### Scale API horizontally

```bash
kubectl scale deployment/llmops-api -n llmops --replicas=4
```

### Roll out a new API version

```bash
docker build -t ${REGISTRY}/llmops-platform_api:v1.2.0 platform/api
docker push ${REGISTRY}/llmops-platform_api:v1.2.0
kubectl set image deployment/llmops-api api=${REGISTRY}/llmops-platform_api:v1.2.0 -n llmops
kubectl rollout status deployment/llmops-api -n llmops
```

### Roll back

```bash
kubectl rollout undo deployment/llmops-api -n llmops
```

### Database backup

```bash
kubectl exec -n llmops \
  $(kubectl get pod -n llmops -l app=llmops-db -o jsonpath='{.items[0].metadata.name}') \
  -- pg_dump -U llmops llmops | gzip > llmops-db-$(date +%Y%m%d).sql.gz
```

### Disaster recovery — restore database

```bash
gunzip -c llmops-db-20260301.sql.gz | kubectl exec -i -n llmops \
  $(kubectl get pod -n llmops -l app=llmops-db -o jsonpath='{.items[0].metadata.name}') \
  -- psql -U llmops llmops
```

### Check vLLM engine logs (hybrid mode)

```bash
# From the UI: Engines → Logs button
# Or directly via API:
curl -N -H "Authorization: Bearer ${TOKEN}" \
  https://llmops.your-domain.com/v1/engines/{engine_id}/logs
```

### GPU VM health check (Option A hybrid)

```bash
ssh azureuser@<GPU_VM_IP> "nvidia-smi && curl -s http://localhost:9001/alive"
```

---

## Phase 5 Roadmap (Engine evolution — Option B)

The current hybrid model is production-ready today. The Phase 5.5 target is to make vLLM engines fully Kubernetes-native:

```
Current (Option A):         Target (Option B - Phase 5.5):
  API ─► host-launcher        API ─► K8s API Server
         │                           │
         └─► vLLM processes          └─► vLLM Deployment (GPU pod)
             on GPU VM                   in k8s/engines/ namespace
```

This requires:
1. `engine_launcher.py` → calls `kubernetes` Python client to `create_namespaced_deployment`
2. `reconciler.py` → watches K8s Deployment status instead of PID/port
3. K8s RBAC: the API ServiceAccount needs `deployments` CRUD on the `llmops-engines` namespace
4. GPU node pool with NVIDIA device plugin + proper resource requests

See [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md) Phase 5 for the full plan.

---

*Document version: 2026-03-01 · Option A (Hybrid) production-ready · Option B (Full K8s) Phase 5.5 target*
