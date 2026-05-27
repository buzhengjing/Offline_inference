"""Side Effect Journal — append-only record of all runtime side effects."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field

from .command_runtime import CommandResult, ExecutionTarget


class JournalEntry(BaseModel):
    """Single side-effect record."""

    entry_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    run_id: str = ""
    node_id: str = ""
    command: str = ""
    target: ExecutionTarget = ExecutionTarget.HOST
    container_name: str | None = None
    returncode: int | None = None
    stdout_preview: str = ""
    stderr_preview: str = ""
    duration_seconds: float | None = None
    rollback_strategy: str | None = None
    compensating_action: str | None = None
    status: str = "executed"  # executed | rolled_back | failed | pending


class SideEffectJournal:
    """Append-only journal of all side effects in a workflow run."""

    def __init__(self, journal_dir: Path, run_id: str):
        self._journal_dir = journal_dir
        self._journal_dir.mkdir(parents=True, exist_ok=True)
        self._run_id = run_id
        self._entries: list[JournalEntry] = []
        self._journal_file = self._journal_dir / f"{run_id}.jsonl"

    def record(self, result: CommandResult, rollback_command: str | None = None) -> JournalEntry:
        entry = JournalEntry(
            run_id=result.run_id or self._run_id,
            node_id=result.node_id,
            command=result.command,
            target=result.target,
            returncode=result.returncode,
            stdout_preview=result.stdout[:500] if result.stdout else "",
            stderr_preview=result.stderr[:500] if result.stderr else "",
            duration_seconds=result.duration_seconds,
            rollback_strategy=rollback_command,
            status="executed" if result.returncode == 0 else "failed",
        )
        self._entries.append(entry)
        self._append_to_file(entry)
        return entry

    def record_rollback(self, original_entry_id: str, result: CommandResult) -> JournalEntry:
        entry = JournalEntry(
            run_id=result.run_id or self._run_id,
            node_id=result.node_id,
            command=result.command,
            target=result.target,
            returncode=result.returncode,
            stdout_preview=result.stdout[:500] if result.stdout else "",
            stderr_preview=result.stderr[:500] if result.stderr else "",
            duration_seconds=result.duration_seconds,
            compensating_action=f"rollback of {original_entry_id}",
            status="rolled_back",
        )
        self._entries.append(entry)
        self._append_to_file(entry)
        return entry

    def get_entries(self, node_id: str | None = None) -> list[JournalEntry]:
        if node_id:
            return [e for e in self._entries if e.node_id == node_id]
        return list(self._entries)

    def get_rollback_targets(self, node_id: str) -> list[JournalEntry]:
        return [
            e for e in self._entries
            if e.node_id == node_id and e.rollback_strategy and e.status == "executed"
        ]

    def load(self) -> list[JournalEntry]:
        if not self._journal_file.exists():
            return []
        entries = []
        for line in self._journal_file.read_text(encoding="utf-8").splitlines():
            if line.strip():
                entries.append(JournalEntry.model_validate_json(line))
        self._entries = entries
        return entries

    def _append_to_file(self, entry: JournalEntry) -> None:
        with open(self._journal_file, "a", encoding="utf-8") as f:
            f.write(entry.model_dump_json() + "\n")

    @property
    def entry_count(self) -> int:
        return len(self._entries)

    def summary(self) -> dict:
        executed = sum(1 for e in self._entries if e.status == "executed")
        failed = sum(1 for e in self._entries if e.status == "failed")
        rolled_back = sum(1 for e in self._entries if e.status == "rolled_back")
        return {
            "total": len(self._entries),
            "executed": executed,
            "failed": failed,
            "rolled_back": rolled_back,
        }
