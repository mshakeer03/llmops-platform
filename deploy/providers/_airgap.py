"""
Shared air-gap packaging utility.
Callable by any provider via:  python deploy/master.py --action airgap
"""
from __future__ import annotations

import datetime
import subprocess
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn

PLATFORM_IMAGES = [
    "postgres:16-alpine",
    "nginx:1.27-alpine",
    "node:22-alpine",
    "ghcr.io/berriai/litellm:main-latest",
    "ghcr.io/open-webui/open-webui:main",
    "ghcr.io/mlflow/mlflow:v2.14.3",
    "prom/prometheus:v2.53.0",
    "grafana/grafana:11.1.0",
]


def _run(console: Console, cmd: list[str]) -> int:
    console.print(f"[dim]$ {' '.join(cmd)}[/dim]")
    return subprocess.run(cmd).returncode


def package(console: Console, repo_root: Path) -> int:
    """Pull all images, build platform images, save to tarball."""
    datestamp = datetime.datetime.now().strftime("%Y%m%d")
    output = repo_root / f"llmops-images-{datestamp}.tar.gz"

    console.print(Panel(
        f"Output tarball: [bold]{output}[/bold]\n\n"
        "This may take 10–20 minutes depending on image sizes and internet speed.",
        title="Air-Gap Packaging",
        border_style="yellow",
    ))

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        # Pull base images
        task = progress.add_task("Pulling base images...", total=len(PLATFORM_IMAGES))
        for img in PLATFORM_IMAGES:
            progress.update(task, description=f"Pulling {img.split('/')[-1]}")
            subprocess.run(["docker", "pull", img], capture_output=True)
            progress.advance(task)

        # Build platform images
        progress.add_task("Building api image...")
        subprocess.run(["docker", "build", "-t", "llmops-platform_api:latest",
                         str(repo_root / "platform" / "api")], capture_output=True)

        progress.add_task("Building ui image...")
        subprocess.run(["docker", "build", "-t", "llmops-platform_ui:latest",
                         str(repo_root / "platform" / "ui")], capture_output=True)

    all_images = PLATFORM_IMAGES + [
        "llmops-platform_api:latest",
        "llmops-platform_ui:latest",
    ]

    console.print("[info]Saving all images to tarball...[/info]")
    rc = subprocess.run(
        f"docker save {' '.join(all_images)} | gzip > {output}",
        shell=True,
    ).returncode

    if rc == 0:
        size_mb = output.stat().st_size / 1_048_576
        console.print(f"\n[success]✓ Tarball created: {output} ({size_mb:.0f} MB)[/success]")
        console.print("\nTransfer to air-gapped host and load with:")
        console.print(f"  [dim]docker load < {output.name}[/dim]")
    return rc
