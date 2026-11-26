"""P4.17 OB-1..OB-3, RP-1..RP-6: the per-purpose output limits and the repetition cut (``output_bound``)."""

from __future__ import annotations

import itertools
import json
import random
from typing import TYPE_CHECKING

import pytest

from harness_core.kernel.core.messages import AssistantMessage, TextPart
from harness_core.kernel.core.responses import ModelResponse, StopReason, TokenUsage
from harness_core.kernel.core.streaming import (
    StreamCompleteEvent,
    TextDeltaEvent,
    ThinkingDeltaEvent,
    ToolCallDeltaEvent,
)
from harness_core.models.output_bound import (
    OFF,
    REPEAT_SPAN,
    SHORTENED_NOTICE,
    OutputLimits,
    RepetitionWatch,
    before_loop,
    bound_response,
    bound_stream,
    shortened,
    thinking_within,
    within,
)
from tests.support.cpu_budget import thread_cpu

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterable

    from harness_core.kernel.core.streaming import StreamEvent


# ---- OB: limits ----


@pytest.mark.parametrize(
    ("purpose", "model", "expected"),
    [
        ("turn", "gpt-6-luna", 32_768),
        ("engine", "gpt-6-luna", 32_768),
        ("structured", "gpt-6-luna", 8_192),
        ("summary", "gpt-6-luna", 8_192),
        # gpt-4.1's published maximum (32,768) is the bound when it is lower than a purpose's.
        ("turn", "gpt-4.1", 32_768),
        ("turn", "an-unknown-model", 32_768),
    ],
)
def test_ob1_each_purpose_has_its_limit_capped_by_the_model(purpose: str, model: str, expected: int) -> None:
    assert OutputLimits().for_call(purpose, model) == expected  # pyright: ignore[reportArgumentType]


def test_ob1_a_model_maximum_below_the_purpose_wins() -> None:
    limits = OutputLimits(turn=200_000)
    assert limits.for_call("turn", "gpt-6-luna") == 128_000


def test_ob1_an_engine_keeps_its_own_smaller_limit() -> None:
    limits = OutputLimits()
    assert limits.for_engine(4_000, "gpt-6-luna") == 4_000
    assert limits.for_engine(100_000, "gpt-6-luna") == 32_768
    assert limits.for_engine(None, "gpt-6-luna") == 32_768


def test_ob2_off_sends_no_limit_and_does_not_cut() -> None:
    assert OutputLimits.from_turn_limit(0) == OFF
    assert OutputLimits.from_turn_limit(None) == OFF
    for purpose in ("turn", "engine", "structured", "summary"):
        assert OFF.for_call(purpose, "gpt-6-luna") is None  # pyright: ignore[reportArgumentType]
    assert OFF.for_engine(5_000, "gpt-6-luna") == 5_000
    assert OFF.repetition is False


def test_ob2_one_number_sets_turn_and_engine_and_caps_the_smaller_purposes() -> None:
    assert OutputLimits.from_turn_limit(6_000) == OutputLimits(6_000, 6_000, 6_000, 6_000)
    assert OutputLimits.from_turn_limit(64_000) == OutputLimits(64_000, 8_192, 8_192, 64_000)


def test_ob3_a_thinking_budget_leaves_the_reply_room_inside_the_limit() -> None:
    assert thinking_within(32_768, 64_000) == 32_768 - 4_096
    assert thinking_within(32_768, 2_048) == 2_048
    assert thinking_within(None, 64_000) == 64_000
    assert thinking_within(32_768, None) is None
    # Never below the providers' floor for an enabled budget.
    assert thinking_within(1_200, 64_000) == 1_024


def test_ob3_a_limit_lowers_a_configured_cap_never_raises_it() -> None:
    assert within(16_384, 32_768) == 16_384
    assert within(64_000, 32_768) == 32_768
    assert within(None, 32_768) == 32_768
    assert within(4_096, None) == 4_096


# ---- RP: the repetition cut ----


async def _events(source: Iterable[StreamEvent]) -> AsyncIterator[StreamEvent]:
    for event in source:
        yield event


class _Closing:
    """An endless provider stream that records being closed."""

    def __init__(self, events: Iterable[StreamEvent]) -> None:
        self._events = iter(events)
        self.closed = False
        self.sent = 0

    def __aiter__(self) -> _Closing:
        return self

    async def __anext__(self) -> StreamEvent:
        self.sent += 1
        return next(self._events)

    async def aclose(self) -> None:
        self.closed = True


async def _drain(stream: AsyncIterator[StreamEvent]) -> list[StreamEvent]:
    return [event async for event in stream]


def _final(events: list[StreamEvent]) -> ModelResponse:
    last = events[-1]
    assert isinstance(last, StreamCompleteEvent)
    return last.response


async def test_rp1_an_endless_repeating_text_is_cut_at_the_bound() -> None:
    unit = "The renewal date has passed. "
    inner = _Closing(TextDeltaEvent(delta=unit) for _ in itertools.count())
    events = await _drain(bound_stream(_events_of(inner), model="m"))

    response = _final(events)
    streamed = sum(len(e.delta) for e in events if isinstance(e, TextDeltaEvent))
    assert streamed <= REPEAT_SPAN + 256 + 2 * len(unit)
    assert response.message.truncated and response.message.cut == "repetition"
    assert response.stop_reason == StopReason.MAX_TOKENS
    assert response.usage == TokenUsage()
    assert inner.closed


def _events_of(inner: _Closing) -> AsyncIterator[StreamEvent]:
    return inner  # pyright: ignore[reportReturnType]


async def test_rp2_repetition_in_a_tool_calls_arguments_drops_the_arguments() -> None:
    head = [ToolCallDeltaEvent(index=0, id="c1", name="create_document", arguments_delta='{"body": "')]
    loop = (ToolCallDeltaEvent(index=0, arguments_delta="| 0 | 0 | 0 |\n") for _ in itertools.count())
    inner = _Closing(itertools.chain(head, loop))
    response = _final(await _drain(bound_stream(_events_of(inner), model="m")))

    assert response.message.cut == "repetition" and response.message.truncated
    [call] = response.message.tool_calls or []
    assert (call.id, call.name, call.arguments) == ("c1", "create_document", {})
    assert inner.closed


async def test_rp3_repetition_in_visible_reasoning_is_cut() -> None:
    inner = _Closing(ThinkingDeltaEvent(delta="Wait, let me reconsider. ") for _ in itertools.count())
    response = _final(await _drain(bound_stream(_events_of(inner), model="m")))
    assert response.message.cut == "repetition"
    assert response.message.text == "" and not response.message.tool_calls


def _deck_script() -> str:
    """od-15-competitor-deck's deliverable as the engine writes it: a python-pptx script, one block per slide."""
    slides = [
        ("Arm-200 vs Kinetica Arms", ["Payload 12 kg vs 9 kg", "Reach 1,350 mm vs 1,100 mm", "Repeatability 0.03 mm"]),
        ("Price and total cost", ["List USD 41,500 vs USD 38,900", "Service plan 7% vs 11% of list", "3-year TCO lower"]),
        ("Safety", ["SafeStop certified PL d", "Kinetica: PL c, retrofit kit", "False stops: P2 open on line 3"]),
        ("Customers", ["Acme Corp renewal due 31 Jan", "Globex pilot of 4 arms", "Initech evaluating both"]),
        ("Recommendation", ["Lead with payload and TCO", "Address SafeStop false stops", "Offer a 60-day pilot"]),
    ]
    lines = ["from pptx import Presentation", "from pptx.util import Inches, Pt", "", "prs = Presentation()"]
    # The deck plus its appendix: each topic again per region, as a 30-slide deck the engine writes in one call.
    regions = ["", " (EMEA)", " (APAC)", " (Americas)", " (public sector)", " (partners)"]
    slides = [(title + region, bullets) for region in regions for title, bullets in slides]
    for n, (title, bullets) in enumerate(slides, 1):
        lines += [
            f"# Slide {n}: {title}",
            f"slide{n} = prs.slides.add_slide(prs.slide_layouts[1])",
            f"slide{n}.shapes.title.text = {title!r}",
            f"body{n} = slide{n}.placeholders[1].text_frame",
            f"body{n}.text = {bullets[0]!r}",
        ]
        for bullet in bullets[1:]:
            lines += [f"p = body{n}.add_paragraph()", f"p.text = {bullet!r}", "p.level = 1", "p.font.size = Pt(18)"]
        lines.append(f"slide{n}.notes_slide.notes_text_frame.text = 'Source: [{n}] comparison sheet'")
    lines.append("prs.save('/output/vs_kinetica.pptx')")
    return "\n".join(lines)


def _report_with_table() -> str:
    rows = "\n".join(
        f"| TKT-{1000 + i} | {['P1', 'P2', 'P3'][i % 3]} | {['open', 'resolved', 'triage'][i % 3]} | "
        f"{(i * 37) % 90 + 1} h | line {i % 7 + 1} |"
        for i in range(800)
    )
    return (
        "# Q3 incident report\n\n## Summary\n\nFour incidents breached the SLA [1][2].\n\n"
        "| Ticket | Priority | State | Time to resolve | Where |\n|---|---|---|---|---|\n" + rows
    )


def _csv() -> str:
    rng = random.Random(7)
    return "month,region,units,revenue\n" + "\n".join(
        f"2026-{m:02d},{r},{rng.randint(10, 999)},{rng.randint(1_000, 99_999)}.{rng.randint(0, 99):02d}"
        for m in range(1, 13)
        for r in ("north", "south", "east", "west", "central")
        for _ in range(40)
    )


def _json_records() -> str:
    return json.dumps(
        [{"id": f"rec-{i}", "type": "issue", "status": "open", "priority": "P2", "score": i * 3 % 17} for i in range(600)]
    )


def _code_file() -> str:
    return "\n\n".join(
        f"def handler_{i}(event: dict) -> dict:\n    value = event.get('field_{i}', {i})\n"
        f"    if value > {i * 3}:\n        return {{'ok': True, 'n': value - {i}}}\n    return {{'ok': False}}"
        for i in range(300)
    )


@pytest.mark.parametrize("make", [_deck_script, _report_with_table, _csv, _json_records, _code_file])
async def test_rp4_a_long_legitimate_deliverable_is_not_cut(make: object) -> None:
    text: str = make()  # pyright: ignore[reportCallIssue, reportAssignmentType]
    assert len(text) > 12_000
    chunks = [text[i : i + 7] for i in range(0, len(text), 7)]
    final = StreamCompleteEvent(
        response=ModelResponse(message=AssistantMessage(content=[TextPart(text=text)]), model="m")
    )
    events = await _drain(bound_stream(_events([*(TextDeltaEvent(delta=c) for c in chunks), final]), model="m"))
    assert events[-1] is final
    assert bound_response(final.response) is final.response


async def test_rp4_a_long_deliverable_in_a_tool_call_is_not_cut() -> None:
    args = json.dumps({"code": _deck_script()})
    deltas = [ToolCallDeltaEvent(index=0, id="c", name="run_code", arguments_delta=args[i : i + 9]) for i in range(0, len(args), 9)]
    final = StreamCompleteEvent(response=ModelResponse(message=AssistantMessage(content=[]), model="m"))
    events = await _drain(bound_stream(_events([*deltas, final]), model="m"))
    assert events[-1] is final


async def test_rp5_the_text_is_trimmed_before_the_loop() -> None:
    intro = "Acme's renewal is due 31 January 2026. The clearest risk is the SafeStop issue. "
    unit = "It stops twice a week. "
    deltas = [TextDeltaEvent(delta=intro)] + [TextDeltaEvent(delta=unit) for _ in range(2_000)]
    kept = _final(await _drain(bound_stream(_events(deltas), model="m"))).message.text
    # One unit of the loop survives, possibly rotated onto the intro's matching tail (". ").
    assert kept.startswith(intro[:-2])
    assert kept.count("It stops twice a week") == 1
    assert len(kept) <= len(intro) + len(unit)


def test_rp5_before_loop_keeps_one_unit() -> None:
    assert before_loop("intro abcabcabcabc", 3) == "intro abc"
    assert before_loop("abcabc", 3) == "abc"


def test_rp6_the_watch_costs_little_on_ordinary_text_and_cuts_a_loop_promptly() -> None:
    rng = random.Random(1)
    words = ["revenue", "grew", "by", "the", "quarter", "renewal", "incident", "line", "arm", "risk", "and", "of"]
    text = " ".join(rng.choice(words) + str(rng.randint(0, 99)) for _ in range(70_000))[:500_000]
    watch = RepetitionWatch()
    with thread_cpu() as cpu:
        assert all(watch.feed(text[i : i + 5]) is None for i in range(0, len(text), 5))
    # ~0.16 s alone; checking on every delta instead of every 256 characters costs ~9 s.
    assert cpu.seconds < 2.0, cpu.seconds

    looping = RepetitionWatch()
    fed = 0
    for _ in range(10_000):
        fed += 4
        if looping.feed("abc ") is not None:
            break
    assert fed <= REPEAT_SPAN + 256 + 4


def test_rp6_a_whitespace_run_is_a_loop() -> None:
    watch = RepetitionWatch()
    assert any(watch.feed(" \n") is not None for _ in range(5_000))


def test_bound_response_trims_a_complete_reply_that_loops() -> None:
    text = "Summary. " + "All good. " * 2_000
    response = ModelResponse(message=AssistantMessage(content=[TextPart(text=text)]), usage=TokenUsage(output_tokens=9))
    bounded = bound_response(response)
    assert bounded.message.text.startswith("Summary")
    assert bounded.message.text.count("All good") == 1
    assert bounded.message.truncated and bounded.message.cut == "repetition"
    assert bounded.stop_reason == StopReason.MAX_TOKENS
    assert bounded.usage == response.usage


def test_ux6_a_shortened_answer_says_so() -> None:
    assert shortened("The report.  ") == f"The report.\n\n{SHORTENED_NOTICE}"
