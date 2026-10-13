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

