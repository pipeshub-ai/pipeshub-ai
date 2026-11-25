"""What the model is sent, captured whole at the transport, for comparing two loops request by request.

The replay lane's request digest leaves the system prompt out (it embeds the date) and names tools without their
descriptions, so it could not see the two drivers choosing different prompt tiers or time context (DUR1).
:class:`CapturingTransport` keeps every call as the provider would get it: the system prompt and blocks, each tool's
name, description and input schema, and the messages with their tool calls. :func:`normalized` blanks only what also
differs between two runs of one loop (timings, sandbox and artifact ids).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, override

from harness_core.models.output_bound import output_limit
from harness_core.models.transports.base import LLMTransport

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable

    from harness_core.kernel.core.messages import Message
    from harness_core.kernel.core.responses import ModelResponse, StructuredResponse
    from harness_core.kernel.core.streaming import StreamEvent
    from harness_core.kernel.core.tool_schema import ToolChoice, ToolSchema
    from harness_core.testing import ModelScript
    from tests.harness_replay.legacy_driver import DriveSpec

_VOLATILE = (
    (re.compile(r'\\?"duration_ms\\?": [0-9.]+'), '"duration_ms": 0'),
    (re.compile(r'\\?"sandbox_id\\?": \\?"[^"\\]*\\?"'), '"sandbox_id": ""'),
    (re.compile(r"\bart-\d+\b"), "art-N"),
    # An office file is a zip stamped with the time it was written: one byte either way between two runs.
    (re.compile(r'\\?"size_bytes\\?": \d+'), '"size_bytes": 0'),
    # A question card's ids are minted per run (ask_user_question).
    (re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b"), "<uuid>"),
)


@dataclass(frozen=True)
class WireRequest:
    kind: str
    system: str
    # The cache bands a transport may mark (Anthropic cache breakpoints); empty when the caller sent none.
    system_blocks: tuple[str, ...]
    tools: tuple[str, ...]
    messages: tuple[str, ...]
    # The ``max_output_tokens`` the call carried to the provider's transport (P4.17); ``None``: no limit.
    output_limit: int | None = None

    @property
    def tool_names(self) -> list[str]:
        return [json.loads(t)["name"] for t in self.tools]


def normalized(text: str) -> str:
    for pattern, replacement in _VOLATILE:
        text = pattern.sub(replacement, text)
    return text


def _dump(value: Any) -> str:  # noqa: ANN401 - a pydantic message or tool schema
    return normalized(json.dumps(value.model_dump(mode="json"), sort_keys=True))


def _choice(tool_choice: str | None) -> str:
    """Named only when set, so a request without one reads as it always did (main #4379)."""
    return f" tool_choice={tool_choice}" if tool_choice else ""


class CapturingTransport(LLMTransport):
    """``inner`` with every call it serves appended to ``seen``."""

    def __init__(self, inner: LLMTransport, seen: list[WireRequest]) -> None:
        super().__init__()
        self._inner = inner
        self._seen = seen

    @property
    @override
    def provider(self) -> str:
        return self._inner.provider

    @property
    @override
    def accepts_output_limit(self) -> bool:
        # Not the base's False: TransportModel would then send a captured run no limit at all (P4.17L LV-1).
        return self._inner.accepts_output_limit

    def _keep(
        self, kind: str, messages: list[Message], tools: list[ToolSchema] | None, system: str | None,
        system_blocks: list[str] | None = None, max_output_tokens: int | None = None,
    ) -> None:
        self._seen.append(
            WireRequest(
                kind=kind,
                system=normalized(system or ""),
                system_blocks=tuple(normalized(b) for b in system_blocks or ()),
                tools=tuple(_dump(t) for t in tools or ()),
                messages=tuple(_dump(m) for m in messages),
                output_limit=max_output_tokens,
            )
        )

    @override
    async def complete(
        self,
        messages: list[Message],
        tools: list[ToolSchema] | None = None,
        system: str | None = None,
        model: str | None = None,
        thinking_budget: int | None = None,
        effort: str | None = None,
        system_blocks: list[str] | None = None,
        max_output_tokens: int | None = None,
        *,
        tool_choice: ToolChoice | None = None,
    ) -> ModelResponse:
        self._keep(f"complete model={model or self._inner.model_name} thinking={thinking_budget} effort={effort}{_choice(tool_choice)}", messages, tools, system, system_blocks, max_output_tokens)
        return await self._inner.complete(
            messages, tools, system, model, thinking_budget, effort, system_blocks, **output_limit(max_output_tokens),
            **({"tool_choice": tool_choice} if tool_choice else {}),
        )

    @override
    async def complete_structured(
        self,
        messages: list[Message],
        output_schema: dict[str, Any],
        system: str | None = None,
        model: str | None = None,
        max_output_tokens: int | None = None,
    ) -> StructuredResponse:
        self._keep(f"structured model={model or self._inner.model_name} " + json.dumps(output_schema, sort_keys=True), messages, None, system, None, max_output_tokens)
        return await self._inner.complete_structured(
            messages, output_schema, system, model, **output_limit(max_output_tokens)
        )

    @override
    def stream(
        self,
        messages: list[Message],
        tools: list[ToolSchema] | None = None,
        system: str | None = None,
        model: str | None = None,
        thinking_budget: int | None = None,
        effort: str | None = None,
        system_blocks: list[str] | None = None,
        max_output_tokens: int | None = None,
        *,
        tool_choice: ToolChoice | None = None,
    ) -> AsyncIterator[StreamEvent]:
        self._keep(f"stream model={model or self._inner.model_name} thinking={thinking_budget} effort={effort}{_choice(tool_choice)}", messages, tools, system, system_blocks, max_output_tokens)
        return self._inner.stream(
            messages, tools, system, model, thinking_budget, effort, system_blocks, **output_limit(max_output_tokens),
            **({"tool_choice": tool_choice} if tool_choice else {}),
        )


@dataclass(frozen=True)
class Driven:
    requests: list[WireRequest]
    result: Any  # a DriveResult


async def drive_captured(drive: Callable[[DriveSpec], Awaitable[Any]], spec: DriveSpec, script: ModelScript) -> Driven:
    """``spec`` driven by ``drive`` on ``script``, every model call captured. The scripted model is reached as a
    live one is (``spec.transport``), so both drivers wrap it the same way."""
    from app.harness.testing_adapters import ScriptedModelTransport
    from harness_core.testing import ScriptedModel

    seen: list[WireRequest] = []
    spec.script = None
    spec.transport = CapturingTransport(
        ScriptedModelTransport(ScriptedModel(script), model_name=spec.model_name), seen
    )
    result = await drive(spec)
    return Driven(seen, result)


# UXR-B1 (``plan_tool`` in durable_parity.py, an allowed difference): the durable quick, react and auto loop offers its plan
# tool, which today's loop never had. Left out of both sides, so a request that differs in anything else still fails.
_PLAN_TOOL = "write_todos"
_PLAN_LINE = re.compile(r"^- \*\*write_todos\*\*: .*\n", re.MULTILINE)


def _without_plan_tool(request: WireRequest) -> WireRequest:
    from dataclasses import replace

    return replace(
        request,
        system=_PLAN_LINE.sub("", request.system),
        system_blocks=tuple(_PLAN_LINE.sub("", b) for b in request.system_blocks),
        tools=tuple(t for t in request.tools if json.loads(t)["name"] != _PLAN_TOOL),
    )


def first_difference(a: list[WireRequest], b: list[WireRequest], *, blocks: bool = True) -> str | None:
    """Where two runs' requests first differ, said plainly; ``None`` when they are equal. ``blocks=False`` leaves
    the system cache blocks out (DUR1-F5's difference, fixed in P4.8)."""
    a, b = [_without_plan_tool(r) for r in a], [_without_plan_tool(r) for r in b]
    if len(a) != len(b):
        return f"{len(a)} requests against {len(b)}"
    for i, (x, y) in enumerate(zip(a, b, strict=True)):
        if x.kind != y.kind:
            return f"request {i}: {x.kind[:60]} against {y.kind[:60]}"
        if x.system != y.system:
            return f"request {i}, system prompt: {_first_line_diff(x.system, y.system)}"
        if blocks and x.system_blocks != y.system_blocks:
            return f"request {i}: {len(x.system_blocks)} system cache blocks against {len(y.system_blocks)}"
        if x.tool_names != y.tool_names:
            return f"request {i}: tools {x.tool_names} against {y.tool_names}"
        for tx, ty in zip(x.tools, y.tools, strict=True):
            if tx != ty:
                return f"request {i}: tool {json.loads(tx)['name']} described differently"
        if len(x.messages) != len(y.messages):
            return f"request {i}: {len(x.messages)} messages against {len(y.messages)}"
        for j, (mx, my) in enumerate(zip(x.messages, y.messages, strict=True)):
            if mx != my:
                return f"request {i}, message {j}: {_first_line_diff(mx, my)}"
    return None


# The one allowed difference in what a write sends (OWD-18, WP-23): the durable path adds the AI disclosure to an
# external write's text, which today's loop, outside the harness rollout, does not. Only that exact suffix is left out,
# in each markup ``app.harness.write_identity.disclosure.render`` writes; anything else a write sends still counts.
_DISCLOSURE = re.compile(
    "(?:\n\n\u2014 |\n\n---\n|\n\n)?(?:<p><em>)?_?(?:Sent|Written|Created|Committed) via PipesHub agent .*? \u00b7 "
    "(?:<a href=\"[^\"]*\">View the session</a></p>|\\[View the session\\]\\([^)]*\\)|<[^|>]*\\|View the session>"
    "|View the session: \\S+|session \\S+)$",
    re.S,
)


def without_disclosure(value: Any) -> Any:  # noqa: ANN401 - JSON
    """``value`` with the disclosure suffix taken off every text in it."""
    if isinstance(value, str):
        return _DISCLOSURE.sub("", value)
    if isinstance(value, dict):
        return {key: without_disclosure(item) for key, item in value.items()}
    if isinstance(value, list):
        return [without_disclosure(item) for item in value]
    return value


def _step(step: Any) -> tuple[str, str, str | None, str, str]:  # noqa: ANN401 - a ToolStep
    def dump(value: Any) -> str:  # noqa: ANN401 - JSON
        return normalized(json.dumps(without_disclosure(value), sort_keys=True, default=str))

    # A failed step reaches the model and the user as its error. Its payload is the executor's internals: the durable
    # port refuses an unknown tool through an override that carries the message, legacy's executor carries none.
    result = dump(step.result) if step.status == "succeeded" else ""
    return step.name, step.status, step.error, dump(step.args), result


def outputs_difference(a: Any, b: Any) -> str | None:  # noqa: ANN401 - two DriveResults
    """Where two runs' outputs first differ after the model calls (DUR3 condition c): each tool step's name,
    status, error, arguments and result, the effects the world committed, the final answer and status, and the
    sources each step cited; ``None`` when they are equal."""
    ra, rb = a.recording, b.recording
    sa, sb = ra.tool_steps(), rb.tool_steps()
    if len(sa) != len(sb):
        return f"{len(sa)} tool steps against {len(sb)}"
    for i, (x, y) in enumerate(zip(sa, sb, strict=True)):
        if _step(x) != _step(y):
            fields = ("name", "status", "error", "args", "result")
            field = next(f for f, p, q in zip(fields, _step(x), _step(y), strict=True) if p != q)
            return f"tool step {i} ({x.name}): {field} differs"
        if normalized(json.dumps(x.sources, sort_keys=True)) != normalized(json.dumps(y.sources, sort_keys=True)):
            return f"tool step {i} ({x.name}): sources differ"
    ea = [(e.op, normalized(json.dumps(without_disclosure(e.payload), sort_keys=True))) for e in ra.effects]
    eb = [(e.op, normalized(json.dumps(without_disclosure(e.payload), sort_keys=True))) for e in rb.effects]
    if ea != eb:
        return f"effects {[e[0] for e in ea]} against {[e[0] for e in eb]}"
    if (ra.final.status, normalized(ra.final.answer)) != (rb.final.status, normalized(rb.final.answer)):
        return f"final {ra.final.status!r} {ra.final.answer[:80]!r} against {rb.final.status!r} {rb.final.answer[:80]!r}"
    return None


def _first_line_diff(a: str, b: str) -> str:
    la, lb = a.splitlines(), b.splitlines()
    for k, (x, y) in enumerate(zip(la, lb, strict=False)):
        if x != y:
            at = next((n for n, (p, q) in enumerate(zip(x, y, strict=False)) if p != q), min(len(x), len(y)))
            return f"line {k}: {x[max(0, at - 60):at + 100]!r} against {y[max(0, at - 60):at + 100]!r}"
    return f"{len(la)} lines against {len(lb)}"


__all__ = [
    "CapturingTransport",
    "Driven",
    "WireRequest",
    "drive_captured",
    "first_difference",
    "normalized",
    "outputs_difference",
]
