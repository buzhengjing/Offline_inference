"""Node-level observability: trace recording for each node execution."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field


class NodeTrace(BaseModel):
    node_id: str
    node_type: str  # llm / deterministic
    started_at: str = ""
    finished_at: str = ""
    duration_seconds: float = 0.0
    input: dict = Field(default_factory=dict)
    output: dict = Field(default_factory=dict)
    retry_count: int = 0
    status: str = "pending"  # pending/running/success/failed/skipped
    error: Optional[str] = None
    # LLM-specific
    claude_prompt: Optional[str] = None
    claude_response: Optional[str] = None
    claude_tokens_used: Optional[int] = None
    # Deterministic-specific
    commands_executed: list[str] = Field(default_factory=list)
    command_outputs: list[str] = Field(default_factory=list)


class TraceRecorder:
    """Records and persists node traces for a workflow run."""

    def __init__(self, trace_dir: Path):
        self.trace_dir = trace_dir
        self.trace_dir.mkdir(parents=True, exist_ok=True)
        self._traces: list[NodeTrace] = []

    def start_trace(self, node_id: str, node_type: str, input_data: dict) -> NodeTrace:
        trace = NodeTrace(
            node_id=node_id,
            node_type=node_type,
            started_at=datetime.now(timezone.utc).isoformat(),
            status="running",
            input=input_data,
        )
        self._traces.append(trace)
        return trace

    def finish_trace(self, trace: NodeTrace, output_data: dict, status: str = "success") -> None:
        trace.finished_at = datetime.now(timezone.utc).isoformat()
        trace.status = status
        trace.output = output_data
        if trace.started_at:
            start = datetime.fromisoformat(trace.started_at)
            end = datetime.fromisoformat(trace.finished_at)
            trace.duration_seconds = (end - start).total_seconds()
        self._save_trace(trace)

    def fail_trace(self, trace: NodeTrace, error: str) -> None:
        trace.finished_at = datetime.now(timezone.utc).isoformat()
        trace.status = "failed"
        trace.error = error
        if trace.started_at:
            start = datetime.fromisoformat(trace.started_at)
            end = datetime.fromisoformat(trace.finished_at)
            trace.duration_seconds = (end - start).total_seconds()
        self._save_trace(trace)

    def _save_trace(self, trace: NodeTrace) -> None:
        path = self.trace_dir / f"{trace.node_id}.json"
        path.write_text(trace.model_dump_json(indent=2), encoding="utf-8")

    def save_timeline(self) -> Path:
        path = self.trace_dir / "timeline.json"
        data = [t.model_dump() for t in self._traces]
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        return path

    def get_trace(self, node_id: str) -> Optional[NodeTrace]:
        for t in self._traces:
            if t.node_id == node_id:
                return t
        return None
