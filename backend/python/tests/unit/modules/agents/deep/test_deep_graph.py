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
    mock_orchestrator_critic = MagicMock()
    mock_respond = MagicMock()
    mock_sub_agent = MagicMock()
    mock_state = MagicMock()
    # DeepAgentState must be a proper TypedDict for StateGraph / get_graph()
    mock_state.DeepAgentState = _DummyState

    saved = {}
    modules_to_mock = {
        "app.modules.agents.deep.aggregator": mock_aggregator,
        "app.modules.agents.deep.orchestrator": mock_orchestrator,
        "app.modules.agents.deep.orchestrator_critic": mock_orchestrator_critic,
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
        for expected in (
            "orchestrator",
            "critic",
            "execute_sub_agents",
            "aggregator",
            "respond",
        ):
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
        """Orchestrator routes to critic, execute_sub_agents, or respond."""
        graph_mod = _load_graph_module()
        graph = graph_mod.create_deep_agent_graph()
        graph_repr = graph.get_graph()
        orch_targets = {
            e.target for e in graph_repr.edges if e.source == "orchestrator"
        }
        # New routing — first run goes to critic; subsequent runs may bypass
        # straight to execute_sub_agents or respond.
        for expected in ("critic", "execute_sub_agents", "respond"):
            assert expected in orch_targets, f"Missing orchestrator → {expected}"

    def test_critic_has_conditional_edges(self):
        """Critic routes to execute_sub_agents, orchestrator (revise), or respond."""
        graph_mod = _load_graph_module()
        graph = graph_mod.create_deep_agent_graph()
        graph_repr = graph.get_graph()
        critic_targets = {
            e.target for e in graph_repr.edges if e.source == "critic"
        }
        for expected in ("execute_sub_agents", "orchestrator", "respond"):
            assert expected in critic_targets, f"Missing critic → {expected}"

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


class TestRouteAfterOrchestrator:
    """Tests for the new route_after_orchestrator routing function."""

    def test_first_run_goes_to_critic(self):
        graph_mod = _load_graph_module()
        # First run: critic_done is False (or absent).
        state = {"critic_done": False}
        assert graph_mod.route_after_orchestrator(state) == "critic"

    def test_first_run_with_missing_key_goes_to_critic(self):
        """Missing critic_done is treated as not-yet-evaluated."""
        graph_mod = _load_graph_module()
        assert graph_mod.route_after_orchestrator({}) == "critic"

    def test_first_run_direct_answer_bypasses_critic(self):
        """can_answer_directly=True on the first pass skips the critic LLM call."""
        graph_mod = _load_graph_module()
        state = {
            "critic_done": False,
            "execution_plan": {"can_answer_directly": True},
        }
        assert graph_mod.route_after_orchestrator(state) == "respond"

    def test_first_run_non_direct_answer_still_hits_critic(self):
        """can_answer_directly=False on the first pass must still go to the critic."""
        graph_mod = _load_graph_module()
        state = {
            "critic_done": False,
            "execution_plan": {"can_answer_directly": False},
        }
        assert graph_mod.route_after_orchestrator(state) == "critic"

    def test_error_short_circuits_to_respond(self):
        graph_mod = _load_graph_module()
        state = {"error": {"message": "boom"}, "critic_done": False}
        assert graph_mod.route_after_orchestrator(state) == "respond"

    def test_post_critic_delegates_to_should_dispatch(self):
        """After the critic ran, routing delegates to should_dispatch."""
        import sys
        graph_mod = _load_graph_module()
        # The orchestrator module is mocked at the sys.modules level — replace
        # should_dispatch with a deterministic stub so we can verify delegation.
        mock_orchestrator = sys.modules["app.modules.agents.deep.orchestrator"]
        mock_orchestrator.should_dispatch = lambda s: "dispatch"
        # Re-import so the graph module picks up the new should_dispatch ref.
        graph_mod = _load_graph_module()

        state = {"critic_done": True, "error": None}
        assert graph_mod.route_after_orchestrator(state) == "dispatch"

        mock_orchestrator.should_dispatch = lambda s: "respond"
        graph_mod = _load_graph_module()
        assert graph_mod.route_after_orchestrator(state) == "respond"
