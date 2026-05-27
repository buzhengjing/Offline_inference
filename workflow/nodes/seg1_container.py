"""Seg1 container preparation nodes (deterministic)."""

from __future__ import annotations

import subprocess
from logging import Logger
from pathlib import Path

from ..deterministic_node import DeterministicNode
from ..docker_utils import container_running, find_container_by_prefix
from ..node import NodeResult
from ..retry import RetryPolicy
from ..state import WorkflowState


class SearchModelWeightsNode(DeterministicNode):
    def __init__(self):
        super().__init__(
            node_id="search_model_weights",
            timeout=120,
            retry_policy=RetryPolicy(max_retries=1, backoff_base=2.0),
        )

    def execute(self, state: WorkflowState, logger: Logger, ctx=None) -> NodeResult:
        project_root = Path(state.project_root)
        script = project_root / "skills" / "flagos-container-preparation" / "tools" / "check_model_local.py"

        result = self.run_on_host(
            ["python3", str(script), "--model", state.model, "--output-json"],
            timeout=self.timeout,
        )

        if result.returncode == 0 and result.stdout.strip():
            import json
            try:
                data = json.loads(result.stdout.strip())
                found = data.get("found", False)
                local_path = data.get("path", "")
                if found and local_path:
                    if not state.model_path:
                        state.model_path = local_path
                    return NodeResult(
                        success=True,
                        data={"found": True, "local_path": local_path},
                    )
            except json.JSONDecodeError:
                pass

        return NodeResult(
            success=True,
            data={"found": False, "local_path": None, "search_paths_checked": ["/data", "/mnt", "/models"]},
        )

    def get_input_snapshot(self, state: WorkflowState) -> dict:
        return {"model": state.model, "model_path": state.model_path}


class DetectGpuNode(DeterministicNode):
    def __init__(self):
        super().__init__(
            node_id="detect_gpu",
            timeout=30,
            retry_policy=RetryPolicy(max_retries=1),
        )

    def execute(self, state: WorkflowState, logger: Logger, ctx=None) -> NodeResult:
        gpu_checks = [
            ("nvidia", ["nvidia-smi", "--query-gpu=name,count", "--format=csv,noheader"]),
            ("ascend", ["npu-smi", "info"]),
            ("metax", ["mx-smi"]),
            ("mthreads", ["mthreads-gmi"]),
            ("iluvatar", ["ixsmi"]),
        ]

        for vendor, cmd in gpu_checks:
            try:
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
                if result.returncode == 0:
                    count = 1
                    model_name = ""
                    if vendor == "nvidia" and result.stdout.strip():
                        lines = result.stdout.strip().split("\n")
                        count = len(lines)
                        model_name = lines[0].split(",")[0].strip() if lines else ""
                    return NodeResult(
                        success=True,
                        data={"vendor": vendor, "count": count, "model_name": model_name},
                    )
            except (FileNotFoundError, subprocess.TimeoutExpired):
                continue

        return NodeResult(success=False, error="No GPU detected")


class CreateContainerNode(DeterministicNode):
    def __init__(self):
        super().__init__(
            node_id="create_container",
            timeout=120,
            retry_policy=RetryPolicy(max_retries=1, backoff_base=5.0),
            has_side_effects=True,
        )

    def should_skip(self, state: WorkflowState) -> bool:
        return not state.image_mode

    def execute(self, state: WorkflowState, logger: Logger, ctx=None) -> NodeResult:
        if not state.image_mode:
            if state.container_name and container_running(state.container_name):
                return NodeResult(success=True, data={"container": state.container_name, "action": "existing"})
            return NodeResult(success=False, error=f"Container '{state.container_name}' not running")

        model_short = state.model.split("/")[-1] if "/" in state.model else state.model
        container_name = f"{model_short}_offline_infer"
        model_safe = state.model_safe
        workspace_mount = f"/data/Offline_inference_workspace/{model_safe}"

        docker_cmd = [
            "docker", "run", "-itd",
            f"--name={container_name}",
            "--gpus=all",
            "--network=host",
        ]

        if state.model_path:
            docker_cmd.extend(["-v", f"{state.model_path}:{state.model_path}"])

        docker_cmd.extend(["-v", f"{workspace_mount}:/Offline_inference_workspace"])
        docker_cmd.append(state.target)

        result = self.run_on_host(docker_cmd, timeout=60)

        if result.returncode != 0:
            if "already in use" in result.stderr:
                state.container_name = container_name
                return NodeResult(success=True, data={"container": container_name, "action": "reused"})
            return NodeResult(success=False, error=f"docker run failed: {result.stderr}")

        state.container_name = container_name
        return NodeResult(success=True, data={"container": container_name, "action": "created"})

    def get_input_snapshot(self, state: WorkflowState) -> dict:
        return {"target": state.target, "image_mode": state.image_mode}


class DeployWorkspaceNode(DeterministicNode):
    def __init__(self):
        super().__init__(
            node_id="deploy_workspace",
            timeout=60,
            retry_policy=RetryPolicy(max_retries=2, backoff_base=3.0),
            has_side_effects=True,
        )

    def execute(self, state: WorkflowState, logger: Logger, ctx=None) -> NodeResult:
        project_root = Path(state.project_root)
        script = project_root / "skills" / "flagos-container-preparation" / "tools" / "setup_workspace.sh"

        result = self.run_on_host(
            ["bash", str(script), state.container_name, state.model],
            timeout=self.timeout,
            cwd=str(project_root),
        )

        if result.returncode != 0:
            return NodeResult(success=False, error=f"setup_workspace.sh failed: {result.stderr}")

        return NodeResult(success=True, data={"deployed": True})


class InitContextNode(DeterministicNode):
    def __init__(self):
        super().__init__(
            node_id="init_context",
            timeout=30,
            retry_policy=RetryPolicy(max_retries=1),
            has_side_effects=True,
        )

    def execute(self, state: WorkflowState, logger: Logger, ctx=None) -> NodeResult:
        gpu_data = {}  # Will be populated from state after detect_gpu
        cmd = (
            f"PATH=/opt/conda/bin:$PATH python3 /Offline_inference_workspace/scripts/update_context.py "
            f"--set container.name={state.container_name} "
            f"--set container.status=running "
            f"--set model.name={state.model} "
            f"--set model.container_path={state.model_path or ''} "
            f"--ledger-update 01_container_preparation --ledger-status success "
            f"--json"
        )

        result = self.run_in_container(state, cmd, timeout=20)
        if result.returncode != 0:
            return NodeResult(success=False, error=f"init_context failed: {result.stderr}")

        return NodeResult(success=True, data={"context_initialized": True})


class ValidateSeg1Node(DeterministicNode):
    def __init__(self):
        super().__init__(
            node_id="validate_seg1",
            timeout=30,
            retry_policy=RetryPolicy(max_retries=0),
        )

    def execute(self, state: WorkflowState, logger: Logger, ctx=None) -> NodeResult:
        if not state.container_name:
            return NodeResult(success=False, error="No container name set")

        if not container_running(state.container_name):
            return NodeResult(success=False, error=f"Container '{state.container_name}' not running")

        # Check context.yaml exists
        result = self.run_in_container(
            state, "test -f /Offline_inference_workspace/shared/context.yaml", timeout=10
        )
        if result.returncode != 0:
            return NodeResult(success=False, error="context.yaml not found in container")

        return NodeResult(success=True, data={"container": state.container_name, "validated": True})
