"""Seg1 environment exploration nodes (deterministic)."""

from __future__ import annotations

import json
from logging import Logger

from ..deterministic_node import DeterministicNode
from ..node import NodeResult
from ..retry import RetryPolicy
from ..state import WorkflowState


class DetectBaseEnvNode(DeterministicNode):
    def __init__(self):
        super().__init__(
            node_id="detect_base_env",
            timeout=60,
            retry_policy=RetryPolicy(max_retries=2),
        )

    def execute(self, state: WorkflowState, logger: Logger, ctx=None) -> NodeResult:
        py_result = self.run_in_container(state, "python3 --version 2>&1")
        python_version = py_result.stdout.strip().replace("Python ", "") if py_result.returncode == 0 else "unknown"

        cuda_result = self.run_in_container(
            state, "nvcc --version 2>/dev/null | grep release | awk '{print $6}' | tr -d 'V' || cat /usr/local/cuda/version.txt 2>/dev/null || echo unknown"
        )
        cuda_version = cuda_result.stdout.strip() or "unknown"

        pip_result = self.run_in_container(
            state, "pip list 2>/dev/null | grep -iE 'torch|transformers|triton|flag|llama.cpp|llama_cpp|vllm|sglang'"
        )
        key_packages = {}
        if pip_result.returncode == 0:
            for line in pip_result.stdout.strip().split("\n"):
                parts = line.split()
                if len(parts) >= 2:
                    key_packages[parts[0]] = parts[1]

        return NodeResult(
            success=True,
            data={
                "python_version": python_version,
                "cuda_version": cuda_version,
                "key_packages": key_packages,
            },
        )


class DetectModelFormatNode(DeterministicNode):
    def __init__(self):
        super().__init__(
            node_id="detect_model_format",
            timeout=30,
            retry_policy=RetryPolicy(max_retries=1),
        )

    def execute(self, state: WorkflowState, logger: Logger, ctx=None) -> NodeResult:
        model_path = state.model_path or f"/data/models/{state.model}"
        cmd = f"""
if ls {model_path}/*.gguf 1>/dev/null 2>&1; then echo 'gguf'
elif ls {model_path}/*.safetensors 1>/dev/null 2>&1; then echo 'safetensors'
elif ls {model_path}/*.bin 1>/dev/null 2>&1; then echo 'bin'
else echo 'unknown'
fi
"""
        result = self.run_in_container(state, cmd)
        fmt = result.stdout.strip() if result.returncode == 0 else "unknown"
        return NodeResult(success=True, data={"format": fmt})


class DetectModelTypeNode(DeterministicNode):
    def __init__(self):
        super().__init__(
            node_id="detect_model_type",
            timeout=30,
            retry_policy=RetryPolicy(max_retries=1),
        )

    def execute(self, state: WorkflowState, logger: Logger, ctx=None) -> NodeResult:
        model_path = state.model_path or f"/data/models/{state.model}"
        cmd = f"""python3 -c '
import json, os, sys
model_path = "{model_path}"
config_file = os.path.join(model_path, "config.json")
if not os.path.exists(config_file):
    print("causal_lm")
    sys.exit(0)
with open(config_file) as f:
    config = json.load(f)
architectures = config.get("architectures", [])
model_type = config.get("model_type", "")
arch_str = " ".join(architectures).lower()
if any(k in arch_str for k in ["embedding", "forsequenceclassification", "reranker"]):
    if "rerank" in arch_str or "forsequenceclassification" in arch_str:
        print("reranker")
    else:
        print("embedding")
elif "bert" in arch_str and "causal" not in arch_str:
    print("embedding")
elif any(k in arch_str for k in ["vl", "vision", "visual", "multimodal", "image"]) or any(k in model_type.lower() for k in ["vl", "vision", "visual"]):
    print("vlm")
else:
    print("causal_lm")
'"""
        result = self.run_in_container(state, cmd)
        model_type = result.stdout.strip() if result.returncode == 0 else "causal_lm"
        if model_type not in ("causal_lm", "vlm", "embedding", "reranker"):
            model_type = "causal_lm"
        return NodeResult(success=True, data={"model_type": model_type})


class DetectInferenceModeNode(DeterministicNode):
    def __init__(self):
        super().__init__(
            node_id="detect_inference_mode",
            timeout=30,
            retry_policy=RetryPolicy(max_retries=1),
        )

    def execute(self, state: WorkflowState, logger: Logger, ctx=None) -> NodeResult:
        model_path = state.model_path or f"/data/models/{state.model}"

        # Check for custom scripts
        script_cmd = f"""
SCRIPTS=$(find {model_path} -maxdepth 2 \\( -name 'run_inference*.py' -o -name 'demo*.py' -o -name 'inference*.py' -o -name 'predict*.py' -o -name 'generate*.py' \\) 2>/dev/null | head -5)
if [ -n "$SCRIPTS" ]; then
    echo "custom_script:$SCRIPTS"
else
    echo 'no_custom_script'
fi
"""
        script_result = self.run_in_container(state, script_cmd)
        script_output = script_result.stdout.strip()

        if script_output.startswith("custom_script:"):
            script_path = script_output.split(":", 1)[1].split("\n")[0].strip()
            return NodeResult(
                success=True,
                data={
                    "inference_mode": "custom_script",
                    "inference_framework": "custom_script",
                    "custom_script_path": script_path,
                },
            )

        # Check for GGUF + llama_cpp
        llama_result = self.run_in_container(
            state, "python3 -c 'from llama_cpp import Llama; print(\"ok\")' 2>/dev/null"
        )
        # Get model format from state context (set by previous node)
        format_cmd = f"ls {model_path}/*.gguf 1>/dev/null 2>&1 && echo gguf || echo other"
        format_result = self.run_in_container(state, format_cmd)

        if llama_result.returncode == 0 and "gguf" in format_result.stdout:
            return NodeResult(
                success=True,
                data={
                    "inference_mode": "offline_native",
                    "inference_framework": "llama_cpp",
                    "custom_script_path": None,
                },
            )

        return NodeResult(
            success=True,
            data={
                "inference_mode": "offline_native",
                "inference_framework": "transformers",
                "custom_script_path": None,
            },
        )


class WriteEnvContextNode(DeterministicNode):
    def __init__(self):
        super().__init__(
            node_id="write_env_context",
            timeout=30,
            retry_policy=RetryPolicy(max_retries=1),
            has_side_effects=True,
        )

    def execute(self, state: WorkflowState, logger: Logger, ctx=None) -> NodeResult:
        # Collect data from previous node results stored in state
        # This node reads from state._node_outputs populated by runner
        env_data = getattr(state, "_env_exploration_data", {})
        if not env_data:
            return NodeResult(success=False, error="No env exploration data available")

        sets = []
        for key, value in env_data.items():
            if value:
                sets.append(f"--set env_exploration.{key}={value}")

        sets_str = " ".join(sets)
        cmd = (
            f"PATH=/opt/conda/bin:$PATH python3 /Offline_inference_workspace/scripts/update_context.py "
            f"{sets_str} "
            f"--ledger-update 02_env_exploration --ledger-status success --json"
        )

        result = self.run_in_container(state, cmd, timeout=15)
        if result.returncode != 0:
            return NodeResult(success=False, error=f"update_context.py failed: {result.stderr}")

        return NodeResult(success=True, data={"context_updated": True})
