"""RuntimeContext — facade injected into nodes for all runtime operations."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from .command_runtime import CommandRequest, CommandResult, CommandRuntime, ExecutionTarget
from .journal import SideEffectJournal
from .sandbox import SandboxBoundary, SandboxEnforcer

if TYPE_CHECKING:
    from ..artifacts import ArtifactRegistry, ArtifactType


class RuntimeContext:
    """Injected into each node — provides isolated access to runtime services."""

    def __init__(
        self,
        runtime: CommandRuntime,
        journal: SideEffectJournal,
        artifact_registry: "ArtifactRegistry",
        sandbox_enforcer: SandboxEnforcer | None = None,
        node_id: str = "",
        run_id: str = "",
        container_name: str | None = None,
        dry_run_mode: bool = False,
    ):
        self._runtime = runtime
        self._journal = journal
        self._artifact_registry = artifact_registry
        self._sandbox_enforcer = sandbox_enforcer
        self._node_id = node_id
        self._run_id = run_id
        self._container_name = container_name
        self._dry_run_mode = dry_run_mode

    def execute(
        self,
        command: str | list[str],
        target: ExecutionTarget = ExecutionTarget.HOST,
        timeout: float = 60,
        container_name: str | None = None,
        working_dir: str | None = None,
        env: dict[str, str] | None = None,
        rollback_command: str | None = None,
        description: str = "",
        idempotent: bool = False,
    ) -> CommandResult:
        request = CommandRequest(
            command=command,
            target=target,
            container_name=container_name or self._container_name,
            working_dir=working_dir,
            timeout=timeout,
            env=env or {},
            rollback_command=rollback_command,
            description=description,
            idempotent=idempotent,
        )

        if self._dry_run_mode:
            return self._runtime.dry_run(request, self._node_id, self._run_id)
        return self._runtime.execute(request, self._node_id, self._run_id)

    def execute_in_container(
        self,
        command: str,
        timeout: float = 60,
        container_name: str | None = None,
        rollback_command: str | None = None,
        description: str = "",
    ) -> CommandResult:
        return self.execute(
            command=command,
            target=ExecutionTarget.CONTAINER,
            timeout=timeout,
            container_name=container_name,
            rollback_command=rollback_command,
            description=description,
        )

    def execute_on_host(
        self,
        command: str | list[str],
        timeout: float = 60,
        working_dir: str | None = None,
        env: dict[str, str] | None = None,
        rollback_command: str | None = None,
        description: str = "",
    ) -> CommandResult:
        return self.execute(
            command=command,
            target=ExecutionTarget.HOST,
            timeout=timeout,
            working_dir=working_dir,
            env=env,
            rollback_command=rollback_command,
            description=description,
        )

    def produce_artifact(
        self,
        content: bytes | str,
        artifact_type: "ArtifactType",
        filename: str,
        metadata: dict | None = None,
    ):
        from ..artifacts import Artifact, ArtifactRef

        if isinstance(content, str):
            content = content.encode("utf-8")

        checksum = hashlib.sha256(content).hexdigest()
        artifact_id = f"{self._node_id}_{checksum[:12]}"

        storage_path = self._artifact_registry.store(artifact_id, content, filename)

        artifact = Artifact(
            id=artifact_id,
            type=artifact_type,
            producer_node=self._node_id,
            run_id=self._run_id,
            path=str(storage_path),
            checksum=checksum,
            size_bytes=len(content),
            created_at=datetime.now(timezone.utc),
            metadata=metadata or {},
        )

        return self._artifact_registry.register(artifact)

    def register_external_artifact(
        self,
        path: str,
        artifact_type: "ArtifactType",
        metadata: dict | None = None,
    ):
        from ..artifacts import Artifact, ArtifactRef

        file_path = Path(path)
        if file_path.exists():
            content = file_path.read_bytes()
            checksum = hashlib.sha256(content).hexdigest()
            size = len(content)
        else:
            checksum = "external"
            size = 0

        artifact_id = f"{self._node_id}_{checksum[:12]}"
        artifact = Artifact(
            id=artifact_id,
            type=artifact_type,
            producer_node=self._node_id,
            run_id=self._run_id,
            path=path,
            checksum=checksum,
            size_bytes=size,
            created_at=datetime.now(timezone.utc),
            metadata=metadata or {},
        )

        return self._artifact_registry.register(artifact)

    @property
    def journal(self) -> SideEffectJournal:
        return self._journal

    @property
    def node_id(self) -> str:
        return self._node_id

    @property
    def run_id(self) -> str:
        return self._run_id
