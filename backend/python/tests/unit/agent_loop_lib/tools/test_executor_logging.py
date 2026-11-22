"""`tool_call.end`: one line per tool call from `ToolExecutor.call_tool`, saying how it ended,
with the call's ids bound on every line logged while it runs (`app/agent_loop_lib/tools/executor.py`)."""

from __future__ import annotations

import asyncio
import logging
from typing import Any
from unittest.mock import MagicMock, patch

import httpx
import pytest

from app.agent_loop_lib.core.types import ToolCall
from app.agent_loop_lib.core.types import ToolResult as CoreToolResult
from app.agent_loop_lib.hooks.events import HookEvent
from app.agent_loop_lib.hooks.middleware.decisions import PendingApproval
from app.agent_loop_lib.hooks.registry import HookRegistry
from app.agent_loop_lib.tools.base import ParameterType, Tool, ToolOutput, ToolParameter
from app.agent_loop_lib.tools.builtin.coordination.agent_tool import AgentTool
from app.agent_loop_lib.tools.builtin.coordination.best_of_n import BestOfNTool
from app.agent_loop_lib.tools.builtin.coordination.handoff import HandoffTool
from app.agent_loop_lib.tools.builtin.coordination.spawn_agent import SpawnAgentTool
from app.agent_loop_lib.tools.executor import ToolExecutor, log_tool_call_end
from app.agent_loop_lib.tools.registry import ToolRegistry
from app.schema.node_validator import NodeSchemaValidator
from app.utils import error_class
from app.utils.logger import log_record_fields
from app.utils.logging_fields import current_log_fields

pytestmark = pytest.mark.asyncio

_SENTINEL = "sentinel-7f3a"
_PROBE = logging.getLogger("tests.executor_logging.probe")


class _Tool(Tool):
    """`execute` runs `behaviour`; it may log, return or raise."""

    def __init__(self, behaviour: Any, *, name: str = "probe__run", ids: dict[str, str] | None = None) -> None:  # noqa: ANN401
        self._behaviour = behaviour
        self._name = name
        self._ids = ids or {}

    name = property(lambda self: self._name)
    short_description = property(lambda self: "probe")
    description = property(lambda self: "probe")
    path = property(lambda self: f"/toolsets/probe/{self._name}")
    log_ids = property(lambda self: self._ids)

    @property
    def parameters(self) -> list[ToolParameter]:
        return [ToolParameter(name="q", type=ParameterType.STRING, description="q", required=False)]

    async def execute(self, **kwargs: Any) -> ToolOutput:  # noqa: ANN401
        return await self._behaviour(**kwargs)


def _returns(output: ToolOutput) -> Any:  # noqa: ANN401
    async def _run(**_kwargs: Any) -> ToolOutput:  # noqa: ANN401
        return output
    return _run


def _raises(exc: BaseException) -> Any:  # noqa: ANN401
    async def _run(**_kwargs: Any) -> ToolOutput:  # noqa: ANN401
        raise exc
    return _run


def _executor(*tools: Tool, kernel: HookRegistry | None = None) -> ToolExecutor:
    registry = ToolRegistry()
    for tool in tools:
        registry.register_tool(tool)
    return ToolExecutor(registry, kernel)


def _call(name: str = "probe__run", call_id: str = "call-1", **arguments: Any) -> ToolCall:  # noqa: ANN401
    return ToolCall(id=call_id, name=name, arguments=arguments)


def _ends(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.getMessage() == "tool_call.end"]


def _one_end(caplog: pytest.LogCaptureFixture) -> tuple[logging.LogRecord, dict[str, object]]:
    (record,) = _ends(caplog)
    return record, log_record_fields(record)


def _clock(*readings: float) -> Any:  # noqa: ANN401
    clock = MagicMock()
    clock.perf_counter.side_effect = list(readings)
    return patch("app.agent_loop_lib.tools.executor.time", clock)


class TestOutcomes:
    async def test_a_success_is_one_info_line_with_its_ids_and_duration(self, caplog: pytest.LogCaptureFixture) -> None:
        caplog.set_level(logging.DEBUG)
        with _clock(10.0, 11.5):
            await _executor(_Tool(_returns(ToolOutput(success=True, data="ok")))).call_tool(_call())

        record, fields = _one_end(caplog)
        assert record.levelno == logging.INFO
        assert fields == {
            "tool": "probe__run", "tool_call_id": "call-1", "outcome": "success", "kind": "builtin", "duration_ms": 1500,
        }

    async def test_our_bug_is_one_error_line_with_its_stack_and_the_model_text_unchanged(
        self, caplog: pytest.LogCaptureFixture,
    ) -> None:
        caplog.set_level(logging.DEBUG)
        result = await _executor(_Tool(_raises(KeyError(_SENTINEL)))).call_tool(_call())

        record, fields = _one_end(caplog)
        assert record.levelno == logging.ERROR and record.exc_text is not None
        assert fields["error_class"] == "internal" and fields["error_type"] == "KeyError"
        assert fields["outcome"] == "failure" and fields["reason"] == "exception"
        assert "error_message" not in fields
        assert _SENTINEL not in (record.exc_text or "")
        assert result.is_error and result.content == repr(_SENTINEL)

    async def test_a_schema_rejected_write_reaches_the_model_without_the_rejected_value(
        self, caplog: pytest.LogCaptureFixture,
    ) -> None:
        # The error text is the tool result the LLM provider (and Opik) receive.
        seeded = "+1 555 0100 Seeded"

        async def write(**_kwargs: Any) -> ToolOutput:  # noqa: ANN401
            NodeSchemaValidator().validate_node("users", {"email": "u1@example.invalid", "businessPhones": seeded})
            return ToolOutput(success=True, data="written")

        caplog.set_level(logging.DEBUG)
        result = await _executor(_Tool(write)).call_tool(_call())

        assert result.is_error
        assert seeded not in result.content
        assert "at 'businessPhones': value is not of type 'array'" in result.content
        assert all(seeded not in r.getMessage() + str(log_record_fields(r)) for r in caplog.records)

    async def test_an_upstream_timeout_is_a_warning_without_a_stack(self, caplog: pytest.LogCaptureFixture) -> None:
        caplog.set_level(logging.DEBUG)
        await _executor(_Tool(_raises(httpx.ReadTimeout("slow")))).call_tool(_call())

        record, fields = _one_end(caplog)
        assert record.levelno == logging.WARNING and record.exc_text is None
        assert fields["error_class"] == "timeout"

    async def test_bad_arguments_are_a_warning_and_the_usage_hint_still_reaches_the_model(
        self, caplog: pytest.LogCaptureFixture,
    ) -> None:
        caplog.set_level(logging.DEBUG)
        result = await _executor(_Tool(_returns(ToolOutput(success=True)))).call_tool(_call(nope=1))

        record, fields = _one_end(caplog)
        assert record.levelno == logging.WARNING
        assert (fields["reason"], fields["error_class"]) == ("bad_args", "bad_request")
        assert "Correct usage of `probe__run`" in str(result.content)

    async def test_an_unknown_tool_has_no_kind_and_a_capped_name(self, caplog: pytest.LogCaptureFixture) -> None:
        caplog.set_level(logging.DEBUG)
        name = "x" * 299 + "y"
        result = await _executor(_Tool(_returns(ToolOutput(success=True)))).call_tool(_call(name))

        record, fields = _one_end(caplog)
        assert record.levelno == logging.WARNING
        assert (fields["reason"], fields["error_class"]) == ("unknown_tool", "not_found")
        assert "kind" not in fields
        assert fields["tool"] == "x" * 128
        assert str(result.content).startswith("Unknown tool:")

    async def test_a_failed_output_without_details_is_an_unclassified_warning(self, caplog: pytest.LogCaptureFixture) -> None:
        caplog.set_level(logging.DEBUG)
        await _executor(_Tool(_returns(ToolOutput(success=False, error="no")))).call_tool(_call())

        record, fields = _one_end(caplog)
        assert record.levelno == logging.WARNING
        assert fields["reason"] == "tool_error" and "error_class" not in fields

    async def test_only_the_known_end_fields_are_logged(self, caplog: pytest.LogCaptureFixture) -> None:
        caplog.set_level(logging.DEBUG)
        output = ToolOutput(success=False, error="no", end_fields={
            "reason": "server_error", "status": 502, "error_class": "upstream_5xx", "mcp_reason": "unreachable",
            "query": _SENTINEL,
        })
        await _executor(_Tool(_returns(output))).call_tool(_call())

        _, fields = _one_end(caplog)
        assert {k: fields[k] for k in ("reason", "status", "error_class", "mcp_reason")} == {
            "reason": "server_error", "status": 502, "error_class": "upstream_5xx", "mcp_reason": "unreachable",
        }
        assert "query" not in fields


class _Decide:
    """A PRE_TOOL_USE / POST_TOOL_USE middleware that makes one decision."""

    @staticmethod
    def kernel(event: HookEvent, decide: Any) -> HookRegistry:  # noqa: ANN401
        kernel = HookRegistry()

        async def _mw(ctx: Any, next_fn: Any) -> None:  # noqa: ANN401
            decide(ctx)
            await next_fn()

        kernel.on(event).use(_mw)
        return kernel


class TestHookExits:
    async def test_a_denied_call_is_skipped_at_info_without_a_duration(self, caplog: pytest.LogCaptureFixture) -> None:
        caplog.set_level(logging.DEBUG)
        kernel = _Decide.kernel(HookEvent.PRE_TOOL_USE, lambda ctx: ctx.deny("no"))
        await _executor(_Tool(_returns(ToolOutput(success=True))), kernel=kernel).call_tool(_call())

        record, fields = _one_end(caplog)
        assert record.levelno == logging.INFO
        assert (fields["outcome"], fields["reason"]) == ("skipped", "denied")
        assert "duration_ms" not in fields

    async def test_an_unapproved_ask_is_skipped_like_a_denial(self, caplog: pytest.LogCaptureFixture) -> None:
        caplog.set_level(logging.DEBUG)
        kernel = _Decide.kernel(HookEvent.PRE_TOOL_USE, lambda ctx: ctx.ask("ok?"))

        async def _no(_call: ToolCall, _reason: str) -> bool:
            return False

        await _executor(_Tool(_returns(ToolOutput(success=True))), kernel=kernel).call_tool(_call(), on_ask=_no)

        record, fields = _one_end(caplog)
        assert record.levelno == logging.INFO and fields["reason"] == "denied"

    async def test_a_call_parked_for_approval_is_only_a_debug_line(self, caplog: pytest.LogCaptureFixture) -> None:
        caplog.set_level(logging.DEBUG)
        kernel = _Decide.kernel(HookEvent.PRE_TOOL_USE, lambda ctx: ctx.ask_later(PendingApproval(message="wait")))

        async def _parked(_call: ToolCall, _pending: PendingApproval) -> None:
            return None

        await _executor(_Tool(_returns(ToolOutput(success=True))), kernel=kernel).call_tool(_call(), on_pending=_parked)

        record, fields = _one_end(caplog)
        assert record.levelno == logging.DEBUG
        assert (fields["outcome"], fields["reason"]) == ("skipped", "awaiting_approval")

    async def test_a_blocked_result_is_a_warning(self, caplog: pytest.LogCaptureFixture) -> None:
        caplog.set_level(logging.DEBUG)
        kernel = _Decide.kernel(HookEvent.POST_TOOL_USE, lambda ctx: ctx.block("too big"))
        await _executor(_Tool(_returns(ToolOutput(success=True, data="x"))), kernel=kernel).call_tool(_call())

        record, fields = _one_end(caplog)
        assert record.levelno == logging.WARNING
        assert (fields["outcome"], fields["reason"]) == ("failure", "blocked")


class TestRaisedFromTheCall:
    async def test_an_override_that_raises_logs_once_and_propagates(self, caplog: pytest.LogCaptureFixture) -> None:
        caplog.set_level(logging.DEBUG)

        async def _override() -> CoreToolResult:
            raise RuntimeError(_SENTINEL)

        with pytest.raises(RuntimeError) as raised:
            await _executor(_Tool(_returns(ToolOutput(success=True)))).call_tool(_call(), override_execute=_override)
        error_class.log_failure(_PROBE, "agent_turn.failed", raised.value)

        record, fields = _one_end(caplog)
        assert record.levelno == logging.ERROR and record.exc_text is not None
        assert fields["reason"] == "exception" and "error_message" not in fields
        (outer,) = [r for r in caplog.records if r.getMessage() == "agent_turn.failed"]
        assert outer.levelno == logging.DEBUG

    async def test_a_warning_class_raise_logs_once_and_the_outer_line_drops_to_debug(
        self, caplog: pytest.LogCaptureFixture,
    ) -> None:
        caplog.set_level(logging.DEBUG)

        async def _override() -> CoreToolResult:
            raise httpx.ConnectError("refused")

        with pytest.raises(httpx.ConnectError) as raised:
            await _executor(_Tool(_returns(ToolOutput(success=True)))).call_tool(_call(), override_execute=_override)
        error_class.log_failure(_PROBE, "agent_turn.failed", raised.value)

        record, fields = _one_end(caplog)
        assert record.levelno == logging.WARNING and fields["error_class"] == "unavailable"
        (outer,) = [r for r in caplog.records if r.getMessage() == "agent_turn.failed"]
        assert outer.levelno == logging.DEBUG

    async def test_a_cancelled_call_is_info_with_its_run_time(self, caplog: pytest.LogCaptureFixture) -> None:
        caplog.set_level(logging.DEBUG)
        with _clock(5.0, 5.25), pytest.raises(asyncio.CancelledError):
            await _executor(_Tool(_raises(asyncio.CancelledError()))).call_tool(_call())

        record, fields = _one_end(caplog)
        assert record.levelno == logging.INFO
        assert (fields["outcome"], fields["error_class"], fields["duration_ms"]) == ("failure", "cancelled", 250)

    async def test_an_error_logged_before_keeps_the_end_line_at_its_level_without_a_second_stack(
        self, caplog: pytest.LogCaptureFixture,
    ) -> None:
        caplog.set_level(logging.DEBUG)

        async def _logs_then_returns(**_kwargs: Any) -> ToolOutput:  # noqa: ANN401
            exc = KeyError("k")
            error_class.log_failure(_PROBE, "probe.failed", exc)
            return ToolOutput(success=False, error="k", cause=exc)

        await _executor(_Tool(_logs_then_returns)).call_tool(_call())

        record, fields = _one_end(caplog)
        assert record.levelno == logging.ERROR and record.exc_text is None
        assert (fields["error_class"], fields["error_type"], fields["outcome"]) == ("internal", "KeyError", "failure")
        assert len([r for r in caplog.records if r.exc_text and r.levelno > logging.DEBUG]) == 1

    async def test_an_exception_logged_at_warn_and_returned_as_an_error_class_is_error_with_its_stack(
        self, caplog: pytest.LogCaptureFixture,
    ) -> None:
        caplog.set_level(logging.DEBUG)

        async def _logs_warn_then_returns(**_kwargs: Any) -> ToolOutput:  # noqa: ANN401
            exc = RuntimeError("r")
            error_class.log_failure(_PROBE, "probe.failed", exc, error_class="timeout")
            return ToolOutput(success=False, error="r", cause=exc, end_fields={"error_class": "internal"})

        await _executor(_Tool(_logs_warn_then_returns)).call_tool(_call())

        (probe,) = [r for r in caplog.records if r.getMessage() == "probe.failed"]
        record, fields = _one_end(caplog)
        assert probe.levelno == logging.WARNING and probe.exc_text is None
        assert record.levelno == logging.ERROR and record.exc_text is not None
        assert fields["error_class"] == "internal"

    @pytest.mark.parametrize(("shared", "level"), [
        (httpx.ReadTimeout("slow"), logging.WARNING),
        (KeyError("k"), logging.ERROR),
    ])
    async def test_parallel_calls_failing_with_one_exception_each_get_their_line(
        self, shared: BaseException, level: int, caplog: pytest.LogCaptureFixture,
    ) -> None:
        """As when parallel first MCP calls share one listing, or tools share one memoised lookup."""
        caplog.set_level(logging.DEBUG)
        both_inside = asyncio.Barrier(2)

        async def _shared_failure(**_kwargs: Any) -> ToolOutput:  # noqa: ANN401
            await both_inside.wait()
            raise shared

        executor = _executor(_Tool(_shared_failure))
        await asyncio.gather(executor.call_tool(_call(call_id="a")), executor.call_tool(_call(call_id="b")))

        ends = _ends(caplog)
        assert sorted((log_record_fields(r)["tool_call_id"], r.levelno) for r in ends) == [("a", level), ("b", level)]
        assert {log_record_fields(r)["error_type"] for r in ends} == {type(shared).__name__}
        assert len([r for r in ends if r.exc_text]) == (1 if level >= logging.ERROR else 0)

    async def test_two_calls_raising_one_exception_each_get_their_line(self, caplog: pytest.LogCaptureFixture) -> None:
        caplog.set_level(logging.DEBUG)
        shared = RuntimeError("boom")

        async def _override() -> CoreToolResult:
            raise shared

        executor = _executor(_Tool(_returns(ToolOutput(success=True))))
        for call_id in ("a", "b"):
            with pytest.raises(RuntimeError):
                await executor.call_tool(_call(call_id=call_id), override_execute=_override)

        assert [r.levelno for r in _ends(caplog)] == [logging.ERROR, logging.ERROR]
        assert len([r for r in caplog.records if r.exc_text and r.levelno > logging.DEBUG]) == 1


class TestBinding:
    async def test_lines_inside_the_call_carry_its_ids_and_none_leak_after(self, caplog: pytest.LogCaptureFixture) -> None:
        caplog.set_level(logging.DEBUG)

        async def _logs(**_kwargs: Any) -> ToolOutput:  # noqa: ANN401
            _PROBE.info("inside")
            return ToolOutput(success=True)

        tool = _Tool(_logs, ids={"mcp_server_id": "inst-1"})
        await _executor(tool).call_tool(_call())

        (inside,) = [r for r in caplog.records if r.getMessage() == "inside"]
        assert log_record_fields(inside) == {"tool": "probe__run", "tool_call_id": "call-1", "mcp_server_id": "inst-1"}
        assert dict(current_log_fields()) == {}

    async def test_parallel_calls_keep_their_own_ids(self, caplog: pytest.LogCaptureFixture) -> None:
        caplog.set_level(logging.DEBUG)
        both_inside = asyncio.Barrier(2)

        async def _logs(**_kwargs: Any) -> ToolOutput:  # noqa: ANN401
            await both_inside.wait()
            _PROBE.info("inside")
            return ToolOutput(success=True)

        executor = _executor(_Tool(_logs))
        await asyncio.gather(executor.call_tool(_call(call_id="a")), executor.call_tool(_call(call_id="b")))

        ids = sorted(log_record_fields(r)["tool_call_id"] for r in caplog.records if r.getMessage() == "inside")
        assert ids == ["a", "b"]
        assert sorted(log_record_fields(r)["tool_call_id"] for r in _ends(caplog)) == ["a", "b"]

    async def test_the_tool_kinds(self) -> None:
        assert _Tool(_returns(ToolOutput(success=True))).log_kind == "builtin"
        for tool_class in (SpawnAgentTool, AgentTool, HandoffTool, BestOfNTool):
            assert tool_class.log_kind.fget(MagicMock()) == "sub_agent"


class TestNoContent:
    async def test_arguments_results_and_error_texts_are_never_logged(self, caplog: pytest.LogCaptureFixture) -> None:
        caplog.set_level(logging.DEBUG)
        request = httpx.Request("GET", "https://api.example.com/x")
        upstream = httpx.HTTPStatusError(_SENTINEL, request=request, response=httpx.Response(502, request=request))
        tools = [
            _Tool(_returns(ToolOutput(success=True, data=_SENTINEL)), name="a__ok"),
            _Tool(_returns(ToolOutput(success=False, error=_SENTINEL)), name="a__fails"),
            _Tool(_raises(upstream), name="a__upstream"),
            _Tool(_raises(TypeError(_SENTINEL)), name="a__bug"),
        ]
        executor = _executor(*tools)
        for tool in tools:
            await executor.call_tool(_call(tool.name, q=_SENTINEL))

        assert len(_ends(caplog)) == 4
        for record in caplog.records:
            assert _SENTINEL not in record.getMessage()
            assert _SENTINEL not in str(log_record_fields(record))
            assert _SENTINEL not in (record.exc_text or "")
        assert all("error_message" not in log_record_fields(r) for r in _ends(caplog))
        assert [log_record_fields(r).get("error_class") for r in _ends(caplog)] == [None, None, "upstream_5xx", "internal"]


class TestTheEmitter:
    async def test_explicit_ids_and_levels(self, caplog: pytest.LogCaptureFixture) -> None:
        caplog.set_level(logging.DEBUG)
        log_tool_call_end(outcome="skipped", reason="duplicate", tool="t" * 200, tool_call_id="c9")
        log_tool_call_end(outcome="failure", reason="malformed_args", error_class="bad_request", tool="t", tool_call_id="c8")

        skipped, failed = _ends(caplog)
        assert skipped.levelno == logging.INFO and failed.levelno == logging.WARNING
        assert log_record_fields(skipped) == {
            "outcome": "skipped", "reason": "duplicate", "tool": "t" * 128, "tool_call_id": "c9",
        }
        assert log_record_fields(failed)["error_class"] in error_class.ERROR_CLASSES


class TestAnEndFieldsLevel:
    """`ToolOutput.end_fields["level"]` (the MCP adapter's hourly gate) lowers only a `misconfigured` line."""

    async def test_a_misconfigured_repeat_is_debug_and_is_never_a_second_keyword(self, caplog: pytest.LogCaptureFixture) -> None:
        caplog.set_level(logging.DEBUG)
        exc = RuntimeError("no OAuth client")
        output = ToolOutput(success=False, error="x", cause=exc, end_fields={"error_class": "misconfigured", "level": logging.DEBUG})

        result = await _executor(_Tool(_returns(output))).call_tool(_call())

        record, fields = _one_end(caplog)
        assert (record.levelno, fields["error_class"]) == (logging.DEBUG, "misconfigured")
        assert "level" not in fields
        assert result.is_error

    async def test_without_a_level_a_misconfigured_line_is_an_error(self, caplog: pytest.LogCaptureFixture) -> None:
        caplog.set_level(logging.DEBUG)
        output = ToolOutput(success=False, error="x", cause=RuntimeError("x"), end_fields={"error_class": "misconfigured", "level": None})

        await _executor(_Tool(_returns(output))).call_tool(_call())

        record, _ = _one_end(caplog)
        assert record.levelno == logging.ERROR and record.exc_text

    @pytest.mark.parametrize(("cls", "level", "expected"), [
        ("internal", logging.DEBUG, logging.ERROR),  # our bug is never lowered
        ("unavailable", logging.DEBUG, logging.WARNING),
        ("misconfigured", logging.CRITICAL, logging.ERROR),  # only ever lower
    ])
    async def test_any_other_use_of_level_is_ignored(
        self, caplog: pytest.LogCaptureFixture, cls: str, level: int, expected: int,
    ) -> None:
        caplog.set_level(logging.DEBUG)

        log_tool_call_end(outcome="failure", reason="exception", error_class=cls, cause=KeyError("k"), level=level, tool="t")

        record, _ = _one_end(caplog)
        assert record.levelno == expected
        assert bool(record.exc_text) is (expected >= logging.ERROR)

    async def test_an_already_logged_misconfigured_repeat_is_debug_too(self, caplog: pytest.LogCaptureFixture) -> None:
        caplog.set_level(logging.DEBUG)
        exc = RuntimeError("x")
        error_class.log_failure(_PROBE, "credential_refresh.end", exc, error_class="misconfigured")

        log_tool_call_end(outcome="failure", reason="exception", error_class="misconfigured", cause=exc, level=logging.DEBUG, tool="t")

        record, _ = _one_end(caplog)
        assert (record.levelno, record.exc_text) == (logging.DEBUG, None)


class TestDenyReasonsAndSpecialRouteCauses:
    """P3b: why a call was denied (`deny_reason`), and a special route's handled failure keeps its class."""

    async def test_a_deny_code_reaches_the_end_line(self, caplog: pytest.LogCaptureFixture) -> None:
        caplog.set_level(logging.DEBUG)
        kernel = _Decide.kernel(HookEvent.PRE_TOOL_USE, lambda ctx: ctx.deny("no", code="unattended"))
        result = await _executor(_Tool(_returns(ToolOutput(success=True))), kernel=kernel).call_tool(_call())

        record, fields = _one_end(caplog)
        assert (record.levelno, fields["reason"], fields["deny_reason"]) == (logging.INFO, "denied", "unattended")
        assert result.content == "no"

    async def test_the_first_deny_keeps_its_code(self, caplog: pytest.LogCaptureFixture) -> None:
        caplog.set_level(logging.DEBUG)

        def _twice(ctx: Any) -> None:  # noqa: ANN401
            ctx.deny("first", code="rule")
            ctx.deny("second", code="other")

        await _executor(_Tool(_returns(ToolOutput(success=True))), kernel=_Decide.kernel(HookEvent.PRE_TOOL_USE, _twice)).call_tool(_call())
        assert _one_end(caplog)[1]["deny_reason"] == "rule"

    async def test_a_deny_without_a_code_has_no_deny_reason(self, caplog: pytest.LogCaptureFixture) -> None:
        caplog.set_level(logging.DEBUG)
        kernel = _Decide.kernel(HookEvent.PRE_TOOL_USE, lambda ctx: ctx.deny("no"))
        await _executor(_Tool(_returns(ToolOutput(success=True))), kernel=kernel).call_tool(_call())
        assert "deny_reason" not in _one_end(caplog)[1]

    async def test_a_special_route_failure_is_classified_from_its_cause(self, caplog: pytest.LogCaptureFixture) -> None:
        caplog.set_level(logging.DEBUG)

        async def _override() -> CoreToolResult:
            return CoreToolResult(
                tool_call_id="call-1", name="probe__run", content=_SENTINEL, is_error=True,
                cause=httpx.ReadTimeout(_SENTINEL), end_fields={"reason": "exception"},
            )

        result = await _executor(_Tool(_returns(ToolOutput(success=True)))).call_tool(_call(), override_execute=_override)

        record, fields = _one_end(caplog)
        assert (fields["outcome"], fields["reason"], fields["error_class"]) == ("failure", "exception", "timeout")
        assert record.levelno == logging.WARNING and result.content == _SENTINEL
        assert _SENTINEL not in caplog.text

    async def test_a_special_route_class_wins_over_the_cause(self, caplog: pytest.LogCaptureFixture) -> None:
        caplog.set_level(logging.DEBUG)

        async def _override() -> CoreToolResult:
            return CoreToolResult(
                tool_call_id="call-1", name="probe__run", content="refused", is_error=True,
                cause=RuntimeError("refused"), end_fields={"reason": "max_spawn_depth", "error_class": "bad_request"},
            )

        await _executor(_Tool(_returns(ToolOutput(success=True)))).call_tool(_call(), override_execute=_override)

        record, fields = _one_end(caplog)
        assert (record.levelno, record.exc_text) == (logging.WARNING, None)
        assert (fields["reason"], fields["error_class"]) == ("max_spawn_depth", "bad_request")


class TestToolResultLogFields:
    async def test_they_are_never_dumped(self) -> None:
        result = CoreToolResult(tool_call_id="c", name="n", content="x", cause=ValueError(_SENTINEL), end_fields={"reason": "r"})
        assert "cause" not in result.model_dump() and "end_fields" not in result.model_dump()
        assert _SENTINEL not in result.model_dump_json() and _SENTINEL not in repr(result)

    async def test_they_keep_the_cause_and_stay_out_of_every_schema(self) -> None:
        from app.agent_loop_lib.core.types import AgentResult, AgentTurn

        cause = ValueError("x")
        result = CoreToolResult(tool_call_id="c", name="n", content="x", cause=cause, end_fields={"reason": "r"})
        assert (result.cause, result.end_fields) == (cause, {"reason": "r"})
        for model in (CoreToolResult, AgentTurn, AgentResult):
            assert "cause" not in str(model.model_json_schema())
