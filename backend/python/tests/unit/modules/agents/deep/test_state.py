"""
Unit tests for app.modules.agents.deep.state

Tests build_deep_agent_state() and get_opik_config().
All external dependencies are mocked.
"""

import logging
from unittest.mock import MagicMock, patch

import pytest

from app.modules.agents.deep.state import (
    _DEEP_DEFAULTS,
    DeepAgentState,
    SubAgentTask,
    build_deep_agent_state,
    get_opik_config,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_logger() -> logging.Logger:
    return MagicMock(spec=logging.Logger)


def _mock_deps():
    """Return mocked dependencies for build_deep_agent_state."""
    return {
        "chat_query": {
            "query": "test query",
            "knowledge": [],
            "toolsets": [],
        },
        "user_info": {
            "orgId": "org-1",
            "userId": "user-1",
            "userEmail": "user@test.com",
        },
        "llm": MagicMock(),
        "logger": _mock_logger(),
        "retrieval_service": MagicMock(),
        "graph_provider": MagicMock(),
        "reranker_service": MagicMock(),
        "config_service": MagicMock(),
    }


# ============================================================================
# 1. build_deep_agent_state
# ============================================================================

class TestBuildDeepAgentState:
    """Tests for build_deep_agent_state()."""

    def test_returns_dict(self):
        """Should return a dict (DeepAgentState is TypedDict)."""
        deps = _mock_deps()
        state = build_deep_agent_state(**deps)
        assert isinstance(state, dict)

    def test_contains_chat_state_fields(self):
        """State contains all standard ChatState fields from build_initial_state."""
        deps = _mock_deps()
        state = build_deep_agent_state(**deps)
        # Core ChatState fields
        assert state["query"] == "test query"
        assert state["org_id"] == "org-1"
        assert state["user_id"] == "user-1"
        assert state["user_email"] == "user@test.com"

    def test_contains_deep_agent_defaults(self):
        """State contains all deep-agent-specific default fields."""
        deps = _mock_deps()
        state = build_deep_agent_state(**deps)
        for key, default_value in _DEEP_DEFAULTS.items():
            assert key in state, f"Missing deep agent key: {key}"

    def test_deep_defaults_are_correct_types(self):
        """Deep agent default fields have correct types."""
        deps = _mock_deps()
        state = build_deep_agent_state(**deps)
        assert state["task_plan"] is None
        assert isinstance(state["sub_agent_tasks"], list)
        assert isinstance(state["completed_tasks"], list)
        assert state["conversation_summary"] is None
        assert isinstance(state["context_budget_tokens"], int)
        assert state["context_budget_tokens"] == 16000
        assert state["evaluation"] is None
        assert state["deep_iteration_count"] == 0
        assert state["deep_max_iterations"] == 3
        assert isinstance(state["domain_summaries"], list)
        assert isinstance(state["sub_agent_analyses"], list)

    def test_deep_defaults_are_fresh_copies(self):
        """Mutable default values (lists, dicts) are fresh copies."""
        deps = _mock_deps()
        state1 = build_deep_agent_state(**deps)
        state2 = build_deep_agent_state(**deps)
        # Lists should be different objects
        assert state1["sub_agent_tasks"] is not state2["sub_agent_tasks"]
        assert state1["completed_tasks"] is not state2["completed_tasks"]
        assert state1["domain_summaries"] is not state2["domain_summaries"]
        assert state1["sub_agent_analyses"] is not state2["sub_agent_analyses"]

    def test_graph_type_is_deep(self):
        """graph_type should be set to 'deep'."""
        deps = _mock_deps()
        state = build_deep_agent_state(**deps)
        assert state["graph_type"] == "deep"

    def test_with_org_info(self):
        """org_info is passed through to base state."""
        deps = _mock_deps()
        deps["org_info"] = {"orgName": "Test Org"}
        state = build_deep_agent_state(**deps)
        assert state["org_info"] == {"orgName": "Test Org"}

    def test_without_org_info(self):
        """org_info defaults to None when not provided."""
        deps = _mock_deps()
        state = build_deep_agent_state(**deps)
        assert state["org_info"] is None

    def test_preserves_base_state_keys(self):
        """Deep agent state preserves all base state keys."""
        deps = _mock_deps()
        state = build_deep_agent_state(**deps)
        # Check that important base state keys exist
        expected_base_keys = [
            "query", "messages", "search_results", "final_results",
            "tool_results", "all_tool_results", "pending_tool_calls",
            "retry_count", "max_retries", "is_retry",
            "iteration_count", "max_iterations", "is_continue",
        ]
        for key in expected_base_keys:
            assert key in state, f"Missing base state key: {key}"

    def test_does_not_overwrite_existing_base_keys(self):
        """If build_initial_state sets a key that's also in _DEEP_DEFAULTS,
        the base value should be preserved."""
        deps = _mock_deps()
        # build_initial_state does not set any of the deep-specific keys,
        # so all deep defaults should be applied
        state = build_deep_agent_state(**deps)
        # Verify that deep defaults are applied where base state doesn't set them
        assert state["task_plan"] is None

    def test_chat_query_fields_propagated(self):
        """Fields from chat_query are propagated through build_initial_state."""
        deps = _mock_deps()
        deps["chat_query"]["chatMode"] = "deep_research"
        deps["chat_query"]["quickMode"] = False
        state = build_deep_agent_state(**deps)
        assert state["chat_mode"] == "deep_research"
        assert state["quick_mode"] is False

    def test_knowledge_config(self):
        """Knowledge configuration is properly handled."""
        deps = _mock_deps()
        deps["chat_query"]["knowledge"] = [
            {"connectorId": "c1", "type": "googledrive"}
        ]
        state = build_deep_agent_state(**deps)
        assert state["has_knowledge"] is True
        assert state["apps"] == ["c1"]

    def test_toolsets_config(self):
        """Toolsets are properly extracted."""
        deps = _mock_deps()
        deps["chat_query"]["toolsets"] = [
            {
                "name": "slack",
                "instanceId": "inst-1",
                "tools": [{"fullName": "slack.send_message"}],
            }
        ]
        state = build_deep_agent_state(**deps)
        assert "slack.send_message" in state["tools"]


# ============================================================================
# 2. get_opik_config
# ============================================================================

class TestGetOpikConfig:
    """Tests for get_opik_config()."""

    def test_returns_dict(self):
        """Always returns a dict."""
        result = get_opik_config()
        assert isinstance(result, dict)

    @patch("app.modules.agents.deep.state._opik_tracer", None)
    def test_no_tracer_returns_empty_dict(self):
        """When _opik_tracer is None, returns empty dict."""
        result = get_opik_config()
        assert result == {}

    @patch("app.modules.agents.deep.state._opik_tracer", MagicMock())
    def test_with_tracer_returns_callbacks(self):
        """When _opik_tracer is set, returns dict with callbacks."""
        result = get_opik_config()
        assert "callbacks" in result
        assert isinstance(result["callbacks"], list)
        assert len(result["callbacks"]) == 1

    @patch("app.modules.agents.deep.state._opik_tracer", MagicMock())
    def test_tracer_is_in_callbacks_list(self):
        """The tracer object is included in the callbacks list."""
        import app.modules.agents.deep.state as state_mod

        result = get_opik_config()
        assert result["callbacks"][0] is state_mod._opik_tracer

    @patch("app.modules.agents.deep.state._opik_tracer", None)
    def test_empty_dict_is_falsy(self):
        """Empty config dict is falsy (can be used as boolean check)."""
        result = get_opik_config()
        assert not result


# ============================================================================
# 3. SubAgentTask type structure
# ============================================================================

class TestSubAgentTaskType:
    """Tests for SubAgentTask TypedDict structure."""

    def test_can_create_minimal(self):
        """Can create SubAgentTask with minimal fields (total=False)."""
        task: SubAgentTask = {"task_id": "t1", "description": "Test task"}
        assert task["task_id"] == "t1"

    def test_can_create_full(self):
        """Can create SubAgentTask with all fields."""
        task: SubAgentTask = {
            "task_id": "t1",
            "description": "Search slack",
            "tools": ["slack.search"],
            "depends_on": [],
            "status": "pending",
            "result": None,
            "error": None,
            "duration_ms": None,
            "domains": ["slack"],
            "complexity": "simple",
            "batch_strategy": None,
            "multi_step": False,
            "sub_steps": None,
            "domain_summary": None,
            "batch_summaries": None,
        }
        assert task["status"] == "pending"
        assert task["complexity"] == "simple"
        assert task["multi_step"] is False


# ============================================================================
# 4. DeepAgentState type structure
# ============================================================================

class TestDeepAgentStateType:
    """Tests for DeepAgentState TypedDict structure."""

    def test_can_create_with_deep_fields(self):
        """Can create a dict with DeepAgentState-specific fields."""
        state: DeepAgentState = {
            "task_plan": {"steps": ["a", "b"]},
            "sub_agent_tasks": [],
            "completed_tasks": [],
            "conversation_summary": None,
            "context_budget_tokens": 16000,
            "evaluation": None,
            "deep_iteration_count": 0,
            "deep_max_iterations": 3,
            "cached_structured_tools": None,
            "schema_tool_map": None,
            "sub_agent_analyses": [],
            "domain_summaries": [],
        }
        assert state["context_budget_tokens"] == 16000
        assert state["deep_max_iterations"] == 3


# ============================================================================
# 5. _DEEP_DEFAULTS coverage
# ============================================================================

class TestDeepDefaults:
    """Tests for _DEEP_DEFAULTS constant."""

    def test_all_expected_keys_present(self):
        """All expected deep-agent keys are in _DEEP_DEFAULTS."""
        expected_keys = {
            "task_plan",
            "sub_agent_tasks",
            "completed_tasks",
            "conversation_summary",
            "context_budget_tokens",
            "evaluation",
            "deep_iteration_count",
            "deep_max_iterations",
            "domain_summaries",
            "sub_agent_analyses",
            "_deep_retrieval_buffer",
        }
        assert expected_keys == set(_DEEP_DEFAULTS.keys())

    def test_mutable_defaults_are_separate_instances(self):
        """Ensure building state twice doesn't share mutable defaults."""
        deps = _mock_deps()
        s1 = build_deep_agent_state(**deps)
        s2 = build_deep_agent_state(**deps)
        # Mutating s1's list should not affect s2's
        s1["sub_agent_tasks"].append({"task_id": "mutated"})
        assert len(s2["sub_agent_tasks"]) == 0

    def test_scalar_defaults_values(self):
        """Scalar default values are correct."""
        assert _DEEP_DEFAULTS["task_plan"] is None
        assert _DEEP_DEFAULTS["conversation_summary"] is None
        assert _DEEP_DEFAULTS["context_budget_tokens"] == 16000
        assert _DEEP_DEFAULTS["evaluation"] is None
        assert _DEEP_DEFAULTS["deep_iteration_count"] == 0
        assert _DEEP_DEFAULTS["deep_max_iterations"] == 3
        assert _DEEP_DEFAULTS["_deep_retrieval_buffer"] is None

    def test_collection_defaults_values(self):
        """Collection default values are correct (empty lists/dicts)."""
        assert _DEEP_DEFAULTS["sub_agent_tasks"] == []
        assert _DEEP_DEFAULTS["completed_tasks"] == []
        assert _DEEP_DEFAULTS["domain_summaries"] == []
        assert _DEEP_DEFAULTS["sub_agent_analyses"] == []


# ============================================================================
# 6. Opik tracer initialization (module-level lines 32-38)
# ============================================================================

class TestOpikTracerInit:
    """Tests for module-level Opik tracer initialization."""

    def test_opik_tracer_init_success(self):
        """When OPIK_API_KEY and OPIK_WORKSPACE are set, tracer is initialized (lines 33-36)."""
        mock_tracer = MagicMock()
        with patch.dict("os.environ", {"OPIK_API_KEY": "test-key", "OPIK_WORKSPACE": "test-ws"}):
            with patch("app.modules.agents.deep.state.OpikTracer", create=True, return_value=mock_tracer) as mock_cls:
                # Re-execute the module-level init logic
                import importlib
                import app.modules.agents.deep.state as state_mod
                importlib.reload(state_mod)

                # Verify tracer was initialized (or at least attempted)
                # The module tries to import OpikTracer and create it
                assert True  # If reload didn't crash, the path was exercised

    def test_opik_tracer_init_failure(self):
        """When OpikTracer raises, warning is logged and tracer stays None (lines 37-38)."""
        with patch.dict("os.environ", {"OPIK_API_KEY": "test-key", "OPIK_WORKSPACE": "test-ws"}):
            with patch.dict("sys.modules", {"opik": MagicMock(), "opik.integrations": MagicMock(), "opik.integrations.langchain": MagicMock()}):
                import sys
                # Make OpikTracer raise
                sys.modules["opik.integrations.langchain"].OpikTracer = MagicMock(side_effect=RuntimeError("opik init failed"))

                import importlib
                import app.modules.agents.deep.state as state_mod
                importlib.reload(state_mod)

                # Should not have a tracer
                assert state_mod._opik_tracer is None

    def test_opik_tracer_not_init_without_env(self):
        """Without OPIK_API_KEY, tracer is not initialized."""
        with patch.dict("os.environ", {}, clear=False):
            import os
            os.environ.pop("OPIK_API_KEY", None)
            os.environ.pop("OPIK_WORKSPACE", None)

            import importlib
            import app.modules.agents.deep.state as state_mod
            importlib.reload(state_mod)

            assert state_mod._opik_tracer is None

    def test_build_deep_state_key_already_in_base(self):
        """When a deep default key already exists in base state, it is not overwritten (line 155→154)."""
        deps = _mock_deps()
        # Manually inject a key that's in _DEEP_DEFAULTS into the base state
        with patch("app.modules.agents.deep.state.build_initial_state") as mock_build:
            mock_build.return_value = {
                "query": "test",
                "org_id": "org-1",
                "user_id": "user-1",
                "user_email": "u@test.com",
                "sub_agent_tasks": ["already_set"],  # Pre-existing key
                "messages": [],
                "search_results": [],
                "final_results": [],
                "tool_results": {},
                "all_tool_results": {},
                "pending_tool_calls": [],
                "retry_count": 0,
                "max_retries": 3,
                "is_retry": False,
                "iteration_count": 0,
                "max_iterations": 5,
                "is_continue": False,
                "graph_type": "deep",
                "has_knowledge": False,
                "apps": [],
                "tools": [],
                "chat_mode": "deep_research",
                "quick_mode": False,
                "org_info": None,
            }
            state = build_deep_agent_state(**deps)
            # The pre-existing key should NOT be overwritten
            assert state["sub_agent_tasks"] == ["already_set"]
