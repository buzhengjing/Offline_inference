"""Deterministic Node base class — wraps shell/docker commands with runtime isolation."""

from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING

from .docker_utils import docker_exec
from .node import BaseNode, NodeResult, NodeType
from .retry import RetryPolicy
from .runtime.command_runtime import CommandResult, ExecutionTarget
from .runtime.sandbox import SandboxBoundary

if TYPE_CHECKING:
    from logging import Logger

    from .runtime.context import RuntimeContext
    from .state import WorkflowState


class DeterministicNode(BaseNode):
    """Base for nodes that execute deterministic logic (docker exec, file ops, etc.)."""

    sandbox: SandboxBoundary | None = None

    def __init__(
        self,
        node_id: str,
        timeout: float = 300,
        retry_policy: RetryPolicy | None = None,
        has_side_effects: bool = False,
        sandbox: SandboxBoundary | None = None,
    ):
        super().__init__(
            node_id=node_id,
            node_type=NodeType.DETERMINISTIC,
            timeout=timeout,
            retry_policy=retry_policy,
            has_side_effects=has_side_effects,
        )
        self.sandbox = sandbox
        self._ctx: RuntimeContext | None = None

    def run_in_container(
        self,
        state: WorkflowState,
        cmd: str,
        timeout: float = 60,
        rollback_command: str | None = None,
        description: str = "",
    ) -> subprocess.CompletedProcess | CommandResult:
        if self._ctx:
            return self._ctx.execute_in_container(
                command=cmd,
                timeout=timeout,
                container_name=state.container_name,
                rollback_command=rollback_command,
                description=description,
            )
        return docker_exec(state.container_name, cmd, timeout=timeout)

    def run_on_host(
        self,
        cmd: list[str] | str,
        timeout: float = 60,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        rollback_command: str | None = None,
        description: str = "",
    ) -> subprocess.CompletedProcess | CommandResult:
        if self._ctx:
            return self._ctx.execute_on_host(
                command=cmd,
                timeout=timeout,
                working_dir=cwd,
                env=env,
                rollback_command=rollback_command,
                description=description,
            )
        if isinstance(cmd, str):
            cmd = ["bash", "-c", cmd]
        return subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=cwd,
        )

    def set_runtime_context(self, ctx: RuntimeContext) -> None:
        self._ctx = ctx
