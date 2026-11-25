"""Information parity between a shaped and a compiled model request (P4.8).

The shaper path's request is the reference. ``elements`` takes from it what the
model is told: every system-prompt paragraph, every message's text, each tool
call and result, each image, each artifact id, and the loop step it is shown last.
``missing`` says which of those the compiled request does not carry.

Placement is free: a system paragraph may sit in the system band or in the tail,
a memory note before the input or in the tail, a step footer on a result or once
in the tail. Rendering is free where it is cache-stable: a result quoted as
external data still carries its text, and the tool band's order is the compiler's.
Everything else is a violation, including history messages out of their order.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal, TypeVar, cast, override

from pydantic import TypeAdapter

from harness_core.kernel.core.messages import (
    AssistantMessage,
    ImagePart,
    TextPart,
    ToolMessage,
    UserMessage,
)
from harness_core.kernel.core.messages import Message as _AnyMessage

if TYPE_CHECKING:
    from collections.abc import Sequence

    from harness_core.kernel.core.messages import Message, ToolCall
    from tests.harness_replay.wire import WireRequest

_M = TypeVar("_M")
_MESSAGE: TypeAdapter[_AnyMessage] = TypeAdapter(_AnyMessage)

Kind = Literal[
    "section",
    "user",
    "assistant",
    "tool_call",
    "tool_result",
    "image",
    "artifact",
    "step",
]

STEP = re.compile(r"\[loop: step (\d+)/(\d+), stale_rounds=(\d+)\]")
# A quoted block escapes its closing marker inside content (``harness_core.context.quoting``).
_ESCAPES = (("<\\/quoted-data", "</quoted-data"),)


@dataclass(frozen=True)
class Element:
    kind: Kind
    value: str
    # History elements keep their order on both paths; a placed element (system, tail) does not.
    ordered: bool = False

    @override
    def __str__(self) -> str:
        short = self.value if len(self.value) <= 160 else self.value[:157] + "..."
        return f"{self.kind}: {short}"


@dataclass(frozen=True)
class Request:
    system: str | None
    messages: tuple[Message, ...]
    tools: tuple[str, ...] = ()

    @classmethod
    def of(cls, call: dict[str, Any]) -> Request:
        """A ``ScriptedTransport`` call (``{"system", "messages", "tools"}``)."""
        tools = tuple(t.name for t in call.get("tools") or ())
        return cls(call.get("system"), tuple(call["messages"]), tools)

    @classmethod
    def from_wire(cls, request: WireRequest) -> Request:
        """A request ``tests.harness_replay.wire`` captured."""
        messages = tuple(_MESSAGE.validate_json(m) for m in request.messages)
        return cls(request.system or None, messages, tuple(request.tool_names))


@dataclass
class Report:
    missing: list[Element] = field(default_factory=list[Element])
    out_of_order: list[Element] = field(default_factory=list[Element])

    @property
    def ok(self) -> bool:
        return not self.missing and not self.out_of_order

    @override
    def __str__(self) -> str:
        lines = [f"missing {e}" for e in self.missing] + [f"out of order {e}" for e in self.out_of_order]
        return "\n".join(lines)


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _as(kind: type[_M], value: object) -> _M | None:
    """``value`` as ``kind``: the message union does not narrow under ``isinstance`` here."""
    return value if isinstance(value, kind) else None


def _parts(content: object) -> list[object]:
    return list(cast("list[object]", content)) if isinstance(content, list) else []


def _text(content: object) -> str:
    if isinstance(content, str):
        return content
    return "".join(part.text for p in _parts(content) if (part := _as(TextPart, p)) is not None)


def _images(content: object) -> list[str]:
    return [_digest(image.source.data) for p in _parts(content) if (image := _as(ImagePart, p)) is not None]


def _digest(data: str) -> str:
    return hashlib.sha256(data.encode("utf-8")).hexdigest()[:16]


def _paragraphs(system: str | None) -> list[str]:
    return [_norm(p) for p in re.split(r"\n\s*\n", system or "") if p.strip()]


def _call_key(call: ToolCall) -> str:
    return json.dumps({"id": call.id, "name": call.name, "arguments": call.arguments}, sort_keys=True)


def _calls(message: object) -> list[str]:
    assistant = _as(AssistantMessage, message)
    return [_call_key(c) for c in assistant.tool_calls or ()] if assistant is not None else []


def elements(request: Request) -> list[Element]:
    """What the model is told, in order."""
    found: list[Element] = [Element("section", p) for p in _paragraphs(request.system)]
    last_step: str | None = None
    for message in request.messages:
        if (tool := _as(ToolMessage, message)) is not None:
            body = _norm(_text(tool.content))
            found.append(Element("tool_result", f"{tool.tool_call_id}|{int(tool.is_error)}|{body}", ordered=True))
            if tool.artifact_meta is not None:
                found.append(Element("artifact", tool.artifact_meta.artifact_id))
            if (match := STEP.search(tool.step_footer or "")) is not None:
                last_step = match.group(0)
        elif (assistant := _as(AssistantMessage, message)) is not None:
            text = _norm(_text(assistant.content))
            if text:
                found.append(Element("assistant", text, ordered=True))
            found.extend(Element("tool_call", key, ordered=True) for key in _calls(assistant))
        elif (user := _as(UserMessage, message)) is not None:
            text = _norm(_text(user.content))
            if text:
                found.append(Element("user", text, ordered=not user.injected))
        found.extend(Element("image", d) for d in _images(message.content))
    if last_step is not None:
        found.append(Element("step", last_step))
    return found


class _Compiled:
    """The compiled request, indexed for the lookups ``missing`` makes."""

    def __init__(self, request: Request) -> None:
        super().__init__()
        self.request = request
        self.paragraphs = set(_paragraphs(request.system))
        self.everything = _norm(
            "\n\n".join([request.system or "", *(_unquoted(_text(m.content)) for m in request.messages)])
        )
        self.images = {d for m in request.messages for d in _images(m.content)}
        tools = [t for m in request.messages if (t := _as(ToolMessage, m)) is not None]
        self.artifacts: set[str] = {t.artifact_meta.artifact_id for t in tools if t.artifact_meta is not None}
        footers = [t.step_footer for t in tools if t.step_footer]
        # The step the model is shown last: the last result's footer, else the tail's.
        tail = _norm(_text(request.messages[-1].content)) if request.messages else ""
        self.last_step = footers[-1] if footers else tail

    def position(self, element: Element, start: int) -> int | None:
        """The first message at or after ``start`` that carries ``element``."""
        for index in range(start, len(self.request.messages)):
            if self._carries(self.request.messages[index], element):
                return index
        return None

    def _carries(self, message: Message, element: Element) -> bool:
        match element.kind:
            case "tool_result":
                tool = _as(ToolMessage, message)
                if tool is None:
                    return False
                call_id, error, body = element.value.split("|", 2)
                return (
                    tool.tool_call_id == call_id
                    and int(tool.is_error) == int(error)
                    and body in _norm(_unquoted(_text(tool.content)))
                )
            case "tool_call":
                return element.value in _calls(message)
            case "assistant":
                return _as(AssistantMessage, message) is not None and element.value in _norm(_text(message.content))
            case "user":
                return _as(UserMessage, message) is not None and element.value in _norm(_unquoted(_text(message.content)))
            case _:
                return False

    def carries(self, element: Element) -> bool:
        match element.kind:
            case "section":
                return element.value in self.paragraphs or element.value in self.everything
            case "image":
                return element.value in self.images
            case "artifact":
                return element.value in self.artifacts or element.value in self.everything
            case "step":
                return element.value in self.last_step
            case _:
                return self.position(element, 0) is not None


def _unquoted(text: str) -> str:
    for marker, replacement in _ESCAPES:
        text = text.replace(marker, replacement)
    return text


def missing(shaped: Request, compiled: Request) -> Report:
    """What ``compiled`` lacks of ``shaped``, and the history messages it carries out of order."""
    target = _Compiled(compiled)
    report = Report()
    cursor = 0
    for element in elements(shaped):
        if not target.carries(element):
            report.missing.append(element)
            continue
        if not element.ordered:
            continue
        at = target.position(element, cursor)
        if at is None:
            report.out_of_order.append(element)
            continue
        # Parallel calls share a message: the cursor stays on it for their siblings.
        cursor = at
    return report


def compare_runs(shaped: Sequence[Request], compiled: Sequence[Request]) -> list[str]:
    """Call by call; a different number of calls is itself a violation."""
    problems: list[str] = []
    if len(shaped) != len(compiled):
        problems.append(f"call count: shaper {len(shaped)}, compiler {len(compiled)}")
    for index, (a, b) in enumerate(zip(shaped, compiled, strict=False)):
        report = missing(a, b)
        if not report.ok:
            problems.append(f"call {index}:\n{report}")
    return problems


__all__ = ["STEP", "Element", "Report", "Request", "compare_runs", "elements", "missing"]
