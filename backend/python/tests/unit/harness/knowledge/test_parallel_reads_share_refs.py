"""Two records read side by side cite their own blocks (integration run 46048112752, q5 bob-agent).

The agent's first knowledge call was an entity search, which mints no citation refs, so the run had no ref mapper
yet. It then fetched the pricing strategy and the #pricing-committee thread in one turn. Each read made its own
mapper and numbered its blocks from ref1; the one stored last won, so the answer's refs to the strategy's blocks
resolved to the thread's blocks of the same number, and the strategy was never cited.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from app.harness.knowledge.knowledge_service import (
    FetchRequest,
    KnowledgeBackends,
    KnowledgeCall,
    KnowledgeModel,
    KnowledgePrincipal,
    KnowledgeService,
    PinnedKnowledge,
)
from app.models.blocks import BlockType
from app.utils.chat_helpers import build_block_web_url
from harness_core.gateway.memory import refs_in

_FRONTEND = "https://app.test"


def _record(record_id: str, blocks: int) -> dict[str, Any]:
    return {
        "id": record_id,
        "virtual_record_id": f"vr-{record_id}",
        "record_name": record_id,
        "frontend_url": _FRONTEND,
        "context_metadata": f"Record ID: {record_id}\nName: {record_id}",
        "block_containers": {
            "blocks": [
                {"index": i, "type": BlockType.TEXT.value, "data": f"{record_id} block {i}"} for i in range(blocks)
            ],
            "block_groups": [],
        },
    }


_RECORDS = {"drive-pricing-2026": _record("drive-pricing-2026", 3), "slack-pricing-0402": _record("slack-pricing-0402", 3)}


def _reader() -> MagicMock:
    """The record read, which waits on the graph and blob store before it returns, as the real one does."""

    async def read(record_ids: list[str], reason: str) -> dict[str, Any]:
        await asyncio.sleep(0)
        return {"ok": True, "records": [_RECORDS[r] for r in record_ids], "not_available_ids": []}

    tool = MagicMock()
    tool.coroutine = read
    return tool


async def test_reads_run_side_by_side_number_their_blocks_in_one_mapper() -> None:
    graph = AsyncMock()
    graph.config_service = MagicMock()
    service = KnowledgeService(KnowledgeBackends(retrieval=None, graph=graph, config_service=MagicMock()))
    call = KnowledgeCall(
        KnowledgePrincipal(org_id="org-1", user_id="bob"),
        PinnedKnowledge(filters={}),
        KnowledgeModel(context_length=128_000),
        session={},  # no mapper yet: nothing before these reads minted a ref
    )

    with patch("app.utils.fetch_full_record.create_fetch_full_record_tool", return_value=_reader()):
        outputs = await asyncio.gather(*(
            service.fetch(call, FetchRequest(record_ids=[record_id], reason="read it")) for record_id in _RECORDS
        ))

    ref_to_url = call.session["citation_ref_mapper"].ref_to_url
    for record_id, output in zip(_RECORDS, outputs):
        assert output.success, output.error
        shown = refs_in(str(output.data).split("</record>")[0])  # the blocks, not the citing hint's example
        assert len(shown) == 3, output.data
        # Every ref a result showed resolves to that record's own block, in the mapper the answer is resolved with.
        assert [ref_to_url.get(ref) for ref in shown] == [
            build_block_web_url(_FRONTEND, record_id, i) for i in range(3)
        ]
