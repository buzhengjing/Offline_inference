"""Audit log formatting for runtime operations."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from .journal import JournalEntry, SideEffectJournal


class AuditLog:
    """Formats and persists human-readable audit logs from journal entries."""

    def __init__(self, audit_dir: Path):
        self._audit_dir = audit_dir
        self._audit_dir.mkdir(parents=True, exist_ok=True)

    def generate_report(self, journal: SideEffectJournal, run_id: str) -> Path:
        entries = journal.get_entries()
        report_path = self._audit_dir / f"audit_{run_id}.md"

        lines = [
            f"# Audit Report — Run {run_id}",
            f"Generated: {datetime.now(timezone.utc).isoformat()}",
            f"Total side effects: {len(entries)}",
            "",
            "## Side Effect Timeline",
            "",
            "| # | Node | Command | Target | RC | Duration |",
            "|---|------|---------|--------|----|---------:|",
        ]

        for i, entry in enumerate(entries, 1):
            cmd_short = entry.command[:60] + ("..." if len(entry.command) > 60 else "")
            dur = f"{entry.duration_seconds:.1f}s" if entry.duration_seconds else "-"
            lines.append(
                f"| {i} | {entry.node_id} | `{cmd_short}` | {entry.target.value} | {entry.returncode} | {dur} |"
            )

        summary = journal.summary()
        lines.extend([
            "",
            "## Summary",
            "",
            f"- Executed: {summary['executed']}",
            f"- Failed: {summary['failed']}",
            f"- Rolled back: {summary['rolled_back']}",
        ])

        rollback_entries = [e for e in entries if e.rollback_strategy]
        if rollback_entries:
            lines.extend(["", "## Rollback Strategies", ""])
            for entry in rollback_entries:
                lines.append(f"- **{entry.node_id}**: `{entry.rollback_strategy}`")

        report_path.write_text("\n".join(lines), encoding="utf-8")
        return report_path
