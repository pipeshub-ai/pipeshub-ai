"""A record id that does not resolve gets pointed back at the ids the model was shown.

The 2026-10-09 nightly demo run asked for "call notes" by an id whose first
three groups were right and the rest invented; the reply gave the model
nothing to recover with. These tests drive `execute_fetch_record` through the
real `_fetch_multiple_records_impl`, against a fake graph that answers the
access check the way the real stores do: no details for a record that is
missing and no details for one this person may not read.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock, patch

import pytest

from app.agents.actions.knowledge_graph.ops.fetch import execute_fetch_record
from app.modules.agents.qna.chat_state import remember_record_ids

if TYPE_CHECKING:
    from app.agent_loop_lib.tools.base import ToolOutput

MISTYPED = "8a2cfb7d-04fc-4647-8d43-5d0527800009"
REAL = "8a2cfb7d-04fc-4647-a5db-4602ab67b1f0"
OTHER = "3f9c2e10-7b44-4d21-9e0a-1c2b3d4e5f60"
UNRELATED = "c0ffee00-1111-4222-8333-944445555666"

TODAYS_MESSAGE = (
    "None of the requested records were available.\n\n"
    f"Note: '{MISTYPED}': not available"
)


class _Graph:
    """Answers like Neo4j/Arango: `None` for a record that is missing and for
    one the user may not read. Records every call so the two cases can be
    shown to take the same path."""

    def __init__(self, readable_by: dict[str, set[str]] | None = None) -> None:
        self._readable_by = readable_by or {}
        self.calls: list[str] = []
        self.config_service = None

    async def check_record_access_with_details(
        self, user_id: str, org_id: str, record_id: str,
    ) -> dict[str, Any] | None:
        self.calls.append("check_record_access_with_details")
        if user_id in self._readable_by.get(record_id, set()):
            return {"_key": record_id}
        return None

    async def get_document(self, document_key: str, collection: str) -> dict[str, Any] | None:
        self.calls.append("get_document")
        return None


def _context(tool_state: dict[str, Any], graph: _Graph | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        org_id="org-1",
        user_id="user-1",
        graph_provider=graph or _Graph(),
        full_records_fetched=set(),
        tool_state=tool_state,
        is_multimodal_llm=False,
        context_length=128_000,
        query="",
        retrieval_service=None,
    )


async def _fetch(
    record_ids: list[str], tool_state: dict[str, Any], graph: _Graph | None = None,
) -> ToolOutput:
    output, _ = await execute_fetch_record(
        context=_context(tool_state, graph),
        virtual_records={},
        citation_ref_mapper=None,
        record_ids=record_ids,
    )
    return output


def _shown(names: dict[str, str]) -> dict[str, Any]:
    state: dict[str, Any] = {}
    remember_record_ids(state, list(names), names=names)
    return state


@pytest.mark.asyncio
class TestUnknownIdRecovery:
    async def test_lists_earlier_records_and_names_the_closest(self) -> None:
        state = _shown({OTHER: "Q3 roadmap", REAL: "Call notes"})

        output = await _fetch([MISTYPED], state)

        assert output.success is False
        assert output.error.startswith(TODAYS_MESSAGE)
        assert "Records returned earlier in this conversation:" in output.error
        assert f"- {REAL} (Call notes)" in output.error
        assert f"- {OTHER} (Q3 roadmap)" in output.error
        assert f"The closest to the id you used is {REAL} (Call notes)." in output.error

    async def test_no_earlier_records_keeps_todays_message(self) -> None:
        output = await _fetch([MISTYPED], {})

        assert output.success is False
        assert output.error == TODAYS_MESSAGE

    async def test_missing_and_unreadable_ids_get_byte_identical_replies(self) -> None:
        """Otherwise the hint becomes a way to learn that a hidden record exists."""
        missing_graph = _Graph()
        unreadable_graph = _Graph(readable_by={MISTYPED: {"someone-else"}})

        missing = await _fetch([MISTYPED], _shown({REAL: "Call notes"}), missing_graph)
        unreadable = await _fetch([MISTYPED], _shown({REAL: "Call notes"}), unreadable_graph)

        assert missing.error.encode() == unreadable.error.encode()
        assert missing.success is unreadable.success is False
        assert missing_graph.calls == unreadable_graph.calls

    async def test_list_holds_only_the_twenty_most_recent(self) -> None:
        ids = [f"{i:08x}-0000-4000-8000-000000000000" for i in range(30)]
        state: dict[str, Any] = {}
        for i, rid in enumerate(ids):
            remember_record_ids(state, [rid], names={rid: f"Doc {i}"})

        output = await _fetch([UNRELATED], state)

        listed = [line for line in output.error.splitlines() if line.startswith("- ")]
        assert listed == [f"- {ids[i]} (Doc {i})" for i in range(29, 9, -1)]

    async def test_an_unrelated_id_names_no_closest(self) -> None:
        state = _shown({OTHER: "Q3 roadmap", REAL: "Call notes"})

        output = await _fetch([UNRELATED], state)

        assert f"- {REAL} (Call notes)" in output.error
        assert "closest" not in output.error

    async def test_a_one_character_slip_anywhere_is_named(self) -> None:
        slipped = "9" + REAL[1:]
        state = _shown({OTHER: "Q3 roadmap", REAL: "Call notes"})

        output = await _fetch([slipped], state)

        assert f"The closest to the id you used is {REAL} (Call notes)." in output.error

    async def test_two_equally_close_ids_name_neither(self) -> None:
        twin = "8a2cfb7d-04fc-4647-ffff-000000000000"
        state = _shown({REAL: "Call notes", twin: "Call notes (copy)"})

        output = await _fetch([MISTYPED], state)

        assert "closest" not in output.error

    async def test_shortened_ids_are_listed_as_the_model_saw_them(self) -> None:
        state = _shown({REAL: "Call notes"})
        shortener = MagicMock()
        shortener.resolve = MagicMock(side_effect=lambda rid: rid)
        shortener.shorten_if_known = MagicMock(
            side_effect=lambda rid: "R1" if rid == REAL else rid,
        )

        with patch(
            "app.utils.chat_helpers.get_record_id_shortener_if_enabled",
            return_value=shortener,
        ):
            output = await _fetch([MISTYPED], state)

        assert "- R1 (Call notes)" in output.error
        assert "The closest to the id you used is R1 (Call notes)." in output.error
        assert REAL not in output.error

    async def test_a_partly_read_batch_points_the_failed_id_elsewhere(self) -> None:
        state = _shown({OTHER: "Q3 roadmap", REAL: "Call notes"})
        read_now = {"id": OTHER, "record_name": "Q3 roadmap", "virtual_record_id": "vr-1"}
        ref_mapper = MagicMock()

        with patch(
            "app.utils.chat_helpers.record_to_message_content",
            return_value=([{"type": "text", "text": "content"}], ref_mapper),
        ):
            output, _ = await execute_fetch_record(
                context=_context(state),
                virtual_records={"vr-1": read_now},
                citation_ref_mapper=ref_mapper,
                record_ids=[OTHER, MISTYPED],
            )

        assert output.success is True
        assert f"The closest to the id you used is {REAL} (Call notes)." in output.data
        assert f"- {OTHER}" not in output.data

    async def test_a_record_still_indexing_gets_no_suggestions(self) -> None:
        """The id was right; pointing elsewhere would mislead."""
        state = _shown({REAL: "Call notes"})
        fake_tool = MagicMock()

        async def _result(**_: object) -> dict[str, Any]:
            return {
                "ok": False,
                "error": "None of the requested records were available.",
                "not_available_ids": [MISTYPED],
                "unavailable_reasons": {MISTYPED: "not_indexed_yet"},
            }

        fake_tool.coroutine = _result
        with patch(
            "app.utils.fetch_full_record.create_fetch_full_record_tool",
            return_value=fake_tool,
        ):
            output = await _fetch([MISTYPED], state)

        assert "Records returned earlier" not in output.error
