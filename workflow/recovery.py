"""Recovery semantics for the state machine workflow."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional


class RecoveryAction(str, Enum):
    RETRY_CURRENT = "retry_current"
    ROLLBACK_TO = "rollback_to"
    SKIP_AND_CONTINUE = "skip_and_continue"
    TERMINATE = "terminate"


@dataclass
class RecoveryPolicy:
    """Defines how to recover when a specific node fails."""

    node_id: str
    on_failure: RecoveryAction = RecoveryAction.TERMINATE
    rollback_target: Optional[str] = None
    max_retries: int = 2
    is_optional: bool = False

    def should_skip_on_failure(self) -> bool:
        return self.is_optional or self.on_failure == RecoveryAction.SKIP_AND_CONTINUE


@dataclass
class NodeSemantics:
    """Execution semantics metadata for a node."""

    idempotent: bool = False
    has_side_effects: bool = False
    compensating_node: Optional[str] = None
    rollback_node: Optional[str] = None
    side_effect_boundary: bool = False
