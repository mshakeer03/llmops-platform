"""
On-premises Kubernetes deployment provider.
Supports: kubeadm bare-metal, OpenShift, Rancher.
Requires: kubectl configured with cluster credentials.
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


def _get_config(console: Console) -> dict[str, str]:
    console.print()
    console.print(Panel(
        "On-prem deployment. Ensure kubectl is pointing at your cluster\n"
        "([dim]kubectl cluster-info[/dim] to verify).",
        title="On-Premises Configuration", border_style="white",
    ))
    registry = Prompt.ask("Private registry URL (e.g. registry.corp:5000/llmops)")
    ns       = Prompt.ask("Kubernetes namespace", default="llmops")
    gpu_vm   = Prompt.ask("GPU server private IP (leave blank to skip)", default="")
    return {"registry": registry, "ns": ns, "gpu_vm": gpu_vm}


def action_deploy(console: Console, repo_root: Path) -> int:
    cfg = _get_config(console)

    # Build & push to internal registry
    for name, ctx in [("api", "api"), ("ui", "ui")]:
        image = f"{cfg['registry']}/llmops-platform_{name}:latest"
        rc = _run(console, ["docker", "build", "-t", image,
                             str(repo_root / "platform" / ctx)])
        if rc:
            return rc
        rc = _run(console, ["docker", "push", image])
        if rc:
            return rc

    return action_up(console, repo_root, ns=cfg["ns"], gpu_vm=cfg["gpu_vm"])


def action_build(console: Console, repo_root: Path) -> int:
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
    if not k8s_dir.exists():
        console.print(f"[warning]K8s manifests not found at {k8s_dir}.[/]\n"
                       "Generate them first or point kubectl at your cluster.")
        return 1
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
    rc = _run(console, ["kubectl", "get", "pods", "-n", ns, "-o", "wide"])
    _run(console, ["kubectl", "get", "ingress", "-n", ns])
    return rc


def action_teardown(console: Console, repo_root: Path) -> int:
    ns = Prompt.ask("Namespace to delete", default="llmops")
    return _run(console, ["kubectl", "delete", "namespace", ns])


def action_airgap(console: Console, repo_root: Path) -> int:
    """Package images + model cache for fully offline install."""
    from providers import _airgap  # type: ignore[import]
    return _airgap.package(console, repo_root)
