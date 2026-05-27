"""StateMachineRunner — state-driven execution engine with recovery and failure routing."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .artifacts import ArtifactRegistry
from .checkpoint import CheckpointManager
from .execution_status import NodeExecutionStatus
from .failure_policy import TERMINATE_SENTINEL, classify_error
from .node import BaseNode, NodeResult
from .observability import TraceRecorder
from .recovery import RecoveryAction, RecoveryPolicy
from .runtime import (
    LiveCommandRuntime,
    SandboxEnforcer,
    SideEffectJournal,
)
from .runtime.context import RuntimeContext
from .state import NodeRecord, WorkflowState
from .state_graph import StateGraph
from .termination import ErrorSeverity, TerminationPolicy
from .timeout import StepTimeoutError, check_workflow_timeout, step_timeout, workflow_timeout


class StateMachineRunner:
    """Executes a StateGraph using state-transition semantics."""

    def __init__(
        self,
        graph: StateGraph,
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
        self._consecutive_failures = 0
        self._total_failures = 0

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
        entry_node: Optional[str] = None,
    ) -> WorkflowState:
        state.started_at = datetime.now(timezone.utc)

        if not state.node_records:
            state.init_nodes(self.graph.all_node_ids())

        current = entry_node or self.graph.entry_node
        if not current:
            self.logger.error("No entry node defined in graph")
            return state

        with workflow_timeout(self.total_timeout):
            while current:
                # Check global workflow timeout between nodes
                try:
                    check_workflow_timeout(current)
                except StepTimeoutError:
                    state.workflow_terminated = True
                    state.termination_reason = f"Total workflow timeout ({self.total_timeout}s) exceeded"
                    break

                node = self.graph.get_node(current)
                if not node:
                    self.logger.error(f"Node '{current}' not found in graph")
                    break

                node_record = state.get_node(current)
                if not node_record:
                    node_record = NodeRecord(node_id=current)
                    state.node_records.append(node_record)

                if node_record.status == NodeExecutionStatus.SUCCESS:
                    current = self.graph.resolve_next(current, state)
                    continue

                if node.should_skip(state):
                    node_record.status = NodeExecutionStatus.SKIPPED
                    self.checkpoint_mgr.save(state, current)
                    self.logger.info(
                        f"[{current}] Skipped",
                        extra={"step": current, "run_id": self.run_id},
                    )
                    current = self.graph.resolve_next(current, state)
                    continue

                state.current_node = current
                result = self._execute_node(node, node_record, state)

                if result.success:
                    self._consecutive_failures = 0
                    self.checkpoint_mgr.save(state, current)
                    current = self.graph.resolve_next(current, state)
                else:
                    current = self._handle_failure(current, node_record, result, state)

                if state.workflow_terminated:
                    self.logger.info(
                        f"Workflow terminated: {state.termination_reason}",
                        extra={"step": current or "unknown", "run_id": self.run_id},
                    )
                    break

        state.finished_at = datetime.now(timezone.utc)
        state.current_node = None
        self.checkpoint_mgr.save(state, "final")
        self.trace_recorder.save_timeline()
        self._print_summary(state)
        return state

    def _execute_node(
        self, node: BaseNode, record: NodeRecord, state: WorkflowState
    ) -> NodeResult:
        """Execute a single node with retry according to its RetryPolicy."""
        policy = node.retry_policy

        # Inject RuntimeContext into nodes that support it
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
            record.status = NodeExecutionStatus.RUNNING
            record.started_at = datetime.now(timezone.utc)
            record.retry_count = attempt

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
                    record.status = NodeExecutionStatus.SUCCESS
                    record.finished_at = datetime.now(timezone.utc)
                    if record.started_at:
                        record.duration_seconds = (record.finished_at - record.started_at).total_seconds()
                    record.output_snapshot = result.data
                    self.trace_recorder.finish_trace(trace, result.data, "success")
                    state.set_node_data(f"{node.node_id}_output", result.data)
                    self.logger.info(
                        f"[{node.node_id}] Completed in {record.duration_seconds:.1f}s",
                        extra={"step": node.node_id, "run_id": self.run_id},
                    )
                    return result
                else:
                    error_msg = result.error or "Node returned failure"
                    if policy.should_retry(attempt, error_msg):
                        self.trace_recorder.fail_trace(trace, error_msg)
                        policy.wait(attempt)
                        continue
                    record.status = NodeExecutionStatus.FAILED
                    record.finished_at = datetime.now(timezone.utc)
                    record.error = error_msg
                    if record.started_at:
                        record.duration_seconds = (record.finished_at - record.started_at).total_seconds()
                    self.trace_recorder.fail_trace(trace, error_msg)
                    return result

            except StepTimeoutError as e:
                error_msg = str(e)
                self.trace_recorder.fail_trace(trace, error_msg)
                if policy.should_retry(attempt, error_msg):
                    policy.wait(attempt)
                    continue
                record.status = NodeExecutionStatus.FAILED
                record.finished_at = datetime.now(timezone.utc)
                record.error = error_msg
                return NodeResult(success=False, error=error_msg)

            except Exception as e:
                error_msg = str(e)
                self.trace_recorder.fail_trace(trace, error_msg)
                if policy.should_retry(attempt, error_msg):
                    policy.wait(attempt)
                    continue
                record.status = NodeExecutionStatus.FAILED
                record.finished_at = datetime.now(timezone.utc)
                record.error = error_msg
                return NodeResult(success=False, error=error_msg)

        record.status = NodeExecutionStatus.FAILED
        record.finished_at = datetime.now(timezone.utc)
        return NodeResult(success=False, error="Exhausted all retries")

    def _handle_failure(
        self,
        node_id: str,
        record: NodeRecord,
        result: NodeResult,
        state: WorkflowState,
    ) -> Optional[str]:
        """Handle a node failure: classify, route, recover, or terminate."""
        self._consecutive_failures += 1
        self._total_failures += 1
        error = result.error or ""

        category = classify_error(error)
        severity = self.graph.termination_policy.classify_severity(node_id, category)

        if self.graph.termination_policy.should_terminate(
            self._consecutive_failures, self._total_failures, severity
        ):
            state.workflow_terminated = True
            state.termination_reason = f"Fatal failure at '{node_id}': {error[:200]}"
            record.status = NodeExecutionStatus.TERMINATED
            return None

        # Try failure routing (e.g., OOM → reduce_batch_size node)
        failure_target = self.graph.resolve_failure_target(node_id, error)
        if failure_target and failure_target != TERMINATE_SENTINEL:
            self.logger.info(
                f"[{node_id}] Failure routed to '{failure_target}' (category={category.value})",
                extra={"step": node_id, "run_id": self.run_id},
            )
            record.status = NodeExecutionStatus.RETRYABLE
            self.checkpoint_mgr.save(state, node_id)
            return failure_target

        if failure_target == TERMINATE_SENTINEL:
            state.workflow_terminated = True
            state.termination_reason = f"Routed to terminate at '{node_id}': {error[:200]}"
            record.status = NodeExecutionStatus.TERMINATED
            return None

        # Try recovery policy
        recovery = self.graph.get_recovery(node_id)
        return self._apply_recovery(node_id, record, recovery, state)

    def _apply_recovery(
        self,
        node_id: str,
        record: NodeRecord,
        recovery: RecoveryPolicy,
        state: WorkflowState,
    ) -> Optional[str]:
        """Apply recovery policy and return the next node to execute."""
        if recovery.on_failure == RecoveryAction.SKIP_AND_CONTINUE:
            record.status = NodeExecutionStatus.SKIPPED
            self.logger.info(
                f"[{node_id}] Skipped (optional node, recovery=skip_and_continue)",
                extra={"step": node_id, "run_id": self.run_id},
            )
            self.checkpoint_mgr.save(state, node_id)
            return self.graph.resolve_next(node_id, state)

        if recovery.on_failure == RecoveryAction.ROLLBACK_TO and recovery.rollback_target:
            record.status = NodeExecutionStatus.ROLLED_BACK
            self.logger.info(
                f"[{node_id}] Rolling back to '{recovery.rollback_target}'",
                extra={"step": node_id, "run_id": self.run_id},
            )
            # Execute compensating action if defined
            semantics = self.graph.get_semantics(node_id)
            if semantics.compensating_node:
                comp_node = self.graph.get_node(semantics.compensating_node)
                if comp_node:
                    comp_record = state.get_node(semantics.compensating_node)
                    if not comp_record:
                        comp_record = NodeRecord(node_id=semantics.compensating_node)
                        state.node_records.append(comp_record)
                    self._execute_node(comp_node, comp_record, state)
            self.checkpoint_mgr.save(state, node_id)
            return recovery.rollback_target

        if recovery.on_failure == RecoveryAction.RETRY_CURRENT:
            record.status = NodeExecutionStatus.RETRYABLE
            self.checkpoint_mgr.save(state, node_id)
            return node_id

        # Default: terminate
        state.workflow_terminated = True
        state.termination_reason = f"No recovery for '{node_id}': {record.error or 'unknown'}"
        record.status = NodeExecutionStatus.TERMINATED
        return None

    def _print_summary(self, state: WorkflowState) -> None:
        total = 0.0
        print("\n" + "=" * 70)
        print("  State Machine Workflow Summary")
        print("=" * 70)
        for rec in state.node_records:
            dur = rec.duration_seconds or 0
            total += dur
            status_icon = {
                "success": "✓", "failed": "✗", "skipped": "⊘",
                "pending": "○", "retryable": "↻", "terminated": "⊗",
                "rolled_back": "↩",
            }.get(rec.status.value, "?")
            print(f"  {status_icon} {rec.node_id:<35} {dur:>6.0f}s  [{rec.status.value}]")
        print("-" * 70)
        print(f"  Total: {total:.0f}s ({total/60:.1f}m)")
        if state.workflow_terminated:
            print(f"  Terminated: {state.termination_reason}")
        print("=" * 70 + "\n")
