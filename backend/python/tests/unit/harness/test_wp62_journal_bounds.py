"""WP-62 (WP62-JRNL-2..5, WP62-PART-2..3): no journal write that carries tool output can pass the store's limit.

A recorded child: ``journal.rejected operation=content.put reason=CONTENT_TOO_LARGE status=413`` right after
``engine.kernel.cut_short``. The refused put was the child's result document: ``EngineChildResult`` added the run's
whole citation state (every web block read, two 4.2 MB GitHub tree listings among them, the journal's
``x.pipeshub.citations.recorded`` payloads of 4,201,896 and 4,150,374 bytes), about 8.7 MB against Node's 8 MiB.
Each write that carries tool output is now bounded where it is made; this guard drives 20 MB of tool output through
every one of them.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock

from app.agents.agent_loop.context import AgentContext
from app.harness.assembly import handoff_instructions as instructions
from app.harness.assembly.deep_route import ENGINE_FAILED_INSTRUCTION
from app.harness.citations import DELTA_MAX_BYTES, CitationJournal
from app.harness.engines.child_result import cited_sources
from harness_core.contracts import canonical_json
from harness_core.contracts.generated.common import ContentRef
from harness_core.effects.journal import JournalEffectLog
from harness_core.effects.results import decode_result
from harness_core.effects.types import ToolExecution
from harness_core.engines.outcome.briefing import ChildReport
from harness_core.journal.bounds import (
    ANSWER_MAX_BYTES,
    CHILD_RESULT_MAX_BYTES,
    CONTENT_MAX_BYTES,
    TOOL_RESULT_MAX_BYTES,
    bounded_child_document,
    bounded_text,
)
from harness_core.journal.children import STOPPED_EARLY_NOTES, child_result_document
from harness_core.journal.memory import InMemoryContentStore

if TYPE_CHECKING:
    from harness_core.kernel.durable.harness_channel import HarnessInstruction


def engine_failed_instruction(content: object) -> HarnessInstruction:
    """WP-89: the failed hand-off's instruction is the outcome briefing's, read once from the child's report."""
    return instructions.engine_failed_briefed(ChildReport.read(content))


HUGE = 20 * 1024 * 1024
# JSON text escapes into more bytes than it has characters: the worst case for a bound counted in characters.
QUOTED = '{"path": "a"}' * (HUGE // 13)


class _Sized(InMemoryContentStore):
    largest = 0

    async def put(self, data: bytes) -> ContentRef:
        _Sized.largest = max(_Sized.largest, len(data))
        assert len(data) <= CONTENT_MAX_BYTES, len(data)
        return await super().put(data)


async def test_wp62_jrnl_2_a_tool_result_of_20_mb_is_journaled_bounded_and_says_so() -> None:
    store = _Sized()
    log = JournalEffectLog(MagicMock(), store, MagicMock())
    for content in ("x" * HUGE, QUOTED, {"rows": ["y" * 1000] * 20_000}):
        event = await log.outcome_event("inv-1", ToolExecution(status="ok", content=content))
        ref = ContentRef.model_validate(event.data["resultRef"])
        kept, _ = decode_result(await store.get(ref))
        assert ref.size <= TOOL_RESULT_MAX_BYTES + 1024
        assert isinstance(kept, str) and "were not kept" in kept


async def test_wp62_jrnl_2_a_normal_result_is_untouched() -> None:
    store = InMemoryContentStore()
    log = JournalEffectLog(MagicMock(), store, MagicMock())
    event = await log.outcome_event("inv-1", ToolExecution(status="ok", content={"a": 1}))
    kept, _ = decode_result(await store.get(ContentRef.model_validate(event.data["resultRef"])))
    assert kept == {"a": 1}


def _context(web_records: list[dict[str, Any]], refs: dict[str, str]) -> AgentContext:
    context = AgentContext(
        org_id="org-1", user_id="u", user_email="u@example.com", user_info={"userId": "u", "orgId": "org-1"},
        org_info={"name": "T"}, logger=MagicMock(), llm=MagicMock(), retrieval_service=MagicMock(),
        graph_provider=MagicMock(), config_service=MagicMock(), blob_store=MagicMock(),
    )
    mapper = MagicMock()
    mapper.url_to_ref = refs
    context.tool_state["citation_ref_mapper"] = mapper
    context.tool_state["web_records"] = web_records
    return context


def _recorded_records() -> tuple[list[dict[str, Any]], dict[str, str]]:
    records = [
        {"url": f"https://api.github.com/tree#{i}", "content": QUOTED[:2000], "title": "tree"} for i in range(4400)
    ] + [{"url": "https://docs.pipeshub.com/#a", "content": "PipesHub indexes permissions.", "title": "docs"}]
    refs = {r["url"]: f"ref{n + 1}" for n, r in enumerate(records)}
    return records, refs


def test_wp62_jrnl_3_a_citation_delta_stays_under_its_bound() -> None:
    records, refs = _recorded_records()
    assert len(canonical_json(records)) > CONTENT_MAX_BYTES  # the recorded size, uncut
    delta = CitationJournal(_context(records, refs)).delta("call-1")
    assert delta is not None
    assert len(canonical_json(delta)) <= DELTA_MAX_BYTES


def test_wp62_jrnl_4_a_childs_sources_are_the_pages_its_answer_cites() -> None:
    records, refs = _recorded_records()
    sources = {"refs": [{"url": u, "ref": r} for u, r in refs.items()], "webRecords": records}
    cited = f"Onyx and PipesHub differ [docs]({refs['https://docs.pipeshub.com/#a']})."
    narrowed = cited_sources(sources, cited)
    assert narrowed["refs"] == [{"url": "https://docs.pipeshub.com/#a", "ref": "ref4401"}]
    assert [r["url"] for r in narrowed["webRecords"]] == ["https://docs.pipeshub.com/#a"]


def test_wp62_jrnl_5_a_child_document_too_large_keeps_its_answer_and_partial_flag() -> None:
    document = child_result_document(outcome="answered", output="partial findings " * 10, truncated_by="stall")
    document["x.pipeshub.citations"] = {"webRecords": [{"content": QUOTED[: 9 * 1024 * 1024]}]}
    document["files"] = [{"artifactId": "a1", "fileName": "report.md"}]
    bounded, cut = bounded_child_document(document)
    assert cut is True
    assert len(canonical_json(bounded)) <= CHILD_RESULT_MAX_BYTES
    assert bounded["output"] == document["output"]
    assert bounded["truncatedBy"] == "stall" and "stopped_early" in bounded
    assert bounded["files"] == document["files"]
    assert "x.pipeshub.citations" not in bounded

    long_answer = child_result_document(outcome="answered", output=QUOTED[: 5 * 1024 * 1024])
    bounded, cut = bounded_child_document(long_answer)
    assert cut and len(canonical_json(bounded)) <= CHILD_RESULT_MAX_BYTES
    assert "were not kept" in str(bounded["output"])


def test_wp62_jrnl_5_a_final_answer_is_bounded() -> None:
    assert len(canonical_json(bounded_text(QUOTED, ANSWER_MAX_BYTES))) <= ANSWER_MAX_BYTES
    assert bounded_text("short", ANSWER_MAX_BYTES) == "short"


def test_wp62_part_2_a_stalled_childs_note_says_why_it_stopped() -> None:
    assert "not making progress" in STOPPED_EARLY_NOTES["stall"]
    assert "spending limit" not in STOPPED_EARLY_NOTES["stall"]
    document = child_result_document(outcome="answered", output="x", truncated_by="stall")
    assert document["stopped_early"] == STOPPED_EARLY_NOTES["stall"]


def test_wp62_part_3_a_failed_child_with_saved_work_is_not_said_to_have_returned_nothing() -> None:
    """The recorded code_task result: failed, its fork at revision 25, not merged."""
    content = {
        "error": "commit_rejected",
        "output": "",
        "success": False,
        "workspace": {
            "fork": {"revision": 25, "workspaceId": "ws:s:fork:c"},
            "reason": "child_failed",
            "status": "not_merged",
        },
    }
    said = engine_failed_instruction(content).text
    assert "returned no answer" not in said
    assert "25 versions of its working files" in said and "'continue'" in said
    assert engine_failed_instruction({"error": "crash_loop", "success": False}).text == ENGINE_FAILED_INSTRUCTION
