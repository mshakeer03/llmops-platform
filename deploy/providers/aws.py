"""
AWS EKS deployment provider.
Requires: aws CLI authenticated + kubectl + eksctl installed.
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


def _require_aws(console: Console) -> bool:
    rc = subprocess.run(["aws", "sts", "get-caller-identity"], capture_output=True).returncode
    if rc:
        console.print("[error]AWS CLI not authenticated. Run: aws configure[/]")
        return False
    return True


def _get_config(console: Console) -> dict[str, str]:
    console.print()
    console.print(Panel(
        "Provide your AWS deployment configuration.",
        title="AWS Configuration", border_style="yellow",
    ))
    region    = Prompt.ask("AWS region",         default="us-east-1")
    cluster   = Prompt.ask("EKS cluster name",   default="eks-llmops-prod")
    ns        = Prompt.ask("Kubernetes namespace", default="llmops")
    gpu_vm    = Prompt.ask("GPU EC2 private IP (leave blank to skip)", default="")

    # Derive account ID
    result = subprocess.run(
        ["aws", "sts", "get-caller-identity", "--query", "Account", "--output", "text"],
        capture_output=True, text=True,
    )
    account_id = result.stdout.strip() if result.returncode == 0 else "ACCOUNT_ID"
    registry = f"{account_id}.dkr.ecr.{region}.amazonaws.com"

    return {
        "region": region, "cluster": cluster, "ns": ns,
        "gpu_vm": gpu_vm, "account_id": account_id, "registry": registry,
    }


def action_deploy(console: Console, repo_root: Path) -> int:
    if not _require_aws(console):
        return 1
    cfg = _get_config(console)

    # ECR login
    console.print("\n[info]Authenticating with ECR...[/info]")
    rc = subprocess.run(
        f"aws ecr get-login-password --region {cfg['region']} | "
        f"docker login --username AWS --password-stdin {cfg['registry']}",
        shell=True,
    ).returncode
    if rc:
        return rc

    # Build & push
    api_image = f"{cfg['registry']}/llmops-platform_api:latest"
    ui_image  = f"{cfg['registry']}/llmops-platform_ui:latest"
    for image, context in [(api_image, "api"), (ui_image, "ui")]:
        rc = _run(console, ["docker", "build", "-t", image, str(repo_root / "platform" / context)])
        if rc:
            return rc
        rc = _run(console, ["docker", "push", image])
        if rc:
            return rc

    # kubectl credentials
    rc = _run(console, [
        "aws", "eks", "update-kubeconfig",
        "--region", cfg["region"], "--name", cfg["cluster"],
    ])
    if rc:
        return rc

    return action_up(console, repo_root, ns=cfg["ns"], gpu_vm=cfg["gpu_vm"])


def action_build(console: Console, repo_root: Path) -> int:
    if not _require_aws(console):
        return 1
    cfg = _get_config(console)
    for name, ctx in [("api", "api"), ("ui", "ui")]:
        image = f"{cfg['registry']}/llmops-platform_{name}:latest"
        rc = _run(console, ["docker", "build", "-t", image, str(repo_root / "platform" / ctx)])
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
