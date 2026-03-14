"""
GCP GKE deployment provider.
Requires: gcloud CLI authenticated + kubectl configured.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt


def _run(console: Console, cmd: list[str], cwd: Path | None = None) -> int:
    console.print(f"[dim]$ {' '.join(str(c) for c in cmd)}[/dim]")
    return subprocess.run(cmd, cwd=cwd).returncode


def _require_gcloud(console: Console) -> bool:
    rc = subprocess.run(["gcloud", "auth", "print-identity-token"],
                        capture_output=True).returncode
    if rc:
        console.print("[error]gcloud not authenticated. Run: gcloud auth login[/]")
        return False
    return True


def _get_config(console: Console) -> dict[str, str]:
    console.print()
    console.print(Panel(
        "Provide your GCP deployment configuration.",
        title="GCP Configuration", border_style="blue",
    ))
    project  = Prompt.ask("GCP project ID")
    region   = Prompt.ask("Region",               default="us-central1")
    cluster  = Prompt.ask("GKE cluster name",     default="gke-llmops-prod")
    ns       = Prompt.ask("Kubernetes namespace", default="llmops")
    gpu_vm   = Prompt.ask("GPU VM private IP (leave blank to skip)", default="")
    registry = f"{region}-docker.pkg.dev/{project}/llmops"
    return {
        "project": project, "region": region, "cluster": cluster,
        "ns": ns, "gpu_vm": gpu_vm, "registry": registry,
    }


def action_deploy(console: Console, repo_root: Path) -> int:
    if not _require_gcloud(console):
        return 1
    cfg = _get_config(console)

    rc = _run(console, ["gcloud", "auth", "configure-docker",
                         f"{cfg['region']}-docker.pkg.dev", "--quiet"])
    if rc:
        return rc

    for name, ctx in [("api", "api"), ("ui", "ui")]:
        image = f"{cfg['registry']}/llmops-platform_{name}:latest"
        rc = _run(console, ["docker", "build", "-t", image,
                             str(repo_root / "platform" / ctx)])
        if rc:
            return rc
        rc = _run(console, ["docker", "push", image])
        if rc:
            return rc

    rc = _run(console, [
        "gcloud", "container", "clusters", "get-credentials",
        cfg["cluster"], "--region", cfg["region"], "--project", cfg["project"],
    ])
    if rc:
        return rc

    return action_up(console, repo_root, ns=cfg["ns"], gpu_vm=cfg["gpu_vm"])


def action_build(console: Console, repo_root: Path) -> int:
    if not _require_gcloud(console):
        return 1
    cfg = _get_config(console)
    for name, ctx in [("api", "api"), ("ui", "ui")]:
        image = f"{cfg['registry']}/llmops-platform_{name}:latest"
        rc = _run(console, ["docker", "build", "-t", image,
                             str(repo_root / "platform" / ctx)])
        if rc:
            return rc
        rc = _run(console, ["docker", "push", image])
        if rc:
            return rc
    return 0


def action_up(console: Console, repo_root: Path,
              ns: str = "llmops", gpu_vm: str = "") -> int:
    k8s_dir = repo_root / "platform" / "k8s"
    for manifest in [k8s_dir / "namespace.yaml", k8s_dir / "platform", k8s_dir / "ingress"]:
        rc = _run(console, ["kubectl", "apply", "-f", str(manifest)])
        if rc:
            return rc
    if gpu_vm:
        _run(console, ["kubectl", "set", "env", "deployment/llmops-api",
                        "-n", ns, f"ENGINE_HOST={gpu_vm}"])
    return 0


def action_validate(console: Console, repo_root: Path) -> int:
    ns = Prompt.ask("Namespace", default="llmops")
    return _run(console, ["kubectl", "get", "pods", "-n", ns, "-o", "wide"])


def action_teardown(console: Console, repo_root: Path) -> int:
    ns = Prompt.ask("Namespace to delete", default="llmops")
    return _run(console, ["kubectl", "delete", "namespace", ns])
