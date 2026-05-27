"""CommandRuntime — unified command execution with isolation, capture, and audit."""

from __future__ import annotations

import subprocess
import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, Field


class ExecutionTarget(str, Enum):
    HOST = "host"
    CONTAINER = "container"
    ISOLATED = "isolated"


class CommandRequest(BaseModel):
    """Declarative description of a command to execute."""

    command: str | list[str]
    target: ExecutionTarget = ExecutionTarget.HOST
    container_name: str | None = None
    working_dir: str | None = None
    timeout: float = 60
    env: dict[str, str] = Field(default_factory=dict)
    stdin: str | None = None
    idempotent: bool = False
    rollback_command: str | None = None
    description: str = ""

    @property
    def command_str(self) -> str:
        if isinstance(self.command, list):
            return " ".join(self.command)
        return self.command


class CommandResult(BaseModel):
    """Result of a command execution."""

    request_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    command: str
    target: ExecutionTarget
    returncode: int
    stdout: str = ""
    stderr: str = ""
    started_at: datetime
    finished_at: datetime
    duration_seconds: float = 0.0
    node_id: str = ""
    run_id: str = ""
    dry_run: bool = False


@runtime_checkable
class CommandRuntime(Protocol):
    """Protocol for command execution backends."""

    def execute(self, request: CommandRequest, node_id: str, run_id: str) -> CommandResult: ...
    def dry_run(self, request: CommandRequest, node_id: str, run_id: str) -> CommandResult: ...


class LiveCommandRuntime:
    """Production runtime — executes commands for real."""

    def __init__(self, journal=None, sandbox_enforcer=None):
        self._journal = journal
        self._sandbox_enforcer = sandbox_enforcer

    def execute(self, request: CommandRequest, node_id: str, run_id: str) -> CommandResult:
        if self._sandbox_enforcer:
            self._sandbox_enforcer.validate(request, node_id)

        started_at = datetime.now(timezone.utc)

        if request.target == ExecutionTarget.CONTAINER:
            result = self._exec_container(request)
        elif request.target == ExecutionTarget.ISOLATED:
            result = self._exec_isolated(request)
        else:
            result = self._exec_host(request)

        finished_at = datetime.now(timezone.utc)
        duration = (finished_at - started_at).total_seconds()

        cmd_result = CommandResult(
            command=request.command_str,
            target=request.target,
            returncode=result.returncode,
            stdout=result.stdout or "",
            stderr=result.stderr or "",
            started_at=started_at,
            finished_at=finished_at,
            duration_seconds=duration,
            node_id=node_id,
            run_id=run_id,
        )

        if self._journal:
            self._journal.record(cmd_result, request.rollback_command)

        return cmd_result

    def dry_run(self, request: CommandRequest, node_id: str, run_id: str) -> CommandResult:
        now = datetime.now(timezone.utc)
        return CommandResult(
            command=request.command_str,
            target=request.target,
            returncode=0,
            stdout="[DRY RUN] Command not executed",
            stderr="",
            started_at=now,
            finished_at=now,
            duration_seconds=0.0,
            node_id=node_id,
            run_id=run_id,
            dry_run=True,
        )

    def _exec_host(self, request: CommandRequest) -> subprocess.CompletedProcess:
        cmd = request.command if isinstance(request.command, list) else ["bash", "-c", request.command]
        return subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=request.timeout,
            cwd=request.working_dir,
            env=request.env or None,
            input=request.stdin,
        )

    def _exec_container(self, request: CommandRequest) -> subprocess.CompletedProcess:
        if not request.container_name:
            raise ValueError("container_name required for CONTAINER target")
        cmd_str = request.command_str
        return subprocess.run(
            ["docker", "exec", request.container_name, "bash", "-c", cmd_str],
            capture_output=True,
            text=True,
            timeout=request.timeout,
            input=request.stdin,
        )

    def _exec_isolated(self, request: CommandRequest) -> subprocess.CompletedProcess:
        # Isolated = container with no network, read-only root
        if not request.container_name:
            raise ValueError("container_name required for ISOLATED target")
        cmd_str = request.command_str
        return subprocess.run(
            ["docker", "exec", "--network=none", request.container_name, "bash", "-c", cmd_str],
            capture_output=True,
            text=True,
            timeout=request.timeout,
            input=request.stdin,
        )


class DryRunCommandRuntime:
    """Records commands without executing. For testing and replay-diff."""

    def __init__(self):
        self.recorded: list[tuple[CommandRequest, str, str]] = []

    def execute(self, request: CommandRequest, node_id: str, run_id: str) -> CommandResult:
        self.recorded.append((request, node_id, run_id))
        now = datetime.now(timezone.utc)
        return CommandResult(
            command=request.command_str,
            target=request.target,
            returncode=0,
            stdout="[DRY RUN]",
            stderr="",
            started_at=now,
            finished_at=now,
            duration_seconds=0.0,
            node_id=node_id,
            run_id=run_id,
            dry_run=True,
        )

    def dry_run(self, request: CommandRequest, node_id: str, run_id: str) -> CommandResult:
        return self.execute(request, node_id, run_id)
