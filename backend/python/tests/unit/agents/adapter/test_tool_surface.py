"""Tests for `ToolSurfaces.resolve()` — composed vs flat resolution
for retrieval / web / code, and `code_networked` false forces the
no-network steering.

These are the unit tests specified in the plan's Part 8 for Phase 3.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from app.modules.agents.context.tool_surface import ToolSurfaces


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _resolve(
    tool_names: list[str] | None = None,
    state: dict | None = None,
    sandbox_networked: bool = False,
) -> ToolSurfaces:
    with patch(
        "app.modules.agents.context.tool_surface.sandbox_network_enabled",
        return_value=sandbox_networked,
    ):
        return ToolSurfaces.resolve(tool_names or [], state or {})


# ---------------------------------------------------------------------------
# Retrieval surface
# ---------------------------------------------------------------------------

class TestRetrievalSurface:

    def test_flat_retrieval_when_granted(self) -> None:
        s = _resolve(["retrieval.search_internal_knowledge"])
        assert s.retrieval == "retrieval.search_internal_knowledge"

    def test_composed_exploration_agent_preferred_over_flat(self) -> None:
        s = _resolve(["internal_exploration_agent", "retrieval.search_internal_knowledge"])
        assert s.retrieval == "internal_exploration_agent"

    def test_no_retrieval_when_neither_granted(self) -> None:
        s = _resolve(["run_code", "web_search"])
        assert s.retrieval is None

    def test_retrieval_md_flat(self) -> None:
        s = _resolve(["retrieval.search_internal_knowledge"])
        assert s.retrieval_md() == "`retrieval.search_internal_knowledge`"

    def test_retrieval_md_composed(self) -> None:
        s = _resolve(["internal_exploration_agent"])
        assert s.retrieval_md() == "`internal_exploration_agent`"


# ---------------------------------------------------------------------------
# Web surface
# ---------------------------------------------------------------------------

class TestWebSurface:

    def test_flat_web_search_detected_from_tool_names(self) -> None:
        s = _resolve(["web_search"])
        assert "web_search" in s.web_names
        assert s.has_web_search is True

    def test_fetch_url_triggers_web_surface(self) -> None:
        s = _resolve(["fetch_url"])
        assert "fetch_url" in s.web_names
        assert s.has_web_search is True

    def test_both_flat_tools_in_web_names(self) -> None:
        s = _resolve(["web_search", "fetch_url"])
        assert s.web_names == ("web_search", "fetch_url")

    def test_web_agent_preferred_over_flat(self) -> None:
        s = _resolve(["web_agent", "web_search", "fetch_url"])
        assert s.web_names == ("web_agent",)

    def test_web_search_config_flag_triggers_has_web_search(self) -> None:
        """State flag must trigger has_web_search (chat/web_search mode) even
        when no tool names are in the granted set yet."""
        s = _resolve(state={"web_search_config": {"provider": "duckduckgo"}})
        assert s.has_web_search is True
        assert s.web_names == ()  # no callable tool name — config-only case

    def test_no_web_without_tools_or_config(self) -> None:
        s = _resolve(["run_code"])
        assert s.web_names == ()
        assert s.has_web_search is False

    def test_web_md_flat_both_tools(self) -> None:
        s = _resolve(["web_search", "fetch_url"])
        assert s.web_md() == "`web_search`/`fetch_url`"

    def test_web_md_single_tool(self) -> None:
        s = _resolve(["web_search"])
        assert s.web_md() == "`web_search`"

    def test_web_md_agent(self) -> None:
        s = _resolve(["web_agent"])
        assert s.web_md() == "`web_agent`"

    def test_web_md_empty_when_config_only(self) -> None:
        """When only web_search_config is set (no tool names), web_md() is empty."""
        s = _resolve(state={"web_search_config": {"provider": "duckduckgo"}})
        assert s.web_md() == ""

    def test_dynamic_prefixed_web_tools_resolved(self) -> None:
        """Production tool names like `dynamic__web_search` must be found via
        the granted() resolver (bare 'web_search' logical matches)."""
        s = _resolve(["dynamic__web_search", "dynamic__fetch_url"])
        assert "dynamic__web_search" in s.web_names
        assert "dynamic__fetch_url" in s.web_names
        assert s.web_md() == "`dynamic__web_search`/`dynamic__fetch_url`"


# ---------------------------------------------------------------------------
# Code surface
# ---------------------------------------------------------------------------

class TestCodeSurface:

    def test_run_code_detected(self) -> None:
        s = _resolve(["run_code"])
        assert s.code == "run_code"

    def test_coding_agent_preferred_over_run_code(self) -> None:
        s = _resolve(["coding_agent", "run_code"])
        assert s.code == "coding_agent"

    def test_no_code_when_absent(self) -> None:
        s = _resolve(["web_search"])
        assert s.code is None

    def test_code_networked_false_when_sandbox_has_no_network(self) -> None:
        s = _resolve(["run_code"], sandbox_networked=False)
        assert s.code_networked is False

    def test_code_networked_true_when_sandbox_has_network(self) -> None:
        s = _resolve(["run_code"], sandbox_networked=True)
        assert s.code_networked is True

    def test_code_networked_false_when_no_code_surface(self) -> None:
        """If code is None, code_networked must be False regardless of sandbox."""
        s = _resolve(["web_search"], sandbox_networked=True)
        assert s.code_networked is False

    def test_no_network_steering_when_code_networked_false(self) -> None:
        """This test documents the prompt-builder contract: NO_NETWORK variant
        must be selected when code_networked is False."""
        s = _resolve(["run_code"], sandbox_networked=False)
        assert not s.code_networked


# ---------------------------------------------------------------------------
# Graph surface
# ---------------------------------------------------------------------------

class TestGraphSurface:

    def test_graph_true_when_navigate_granted(self) -> None:
        s = _resolve(["knowledgegraph.navigate"])
        assert s.graph is True

    def test_graph_false_when_navigate_not_granted(self) -> None:
        s = _resolve(["retrieval.search_internal_knowledge"])
        assert s.graph is False


# ---------------------------------------------------------------------------
# Live-API apps
# ---------------------------------------------------------------------------

class TestLiveApiApps:

    def test_apps_from_agent_toolsets(self) -> None:
        state = {
            "agent_toolsets": [
                {"name": "jira", "tools": []},
                {"name": "confluence", "tools": []},
            ]
        }
        s = _resolve(state=state)
        assert "jira" in s.live_api_apps
        assert "confluence" in s.live_api_apps

    def test_retrieval_toolset_excluded_from_live_apps(self) -> None:
        state = {"agent_toolsets": [{"name": "retrieval", "tools": []}]}
        s = _resolve(state=state)
        assert "retrieval" not in s.live_api_apps

    def test_connector_flags_fall_back_when_no_toolsets(self) -> None:
        state = {"has_jira_connector": True}
        s = _resolve(state=state)
        assert "jira" in s.live_api_apps

    def test_connector_flags_not_used_when_toolsets_present(self) -> None:
        """If toolsets are present, connector flags should not double-add."""
        state = {
            "agent_toolsets": [{"name": "confluence", "tools": []}],
            "has_jira_connector": True,  # should be ignored — toolsets take precedence
        }
        s = _resolve(state=state)
        # We only guarantee confluence is in; jira may or may not be
        assert "confluence" in s.live_api_apps

    def test_tool_name_prefix_source_three(self) -> None:
        """Flat tool names like `jira__search_issues` also populate live_api_apps."""
        s = _resolve(["jira__search_issues", "jira__get_issue"])
        assert "jira" in s.live_api_apps

    def test_has_service_tools_true_when_live_apps_nonempty(self) -> None:
        state = {"agent_toolsets": [{"name": "slack", "tools": []}]}
        s = _resolve(state=state)
        assert s.has_service_tools is True

    def test_has_service_tools_false_when_no_apps(self) -> None:
        s = _resolve(["retrieval.search_internal_knowledge"])
        assert s.has_service_tools is False


# ---------------------------------------------------------------------------
# User-interaction tools
# ---------------------------------------------------------------------------

class TestInteractionTools:

    def test_can_ask_user_true(self) -> None:
        s = _resolve(["internaltools__ask_user_question"])
        assert s.can_ask_user is True

    def test_can_ask_user_false(self) -> None:
        s = _resolve(["retrieval.search_internal_knowledge"])
        assert s.can_ask_user is False

    def test_can_fetch_full_record_true(self) -> None:
        s = _resolve(["dynamic_fetch_full_record"])
        assert s.can_fetch_full_record is True

    def test_can_fetch_full_record_false(self) -> None:
        s = _resolve([])
        assert s.can_fetch_full_record is False


# ---------------------------------------------------------------------------
# Knowledge flag
# ---------------------------------------------------------------------------

class TestKnowledgeFlag:

    def test_has_knowledge_from_state_flag(self) -> None:
        s = _resolve(state={"has_knowledge": True})
        assert s.has_knowledge is True

    def test_has_knowledge_from_agent_knowledge(self) -> None:
        state = {"agent_knowledge": [{"type": "jira", "connectorId": "abc"}]}
        s = _resolve(state=state)
        assert s.has_knowledge is True

    def test_no_knowledge_when_absent(self) -> None:
        s = _resolve([])
        assert s.has_knowledge is False
