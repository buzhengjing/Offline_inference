"""Docker utility functions migrated from shell."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Optional

import yaml


def docker_available() -> bool:
    try:
        subprocess.run(["docker", "ps"], capture_output=True, check=True, timeout=10)
        return True
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        return False


def container_running(container: str) -> bool:
    try:
        result = subprocess.run(
            ["docker", "inspect", "--type=container", container],
            capture_output=True, timeout=10,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def find_container_by_prefix(prefix: str) -> Optional[str]:
    try:
        result = subprocess.run(
            ["docker", "ps", "--filter", f"name={prefix}", "--format", "{{.Names}}"],
            capture_output=True, text=True, timeout=10,
        )
        names = result.stdout.strip().split("\n")
        return names[0] if names and names[0] else None
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None


def docker_exec(container: str, cmd: str, timeout: float = 60) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["docker", "exec", container, "bash", "-c", cmd],
        capture_output=True, text=True, timeout=timeout,
    )


def sync_context(container: str, model: str, workspace_base: str) -> Optional[Path]:
    """Sync context.yaml from container to host config directory.

    Returns path to context_snapshot.yaml on success, None on failure.
    """
    model_safe = model.replace("/", "_")
    config_dir = Path(workspace_base) / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    ctx_file = config_dir / "context_snapshot.yaml"

    # Check mount mode
    result = docker_exec(
        container,
        "cat /Offline_inference_workspace/.mount_mode 2>/dev/null || echo internal",
        timeout=10,
    )
    mount_mode = result.stdout.strip()

    if mount_mode in ("mounted", "symlink"):
        src = Path(workspace_base) / "shared" / "context.yaml"
        if src.exists():
            shutil.copy2(src, ctx_file)
            return ctx_file
    else:
        # docker cp from container
        tmp = ctx_file.with_suffix(".tmp")
        cp_result = subprocess.run(
            ["docker", "cp", f"{container}:/Offline_inference_workspace/shared/context.yaml", str(tmp)],
            capture_output=True, timeout=30,
        )
        if cp_result.returncode == 0 and tmp.exists():
            tmp.replace(ctx_file)
            return ctx_file
        tmp.unlink(missing_ok=True)

    return None


def check_native_inference_ok(container: str, ctx_file: Path) -> str:
    """Check if native inference succeeded.

    Returns: "OK", "TERMINATED", "FAILED", or "ERROR"
    """
    # Check key artifact files in container
    has_script = docker_exec(container, "test -f /root/run_inference.py", timeout=10).returncode == 0
    has_readme = docker_exec(container, "test -f /root/README.md", timeout=10).returncode == 0

    # Read workflow fields from context
    if not ctx_file.exists():
        return "ERROR"

    try:
        with open(ctx_file) as f:
            ctx = yaml.safe_load(f) or {}
        workflow = ctx.get("workflow", {})
        ok = workflow.get("native_inference_ok", False)
        terminated = workflow.get("terminated", False)
    except Exception:
        return "ERROR"

    if terminated:
        return "TERMINATED"
    elif ok:
        if has_script and has_readme:
            return "OK"
        else:
            return "FAILED"
    else:
        if has_script and has_readme:
            return "OK"
        return "FAILED"
