"""Base Node class for fine-grained workflow execution."""

from __future__ import annotations

from abc import ABC, abstractmethod
from enum import Enum
from typing import TYPE_CHECKING, Any, Optional

from pydantic import BaseModel

from .retry import RetryPolicy

if TYPE_CHECKING:
    from logging import Logger

    from .observability import NodeTrace, TraceRecorder
    from .runtime.context import RuntimeContext
    from .state import WorkflowState


class NodeType(str, Enum):
    DETERMINISTIC = "deterministic"
    LLM = "llm"


class NodeResult(BaseModel):
    """Standard result wrapper for all nodes."""
    success: bool
    data: dict = {}
    error: Optional[str] = None


class BaseNode(ABC):
    """Base class for all workflow nodes."""

    node_id: str
    node_type: NodeType
    timeout: float
    retry_policy: RetryPolicy
    has_side_effects: bool = False
    idempotent: bool = False
    side_effect_boundary: bool = False

    def __init__(
        self,
        node_id: str,
        node_type: NodeType,
        timeout: float = 300,
        retry_policy: RetryPolicy | None = None,
        has_side_effects: bool = False,
        idempotent: bool = False,
        side_effect_boundary: bool = False,
    ):
        self.node_id = node_id
        self.node_type = node_type
        self.timeout = timeout
        self.retry_policy = retry_policy or RetryPolicy()
        self.has_side_effects = has_side_effects
        self.idempotent = idempotent
        self.side_effect_boundary = side_effect_boundary

    @abstractmethod
    def execute(self, state: WorkflowState, logger: Logger, ctx: RuntimeContext | None = None) -> NodeResult:
        """Execute the node logic. Return a NodeResult."""
        ...

    def should_skip(self, state: WorkflowState) -> bool:
        """Return True to skip this node based on current state."""
        return False

    def get_input_snapshot(self, state: WorkflowState) -> dict:
        """Extract relevant input data from state for tracing."""
        return {}

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__} id={self.node_id} type={self.node_type.value}>"
