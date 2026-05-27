"""Node DAG definition and topological execution support."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .node import BaseNode


@dataclass
class NodeEdge:
    from_node: str
    to_node: str
    condition: Optional[str] = None  # e.g., "inference_failed", "inference_passed"


@dataclass
class NodeGraph:
    """Directed acyclic graph of workflow nodes with conditional edges."""

    nodes: dict[str, BaseNode] = field(default_factory=dict)
    edges: list[NodeEdge] = field(default_factory=list)

    def add_node(self, node: BaseNode) -> None:
        self.nodes[node.node_id] = node

    def add_edge(self, from_id: str, to_id: str, condition: Optional[str] = None) -> None:
        self.edges.append(NodeEdge(from_node=from_id, to_node=to_id, condition=condition))

    def get_node(self, node_id: str) -> Optional[BaseNode]:
        return self.nodes.get(node_id)

    def get_successors(self, node_id: str, active_conditions: set[str] | None = None) -> list[str]:
        """Get successor node IDs, filtering by active conditions."""
        successors = []
        for edge in self.edges:
            if edge.from_node != node_id:
                continue
            if edge.condition is None:
                successors.append(edge.to_node)
            elif active_conditions and edge.condition in active_conditions:
                successors.append(edge.to_node)
        return successors

    def get_predecessors(self, node_id: str) -> list[str]:
        return [e.from_node for e in self.edges if e.to_node == node_id]

    def topological_order(self) -> list[str]:
        """Return nodes in topological order (all edges count regardless of condition)."""
        in_degree: dict[str, int] = {nid: 0 for nid in self.nodes}
        adj: dict[str, list[str]] = {nid: [] for nid in self.nodes}

        for edge in self.edges:
            if edge.from_node in self.nodes and edge.to_node in self.nodes:
                # Avoid counting duplicate edges to same target
                if edge.to_node not in adj[edge.from_node]:
                    adj[edge.from_node].append(edge.to_node)
                    in_degree[edge.to_node] += 1

        queue = sorted([nid for nid, deg in in_degree.items() if deg == 0])
        order = []

        while queue:
            node_id = queue.pop(0)
            order.append(node_id)
            for successor in sorted(adj[node_id]):
                in_degree[successor] -= 1
                if in_degree[successor] == 0:
                    queue.append(successor)
            queue.sort()

        return order

    def execution_order(self) -> list[str]:
        """Return the linear execution order for the workflow."""
        return self.topological_order()
