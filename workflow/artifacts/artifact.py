"""Artifact schema — typed intermediate products of workflow execution."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class ArtifactType(str, Enum):
    SCRIPT = "script"
    LOG = "log"
    MODEL_OUTPUT = "model_output"
    CONFIG = "config"
    MANIFEST = "manifest"
    README = "readme"
    REPORT = "report"
    CONTEXT = "context"
    IMAGE_REF = "image_ref"
    CHECKPOINT = "checkpoint"


class Artifact(BaseModel):
    """A typed, content-addressable intermediate product."""

    id: str
    type: ArtifactType
    producer_node: str
    run_id: str
    path: str
    checksum: str
    size_bytes: int = 0
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: dict = Field(default_factory=dict)
    version: int = 1
    parent_id: str | None = None


class ArtifactRef(BaseModel):
    """Lightweight reference stored in WorkflowState."""

    artifact_id: str
    type: ArtifactType
    producer_node: str
