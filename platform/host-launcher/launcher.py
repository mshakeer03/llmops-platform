#!/usr/bin/env python3
"""
vLLM Host Launcher Service
==========================
Runs on the HOST machine so it can spawn/kill vLLM processes
(which need the host Python venv, not the container's environment).

The llmops-api container calls this via http://host.containers.internal:9001.

Usage (run once after boot, before starting engines from the UI):
    python3 /path/to/vllm_poc/platform/host-launcher/launcher.py

Or as a background daemon:
    nohup python3 platform/host-launcher/launcher.py > /tmp/host-launcher.log 2>&1 &

Environment variables:
    LAUNCHER_PORT        Port to listen on (default: 9001)
    LAUNCHER_VLLM_PYTHON Path to the vLLM python executable
                         (default: auto-detected from ../../vllm_env/bin/python)
    LAUNCHER_LOG_DIR     Where engine log files are written (default: /tmp/vllm_logs)
"""

import json
import os
import signal
import subprocess
import sys
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

# ── Configuration ─────────────────────────────────────────────────────────────

# Auto-detect vllm_env relative to this file: launcher.py → host-launcher/ → platform/ → vllm_poc/
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_DEFAULT_VLLM_PYTHON = str(_REPO_ROOT / "vllm_env" / "bin" / "python")

LAUNCHER_PORT  = int(os.environ.get("LAUNCHER_PORT", "9001"))
VLLM_PYTHON    = os.environ.get("LAUNCHER_VLLM_PYTHON", _DEFAULT_VLLM_PYTHON)
LOG_DIR        = Path(os.environ.get("LAUNCHER_LOG_DIR", "/tmp/vllm_logs"))


# ── Utilities ─────────────────────────────────────────────────────────────────

def make_log_path(alias: str, port: int) -> str:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    return str(LOG_DIR / f"engine_{alias}_{port}_{ts}.log")


def is_pid_alive(pid: int) -> bool:
    """Return True only if *pid* exists AND is not a zombie (defunct) process.

    ``os.kill(pid, 0)`` returns True for zombie processes because the OS keeps
    the PID in the table until the parent calls wait(). We check the process
    state explicitly to avoid false-positives from dead-but-unreaped children.
    """
    try:
        os.kill(pid, 0)  # raises if PID doesn't exist at all
    except (ProcessLookupError, PermissionError):
        return False

    # Check for zombie state: `ps -p PID -o stat=` returns "Z" on macOS/Linux
    try:
        import subprocess as _sp
        result = _sp.run(
            ["ps", "-p", str(pid), "-o", "stat="],
            capture_output=True, text=True, timeout=2,
        )
        stat = result.stdout.strip()
        if stat.startswith("Z"):
            return False  # zombie — effectively dead
    except Exception:
        pass  # if ps fails, fall through and assume alive

    return True


# ── Request Handler ──────────────────────────────────────────────────────────

class LauncherHandler(BaseHTTPRequestHandler):

    def log_message(self, fmt, *args):  # noqa: suppress per-request Apache logs
        pass

    def _send_json(self, code: int, obj: dict) -> None:
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self) -> dict:
        length = int(self.headers.get("Content-Length", 0))
        if length:
            return json.loads(self.rfile.read(length))
        return {}

    # ── POST /launch ──────────────────────────────────────────────────────────

    def _handle_launch(self) -> None:
        body = self._read_body()

        model_path  = body.get("model_path") or body.get("repo_id", "")
        alias       = body.get("alias", "model")
        port        = int(body.get("port", 9000))
        dtype       = body.get("dtype") or "bfloat16"
        gpu_mem     = float(body.get("gpu_memory_utilization") or 0.5)
        max_len     = body.get("max_model_len")
        served_name = body.get("served_model_name") or alias
        extra_args  = list(body.get("extra_args") or [])

        if not model_path:
            return self._send_json(400, {"error": "model_path or repo_id required"})

        log_path = make_log_path(alias, port)
        # Touch the file immediately so the container can start tailing before vLLM prints anything
        Path(log_path).touch()

        cmd = [
            VLLM_PYTHON,
            "-m", "vllm.entrypoints.openai.api_server",
            "--model",  model_path,
            "--host",   "0.0.0.0",
            "--port",   str(port),
            "--served-model-name", served_name,
            "--dtype",  dtype,
            "--gpu-memory-utilization", str(gpu_mem),
        ]
        if max_len:
            cmd += ["--max-model-len", str(int(max_len))]
        cmd += extra_args

        # Build a clean environment for the subprocess.
        # We explicitly set PYTHONPATH to the vllm source tree so Python resolves
        # the vllm package directly, bypassing editable-install .pth / egg-link
        # resolution which can fail when launched indirectly via launchd/systemd.
        vllm_src = str(_REPO_ROOT / "vllm")   # .../vllm_poc/vllm  (contains vllm/ pkg)
        proc_env = dict(os.environ)
        existing_pp = proc_env.get("PYTHONPATH", "")
        proc_env["PYTHONPATH"] = f"{vllm_src}:{existing_pp}" if existing_pp else vllm_src

        # Ensure vLLM can find the host HuggingFace model cache when given a
        # repo_id (e.g. "Qwen/Qwen2.5-0.5B-Instruct") rather than a local path.
        # launchd strips most env vars, so we set this explicitly.
        if not proc_env.get("HF_HOME"):
            proc_env["HF_HOME"] = str(Path.home() / ".cache" / "huggingface")

        with open(log_path, "a") as logfh:
            proc = subprocess.Popen(
                cmd,
                stdout=logfh,
                stderr=logfh,
                stdin=subprocess.DEVNULL,
                env=proc_env,
                start_new_session=True,   # detach from this server's process group
            )

        print(f"[launcher] started pid={proc.pid} port={port} alias={alias} log={log_path}",
              flush=True)
        self._send_json(200, {"pid": proc.pid, "log_path": log_path})

    # ── POST /stop ────────────────────────────────────────────────────────────

    def _handle_stop(self) -> None:
        body = self._read_body()
        pid = int(body.get("pid", 0))
        if not pid:
            return self._send_json(400, {"error": "pid required"})

        try:
            os.kill(pid, signal.SIGTERM)
            print(f"[launcher] SIGTERM → pid={pid}", flush=True)
            self._send_json(200, {"ok": True, "pid": pid})
        except ProcessLookupError:
            self._send_json(200, {"ok": True, "pid": pid, "note": "process already gone"})

    # ── GET /alive?pid=N ─────────────────────────────────────────────────────

    def _handle_alive(self) -> None:
        params = parse_qs(urlparse(self.path).query)
        pid_str = (params.get("pid") or ["0"])[0]
        try:
            pid   = int(pid_str)
            alive = is_pid_alive(pid)
        except (ValueError, TypeError):
            pid, alive = 0, False
        self._send_json(200, {"alive": alive, "pid": pid})

    # ── GET /health ───────────────────────────────────────────────────────────

    def _handle_health(self) -> None:
        self._send_json(200, {
            "ok": True,
            "vllm_python": VLLM_PYTHON,
            "vllm_python_exists": Path(VLLM_PYTHON).exists(),
            "log_dir": str(LOG_DIR),
            "port": LAUNCHER_PORT,
        })

    # ── Routing ───────────────────────────────────────────────────────────────

    def do_POST(self) -> None:
        path = self.path.split("?")[0]
        try:
            if path == "/launch":
                self._handle_launch()
            elif path == "/stop":
                self._handle_stop()
            else:
                self._send_json(404, {"error": f"Unknown path: {path}"})
        except Exception as exc:  # noqa: BLE001
            print(f"[launcher] ERROR in do_POST {path}: {exc}", flush=True)
            self._send_json(500, {"error": str(exc)})

    def do_GET(self) -> None:
        path = self.path.split("?")[0]
        try:
            if path in ("/health", "/"):
                self._handle_health()
            elif path == "/alive":
                self._handle_alive()
            else:
                self._send_json(404, {"error": f"Unknown path: {path}"})
        except Exception as exc:  # noqa: BLE001
            print(f"[launcher] ERROR in do_GET {path}: {exc}", flush=True)
            self._send_json(500, {"error": str(exc)})


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if not Path(VLLM_PYTHON).exists():
        print(
            f"[launcher] WARNING: VLLM_PYTHON={VLLM_PYTHON!r} not found!\n"
            f"           Set LAUNCHER_VLLM_PYTHON env var to the correct path, e.g.:\n"
            f"           export LAUNCHER_VLLM_PYTHON=/Users/yourname/vllm_poc/vllm_env/bin/python",
            file=sys.stderr,
        )

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    server = HTTPServer(("0.0.0.0", LAUNCHER_PORT), LauncherHandler)

    print("=" * 60)
    print(f"[launcher] vLLM Host Launcher ready")
    print(f"[launcher] Listening on ::{LAUNCHER_PORT}")
    print(f"[launcher] VLLM_PYTHON  = {VLLM_PYTHON}")
    print(f"[launcher] LOG_DIR      = {LOG_DIR}")
    print(f"[launcher] Health check : curl http://localhost:{LAUNCHER_PORT}/health")
    print("=" * 60)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[launcher] Shutting down.", flush=True)
