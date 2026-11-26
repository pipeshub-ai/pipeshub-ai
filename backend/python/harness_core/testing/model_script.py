"""Script DSL for :class:`~harness_core.testing.scripted_model.ScriptedModel`.

YAML form (``extra`` keys are rejected so typos fail loudly)::

    id: approval-jira-create
    turns:
      - expect: { last_user_contains: "create a P1 bug" }
        reply:
          tool_calls: [{ id: toolu_1, name: jira_create_issue, args: { summary: "Login 500" } }]
          usage: { input_tokens: 120, output_tokens: 14, cache_read_tokens: 100 }
      - expect: { tool_result_for: jira_create_issue, status: succeeded }
        reply: { text: "Created PROJ-1.", chunks: 4 }
      - reply: { error: { kind: rate_limited, retry_after_s: 2 } }
      - reply: { text: "partial", error: { kind: server_error, status: 503 } }  # cut mid-stream
"""

# No ``from __future__ import annotations``: pydantic resolves these field
# types at class creation, so the imports below must stay runtime imports.
from collections.abc import Callable
from typing import TYPE_CHECKING, Literal, Self

from pydantic import BaseModel, ConfigDict, JsonValue, model_validator

from harness_core.testing.model_types import ErrorKind, ModelRequest, Usage

if TYPE_CHECKING:
    from pathlib import Path

ToolStatus = Literal["succeeded", "failed"]

_DEFAULT_STATUS: dict[ErrorKind, int | None] = {
    "server_error": 500,
    "rate_limited": 429,
    "content_filter": 400,
    "timeout": None,
    "malformed": None,
}
_RETRYABLE: frozenset[ErrorKind] = frozenset({"server_error", "rate_limited", "timeout"})


class _Spec(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class ToolCallSpec(_Spec):
    id: str | None = None
    name: str
    args: dict[str, JsonValue] = {}


class ErrorSpec(_Spec):
    kind: ErrorKind
    status: int | None = None
    retry_after_s: float | None = None
    message: str = ""

    @property
    def effective_status(self) -> int | None:
        return self.status if self.status is not None else _DEFAULT_STATUS[self.kind]

    @property
    def retryable(self) -> bool:
        return self.kind in _RETRYABLE


class Reply(_Spec):
    text: str = ""
    chunks: int | list[str] | None = None
    reasoning: str = ""
    tool_calls: list[ToolCallSpec] = []
    error: ErrorSpec | None = None
    delay_ms: int = 0
    usage: Usage = Usage()
    stop_reason: str | None = None

    @model_validator(mode="after")
    def _explicit_chunks_match_text(self) -> Self:
        if isinstance(self.chunks, list):
            if self.text and self.text != "".join(self.chunks):
                raise ValueError("reply.text must equal the concatenated chunks")
        elif isinstance(self.chunks, int) and self.chunks < 1:
            raise ValueError("reply.chunks must be >= 1")
        return self

    @property
    def content(self) -> str:
        return "".join(self.chunks) if isinstance(self.chunks, list) else self.text

    def text_chunks(self) -> list[str]:
        if isinstance(self.chunks, list):
            return [c for c in self.chunks if c]
        if not self.text:
            return []
        n = min(self.chunks or 1, len(self.text))
        size, extra = divmod(len(self.text), n)
        out: list[str] = []
        start = 0
        for i in range(n):
            end = start + size + (1 if i < extra else 0)
            out.append(self.text[start:end])
            start = end
        return out

    def resolved_stop_reason(self) -> str:
        if self.stop_reason is not None:
            return self.stop_reason
        return "tool_use" if self.tool_calls else "end_turn"


class Expect(_Spec):
    last_user_contains: str | None = None
    tool_result_for: str | None = None
    status: ToolStatus | None = None


class Turn(_Spec):
    expect: Expect | None = None
    reply: Reply | None = None
    # Python-only: compute the reply from the request (e.g. echo a returned path).
    from_request: Callable[[ModelRequest], Reply] | None = None

    @model_validator(mode="after")
    def _exactly_one_reply(self) -> Self:
        if (self.reply is None) == (self.from_request is None):
            raise ValueError("a turn needs exactly one of reply / from_request")
        return self


class ModelScript(_Spec):
    id: str = "script"
    turns: list[Turn]


def parse_script(yaml_text: str) -> ModelScript:
    import yaml  # test-only dependency; keeps harness_core importable without it

    return ModelScript.model_validate(yaml.safe_load(yaml_text))


def load_script(path: "Path") -> ModelScript:
    return parse_script(path.read_text(encoding="utf-8"))


__all__ = [
    "ErrorKind",
    "ErrorSpec",
    "Expect",
    "ModelScript",
    "Reply",
    "ToolCallSpec",
    "ToolStatus",
    "Turn",
    "Usage",
    "load_script",
    "parse_script",
]
