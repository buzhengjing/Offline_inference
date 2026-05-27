"""Artifact lineage tracking."""

from __future__ import annotations

from .artifact import Artifact
from .registry import ArtifactRegistry


class LineageTracker:
    """Tracks parent-child relationships between artifacts."""

    def __init__(self, registry: ArtifactRegistry):
        self._registry = registry

    def get_full_lineage(self, artifact_id: str) -> list[Artifact]:
        return self._registry.get_lineage(artifact_id)

    def get_descendants(self, artifact_id: str) -> list[Artifact]:
        descendants = []
        for artifact in self._registry._artifacts.values():
            if artifact.parent_id == artifact_id:
                descendants.append(artifact)
                descendants.extend(self.get_descendants(artifact.id))
        return descendants
