"""Tests for ``app.modules.agents.enumeration.run``.

The property under test is the boundary between "safe to fall back" and "an
answer may already have reached the client". Getting that wrong shows the reader
two answers to one question.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.modules.agents.enumeration.run import (
    EnumerationFinalizationError,
    try_answer_enumeration,
)


class TestEnumerationFinalizationError:
    def test_is_an_exception(self) -> None:
        assert issubclass(EnumerationFinalizationError, Exception)

    def test_carries_the_original_cause(self) -> None:
        """The bridge re-raises this rather than retrying, so whatever actually
        failed has to survive for the error surfaced to the caller."""
        original = ValueError("event sink closed")
        try:
            try:
                raise original
            except ValueError as exc:
                raise EnumerationFinalizationError("finalisation failed") from exc
        except EnumerationFinalizationError as wrapped:
            assert wrapped.__cause__ is original

    def test_is_distinguishable_from_an_ordinary_failure(self) -> None:
        """A lookup or storage failure before finalisation must still be caught
        by the bridge's general handler and fall through to the agent."""
        assert not isinstance(RuntimeError("graph unavailable"),
                              EnumerationFinalizationError)


class TestAMalformedAccessibleMapDefersToTheAgent:
    """The answer is built from the ids in this map, so a provider that returns
    the wrong shape must defer to the agent -- the same as a failed lookup --
    rather than crash on .items() or cite records that do not resolve."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "returned",
        [["vr1"], "vr1", {"vr1": None}, {"": "rec1"}, {1: "rec1"}, {"vr1": ""}],
        ids=["list", "string", "null-record-id", "empty-vrid", "int-vrid", "empty-record-id"],
    )
    async def test_defers_instead_of_answering(self, returned: object) -> None:
        graph = MagicMock()
        graph.get_accessible_virtual_record_ids = AsyncMock(return_value=returned)
        context = SimpleNamespace(tool_state={}, org_id="org-1", user_id="user-1")

        answered = await try_answer_enumeration(
            query="How many documents do we have?",
            context=context,
            retrieval_service=MagicMock(),
            graph_provider=graph,
            filters=None,
            event_sink=MagicMock(),
            log=MagicMock(),
        )

        assert answered is False
        # Asserted so a query that never qualifies cannot pass this vacuously.
        graph.get_accessible_virtual_record_ids.assert_awaited_once()



class TestTheReadIsStrict:
    """An unreadable or partly read record set must not be answered as the
    whole corpus: "no documents" or a short count would be confidently wrong."""

    @staticmethod
    async def _run(graph: MagicMock, tool_state: dict) -> bool:
        context = SimpleNamespace(tool_state=tool_state, org_id="org-1", user_id="user-1")
        return await try_answer_enumeration(
            query="How many documents do we have?",
            context=context,
            retrieval_service=MagicMock(),
            graph_provider=graph,
            filters=None,
            event_sink=MagicMock(),
            log=MagicMock(),
        )

    @pytest.mark.asyncio
    async def test_a_failed_read_defers_to_the_agent(self) -> None:
        graph = MagicMock()
        graph.get_accessible_virtual_record_ids = AsyncMock(side_effect=RuntimeError("graph down"))

        assert await self._run(graph, {}) is False

    @pytest.mark.asyncio
    async def test_it_asks_for_errors_and_leaves_out_switched_off_demo_records(self) -> None:
        graph = MagicMock()
        # An unusable shape defers right after the read, so only the call is checked.
        graph.get_accessible_virtual_record_ids = AsyncMock(return_value=[])

        await self._run(graph, {"excluded_app_ids": frozenset({"acme-demo"})})

        kwargs = graph.get_accessible_virtual_record_ids.await_args.kwargs
        assert kwargs["raise_on_error"] is True
        assert kwargs["exclude_app_ids"] == frozenset({"acme-demo"})


class TestAScopedQuestionIsLeftToTheAgent:
    """The census read scopes by apps and kb only, so a folder or item selection,
    or a saved agent's or project's allow-list, would be counted as the whole
    app or as nothing. Those questions go to the agent without the read."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "filters",
        [
            {"recordGroups": ["g1"], "strictScope": True},
            {"apps": ["drive"], "recordGroups": ["g1"]},
            {"records": ["r1"]},
            {"allowedRecordGroups": ["g1"]},
            {"projectApps": []},
        ],
        ids=["folder-pick", "app-and-folder", "record-pick", "agent-allow-list", "empty-project-bound"],
    )
    async def test_defers_without_reading(self, filters: dict) -> None:
        graph = MagicMock()
        graph.get_accessible_virtual_record_ids = AsyncMock(return_value={"vr1": "rec1"})
        event_sink = MagicMock()
        event_sink.write = AsyncMock()
        context = SimpleNamespace(tool_state={}, org_id="org-1", user_id="user-1")

        answered = await try_answer_enumeration(
            query="How many documents do we have?",
            context=context,
            retrieval_service=MagicMock(),
            graph_provider=graph,
            filters=filters,
            event_sink=event_sink,
            log=MagicMock(),
        )

        assert answered is False
        graph.get_accessible_virtual_record_ids.assert_not_awaited()
        event_sink.write.assert_not_awaited()

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "filters",
        [
            {"apps": ["conn-1"], "kb": ["NO_KB_SELECTED"], "allowedApps": ["conn-1"],
             "allowedRecordGroups": [], "allowedRecords": []},
            {"apps": ["conn-1"], "allowedApps": ["conn-1", "conn-2"],
             "allowedRecordGroups": [], "allowedRecords": []},
            # A Collection agent names its Collection under `kb`, not `apps`.
            # The read scopes by both, so this bound matches the read exactly.
            {"apps": [], "kb": ["kb-1"], "allowedApps": ["kb-1"],
             "allowedRecordGroups": [], "allowedRecords": []},
            # A group on the same bound as the Collection does not take
            # anything out of it — `admits` keeps every record of a bound app
            # whatever else the bound names. The group is reach outside what
            # was asked for, which the read does not count either way.
            {"apps": [], "kb": ["kb-1"], "allowedApps": ["kb-1"],
             "allowedRecordGroups": ["proj-a"], "allowedRecords": []},
            # Same for a connected app that names a whole Collection and a
            # folder somewhere else. The turn asks for the Collection.
            {"apps": ["kb-whole"], "connectionApps": ["kb-whole"],
             "connectionRecordGroups": [], "connectionRecords": ["folder-in-legal"]},
        ],
        ids=[
            "agent-on-whole-apps",
            "agent-allows-more-than-is-asked",
            "collection-agent-named-under-kb",
            "group-on-the-bound-only-widens-it",
            "connection-file-outside-what-was-asked",
        ],
    )
    async def test_a_saved_agent_bound_only_to_apps_is_still_counted(self, filters: dict) -> None:
        """Every saved-agent turn carries these keys, so treating their presence
        as a reason to decline turned the census off for the custom agents it
        exists for. A bound that only limits apps is already in `apps`."""
        graph = MagicMock()
        graph.get_accessible_virtual_record_ids = AsyncMock(return_value=[])
        context = SimpleNamespace(tool_state={}, org_id="org-1", user_id="user-1")
        event_sink = MagicMock()
        event_sink.write = AsyncMock()

        await try_answer_enumeration(
            query="How many documents do we have?",
            context=context,
            retrieval_service=MagicMock(),
            graph_provider=graph,
            filters=filters,
            event_sink=event_sink,
            log=MagicMock(),
        )

        graph.get_accessible_virtual_record_ids.assert_awaited()

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "filters",
        [
            {"apps": ["conn-1", "conn-2"], "allowedApps": ["conn-1"],
             "allowedRecordGroups": [], "allowedRecords": []},
            {"allowedApps": ["conn-1"], "allowedRecordGroups": [], "allowedRecords": []},
            # The Collection under `kb` is outside the bound. The read covers
            # `apps` and `kb` together, so counting here would tell the reader
            # about documents this connection is not allowed to see.
            {"apps": ["conn-1"], "kb": ["kb-outside"], "connectionApps": ["conn-1"],
             "connectionRecordGroups": [], "connectionRecords": []},
            # A bound that names no app admits no app, however many nodes it
            # names: `within_connection({"apps": ["A"]}, {"records": ["R"]})`
            # is this shape, and the read would count the whole of A.
            {"apps": ["conn-1"], "connectionApps": [],
             "connectionRecordGroups": [], "connectionRecords": ["file-1"]},
            # The agent equivalent: an allow-list of groups only.
            {"apps": ["conn-1"], "allowedApps": [],
             "allowedRecordGroups": ["group-1"], "allowedRecords": []},
        ],
        ids=[
            "bound-narrower-than-asked",
            "bound-with-nothing-asked",
            "collection-under-kb-outside-the-bound",
            "connection-names-only-a-file",
            "allow-list-of-groups-only",
        ],
    )
    async def test_a_bound_the_read_would_overshoot_still_defers(self, filters: dict) -> None:
        """The read scopes by `apps`. If the bound admits fewer apps than that
        — or the read is unscoped and the bound is not — it would count
        outside the bound."""
        graph = MagicMock()
        graph.get_accessible_virtual_record_ids = AsyncMock(return_value={"vr1": "rec1"})
        event_sink = MagicMock()
        event_sink.write = AsyncMock()
        context = SimpleNamespace(tool_state={}, org_id="org-1", user_id="user-1")

        answered = await try_answer_enumeration(
            query="How many documents do we have?",
            context=context,
            retrieval_service=MagicMock(),
            graph_provider=graph,
            filters=filters,
            event_sink=event_sink,
            log=MagicMock(),
        )

        assert answered is False
        graph.get_accessible_virtual_record_ids.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_whole_app_scope_is_still_read(self) -> None:
        graph = MagicMock()
        # An unusable shape defers right after the read, so only the call is checked.
        graph.get_accessible_virtual_record_ids = AsyncMock(return_value=[])
        context = SimpleNamespace(tool_state={}, org_id="org-1", user_id="user-1")

        await try_answer_enumeration(
            query="How many documents do we have?",
            context=context,
            retrieval_service=MagicMock(),
            graph_provider=graph,
            filters={"apps": ["drive"]},
            event_sink=MagicMock(),
            log=MagicMock(),
        )

        graph.get_accessible_virtual_record_ids.assert_awaited_once()
