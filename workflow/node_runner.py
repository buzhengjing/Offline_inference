"""NodeRunner — executes fine-grained nodes with retry, checkpoint, and observability."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .artifacts import ArtifactRegistry
from .checkpoint import CheckpointManager
from .graph import NodeGraph
from .node import BaseNode, NodeResult
from .observability import TraceRecorder
from .retry import RetryPolicy
from .runtime import LiveCommandRuntime, SandboxEnforcer, SideEffectJournal
from .runtime.context import RuntimeContext
from .execution_status import NodeExecutionStatus
from .state import NodeRecord, WorkflowState
from .timeout import StepTimeoutError, check_workflow_timeout, step_timeout, workflow_timeout


class NodeRunner:
    """Orchestrates node-level execution with checkpoint and observability."""

    def __init__(
        self,
        graph: NodeGraph,
        checkpoint_mgr: CheckpointManager,
        trace_recorder: TraceRecorder,
        logger: logging.Logger,
        run_id: str,
        total_timeout: float = 10800,
        runtime_base_dir: Path | None = None,
    ):
        self.graph = graph
        self.checkpoint_mgr = checkpoint_mgr
        self.trace_recorder = trace_recorder
        self.logger = logger
        self.run_id = run_id
        self.total_timeout = total_timeout

        # Runtime isolation layer
        base_dir = runtime_base_dir or Path(f"/tmp/workflow_runtime/{run_id}")
        self._journal = SideEffectJournal(base_dir / "journal", run_id)
        self._artifact_registry = ArtifactRegistry(base_dir / "artifacts")
        self._sandbox_enforcer = SandboxEnforcer()
        self._command_runtime = LiveCommandRuntime(
            journal=self._journal,
            sandbox_enforcer=self._sandbox_enforcer,
        )

    @property
    def journal(self) -> SideEffectJournal:
        return self._journal

    @property
    def artifact_registry(self) -> ArtifactRegistry:
        return self._artifact_registry

    def run(
        self,
        state: WorkflowState,
        resume_from: Optional[str] = None,
    ) -> WorkflowState:
        state.started_at = datetime.now(timezone.utc)
        execution_order = self.graph.execution_order()

        if not state.node_records:
            state.init_nodes(execution_order)

        skip_until_found = resume_from is not None
        resume_index = 0
        if resume_from:
            for i, nid in enumerate(execution_order):
                if nid == resume_from:
                    resume_index = i
                    break

        with workflow_timeout(self.total_timeout):
            for i, node_id in enumerate(execution_order):
                if skip_until_found and i < resume_index:
                    continue
                skip_until_found = False

                node = self.graph.get_node(node_id)
                if not node:
                    continue

                node_record = state.get_node(node_id)
                if not node_record:
                    continue

                if node_record.status == NodeExecutionStatus.SUCCESS:
                    continue

                # Check global workflow timeout between nodes
                try:
                    check_workflow_timeout(node_id)
                except StepTimeoutError:
                    state.workflow_terminated = True
                    state.termination_reason = f"Total workflow timeout ({self.total_timeout}s) exceeded"
                    break

                if node.should_skip(state):
                    node_record.status = NodeExecutionStatus.SKIPPED
                    self.checkpoint_mgr.save(state, node_id)
                    continue

                state.current_node = node_id
                state = self._execute_node_with_retry(node, state)
                self.checkpoint_mgr.save(state, node_id)

                if state.workflow_terminated:
                    self.logger.info(
                        f"Workflow terminated at node '{node_id}': {state.termination_reason}",
                        extra={"step": node_id, "run_id": self.run_id},
                    )
                    break

                if node_record.status == NodeExecutionStatus.FAILED:
                    self.logger.error(
                        f"Node '{node_id}' failed: {node_record.error}",
                        extra={"step": node_id, "run_id": self.run_id},
                    )
                    # Check if this is a non-critical failure (conditional path)
                    if not self._is_critical_failure(node_id, state):
                        continue
                    break

        state.finished_at = datetime.now(timezone.utc)
        state.current_node = None
        self.checkpoint_mgr.save(state, "final")
        self.trace_recorder.save_timeline()
        self._print_summary(state)
        return state

    def _execute_node_with_retry(self, node: BaseNode, state: WorkflowState) -> WorkflowState:
        node_record = state.get_node(node.node_id)
        policy = node.retry_policy

        # Inject RuntimeContext
        ctx = RuntimeContext(
            runtime=self._command_runtime,
            journal=self._journal,
            artifact_registry=self._artifact_registry,
            sandbox_enforcer=self._sandbox_enforcer,
            node_id=node.node_id,
            run_id=self.run_id,
            container_name=state.container_name,
        )
        if hasattr(node, "set_runtime_context"):
            node.set_runtime_context(ctx)

        for attempt in range(policy.max_retries + 1):
            node_record.mark_running()
            node_record.retry_count = attempt

            trace = self.trace_recorder.start_trace(
                node.node_id, node.node_type.value, node.get_input_snapshot(state)
            )

            self.logger.info(
                f"[{node.node_id}] Starting (attempt {attempt + 1}/{policy.max_retries + 1})",
                extra={"step": node.node_id, "run_id": self.run_id},
            )

            try:
                with step_timeout(node.node_id, node.timeout):
                    result = node.execute(state, self.logger, ctx)

                if result.success:
                    node_record.mark_success(output=result.data)
                    self.trace_recorder.finish_trace(trace, result.data, "success")
                    state.set_node_data(f"{node.node_id}_output", result.data)
                    self.logger.info(
                        f"[{node.node_id}] Completed in {node_record.duration_seconds:.1f}s",
                        extra={"step": node.node_id, "run_id": self.run_id},
                    )
                    return state
                else:
                    error_msg = result.error or "Node returned failure"
                    if policy.should_retry(attempt, error_msg):
                        self.trace_recorder.fail_trace(trace, error_msg)
                        policy.wait(attempt)
                        continue
                    node_record.mark_failed(error_msg)
                    self.trace_recorder.fail_trace(trace, error_msg)
                    return state

            except StepTimeoutError as e:
                error_msg = str(e)
                self.trace_recorder.fail_trace(trace, error_msg)
                if policy.should_retry(attempt, error_msg):
                    policy.wait(attempt)
                    continue
                node_record.mark_failed(error_msg)
                return state

            except Exception as e:
                error_msg = str(e)
                self.trace_recorder.fail_trace(trace, error_msg)
                if policy.should_retry(attempt, error_msg):
                    policy.wait(attempt)
                    continue
                node_record.mark_failed(error_msg)
                return state

        return state

    def _is_critical_failure(self, node_id: str, state: WorkflowState) -> bool:
        """Determine if a node failure should halt the workflow."""
        non_critical = {"diagnose_failure", "apply_fix", "diagnose_flaggems_failure", "apply_flaggems_fix"}
        return node_id not in non_critical

    def _print_summary(self, state: WorkflowState) -> None:
        total = 0.0
        print("\n" + "=" * 70)
        print("  Node Workflow Summary")
        print("=" * 70)
        for rec in state.node_records:
            dur = rec.duration_seconds or 0
            total += dur
            status_icon = {"success": "✓", "failed": "✗", "skipped": "⊘", "pending": "○"}.get(rec.status.value, "?")
            print(f"  {status_icon} {rec.node_id:<35} {dur:>6.0f}s  [{rec.status.value}]")
        print("-" * 70)
        print(f"  Total: {total:.0f}s ({total/60:.1f}m)")
        if state.workflow_terminated:
            print(f"  Terminated: {state.termination_reason}")
        print("=" * 70 + "\n")
