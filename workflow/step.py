"""WorkflowStep abstract base class."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from .retry import RetryPolicy

if TYPE_CHECKING:
    from logging import Logger
    from .state import WorkflowState


class WorkflowStep(ABC):
    name: str
    timeout: float  # seconds
    retry_policy: RetryPolicy

    def __init__(self, name: str, timeout: float, retry_policy: RetryPolicy | None = None):
        self.name = name
        self.timeout = timeout
        self.retry_policy = retry_policy or RetryPolicy()

    @abstractmethod
    def execute(self, state: WorkflowState, logger: Logger) -> WorkflowState:
        """Execute the step. Mutate and return state."""
        ...

    def should_skip(self, state: WorkflowState) -> bool:
        """Return True to skip this step based on current state."""
        return False
