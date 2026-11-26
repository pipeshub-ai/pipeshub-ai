from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from pydantic import ValidationError

from harness_core.testing import (
    Completed,
    ErrorSpec,
    ModelCallError,
    ModelEvent,
    ModelRequest,
    ModelScript,
    Reply,
    RequestMessage,
    ScriptedModel,
    ScriptExhausted,
    ScriptMismatch,
    TextDelta,
    ToolCallEvent,
    ToolCallSpec,
    Turn,
    Usage,
    load_script,
    parse_script,
)
from harness_core.testing.clock import ManualClock

FIXTURE = Path(__file__).parent / "fixtures" / "approval_jira_create.yaml"

USER = ModelRequest((RequestMessage("user", "please create a P1 bug for login"),))
TOOL_OK = ModelRequest(
    (
        RequestMessage("user", "please create a P1 bug for login"),
        RequestMessage("assistant", ""),
        RequestMessage("tool", "PROJ-1", tool_name="jira_create_issue", tool_status="succeeded"),
    )
)

Outcome = tuple[list[ModelEvent], tuple[str, int | None, float | None] | None]


async def _drain(model: ScriptedModel, request: ModelRequest) -> Outcome:
    events: list[ModelEvent] = []
    try:
        async for event in model.stream(request):
            events.append(event)  # noqa: PERF401 - keep deltas seen before the error
    except ModelCallError as err:
        return events, (err.kind, err.status, err.retry_after_s)
    return events, None


async def _run_all(script: ModelScript, requests: list[ModelRequest]) -> tuple[list[Outcome], Usage]:
    model = ScriptedModel(script)
    outcomes = [await _drain(model, r) for r in requests]
    return outcomes, model.usage_total


class TestK1Determinism:
    async def test_yaml_script_replays_identically_across_runs(self) -> None:
        first = await _run_all(load_script(FIXTURE), [USER, TOOL_OK])
        second = await _run_all(load_script(FIXTURE), [USER, TOOL_OK])
        assert first == second
        outcomes, usage = first
        assert usage == Usage(
            input_tokens=280, output_tokens=20, cache_read_tokens=220, cache_write_tokens=20
        )
        tool_turn, text_turn = outcomes[0][0], outcomes[1][0]
        assert next(e for e in tool_turn if isinstance(e, ToolCallEvent)).call.id == "toolu_01"
        assert "".join(e.text for e in text_turn if isinstance(e, TextDelta)) == "Created PROJ-1."
        assert len([e for e in text_turn if isinstance(e, TextDelta)]) == 4

    @settings(max_examples=60, deadline=None)
    @given(st.data())
    def test_any_script_is_deterministic(self, data: st.DataObject) -> None:
        script = data.draw(_scripts())
        requests = [USER] * len(script.turns)
        first = asyncio.run(_run_all(script, requests))
        second = asyncio.run(_run_all(script, requests))
        assert first == second
        for (events, error), turn in zip(first[0], script.turns, strict=True):
            assert turn.reply is not None
            streamed = "".join(e.text for e in events if isinstance(e, TextDelta))
            assert streamed == turn.reply.content
            if error is None:
                assert isinstance(events[-1], Completed)
                assert events[-1].response.text == streamed


_json_scalars = st.none() | st.booleans() | st.integers(-5, 5) | st.text(max_size=5)
_errors = st.builds(
    ErrorSpec,
    kind=st.sampled_from(["server_error", "rate_limited", "content_filter", "timeout", "malformed"]),
    retry_after_s=st.none() | st.floats(0, 5),
)
_replies = st.builds(
    Reply,
    text=st.text(max_size=20),
    chunks=st.none() | st.integers(1, 6),
    reasoning=st.text(max_size=5),
    tool_calls=st.lists(
        st.builds(
            ToolCallSpec,
            id=st.none() | st.text(min_size=1, max_size=4),
            name=st.sampled_from(["search", "jira_create_issue"]),
            args=st.dictionaries(st.text(max_size=3), _json_scalars, max_size=3),
        ),
        max_size=2,
    ),
    error=st.none() | _errors,
    usage=st.builds(Usage, input_tokens=st.integers(0, 999), output_tokens=st.integers(0, 99)),
)


@st.composite
def _scripts(draw: st.DrawFn) -> ModelScript:
    replies = draw(st.lists(_replies, min_size=1, max_size=5))
    return ModelScript(id="generated", turns=[Turn(reply=r) for r in replies])


class TestErrors:
    @pytest.mark.parametrize(
        ("yaml_error", "expected"),
        [
            ("{kind: server_error, status: 503}", ("server_error", 503, None, True)),
            ("{kind: rate_limited, retry_after_s: 2.5}", ("rate_limited", 429, 2.5, True)),
            ("{kind: content_filter}", ("content_filter", 400, None, False)),
            ("{kind: timeout}", ("timeout", None, None, True)),
        ],
    )
    async def test_error_shapes(self, yaml_error: str, expected: tuple[object, ...]) -> None:
        model = ScriptedModel(parse_script(f"turns: [{{reply: {{error: {yaml_error}}}}}]"))
        with pytest.raises(ModelCallError) as info:
            await model.complete(USER)
        err = info.value
        assert (err.kind, err.status, err.retry_after_s, err.retryable) == expected

    async def test_text_then_error_is_a_mid_stream_cut(self) -> None:
        model = ScriptedModel(
            parse_script("turns: [{reply: {text: abcd, chunks: 2, error: {kind: server_error}}}]")
        )
        events, error = await _drain(model, USER)
        assert events == [TextDelta("ab"), TextDelta("cd")]
        assert error == ("server_error", 500, None)


class TestGuardsAndExhaustion:
    async def test_guard_mismatch_is_readable(self) -> None:
        model = ScriptedModel.from_yaml(FIXTURE)
        with pytest.raises(ScriptMismatch, match="turn 0: last user message"):
            await model.complete(ModelRequest((RequestMessage("user", "hello"),)))

    async def test_tool_status_guard(self) -> None:
        model = ScriptedModel.from_yaml(FIXTURE)
        await model.complete(USER)
        failed = ModelRequest(
            (RequestMessage("tool", "", tool_name="jira_create_issue", tool_status="failed"),)
        )
        with pytest.raises(ScriptMismatch, match="status 'failed'"):
            await model.complete(failed)

    async def test_extra_call_raises_exhausted(self) -> None:
        model = ScriptedModel(parse_script("turns: [{reply: {text: hi}}]"))
        await model.complete(USER)
        model.assert_exhausted()
        with pytest.raises(ScriptExhausted):
            await model.complete(USER)

    async def test_from_request_computes_reply(self) -> None:
        def echo(request: ModelRequest) -> Reply:
            last = request.last("user")
            return Reply(text=f"echo: {last.text if last else ''}")

        model = ScriptedModel(ModelScript(turns=[Turn(from_request=echo)]))
        assert (await model.complete(USER)).text == "echo: please create a P1 bug for login"


class TestDsl:
    def test_unknown_keys_are_rejected(self) -> None:
        with pytest.raises(ValidationError):
            parse_script("turns: [{reply: {txt: typo}}]")

    def test_explicit_chunks_must_match_text(self) -> None:
        with pytest.raises(ValidationError):
            parse_script("turns: [{reply: {text: abc, chunks: [a, b]}}]")

    def test_default_tool_call_ids_and_stop_reason(self) -> None:
        async def run() -> Completed:
            model = ScriptedModel(parse_script("turns: [{reply: {tool_calls: [{name: a}, {name: b}]}}]"))
            events = [e async for e in model.stream(USER)]
            last = events[-1]
            assert isinstance(last, Completed)
            return last

        response = asyncio.run(run()).response
        assert [c.id for c in response.tool_calls] == ["call_0_0", "call_0_1"]
        assert response.stop_reason == "tool_use"

    async def test_delay_uses_injected_sleep(self) -> None:
        clock = ManualClock()
        model = ScriptedModel(parse_script("turns: [{reply: {text: hi, delay_ms: 1500}}]"), sleep=clock.sleep)
        task = asyncio.create_task(model.complete(USER))
        await asyncio.sleep(0)
        assert not task.done()
        clock.advance(1.0)
        await asyncio.sleep(0)
        assert not task.done()
        clock.advance(0.5)
        assert (await task).text == "hi"
