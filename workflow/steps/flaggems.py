"""Seg3: FlagGems install + inference verify + release."""

from __future__ import annotations

import json
import logging
import re
import shlex
from pathlib import Path

import yaml

from ..claude_executor import ClaudeExecutor
from ..docker_utils import sync_context
from ..retry import RetryPolicy
from ..state import WorkflowState
from ..step import WorkflowStep


class Seg3InstallStep(WorkflowStep):
    """Step 5: Install FlagGems + FlagTree."""

    def __init__(self):
        super().__init__(
            name="seg3_install",
            timeout=2400,  # 40 min — installs can be slow
            retry_policy=RetryPolicy(max_retries=1, retryable_errors=["claude exit code"]),
        )

    def should_skip(self, state: WorkflowState) -> bool:
        return not state.native_inference_ok

    def execute(self, state: WorkflowState, logger: logging.Logger) -> WorkflowState:
        project_root = Path(state.project_root)
        log_dir = Path(state.log_dir) / "steps"
        log_dir.mkdir(parents=True, exist_ok=True)

        model_safe = state.model_safe
        prompt = f"""容器: {state.container_name}，模型名: {state.model}，模型路径: {state.model_path}，宿主机工作目录: /data/Offline_inference_workspace/{model_safe}

请执行离线推理验证流水线的步骤5：

**步骤5 — FlagGems/FlagTree 安装**
阅读 skills/flagos-component-install/SKILL.md，按其流程执行。
- 安装 FlagGems 和 FlagTree
- 容器内已配置 pip 镜像源（/root/.config/pip/pip.conf），pip install 会自动使用
- 如果 pip install 仍然慢，可加 --pip-index https://mirrors.aliyun.com/pypi/simple/ 参数
- 安装完成后通过 update_context.py 设置 workflow.flaggems_installed=true
- 验证 flag_gems 和 triton 均可正常 import

**规则**：
- 输出 [步骤5] FlagGems/FlagTree 安装 — 开始
- 完成后输出 [步骤5] FlagGems/FlagTree 安装 — 完成
- 通过 docker exec 操作容器内文件
- 使用 update_context.py 更新 context.yaml
"""

        executor = ClaudeExecutor(project_root)
        exit_code = executor.run(
            prompt=prompt,
            max_turns=80,
            timeout_seconds=self.timeout - 60,
            log_path=log_dir / "seg3_install.log",
            jsonl_path=log_dir / "seg3_install_claude.jsonl",
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

        sync_context(state.container_name, state.model, state.workspace_base)
        return state


class Seg3InstallValidateStep(WorkflowStep):
    """Validate that FlagGems/FlagTree are actually installed."""

    def __init__(self):
        super().__init__(
            name="seg3_install_validate",
            timeout=60,
            retry_policy=RetryPolicy(max_retries=0),
        )

    def should_skip(self, state: WorkflowState) -> bool:
        return not state.native_inference_ok

    def execute(self, state: WorkflowState, logger: logging.Logger) -> WorkflowState:
        from ..docker_utils import docker_exec

        sync_context(state.container_name, state.model, state.workspace_base)

        result = docker_exec(
            state.container_name,
            "python3 -c \"import flag_gems; import triton; print('OK')\"",
            timeout=30,
        )
        if result.returncode != 0 or "OK" not in (result.stdout or ""):
            raise RuntimeError(
                f"FlagGems/FlagTree import check failed: {result.stderr or result.stdout}"
            )

        logger.info("FlagGems/FlagTree install validated", extra={"step": self.name, "run_id": state.run_id})
        return state


class Seg3InferenceStep(WorkflowStep):
    """Step 6: FlagGems inference verification."""

    def __init__(self):
        super().__init__(
            name="seg3_inference",
            timeout=1800,  # 30 min
            retry_policy=RetryPolicy(max_retries=1, retryable_errors=["claude exit code"]),
        )

    def should_skip(self, state: WorkflowState) -> bool:
        return not state.native_inference_ok

    def execute(self, state: WorkflowState, logger: logging.Logger) -> WorkflowState:
        project_root = Path(state.project_root)
        log_dir = Path(state.log_dir) / "steps"
        log_dir.mkdir(parents=True, exist_ok=True)

        model_safe = state.model_safe
        prompt = f"""容器: {state.container_name}，模型名: {state.model}，模型路径: {state.model_path}，宿主机工作目录: /data/Offline_inference_workspace/{model_safe}

请执行离线推理验证流水线的步骤6：

**步骤6 — FlagGems 推理验证**
阅读 skills/flagos-inference-verify/SKILL.md，按其流程执行。
模式: flaggems（FlagGems 推理验证）
- 复用步骤3的推理脚本（/root/run_inference.py 已含 flag_gems try/except）
- 验证 FlagGems 推理
- 对比原生推理结果，确认数值等价性（max_abs_diff < 0.01）
- 更新 /root/README.md 追加 FlagGems 验证结果
- 通过 update_context.py 设置 workflow.flaggems_inference_ok=true

**规则**：
- 输出 [步骤6] FlagGems 推理验证 — 开始
- 完成后输出 [步骤6] FlagGems 推理验证 — 完成
- 通过 docker exec 操作容器内文件
- 使用 update_context.py 更新 context.yaml
"""

        executor = ClaudeExecutor(project_root)
        exit_code = executor.run(
            prompt=prompt,
            max_turns=60,
            timeout_seconds=self.timeout - 60,
            log_path=log_dir / "seg3_inference.log",
            jsonl_path=log_dir / "seg3_inference_claude.jsonl",
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

        sync_context(state.container_name, state.model, state.workspace_base)
        return state


class Seg3InferenceValidateStep(WorkflowStep):
    """Validate FlagGems inference actually passed."""

    def __init__(self):
        super().__init__(
            name="seg3_inference_validate",
            timeout=60,
            retry_policy=RetryPolicy(max_retries=0),
        )

    def should_skip(self, state: WorkflowState) -> bool:
        return not state.native_inference_ok

    def execute(self, state: WorkflowState, logger: logging.Logger) -> WorkflowState:
        sync_context(state.container_name, state.model, state.workspace_base)

        ctx_path = Path(state.workspace_base) / "config" / "context_snapshot.yaml"
        if not ctx_path.exists():
            raise RuntimeError("context_snapshot.yaml not found after seg3 inference")

        ctx = yaml.safe_load(ctx_path.read_text()) or {}
        workflow = ctx.get("workflow", {})
        flaggems_inf = ctx.get("flaggems_inference", {})

        if not workflow.get("flaggems_inference_ok") and not flaggems_inf.get("success"):
            raise RuntimeError(
                "FlagGems inference not verified: workflow.flaggems_inference_ok is not set"
            )

        logger.info("FlagGems inference validated", extra={"step": self.name, "run_id": state.run_id})
        return state


class Seg3ReleaseStep(WorkflowStep):
    """Step 7: FlagGems image release (deterministic, no Claude needed)."""

    def __init__(self):
        super().__init__(
            name="seg3_release",
            timeout=900,  # 15 min for commit+push
            retry_policy=RetryPolicy(max_retries=1, retryable_errors=["release failed"]),
        )

    def should_skip(self, state: WorkflowState) -> bool:
        return not state.native_inference_ok

    def execute(self, state: WorkflowState, logger: logging.Logger) -> WorkflowState:
        import os
        import subprocess

        project_root = Path(state.project_root)
        ctx_path = Path(state.workspace_base) / "config" / "context_snapshot.yaml"
        sync_context(state.container_name, state.model, state.workspace_base)

        cmd = [
            "python3", str(project_root / "skills" / "flagos-release" / "tools" / "main.py"),
            "--from-context", str(ctx_path),
        ]

        env = os.environ.copy()
        env["HARBOR_USER"] = state.harbor_user
        env["HARBOR_PASSWORD"] = state.harbor_password

        logger.info("Starting FlagGems image release", extra={"step": self.name, "run_id": state.run_id})

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=self.timeout - 60,
            env=env,
            cwd=str(project_root),
        )

        if result.returncode != 0:
            raise RuntimeError(f"FlagGems release failed: {result.stderr or result.stdout}")

        match = re.search(r'\[RELEASE_SUMMARY\](.*?)\[/RELEASE_SUMMARY\]', result.stdout, re.DOTALL)
        if match:
            try:
                summary = json.loads(match.group(1))
                harbor_image = summary.get("harbor_image", "")
                if harbor_image:
                    from ..docker_utils import docker_exec
                    update_cmd = (
                        "python3 /Offline_inference_workspace/scripts/update_context.py "
                        f"--set release.flaggems.image_url={shlex.quote(harbor_image)} "
                        "--set workflow.flaggems_released=true"
                    )
                    docker_exec(state.container_name, update_cmd, timeout=30)
            except (json.JSONDecodeError, ValueError) as e:
                logger.warning(f"Failed to parse RELEASE_SUMMARY: {e}",
                               extra={"step": self.name, "run_id": state.run_id})

        sync_context(state.container_name, state.model, state.workspace_base)
        logger.info("FlagGems image released", extra={"step": self.name, "run_id": state.run_id})
        return state


class Seg3ValidateStep(WorkflowStep):
    """Final validation: confirm FlagGems image was actually pushed."""

    def __init__(self):
        super().__init__(
            name="seg3_validate",
            timeout=120,
            retry_policy=RetryPolicy(max_retries=0),
        )

    def should_skip(self, state: WorkflowState) -> bool:
        return not state.native_inference_ok

    def execute(self, state: WorkflowState, logger: logging.Logger) -> WorkflowState:
        sync_context(state.container_name, state.model, state.workspace_base)

        ctx_path = Path(state.workspace_base) / "config" / "context_snapshot.yaml"
        if not ctx_path.exists():
            raise RuntimeError("context_snapshot.yaml not found")

        ctx = yaml.safe_load(ctx_path.read_text()) or {}

        errors = []

        flaggems_inf = ctx.get("flaggems_inference", {})
        if not flaggems_inf.get("success") and not ctx.get("workflow", {}).get("flaggems_inference_ok"):
            errors.append("FlagGems inference not verified")

        release = ctx.get("release", {}).get("flaggems", {})
        if not release.get("image_url"):
            errors.append("FlagGems image not released to Harbor")

        if errors:
            raise RuntimeError(f"Seg3 validation failed: {'; '.join(errors)}")

        logger.info(
            "Seg3 (FlagGems) fully completed and validated",
            extra={"step": self.name, "run_id": state.run_id},
        )
        return state

