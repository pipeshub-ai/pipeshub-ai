"""Tests for agent capabilities gating in app.api.routes.agent.

Covers _parse_agent_capabilities and the ChatQuery.agentCapabilities field.
These tests are pure-unit — no database, no HTTP, no imports of heavy deps.
"""
import pytest


class TestParseAgentCapabilities:
    """Direct unit tests for the _parse_agent_capabilities helper."""

    def _parse(self, raw):
        from app.api.routes.agent import _parse_agent_capabilities

        return _parse_agent_capabilities(raw)

    def test_none_returns_all_enabled(self) -> None:
        caps = self._parse(None)
        assert caps.internal_search is True
        assert caps.web_search is True
        assert caps.deep_search is False

    def test_empty_dict_returns_all_enabled(self) -> None:
        caps = self._parse({})
        assert caps.internal_search is True
        assert caps.web_search is True

    def test_non_dict_returns_all_enabled(self) -> None:
        for bad in ["string", 42, [], True]:
            caps = self._parse(bad)
            assert caps.internal_search is True
            assert caps.web_search is True

    def test_internal_search_false(self) -> None:
        caps = self._parse({"internalSearch": False, "webSearch": True})
        assert caps.internal_search is False
        assert caps.web_search is True

    def test_web_search_false(self) -> None:
        caps = self._parse({"internalSearch": True, "webSearch": False})
        assert caps.internal_search is True
        assert caps.web_search is False

    def test_both_disabled(self) -> None:
        caps = self._parse({"internalSearch": False, "webSearch": False})
        assert caps.internal_search is False
        assert caps.web_search is False

    def test_deep_search_parsed(self) -> None:
        caps = self._parse({"internalSearch": True, "webSearch": True, "deepSearch": True})
        assert caps.deep_search is True

    def test_deep_search_defaults_false(self) -> None:
        caps = self._parse({"internalSearch": True, "webSearch": True})
        assert caps.deep_search is False

    def test_unknown_keys_ignored(self) -> None:
        caps = self._parse({"internalSearch": False, "unknownFutureField": "x"})
        assert caps.internal_search is False

    def test_truthy_non_bool_values_coerced(self) -> None:
        caps = self._parse({"internalSearch": 0, "webSearch": 1})
        assert caps.internal_search is False
        assert caps.web_search is True


class TestChatQueryAgentCapabilities:
    """Verify agentCapabilities field on ChatQuery Pydantic model."""

    def test_defaults_to_none(self) -> None:
        from app.api.routes.agent import ChatQuery

        q = ChatQuery(query="test")
        assert q.agentCapabilities is None

    def test_accepts_full_capabilities_dict(self) -> None:
        from app.api.routes.agent import ChatQuery

        q = ChatQuery(
            query="test",
            agentCapabilities={"internalSearch": False, "webSearch": True},
        )
        assert q.agentCapabilities == {"internalSearch": False, "webSearch": True}

    def test_accepts_none_explicitly(self) -> None:
        from app.api.routes.agent import ChatQuery

        q = ChatQuery(query="test", agentCapabilities=None)
        assert q.agentCapabilities is None


class TestCapabilityGatingLogic:
    """Simulate the gating logic that runs inside chat_stream().

    We replicate the exact conditional block from agent.py to verify the
    semantics without spinning up a full FastAPI app.
    """

    NO_KB_SELECTED_FILTER = "NO_KB_SELECTED"

    def _apply_caps(self, caps, *, web_search_provider="tavilydemo", agent_knowledge=None, filters=None):
        """Replicate the gating block from chat_stream()."""
        if agent_knowledge is None:
            agent_knowledge = ["kb-1", "kb-2"]
        if filters is None:
            filters = {"apps": ["app1"], "kb": ["kb-1"]}

        if not caps.web_search:
            web_search_provider = None
        if not caps.internal_search:
            filters = {"apps": [], "kb": [self.NO_KB_SELECTED_FILTER]}
            agent_knowledge = []

        return web_search_provider, agent_knowledge, filters

    def test_both_enabled_no_changes(self) -> None:
        from app.api.routes.agent import _parse_agent_capabilities

        caps = _parse_agent_capabilities({"internalSearch": True, "webSearch": True})
        provider, knowledge, filt = self._apply_caps(caps)
        assert provider == "tavilydemo"
        assert knowledge == ["kb-1", "kb-2"]
        assert filt == {"apps": ["app1"], "kb": ["kb-1"]}

    def test_web_search_disabled_nullifies_provider(self) -> None:
        from app.api.routes.agent import _parse_agent_capabilities

        caps = _parse_agent_capabilities({"internalSearch": True, "webSearch": False})
        provider, knowledge, filt = self._apply_caps(caps)
        assert provider is None
        assert knowledge == ["kb-1", "kb-2"]

    def test_internal_search_disabled_clears_knowledge_and_filters(self) -> None:
        from app.api.routes.agent import _parse_agent_capabilities

        caps = _parse_agent_capabilities({"internalSearch": False, "webSearch": True})
        provider, knowledge, filt = self._apply_caps(caps)
        assert provider == "tavilydemo"
        assert knowledge == []
        assert filt == {"apps": [], "kb": [self.NO_KB_SELECTED_FILTER]}

    def test_both_disabled_clears_everything(self) -> None:
        from app.api.routes.agent import _parse_agent_capabilities

        caps = _parse_agent_capabilities({"internalSearch": False, "webSearch": False})
        provider, knowledge, filt = self._apply_caps(caps)
        assert provider is None
        assert knowledge == []
        assert filt == {"apps": [], "kb": [self.NO_KB_SELECTED_FILTER]}

    def test_missing_caps_preserves_original_state(self) -> None:
        from app.api.routes.agent import _parse_agent_capabilities

        caps = _parse_agent_capabilities(None)
        provider, knowledge, filt = self._apply_caps(caps)
        assert provider == "tavilydemo"
        assert knowledge == ["kb-1", "kb-2"]

    def test_capabilities_only_narrow_agent_without_web_search(self) -> None:
        """When agent has no web search configured (provider=None), caps cannot enable it."""
        from app.api.routes.agent import _parse_agent_capabilities

        caps = _parse_agent_capabilities({"internalSearch": True, "webSearch": True})
        # Start with provider=None (agent not configured with web search)
        provider, knowledge, filt = self._apply_caps(caps, web_search_provider=None)
        assert provider is None  # caps=True but agent had no provider → stays None
