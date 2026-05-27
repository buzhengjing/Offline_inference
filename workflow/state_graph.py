"""StateGraph — directed graph with condition edges, failure routing, and recovery semantics."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .edges import ConditionEdge, FallbackEdge, UnconditionalEdge
from .failure_policy import FailureRoute, FailureRouter
from .node import BaseNode
from .recovery import NodeSemantics, RecoveryAction, RecoveryPolicy
from .state import WorkflowState
from .termination import TerminationPolicy


@dataclass
class StateGraph:
    """State-machine-style graph with conditional routing and recovery policies."""

    nodes: dict[str, BaseNode] = field(default_factory=dict)
    unconditional_edges: list[UnconditionalEdge] = field(default_factory=list)
    condition_edges: list[ConditionEdge] = field(default_factory=list)
    fallback_edges: list[FallbackEdge] = field(default_factory=list)

    failure_router: FailureRouter = field(default_factory=FailureRouter)
    recovery_policies: dict[str, RecoveryPolicy] = field(default_factory=dict)
    node_semantics: dict[str, NodeSemantics] = field(default_factory=dict)
    termination_policy: TerminationPolicy = field(default_factory=TerminationPolicy)

    entry_node: Optional[str] = None

    # --- Node management ---

    def add_node(self, node: BaseNode) -> None:
        self.nodes[node.node_id] = node
        if self.entry_node is None:
            self.entry_node = node.node_id

    def get_node(self, node_id: str) -> Optional[BaseNode]:
        return self.nodes.get(node_id)

    def set_entry(self, node_id: str) -> None:
        self.entry_node = node_id

    # --- Edge management ---

    def add_edge(self, from_id: str, to_id: str) -> None:
        self.unconditional_edges.append(UnconditionalEdge(from_node=from_id, to_node=to_id))

    def add_condition_edge(
        self,
        from_id: str,
        to_id: str,
        condition,
        priority: int = 0,
        label: str = "",
    ) -> None:
        self.condition_edges.append(
            ConditionEdge(
                from_node=from_id,
                to_node=to_id,
                condition=condition,
                priority=priority,
                label=label,
            )
        )

    def add_fallback_edge(self, from_id: str, to_id: str) -> None:
        self.fallback_edges.append(FallbackEdge(from_node=from_id, to_node=to_id))

    # --- Failure routing ---

    def set_failure_routes(self, node_id: str, routes: list[FailureRoute]) -> None:
        self.failure_router.add_routes(node_id, routes)

    # --- Recovery ---

    def set_recovery(self, policy: RecoveryPolicy) -> None:
        self.recovery_policies[policy.node_id] = policy

    def get_recovery(self, node_id: str) -> RecoveryPolicy:
        return self.recovery_policies.get(
            node_id,
            RecoveryPolicy(node_id=node_id, on_failure=RecoveryAction.TERMINATE),
        )

    # --- Semantics ---

    def set_semantics(self, node_id: str, semantics: NodeSemantics) -> None:
        self.node_semantics[node_id] = semantics

    def get_semantics(self, node_id: str) -> NodeSemantics:
        return self.node_semantics.get(node_id, NodeSemantics())

    # --- State transition resolution ---

    def resolve_next(self, current_node: str, state: WorkflowState) -> Optional[str]:
        """Determine the next node based on current state.

        Resolution order:
        1. Condition edges (sorted by priority descending) — first match wins
        2. Fallback edge — if no condition matched
        3. Unconditional edge — always taken if present and no conditional routing exists
        """
        # Collect condition edges from current node
        cond_edges = sorted(
            [e for e in self.condition_edges if e.from_node == current_node],
            key=lambda e: e.priority,
            reverse=True,
        )

        if cond_edges:
            for edge in cond_edges:
                if edge.evaluate(state):
                    return edge.to_node
            # No condition matched — try fallback
            for fb in self.fallback_edges:
                if fb.from_node == current_node:
                    return fb.to_node
            return None

        # No condition edges — use unconditional edges
        for edge in self.unconditional_edges:
            if edge.from_node == current_node:
                return edge.to_node

        return None

    def resolve_failure_target(
        self, node_id: str, error: str, exit_code: int = -1
    ) -> Optional[str]:
        """Given a failure, determine where to route."""
        route = self.failure_router.route(node_id, error, exit_code)
        if route:
            return route.target_node
        return None

    # --- Introspection ---

    def all_node_ids(self) -> list[str]:
        return list(self.nodes.keys())

    def get_predecessors(self, node_id: str) -> list[str]:
        preds = set()
        for e in self.unconditional_edges:
            if e.to_node == node_id:
                preds.add(e.from_node)
        for e in self.condition_edges:
            if e.to_node == node_id:
                preds.add(e.from_node)
        for e in self.fallback_edges:
            if e.to_node == node_id:
                preds.add(e.from_node)
        return list(preds)

    def get_successors(self, node_id: str) -> list[str]:
        succs = set()
        for e in self.unconditional_edges:
            if e.from_node == node_id:
                succs.add(e.to_node)
        for e in self.condition_edges:
            if e.from_node == node_id:
                succs.add(e.to_node)
        for e in self.fallback_edges:
            if e.from_node == node_id:
                succs.add(e.to_node)
        return list(succs)
