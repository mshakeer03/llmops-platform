#!/usr/bin/env python3
"""
LLMOps Platform — Master Deployment CLI
========================================
Interactive deployment tool for on-prem and cloud environments.

Usage:
    python deploy/master.py
    python deploy/master.py --provider azure --action deploy
    python deploy/master.py --provider local  --action up

Requirements:
    pip install -r deploy/requirements.txt
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

try:
    import questionary
    from rich import print as rprint
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text
    from rich.rule import Rule
    from rich.prompt import Confirm
    from rich.progress import Progress, SpinnerColumn, TextColumn
    from rich.theme import Theme
except ImportError:
    print("Missing dependencies — run: pip install -r deploy/requirements.txt")
    sys.exit(1)

# ── Theme ────────────────────────────────────────────────────────────────────

THEME = Theme({
    "info":     "bold cyan",
    "success":  "bold green",
    "warning":  "bold yellow",
    "error":    "bold red",
    "heading":  "bold white",
    "muted":    "dim white",
    "accent":   "bold blue",
})

console = Console(theme=THEME)

REPO_ROOT = Path(__file__).resolve().parent.parent

# ── Provider registry ────────────────────────────────────────────────────────

PROVIDERS = {
    "local":  {
        "label":       "Local (docker-compose / podman-compose)",
        "description": "Single machine dev/demo deployment using Compose",
        "module":      "providers.local",
        "icon":        "🖥️ ",
    },
    "azure":  {
        "label":       "Azure AKS",
        "description": "Azure Kubernetes Service with ACR + Azure Files",
        "module":      "providers.azure",
        "icon":        "☁️ ",
    },
    "aws":    {
        "label":       "AWS EKS",
        "description": "Elastic Kubernetes Service with ECR + EFS",
        "module":      "providers.aws",
        "icon":        "☁️ ",
    },
    "gcp":    {
        "label":       "GCP GKE",
        "description": "Google Kubernetes Engine with Artifact Registry + Filestore",
        "module":      "providers.gcp",
        "icon":        "☁️ ",
    },
    "onprem": {
        "label":       "On-Premises Kubernetes",
        "description": "Bare-metal kubeadm or OpenShift cluster",
        "module":      "providers.onprem",
        "icon":        "🏢 ",
    },
}

ACTIONS = {
    "deploy":    "Full deploy (build → push → apply manifests → migrate DB)",
    "build":     "Build & push container images only",
    "up":        "Apply Kubernetes/Compose manifests only (skip image build)",
    "validate":  "Post-deployment health check",
    "teardown":  "Destroy all platform resources (destructive!)",
    "airgap":    "Package all images + HF model cache as tarballs",
}

# ── Banner ───────────────────────────────────────────────────────────────────

def print_banner() -> None:
    console.print()
    console.print(Panel.fit(
        Text.assemble(
            ("  ⚡ LLMOps Platform  ", "bold white on blue"),
            "\n",
            ("  Master Deployment CLI  ", "bold cyan"),
        ),
        border_style="blue",
        padding=(0, 2),
    ))
    console.print()


def print_status_table() -> None:
    """Show a quick summary of what's wired up."""
    t = Table(title="Supported Targets", show_header=True, header_style="bold cyan",
              border_style="dim white", min_width=60)
    t.add_column("Key",         style="accent",  width=10)
    t.add_column("Target",      style="heading", width=30)
    t.add_column("Status",      justify="center", width=12)
    t.add_column("Description", style="muted")

    status_map = {
        "local":  ("[green]✓ Ready[/]",      "docker-compose / podman"),
        "azure":  ("[green]✓ Ready[/]",      "AKS + ACR + Azure Files"),
        "aws":    ("[green]✓ Ready[/]",      "EKS + ECR + EFS"),
        "gcp":    ("[green]✓ Ready[/]",      "GKE + Artifact Registry"),
        "onprem": ("[green]✓ Ready[/]",      "kubeadm / OpenShift"),
    }

    for key, info in PROVIDERS.items():
        status, desc = status_map[key]
        t.add_row(key, f"{info['icon']} {info['label']}", status, desc)

    console.print(t)
    console.print()


# ── Interactive menu ──────────────────────────────────────────────────────────

def ask_provider() -> str:
    choices = [
        questionary.Choice(
            title=f"{info['icon']}  {info['label']:40s}  — {info['description']}",
            value=key,
        )
        for key, info in PROVIDERS.items()
    ]
    return questionary.select(
        "Select deployment target:",
        choices=choices,
        use_indicator=True,
        style=questionary.Style([
            ("selected",       "fg:cyan bold"),
            ("pointer",        "fg:blue bold"),
            ("highlighted",    "fg:cyan"),
            ("answer",         "fg:green bold"),
            ("question",       "fg:white bold"),
        ]),
    ).ask()


def ask_action(provider: str) -> str:
    # local only supports a subset of actions
    available = list(ACTIONS.keys())
    if provider == "local":
        available = ["deploy", "up", "validate", "teardown"]

    choices = [
        questionary.Choice(
            title=f"{action:12s}  {desc}",
            value=action,
        )
        for action, desc in ACTIONS.items()
        if action in available
    ]
    return questionary.select(
        "Select action:",
        choices=choices,
        use_indicator=True,
        style=questionary.Style([
            ("selected",    "fg:cyan bold"),
            ("pointer",     "fg:blue bold"),
            ("answer",      "fg:green bold"),
            ("question",    "fg:white bold"),
        ]),
    ).ask()


def ask_confirm(provider: str, action: str) -> bool:
    if action == "teardown":
        console.print(Panel(
            f"[error]WARNING: This will DESTROY all platform resources for target '[bold]{provider}[/bold]'.\n"
            f"This action is IRREVERSIBLE. All data will be lost.",
            title="⚠️  Destructive Action",
            border_style="red",
        ))

    return Confirm.ask(
        f"\nProceed with [accent]{action}[/accent] on [accent]{provider}[/accent]?",
        default=True,
    )


# ── Action dispatch ───────────────────────────────────────────────────────────

def _run(cmd: list[str], cwd: Path | None = None) -> int:
    """Run a shell command, streaming output to the terminal."""
    console.print(f"[muted]$ {' '.join(cmd)}[/]")
    result = subprocess.run(cmd, cwd=cwd or REPO_ROOT)
    return result.returncode


def dispatch(provider: str, action: str) -> int:
    """Route action to the appropriate provider module."""
    console.print(Rule(f"[heading]{PROVIDERS[provider]['icon']} {PROVIDERS[provider]['label']}  ·  {action}"))
    console.print()

    # airgap is provider-agnostic — always use the shared utility
    if action == "airgap":
        try:
            from providers._airgap import package
            return package(console, REPO_ROOT) or 0
        except Exception as exc:  # noqa: BLE001
            console.print(f"[error]Air-gap packaging failed: {exc}[/]")
            return 1

    # Dynamic import to keep startup fast
    try:
        import importlib
        mod = importlib.import_module(f"providers.{provider}")
        handler = getattr(mod, f"action_{action}", None)
        if handler is None:
            console.print(f"[error]Action '{action}' is not implemented for provider '{provider}'.[/]")
            return 1
        return handler(console, REPO_ROOT) or 0
    except ModuleNotFoundError:
        console.print(f"[error]Provider module 'providers/{provider}.py' not found.[/]")
        return 1
    except Exception as exc:  # noqa: BLE001
        console.print(f"[error]Error: {exc}[/]")
        import traceback
        console.print_exception()
        return 1


# ── CLI entrypoint ────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="LLMOps Platform — Master Deployment CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--provider", choices=list(PROVIDERS.keys()),
                        help="Skip interactive provider selection")
    parser.add_argument("--action",   choices=list(ACTIONS.keys()),
                        help="Skip interactive action selection")
    parser.add_argument("--yes", "-y", action="store_true",
                        help="Skip confirmation prompts")
    parser.add_argument("--list",  action="store_true",
                        help="List available providers and exit")
    args = parser.parse_args()

    print_banner()

    if args.list:
        print_status_table()
        return

    print_status_table()

    # ── Interactive prompts (when not fully specified via flags) ─────────────
    provider = args.provider or ask_provider()
    if provider is None:        # user pressed Ctrl-C
        console.print("\n[muted]Aborted.[/]")
        return

    action = args.action or ask_action(provider)
    if action is None:
        console.print("\n[muted]Aborted.[/]")
        return

    if not args.yes and not ask_confirm(provider, action):
        console.print("\n[muted]Cancelled.[/]")
        return

    # ── Dispatch ─────────────────────────────────────────────────────────────
    rc = dispatch(provider, action)

    console.print()
    if rc == 0:
        console.print(Panel(
            f"[success]✓ {action.upper()} completed successfully.[/]\n\n"
            f"Run [accent]python deploy/master.py --provider {provider} --action validate[/] "
            f"to verify the deployment.",
            title="Done",
            border_style="green",
        ))
    else:
        console.print(Panel(
            f"[error]✗ {action.upper()} finished with errors (exit code {rc}).[/]\n"
            f"Check the output above for details.",
            title="Failed",
            border_style="red",
        ))
        sys.exit(rc)


if __name__ == "__main__":
    # Ensure the deploy/ dir is on sys.path for relative provider imports
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    main()
