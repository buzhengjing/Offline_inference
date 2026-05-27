"""Seg3 FlagGems deterministic nodes."""

from __future__ import annotations

import time
from logging import Logger
from pathlib import Path

from ..deterministic_node import DeterministicNode
from ..docker_utils import sync_context
from ..node import NodeResult
from ..retry import RetryPolicy
from ..state import WorkflowState


class InstallFlaggemsNode(DeterministicNode):
    def __init__(self):
        super().__init__(
            node_id="install_flaggems", timeout=600,
            retry_policy=RetryPolicy(max_retries=2, backoff_base=5.0), has_side_effects=True,
        )

    def should_skip(self, state: WorkflowState) -> bool:
        return not state.native_inference_ok

    def execute(self, state: WorkflowState, logger: Logger, ctx=None) -> NodeResult:
        cmd = (
            "PATH=/opt/conda/bin:$PATH python3 "
            "/Offline_inference_workspace/scripts/install_component.py "
            "--component flaggems --action install --json"
        )
        result = self.run_in_container(state, cmd, timeout=self.timeout - 30)

        if result.returncode != 0:
            return NodeResult(success=False, error=f"FlagGems install failed: {result.stderr or result.stdout}")

        import json
        try:
            data = json.loads(result.stdout)
            return NodeResult(success=True, data={
                "component": "flaggems",
                "version": data.get("version", ""),
                "success": data.get("success", True),
            })
        except (json.JSONDecodeError, ValueError):
            if result.returncode == 0:
                return NodeResult(success=True, data={"component": "flaggems", "version": "", "success": True})
            return NodeResult(success=False, error="Cannot parse install output")


class InstallFlagtreeNode(DeterministicNode):
    def __init__(self):
        super().__init__(
            node_id="install_flagtree", timeout=600,
            retry_policy=RetryPolicy(max_retries=2, backoff_base=5.0), has_side_effects=True,
        )

    def should_skip(self, state: WorkflowState) -> bool:
        return not state.native_inference_ok

    def execute(self, state: WorkflowState, logger: Logger, ctx=None) -> NodeResult:
        gpu_vendor = state._node_data.get("gpu_vendor", "nvidia")
        cmd = (
            f"PATH=/opt/conda/bin:$PATH python3 "
            f"/Offline_inference_workspace/scripts/install_component.py "
            f"--component flagtree --action install --vendor {gpu_vendor} --json"
        )
        result = self.run_in_container(state, cmd, timeout=self.timeout - 30)

        if result.returncode != 0:
            return NodeResult(success=False, error=f"FlagTree install failed: {result.stderr or result.stdout}")

        import json
        try:
            data = json.loads(result.stdout)
            return NodeResult(success=True, data={
                "component": "flagtree",
                "version": data.get("version", ""),
                "success": data.get("success", True),
            })
        except (json.JSONDecodeError, ValueError):
            if result.returncode == 0:
                return NodeResult(success=True, data={"component": "flagtree", "version": "", "success": True})
            return NodeResult(success=False, error="Cannot parse install output")


class VerifyComponentsNode(DeterministicNode):
    def __init__(self):
        super().__init__(node_id="verify_components", timeout=60, retry_policy=RetryPolicy(max_retries=1))

    def should_skip(self, state: WorkflowState) -> bool:
        return not state.native_inference_ok

    def execute(self, state: WorkflowState, logger: Logger, ctx=None) -> NodeResult:
        gems_result = self.run_in_container(
            state,
            "PATH=/opt/conda/bin:$PATH python3 /Offline_inference_workspace/scripts/install_component.py "
            "--component flaggems --action verify --json"
        )
        tree_result = self.run_in_container(
            state,
            "PATH=/opt/conda/bin:$PATH python3 /Offline_inference_workspace/scripts/install_component.py "
            "--component flagtree --action verify --json"
        )

        import json
        gems_ok = gems_result.returncode == 0
        tree_ok = tree_result.returncode == 0
        gems_ver = ""
        tree_ver = ""

        try:
            if gems_ok:
                d = json.loads(gems_result.stdout)
                gems_ver = d.get("version", "")
                gems_ok = d.get("success", True)
        except (json.JSONDecodeError, ValueError):
            pass

        try:
            if tree_ok:
                d = json.loads(tree_result.stdout)
                tree_ver = d.get("version", "")
                tree_ok = d.get("success", True)
        except (json.JSONDecodeError, ValueError):
            pass

        return NodeResult(
            success=gems_ok,
            data={
                "flaggems_ok": gems_ok, "flaggems_version": gems_ver,
                "flagtree_ok": tree_ok, "flagtree_version": tree_ver,
            },
        )


class ExecuteFlaggemsInferenceNode(DeterministicNode):
    def __init__(self):
        super().__init__(
            node_id="execute_flaggems_inference", timeout=900,
            retry_policy=RetryPolicy(max_retries=0), has_side_effects=True,
        )

    def should_skip(self, state: WorkflowState) -> bool:
        return not state.native_inference_ok

    def execute(self, state: WorkflowState, logger: Logger, ctx=None) -> NodeResult:
        model_path = state.model_path or f"/data/models/{state.model}"
        cmd = (
            f"cd /root && python3 run_inference.py "
            f"--model_path {model_path} "
            f"--input_file /root/test_input.txt "
            f"--output_file /root/output_flaggems.txt "
            f"2>&1 | tee /root/inference_flaggems.log"
        )

        start = time.time()
        result = self.run_in_container(state, cmd, timeout=self.timeout - 30)
        duration = time.time() - start

        return NodeResult(
            success=(result.returncode == 0),
            data={
                "exit_code": result.returncode,
                "stdout_tail": "\n".join((result.stdout or "").split("\n")[-50:]),
                "duration_seconds": duration,
            },
            error=f"FlagGems inference failed: exit code {result.returncode}" if result.returncode != 0 else None,
        )


class CollectFlaggemsLogsNode(DeterministicNode):
    def __init__(self):
        super().__init__(node_id="collect_flaggems_logs", timeout=30, retry_policy=RetryPolicy(max_retries=1))

    def should_skip(self, state: WorkflowState) -> bool:
        return not state.native_inference_ok

    def execute(self, state: WorkflowState, logger: Logger, ctx=None) -> NodeResult:
        log_result = self.run_in_container(state, "cat /root/inference_flaggems.log 2>/dev/null | tail -100")
        gems_result = self.run_in_container(state, "cat /root/gems.txt 2>/dev/null")

        return NodeResult(
            success=True,
            data={
                "log_tail": log_result.stdout.strip() if log_result.returncode == 0 else "",
                "gems_txt": gems_result.stdout.strip() if gems_result.returncode == 0 else "",
                "gems_txt_exists": gems_result.returncode == 0 and bool(gems_result.stdout.strip()),
            },
        )


class ValidateFlaggemsOutputNode(DeterministicNode):
    def __init__(self):
        super().__init__(node_id="validate_flaggems_output", timeout=30, retry_policy=RetryPolicy(max_retries=0))

    def should_skip(self, state: WorkflowState) -> bool:
        return not state.native_inference_ok

    def execute(self, state: WorkflowState, logger: Logger, ctx=None) -> NodeResult:
        output_check = self.run_in_container(state, "test -s /root/output_flaggems.txt && echo EXISTS || echo MISSING")
        gems_check = self.run_in_container(state, "test -s /root/gems.txt && echo EXISTS || echo MISSING")

        output_exists = "EXISTS" in output_check.stdout
        gems_exists = "EXISTS" in gems_check.stdout

        return NodeResult(
            success=True,
            data={"valid": output_exists, "output_exists": output_exists, "gems_txt_exists": gems_exists},
        )


class DeployReadmeFlaggemsNode(DeterministicNode):
    def __init__(self):
        super().__init__(
            node_id="deploy_readme_flaggems", timeout=30,
            retry_policy=RetryPolicy(max_retries=1), has_side_effects=True,
        )

    def should_skip(self, state: WorkflowState) -> bool:
        return not state.native_inference_ok

    def execute(self, state: WorkflowState, logger: Logger, ctx=None) -> NodeResult:
        readme_content = state._node_data.get("readme_flaggems_content", "")
        if not readme_content:
            return NodeResult(success=False, error="No FlagGems README content")

        cmd = f"cat > /root/README.md << 'README_EOF'\n{readme_content}\nREADME_EOF"
        result = self.run_in_container(state, cmd, timeout=10)

        if result.returncode != 0:
            return NodeResult(success=False, error=f"Failed to write README: {result.stderr}")
        return NodeResult(success=True, data={"deployed": True})


class UpdateFlaggemsContextNode(DeterministicNode):
    def __init__(self):
        super().__init__(
            node_id="update_flaggems_context", timeout=30,
            retry_policy=RetryPolicy(max_retries=1), has_side_effects=True,
        )

    def should_skip(self, state: WorkflowState) -> bool:
        return not state.native_inference_ok

    def execute(self, state: WorkflowState, logger: Logger, ctx=None) -> NodeResult:
        gems_version = state._node_data.get("flaggems_version", "")
        tree_version = state._node_data.get("flagtree_version", "")

        cmd = (
            "PATH=/opt/conda/bin:$PATH python3 /Offline_inference_workspace/scripts/update_context.py "
            f"--set flaggems_inference.flaggems_version={gems_version} "
            f"--set flaggems_inference.flagtree_version={tree_version} "
            "--set flaggems_inference.success=true "
            "--set workflow.flaggems_inference_ok=true "
            "--ledger-update 06_flaggems_inference --ledger-status success --json"
        )
        result = self.run_in_container(state, cmd)
        sync_context(state.container_name, state.model, state.workspace_base)
        return NodeResult(success=True, data={"context_updated": True})


class ReleaseFlaggemsImageNode(DeterministicNode):
    def __init__(self):
        super().__init__(
            node_id="release_flaggems_image", timeout=600,
            retry_policy=RetryPolicy(max_retries=1, backoff_base=10.0), has_side_effects=True,
        )

    def should_skip(self, state: WorkflowState) -> bool:
        return not state.native_inference_ok

    def execute(self, state: WorkflowState, logger: Logger, ctx=None) -> NodeResult:
        project_root = Path(state.project_root)
        ctx_path = Path(state.workspace_base) / "config" / "context_snapshot.yaml"
        sync_context(state.container_name, state.model, state.workspace_base)

        cmd = [
            "python3", str(project_root / "skills" / "flagos-release" / "tools" / "main.py"),
            "--from-context", str(ctx_path),
        ]

        import os
        env = os.environ.copy()
        env["HARBOR_USER"] = state.harbor_user
        env["HARBOR_PASSWORD"] = state.harbor_password

        result = self.run_on_host(cmd, timeout=self.timeout - 30)

        if result.returncode != 0:
            return NodeResult(success=False, error=f"FlagGems release failed: {result.stderr or result.stdout}")
        return NodeResult(success=True, data={"released": True, "output": result.stdout[:500]})


class ValidateSeg3Node(DeterministicNode):
    def __init__(self):
        super().__init__(node_id="validate_seg3", timeout=60, retry_policy=RetryPolicy(max_retries=0))

    def should_skip(self, state: WorkflowState) -> bool:
        return not state.native_inference_ok

    def execute(self, state: WorkflowState, logger: Logger, ctx=None) -> NodeResult:
        sync_context(state.container_name, state.model, state.workspace_base)

        output_path = "/root/output_flaggems.txt"
        ctx_read = self.run_in_container(
            state,
            "python3 -c \"import yaml; d=yaml.safe_load(open('/Offline_inference_workspace/shared/context.yaml')); print(d.get('flaggems_inference',{}).get('output_path',''))\"",
        )
        if ctx_read.returncode == 0 and ctx_read.stdout.strip():
            output_path = ctx_read.stdout.strip()

        checks = {
            "output_flaggems": self.run_in_container(state, f"test -f {output_path}").returncode == 0,
            "gems_txt": self.run_in_container(state, "test -f /root/gems.txt").returncode == 0,
            "readme": self.run_in_container(state, "test -f /root/README.md").returncode == 0,
        }

        all_ok = all(checks.values())
        return NodeResult(success=all_ok, data=checks)
