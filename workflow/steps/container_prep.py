"""Seg1: Container preparation + environment exploration (Claude call)."""

from __future__ import annotations

import logging
from pathlib import Path

from ..claude_executor import ClaudeExecutor
from ..docker_utils import find_container_by_prefix, sync_context
from ..retry import RetryPolicy
from ..state import WorkflowState
from ..step import WorkflowStep


class Seg1ContainerEnvStep(WorkflowStep):
    def __init__(self):
        super().__init__(
            name="seg1_container_env",
            timeout=1800,  # 30 min
            retry_policy=RetryPolicy(max_retries=1, retryable_errors=["claude exit code"]),
        )

    def execute(self, state: WorkflowState, logger: logging.Logger) -> WorkflowState:
        project_root = Path(state.project_root)
        model_safe = state.model_safe
        log_dir = Path(state.log_dir) / "steps"
        log_dir.mkdir(parents=True, exist_ok=True)

        prompt = self._build_prompt(state)

        executor = ClaudeExecutor(project_root)
        exit_code = executor.run(
            prompt=prompt,
            max_turns=80,
            timeout_seconds=self.timeout - 60,
            log_path=log_dir / "seg1_container_env.log",
            jsonl_path=log_dir / "seg1_container_env_claude.jsonl",
            env_extra={
                "HARBOR_USER": state.harbor_user,
                "HARBOR_PASSWORD": state.harbor_password,
            },
            verbose=state.verbose,
        )

        step_record = state.get_step(self.name)
        if step_record:
            step_record.claude_exit_code = exit_code

        # Discover container name if image mode
        if state.image_mode and not state.container_name:
            model_short = state.model.split("/")[-1] if "/" in state.model else state.model
            container = find_container_by_prefix(f"{model_short}_offline_infer")
            if not container:
                raise RuntimeError(
                    f"Seg1 did not produce a running container (expected: {model_short}_offline_infer)"
                )
            state.container_name = container

        # Sync context
        if state.container_name:
            sync_context(state.container_name, state.model, state.workspace_base)
            self._reset_workflow_state(state)
            self._clean_stale_artifacts(state)

        return state

    def _reset_workflow_state(self, state: WorkflowState):
        """Reset workflow execution flags in context.yaml so seg2/seg3 run fresh."""
        from ..docker_utils import docker_exec
        cmd = (
            "python3 /Offline_inference_workspace/scripts/update_context.py "
            "--set workflow.native_inference_ok=false "
            "--set workflow.native_released=false "
            "--set workflow.flaggems_installed=false "
            "--set workflow.flaggems_inference_ok=false "
            "--set workflow.flaggems_released=false "
            "--set workflow.all_done=false "
            "--set workflow.terminated=false "
            "--set workflow.termination_reason="
        )
        docker_exec(state.container_name, cmd, timeout=30)

    def _clean_stale_artifacts(self, state: WorkflowState):
        """Remove inference artifacts from previous runs."""
        from ..docker_utils import docker_exec
        docker_exec(
            state.container_name,
            "rm -f /root/run_inference.py /root/output_native.txt "
            "/root/output_embeddings.npy /root/output_flaggems.txt "
            "/root/gems.txt /root/README.md /root/test_input.txt",
            timeout=10,
        )

    def _build_prompt(self, state: WorkflowState) -> str:
        model_safe = state.model_safe

        if state.image_mode:
            step1_desc = (
                f"镜像: {state.target}，模型名: {state.model}，"
                f"模型路径: {state.model_path}，"
                f"宿主机工作目录: /data/Offline_inference_workspace/{model_safe}"
            )
            entry_type = "入口类型: 镜像模式，需要 docker run 创建容器"
        else:
            step1_desc = (
                f"容器: {state.container_name}，模型名: {state.model}，"
                f"模型路径: {state.model_path}，"
                f"宿主机工作目录: /data/Offline_inference_workspace/{model_safe}"
            )
            entry_type = "入口类型: 已有容器模式"

        return f"""{step1_desc}

请执行离线推理验证流水线的段1（步骤1-2）：

**步骤1 — 容器准备**
阅读 skills/flagos-container-preparation/SKILL.md，按其流程执行。
{entry_type}
docker run 时的工作空间挂载必须原样使用（已计算好，禁止修改此路径）: -v /data/Offline_inference_workspace/{model_safe}:/Offline_inference_workspace
注意: 上面的路径中模型名的 / 已替换为 _，这是正确的，不要改回含 / 的形式。

**步骤2 — 环境探索**
阅读 skills/flagos-env-exploration/SKILL.md，按其流程执行。
探测容器内推理框架、模型格式、关键依赖。

**规则**：
- 每步开始前输出 [步骤X] <名称> — 开始
- 每步完成后输出 [步骤X] <名称> — 完成
- 通过 docker exec 操作容器内文件
- 使用 update_context.py 更新 context.yaml
- 每步完成后同步 context_snapshot.yaml
- Harbor 凭证已通过环境变量 HARBOR_USER / HARBOR_PASSWORD 提供（如需 docker login）
"""


class Seg1ValidateStep(WorkflowStep):
    def __init__(self):
        super().__init__(
            name="seg1_validate",
            timeout=120,
            retry_policy=RetryPolicy(max_retries=0),
        )

    def execute(self, state: WorkflowState, logger: logging.Logger) -> WorkflowState:
        from ..docker_utils import container_running

        if not state.container_name:
            raise RuntimeError("No container name after seg1")

        if not container_running(state.container_name):
            raise RuntimeError(f"Container '{state.container_name}' is not running after seg1")

        # Sync context one more time
        ctx_path = sync_context(state.container_name, state.model, state.workspace_base)
        if not ctx_path:
            raise RuntimeError("Failed to sync context.yaml after seg1")

        logger.info(
            f"Seg1 validated: container={state.container_name}",
            extra={"step": self.name, "run_id": state.run_id},
        )
        return state
