"""Node execution status enum for the state machine workflow."""

from __future__ import annotations

from enum import Enum


class NodeExecutionStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    RETRYABLE = "retryable"
    SKIPPED = "skipped"
    TERMINATED = "terminated"
    ROLLED_BACK = "rolled_back"
