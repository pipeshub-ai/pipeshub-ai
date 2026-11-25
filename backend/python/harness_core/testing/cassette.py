"""Record and replay a model provider's side of the gateway (G-49 / GA-26: the "VCR gateway").

A ``CassetteTransport`` sits where the model gateway (or the loop's model port) holds a provider transport. In
``record`` mode it passes every call to an inner ``LLMTransport`` and keeps what came back, stream events included;
in ``replay`` mode it answers each call from the cassette, in order, and refuses a request that is not the one
recorded at that position. So a journey recorded once replays with no provider, no network and no spend, and a change
in what the harness asks the model shows as a ``CassetteMismatch`` naming the first call that differs.

A cassette is plain JSON a reviewer can read in a PR. ``source`` says what was recorded: ``scripted`` (a scripted
model; the only kind CI commits) or ``provider`` (a live provider, which must go through ``Scrubber`` before it is
saved). ``save`` refuses a cassette that still holds a credential.

The request digest covers the non-system messages and the tool names: the system prompt carries the date, so a
digest over it would differ on every day a cassette is replayed.
"""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING, Any, Literal, override

from pydantic import BaseModel, ConfigDict, JsonValue

from harness_core.kernel.core.exceptions import TransportError
from harness_core.kernel.core.responses import (
    ModelResponse,
    StructuredResponse,
    TokenUsage,
)
from harness_core.kernel.core.streaming import StreamCompleteEvent, StreamEvent
from harness_core.models.output_bound import output_limit
from harness_core.models.transports.base import LLMTransport
from harness_core.testing.replay.scrub import SecretLeftBehind, find_secrets

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Sequence
    from pathlib import Path

    from harness_core.kernel.core.messages import Message
    from harness_core.kernel.core.tool_schema import ToolChoice, ToolSchema

FORMAT = "pipeshub.harness.cassette/v1"
Source = Literal["scripted", "provider"]
Call = Literal["stream", "complete", "structured"]
_LAST_USER_CHARS = 300


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class CassetteRequest(_Frozen):
    """What the harness asked, reduced to what matching and a reviewer need."""

    call: Call
    digest: str
    message_count: int
    last_user: str = ""
    tools: list[str] = []


class CassetteError(_Frozen):
    """A failed call, raised again on replay as the ``TransportError`` it was."""

    message: str
    status_code: int | None = None
    retryable: bool = False
    partial_output: bool = False


class Interaction(_Frozen):
    request: CassetteRequest
    events: list[StreamEvent] = []
    structured: dict[str, JsonValue] | None = None
    usage: TokenUsage | None = None
    error: CassetteError | None = None


class CassetteManifest(_Frozen):
    name: str
    source: Source
    provider: str
    model: str = ""
    accepts_output_limit: bool = False


class Cassette(_Frozen):
    format: Literal["pipeshub.harness.cassette/v1"] = FORMAT
    manifest: CassetteManifest
    interactions: list[Interaction] = []

    def to_json(self) -> str:
        return json.dumps(self.model_dump(mode="json"), indent=1, ensure_ascii=False) + "\n"

    def save(self, path: Path) -> None:
        text = self.to_json()
        found = [f for f in find_secrets(self.model_dump(mode="json")) if not _reserved_address(f)]
        if found:
            raise SecretLeftBehind(found)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> Cassette:
        return cls.model_validate_json(path.read_text(encoding="utf-8"))


def _reserved_address(finding: str) -> bool:
    """An email at a domain reserved for examples (RFC 2606) is a fixture's, never a person's."""
    kind, _, address = finding.partition(": ")
    domain = address.rpartition("@")[2].lower()
    return kind == "email" and (domain.endswith(".example") or domain in _RESERVED_DOMAINS)


_RESERVED_DOMAINS = frozenset({"example.com", "example.org", "example.net"})


class CassetteMismatch(AssertionError):
    """A replayed call is not the call recorded at its position."""


class CassetteExhausted(AssertionError):
    """A replayed run asked the model more often than the recording did."""


def _text(message: Message) -> str:
    content = getattr(message, "content", "")
    if isinstance(content, str):
        return content
    return "".join(str(getattr(part, "text", "")) for part in content)


def describe_request(call: Call, messages: Sequence[Message], tools: Sequence[ToolSchema] | None) -> CassetteRequest:
    body = [m.model_dump(mode="json", exclude_none=True) for m in messages if str(m.role.value) != "system"]
    names = sorted(t.name for t in tools or ())
    raw = json.dumps({"messages": body, "tools": names}, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    users = [m for m in messages if str(m.role.value) == "user"]
    return CassetteRequest(
        call=call,
        digest=hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20],
        message_count=len(messages),
        last_user=_text(users[-1])[:_LAST_USER_CHARS] if users else "",
        tools=names,
    )


def _as_error(exc: BaseException) -> CassetteError:
    if isinstance(exc, TransportError):
        return CassetteError(
            message=str(exc), status_code=exc.status_code, retryable=exc._transient,  # pyright: ignore[reportPrivateUsage]
            partial_output=exc.partial_output,
        )
    return CassetteError(message=f"{type(exc).__name__}: {exc}")


def _raise(error: CassetteError) -> None:
    raise TransportError(
        error.message, status_code=error.status_code, retryable=error.retryable, partial_output=error.partial_output
    )


class CassetteTransport(LLMTransport):
    """``record(inner, ...)`` keeps every call ``inner`` answers; ``replay(cassette)`` answers from the cassette."""

    def __init__(self, cassette: Cassette, inner: LLMTransport | None = None) -> None:
        super().__init__()
        self._manifest = cassette.manifest
        self._inner = inner
        self._interactions = list(cassette.interactions)
        self._played = 0
        # The first refusal: the harness may turn it into a failed run, so the test still learns why.
        self._refused: AssertionError | None = None

    @classmethod
    def record(cls, inner: LLMTransport, *, name: str, source: Source = "scripted") -> CassetteTransport:
        manifest = CassetteManifest(
            name=name, source=source, provider=inner.provider, model=inner.model_name,
            accepts_output_limit=inner.accepts_output_limit,
        )
        return cls(Cassette(manifest=manifest), inner)

    @classmethod
    def replay(cls, cassette: Cassette) -> CassetteTransport:
        return cls(cassette)

    @property
    def recording(self) -> bool:
        return self._inner is not None

    def cassette(self) -> Cassette:
        return Cassette(manifest=self._manifest, interactions=list(self._interactions))

    @property
    def played(self) -> int:
        return self._played

    def assert_played(self) -> None:
        """Every recorded call was asked for (a replay that stopped early behaved differently)."""
        if self._refused is not None:
            raise self._refused
        left = len(self._interactions) - self._played
        if not self.recording and left:
            raise CassetteMismatch(
                f"{self._manifest.name}: the replay made {self._played} model calls, the recording "
                f"{len(self._interactions)}; the first not made asked {self._interactions[self._played].request!r}"
            )

    @property
    @override
    def provider(self) -> str:
        return self._manifest.provider

    @property
    @override
    def model_name(self) -> str:
        return self._manifest.model

    @property
    @override
    def accepts_output_limit(self) -> bool:
        return self._manifest.accepts_output_limit

    def _next(self, asked: CassetteRequest) -> Interaction:
        refused: AssertionError | None = None
        if self._played >= len(self._interactions):
            refused = CassetteExhausted(
                f"{self._manifest.name}: model call {self._played + 1} was never recorded (asked {asked!r})"
            )
        elif (recorded := self._interactions[self._played]).request.call != asked.call or (
            recorded.request.digest != asked.digest
        ):
            refused = CassetteMismatch(
                f"{self._manifest.name}: model call {self._played + 1} differs from the recording.\n"
                f"  recorded: {recorded.request!r}\n  asked:    {asked!r}\n"
                "Re-record the cassette if the change is intended."
            )
        if refused is not None:
            self._refused = self._refused or refused
            raise refused
        self._played += 1
        return recorded

    def _keep(self, interaction: Interaction) -> None:
        self._interactions.append(interaction)
        self._played += 1

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
        asked = describe_request("complete", messages, tools)
        if self._inner is None:
            recorded = self._next(asked)
            if recorded.error is not None:
                _raise(recorded.error)
            final = recorded.events[-1]
            assert isinstance(final, StreamCompleteEvent)
            return final.response
        try:
            response = await self._inner.complete(
                messages, tools=tools, system=system, model=model, thinking_budget=thinking_budget, effort=effort,
                system_blocks=system_blocks, **output_limit(max_output_tokens),
                **({"tool_choice": tool_choice} if tool_choice else {}),
            )
        except Exception as exc:
            self._keep(Interaction(request=asked, error=_as_error(exc)))
            raise
        self._keep(Interaction(request=asked, events=[StreamCompleteEvent(response=response)]))
        return response

    @override
    async def complete_structured(
        self,
        messages: list[Message],
        output_schema: dict[str, Any],
        system: str | None = None,
        model: str | None = None,
        max_output_tokens: int | None = None,
    ) -> StructuredResponse:
        asked = describe_request("structured", messages, None)
        if self._inner is None:
            recorded = self._next(asked)
            if recorded.error is not None:
                _raise(recorded.error)
            return StructuredResponse(
                data=dict(recorded.structured or {}), usage=recorded.usage or TokenUsage(), model=self._manifest.model
            )
        try:
            response = await self._inner.complete_structured(
                messages, output_schema, system=system, model=model, **output_limit(max_output_tokens)
            )
        except Exception as exc:
            self._keep(Interaction(request=asked, error=_as_error(exc)))
            raise
        data = json.loads(json.dumps(response.data, default=str))
        self._keep(Interaction(request=asked, structured=data, usage=response.usage))
        return response

    @override
    async def stream(  # pyright: ignore[reportIncompatibleMethodOverride]
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
        asked = describe_request("stream", messages, tools)
        if self._inner is None:
            recorded = self._next(asked)
            for event in recorded.events:
                yield event
            if recorded.error is not None:
                _raise(recorded.error)
            return
        events: list[StreamEvent] = []
        kept = False
        try:
            async for event in self._inner.stream(
                messages, tools=tools, system=system, model=model, thinking_budget=thinking_budget, effort=effort,
                system_blocks=system_blocks, **output_limit(max_output_tokens),
                **({"tool_choice": tool_choice} if tool_choice else {}),
            ):
                events.append(event)
                if isinstance(event, StreamCompleteEvent):
                    # Kept before it is handed on: a caller that stops reading at the terminal event never resumes
                    # this generator, so nothing after the loop would run.
                    self._keep(Interaction(request=asked, events=list(events)))
                    kept = True
                yield event
        except GeneratorExit:
            # The caller stopped reading (a repetition cut): it reads exactly as far on replay.
            if not kept:
                self._keep(Interaction(request=asked, events=events))
            raise
        except Exception as exc:
            if not kept:
                self._keep(Interaction(request=asked, events=events, error=_as_error(exc)))
            raise
        if not kept:
            self._keep(Interaction(request=asked, events=events))


__all__ = [
    "FORMAT",
    "Cassette",
    "CassetteError",
    "CassetteExhausted",
    "CassetteManifest",
    "CassetteMismatch",
    "CassetteRequest",
    "CassetteTransport",
    "Interaction",
    "describe_request",
]
