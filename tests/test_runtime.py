"""Tests for the Runtime Isolation Layer."""

from __future__ import annotations

import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from workflow.artifacts import Artifact, ArtifactRef, ArtifactRegistry, ArtifactType
from workflow.runtime import (
    CommandRequest,
    CommandResult,
    DryRunCommandRuntime,
    ExecutionTarget,
    LiveCommandRuntime,
    SandboxBoundary,
    SandboxEnforcer,
    SandboxViolation,
    SideEffectJournal,
)
from workflow.runtime.context import RuntimeContext
from workflow.runtime.replay import ReplayEngine, ReplayResult
from workflow.runtime.sandbox import SandboxMode


class TestCommandRuntime:
    def test_dry_run_does_not_execute(self):
        runtime = DryRunCommandRuntime()
        request = CommandRequest(
            command="echo hello",
            target=ExecutionTarget.HOST,
        )
        result = runtime.execute(request, "test_node", "run_1")
        assert result.dry_run is True
        assert result.returncode == 0
        assert len(runtime.recorded) == 1

    def test_live_runtime_executes_host_command(self):
        runtime = LiveCommandRuntime()
        request = CommandRequest(
            command="echo hello",
            target=ExecutionTarget.HOST,
            timeout=10,
        )
        result = runtime.execute(request, "test_node", "run_1")
        assert result.returncode == 0
        assert "hello" in result.stdout
        assert result.dry_run is False
        assert result.node_id == "test_node"
        assert result.run_id == "run_1"

    def test_live_runtime_captures_failure(self):
        runtime = LiveCommandRuntime()
        request = CommandRequest(
            command="exit 42",
            target=ExecutionTarget.HOST,
            timeout=10,
        )
        result = runtime.execute(request, "test_node", "run_1")
        assert result.returncode == 42

    def test_live_runtime_with_journal(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            journal = SideEffectJournal(Path(tmpdir), "run_1")
            runtime = LiveCommandRuntime(journal=journal)
            request = CommandRequest(
                command="echo journaled",
                target=ExecutionTarget.HOST,
                rollback_command="echo rollback",
            )
            runtime.execute(request, "node_a", "run_1")
            assert journal.entry_count == 1
            entries = journal.get_entries("node_a")
            assert len(entries) == 1
            assert entries[0].rollback_strategy == "echo rollback"


class TestSandboxEnforcer:
    def test_no_boundary_passes(self):
        enforcer = SandboxEnforcer(mode=SandboxMode.STRICT)
        request = CommandRequest(command="ls", target=ExecutionTarget.HOST)
        enforcer.validate(request, "unknown_node")  # Should not raise

    def test_target_mismatch_strict_raises(self):
        enforcer = SandboxEnforcer(mode=SandboxMode.STRICT)
        enforcer.register_boundary("node_a", SandboxBoundary(
            target=ExecutionTarget.CONTAINER,
            container_name="my_container",
        ))
        request = CommandRequest(command="ls", target=ExecutionTarget.HOST)
        with pytest.raises(SandboxViolation):
            enforcer.validate(request, "node_a")

    def test_target_mismatch_warn_records(self):
        enforcer = SandboxEnforcer(mode=SandboxMode.WARN)
        enforcer.register_boundary("node_a", SandboxBoundary(
            target=ExecutionTarget.CONTAINER,
            container_name="my_container",
        ))
        request = CommandRequest(command="ls", target=ExecutionTarget.HOST)
        enforcer.validate(request, "node_a")  # Should not raise
        assert len(enforcer.violations) == 1

    def test_matching_boundary_passes(self):
        enforcer = SandboxEnforcer(mode=SandboxMode.STRICT)
        enforcer.register_boundary("node_a", SandboxBoundary(
            target=ExecutionTarget.HOST,
        ))
        request = CommandRequest(command="ls", target=ExecutionTarget.HOST)
        enforcer.validate(request, "node_a")  # Should not raise


class TestSideEffectJournal:
    def test_record_and_retrieve(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            journal = SideEffectJournal(Path(tmpdir), "run_1")
            now = datetime.now(timezone.utc)
            result = CommandResult(
                command="echo test",
                target=ExecutionTarget.HOST,
                returncode=0,
                stdout="test\n",
                stderr="",
                started_at=now,
                finished_at=now,
                node_id="node_a",
                run_id="run_1",
            )
            journal.record(result, rollback_command="echo undo")
            assert journal.entry_count == 1
            entries = journal.get_entries("node_a")
            assert entries[0].command == "echo test"
            assert entries[0].rollback_strategy == "echo undo"

    def test_journal_persistence(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            journal = SideEffectJournal(Path(tmpdir), "run_1")
            now = datetime.now(timezone.utc)
            result = CommandResult(
                command="echo persist",
                target=ExecutionTarget.HOST,
                returncode=0,
                started_at=now,
                finished_at=now,
                node_id="node_b",
                run_id="run_1",
            )
            journal.record(result)

            # Load from disk
            journal2 = SideEffectJournal(Path(tmpdir), "run_1")
            loaded = journal2.load()
            assert len(loaded) == 1
            assert loaded[0].command == "echo persist"

    def test_summary(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            journal = SideEffectJournal(Path(tmpdir), "run_1")
            now = datetime.now(timezone.utc)
            journal.record(CommandResult(
                command="ok", target=ExecutionTarget.HOST, returncode=0,
                started_at=now, finished_at=now, node_id="a", run_id="run_1",
            ))
            journal.record(CommandResult(
                command="fail", target=ExecutionTarget.HOST, returncode=1,
                started_at=now, finished_at=now, node_id="b", run_id="run_1",
            ))
            s = journal.summary()
            assert s["total"] == 2
            assert s["executed"] == 1
            assert s["failed"] == 1


class TestArtifactRegistry:
    def test_store_and_register(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            registry = ArtifactRegistry(Path(tmpdir))
            content = b"print('hello')"
            path = registry.store("art_001", content, "script.py")
            assert path.exists()
            assert path.read_bytes() == content

            artifact = Artifact(
                id="art_001",
                type=ArtifactType.SCRIPT,
                producer_node="gen_script",
                run_id="run_1",
                path=str(path),
                checksum="abc123",
                size_bytes=len(content),
            )
            ref = registry.register(artifact)
            assert ref.artifact_id == "art_001"
            assert ref.type == ArtifactType.SCRIPT

    def test_get_by_node(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            registry = ArtifactRegistry(Path(tmpdir))
            for i in range(3):
                artifact = Artifact(
                    id=f"art_{i}",
                    type=ArtifactType.LOG,
                    producer_node="node_a" if i < 2 else "node_b",
                    run_id="run_1",
                    path=f"/tmp/log_{i}.txt",
                    checksum=f"hash_{i}",
                )
                registry.register(artifact)

            results = registry.get_by_node("node_a")
            assert len(results) == 2

    def test_lineage(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            registry = ArtifactRegistry(Path(tmpdir))
            a1 = Artifact(
                id="v1", type=ArtifactType.SCRIPT, producer_node="n",
                run_id="r", path="/p", checksum="h1",
            )
            a2 = Artifact(
                id="v2", type=ArtifactType.SCRIPT, producer_node="n",
                run_id="r", path="/p2", checksum="h2", parent_id="v1",
            )
            registry.register(a1)
            registry.register(a2)
            lineage = registry.get_lineage("v2")
            assert len(lineage) == 2
            assert lineage[0].id == "v2"
            assert lineage[1].id == "v1"

    def test_gc(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            registry = ArtifactRegistry(Path(tmpdir))
            for i in range(10):
                registry.store(f"art_{i}", b"data", "f.txt")
                registry.register(Artifact(
                    id=f"art_{i}", type=ArtifactType.LOG, producer_node="n",
                    run_id=f"run_{i}", path=f"{tmpdir}/art_{i}/f.txt", checksum=f"h{i}",
                ))
            removed = registry.gc(keep_latest=3)
            assert len(removed) == 7


class TestRuntimeContext:
    def test_execute_delegates_to_runtime(self):
        runtime = DryRunCommandRuntime()
        with tempfile.TemporaryDirectory() as tmpdir:
            journal = SideEffectJournal(Path(tmpdir) / "j", "run_1")
            registry = ArtifactRegistry(Path(tmpdir) / "a")
            ctx = RuntimeContext(
                runtime=runtime,
                journal=journal,
                artifact_registry=registry,
                node_id="test_node",
                run_id="run_1",
                container_name="my_ctr",
            )
            result = ctx.execute_in_container("echo hi", timeout=10)
            assert result.dry_run is True
            assert len(runtime.recorded) == 1
            req = runtime.recorded[0][0]
            assert req.target == ExecutionTarget.CONTAINER
            assert req.container_name == "my_ctr"

    def test_produce_artifact(self):
        runtime = DryRunCommandRuntime()
        with tempfile.TemporaryDirectory() as tmpdir:
            journal = SideEffectJournal(Path(tmpdir) / "j", "run_1")
            registry = ArtifactRegistry(Path(tmpdir) / "a")
            ctx = RuntimeContext(
                runtime=runtime,
                journal=journal,
                artifact_registry=registry,
                node_id="gen_node",
                run_id="run_1",
            )
            ref = ctx.produce_artifact(
                content="print('hello')",
                artifact_type=ArtifactType.SCRIPT,
                filename="run_inference.py",
            )
            assert ref.type == ArtifactType.SCRIPT
            assert ref.producer_node == "gen_node"
            stored = registry.get(ref.artifact_id)
            assert stored is not None
            assert stored.size_bytes == len("print('hello')".encode())


class TestReplayEngine:
    def test_replay_with_dry_run_runtime(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            journal = SideEffectJournal(Path(tmpdir), "run_1")
            now = datetime.now(timezone.utc)
            journal.record(CommandResult(
                command="echo hello", target=ExecutionTarget.HOST, returncode=0,
                stdout="hello\n", stderr="", started_at=now, finished_at=now,
                node_id="node_a", run_id="run_1",
            ))

            replay_runtime = DryRunCommandRuntime()
            engine = ReplayEngine(replay_runtime, journal)
            result = engine.replay_workflow("run_replay")
            assert result.total_entries == 1
            assert result.replayed_entries == 1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
