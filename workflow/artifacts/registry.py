"""ArtifactRegistry — stores, indexes, and manages artifact lifecycle."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .artifact import Artifact, ArtifactRef, ArtifactType


class ArtifactRegistry:
    """Central registry for all workflow artifacts."""

    def __init__(self, storage_dir: Path):
        self._storage_dir = storage_dir
        self._storage_dir.mkdir(parents=True, exist_ok=True)
        self._index_file = self._storage_dir / "artifacts.json"
        self._artifacts: dict[str, Artifact] = {}
        self._load_index()

    def store(self, artifact_id: str, content: bytes, filename: str) -> Path:
        artifact_dir = self._storage_dir / artifact_id
        artifact_dir.mkdir(parents=True, exist_ok=True)
        file_path = artifact_dir / filename
        file_path.write_bytes(content)
        return file_path

    def register(self, artifact: Artifact) -> ArtifactRef:
        self._artifacts[artifact.id] = artifact
        self._save_index()
        return ArtifactRef(
            artifact_id=artifact.id,
            type=artifact.type,
            producer_node=artifact.producer_node,
        )

    def get(self, artifact_id: str) -> Artifact | None:
        return self._artifacts.get(artifact_id)

    def get_by_node(self, node_id: str, run_id: str | None = None) -> list[Artifact]:
        results = [a for a in self._artifacts.values() if a.producer_node == node_id]
        if run_id:
            results = [a for a in results if a.run_id == run_id]
        return results

    def get_by_type(self, artifact_type: ArtifactType) -> list[Artifact]:
        return [a for a in self._artifacts.values() if a.type == artifact_type]

    def get_lineage(self, artifact_id: str) -> list[Artifact]:
        lineage = []
        current = self._artifacts.get(artifact_id)
        while current:
            lineage.append(current)
            if current.parent_id:
                current = self._artifacts.get(current.parent_id)
            else:
                break
        return lineage

    def gc(self, keep_runs: list[str] | None = None, keep_latest: int = 5) -> list[str]:
        if not keep_runs:
            all_runs = sorted(set(a.run_id for a in self._artifacts.values()))
            keep_runs = all_runs[-keep_latest:] if len(all_runs) > keep_latest else all_runs

        to_remove = []
        for aid, artifact in list(self._artifacts.items()):
            if artifact.run_id not in keep_runs:
                to_remove.append(aid)
                artifact_dir = self._storage_dir / aid
                if artifact_dir.exists():
                    import shutil
                    shutil.rmtree(artifact_dir, ignore_errors=True)
                del self._artifacts[aid]

        if to_remove:
            self._save_index()
        return to_remove

    def _load_index(self) -> None:
        if self._index_file.exists():
            data = json.loads(self._index_file.read_text(encoding="utf-8"))
            for item in data:
                artifact = Artifact.model_validate(item)
                self._artifacts[artifact.id] = artifact

    def _save_index(self) -> None:
        data = [a.model_dump(mode="json") for a in self._artifacts.values()]
        self._index_file.write_text(
            json.dumps(data, indent=2, ensure_ascii=False, default=str),
            encoding="utf-8",
        )
