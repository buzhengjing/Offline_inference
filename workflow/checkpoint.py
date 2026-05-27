"""Checkpoint management for workflow state persistence."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from .state import WorkflowState


class CheckpointManager:
    def __init__(self, checkpoint_dir: Path):
        self.checkpoint_dir = checkpoint_dir
        self.checkpoint_dir.mkdir(parents=True, exist_ok=True)

    def save(self, state: WorkflowState, step_id: str) -> Path:
        path = self.checkpoint_dir / f"{step_id}.json"
        state.to_file(path)
        latest = self.checkpoint_dir / "latest.json"
        latest.write_text(
            json.dumps({"step_id": step_id, "file": path.name}, indent=2),
            encoding="utf-8",
        )
        return path

    def load_latest(self) -> Optional[tuple[WorkflowState, str]]:
        latest = self.checkpoint_dir / "latest.json"
        if not latest.exists():
            return None
        meta = json.loads(latest.read_text(encoding="utf-8"))
        step_id = meta["step_id"]
        state_path = self.checkpoint_dir / meta["file"]
        if not state_path.exists():
            return None
        state = WorkflowState.from_file(state_path)
        return state, step_id

    def load_step(self, step_id: str) -> Optional[WorkflowState]:
        path = self.checkpoint_dir / f"{step_id}.json"
        if not path.exists():
            return None
        return WorkflowState.from_file(path)

    def list_checkpoints(self) -> list[str]:
        return sorted(
            p.stem for p in self.checkpoint_dir.glob("*.json")
            if p.stem != "latest"
        )
