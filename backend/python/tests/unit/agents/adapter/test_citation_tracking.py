"""`citation_tracking` (`app/agents/agent_loop/hooks/citations.py`) — the
POST_TOOL_USE middleware that dynamically registers `_FetchFullRecordTool`
and grants it to the calling spec(s) once retrieval produces virtual
records.

Regression coverage for the fix-registry-races todo: concurrent invocations
(two retrieval-bearing tool calls in the same gathered turn) must never let
one caller's grant be skipped just because a sibling call already won the
registration race — `register_tool_if_absent` (see `tools/registry.py`)
replaces the check-then-`register_tool` pattern that used to raise
`DuplicateToolNameError` and abort the loser's own `_grant` calls.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from app.agent_loop_lib.agent.spec import AgentSpec, ModelSpec
from app.agent_loop_lib.core.context import RunContext
from app.agent_loop_lib.core.scope import RunScope, ToolScope, TurnScope
from app.agent_loop_lib.core.types import Goal
from app.agent_loop_lib.hooks.middleware.context import ToolResultContext
from app.agent_loop_lib.runtime.runtime import AgentRuntime
from app.agent_loop_lib.tools.base import ToolOutput
from app.agent_loop_lib.tools.registry import ToolRegistry
from app.agents.agent_loop.context import AgentContext
from app.agents.agent_loop.hooks.citations import (
    _FETCH_FULL_RECORD_TOOL_NAME,
    CitationCollector,
    citation_tracking,
)
from app.models.blocks import BlockType


async def _noop_next() -> None:
    return None


def _spec(name: str, tool_names: list[str]) -> AgentSpec:
    return AgentSpec(
        name=name, system_prompt="x", tool_names=tool_names,
        model=ModelSpec(provider="scripted", model="m"),
    )


def _tool_scope(spec: AgentSpec, registry: ToolRegistry) -> ToolScope:
    run_scope = RunScope(
        identity=RunContext(role_name=spec.name, model="m"),
        spec=spec, runtime=AgentRuntime(tool_registry=registry), goal=Goal(description="g"),
    )
    turn_scope = TurnScope(run=run_scope, turn_index=0)
    return ToolScope(turn=turn_scope, call=None, tool_path="/toolsets/builtin/search", messages=[])


def _agent_context() -> AgentContext:
    context = AgentContext(org_id="org-1", user_id="user-1", user_email="u@example.com", logger=MagicMock())
    context.tool_state["virtual_record_id_to_result"] = {"rec1": {"content": "hi"}}
    return context


class TestCitationTrackingRegistersOnce:
    async def test_registers_tool_and_grants_to_caller_spec(self) -> None:
        registry = ToolRegistry()
        context = _agent_context()
        collector = CitationCollector(context)
        spec = _spec("caller", tool_names=["retrieval_search_internal_knowledge"])
        scope = _tool_scope(spec, registry)
        middleware = citation_tracking(context, collector)
        ctx = ToolResultContext(
            tool_path="/toolsets/builtin/search", tool_use_id=uuid4(),
            tool_response=ToolOutput(success=True, data="ok"), scope=scope,
        )

        await middleware(ctx, _noop_next)

        assert registry.has(_FETCH_FULL_RECORD_TOOL_NAME)
        assert _FETCH_FULL_RECORD_TOOL_NAME in spec.tool_names

    async def test_second_call_after_already_registered_still_grants(self) -> None:
        """Simulates the losing side of a registration race: the tool is
        already registered (by a sibling call) by the time this middleware
        invocation reaches the register line — it must still reach its own
        `_grant` calls instead of raising."""
        registry = ToolRegistry()
        context = _agent_context()
        collector = CitationCollector(context)

        from app.agents.agent_loop.hooks.citations import _FetchFullRecordTool

        registry.register_tool(_FetchFullRecordTool(collector, context))

        spec = _spec("caller", tool_names=["retrieval_search_internal_knowledge"])
        scope = _tool_scope(spec, registry)
        middleware = citation_tracking(context, collector)
        ctx = ToolResultContext(
            tool_path="/toolsets/builtin/search", tool_use_id=uuid4(),
            tool_response=ToolOutput(success=True, data="ok"), scope=scope,
        )

        await middleware(ctx, _noop_next)

        assert _FETCH_FULL_RECORD_TOOL_NAME in spec.tool_names

    async def test_grants_to_both_caller_and_root_spec_when_reference_present(self) -> None:
        registry = ToolRegistry()
        context = _agent_context()
        collector = CitationCollector(context)
        context.root_agent_spec = _spec("root", tool_names=["retrieval_search_internal_knowledge"])
        caller_spec = _spec("child", tool_names=["retrieval_search_internal_knowledge"])
        scope = _tool_scope(caller_spec, registry)
        middleware = citation_tracking(context, collector)
        ctx = ToolResultContext(
            tool_path="/toolsets/builtin/search", tool_use_id=uuid4(),
            tool_response=ToolOutput(success=True, data="ok"), scope=scope,
        )

        await middleware(ctx, _noop_next)

        assert _FETCH_FULL_RECORD_TOOL_NAME in caller_spec.tool_names
        assert _FETCH_FULL_RECORD_TOOL_NAME in context.root_agent_spec.tool_names

    async def test_no_virtual_records_is_a_noop(self) -> None:
        registry = ToolRegistry()
        context = AgentContext(org_id="org-1", user_id="user-1", user_email="u@example.com", logger=MagicMock())
        collector = CitationCollector(context)
        spec = _spec("caller", tool_names=["retrieval_search_internal_knowledge"])
        scope = _tool_scope(spec, registry)
        middleware = citation_tracking(context, collector)
        ctx = ToolResultContext(
            tool_path="/toolsets/builtin/search", tool_use_id=uuid4(),
            tool_response=ToolOutput(success=True, data="ok"), scope=scope,
        )

        await middleware(ctx, _noop_next)

        assert not registry.has(_FETCH_FULL_RECORD_TOOL_NAME)
        assert _FETCH_FULL_RECORD_TOOL_NAME not in spec.tool_names


class TestCitationTrackingRegistersForNavigationalTools:
    """`navigate`/`lookup_record`/`list_files` surface Record IDs without
    producing virtual records. Before they were counted here, an agent that
    resolved an epic and walked its stories held Record IDs and had no tool
    able to read them — so walking the hierarchy dead-ended and the model had
    no reason to do it."""

    def _run(self, spec: AgentSpec, *, known_ids: set[str] | None) -> ToolRegistry:
        registry = ToolRegistry()
        context = AgentContext(
            org_id="org-1", user_id="user-1", user_email="u@example.com", logger=MagicMock(),
        )
        if known_ids is not None:
            context.tool_state["known_record_ids"] = known_ids
        collector = CitationCollector(context)
        scope = _tool_scope(spec, registry)
        middleware = citation_tracking(context, collector)
        ctx = ToolResultContext(
            tool_path="/tools/knowledgegraph/navigate", tool_use_id=uuid4(),
            tool_response=ToolOutput(success=True, data="Record ID: rec-1"), scope=scope,
        )
        return registry, middleware, ctx

    async def test_known_record_ids_alone_registers_and_grants(self) -> None:
        spec = _spec("caller", tool_names=["knowledgegraph_navigate"])
        registry, middleware, ctx = self._run(spec, known_ids={"rec-1"})

        await middleware(ctx, _noop_next)

        assert registry.has(_FETCH_FULL_RECORD_TOOL_NAME)
        assert _FETCH_FULL_RECORD_TOOL_NAME in spec.tool_names

    async def test_empty_known_record_ids_is_still_a_noop(self) -> None:
        spec = _spec("caller", tool_names=["knowledgegraph_navigate"])
        registry, middleware, ctx = self._run(spec, known_ids=set())

        await middleware(ctx, _noop_next)

        assert not registry.has(_FETCH_FULL_RECORD_TOOL_NAME)
        assert _FETCH_FULL_RECORD_TOOL_NAME not in spec.tool_names

    async def test_lookup_only_agent_can_read_what_it_resolved(self) -> None:
        """A KB-only agent with no retrieval tool at all: `lookup_record`
        reporting an ID must be enough to make the read step callable."""
        spec = _spec("caller", tool_names=["knowledgegraph_lookup_record"])
        _registry, middleware, ctx = self._run(spec, known_ids={"rec-1"})

        await middleware(ctx, _noop_next)

        assert _FETCH_FULL_RECORD_TOOL_NAME in spec.tool_names


def _record_with_blocks(record_id: str, count: int) -> dict:
    """A record whose blocks say which index they are, so a windowed read is
    visible in the rendered text."""
    return {
        "id": record_id,
        "virtual_record_id": f"v-{record_id}",
        "record_name": "Bug Bash Session",
        "context_metadata": f"Record ID       : {record_id}",
        "block_containers": {
            "blocks": [
                {
                    "index": i,
                    "type": BlockType.TEXT.value,
                    "data": f"content of block {i}",
                }
                for i in range(count)
            ],
            "block_groups": [],
        },
    }


class TestFullReadStartsAtTheBeginning:
    """A "read it in full" that starts at the block retrieval happened to match
    returns the tail of the document and calls it the whole thing. Observed on
    a Confluence page whose only matched block was #110 of ~112: the fetch came
    back with those last two blocks — the template footer — so the model
    reported the page as an unfilled template."""

    async def _read(self, record: dict, *, final_results: list, **kwargs) -> str:
        from app.agents.agent_loop.hooks.citations import _FetchFullRecordTool

        context = _agent_context()
        context.tool_state["final_results"] = final_results
        tool = _FetchFullRecordTool(CitationCollector(context), context)

        structured = MagicMock()

        async def _coroutine(**_: object) -> dict:
            return {"ok": True, "records": [record]}

        structured.coroutine = _coroutine
        with patch(
            "app.utils.fetch_full_record.create_fetch_full_record_tool",
            return_value=structured,
        ):
            out = await tool.execute(record_ids=[record["id"]], **kwargs)
        assert out.success
        return out.data

    async def test_blocks_before_the_matched_one_are_present(self) -> None:
        record = _record_with_blocks("rec-1", 8)
        text = await self._read(
            record,
            final_results=[{"virtual_record_id": "v-rec-1", "block_index": 6}],
        )
        assert "content of block 0" in text
        assert "content of block 6" in text

    async def test_an_explicit_start_block_is_still_honoured(self) -> None:
        """Continuation reads depend on it — `record_to_message_content`'s
        truncation hint tells the model to pass the next start_block."""
        record = _record_with_blocks("rec-1", 8)
        text = await self._read(record, final_results=[], start_block=5)
        assert "content of block 0" not in text
        assert "content of block 5" in text

    async def test_no_retrieval_history_reads_from_the_start(self) -> None:
        """The lookup/navigate path has no `final_results` at all."""
        record = _record_with_blocks("rec-2", 4)
        text = await self._read(record, final_results=[])
        assert "content of block 0" in text

    async def test_oversized_record_is_capped_from_the_start_with_a_hint(self) -> None:
        record = _record_with_blocks("rec-3", 10)
        text = await self._read(
            record,
            final_results=[{"virtual_record_id": "v-rec-3", "block_index": 9}],
            max_blocks=4,
        )
        assert "content of block 0" in text
        assert "content of block 4" not in text
        assert "start_block=4" in text


class TestFetchToolDescription:
    """The tool description is the surface the model consults on every turn, so
    it carries the fetch/don't-fetch judgment. It must teach a reusable test
    rather than a list of qualifying scenarios — an earlier version enumerated
    scenarios AND gated the tool on a candidate list being present, so a bare
    "summarize the document" was both unmatched and actively forbidden."""

    def _description(self) -> str:
        from app.agents.agent_loop.hooks.citations import _FetchFullRecordTool

        context = _agent_context()
        return _FetchFullRecordTool(CitationCollector(context), context).description

    def test_states_the_test(self) -> None:
        text = self._description().lower()
        assert "call this before answering" in text
        assert "whole document" in text

    def test_covers_both_branches(self) -> None:
        """Both fetch cases are stated as directives ('call this BEFORE
        answering'); skip is a single parenthetical exception, not a
        co-equal bullet — without a gate/judge backstop, equal billing for
        the skip case reads as permission to take the cheaper path."""
        text = self._description()
        assert "Call this BEFORE answering" in text
        assert "(Skip only when" in text
        assert "Do NOT call this" not in text

    def test_explains_why_fragments_are_insufficient(self) -> None:
        assert "implying are unimportant" in self._description()

    def test_marks_examples_as_illustrations(self) -> None:
        assert "not a checklist" in self._description()

    def test_does_not_gate_on_a_candidate_list_being_present(self) -> None:
        """Regression: the candidate list is an aid to the judgment, never a
        precondition for making it."""
        text = self._description().lower()
        assert "only call this when the candidate list" not in text

    def test_names_the_aggregative_shapes_that_were_being_missed(self) -> None:
        text = self._description().lower()
        for shape in ("summary", "risks", "review", "comparison", "all of something"):
            assert shape in text, shape

    def test_still_documents_single_call_and_no_invented_ids(self) -> None:
        text = self._description()
        assert "ONE call" in text
        assert "never invent IDs" in text

    def test_covers_the_case_where_no_content_is_held(self) -> None:
        """The tool is reachable from lookup/navigate/list_files, which return
        an ID and metadata and no blocks. The skip-fetch escape hatch only
        applies when the exact fact is already visible — unqualified
        "do not call" must not fire on the zero-content path."""
        text = self._description()
        assert "You hold no passage at all" in text
        assert "(Skip only when" in text

    def test_accepts_ids_from_navigation_not_only_search(self) -> None:
        assert "record_id=" in self._description()

    def test_metadata_that_answers_outright_does_not_need_a_read(self) -> None:
        assert "settles the question outright" in self._description()
