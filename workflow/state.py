"""Workflow state management using Pydantic models."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field

from .artifacts.artifact import ArtifactRef, ArtifactType
from .execution_status import NodeExecutionStatus


class StepStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"


class StepRecord(BaseModel):
    step_id: str
    status: StepStatus = StepStatus.PENDING
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    duration_seconds: Optional[float] = None
    retry_count: int = 0
    error: Optional[str] = None
    claude_exit_code: Optional[int] = None

    def mark_running(self) -> None:
        self.status = StepStatus.RUNNING
        self.started_at = datetime.now(timezone.utc)

    def mark_success(self) -> None:
        self.status = StepStatus.SUCCESS
        self.finished_at = datetime.now(timezone.utc)
        if self.started_at:
            self.duration_seconds = (self.finished_at - self.started_at).total_seconds()

    def mark_failed(self, error: str) -> None:
        self.status = StepStatus.FAILED
        self.finished_at = datetime.now(timezone.utc)
        self.error = error
        if self.started_at:
            self.duration_seconds = (self.finished_at - self.started_at).total_seconds()


class NodeRecord(BaseModel):
    """Fine-grained node execution record."""
    node_id: str
    status: NodeExecutionStatus = NodeExecutionStatus.PENDING
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    duration_seconds: Optional[float] = None
    retry_count: int = 0
    error: Optional[str] = None
    output_snapshot: Optional[dict] = None

    def mark_running(self) -> None:
        self.status = NodeExecutionStatus.RUNNING
        self.started_at = datetime.now(timezone.utc)

    def mark_success(self, output: Optional[dict] = None) -> None:
        self.status = NodeExecutionStatus.SUCCESS
        self.finished_at = datetime.now(timezone.utc)
        self.output_snapshot = output
        if self.started_at:
            self.duration_seconds = (self.finished_at - self.started_at).total_seconds()

    def mark_failed(self, error: str) -> None:
        self.status = NodeExecutionStatus.FAILED
        self.finished_at = datetime.now(timezone.utc)
        self.error = error
        if self.started_at:
            self.duration_seconds = (self.finished_at - self.started_at).total_seconds()

    def mark_retryable(self) -> None:
        self.status = NodeExecutionStatus.RETRYABLE

    def mark_terminated(self, reason: str) -> None:
        self.status = NodeExecutionStatus.TERMINATED
        self.finished_at = datetime.now(timezone.utc)
        self.error = reason

    def mark_rolled_back(self) -> None:
        self.status = NodeExecutionStatus.ROLLED_BACK
        self.finished_at = datetime.now(timezone.utc)


class WorkflowState(BaseModel):
    run_id: str
    target: str
    model: str
    model_path: Optional[str] = None
    image_mode: bool = False
    container_name: Optional[str] = None
    model_safe: str = ""

    native_inference_ok: bool = False
    workflow_terminated: bool = False
    termination_reason: Optional[str] = None

    steps: list[StepRecord] = Field(default_factory=list)

    # Node-level records (fine-grained mode)
    node_records: list[NodeRecord] = Field(default_factory=list)
    node_data: dict = Field(default_factory=dict)

    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    current_step: Optional[str] = None
    current_node: Optional[str] = None

    harbor_user: str = ""
    harbor_password: str = ""
    verbose: bool = False
    workspace_base: str = ""
    log_dir: str = ""
    project_root: str = ""

    # Artifact references (replaces raw path strings over time)
    artifact_refs: list[ArtifactRef] = Field(default_factory=list)

    def get_step(self, step_id: str) -> Optional[StepRecord]:
        for s in self.steps:
            if s.step_id == step_id:
                return s
        return None

    def init_steps(self, step_ids: list[str]) -> None:
        self.steps = [StepRecord(step_id=sid) for sid in step_ids]

    def get_node(self, node_id: str) -> Optional[NodeRecord]:
        for n in self.node_records:
            if n.node_id == node_id:
                return n
        return None

    def init_nodes(self, node_ids: list[str]) -> None:
        self.node_records = [NodeRecord(node_id=nid) for nid in node_ids]

    def set_node_data(self, key: str, value) -> None:
        """Store data for inter-node communication (persisted in checkpoints)."""
        self.node_data[key] = value

    def get_node_data(self, key: str, default=None):
        """Retrieve inter-node data."""
        return self.node_data.get(key, default)

    def to_file(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.model_dump_json(indent=2), encoding="utf-8")

    @classmethod
    def from_file(cls, path: Path) -> WorkflowState:
        return cls.model_validate_json(path.read_text(encoding="utf-8"))

    # --- Artifact management ---

    def add_artifact(self, ref: ArtifactRef) -> None:
        self.artifact_refs.append(ref)

    def get_artifacts(self, node_id: str | None = None) -> list[ArtifactRef]:
        if node_id:
            return [r for r in self.artifact_refs if r.producer_node == node_id]
        return list(self.artifact_refs)

    def get_artifact_by_type(self, atype: ArtifactType) -> ArtifactRef | None:
        for ref in reversed(self.artifact_refs):
            if ref.type == atype:
                return ref
        return None

