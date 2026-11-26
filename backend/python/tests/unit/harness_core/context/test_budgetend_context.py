"""BUDGETEND Fix 2: why one Slack question burned about a million tokens, reproduced and fixed.

A recorded run: nine calls of 13k-150k input tokens on a 128k window; four compactions, each at 108k-135k, and the
next request still 108k-150k; about 78k tokens for each summary on top. What made up the request after a compaction:
the system prompt and tools (13k, the cached prefix), the summary (2k), and the kept tail of raw tool results: three
turns kept by count, each result up to 64,000 characters (Slack search pages are 70k-275k characters, at about 2.8
characters a token). The summary itself re-read every result of its range whole.

The fixes, each a function of the journal and the model alone (the cached prefix holds):

* the results of one batch share 12% of the window (``batch_result_cap``), with the result's artifact named to read
  the rest;
* the kept tail is at most a quarter of the window (fewer turns, never fewer than one), and a compaction that would
  repeat itself escalates to keeping the newest turn alone;
* the summary reads at most a quarter of the window.

``test_recorded_shape_*`` replays the recorded call pattern (the same result sizes per turn) through the durable
kernel, before (the bounds off) and after, and checks the request after a compaction and the run's total tokens.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any

from harness_core.access.spend import input_estimate
from harness_core.context import HostBands, LadderPolicy, ModelCompaction
from harness_core.context.compaction import bounded_transcript
from harness_core.context.ladder import forced_compaction
from harness_core.context.results import (
    BATCH_RESULT_FLOOR,
    RESULT_CAP_CHARS,
    batch_result_cap,
    cap_result,
)
from harness_core.context.types import HistoryItem
from harness_core.kernel.core.messages import (
    AssistantMessage,
    TextPart,
    ToolCall,
    ToolMessage,
    ToolMessageMeta,
    UserMessage,
)
from harness_core.kernel.core.responses import ModelResponse, TokenUsage
from harness_core.kernel.durable import ToolExecution
from harness_core.kernel.durable.context_compiler import ContextCompiler
from harness_core.testing import Reply, ToolCallSpec
from tests.unit.harness_core.durable.support import World, scripted

from .support import tool

if TYPE_CHECKING:
    from harness_core.kernel.durable.ports import ModelCall, SampledTurn
    from harness_core.kernel.fold import RunState
    from harness_core.testing.durable import ScriptedModelPort

WINDOW = 128_000
# The result sizes (characters) of the recorded run's nine turns, as journaled (tool.outcome resultRef.size).
RECORDED_TURNS: tuple[tuple[int, ...], ...] = (
    (2_526,),
    (3_005, 3_357, 4_436),
    (96_451,),
    (275_765, 275_800, 84_581),
    (200_045, 396, 389, 384),
    (28_786,),
    (5_905, 2_880, 70_547, 47_931, 271_647, 13_730, 5_230, 94_303),
    (26_649, 136, 4_818, 2_995, 260, 70_045),
    (28_244, 17_827, 411, 25_317),
)
# The recorded run's system prompt and pinned tools came to 13.4k tokens.
SYSTEM = "You are the agent. " + "Operating rules. " * 3_100


# --- the pieces ------------------------------------------------------------------------------------------------


def test_a_batch_shares_a_slice_of_the_window() -> None:
    one = batch_result_cap(1, WINDOW)
    eight = batch_result_cap(8, WINDOW)
    assert one <= RESULT_CAP_CHARS and eight < one
    assert eight == max(one // 8, BATCH_RESULT_FLOOR)
    assert batch_result_cap(100, WINDOW) == BATCH_RESULT_FLOOR
    assert batch_result_cap(3, None) == RESULT_CAP_CHARS, "no window: the per-result cap as before"


def test_a_cut_result_names_its_artifact_to_read_the_rest() -> None:
    meta = ToolMessageMeta(artifact_id="art-1", tool_name="slack__search_messages", turn_index=3)
    cut = cap_result(ToolMessage(content="y" * 50_000, tool_call_id="c1", artifact_meta=meta), 8_000)
    assert 'retrieve_artifact_content(artifact_id="art-1")' in cut.text
    assert len(cut.text) < 8_200
    assert cap_result(cut, 8_000) is cut, "a cut result is not cut again"


def _turn(seq: int, sizes: tuple[int, ...]) -> HistoryItem:
    calls = [ToolCall(id=f"c{seq}-{i}", name="slack", arguments={}) for i in range(len(sizes))]
    results = tuple(ToolMessage(content="z" * n, tool_call_id=c.id) for c, n in zip(calls, sizes, strict=True))
    return HistoryItem(first_seq=seq, last_seq=seq + len(sizes), messages=(AssistantMessage(content="", tool_calls=calls), *results))


def test_the_kept_tail_is_bounded_by_tokens_not_only_turns() -> None:
    """Three recent turns of one large result each are more than a quarter of the window, even capped: two stay."""
    history = [
        HistoryItem(first_seq=1, last_seq=1, messages=(UserMessage(content="what's happening in slack?"),), own_input=True),
        *(_turn(10 * (i + 1), (200_000,)) for i in range(5)),
    ]
    by_count = forced_compaction(history, policy=LadderPolicy(keep_recent_share=None), window_tokens=WINDOW)
    by_tokens = forced_compaction(history, policy=LadderPolicy(), window_tokens=WINDOW)
    assert by_count is not None and by_tokens is not None
    assert (by_count.span.to_seq, by_tokens.span.to_seq) == (21, 31), "large recent turns: fewer of them kept"


def test_a_compaction_that_would_repeat_itself_escalates_to_the_newest_turn() -> None:
    history = [
        HistoryItem(first_seq=1, last_seq=1, messages=(UserMessage(content="q"),), own_input=True),
        *(_turn(10 * (i + 1), (1_000,)) for i in range(4)),
    ]
    from harness_core.context.types import Compaction, SeqSpan

    done = Compaction(SeqSpan(1, 11), "summary")
    plain = forced_compaction(
        history, compactions=[done], policy=LadderPolicy(keep_recent_turns=3, keep_recent_share=None), window_tokens=WINDOW
    )
    escalated = forced_compaction(history, compactions=[done], policy=LadderPolicy(keep_recent_turns=3), window_tokens=WINDOW)
    assert plain is None, "everything before the three kept turns is already summarised: nothing to do"
    assert escalated is not None and escalated.span.to_seq == 31, "it keeps the newest turn alone instead"
    history2 = history[:3]
    assert forced_compaction(history2, compactions=[Compaction(SeqSpan(1, 21), "s")], policy=LadderPolicy(keep_recent_share=None)) is None
    assert forced_compaction(history2, compactions=[Compaction(SeqSpan(1, 21), "s")], policy=LadderPolicy(), window_tokens=WINDOW) is None


def test_the_summary_reads_at_most_its_share() -> None:
    messages = [ToolMessage(content="w " * 100_000, tool_call_id=f"c{i}") for i in range(4)]
    whole = bounded_transcript(messages, None)
    bounded = bounded_transcript(messages, 40_000)
    assert sum(len(line) for line in whole) > 600_000
    assert sum(len(line) + 1 for line in bounded) <= 40_000 and len(bounded) == 4, "every message keeps a line"


# --- the recorded run's shape through the kernel -------------------------------------------------------------------


@dataclass
class Host:
    window_tokens: int | None

    async def bands(self, call: ModelCall, turn: int, state: RunState) -> HostBands:
        return HostBands(
            system=SYSTEM, tools=(tool("slack"),), max_result_chars=RESULT_CAP_CHARS, window_tokens=self.window_tokens
        )


@dataclass
class Measured:
    """The provider: counts each request it is sent (the journal's measurement) as tokens of its text."""

    port: ScriptedModelPort
    sent: list[int] = field(default_factory=list[int])

    async def sample(self, call: ModelCall, on_delta: Any) -> SampledTurn:  # noqa: ANN401
        tokens = input_estimate(call.messages, call.system or "\n\n".join(call.system_blocks or []), call.tools or [])
        self.sent.append(tokens)
        sampled = await self.port.sample(call, on_delta)
        return replace(sampled, usage=TokenUsage(input_tokens=tokens, output_tokens=200))

    def restore(self, raw: bytes, *, provider: str, transport: str) -> Any:  # noqa: ANN401
        return self.port.restore(raw, provider=provider, transport=transport)


@dataclass
class SummaryModel:
    """The summarising call: what it was sent, in tokens."""

    sent: list[int] = field(default_factory=list[int])

    async def complete(self, messages: list[Any], *args: Any, **kwargs: Any) -> ModelResponse:  # noqa: ANN401
        prompt = str(messages[0].content)
        self.sent.append(len(prompt) // 4)
        return ModelResponse(message=AssistantMessage(content=[TextPart(text="Goal: Slack activity. " * 80)]), usage=TokenUsage())


@dataclass
class Shape:
    turns: list[int]
    summaries: list[int]
    after_compaction: list[int]

    @property
    def total(self) -> int:
        return sum(self.turns) + sum(self.summaries)


async def _recorded(*, bounded: bool, repeat: int = 1) -> Shape:
    world = World()

    async def slack(args: Any, ctx: Any) -> ToolExecution:  # noqa: ANN401
        return ToolExecution(status="ok", content={"messages": "m" * int(args["size"])})

    world.tools.add("slack", slack, tags=[("category", "read")])
    world.admit(text="can you find what's happening in slack?", max_turns=15 * repeat)
    replies = [
        Reply(tool_calls=[ToolCallSpec(id=f"t{t}-{i}", name="slack", args={"size": n}) for i, n in enumerate(sizes)])
        for t, sizes in enumerate(RECORDED_TURNS * repeat)
    ]
    _, port = scripted(*replies, Reply(text="Here is what is happening in Slack."))
    measured, summaries = Measured(port), SummaryModel()
    ctx = ContextCompiler(
        window_tokens=WINDOW,
        strategy=ModelCompaction(summaries),  # type: ignore[arg-type]
        host=Host(window_tokens=None if bounded else 0),
        # The recorded run's path (the shaped loop with auto-compact) has the compact rung only.
        policy=LadderPolicy(clear_above=0.8) if bounded else LadderPolicy(clear_above=0.8, keep_recent_share=None),
        summary_input_share=0.25 if bounded else None,
    )
    done = await world.run(world.kernel(measured, context=ctx))  # type: ignore[arg-type]
    assert done.status == "completed"
    compactions = [e.seq for e in world.events("context.compacted")]
    responses = world.events("model.response")
    after = [
        int(next(r for r in responses if r.seq > seq).data["usage"]["inputTokens"])  # type: ignore[index]
        for seq in compactions
    ]
    return Shape(measured.sent, summaries.sent, after)


async def test_recorded_shape_requests_stay_well_under_the_window() -> None:
    before = await _recorded(bounded=False)
    after = await _recorded(bounded=True)
    assert before.after_compaction and min(before.after_compaction) > 0.8 * WINDOW, (
        f"reproduced: after each compaction the request was still {before.after_compaction} of {WINDOW}"
    )
    # The recorded nine turns now fit without a compaction at all, so the prompt cache holds for every call.
    assert after.after_compaction == [] and max(after.turns) < 0.6 * WINDOW, after.turns


async def test_recorded_shape_twice_over_each_compaction_brings_the_request_under_half_the_window() -> None:
    """A task twice the recorded run's length: compactions are needed, and each one actually reduces."""
    after = await _recorded(bounded=True, repeat=2)
    assert after.after_compaction, "the longer task compacts"
    assert max(after.after_compaction) <= 0.5 * WINDOW, after.after_compaction
    assert max(after.turns) <= 0.85 * WINDOW, after.turns


async def test_recorded_shape_total_tokens_drop_by_a_large_factor() -> None:
    before = await _recorded(bounded=False)
    after = await _recorded(bounded=True)
    assert before.total > 900_000, f"reproduced the recorded ~1M tokens: {before.total}"
    assert after.total * 2 <= before.total, f"{after.total} vs {before.total}"
    assert before.summaries and max(before.summaries) > WINDOW, "a summary that re-read every result whole"
