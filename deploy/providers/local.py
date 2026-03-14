"""
Local deployment provider — docker-compose / podman-compose.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from rich.console import Console
from rich.panel import Panel

# ── helpers ──────────────────────────────────────────────────────────────────

def _compose_cmd() -> list[str]:
    """Return the first available compose binary."""
    for cmd in (["podman-compose"], ["docker", "compose"], ["docker-compose"]):
        if shutil.which(cmd[0]):
            return cmd
    raise RuntimeError("No compose binary found. Install podman-compose or docker compose.")


def _run(console: Console, cmd: list[str], cwd: Path) -> int:
    console.print(f"[dim]$ {' '.join(cmd)}[/dim]")
    return subprocess.run(cmd, cwd=cwd).returncode


# ── actions ───────────────────────────────────────────────────────────────────

def action_deploy(console: Console, repo_root: Path) -> int:
    """Full local deploy: build images then bring services up."""
    platform_dir = repo_root / "platform"
    compose = _compose_cmd()

    console.print("[info]Building images...[/info]")
    rc = _run(console, [*compose, "build"], platform_dir)
    if rc:
        return rc

    console.print("[info]Starting services...[/info]")
    rc = _run(console, [*compose, "up", "-d"], platform_dir)
    if rc:
        return rc

    console.print("[info]Running DB migrations...[/info]")
    rc = _run(
        console,
        [*compose, "exec", "api", "alembic", "upgrade", "head"],
        platform_dir,
    )
    return rc


def action_up(console: Console, repo_root: Path) -> int:
    """Bring services up (skip build)."""
    platform_dir = repo_root / "platform"
    compose = _compose_cmd()
    return _run(console, [*compose, "up", "-d"], platform_dir)


def action_validate(console: Console, repo_root: Path) -> int:
    """Run the post-deploy validation script."""
    script = repo_root / "validate-setup.sh"
    if not script.exists():
        console.print(f"[warning]Validation script not found at {script}[/]")
        return 1
    return subprocess.run(["bash", str(script)], cwd=repo_root).returncode


def action_teardown(console: Console, repo_root: Path) -> int:
    """Stop and remove all containers and volumes."""
    platform_dir = repo_root / "platform"
    compose = _compose_cmd()
    return _run(console, [*compose, "down", "-v", "--remove-orphans"], platform_dir)
