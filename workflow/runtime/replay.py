"""Replay engine — replay workflows from journal, compare diffs."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from .command_runtime import CommandRequest, CommandResult, CommandRuntime, ExecutionTarget
from .journal import JournalEntry, SideEffectJournal


@dataclass
class ReplayDiff:
    """Difference between original and replayed execution."""

    entry_id: str
    node_id: str
    command: str
    original_returncode: int | None
    replay_returncode: int | None
    stdout_changed: bool = False
    stderr_changed: bool = False
    original_stdout_preview: str = ""
    replay_stdout_preview: str = ""


@dataclass
class ReplayResult:
    """Result of replaying a workflow or node."""

    run_id: str
    replayed_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    total_entries: int = 0
    replayed_entries: int = 0
    skipped_entries: int = 0
    diffs: list[ReplayDiff] = field(default_factory=list)

    @property
    def has_diffs(self) -> bool:
        return len(self.diffs) > 0


class ReplayEngine:
    """Replays workflow executions from journal entries."""

    def __init__(self, runtime: CommandRuntime, journal: SideEffectJournal):
        self._runtime = runtime
        self._journal = journal

    def replay_node(self, node_id: str, run_id: str) -> ReplayResult:
        entries = self._journal.get_entries(node_id=node_id)
        return self._replay_entries(entries, run_id)

    def replay_workflow(self, run_id: str) -> ReplayResult:
        entries = self._journal.get_entries()
        return self._replay_entries(entries, run_id)

    def dry_run_workflow(self, run_id: str) -> ReplayResult:
        entries = self._journal.get_entries()
        result = ReplayResult(run_id=run_id, total_entries=len(entries))
        for entry in entries:
            request = CommandRequest(
                command=entry.command,
                target=entry.target,
                container_name=entry.container_name,
            )
            self._runtime.dry_run(request, entry.node_id, run_id)
            result.replayed_entries += 1
        return result

    def _replay_entries(self, entries: list[JournalEntry], run_id: str) -> ReplayResult:
        result = ReplayResult(run_id=run_id, total_entries=len(entries))

        for entry in entries:
            if entry.status == "rolled_back":
                result.skipped_entries += 1
                continue

            request = CommandRequest(
                command=entry.command,
                target=entry.target,
                container_name=entry.container_name,
            )

            replay_result = self._runtime.execute(request, entry.node_id, run_id)
            result.replayed_entries += 1

            stdout_changed = (replay_result.stdout[:500] != entry.stdout_preview)
            stderr_changed = (replay_result.stderr[:500] != entry.stderr_preview)

            if (replay_result.returncode != entry.returncode or stdout_changed or stderr_changed):
                result.diffs.append(ReplayDiff(
                    entry_id=entry.entry_id,
                    node_id=entry.node_id,
                    command=entry.command,
                    original_returncode=entry.returncode,
                    replay_returncode=replay_result.returncode,
                    stdout_changed=stdout_changed,
                    stderr_changed=stderr_changed,
                    original_stdout_preview=entry.stdout_preview,
                    replay_stdout_preview=replay_result.stdout[:500],
                ))

        return result
