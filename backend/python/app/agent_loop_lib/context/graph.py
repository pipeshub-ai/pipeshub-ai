from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

"""Context graph data model (Phase 5): a typed graph of entities touched
during one or more runs — nodes are agents/tools/files/sources/artifacts,
edges are the actions that connected them (called, spawned, touched,
cited, ...). Built post-hoc from timeline entries by
`build_context_graph()` (context/graph_builder.py); this module is just the
graph's shape and query surface, independent of how it was built.
"""


class NodeType(str, Enum):
    AGENT = "agent"
    TOOL = "tool"
    FILE = "file"
    SOURCE = "source"
    ARTIFACT = "artifact"


class GraphNode(BaseModel):
    id: str
    type: NodeType
    label: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class GraphEdge(BaseModel):
    source_id: str
    target_id: str
    action: str
    run_id: str
    timestamp: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class ContextGraph(BaseModel):
    nodes: dict[str, GraphNode] = Field(default_factory=dict)
    edges: list[GraphEdge] = Field(default_factory=list)

    def add_node(self, node: GraphNode) -> None:
        """Upsert: a node touched by multiple entries (e.g. the same tool
        called many times) is added once, with later metadata merged in
        rather than the node being duplicated or overwritten wholesale."""
        existing = self.nodes.get(node.id)
        if existing is None:
            self.nodes[node.id] = node
        else:
            existing.metadata.update(node.metadata)

    def add_edge(self, edge: GraphEdge) -> None:
        self.edges.append(edge)

    def nodes_of_type(self, node_type: NodeType) -> list[GraphNode]:
        return [n for n in self.nodes.values() if n.type == node_type]

    def edges_from(self, node_id: str) -> list[GraphEdge]:
        return [e for e in self.edges if e.source_id == node_id]

    def edges_to(self, node_id: str) -> list[GraphEdge]:
        return [e for e in self.edges if e.target_id == node_id]

    def neighbors(self, node_id: str) -> list[GraphNode]:
        target_ids = {e.target_id for e in self.edges_from(node_id)}
        return [self.nodes[t] for t in target_ids if t in self.nodes]

    def to_dict(self) -> dict[str, Any]:
        return {
            "nodes": [n.model_dump(mode="json") for n in self.nodes.values()],
            "edges": [e.model_dump(mode="json") for e in self.edges],
        }
