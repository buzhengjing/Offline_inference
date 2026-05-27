"""Structured logging for workflow execution."""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class JSONFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "step": getattr(record, "step", None),
            "run_id": getattr(record, "run_id", None),
            "event": record.getMessage(),
        }
        data = getattr(record, "data", None)
        if data:
            entry["data"] = data
        return json.dumps(entry, ensure_ascii=False)


class HumanFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
        step = getattr(record, "step", "-")
        return f"[{ts}] [{record.levelname:<5}] [{step}] {record.getMessage()}"


def setup_logging(log_dir: Path, run_id: str) -> logging.Logger:
    log_dir.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger(f"workflow.{run_id}")
    logger.setLevel(logging.DEBUG)
    logger.handlers.clear()

    json_handler = logging.FileHandler(log_dir / "workflow.log", encoding="utf-8")
    json_handler.setFormatter(JSONFormatter())
    json_handler.setLevel(logging.DEBUG)
    logger.addHandler(json_handler)

    human_handler = logging.FileHandler(log_dir / "workflow_human.log", encoding="utf-8")
    human_handler.setFormatter(HumanFormatter())
    human_handler.setLevel(logging.INFO)
    logger.addHandler(human_handler)

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(HumanFormatter())
    console_handler.setLevel(logging.INFO)
    logger.addHandler(console_handler)

    return logger


class StepLogger:
    """Convenience wrapper that injects step context into log records."""

    def __init__(self, logger: logging.Logger, step_name: str, run_id: str):
        self.logger = logger
        self.step_name = step_name
        self.run_id = run_id

    def _log(self, level: int, msg: str, data: Any = None) -> None:
        extra = {"step": self.step_name, "run_id": self.run_id, "data": data}
        self.logger.log(level, msg, extra=extra)

    def info(self, msg: str, data: Any = None) -> None:
        self._log(logging.INFO, msg, data)

    def error(self, msg: str, data: Any = None) -> None:
        self._log(logging.ERROR, msg, data)

    def debug(self, msg: str, data: Any = None) -> None:
        self._log(logging.DEBUG, msg, data)

    def warning(self, msg: str, data: Any = None) -> None:
        self._log(logging.WARNING, msg, data)
