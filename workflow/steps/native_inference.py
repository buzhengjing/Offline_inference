"""Seg2: Native inference verification + release (Claude call)."""

from __future__ import annotations

import logging
from pathlib import Path

from ..claude_executor import ClaudeExecutor
from ..docker_utils import check_native_inference_ok, sync_context
from ..retry import RetryPolicy
from ..state import WorkflowState
from ..step import WorkflowStep


class Seg2NativeInferenceStep(WorkflowStep):
    def __init__(self):
        super().__init__(
            name="seg2_native_inference",
            timeout=3600,  # 60 min
            retry_policy=RetryPolicy(max_retries=1, retryable_errors=["claude exit code"]),
        )

    def execute(self, state: WorkflowState, logger: logging.Logger) -> WorkflowState:
        project_root = Path(state.project_root)
        log_dir = Path(state.log_dir) / "steps"
        log_dir.mkdir(parents=True, exist_ok=True)

        prompt = self._build_prompt(state)

        executor = ClaudeExecutor(project_root)
        exit_code = executor.run(
            prompt=prompt,
            max_turns=100,
            timeout_seconds=self.timeout - 60,
            log_path=log_dir / "seg2_native_inference.log",
            jsonl_path=log_dir / "seg2_native_inference_claude.jsonl",
            env_extra={
                "HARBOR_USER": state.harbor_user,
                "HARBOR_PASSWORD": state.harbor_password,
            },
            verbose=state.verbose,
        )

        step_record = state.get_step(self.name)
        if step_record:
            step_record.claude_exit_code = exit_code

        if exit_code != 0:
            raise RuntimeError(f"claude exit code {exit_code}")

        # Sync context and check result
        sync_context(state.container_name, state.model, state.workspace_base)

        return state

    def _build_prompt(self, state: WorkflowState) -> str:
        model_safe = state.model_safe
        return f"""容器: {state.container_name}，模型名: {state.model}，模型路径: {state.model_path}，宿主机工作目录: /data/Offline_inference_workspace/{model_safe}

请执行离线推理验证流水线的段2（步骤3-4）：

**步骤3 — 原生推理验证**
阅读 skills/flagos-inference-verify/SKILL.md，按其流程执行。
模式: native（原生推理验证）
- 从 context.yaml 读取环境探索结果
- 查询官方测试数据
- 编写推理脚本 /root/run_inference.py
- 执行推理验证
- 编写 /root/README.md
- 失败时排查修复（最多3次），仍失败则记录原因并设置 workflow.terminated=true

**步骤4 — 原生镜像发布**
阅读 skills/flagos-release/SKILL.md，按其流程执行。
- 使用 skills/flagos-release/tools/main.py --from-context
- 发布原生推理验证通过的镜像

**规则**：
- 每步开始前输出 [步骤X] <名称> — 开始
- 每步完成后输出 [步骤X] <名称> — 完成
- 通过 docker exec 操作容器内文件
- 使用 update_context.py 更新 context.yaml
- Harbor 凭证已通过环境变量 HARBOR_USER / HARBOR_PASSWORD 提供
"""


class Seg2ValidateStep(WorkflowStep):
    def __init__(self):
        super().__init__(
            name="seg2_validate",
            timeout=120,
            retry_policy=RetryPolicy(max_retries=0),
        )

    def execute(self, state: WorkflowState, logger: logging.Logger) -> WorkflowState:
        ctx_file = Path(state.workspace_base) / "config" / "context_snapshot.yaml"
        sync_context(state.container_name, state.model, state.workspace_base)

        status = check_native_inference_ok(state.container_name, ctx_file)

        if status == "TERMINATED":
            state.workflow_terminated = True
            state.termination_reason = "Native inference failed and cannot be fixed"
            return state
        elif status == "OK":
            state.native_inference_ok = True
            logger.info(
                "Native inference verified OK",
                extra={"step": self.name, "run_id": state.run_id},
            )
        else:
            state.workflow_terminated = True
            state.termination_reason = f"Native inference status: {status}"

        return state
