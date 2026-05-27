"""Retry policy for workflow steps."""

from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass
class RetryPolicy:
    max_retries: int = 0
    backoff_base: float = 2.0
    backoff_max: float = 60.0
    retryable_errors: list[str] = field(default_factory=list)

    def should_retry(self, attempt: int, error: str) -> bool:
        if attempt >= self.max_retries:
            return False
        if not self.retryable_errors:
            return True
        return any(pat in error for pat in self.retryable_errors)

    def delay(self, attempt: int) -> float:
        d = min(self.backoff_base ** attempt, self.backoff_max)
        return d

    def wait(self, attempt: int) -> None:
        time.sleep(self.delay(attempt))
