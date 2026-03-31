"""Unit tests for app.modules.agents.deep.graph — Deep Agent Graph construction.

The graph module imports node functions and state classes from sibling modules
that have heavy dependency chains (LiteLLM, langchain, etc.).  We intercept
those imports via sys.modules so the graph can be built and inspected without
needing the full runtime environment.
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
def _mock_deep_graph_imports():
    """Patch sys.modules for all deep-agent sibling modules."""
    mock_aggregator = MagicMock()
    mock_orchestrator = MagicMock()
    mock_respond = MagicMock()
    mock_sub_agent = MagicMock()
    mock_state = MagicMock()
    # DeepAgentState must be a proper TypedDict for StateGraph / get_graph()
    mock_state.DeepAgentState = _DummyState

    saved = {}
    modules_to_mock = {
        "app.modules.agents.deep.aggregator": mock_aggregator,
        "app.modules.agents.deep.orchestrator": mock_orchestrator,
        "app.modules.agents.deep.respond": mock_respond,
        "app.modules.agents.deep.sub_agent": mock_sub_agent,
        "app.modules.agents.deep.state": mock_state,
    }

    for name, mock in modules_to_mock.items():
        saved[name] = sys.modules.get(name)
        sys.modules[name] = mock

    # Remove the graph module itself so reload works fresh
    graph_key = "app.modules.agents.deep.graph"
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
    import app.modules.agents.deep.graph as graph_mod
    importlib.reload(graph_mod)
    return graph_mod


class TestCreateDeepAgentGraph:
    """Tests for create_deep_agent_graph()."""

    def test_returns_compiled_graph(self):
        graph_mod = _load_graph_module()
        graph = graph_mod.create_deep_agent_graph()
        assert hasattr(graph, "invoke") or hasattr(graph, "ainvoke")

    def test_graph_has_expected_nodes(self):
        graph_mod = _load_graph_module()
        graph = graph_mod.create_deep_agent_graph()
        node_names = set(graph.nodes.keys())
        for expected in ("orchestrator", "execute_sub_agents", "aggregator", "respond"):
            assert expected in node_names, f"Missing node: {expected}"

    def test_module_level_graph_instance(self):
        graph_mod = _load_graph_module()
        assert graph_mod.deep_agent_graph is not None
        assert hasattr(graph_mod.deep_agent_graph, "invoke") or hasattr(
            graph_mod.deep_agent_graph, "ainvoke"
        )

    def test_all_exports(self):
        graph_mod = _load_graph_module()
        assert "create_deep_agent_graph" in graph_mod.__all__
        assert "deep_agent_graph" in graph_mod.__all__

    def test_entry_point_is_orchestrator(self):
        graph_mod = _load_graph_module()
        graph = graph_mod.create_deep_agent_graph()
        graph_repr = graph.get_graph()
        start_targets = [e.target for e in graph_repr.edges if e.source == "__start__"]
        assert "orchestrator" in start_targets

    def test_respond_connects_to_end(self):
        graph_mod = _load_graph_module()
        graph = graph_mod.create_deep_agent_graph()
        graph_repr = graph.get_graph()
        respond_targets = [e.target for e in graph_repr.edges if e.source == "respond"]
        assert "__end__" in respond_targets

    def test_execute_sub_agents_connects_to_aggregator(self):
        graph_mod = _load_graph_module()
        graph = graph_mod.create_deep_agent_graph()
        graph_repr = graph.get_graph()
        exec_targets = [
            e.target for e in graph_repr.edges if e.source == "execute_sub_agents"
        ]
        assert "aggregator" in exec_targets

    def test_orchestrator_has_conditional_edges(self):
        """Orchestrator has conditional edges to dispatch or respond."""
        graph_mod = _load_graph_module()
        graph = graph_mod.create_deep_agent_graph()
        graph_repr = graph.get_graph()
        orch_targets = [
            e.target for e in graph_repr.edges if e.source == "orchestrator"
        ]
        # Should include both execute_sub_agents and respond via conditional edges
        assert len(orch_targets) >= 2

    def test_aggregator_has_conditional_edges(self):
        """Aggregator has conditional edges for respond, retry, continue."""
        graph_mod = _load_graph_module()
        graph = graph_mod.create_deep_agent_graph()
        graph_repr = graph.get_graph()
        agg_targets = [
            e.target for e in graph_repr.edges if e.source == "aggregator"
        ]
        # Should route to respond and orchestrator (for retry/continue)
        assert len(agg_targets) >= 2
