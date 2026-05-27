"""Failure classification and routing for the state machine."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class FailureCategory(str, Enum):
    OOM = "oom"
    MISSING_DEPENDENCY = "missing_dependency"
    MODEL_CORRUPTION = "model_corruption"
    TIMEOUT = "timeout"
    RUNTIME_ERROR = "runtime_error"
    NETWORK = "network"
    PERMISSION = "permission"
    UNKNOWN = "unknown"


_CATEGORY_PATTERNS: list[tuple[FailureCategory, list[str]]] = [
    (FailureCategory.OOM, [
        "CUDA out of memory", "OutOfMemoryError", "OOM", "torch.cuda.OutOfMemoryError",
        "RuntimeError: CUDA error: out of memory",
    ]),
    (FailureCategory.MISSING_DEPENDENCY, [
        "ModuleNotFoundError", "ImportError", "No module named",
        "command not found", "Package .* is not installed",
    ]),
    (FailureCategory.MODEL_CORRUPTION, [
        "safetensors_rust.SafetensorError", "Corrupted", "checksum mismatch",
        "Invalid model file", "unexpected key",
    ]),
    (FailureCategory.TIMEOUT, [
        "TimeoutError", "timed out", "deadline exceeded",
    ]),
    (FailureCategory.NETWORK, [
        "ConnectionError", "ConnectionRefusedError", "HTTPError",
        "Network is unreachable", "Name or service not known",
    ]),
    (FailureCategory.PERMISSION, [
        "PermissionError", "Permission denied", "Access denied",
    ]),
]


def classify_error(error: str, exit_code: int = -1) -> FailureCategory:
    """Classify an error string into a FailureCategory."""
    for category, patterns in _CATEGORY_PATTERNS:
        for pattern in patterns:
            if re.search(pattern, error, re.IGNORECASE):
                return category
    if exit_code == 137:
        return FailureCategory.OOM
    return FailureCategory.UNKNOWN


@dataclass
class FailureRoute:
    """Maps a failure category to a target node and action."""

    category: FailureCategory
    target_node: str
    action: str  # "retry_with_fix" | "install_dep" | "reduce_batch" | "terminate"
    max_attempts: int = 1


TERMINATE_SENTINEL = "__terminate__"


@dataclass
class FailureRouter:
    """Routes failures to appropriate recovery nodes."""

    routes: dict[str, list[FailureRoute]] = field(default_factory=dict)

    def add_routes(self, node_id: str, failure_routes: list[FailureRoute]) -> None:
        self.routes[node_id] = failure_routes

    def route(self, node_id: str, error: str, exit_code: int = -1) -> Optional[FailureRoute]:
        """Given a failed node and error, return the matching FailureRoute or None."""
        node_routes = self.routes.get(node_id)
        if not node_routes:
            return None
        category = classify_error(error, exit_code)
        for fr in node_routes:
            if fr.category == category:
                return fr
        return None
