"""Shared graph utilities for dependency validation.

Used by both `spawn_scheduler` (runtime cycle detection on spawn batches)
and `create_plan` (plan-time validation of structured step dependencies).
"""

from __future__ import annotations

__all__ = ["find_cycle"]


def find_cycle(adjacency: dict[str, list[str]]) -> list[str] | None:
    """DFS cycle detection on a directed graph.

    ``adjacency`` maps each node to its list of successors (i.e. the nodes
    it depends on / has edges toward).  Returns the node IDs forming one
    cycle in order if one exists, ``None`` if the graph is acyclic.

    Only nodes present as KEYS in ``adjacency`` are visited — edges
    pointing to nodes outside the key set (e.g. already-completed tasks
    from a prior turn) are silently skipped, matching the spawn scheduler's
    existing semantics.
    """
    WHITE, GRAY, BLACK = 0, 1, 2
    color: dict[str, int] = {node: WHITE for node in adjacency}
    stack: list[str] = []

    def _visit(node: str) -> list[str] | None:
        color[node] = GRAY
        stack.append(node)
        for successor in adjacency.get(node, []):
            if successor not in color:
                continue
            if color[successor] == GRAY:
                idx = stack.index(successor)
                return [*stack[idx:], successor]
            if color[successor] == WHITE:
                found = _visit(successor)
                if found is not None:
                    return found
        stack.pop()
        color[node] = BLACK
        return None

    for node in adjacency:
        if color[node] == WHITE:
            found = _visit(node)
            if found is not None:
                return found
    return None
