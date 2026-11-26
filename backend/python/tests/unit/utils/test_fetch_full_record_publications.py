"""P2.13 MD-1, MD-2: ``fetch_full_record`` never opens a record that reached the reader through an answer_only
publication; a ``full`` one, or the reader's own access, is served as today."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.agents.actions.knowledge_graph.ops.fetch import _unavailable_note
from app.utils.chat_helpers import get_record
from app.utils.fetch_full_record import (
    PUBLICATION_ANSWER_ONLY,
    _fetch_multiple_records_impl,
)
from harness_core.knowledge.publications import ReadGrant

if TYPE_CHECKING:
    from harness_core.knowledge.publications import ReadDecision


def _map(**extra: Any) -> dict[str, Any]:  # noqa: ANN401
    return {"v1": {"id": "r1", "record_name": "policy.pdf", "record_type": "FILE", "blocks": [], **extra}}


class _Decided:
    """The read gate's decision for every id (H1: decided at the read, not taken from the map)."""

    def __init__(self, decision: ReadDecision) -> None:
        self.decision = decision

    async def decide(self, record_id: str) -> ReadDecision:
        return self.decision

    async def decide_grants(self, record_ids: list[str]) -> dict[str, ReadGrant]:
        return dict.fromkeys(record_ids, ReadGrant(self.decision, "p1" if self.decision.startswith("published") else None))


async def test_md1_an_answer_only_record_is_not_fetched() -> None:
    result = await _fetch_multiple_records_impl(
        ["r1"], _map(publicationId="p1", publicationMode="answer_only"), gate=_Decided("published_answer_only"),  # type: ignore[arg-type]
    )
    assert result["ok"] is False
    assert result["unavailable_reasons"] == {"r1": "publication_answer_only"}
    assert PUBLICATION_ANSWER_ONLY == "publication_answer_only"
    note = _unavailable_note(result["not_available_ids"], result["unavailable_reasons"])
    assert "answers only" in note


@pytest.mark.parametrize(
    ("extra", "decision"),
    [({"publicationId": "p1", "publicationMode": "full"}, "published_full"), ({}, "own")],
)
async def test_md2_a_full_publication_or_own_access_is_served(extra: dict[str, Any], decision: ReadDecision) -> None:
    result = await _fetch_multiple_records_impl(["r1"], _map(**extra), gate=_Decided(decision))  # type: ignore[arg-type]
    assert result["ok"] is True
    assert [r["id"] for r in result["records"]] == ["r1"]


async def test_md1_the_record_map_carries_the_publication_from_search() -> None:
    blob_store = MagicMock()
    # Main's artifact reader passes the record id and org positionally.
    blob_store.get_record_from_storage = AsyncMock(side_effect=lambda *_, **__: {"blocks": []})
    out: dict[str, Any] = {}
    graph_record = {"_key": "r1", "recordType": "FILE", "recordName": "p", "publicationId": "p1", "publicationMode": "answer_only"}
    await get_record("v1", out, blob_store, "o1", {"v1": graph_record})
    assert (out["v1"]["publicationId"], out["v1"]["publicationMode"]) == ("p1", "answer_only")
    own: dict[str, Any] = {}
    await get_record("v1", own, blob_store, "o1", {"v1": {"_key": "r1", "recordType": "FILE", "recordName": "p"}})
    assert "publicationId" not in own["v1"]
