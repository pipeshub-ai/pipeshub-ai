"""Unit tests for app.modules.agents.qna.graph — QnA Agent Graph construction.

The graph module imports node functions and ChatState from sibling modules
that have heavy dependency chains.  We intercept those imports via sys.modules
so the graph can be built and inspected without the full runtime environment.
"""

import importlib
import sys
from unittest.mock import MagicMock

import pytest
from typing_extensions import TypedDict


class _DummyState(TypedDict):
    """Minimal TypedDict so that StateGraph can build a valid graph."""
    query: str


@pytest.fixture(autouse=True)
def _mock_qna_graph_imports():
    """Patch sys.modules for all qna-agent sibling modules."""
    mock_nodes = MagicMock()
    mock_chat_state = MagicMock()
    # ChatState must be a proper TypedDict for StateGraph / get_graph()
    mock_chat_state.ChatState = _DummyState

    saved = {}
    modules_to_mock = {
        "app.modules.agents.qna.nodes": mock_nodes,
        "app.modules.agents.qna.chat_state": mock_chat_state,
    }

    for name, mock in modules_to_mock.items():
        saved[name] = sys.modules.get(name)
        sys.modules[name] = mock

    # Remove the graph module itself so reload works fresh
    graph_key = "app.modules.agents.qna.graph"
    saved[graph_key] = sys.modules.pop(graph_key, None)

    yield

    # Restore original modules
    for name, original in saved.items():
        if original is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = original


def _load_graph_module():
    """Import (or reload) the graph module under mocked dependencies."""
    import app.modules.agents.qna.graph as graph_mod
    importlib.reload(graph_mod)
    return graph_mod


class TestCreateAgentGraph:
    """Tests for create_agent_graph()."""

    def test_returns_compiled_graph(self):
        graph_mod = _load_graph_module()
        graph = graph_mod.create_agent_graph()
        assert hasattr(graph, "invoke") or hasattr(graph, "ainvoke")

    def test_graph_has_expected_nodes(self):
        graph_mod = _load_graph_module()
        graph = graph_mod.create_agent_graph()
        node_names = set(graph.nodes.keys())
        for expected in (
            "planner", "execute", "reflect",
            "prepare_retry", "prepare_continue", "respond",
        ):
            assert expected in node_names, f"Missing node: {expected}"

    def test_module_level_agent_graph(self):
        graph_mod = _load_graph_module()
        assert graph_mod.agent_graph is not None

    def test_entry_point_is_planner(self):
        graph_mod = _load_graph_module()
        graph = graph_mod.create_agent_graph()
        graph_repr = graph.get_graph()
        start_targets = [e.target for e in graph_repr.edges if e.source == "__start__"]
        assert "planner" in start_targets

    def test_respond_connects_to_end(self):
        graph_mod = _load_graph_module()
        graph = graph_mod.create_agent_graph()
        graph_repr = graph.get_graph()
        respond_targets = [e.target for e in graph_repr.edges if e.source == "respond"]
        assert "__end__" in respond_targets

    def test_execute_connects_to_reflect(self):
        graph_mod = _load_graph_module()
        graph = graph_mod.create_agent_graph()
        graph_repr = graph.get_graph()
        exec_targets = [e.target for e in graph_repr.edges if e.source == "execute"]
        assert "reflect" in exec_targets

    def test_prepare_retry_connects_to_planner(self):
        graph_mod = _load_graph_module()
        graph = graph_mod.create_agent_graph()
        graph_repr = graph.get_graph()
        retry_targets = [e.target for e in graph_repr.edges if e.source == "prepare_retry"]
        assert "planner" in retry_targets

    def test_prepare_continue_connects_to_planner(self):
        graph_mod = _load_graph_module()
        graph = graph_mod.create_agent_graph()
        graph_repr = graph.get_graph()
        continue_targets = [
            e.target for e in graph_repr.edges if e.source == "prepare_continue"
        ]
        assert "planner" in continue_targets

    def test_planner_has_conditional_edges(self):
        """Planner has conditional edges to execute or respond."""
        graph_mod = _load_graph_module()
        graph = graph_mod.create_agent_graph()
        graph_repr = graph.get_graph()
        planner_targets = [e.target for e in graph_repr.edges if e.source == "planner"]
        assert len(planner_targets) >= 2

    def test_reflect_has_conditional_edges(self):
        """Reflect has conditional edges to prepare_retry, prepare_continue, or respond."""
        graph_mod = _load_graph_module()
        graph = graph_mod.create_agent_graph()
        graph_repr = graph.get_graph()
        reflect_targets = [e.target for e in graph_repr.edges if e.source == "reflect"]
        assert len(reflect_targets) >= 3


class TestCreateModernAgentGraph:
    """Tests for create_modern_agent_graph()."""

    def test_returns_compiled_graph(self):
        graph_mod = _load_graph_module()
        graph = graph_mod.create_modern_agent_graph()
        assert hasattr(graph, "invoke") or hasattr(graph, "ainvoke")

    def test_graph_has_agent_and_respond_nodes(self):
        graph_mod = _load_graph_module()
        graph = graph_mod.create_modern_agent_graph()
        node_names = set(graph.nodes.keys())
        assert "agent" in node_names
        assert "respond" in node_names

    def test_module_level_modern_graph(self):
        graph_mod = _load_graph_module()
        assert graph_mod.modern_agent_graph is not None

    def test_entry_point_is_agent(self):
        graph_mod = _load_graph_module()
        graph = graph_mod.create_modern_agent_graph()
        graph_repr = graph.get_graph()
        start_targets = [e.target for e in graph_repr.edges if e.source == "__start__"]
        assert "agent" in start_targets

    def test_agent_connects_to_respond(self):
        graph_mod = _load_graph_module()
        graph = graph_mod.create_modern_agent_graph()
        graph_repr = graph.get_graph()
        agent_targets = [e.target for e in graph_repr.edges if e.source == "agent"]
        assert "respond" in agent_targets

    def test_respond_connects_to_end(self):
        graph_mod = _load_graph_module()
        graph = graph_mod.create_modern_agent_graph()
        graph_repr = graph.get_graph()
        respond_targets = [e.target for e in graph_repr.edges if e.source == "respond"]
        assert "__end__" in respond_targets


class TestExports:
    """Tests for __all__ exports."""

    def test_all_exports(self):
        graph_mod = _load_graph_module()
        assert "agent_graph" in graph_mod.__all__
        assert "create_agent_graph" in graph_mod.__all__
        assert "modern_agent_graph" in graph_mod.__all__
        assert "create_modern_agent_graph" in graph_mod.__all__

    def test_all_has_four_entries(self):
        graph_mod = _load_graph_module()
        assert len(graph_mod.__all__) == 4
