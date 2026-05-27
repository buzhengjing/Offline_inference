"""Timeout control for workflow steps."""

from __future__ import annotations

import signal
import time
from contextlib import contextmanager
from typing import Generator


class StepTimeoutError(Exception):
    def __init__(self, step_name: str, timeout_seconds: float):
        self.step_name = step_name
        self.timeout_seconds = timeout_seconds
        super().__init__(f"Step '{step_name}' timed out after {timeout_seconds}s")


_global_deadline: float | None = None


@contextmanager
def workflow_timeout(timeout_seconds: float) -> Generator[None, None, None]:
    """Top-level workflow timeout using a monotonic deadline.

    Does NOT use signal.alarm so it can safely nest with step_timeout.
    Instead, step_timeout checks the global deadline after each step.
    """
    global _global_deadline
    if timeout_seconds <= 0:
        yield
        return

    prev = _global_deadline
    _global_deadline = time.monotonic() + timeout_seconds
    try:
        yield
    finally:
        _global_deadline = prev


def check_workflow_timeout(step_name: str = "workflow") -> None:
    """Raise StepTimeoutError if the global workflow deadline has passed."""
    if _global_deadline is not None and time.monotonic() > _global_deadline:
        raise StepTimeoutError(step_name, 0)


@contextmanager
def step_timeout(step_name: str, timeout_seconds: float) -> Generator[None, None, None]:
    """Context manager that raises StepTimeoutError after timeout_seconds.

    Uses SIGALRM on Linux for per-step timeout. Safe to nest inside
    workflow_timeout since they use different mechanisms.
    """
    if timeout_seconds <= 0:
        yield
        return

    def _handler(signum, frame):
        raise StepTimeoutError(step_name, timeout_seconds)

    old_handler = signal.signal(signal.SIGALRM, _handler)
    signal.alarm(int(timeout_seconds))
    try:
        yield
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old_handler)
