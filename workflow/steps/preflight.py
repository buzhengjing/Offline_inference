"""Pre-flight step: Docker check, Python deps, model search/download."""

from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path

from ..docker_utils import docker_available, container_running
from ..retry import RetryPolicy
from ..state import WorkflowState
from ..step import WorkflowStep


class PreflightStep(WorkflowStep):
    def __init__(self):
        super().__init__(
            name="preflight",
            timeout=300,  # 5 min
            retry_policy=RetryPolicy(max_retries=2, retryable_errors=["pip install", "docker"]),
        )

    def execute(self, state: WorkflowState, logger: logging.Logger) -> WorkflowState:
        project_root = Path(state.project_root)

        # 1. Docker check
        if not docker_available():
            raise RuntimeError("Docker daemon not running or no permission")

        # 2. Python yaml dependency
        try:
            import yaml  # noqa: F401
        except ImportError:
            subprocess.run(
                ["pip3", "install", "pyyaml", "-q"],
                check=True, timeout=60,
            )

        # 3. Determine mode
        target = state.target
        if ":" in target or "/" in target:
            state.image_mode = True
        elif container_running(target):
            state.image_mode = False
            state.container_name = target
        else:
            state.image_mode = True

        # 4. Model path search (if not provided)
        model_safe = state.model.replace("/", "_")
        state.model_safe = model_safe

        if not state.model_path:
            state.model_path = self._search_model(state, project_root, logger)

        return state

    def _search_model(
        self, state: WorkflowState, project_root: Path, logger: logging.Logger
    ) -> str:
        check_script = project_root / "skills" / "flagos-container-preparation" / "tools" / "check_model_local.py"
        download_script = project_root / "skills" / "flagos-container-preparation" / "tools" / "download_model.py"

        # Search locally
        try:
            result = subprocess.run(
                ["python3", str(check_script), "--model", state.model, "--output-json"],
                capture_output=True, text=True, timeout=60,
            )
            if result.returncode == 0 and result.stdout.strip():
                data = json.loads(result.stdout)
                best = data.get("best_match", "")
                if best:
                    logger.info(
                        f"Model found: {best}",
                        extra={"step": self.name, "run_id": state.run_id},
                    )
                    return best
        except (subprocess.TimeoutExpired, json.JSONDecodeError, FileNotFoundError):
            pass

        # Download
        model_short = state.model.split("/")[-1] if "/" in state.model else state.model
        model_path = f"/data/models/{model_short}"
        logger.info(
            f"Model not found locally, downloading to {model_path}",
            extra={"step": self.name, "run_id": state.run_id},
        )

        try:
            result = subprocess.run(
                [
                    "python3", str(download_script),
                    "--model", state.model,
                    "--source", "auto",
                    "--local-dir", model_path,
                    "--json",
                ],
                capture_output=True, text=True, timeout=1800,
            )
            if result.returncode == 0:
                return model_path
        except subprocess.TimeoutExpired:
            pass

        # Fallback: let the agent handle it
        return model_path
