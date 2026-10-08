import json

from app.modules.named_entities import grounding
from app.modules.named_entities.domain.kinds import EntityKind
from app.modules.named_entities.grounding import Grounder
from app.modules.named_entities.normalizers.dates import NormalizationContext
from app.modules.named_entities.strategies.agent.tools import (
    ExtractionSession,
    FinishTool,
    ReadBlocksTool,
    stage_wire_entity,
)
from app.modules.named_entities.text import TextUnit
from app.modules.named_entities.wire import EntitySubmission, WireEntity


def test_grounder_exact_casefold_fuzzy_and_reject():
    grounder = Grounder([TextUnit(0, "b", "Acme Corp signed the agreement")])
    exact = grounder.align(0, "Acme Corp")
    assert exact is not None and exact[3] == "exact"
    folded = grounder.align(0, "acme corp")
    assert folded is not None and folded[3] == "exact"
    fuzzy = grounder.align(0, "Acme Corps")
    assert fuzzy is not None and fuzzy[3] == "fuzzy"
    assert grounder.align(0, "totally missing phrase") is None
    assert "Acme" in grounder.nearest(0, "Acmee Corp")


def test_a_case_insensitive_match_keeps_offsets_after_a_length_changing_letter():
    text = "Straße 5, ACME GmbH"
    start, end, surface, grounding = Grounder([TextUnit(0, "b", text)]).align(0, "acme gmbh")
    assert (surface, grounding) == ("ACME GmbH", "exact")
    assert text[start:end] == surface


def test_a_fuzzy_match_past_the_head_of_a_long_block_is_found_near_its_anchor():
    text = "filler " * 2_000 + "the Acme  Corp agreement"
    start, end, surface, outcome = Grounder([TextUnit(0, "b", text)]).align(0, "Acme Corp")
    assert (surface, outcome) == ("Acme  Corp", "fuzzy")
    assert text[start:end] == surface


def test_the_fuzzy_scan_of_a_huge_block_is_bounded(monkeypatch):
    calls = []
    real = grounding.SequenceMatcher

    def counting(*args):
        calls.append(1)
        return real(*args)

    monkeypatch.setattr(grounding, "SequenceMatcher", counting)
    grounder = Grounder([TextUnit(0, "b", "word " * 200_000)])
    assert grounder.align(0, "Zyxw Qvut") is None
    assert grounder.nearest(0, "Zyxw Qvut") == ""
    assert len(calls) < 5 * grounding._HEAD_CHARS


def test_wire_schema_has_no_unions_or_optional_fields():
    schema = EntitySubmission.model_json_schema()
    blob = json.dumps(schema)
    assert "anyOf" not in blob
    assert "oneOf" not in blob
    entity = schema["$defs"]["WireEntity"]
    assert set(entity["required"]) == {"text", "block", "kind", "normalized"}
    WireEntity.model_validate({"text": "Acme", "block": 0, "kind": "organization", "normalized": ""})
    from anthropic import transform_schema
    from openai.lib._pydantic import to_strict_json_schema

    for converted in (to_strict_json_schema(EntitySubmission), transform_schema(schema)):
        text = json.dumps(converted)
        assert "anyOf" not in text
        assert "oneOf" not in text


def test_submit_is_idempotent_and_rejects_a_secret():
    session = ExtractionSession(
        [TextUnit(0, "b", "Acme owes 4111111111111111")],
        enabled=frozenset({EntityKind.ORGANIZATION}),
        norm_ctx=NormalizationContext(),
        seed_count=0,
    )
    item = WireEntity(text="Acme", block=0, kind="organization", normalized="")
    first = stage_wire_entity(session, 0, item)
    second = stage_wire_entity(session, 1, item)
    assert first["status"] == "accepted"
    assert second["status"] == "accepted"
    assert len(session.staging) == 1
    secret = WireEntity(text="4111111111111111", block=0, kind="organization", normalized="")
    assert stage_wire_entity(session, 2, secret)["status"] == "rejected"


async def test_finish_refuses_unread_blocks():
    units = [TextUnit(index, "b", "x") for index in range(3)]
    session = ExtractionSession(
        units,
        enabled=frozenset({EntityKind.ORGANIZATION}),
        norm_ctx=NormalizationContext(),
        seed_count=0,
    )
    session.read.add(0)
    refused = await FinishTool(session).execute(reason="done")
    assert refused.success is False
    session.read.update({1, 2})
    accepted = await FinishTool(session).execute(reason="done")
    assert accepted.success is True


async def test_every_block_of_a_long_window_is_shown_and_the_tail_is_still_groundable():
    units = [TextUnit(index, "b", "x" * 50_000 + " Globex") for index in range(5)]
    session = ExtractionSession(
        units, enabled=frozenset({EntityKind.ORGANIZATION}), norm_ctx=NormalizationContext(), seed_count=0,
    )
    text = (await ReadBlocksTool(session).execute(start=0, count=5)).data
    assert len(text) < 16_000
    assert all(f"[B{index}]" in text for index in range(5))
    assert text.endswith(f"</{session.boundary}>")
    item = WireEntity(text="Globex", block=4, kind="organization", normalized="")
    assert stage_wire_entity(session, 0, item)["status"] == "accepted"


def test_a_span_is_grounded_only_as_a_whole_word():
    grounder = Grounder([TextUnit(0, "b", "Edward met Ed. Paid $50, then $5.")])
    start, end, surface, _ = grounder.align(0, "Ed")
    assert (start, surface) == (11, "Ed")
    start, _, surface, _ = grounder.align(0, "$5", fuzzy=False)
    assert (start, surface) == (30, "$5")


def test_text_without_spaces_still_grounds():
    grounder = Grounder([TextUnit(0, "b", "株式会社トヨタ自動車と契約した")])
    assert grounder.align(0, "トヨタ自動車")[2] == "トヨタ自動車"


def test_a_value_is_never_grounded_to_a_near_miss():
    grounder = Grounder([TextUnit(0, "b", "The fee is $12,600 per year.")])
    assert grounder.align(0, "$12,500", fuzzy=False) is None
    assert grounder.align(0, "$12,500") is not None


def test_fuzzy_work_is_capped_per_run():
    grounder = Grounder([TextUnit(0, "b", "Acme Robotics signed with Globex")])
    long_surface = "one two three four five six seven eight nine ten eleven"
    assert grounder.align(0, long_surface) is None
    assert grounder.nearest(0, long_surface) == ""
    grounder._fuzzy_left = 0
    assert grounder.align(0, "Acme Robotix") is None


async def test_a_heavy_submit_does_not_stall_the_event_loop(monkeypatch):
    """Staging is CPU work; a fixed blocking delay per item stands in for it, so
    the test costs the same on any machine and fails only if staging runs inline."""
    import asyncio
    import time

    from app.modules.named_entities.domain.kinds import EntityKind
    from app.modules.named_entities.normalizers.dates import NormalizationContext
    from app.modules.named_entities.strategies.agent.tools import (
        ExtractionSession,
        SubmitEntitiesTool,
    )

    def slow_align(self, block_index, surface, *, fuzzy=True):
        time.sleep(0.05)

    monkeypatch.setattr(Grounder, "align", slow_align)
    session = ExtractionSession(
        [TextUnit(0, "b", "Acme")], enabled=frozenset({EntityKind.ORGANIZATION}), norm_ctx=NormalizationContext(), seed_count=0,
    )
    items = [{"text": f"name {i}", "block": 0, "kind": "organization"} for i in range(10)]
    gaps: list[float] = []
    done = asyncio.Event()

    async def tick() -> None:
        last = time.perf_counter()
        while not done.is_set():
            await asyncio.sleep(0.005)
            now = time.perf_counter()
            gaps.append(now - last)
            last = now

    ticker = asyncio.create_task(tick())
    start = time.perf_counter()
    await SubmitEntitiesTool(session).execute(entities=items)
    elapsed = time.perf_counter() - start
    done.set()
    await ticker
    assert elapsed >= 0.5
    assert gaps, "the loop never ran while the submit was staged"
    assert max(gaps) < elapsed / 2
