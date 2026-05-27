"""Condition edges and fallback edges for the state graph."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable, Optional

if TYPE_CHECKING:
    from .state import WorkflowState


@dataclass
class ConditionEdge:
    """An edge that is traversed only when condition(state) returns True."""

    from_node: str
    to_node: str
    condition: Callable[["WorkflowState"], bool]
    priority: int = 0
    label: str = ""

    def evaluate(self, state: "WorkflowState") -> bool:
        return self.condition(state)


@dataclass
class FallbackEdge:
    """Default edge taken when no ConditionEdge from the same source matches."""

    from_node: str
    to_node: str


@dataclass
class UnconditionalEdge:
    """Always-taken edge (equivalent to the old simple edge)."""

    from_node: str
    to_node: str
