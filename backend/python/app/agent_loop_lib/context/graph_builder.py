from __future__ import annotations

from app.agent_loop_lib.context.graph import (
    ContextGraph,
    GraphEdge,
    GraphNode,
    NodeType,
)
from app.agent_loop_lib.modules.stores.timeline.base import TimelineEntry

"""Builds a ContextGraph from timeline entries (Phase 5) — the same raw
material `eval/trajectory.py` and `eval/decision_trace.py` consume, read
here for a different shape: not "what happened, in order" but "what got
touched, and how are those things connected". Pure function, no I/O; feed
it `TimelineStore.get_by_trace()`/`get_by_run()` output.
"""

# Tools whose args include a file path worth graphing as a FILE node —
# deliberately a small, explicit allowlist rather than guessing at
# arbitrary tool schemas.
_FILE_ARG_TOOLS: dict[str, str] = {
    "read_file": "path",
    "write_file": "path",
    "edit_file": "path",
}


def _agent_node(entry: TimelineEntry) -> GraphNode:
    return GraphNode(
        id=f"agent:{entry.agent_id}",
        type=NodeType.AGENT,
        label=entry.role_name or entry.agent_id,
        metadata={"run_id": entry.run_id},
    )


def _tool_node(name: str) -> GraphNode:
    return GraphNode(id=f"tool:{name}", type=NodeType.TOOL, label=name)


def _file_node(path: str) -> GraphNode:
    return GraphNode(id=f"file:{path}", type=NodeType.FILE, label=path)


def _source_node_id(source: dict) -> str | None:
    key = source.get("url") or source.get("file") or source.get("query")
    return f"source:{key}" if key else None


def build_context_graph(entries: list[TimelineEntry]) -> ContextGraph:
    graph = ContextGraph()
    # Needed to connect a spawned child's `agent_start` entry (which only
    # knows its OWN agent_id + parent_run_id) back to the parent's agent
    # node (identified by agent_id, not run_id) — one pass to learn every
    # run's agent_id before wiring spawn edges in the main pass below.
    run_to_agent: dict[str, str] = {}
    for entry in entries:
        run_to_agent.setdefault(entry.run_id, entry.agent_id)

    ordered = sorted(entries, key=lambda e: e.sequence_id)
    for entry in ordered:
        graph.add_node(_agent_node(entry))
        agent_node_id = f"agent:{entry.agent_id}"

        if entry.event_type == "agent_start" and entry.parent_run_id:
            parent_agent_id = run_to_agent.get(entry.parent_run_id)
            if parent_agent_id:
                parent_node_id = f"agent:{parent_agent_id}"
                graph.add_node(GraphNode(id=parent_node_id, type=NodeType.AGENT, label=parent_agent_id))
                graph.add_edge(GraphEdge(
                    source_id=parent_node_id, target_id=agent_node_id,
                    action="spawned", run_id=entry.run_id, timestamp=entry.timestamp,
                ))

        if entry.event_type in ("tool_call", "tool_blocked"):
            tool_name = entry.detail.get("tool")
            if not tool_name:
                continue
            tool_node_id = f"tool:{tool_name}"
            graph.add_node(_tool_node(tool_name))
            graph.add_edge(GraphEdge(
                source_id=agent_node_id, target_id=tool_node_id,
                action="blocked" if entry.event_type == "tool_blocked" else "called",
                run_id=entry.run_id, timestamp=entry.timestamp,
                metadata={"reason": entry.detail["reason"]} if entry.event_type == "tool_blocked" else {},
            ))

            path_arg = _FILE_ARG_TOOLS.get(tool_name)
            args = entry.detail.get("args")
            if path_arg and isinstance(args, dict) and args.get(path_arg):
                path = args[path_arg]
                file_node_id = f"file:{path}"
                graph.add_node(_file_node(path))
                graph.add_edge(GraphEdge(
                    source_id=tool_node_id, target_id=file_node_id,
                    action="touched", run_id=entry.run_id, timestamp=entry.timestamp,
                ))

        elif entry.event_type == "tool_result_sources":
            tool_name = entry.detail.get("tool")
            if not tool_name:
                continue
            tool_node_id = f"tool:{tool_name}"
            graph.add_node(_tool_node(tool_name))
            for source in entry.detail.get("sources", []):
                source_node_id = _source_node_id(source)
                if source_node_id is None:
                    continue
                label = source.get("title") or source.get("url") or source.get("file") or source.get("query") or source_node_id
                graph.add_node(GraphNode(id=source_node_id, type=NodeType.SOURCE, label=label, metadata=source))
                graph.add_edge(GraphEdge(
                    source_id=tool_node_id, target_id=source_node_id,
                    action="cited", run_id=entry.run_id, timestamp=entry.timestamp,
                ))

    return graph
