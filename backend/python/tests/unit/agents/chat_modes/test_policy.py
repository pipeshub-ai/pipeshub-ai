"""Unit tests for `app.agents.chat_modes.policy`."""

import pytest

from app.agents.chat_modes.policy import (
    AGENT_POLICY,
    INTERNAL_SEARCH_POLICY,
    WEB_SEARCH_POLICY,
    AgentCapabilities,
    resolve_agent_policy,
    resolve_chat_mode_policy,
)


class TestResolveChatModePolicy:
    @pytest.mark.parametrize(
        ("wire_value", "expected"),
        [
            ("internal_search", INTERNAL_SEARCH_POLICY),
            ("web_search", WEB_SEARCH_POLICY),
            ("agent", AGENT_POLICY),
        ],
    )
    def test_canonical_values_resolve_directly(self, wire_value, expected) -> None:
        assert resolve_chat_mode_policy(wire_value) is expected

    @pytest.mark.parametrize(
        "wire_value",
        ["Agent", "AGENT", "  agent  ", "Web_Search", " web_search"],
    )
    def test_canonical_values_are_case_and_whitespace_insensitive(self, wire_value) -> None:
        normalized = wire_value.strip().lower()
        expected = {
            "agent": AGENT_POLICY,
            "web_search": WEB_SEARCH_POLICY,
        }[normalized]
        assert resolve_chat_mode_policy(wire_value) is expected

    @pytest.mark.parametrize(
        "wire_value",
        ["analysis", "deep_research", "creative", "precise", "standard", "quick"],
    )
    def test_legacy_values_fall_back_to_internal_search(self, wire_value) -> None:
        assert resolve_chat_mode_policy(wire_value) is INTERNAL_SEARCH_POLICY

    @pytest.mark.parametrize("wire_value", [None, "", "   ", "unknown_mode", "agent:quick"])
    def test_missing_or_unrecognized_values_fall_back_to_internal_search(self, wire_value) -> None:
        assert resolve_chat_mode_policy(wire_value) is INTERNAL_SEARCH_POLICY


class TestChatModePolicyShape:
    def test_internal_search_forces_knowledge_no_web_prefetches(self) -> None:
        assert INTERNAL_SEARCH_POLICY.has_knowledge is True
        assert INTERNAL_SEARCH_POLICY.include_web_search is False
        assert INTERNAL_SEARCH_POLICY.prefetch_retrieval is True

    def test_web_search_disables_knowledge_no_prefetch(self) -> None:
        assert WEB_SEARCH_POLICY.has_knowledge is False
        assert WEB_SEARCH_POLICY.include_web_search is True
        assert WEB_SEARCH_POLICY.prefetch_retrieval is False

    def test_agent_enables_both_tools_no_upfront_prefetch(self) -> None:
        assert AGENT_POLICY.has_knowledge is True
        assert AGENT_POLICY.include_web_search is True
        assert AGENT_POLICY.prefetch_retrieval is False

    def test_all_policies_use_the_quick_loop(self) -> None:
        for policy in (INTERNAL_SEARCH_POLICY, WEB_SEARCH_POLICY, AGENT_POLICY):
            assert policy.loop_chat_mode == "quick"

    def test_policy_is_frozen(self) -> None:
        with pytest.raises(AttributeError):
            INTERNAL_SEARCH_POLICY.has_knowledge = False  # type: ignore[misc]


class TestResolveAgentPolicy:
    def test_defaults_match_agent_policy_shape(self) -> None:
        p = resolve_agent_policy()
        assert p.has_knowledge is True
        assert p.include_web_search is True
        assert p.prefetch_retrieval is False
        assert p.name == "agent"

    def test_none_capabilities_same_as_defaults(self) -> None:
        assert resolve_agent_policy(None) == resolve_agent_policy()

    def test_internal_search_disabled_clears_knowledge(self) -> None:
        p = resolve_agent_policy(AgentCapabilities(internal_search=False, web_search=True))
        assert p.has_knowledge is False
        assert p.include_web_search is True
        assert p.system_prompt_key == "web_search"

    def test_web_search_disabled_clears_web_search(self) -> None:
        p = resolve_agent_policy(AgentCapabilities(internal_search=True, web_search=False))
        assert p.has_knowledge is True
        assert p.include_web_search is False
        assert p.system_prompt_key == "internal_search"

    def test_both_disabled_neither_tool_available(self) -> None:
        p = resolve_agent_policy(AgentCapabilities(internal_search=False, web_search=False))
        assert p.has_knowledge is False
        assert p.include_web_search is False
        assert p.system_prompt_key == "agent"

    def test_both_enabled_full_agent_policy(self) -> None:
        p = resolve_agent_policy(AgentCapabilities(internal_search=True, web_search=True))
        assert p.has_knowledge is True
        assert p.include_web_search is True
        assert p.system_prompt_key == "agent"

    def test_result_is_frozen(self) -> None:
        p = resolve_agent_policy()
        with pytest.raises(AttributeError):
            p.has_knowledge = False  # type: ignore[misc]


class TestAgentCapabilitiesDefaults:
    def test_defaults_are_both_enabled(self) -> None:
        caps = AgentCapabilities()
        assert caps.internal_search is True
        assert caps.web_search is True
        assert caps.deep_search is False

    def test_is_frozen(self) -> None:
        caps = AgentCapabilities()
        with pytest.raises(AttributeError):
            caps.internal_search = False  # type: ignore[misc]
