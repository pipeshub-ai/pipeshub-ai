"""`KnowledgeGraphPolicy`: the knowledge-graph arm a request runs with, decided
once from the Labs flags before tools load.

`KNOWLEDGE_GRAPH_TOOLS` (default on) and `KNOWLEDGE_GRAPH_PEOPLE_AND_COMPANIES`
(default off) pick `off`, `taxonomy` or `full`; the named-entity flag and the
`ENTITY_SEARCH_TOOL` switch decide the rest. One settings read decides it all.
"""

from __future__ import annotations

import logging
from typing import Any

import pytest

from app.agents.agent_loop.knowledge_graph_policy import (
    ENTITY_SEARCH_TOOL_ENV_VAR,
    KNOWLEDGE_GRAPH_POLICY_STATE_KEY,
    KnowledgeGraphMode,
    KnowledgeGraphPolicy,
    decide_knowledge_graph_policy,
    entity_tools_granted,
    knowledge_graph_policy_in,
)
from app.services.featureflag.config.config import CONFIG
from app.services.featureflag.platform_settings import PLATFORM_SETTINGS_KEY

_TAXONOMY = {"department", "category", "subcategory", "topic", "language"}


class _Settings:
    """A `ConfigurationService` holding the Labs settings blob."""

    def __init__(self, flags: dict[str, bool] | None = None, *, fail: bool = False) -> None:
        self.flags = dict(flags or {})
        self.fail = fail
        self.reads = 0

    async def get_config(self, key: str, default: Any = None, use_cache: bool = False, **_: Any) -> Any:  # noqa: ANN401
        assert key == PLATFORM_SETTINGS_KEY
        self.reads += 1
        if self.fail:
            raise RuntimeError("store unreachable")
        return {"featureFlags": dict(self.flags)}


def _flags(tools: bool | None, people: bool | None, ner: bool | None = None) -> dict[str, bool]:
    named = (
        (CONFIG.KNOWLEDGE_GRAPH_TOOLS, tools),
        (CONFIG.KNOWLEDGE_GRAPH_PEOPLE_AND_COMPANIES, people),
        (CONFIG.ENABLE_NAMED_ENTITY_EXTRACTION, ner),
    )
    return {key: value for key, value in named if value is not None}


@pytest.fixture(autouse=True)
def _environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ENTITY_SEARCH_TOOL_ENV_VAR, raising=False)
    monkeypatch.delenv("HIGH_ACCURACY_MODE", raising=False)


class TestTheFlagKeys:
    def test_the_harness_flips_these_exact_keys(self) -> None:
        assert CONFIG.KNOWLEDGE_GRAPH_TOOLS == "KNOWLEDGE_GRAPH_TOOLS"
        assert CONFIG.KNOWLEDGE_GRAPH_PEOPLE_AND_COMPANIES == "KNOWLEDGE_GRAPH_PEOPLE_AND_COMPANIES"


class TestDecision:
    @pytest.mark.parametrize(("tools", "people", "mode"), [
        (None, None, KnowledgeGraphMode.TAXONOMY),
        (True, None, KnowledgeGraphMode.TAXONOMY),
        (True, False, KnowledgeGraphMode.TAXONOMY),
        (True, True, KnowledgeGraphMode.FULL),
        (None, True, KnowledgeGraphMode.FULL),
        (False, None, KnowledgeGraphMode.OFF),
        (False, True, KnowledgeGraphMode.OFF),
    ])
    async def test_the_two_flags_pick_the_arm(
        self, tools: bool | None, people: bool | None, mode: KnowledgeGraphMode,
    ) -> None:
        state: dict[str, Any] = {"config_service": _Settings(_flags(tools, people))}
        policy = await decide_knowledge_graph_policy(state)
        assert policy.mode is mode
        assert state[KNOWLEDGE_GRAPH_POLICY_STATE_KEY] is policy

    @pytest.mark.parametrize(("tools", "people", "named"), [
        (True, True, True), (True, False, False), (False, True, False),
    ])
    async def test_named_entities_come_only_with_the_full_arm(
        self, tools: bool, people: bool, named: bool,
    ) -> None:
        """Extracted organisations and people are companies and colleagues by
        another route, so the taxonomy arm goes without them too."""
        state: dict[str, Any] = {"config_service": _Settings(_flags(tools, people, ner=True))}
        assert (await decide_knowledge_graph_policy(state)).named_entities is named

    async def test_high_accuracy_mode_turns_named_entities_on_in_the_full_arm(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("HIGH_ACCURACY_MODE", "true")
        state: dict[str, Any] = {"config_service": _Settings(_flags(True, True))}
        assert (await decide_knowledge_graph_policy(state)).named_entities is True

    @pytest.mark.parametrize(("switch", "store", "ner", "offered"), [
        ("true", True, True, True),
        (None, True, True, False),
        ("true", False, True, False),
        ("true", True, False, False),
    ])
    async def test_search_by_entities_needs_the_switch_the_store_and_named_entities(
        self, monkeypatch: pytest.MonkeyPatch, switch: str | None, store: bool, ner: bool, offered: bool,
    ) -> None:
        if switch:
            monkeypatch.setenv(ENTITY_SEARCH_TOOL_ENV_VAR, switch)
        state: dict[str, Any] = {"config_service": _Settings(_flags(True, True, ner=ner))}
        if store:
            state["entity_vector_store"] = object()
        assert (await decide_knowledge_graph_policy(state)).search_by_entities is offered

    async def test_one_settings_read_decides_every_flag(self) -> None:
        settings = _Settings(_flags(True, True, ner=True))
        await decide_knowledge_graph_policy({"config_service": settings})
        assert settings.reads == 1

    async def test_a_second_call_keeps_the_first_decision_without_reading_again(self) -> None:
        settings = _Settings(_flags(True, True))
        state: dict[str, Any] = {"config_service": settings}
        first = await decide_knowledge_graph_policy(state)
        settings.flags = _flags(False, False)
        assert await decide_knowledge_graph_policy(state) is first
        assert settings.reads == 1

    async def test_an_unreadable_store_answers_the_defaults(self) -> None:
        state: dict[str, Any] = {"config_service": _Settings(_flags(False, True), fail=True)}
        policy = await decide_knowledge_graph_policy(state)
        assert policy.mode is KnowledgeGraphMode.TAXONOMY
        assert policy.named_entities is False

    async def test_it_is_logged_once_with_the_conversation_and_no_content(
        self, caplog: pytest.LogCaptureFixture,
    ) -> None:
        state: dict[str, Any] = {
            "config_service": _Settings(_flags(True, True)), "org_id": "org-9",
            "conversation_id": "conv-7", "query": "who owns the Contoso renewal",
        }
        with caplog.at_level(logging.INFO, logger="app.agents.agent_loop.knowledge_graph_policy"):
            await decide_knowledge_graph_policy(state)
            await decide_knowledge_graph_policy(state)
        lines = [r.getMessage() for r in caplog.records if "knowledge graph policy" in r.getMessage()]
        assert len(lines) == 1
        assert "mode=full" in lines[0] and "conversation_id=conv-7" in lines[0] and "org_id=org-9" in lines[0]
        assert "Contoso" not in lines[0]


class TestEntityTypes:
    def test_off_allows_no_entity_type(self) -> None:
        policy = KnowledgeGraphPolicy(mode=KnowledgeGraphMode.OFF)
        assert policy.entity_types == frozenset()
        assert policy.entity_tools is False

    def test_taxonomy_is_the_graph_before_people_and_companies(self) -> None:
        policy = KnowledgeGraphPolicy(mode=KnowledgeGraphMode.TAXONOMY)
        assert policy.entity_types == frozenset({*_TAXONOMY, "record", "record_group"})
        assert not policy.allows("person") and not policy.allows("organization")
        assert not policy.allows("named_entity")
        assert policy.people_and_companies is False

    @pytest.mark.parametrize("named", [True, False])
    def test_full_adds_people_companies_and_named_entities_when_extracted(self, named: bool) -> None:
        policy = KnowledgeGraphPolicy(mode=KnowledgeGraphMode.FULL, named_entities=named)
        expected = {*_TAXONOMY, "record", "record_group", "person", "organization"}
        assert policy.entity_types == frozenset(expected | ({"named_entity"} if named else set()))
        assert policy.people_and_companies is True

    def test_it_is_immutable(self) -> None:
        policy = KnowledgeGraphPolicy(mode=KnowledgeGraphMode.FULL)
        with pytest.raises(AttributeError):
            policy.mode = KnowledgeGraphMode.OFF  # type: ignore[misc]


class TestReadingItBack:
    def test_a_request_that_never_decided_gets_the_defaults(self) -> None:
        policy = knowledge_graph_policy_in({})
        assert policy == KnowledgeGraphPolicy()
        assert policy.mode is KnowledgeGraphMode.TAXONOMY
        assert knowledge_graph_policy_in(None) == KnowledgeGraphPolicy()

    @pytest.mark.parametrize(("mode", "store", "granted"), [
        (KnowledgeGraphMode.FULL, True, True),
        (KnowledgeGraphMode.TAXONOMY, True, True),
        (KnowledgeGraphMode.OFF, True, False),
        (KnowledgeGraphMode.FULL, False, False),
    ])
    def test_entity_tools_need_the_store_and_an_arm_that_has_them(
        self, mode: KnowledgeGraphMode, store: bool, granted: bool,
    ) -> None:
        state: dict[str, Any] = {KNOWLEDGE_GRAPH_POLICY_STATE_KEY: KnowledgeGraphPolicy(mode=mode)}
        if store:
            state["entity_vector_store"] = object()
        assert entity_tools_granted(state) is granted


class TestRecordQuery:
    """`KNOWLEDGE_GRAPH_RECORD_QUERY` (default off) swaps `find_records_by_entity`
    for `find_records`, the exact filter and count tool (`docs/record-query.md`)."""

    def test_the_harness_flips_this_exact_key(self) -> None:
        assert CONFIG.KNOWLEDGE_GRAPH_RECORD_QUERY == "KNOWLEDGE_GRAPH_RECORD_QUERY"

    @pytest.mark.parametrize(("tools", "people", "query", "on", "arm"), [
        (True, True, None, False, "full"),
        (True, True, True, True, "full+query"),
        (True, None, True, True, "taxonomy+query"),
        (False, True, True, False, "off"),
    ])
    async def test_it_is_off_by_default_and_needs_the_entity_tools(
        self, tools: bool | None, people: bool | None, query: bool | None, on: bool, arm: str,
    ) -> None:
        flags = _flags(tools, people)
        if query is not None:
            flags[CONFIG.KNOWLEDGE_GRAPH_RECORD_QUERY] = query
        policy = await decide_knowledge_graph_policy({"config_service": _Settings(flags)})
        assert (policy.record_query, policy.arm) == (on, arm)
