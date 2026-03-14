"""
Azure AKS deployment provider.
Requires: az CLI authenticated + kubectl configured.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import questionary
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt

# ── helpers ───────────────────────────────────────────────────────────────────

def _run(console: Console, cmd: list[str], cwd: Path | None = None) -> int:
    console.print(f"[dim]$ {' '.join(str(c) for c in cmd)}[/dim]")
    result = subprocess.run(cmd, cwd=cwd)
    return result.returncode


def _require_az(console: Console) -> bool:
    rc = subprocess.run(["az", "account", "show"], capture_output=True).returncode
    if rc:
        console.print("[error]Azure CLI not logged in. Run: az login[/]")
        return False
    return True


def _get_config(console: Console) -> dict[str, str] | None:
    """Interactively collect Azure deployment config."""
    console.print()
    console.print(Panel(
        "Provide your Azure deployment configuration.\n"
        "[dim]Press Enter to accept the default shown in (parentheses).[/dim]",
        title="Azure Configuration",
        border_style="blue",
    ))

    rg       = Prompt.ask("Resource group",              default="rg-llmops-prod")
    location = Prompt.ask("Azure region",                default="eastus")
    cluster  = Prompt.ask("AKS cluster name",            default="aks-llmops-prod")
    acr      = Prompt.ask("ACR name (no .azurecr.io)",   default="acrllmopsprod")
    ns       = Prompt.ask("Kubernetes namespace",        default="llmops")
    gpu_vm   = Prompt.ask("GPU VM private IP (Option A hybrid, leave blank to skip)", default="")

    return {
        "rg": rg, "location": location, "cluster": cluster,
        "acr": acr, "ns": ns, "gpu_vm": gpu_vm,
    }


# ── actions ───────────────────────────────────────────────────────────────────

def action_deploy(console: Console, repo_root: Path) -> int:
    """Full Azure deploy: build → push → k8s apply → migrate."""
    if not _require_az(console):
        return 1

    cfg = _get_config(console)
    if not cfg:
        return 1

    registry = f"{cfg['acr']}.azurecr.io"
    api_image = f"{registry}/llmops-platform_api:latest"
    ui_image  = f"{registry}/llmops-platform_ui:latest"
    k8s_dir   = repo_root / "platform" / "k8s"

    # 1. Login to ACR
    console.print("\n[info]Authenticating with ACR...[/info]")
    rc = _run(console, ["az", "acr", "login", "--name", cfg["acr"]])
    if rc:
        return rc

    # 2. Build & push
    rc = action_build(console, repo_root, api_image=api_image, ui_image=ui_image)
    if rc:
        return rc

    # 3. Get credentials
    console.print("\n[info]Fetching AKS credentials...[/info]")
    rc = _run(console, [
        "az", "aks", "get-credentials",
        "--resource-group", cfg["rg"],
        "--name", cfg["cluster"],
        "--overwrite-existing",
    ])
    if rc:
        return rc

    # 4. Apply manifests
    rc = action_up(console, repo_root, registry=registry, ns=cfg["ns"], gpu_vm=cfg["gpu_vm"])
    if rc:
        return rc

    # 5. Migrate DB
    console.print("\n[info]Running DB migrations...[/info]")
    rc = _run(console, [
        "kubectl", "exec", "-n", cfg["ns"],
        f"$(kubectl get pod -n {cfg['ns']} -l app=llmops-api -o jsonpath='{{.items[0].metadata.name}}')",
        "--", "alembic", "upgrade", "head",
    ])
    return rc


def action_build(
    console: Console,
    repo_root: Path,
    api_image: str | None = None,
    ui_image: str | None = None,
) -> int:
    """Build and push images to ACR."""
    if api_image is None or ui_image is None:
        cfg = _get_config(console)
        if not cfg:
            return 1
        registry = f"{cfg['acr']}.azurecr.io"
        api_image = f"{registry}/llmops-platform_api:latest"
        ui_image  = f"{registry}/llmops-platform_ui:latest"

    console.print("\n[info]Building API image...[/info]")
    rc = _run(console, ["docker", "build", "-t", api_image, str(repo_root / "platform" / "api")])
    if rc:
        return rc
    console.print("\n[info]Pushing API image...[/info]")
    rc = _run(console, ["docker", "push", api_image])
    if rc:
        return rc

    console.print("\n[info]Building UI image...[/info]")
    rc = _run(console, ["docker", "build", "-t", ui_image, str(repo_root / "platform" / "ui")])
    if rc:
        return rc
    console.print("\n[info]Pushing UI image...[/info]")
    return _run(console, ["docker", "push", ui_image])


def action_up(
    console: Console,
    repo_root: Path,
    registry: str | None = None,
    ns: str = "llmops",
    gpu_vm: str = "",
) -> int:
    """Apply Kubernetes manifests."""
    k8s_dir = repo_root / "platform" / "k8s"
    if not k8s_dir.exists():
        console.print(f"[error]K8s manifests not found at {k8s_dir}[/]")
        return 1

    _run(console, ["kubectl", "create", "namespace", ns, "--dry-run=client", "-o", "yaml"])

    cmds: list[list[str]] = [
        ["kubectl", "apply", "-f", str(k8s_dir / "namespace.yaml")],
        ["kubectl", "apply", "-f", str(k8s_dir / "platform")],
        ["kubectl", "apply", "-f", str(k8s_dir / "ingress")],
    ]
    for cmd in cmds:
        rc = _run(console, cmd)
        if rc:
            return rc

    if gpu_vm:
        _run(console, [
            "kubectl", "set", "env", "deployment/llmops-api",
            "-n", ns, f"ENGINE_HOST={gpu_vm}",
        ])

    return 0


def action_validate(console: Console, repo_root: Path) -> int:
    """Check pod health after deployment."""
    ns = Prompt.ask("Namespace to check", default="llmops")
    return _run(console, ["kubectl", "get", "pods", "-n", ns, "-o", "wide"])


def action_teardown(console: Console, repo_root: Path) -> int:
    """Delete entire namespace (all platform resources)."""
    ns = Prompt.ask("Namespace to delete", default="llmops")
    console.print(f"\n[error]Deleting namespace '{ns}' — all data will be lost![/]")
    return _run(console, ["kubectl", "delete", "namespace", ns])
