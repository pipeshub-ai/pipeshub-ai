"""Retrieval tool -> `CitationCollector` -> `fetch_full_record` registration
chain (`app/agents/agent_loop/hooks/citations.py`), covering the migration
plan's Phase 3 "Special Tool Categories #1" note: `retrieval.
search_internal_knowledge` mutates `AgentContext.tool_state` directly (via
`RegistryToolWrapper`, unchanged), and `CitationCollector` is a read-only
view over those fields for Phase 6's `RespondPipeline` to consume."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from app.agent_loop_lib.tools.registry import ToolRegistry
from app.agents.agent_loop.hooks.citations import (
    _FETCH_FULL_RECORD_TOOL_NAME,
    CitationCollector,
    _FetchFullRecordTool,
    citation_tracking,
)
from tests.unit.agents.adapter.conftest import make_context
from tests.unit.agents.adapter.support.hook_helpers import run_post_tool


class TestCitationCollector:
    def test_reads_defaults_as_empty_when_unset(self) -> None:
        context = make_context()
        collector = CitationCollector(context)

        assert collector.final_results == []
        assert collector.virtual_records == {}
        assert collector.tool_records == []
        assert collector.citation_ref_mapper is None

    def test_reflects_live_mutations_to_tool_state(self) -> None:
        """`RegistryToolWrapper` mutates the SAME dict `AgentContext.
        tool_state` is — the collector must see updates made after
        construction, not a snapshot."""
        context = make_context()
        collector = CitationCollector(context)

        context.tool_state["final_results"] = [{"id": "r1"}]
        context.tool_state["virtual_record_id_to_result"] = {"vr1": {"id": "r1"}}
        context.tool_state["tool_records"] = [{"id": "r1"}]
        context.tool_state["citation_ref_mapper"] = "ref-mapper-instance"

        assert collector.final_results == [{"id": "r1"}]
        assert collector.virtual_records == {"vr1": {"id": "r1"}}
        assert collector.tool_records == [{"id": "r1"}]
        assert collector.citation_ref_mapper == "ref-mapper-instance"


class TestCitationTrackingHook:
    async def test_noop_without_scope(self) -> None:
        """No `RunScope` (as in isolated hook-helper tests) means no tool
        registry to register into — the hook must not raise."""
        context = make_context()
        collector = CitationCollector(context)
        middleware = citation_tracking(context, collector)

        await run_post_tool(middleware, tool_response=MagicMock(success=True))

    async def test_registers_fetch_full_record_tool_once_populated(self) -> None:
        context = make_context()
        collector = CitationCollector(context)
        middleware = citation_tracking(context, collector)

        registry = ToolRegistry()
        run_scope = SimpleNamespace(
            runtime=SimpleNamespace(tool_registry=registry), spec=SimpleNamespace(tool_names=[])
        )
        turn_scope = SimpleNamespace(run=run_scope)
        scope = SimpleNamespace(turn=turn_scope)

        context.tool_state["virtual_record_id_to_result"] = {"vr1": {"id": "r1"}}
        await run_post_tool(middleware, tool_response=MagicMock(success=True), scope=scope)

        assert registry.has(_FETCH_FULL_RECORD_TOOL_NAME)

    async def test_does_not_register_when_no_virtual_records_yet(self) -> None:
        context = make_context()
        collector = CitationCollector(context)
        middleware = citation_tracking(context, collector)

        registry = ToolRegistry()
        run_scope = SimpleNamespace(
            runtime=SimpleNamespace(tool_registry=registry), spec=SimpleNamespace(tool_names=[])
        )
        turn_scope = SimpleNamespace(run=run_scope)
        scope = SimpleNamespace(turn=turn_scope)

        await run_post_tool(middleware, tool_response=MagicMock(success=True), scope=scope)

        assert not registry.has(_FETCH_FULL_RECORD_TOOL_NAME)

    async def test_does_not_re_register_when_already_present(self) -> None:
        context = make_context()
        collector = CitationCollector(context)
        middleware = citation_tracking(context, collector)

        registry = ToolRegistry()
        registry.register_tool(_FetchFullRecordTool(collector, context))
        run_scope = SimpleNamespace(
            runtime=SimpleNamespace(tool_registry=registry), spec=SimpleNamespace(tool_names=[])
        )
        turn_scope = SimpleNamespace(run=run_scope)
        scope = SimpleNamespace(turn=turn_scope)

        context.tool_state["virtual_record_id_to_result"] = {"vr1": {"id": "r1"}}
        # Should not raise DuplicateToolNameError from a second registration.
        await run_post_tool(middleware, tool_response=MagicMock(success=True), scope=scope)

        assert registry.has(_FETCH_FULL_RECORD_TOOL_NAME)

    async def test_appends_to_explicit_tool_names_grant_when_present(self) -> None:
        context = make_context()
        collector = CitationCollector(context)
        middleware = citation_tracking(context, collector)

        registry = ToolRegistry()
        agent_spec = SimpleNamespace(tool_names=["some_other_tool"])
        run_scope = SimpleNamespace(runtime=SimpleNamespace(tool_registry=registry), spec=agent_spec)
        turn_scope = SimpleNamespace(run=run_scope)
        scope = SimpleNamespace(turn=turn_scope)

        context.tool_state["virtual_record_id_to_result"] = {"vr1": {"id": "r1"}}
        await run_post_tool(middleware, tool_response=MagicMock(success=True), scope=scope)

        assert _FETCH_FULL_RECORD_TOOL_NAME in agent_spec.tool_names

    async def test_also_grants_root_agent_spec_when_it_references_internal_search(self) -> None:
        """Under domain-agent composition, retrieval always runs inside the
        `internal_exploration_agent` CHILD's `RunScope` — but the agent
        that DELEGATED the search (the request's top-level `root_agent_spec`)
        must also be able to fetch a full record directly once it has a
        Record ID, without a second round-trip delegation."""
        context = make_context()
        collector = CitationCollector(context)
        middleware = citation_tracking(context, collector)

        registry = ToolRegistry()
        child_spec = SimpleNamespace(tool_names=["retrieval_search_internal_knowledge"])
        run_scope = SimpleNamespace(runtime=SimpleNamespace(tool_registry=registry), spec=child_spec)
        scope = SimpleNamespace(turn=SimpleNamespace(run=run_scope))

        root_spec = SimpleNamespace(tool_names=["internal_exploration_agent", "web_agent"])
        context.root_agent_spec = root_spec

        context.tool_state["virtual_record_id_to_result"] = {"vr1": {"id": "r1"}}
        await run_post_tool(middleware, tool_response=MagicMock(success=True), scope=scope)

        assert _FETCH_FULL_RECORD_TOOL_NAME in child_spec.tool_names
        assert _FETCH_FULL_RECORD_TOOL_NAME in root_spec.tool_names

    async def test_does_not_grant_root_agent_spec_without_internal_search_reference(self) -> None:
        """A root spec whose grant does NOT reference the internal-search
        surface (e.g. deep mode's `OrchestratorLoop`, restricted to
        coordination tools — see `factory.py`) must never be leaked
        `dynamic_fetch_full_record` — it has no business calling it."""
        context = make_context()
        collector = CitationCollector(context)
        middleware = citation_tracking(context, collector)

        registry = ToolRegistry()
        child_spec = SimpleNamespace(tool_names=["retrieval_search_internal_knowledge"])
        run_scope = SimpleNamespace(runtime=SimpleNamespace(tool_registry=registry), spec=child_spec)
        scope = SimpleNamespace(turn=SimpleNamespace(run=run_scope))

        root_spec = SimpleNamespace(tool_names=["create_plan", "spawn_agent", "critique_plan"])
        context.root_agent_spec = root_spec

        context.tool_state["virtual_record_id_to_result"] = {"vr1": {"id": "r1"}}
        await run_post_tool(middleware, tool_response=MagicMock(success=True), scope=scope)

        assert _FETCH_FULL_RECORD_TOOL_NAME not in root_spec.tool_names

    async def test_root_agent_spec_grant_is_idempotent_across_repeated_calls(self) -> None:
        context = make_context()
        collector = CitationCollector(context)
        middleware = citation_tracking(context, collector)

        registry = ToolRegistry()
        child_spec = SimpleNamespace(tool_names=["retrieval_search_internal_knowledge"])
        run_scope = SimpleNamespace(runtime=SimpleNamespace(tool_registry=registry), spec=child_spec)
        scope = SimpleNamespace(turn=SimpleNamespace(run=run_scope))

        root_spec = SimpleNamespace(tool_names=["internal_exploration_agent"])
        context.root_agent_spec = root_spec

        context.tool_state["virtual_record_id_to_result"] = {"vr1": {"id": "r1"}}
        await run_post_tool(middleware, tool_response=MagicMock(success=True), scope=scope)
        await run_post_tool(middleware, tool_response=MagicMock(success=True), scope=scope)

        assert root_spec.tool_names.count(_FETCH_FULL_RECORD_TOOL_NAME) == 1

    async def test_no_root_grant_when_root_agent_spec_unset(self) -> None:
        """Default (`context.root_agent_spec is None`, e.g. isolated hook
        tests or a caller that never wired it) must not raise."""
        context = make_context()
        collector = CitationCollector(context)
        middleware = citation_tracking(context, collector)

        registry = ToolRegistry()
        child_spec = SimpleNamespace(tool_names=["retrieval_search_internal_knowledge"])
        run_scope = SimpleNamespace(runtime=SimpleNamespace(tool_registry=registry), spec=child_spec)
        scope = SimpleNamespace(turn=SimpleNamespace(run=run_scope))

        context.tool_state["virtual_record_id_to_result"] = {"vr1": {"id": "r1"}}
        await run_post_tool(middleware, tool_response=MagicMock(success=True), scope=scope)

        assert _FETCH_FULL_RECORD_TOOL_NAME in child_spec.tool_names


class TestFetchFullRecordToolRebuildsFromLiveMap:
    async def test_execute_uses_current_virtual_records_not_a_snapshot(self) -> None:
        """Retrieval REPLACES (not merges in place) `virtual_record_id_to_
        result` on every call — the tool must read `collector.virtual_records`
        fresh on each `execute()`, not freeze it at registration time."""
        context = make_context()
        collector = CitationCollector(context)
        tool = _FetchFullRecordTool(collector, context)

        seen_maps = []

        def _fake_create_tool(virtual_records, **_kwargs):
            seen_maps.append(dict(virtual_records))
            fake = MagicMock()

            async def _coro(**_kw):
                return "ok"

            fake.coroutine = _coro
            return fake

        from unittest.mock import patch

        with patch("app.utils.fetch_full_record.create_fetch_full_record_tool", side_effect=_fake_create_tool):
            context.tool_state["virtual_record_id_to_result"] = {"vr1": {"id": "r1"}}
            await tool.execute(record_ids=["vr1"])

            context.tool_state["virtual_record_id_to_result"] = {"vr2": {"id": "r2"}}
            await tool.execute(record_ids=["vr2"])

        assert seen_maps == [{"vr1": {"id": "r1"}}, {"vr2": {"id": "r2"}}]
