"""Seg2 native inference deterministic nodes."""

from __future__ import annotations

import json
import subprocess
import time
from logging import Logger
from pathlib import Path

from ..deterministic_node import DeterministicNode
from ..docker_utils import sync_context
from ..node import NodeResult
from ..retry import RetryPolicy
from ..state import WorkflowState


class ReadEnvContextNode(DeterministicNode):
    def __init__(self):
        super().__init__(node_id="read_env_context", timeout=30, retry_policy=RetryPolicy(max_retries=1))

    def execute(self, state: WorkflowState, logger: Logger, ctx=None) -> NodeResult:
        result = self.run_in_container(state, "cat /Offline_inference_workspace/shared/context.yaml")
        if result.returncode != 0:
            return NodeResult(success=False, error="Cannot read context.yaml from container")

        import yaml
        try:
            ctx = yaml.safe_load(result.stdout) or {}
        except Exception as e:
            return NodeResult(success=False, error=f"Failed to parse context.yaml: {e}")

        env = ctx.get("env_exploration", {})
        return NodeResult(success=True, data={"env_exploration": env, "model": ctx.get("model", {})})


class DeployInferenceScriptNode(DeterministicNode):
    def __init__(self):
        super().__init__(
            node_id="deploy_inference_script", timeout=30,
            retry_policy=RetryPolicy(max_retries=1), has_side_effects=True,
        )

    def execute(self, state: WorkflowState, logger: Logger, ctx=None) -> NodeResult:
        script_content = state._node_data.get("inference_script_content", "")
        if not script_content:
            return NodeResult(success=False, error="No inference script content to deploy")

        escaped = script_content.replace("'", "'\\''")
        cmd = f"cat > /root/run_inference.py << 'SCRIPT_EOF'\n{script_content}\nSCRIPT_EOF"
        result = self.run_in_container(state, cmd, timeout=10)

        if result.returncode != 0:
            return NodeResult(success=False, error=f"Failed to write script: {result.stderr}")

        verify = self.run_in_container(state, "test -f /root/run_inference.py && wc -l /root/run_inference.py")
        return NodeResult(success=True, data={"deployed": True, "path": "/root/run_inference.py"})


class ExecuteInferenceNode(DeterministicNode):
    def __init__(self):
        super().__init__(
            node_id="execute_inference", timeout=900,
            retry_policy=RetryPolicy(max_retries=0), has_side_effects=True,
        )

    def execute(self, state: WorkflowState, logger: Logger, ctx=None) -> NodeResult:
        model_path = state.model_path or f"/data/models/{state.model}"
        cmd = (
            f"cd /root && python3 run_inference.py "
            f"--model_path {model_path} "
            f"--input_file /root/test_input.txt "
            f"--output_file /root/output_native.txt "
            f"2>&1 | tee /root/inference.log"
        )

        start = time.time()
        result = self.run_in_container(state, cmd, timeout=self.timeout - 30)
        duration = time.time() - start

        stdout_lines = result.stdout.strip().split("\n") if result.stdout else []
        stderr_lines = result.stderr.strip().split("\n") if result.stderr else []

        return NodeResult(
            success=(result.returncode == 0),
            data={
                "exit_code": result.returncode,
                "stdout_tail": "\n".join(stdout_lines[-50:]),
                "stderr_tail": "\n".join(stderr_lines[-30:]),
                "duration_seconds": duration,
            },
            error=f"Inference failed with exit code {result.returncode}" if result.returncode != 0 else None,
        )


class CollectExecutionLogsNode(DeterministicNode):
    def __init__(self):
        super().__init__(node_id="collect_execution_logs", timeout=30, retry_policy=RetryPolicy(max_retries=1))

    def execute(self, state: WorkflowState, logger: Logger, ctx=None) -> NodeResult:
        log_result = self.run_in_container(state, "cat /root/inference.log 2>/dev/null | tail -100")
        output_result = self.run_in_container(state, "ls -la /root/output_* 2>/dev/null")

        output_files = []
        if output_result.returncode == 0:
            for line in output_result.stdout.strip().split("\n"):
                if line.strip():
                    parts = line.split()
                    if parts:
                        output_files.append(parts[-1])

        return NodeResult(
            success=True,
            data={
                "log_tail": log_result.stdout.strip() if log_result.returncode == 0 else "",
                "output_files": output_files,
            },
        )


class ValidateInferenceOutputNode(DeterministicNode):
    def __init__(self):
        super().__init__(node_id="validate_inference_output", timeout=30, retry_policy=RetryPolicy(max_retries=0))

    def execute(self, state: WorkflowState, logger: Logger, ctx=None) -> NodeResult:
        file_check = self.run_in_container(
            state, "find /root -maxdepth 1 -name 'output_*' -size +0c -print -quit"
        )
        output_file = file_check.stdout.strip() if file_check.returncode == 0 else ""
        exists = bool(output_file)

        sample = ""
        if exists:
            sample_result = self.run_in_container(state, f"head -c 4096 {output_file}")
            sample = sample_result.stdout.strip() if sample_result.returncode == 0 else ""

        log_check = self.run_in_container(state, "test -s /root/inference.log && echo EXISTS || echo MISSING")
        log_exists = "EXISTS" in log_check.stdout

        content_valid = False
        content_issues = []

        if exists and sample:
            model_type = state._node_data.get("env_exploration", {}).get("model_type", "")
            content_valid, content_issues = self._validate_content(sample, model_type)

        valid = exists and bool(sample) and content_valid
        return NodeResult(
            success=True,
            data={
                "valid": valid,
                "output_file_exists": exists,
                "output_file": output_file if exists else None,
                "output_non_empty": bool(sample),
                "content_valid": content_valid,
                "content_issues": content_issues,
                "sample_output": sample[:500] if sample else None,
                "log_exists": log_exists,
            },
        )

    def _validate_content(self, sample: str, model_type: str) -> tuple[bool, list[str]]:
        """Check output content completeness based on model type."""
        issues = []
        parsed = self._try_parse(sample)
        if parsed is None:
            issues.append("output is not valid JSON/JSONL")
            return False, issues

        records = self._extract_records(parsed)
        if not records:
            issues.append("no result records found in output")
            return False, issues

        first = records[0]

        if model_type == "embedding":
            emb = first.get("embedding")
            if emb is None:
                emb_keys = [
                    k for k in first
                    if "embed" in k.lower() and "first" not in k.lower()
                    and "norm" not in k.lower() and "dim" not in k.lower()
                    and isinstance(first[k], list)
                ]
                emb = first.get(emb_keys[0]) if emb_keys else None
            if emb is None or not isinstance(emb, list):
                issues.append("no full embedding vector found (only partial like embedding_first_5?)")
            elif len(emb) < 16:
                issues.append(f"embedding vector too short ({len(emb)} dims), likely truncated")

        elif model_type in ("causal_lm", "vlm"):
            text_keys = ["output", "generated", "text", "response", "generated_text"]
            text_val = None
            for k in text_keys:
                if k in first and first[k]:
                    text_val = first[k]
                    break
            if text_val is None:
                issues.append("no generated text field found in output")

        elif model_type == "reranker":
            score = first.get("score")
            if score is None:
                score_keys = [k for k in first if "score" in k.lower()]
                score = first.get(score_keys[0]) if score_keys else None
            if score is None:
                issues.append("no score field found in output")
            elif not isinstance(score, (int, float)):
                issues.append(f"score is not numeric: {type(score).__name__}")

        return (len(issues) == 0), issues

    @staticmethod
    def _try_parse(sample: str):
        """Try parsing as JSON object/array or JSONL."""
        import json
        sample = sample.strip()
        try:
            return json.loads(sample)
        except (json.JSONDecodeError, ValueError):
            pass
        lines = [l for l in sample.split("\n") if l.strip()]
        if lines:
            try:
                return [json.loads(l) for l in lines]
            except (json.JSONDecodeError, ValueError):
                pass
        return None

    @staticmethod
    def _extract_records(parsed) -> list[dict]:
        """Extract result records from parsed output."""
        if isinstance(parsed, list):
            return [r for r in parsed if isinstance(r, dict)]
        if isinstance(parsed, dict):
            if "results" in parsed and isinstance(parsed["results"], list):
                return [r for r in parsed["results"] if isinstance(r, dict)]
            return [parsed]
        return []


class DeployReadmeNode(DeterministicNode):
    def __init__(self):
        super().__init__(
            node_id="deploy_readme", timeout=30,
            retry_policy=RetryPolicy(max_retries=1), has_side_effects=True,
        )

    def execute(self, state: WorkflowState, logger: Logger, ctx=None) -> NodeResult:
        readme_content = state._node_data.get("readme_content", "")
        if not readme_content:
            return NodeResult(success=False, error="No README content to deploy")

        cmd = f"cat > /root/README.md << 'README_EOF'\n{readme_content}\nREADME_EOF"
        result = self.run_in_container(state, cmd, timeout=10)

        if result.returncode != 0:
            return NodeResult(success=False, error=f"Failed to write README: {result.stderr}")
        return NodeResult(success=True, data={"deployed": True, "path": "/root/README.md"})


class UpdateNativeContextNode(DeterministicNode):
    def __init__(self):
        super().__init__(
            node_id="update_native_context", timeout=30,
            retry_policy=RetryPolicy(max_retries=1), has_side_effects=True,
        )

    def execute(self, state: WorkflowState, logger: Logger, ctx=None) -> NodeResult:
        cmd = (
            "PATH=/opt/conda/bin:$PATH python3 /Offline_inference_workspace/scripts/update_context.py "
            "--set native_inference.success=true "
            "--set native_inference.output_path=/root/output_native.txt "
            "--set workflow.native_inference_ok=true "
            "--ledger-update 03_native_inference --ledger-status success "
            "--json"
        )
        result = self.run_in_container(state, cmd)
        state.native_inference_ok = True
        if result.returncode != 0:
            return NodeResult(success=False, error=f"Context update failed: {result.stderr}")
        return NodeResult(success=True, data={"updated": True})


class ReleaseNativeImageNode(DeterministicNode):
    def __init__(self):
        super().__init__(
            node_id="release_native_image", timeout=600,
            retry_policy=RetryPolicy(max_retries=1, backoff_base=10.0), has_side_effects=True,
        )

    def execute(self, state: WorkflowState, logger: Logger, ctx=None) -> NodeResult:
        project_root = Path(state.project_root)
        ctx_path = Path(state.workspace_base) / "config" / "context_snapshot.yaml"

        sync_context(state.container_name, state.model, state.workspace_base)

        cmd = [
            "python3",
            str(project_root / "skills" / "flagos-release" / "tools" / "main.py"),
            "--from-context", str(ctx_path),
        ]

        import os
        env = os.environ.copy()
        env["HARBOR_USER"] = state.harbor_user
        env["HARBOR_PASSWORD"] = state.harbor_password

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=self.timeout - 30, env=env)

        if result.returncode != 0:
            return NodeResult(success=False, error=f"Release failed: {result.stderr[:500]}")

        return NodeResult(success=True, data={"released": True, "output": result.stdout[:200]})


class ValidateSeg2Node(DeterministicNode):
    def __init__(self):
        super().__init__(node_id="validate_seg2", timeout=60, retry_policy=RetryPolicy(max_retries=0))

    def execute(self, state: WorkflowState, logger: Logger, ctx=None) -> NodeResult:
        checks = {}
        script_check = self.run_in_container(state, "test -f /root/run_inference.py && echo OK || echo MISSING")
        checks["script"] = "OK" in script_check.stdout

        readme_check = self.run_in_container(state, "test -f /root/README.md && echo OK || echo MISSING")
        checks["readme"] = "OK" in readme_check.stdout

        output_check = self.run_in_container(
            state, "find /root -maxdepth 1 -name 'output_*' -size +0c -print -quit"
        )
        checks["output"] = bool(output_check.stdout.strip()) if output_check.returncode == 0 else False

        all_ok = all(checks.values())
        if not all_ok:
            missing = [k for k, v in checks.items() if not v]
            return NodeResult(success=False, error=f"Missing artifacts: {missing}")

        return NodeResult(success=True, data=checks)
