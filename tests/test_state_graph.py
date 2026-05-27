"""Tests for the StateGraph and StateMachineRunner."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from workflow.edges import ConditionEdge, FallbackEdge, UnconditionalEdge
from workflow.execution_status import NodeExecutionStatus
from workflow.failure_policy import FailureCategory, FailureRoute, classify_error
from workflow.node import BaseNode, NodeResult, NodeType
from workflow.recovery import NodeSemantics, RecoveryAction, RecoveryPolicy
from workflow.state import NodeRecord, WorkflowState
from workflow.state_graph import StateGraph
from workflow.termination import ErrorSeverity, TerminationPolicy


class DummyNode(BaseNode):
    """A test node that always succeeds."""

    def __init__(self, node_id: str, result: NodeResult = None):
        super().__init__(node_id=node_id, node_type=NodeType.DETERMINISTIC, timeout=10)
        self._result = result or NodeResult(success=True, data={"done": True})

    def execute(self, state, logger):
        return self._result


class FailingNode(BaseNode):
    """A test node that always fails."""

    def __init__(self, node_id: str, error: str = "test error"):
        super().__init__(node_id=node_id, node_type=NodeType.DETERMINISTIC, timeout=10)
        self._error = error

    def execute(self, state, logger):
        return NodeResult(success=False, error=self._error)


# --- Test NodeExecutionStatus ---

class TestNodeExecutionStatus:
    def test_all_statuses_exist(self):
        assert NodeExecutionStatus.PENDING == "pending"
        assert NodeExecutionStatus.RUNNING == "running"
        assert NodeExecutionStatus.SUCCESS == "success"
        assert NodeExecutionStatus.FAILED == "failed"
        assert NodeExecutionStatus.RETRYABLE == "retryable"
        assert NodeExecutionStatus.SKIPPED == "skipped"
        assert NodeExecutionStatus.TERMINATED == "terminated"
        assert NodeExecutionStatus.ROLLED_BACK == "rolled_back"


# --- Test ConditionEdge routing ---

class TestConditionEdgeRouting:
    def _make_state(self, **node_data) -> WorkflowState:
        state = WorkflowState(
            run_id="test", target="test", model="test",
            harbor_user="", harbor_password="",
        )
        for k, v in node_data.items():
            state.set_node_data(k, v)
        return state

    def test_condition_edge_true(self):
        graph = StateGraph()
        graph.add_node(DummyNode("a"))
        graph.add_node(DummyNode("b"))
        graph.add_node(DummyNode("c"))

        graph.add_condition_edge("a", "b", condition=lambda s: True, priority=10)
        graph.add_condition_edge("a", "c", condition=lambda s: False, priority=5)

        state = self._make_state()
        assert graph.resolve_next("a", state) == "b"

    def test_condition_edge_fallback(self):
        graph = StateGraph()
        graph.add_node(DummyNode("a"))
        graph.add_node(DummyNode("b"))
        graph.add_node(DummyNode("fallback"))

        graph.add_condition_edge("a", "b", condition=lambda s: False)
        graph.add_fallback_edge("a", "fallback")

        state = self._make_state()
        assert graph.resolve_next("a", state) == "fallback"

    def test_unconditional_edge(self):
        graph = StateGraph()
        graph.add_node(DummyNode("a"))
        graph.add_node(DummyNode("b"))

        graph.add_edge("a", "b")

        state = self._make_state()
        assert graph.resolve_next("a", state) == "b"

    def test_no_edge_returns_none(self):
        graph = StateGraph()
        graph.add_node(DummyNode("a"))

        state = self._make_state()
        assert graph.resolve_next("a", state) is None

    def test_state_predicate_routing(self):
        graph = StateGraph()
        graph.add_node(DummyNode("validate"))
        graph.add_node(DummyNode("success_path"))
        graph.add_node(DummyNode("failure_path"))

        graph.add_condition_edge(
            "validate", "success_path",
            condition=lambda s: s.get_node_data("output_valid", False),
            priority=10,
        )
        graph.add_condition_edge(
            "validate", "failure_path",
            condition=lambda s: not s.get_node_data("output_valid", False),
            priority=5,
        )

        state_pass = self._make_state(output_valid=True)
        assert graph.resolve_next("validate", state_pass) == "success_path"

        state_fail = self._make_state(output_valid=False)
        assert graph.resolve_next("validate", state_fail) == "failure_path"


# --- Test Failure Classification ---

class TestFailureClassification:
    def test_oom_detection(self):
        assert classify_error("CUDA out of memory") == FailureCategory.OOM
        assert classify_error("torch.cuda.OutOfMemoryError") == FailureCategory.OOM

    def test_missing_dep_detection(self):
        assert classify_error("ModuleNotFoundError: No module named 'flash_attn'") == FailureCategory.MISSING_DEPENDENCY

    def test_model_corruption(self):
        assert classify_error("safetensors_rust.SafetensorError: invalid header") == FailureCategory.MODEL_CORRUPTION

    def test_timeout(self):
        assert classify_error("TimeoutError: operation timed out") == FailureCategory.TIMEOUT

    def test_unknown(self):
        assert classify_error("some random error") == FailureCategory.UNKNOWN

    def test_exit_code_137_is_oom(self):
        assert classify_error("Killed", exit_code=137) == FailureCategory.OOM


# --- Test Failure Routing ---

class TestFailureRouting:
    def test_route_to_target(self):
        graph = StateGraph()
        graph.add_node(DummyNode("execute"))
        graph.add_node(DummyNode("diagnose"))

        graph.set_failure_routes("execute", [
            FailureRoute(FailureCategory.OOM, "diagnose", "retry_with_fix"),
        ])

        target = graph.resolve_failure_target("execute", "CUDA out of memory")
        assert target == "diagnose"

    def test_no_route_returns_none(self):
        graph = StateGraph()
        graph.add_node(DummyNode("execute"))

        target = graph.resolve_failure_target("execute", "some error")
        assert target is None


# --- Test Recovery Policy ---

class TestRecoveryPolicy:
    def test_skip_optional(self):
        policy = RecoveryPolicy(
            node_id="install_flaggems",
            on_failure=RecoveryAction.SKIP_AND_CONTINUE,
            is_optional=True,
        )
        assert policy.should_skip_on_failure()

    def test_retry_current(self):
        policy = RecoveryPolicy(
            node_id="execute_inference",
            on_failure=RecoveryAction.RETRY_CURRENT,
            max_retries=2,
        )
        assert not policy.should_skip_on_failure()
        assert policy.on_failure == RecoveryAction.RETRY_CURRENT

    def test_rollback(self):
        policy = RecoveryPolicy(
            node_id="install_flaggems",
            on_failure=RecoveryAction.ROLLBACK_TO,
            rollback_target="validate_seg2",
        )
        assert policy.rollback_target == "validate_seg2"


# --- Test Termination Policy ---

class TestTerminationPolicy:
    def test_fatal_terminates(self):
        policy = TerminationPolicy()
        assert policy.should_terminate(1, 1, ErrorSeverity.FATAL)

    def test_consecutive_failures_terminate(self):
        policy = TerminationPolicy(max_consecutive_failures=3)
        assert not policy.should_terminate(2, 2, ErrorSeverity.RETRYABLE)
        assert policy.should_terminate(3, 3, ErrorSeverity.RETRYABLE)

    def test_ignorable_does_not_terminate(self):
        policy = TerminationPolicy()
        severity = policy.classify_severity("diagnose_failure", FailureCategory.UNKNOWN)
        assert severity == ErrorSeverity.IGNORABLE

    def test_partial_success_segment(self):
        policy = TerminationPolicy(partial_success_segments={"seg3", "flaggems"})
        severity = policy.classify_severity("execute_flaggems_inference", FailureCategory.RUNTIME_ERROR)
        assert severity == ErrorSeverity.PARTIAL


# --- Test NodeRecord with new statuses ---

class TestNodeRecord:
    def test_mark_retryable(self):
        rec = NodeRecord(node_id="test")
        rec.mark_retryable()
        assert rec.status == NodeExecutionStatus.RETRYABLE

    def test_mark_terminated(self):
        rec = NodeRecord(node_id="test")
        rec.mark_terminated("fatal error")
        assert rec.status == NodeExecutionStatus.TERMINATED
        assert rec.error == "fatal error"

    def test_mark_rolled_back(self):
        rec = NodeRecord(node_id="test")
        rec.mark_rolled_back()
        assert rec.status == NodeExecutionStatus.ROLLED_BACK


# --- Test StateGraph build ---

class TestBuildStateGraph:
    def test_build_does_not_crash(self):
        from workflow.build_graph import build_inference_state_graph
        graph = build_inference_state_graph()
        assert graph.entry_node == "search_model_weights"
        assert len(graph.nodes) == 38
        assert len(graph.condition_edges) > 0
        assert len(graph.recovery_policies) > 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
