"""Sandbox boundary enforcement for workflow nodes."""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field

from .command_runtime import CommandRequest, ExecutionTarget


class SandboxMode(str, Enum):
    WARN = "warn"
    STRICT = "strict"


class SandboxBoundary(BaseModel):
    """Declares the execution boundary for a node."""

    target: ExecutionTarget
    container_name: str | None = None
    allowed_write_paths: list[str] = Field(default_factory=list)
    read_only_paths: list[str] = Field(default_factory=list)
    network_access: bool = True
    allow_host_fallback: bool = False


class SandboxViolation(RuntimeError):
    """Raised when a command violates its declared sandbox boundary."""

    def __init__(self, node_id: str, violation: str, request: CommandRequest):
        self.node_id = node_id
        self.violation = violation
        self.request = request
        super().__init__(f"[{node_id}] Sandbox violation: {violation}")


class SandboxEnforcer:
    """Validates that commands respect their node's declared boundary."""

    def __init__(self, boundaries: dict[str, SandboxBoundary] | None = None, mode: SandboxMode = SandboxMode.WARN):
        self._boundaries: dict[str, SandboxBoundary] = boundaries or {}
        self._mode = mode
        self._violations: list[SandboxViolation] = []

    def register_boundary(self, node_id: str, boundary: SandboxBoundary) -> None:
        self._boundaries[node_id] = boundary

    def validate(self, request: CommandRequest, node_id: str) -> None:
        boundary = self._boundaries.get(node_id)
        if not boundary:
            return

        violations = []

        if request.target != boundary.target and not boundary.allow_host_fallback:
            violations.append(
                f"Target mismatch: request={request.target.value}, declared={boundary.target.value}"
            )

        if request.target == ExecutionTarget.CONTAINER:
            if boundary.container_name and request.container_name != boundary.container_name:
                violations.append(
                    f"Container mismatch: request={request.container_name}, declared={boundary.container_name}"
                )

        for v in violations:
            violation = SandboxViolation(node_id, v, request)
            self._violations.append(violation)
            if self._mode == SandboxMode.STRICT:
                raise violation

    @property
    def violations(self) -> list[SandboxViolation]:
        return list(self._violations)

    def clear_violations(self) -> None:
        self._violations.clear()
