"""Tests for `context/graph_builder.py::build_context_graph` — a pure
function turning a flat list of `TimelineEntry` into a `ContextGraph` of
agents/tools/files/sources and the edges connecting them."""

from __future__ import annotations

from app.agent_loop_lib.context.graph import NodeType
from app.agent_loop_lib.context.graph_builder import build_context_graph
from app.agent_loop_lib.modules.stores.state.base import AgentStatus
from app.agent_loop_lib.modules.stores.timeline.base import TimelineEntry


def _entry(
    *,
    seq: int,
    event_type: str,
    run_id: str = "run-1",
    agent_id: str = "agent-1",
    parent_run_id: str | None = None,
    detail: dict | None = None,
    role_name: str = "",
) -> TimelineEntry:
    return TimelineEntry(
        sequence_id=seq,
        trace_id="trace-1",
        run_id=run_id,
        agent_id=agent_id,
        parent_run_id=parent_run_id,
        timestamp=f"t{seq}",
        status=AgentStatus.RUNNING_TOOL,
        event_type=event_type,
        summary="summary",
        detail=detail or {},
        role_name=role_name,
    )


class TestEmptyAndTrivialInputs:
    def test_empty_entries_produce_empty_graph(self) -> None:
        graph = build_context_graph([])
        assert graph.nodes == {}
        assert graph.edges == []

    def test_single_entry_adds_only_an_agent_node(self) -> None:
        entry = _entry(seq=1, event_type="agent_start", role_name="researcher")
        graph = build_context_graph([entry])

        assert list(graph.nodes.keys()) == ["agent:agent-1"]
        node = graph.nodes["agent:agent-1"]
        assert node.type == NodeType.AGENT
        assert node.label == "researcher"
        assert node.metadata == {"run_id": "run-1"}
        assert graph.edges == []

    def test_agent_node_label_falls_back_to_agent_id_when_role_name_blank(self) -> None:
        entry = _entry(seq=1, event_type="agent_start", role_name="")
        graph = build_context_graph([entry])
        assert graph.nodes["agent:agent-1"].label == "agent-1"


class TestAgentSpawnEdges:
    def test_agent_start_with_known_parent_creates_spawn_edge(self) -> None:
        parent = _entry(seq=1, event_type="agent_start", run_id="run-parent", agent_id="agent-parent")
        child = _entry(
            seq=2, event_type="agent_start", run_id="run-child", agent_id="agent-child",
            parent_run_id="run-parent",
        )
        graph = build_context_graph([parent, child])

        assert "agent:agent-parent" in graph.nodes
        assert "agent:agent-child" in graph.nodes
        spawn_edges = [e for e in graph.edges if e.action == "spawned"]
        assert len(spawn_edges) == 1
        edge = spawn_edges[0]
        assert edge.source_id == "agent:agent-parent"
        assert edge.target_id == "agent:agent-child"
        assert edge.run_id == "run-child"

    def test_agent_start_with_unknown_parent_run_id_adds_no_edge(self) -> None:
        """The parent's `agent_start` entry was never seen (e.g. truncated
        timeline query), so `run_to_agent` has no mapping — must degrade
        gracefully rather than crash on a missing lookup."""
        child = _entry(
            seq=1, event_type="agent_start", run_id="run-child", agent_id="agent-child",
            parent_run_id="run-parent-not-in-entries",
        )
        graph = build_context_graph([child])

        assert list(graph.nodes.keys()) == ["agent:agent-child"]
        assert graph.edges == []

    def test_agent_start_without_parent_run_id_adds_no_edge(self) -> None:
        entry = _entry(seq=1, event_type="agent_start", parent_run_id=None)
        graph = build_context_graph([entry])
        assert graph.edges == []

    def test_non_agent_start_event_never_creates_spawn_edge_even_with_parent(self) -> None:
        entry = _entry(seq=1, event_type="turn_start", parent_run_id="run-parent")
        graph = build_context_graph([entry])
        assert graph.edges == []


class TestToolCallEdges:
    def test_tool_call_without_tool_name_is_skipped(self) -> None:
        entry = _entry(seq=1, event_type="tool_call", detail={})
        graph = build_context_graph([entry])
        assert graph.nodes_of_type(NodeType.TOOL) == []

    def test_tool_call_adds_tool_node_and_called_edge(self) -> None:
        entry = _entry(seq=1, event_type="tool_call", detail={"tool": "web_search"})
        graph = build_context_graph([entry])

        assert "tool:web_search" in graph.nodes
        edges = graph.edges_from("agent:agent-1")
        assert len(edges) == 1
        assert edges[0].target_id == "tool:web_search"
        assert edges[0].action == "called"
        assert edges[0].metadata == {}

    def test_tool_blocked_uses_blocked_action_and_carries_reason(self) -> None:
        entry = _entry(
            seq=1, event_type="tool_blocked",
            detail={"tool": "delete_issue", "reason": "not approved"},
        )
        graph = build_context_graph([entry])

        edge = graph.edges_from("agent:agent-1")[0]
        assert edge.action == "blocked"
        assert edge.metadata == {"reason": "not approved"}

    def test_allowlisted_tool_with_path_arg_adds_file_node_and_edge(self) -> None:
        entry = _entry(
            seq=1, event_type="tool_call",
            detail={"tool": "read_file", "args": {"path": "/src/main.py"}},
        )
        graph = build_context_graph([entry])

        assert "file:/src/main.py" in graph.nodes
        file_edges = graph.edges_from("tool:read_file")
        assert len(file_edges) == 1
        assert file_edges[0].target_id == "file:/src/main.py"
        assert file_edges[0].action == "touched"

    def test_non_allowlisted_tool_never_adds_a_file_node(self) -> None:
        entry = _entry(
            seq=1, event_type="tool_call",
            detail={"tool": "web_search", "args": {"path": "/should/not/matter"}},
        )
        graph = build_context_graph([entry])
        assert graph.nodes_of_type(NodeType.FILE) == []

    def test_allowlisted_tool_with_missing_args_adds_no_file_node(self) -> None:
        entry = _entry(seq=1, event_type="tool_call", detail={"tool": "write_file"})
        graph = build_context_graph([entry])
        assert graph.nodes_of_type(NodeType.FILE) == []

    def test_allowlisted_tool_with_non_dict_args_adds_no_file_node(self) -> None:
        entry = _entry(
            seq=1, event_type="tool_call",
            detail={"tool": "edit_file", "args": "not-a-dict"},
        )
        graph = build_context_graph([entry])
        assert graph.nodes_of_type(NodeType.FILE) == []

    def test_allowlisted_tool_with_falsy_path_value_adds_no_file_node(self) -> None:
        entry = _entry(
            seq=1, event_type="tool_call",
            detail={"tool": "read_file", "args": {"path": ""}},
        )
        graph = build_context_graph([entry])
        assert graph.nodes_of_type(NodeType.FILE) == []

    def test_repeated_tool_call_does_not_duplicate_the_tool_node(self) -> None:
        entries = [
            _entry(seq=1, event_type="tool_call", detail={"tool": "web_search"}),
            _entry(seq=2, event_type="tool_call", detail={"tool": "web_search"}),
        ]
        graph = build_context_graph(entries)
        assert len(graph.nodes_of_type(NodeType.TOOL)) == 1
        assert len(graph.edges_from("agent:agent-1")) == 2


class TestToolResultSourcesEdges:
    def test_missing_tool_name_is_skipped(self) -> None:
        entry = _entry(seq=1, event_type="tool_result_sources", detail={"sources": [{"url": "x"}]})
        graph = build_context_graph([entry])
        assert graph.nodes_of_type(NodeType.SOURCE) == []

    def test_source_with_url_creates_source_node_and_cited_edge(self) -> None:
        entry = _entry(
            seq=1, event_type="tool_result_sources",
            detail={"tool": "web_search", "sources": [{"url": "https://example.com", "title": "Example"}]},
        )
        graph = build_context_graph([entry])

        node = graph.nodes["source:https://example.com"]
        assert node.type == NodeType.SOURCE
        assert node.label == "Example"
        edges = graph.edges_from("tool:web_search")
        assert len(edges) == 1
        assert edges[0].action == "cited"
        assert edges[0].target_id == "source:https://example.com"

    def test_source_without_title_falls_back_to_url_then_file_then_query(self) -> None:
        entry = _entry(
            seq=1, event_type="tool_result_sources",
            detail={"tool": "kb_query", "sources": [{"query": "revenue Q3"}]},
        )
        graph = build_context_graph([entry])
        node = graph.nodes["source:revenue Q3"]
        assert node.label == "revenue Q3"

    def test_source_with_none_of_url_file_query_is_skipped(self) -> None:
        entry = _entry(
            seq=1, event_type="tool_result_sources",
            detail={"tool": "web_search", "sources": [{"title": "orphan citation"}]},
        )
        graph = build_context_graph([entry])
        assert graph.nodes_of_type(NodeType.SOURCE) == []

    def test_no_sources_key_defaults_to_empty_list(self) -> None:
        entry = _entry(seq=1, event_type="tool_result_sources", detail={"tool": "web_search"})
        graph = build_context_graph([entry])
        assert "tool:web_search" in graph.nodes
        assert graph.nodes_of_type(NodeType.SOURCE) == []


class TestOrdering:
    def test_entries_are_processed_in_sequence_id_order_regardless_of_input_order(self) -> None:
        first = _entry(seq=1, event_type="tool_call", detail={"tool": "a"})
        second = _entry(seq=2, event_type="tool_call", detail={"tool": "b"})

        graph = build_context_graph([second, first])

        called_tools = [e.target_id for e in graph.edges_from("agent:agent-1")]
        assert called_tools == ["tool:a", "tool:b"]

    def test_run_to_agent_lookup_is_built_before_the_ordered_pass(self) -> None:
        """`run_to_agent` is populated from raw (unsorted) `entries`, so a
        parent's `agent_start` arriving AFTER its child in `sequence_id`
        order (but present in the input) must still resolve."""
        child = _entry(
            seq=1, event_type="agent_start", run_id="run-child", agent_id="agent-child",
            parent_run_id="run-parent",
        )
        parent = _entry(seq=2, event_type="agent_start", run_id="run-parent", agent_id="agent-parent")

        graph = build_context_graph([child, parent])

        spawn_edges = [e for e in graph.edges if e.action == "spawned"]
        assert len(spawn_edges) == 1
        assert spawn_edges[0].source_id == "agent:agent-parent"
        assert spawn_edges[0].target_id == "agent:agent-child"
