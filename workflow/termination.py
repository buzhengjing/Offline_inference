"""Workflow termination policy."""

from __future__ import annotations

from dataclasses import dataclass, field

from .failure_policy import FailureCategory


class ErrorSeverity:
    FATAL = "fatal"
    RETRYABLE = "retryable"
    IGNORABLE = "ignorable"
    PARTIAL = "partial"


@dataclass
class TerminationPolicy:
    """Governs when the workflow should halt vs continue."""

    max_consecutive_failures: int = 3
    max_total_failures: int = 5

    fatal_categories: set[FailureCategory] = field(default_factory=lambda: {
        FailureCategory.MODEL_CORRUPTION,
    })

    ignorable_nodes: set[str] = field(default_factory=lambda: {
        "diagnose_failure",
        "apply_fix",
        "diagnose_flaggems_failure",
        "apply_flaggems_fix",
    })

    partial_success_segments: set[str] = field(default_factory=lambda: {
        "seg3",
    })

    def classify_severity(
        self, node_id: str, category: FailureCategory
    ) -> str:
        if category in self.fatal_categories:
            return ErrorSeverity.FATAL
        if node_id in self.ignorable_nodes:
            return ErrorSeverity.IGNORABLE
        if any(node_id.startswith(seg) or seg in node_id for seg in self.partial_success_segments):
            return ErrorSeverity.PARTIAL
        return ErrorSeverity.RETRYABLE

    def should_terminate(
        self,
        consecutive_failures: int,
        total_failures: int,
        severity: str,
    ) -> bool:
        if severity == ErrorSeverity.FATAL:
            return True
        if consecutive_failures >= self.max_consecutive_failures:
            return True
        if total_failures >= self.max_total_failures:
            return True
        return False
