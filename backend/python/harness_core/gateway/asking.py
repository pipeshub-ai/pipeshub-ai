"""``ask_user`` at the gateway (C3.70, AP-13, J-25): an engine asks the person a question.

The loop's own durable model, offered to an engine through its tool endpoint: the question is shown as the chat's
ask-user card, the engine ends its turn, and the run completes ``needs_input``. The person's answer (the card, or
any reply) starts the session's next run, which carries the engine's state forward, so the wait can be any length
and nothing (sandbox, worker, lease) is held while it lasts. A reply sent while the turn still runs is steering
input, and the run takes it as the next turn instead of completing.

A run with a person in the chat asks, and so does a job it handed work to (ADR-RUN-03 D2): the job's question ends
the job ``needs_input``, and its parent puts the same card in its own chat and hands the answer back with the
request. A scheduled run has nobody to answer it, and neither has a job of one (ANSWER-CONTRACT-FU-3).
"""

from __future__ import annotations

import dataclasses
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from harness_core.contracts import canonical_sha256
from harness_core.gateway.types import McpResult, McpTool

if TYPE_CHECKING:
    from collections.abc import Mapping

    from pydantic import JsonValue

    from harness_core.contracts.generated.run_spec import RunSpec

ASK_USER: Final = "ask_user"
# The same tool for an engine that reserves the bare ``ask_user`` (``EngineCapabilities.reserved_tool_names``).
ASK_USER_ALIAS: Final = "pipeshub_ask_user"
ASK_USER_NAMES: Final = frozenset({ASK_USER, ASK_USER_ALIAS})
INPUT_PENDING: Final = "input_pending"
INVALID_QUESTION: Final = "invalid_question"
MAX_QUESTION_CHARS: Final = 2_000
MAX_OPTION_CHARS: Final = 200
MAX_OPTIONS: Final = 7

ASK_USER_TOOL: Final = McpTool(
    ASK_USER,
    "Ask the person you are working for a question you cannot answer without them (a missing choice, an "
    "ambiguous request, a decision that is theirs). Ask once, with everything you need in one question; offer "
    "short options when the answer is a choice. After calling this, end your turn: their answer arrives as your "
    "next message. Do not ask for approval of a tool call; the tools ask for that themselves.",
    {
        "type": "object",
        "properties": {
            "question": {"type": "string", "description": "The question, in plain words.", "maxLength": MAX_QUESTION_CHARS},
            "options": {
                "type": "array",
                "description": "Answers the person can pick; they can always write their own.",
                "items": {"type": "string", "maxLength": MAX_OPTION_CHARS},
                "maxItems": MAX_OPTIONS,
            },
        },
        "required": ["question"],
        "additionalProperties": False,
    },
)

def ask_user_tool(reserved: frozenset[str] = frozenset()) -> McpTool:
    """The gateway's ``ask_user`` as the engine can see it: under ``ASK_USER_ALIAS`` when the engine drops the
    bare name from an MCP server's list."""
    return ASK_USER_TOOL if ASK_USER not in reserved else dataclasses.replace(ASK_USER_TOOL, name=ASK_USER_ALIAS)


_ASKED_TEXT: Final = (
    "Your question is shown to the person. End this turn now without doing more work; their answer arrives as "
    "your next message."
)


@dataclass(frozen=True, slots=True)
class AskedQuestion:
    question: str
    options: tuple[str, ...] = ()

    @property
    def call_id(self) -> str:
        """The same for the same question, so an attempt that re-drives the turn shows one card, not two."""
        return "ask." + canonical_sha256({"question": self.question, "options": list(self.options)})[:32]


def asks_person(spec: RunSpec, *, parent_asks: bool = False) -> bool:
    """A person's run, or a job handed work by a run that can ask (``child`` with ``parent_asks``, pinned by the
    admission of the child): its parent asks for it. Before ANSWER-CONTRACT-1 a job had no ``ask_user``, and a
    job wrote a whole report about a subject it never found instead of asking; a job of a schedule's run is not
    offered it, or its run would wait on nobody."""
    if spec.run_kind == "child":
        return parent_asks
    return spec.run_kind == "interactive"


def parse_question(arguments: Mapping[str, JsonValue]) -> AskedQuestion | str:
    """The question asked, or what is wrong with the call (the engine may correct it and ask again)."""
    question = arguments.get("question")
    if not isinstance(question, str) or not question.strip():
        return "ask_user needs a non-empty `question`."
    if len(question) > MAX_QUESTION_CHARS:
        return f"The question is too long; keep it under {MAX_QUESTION_CHARS} characters."
    raw = arguments.get("options", [])
    if not isinstance(raw, list) or not all(isinstance(o, str) for o in raw):
        return "`options` must be a list of strings."
    options = tuple(dict.fromkeys(o.strip() for o in raw if isinstance(o, str) and o.strip()))
    if len(options) > MAX_OPTIONS or any(len(o) > MAX_OPTION_CHARS for o in options):
        return f"Offer at most {MAX_OPTIONS} options of at most {MAX_OPTION_CHARS} characters each."
    unknown = sorted(set(arguments) - {"question", "options"})
    if unknown:
        return f"ask_user takes `question` and `options` only, not {', '.join(unknown)}."
    return AskedQuestion(question.strip(), options)


def question_key(text: str) -> str:
    """Two askings of one question differ in case, spacing or the closing mark (as the chat's card compares them)."""
    # The lookbehind starts the trailing run only at its first character, so a long run is not rescanned (SEC-C-18).
    return re.sub(r"(?<![\s?!.:])[\s?!.:]+$", "", re.sub(r"\s+", " ", text.lower())).strip()


ALREADY_ANSWERED: Final = "already_answered"
_ANSWERED_TEXT: Final = (
    "The person already answered this question in the task you were given: “{answer}”. Carry on with that answer; "
    "do not ask it again."
)


def answered_result(answer: str) -> McpResult:
    """A question the person already answered (the answer travels in the task, ADR-RUN-03 D2): the answer, not a
    second card. A job that asked, and was handed the answer with the request, carries on."""
    return McpResult(
        text=_ANSWERED_TEXT.format(answer=answer),
        is_error=False,
        disposition="executed",
        retryable=False,
        reason_code=ALREADY_ANSWERED,
    )


def asked_result(asked: AskedQuestion) -> McpResult:
    return McpResult(
        text=_ASKED_TEXT,
        is_error=False,
        disposition="asked",
        retryable=False,
        invocation_id=asked.call_id,
        reason_code=INPUT_PENDING,
    )


def invalid_question(problem: str) -> McpResult:
    return McpResult(text=problem, is_error=True, disposition="refused", retryable=True, reason_code=INVALID_QUESTION)


__all__ = [
    "ALREADY_ANSWERED",
    "ASK_USER",
    "ASK_USER_ALIAS",
    "ASK_USER_NAMES",
    "ASK_USER_TOOL",
    "INPUT_PENDING",
    "INVALID_QUESTION",
    "MAX_OPTIONS",
    "MAX_OPTION_CHARS",
    "MAX_QUESTION_CHARS",
    "AskedQuestion",
    "answered_result",
    "ask_user_tool",
    "asked_result",
    "asks_person",
    "invalid_question",
    "parse_question",
    "question_key",
]
