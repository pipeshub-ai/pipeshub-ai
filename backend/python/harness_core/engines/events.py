"""Engine events: the only engine vocabulary the rest of the system sees (ARCHITECTURE §4.1, §4.8).

Every event carries ``EngineEventMeta(turn_id, seq)``. ``turn_id`` is derived
from the run and the turn index, never from the attempt, and ``seq`` counts
from 0 within the turn, so a turn re-driven after a crash produces the same
``command_id`` for the same event and the journal refuses the duplicate.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from harness_core.engines.repository_checkpoint import RepositoryCommit
    from harness_core.engines.types import EngineVersion, JsonValue
    from harness_core.sandbox.types import RuntimeMode
    from harness_core.workspace.types import ObjectRef


def _digest(*parts: str) -> str:
    h = hashlib.sha256()
    for part in parts:
        encoded = part.encode("utf-8")
        h.update(len(encoded).to_bytes(8, "big"))
        h.update(encoded)
    return h.hexdigest()


def turn_id_for(run_id: str, turn_index: int) -> str:
    if not run_id or turn_index < 0:
        raise ValueError("A turn id needs a run id and a non-negative index")
    return "trn_" + _digest("engine-turn", run_id, str(turn_index))[:32]


def command_id(run_id: str, turn_id: str, seq: int) -> str:
    """Deterministic journal command id for an engine event (§4.8)."""
    if not run_id or not turn_id or seq < 0:
        raise ValueError("A command id needs a run, a turn and a non-negative seq")
    return "cmd_" + _digest("engine-event", run_id, turn_id, str(seq))[:32]


# A step event's ``sub_session_id`` names the engine's own helper (Claude Code's Agent tool, a Codex sub-agent thread,
# an opencode task session) that took the step, as ``SubAgentStarted`` named it (EV-07); none: the run's own agent.


@dataclass(frozen=True, slots=True)
class EngineEventMeta:
    turn_id: str
    seq: int


@dataclass(frozen=True, slots=True)
class TextDelta:
    meta: EngineEventMeta
    message_id: str
    text: str


@dataclass(frozen=True, slots=True)
class ReasoningDelta:
    meta: EngineEventMeta
    message_id: str
    text: str


@dataclass(frozen=True, slots=True)
class MessageCompleted:
    meta: EngineEventMeta
    message_id: str
    text: str


@dataclass(frozen=True, slots=True)
class CommandStarted:
    meta: EngineEventMeta
    call_id: str
    command: str
    sub_session_id: str | None = None
    # The engine is known to lose a command's output now and then (its profile's ``outputMayBeLost``): an empty
    # result is then unverified, and the step says nothing was captured, never that the command printed nothing.
    output_may_be_lost: bool = False


@dataclass(frozen=True, slots=True)
class CommandOutput:
    meta: EngineEventMeta
    call_id: str
    text: str
    stream: Literal["stdout", "stderr"] = "stdout"
    sub_session_id: str | None = None


@dataclass(frozen=True, slots=True)
class CommandFinished:
    meta: EngineEventMeta
    call_id: str
    exit_code: int | None
    status: Literal["completed", "failed"]
    sub_session_id: str | None = None


@dataclass(frozen=True, slots=True)
class FileChanged:
    meta: EngineEventMeta
    call_id: str
    path: str
    change: Literal["added", "modified", "deleted"] = "modified"
    sub_session_id: str | None = None


@dataclass(frozen=True, slots=True)
class ToolCallSeen:
    meta: EngineEventMeta
    call_id: str
    tool: str
    status: Literal["started", "completed", "failed"]
    sub_session_id: str | None = None
    # The step's facts as the engine reported them (WP-112): without them a step named its tool and nothing else
    # ("Read a page", never which). ``arguments``: what the engine asked of the tool; ``title`` and ``paths``: an ACP
    # agent's own words for the call and the files it touches; ``result``: what one of the engine's own tools returned
    # (a gateway tool's result reaches the presenter from the gateway). Facts the engine sends after it announced the
    # call are a ``ToolCallUpdated``: one call is one start.
    arguments: Mapping[str, JsonValue] | None = None
    title: str = ""
    paths: tuple[str, ...] = ()
    result: str | None = None


@dataclass(frozen=True, slots=True)
class ToolCallUpdated:
    """A started call's facts, sent by the engine after it announced the call (Claude Code's ACP adapter announces a
    call with ``rawInput {}`` and sends the input in an update): the same step, never a second start (WP-112)."""

    meta: EngineEventMeta
    call_id: str
    tool: str
    sub_session_id: str | None = None
    arguments: Mapping[str, JsonValue] | None = None
    title: str = ""
    paths: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ApprovalRequested:
    meta: EngineEventMeta
    request_id: str
    permission: str
    patterns: tuple[str, ...]
    call_id: str | None = None


@dataclass(frozen=True, slots=True)
class SubAgentStarted:
    meta: EngineEventMeta
    sub_session_id: str
    call_id: str
    agent: str
    # What the run's agent asked the helper to do, in its words (Claude Code's description, a Codex prompt).
    task: str = ""


@dataclass(frozen=True, slots=True)
class SubAgentFinished:
    meta: EngineEventMeta
    sub_session_id: str
    call_id: str
    status: Literal["completed", "failed"]


@dataclass(frozen=True, slots=True)
class UsageReported:
    meta: EngineEventMeta
    input_tokens: int
    output_tokens: int
    reasoning_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    cost_usd: float | None = None
    sub_session_id: str | None = None


@dataclass(frozen=True, slots=True)
class ContextCompacted:
    """The engine compacted its own context mid-turn (its auto-compaction); token counts as it reports them."""

    meta: EngineEventMeta
    before_tokens: int | None = None
    after_tokens: int | None = None
    # The person's messages stay word for word after the summary (Codex); false: folded into it.
    user_messages_kept: bool = False


PlanStatus = Literal["pending", "in_progress", "completed", "cancelled"]


@dataclass(frozen=True, slots=True)
class PlanItem:
    title: str
    status: PlanStatus
    # The plan's own step id (deep mode names its children by it); none: positional ``task-<n>``.
    task_id: str | None = None
    deps: tuple[str, ...] = ()
    # The step names a tool the pinned approval rules ask about; none: not known (UXR-B1).
    asks_first: bool | None = None


@dataclass(frozen=True, slots=True)
class PlanUpdated:
    """The engine's whole task list as it now stands (Codex's plan, a todo tool's list): replaces the last one."""

    meta: EngineEventMeta
    items: tuple[PlanItem, ...]


TurnEndReason = Literal["completed", "interrupted", "failed", "limit"]


@dataclass(frozen=True, slots=True)
class TurnCompleted:
    meta: EngineEventMeta
    reason: TurnEndReason
    final_text: str = ""


@dataclass(frozen=True, slots=True)
class EngineError:
    """An engine-side failure. ``message`` is a fixed description, never engine output."""

    meta: EngineEventMeta
    code: str
    message: str
    retryable: bool = False


SignInState = Literal["requested", "signed_in", "failed"]


@dataclass(frozen=True, slots=True)
class SignInProgress:
    """A person's own sign-in to the engine inside their sandbox (SIGNIN): the chat's sign-in card.

    Ids and the plan kind only, never the sign-in link, the code, a token or the account's email (the link is
    asked of the worker by the person's own browser, the email goes to Node over the sign-in relay)."""

    meta: EngineEventMeta
    attempt_id: str
    state: SignInState
    reason_code: str | None = None
    plan: str | None = None


@dataclass(frozen=True, slots=True)
class RepositoryReady:
    """GIT-FLOW: the setup phase cloned the run's repository and the agent phase holds it, before the engine starts
    (the chat's "Copied owner/name" line). Names only: the repository and the one branch the run may push."""

    meta: EngineEventMeta
    repository: str
    branch: str


@dataclass(frozen=True, slots=True)
class NativeUnknown:
    """A native message of a kind the adapter does not know (EV-13): kept for audit and re-parsing, never mapped
    and never shown. ``raw`` is the message's JSON, cut at a bound; ``size_bytes`` its whole length."""

    meta: EngineEventMeta
    kind: str
    raw: bytes
    size_bytes: int

    @property
    def truncated(self) -> bool:
        return len(self.raw) < self.size_bytes


EngineEvent = (
    TextDelta
    | ReasoningDelta
    | MessageCompleted
    | CommandStarted
    | CommandOutput
    | CommandFinished
    | FileChanged
    | ToolCallSeen
    | ToolCallUpdated
    | ApprovalRequested
    | SubAgentStarted
    | SubAgentFinished
    | UsageReported
    | ContextCompacted
    | PlanUpdated
    | TurnCompleted
    | EngineError
    | SignInProgress
    | RepositoryReady
    | NativeUnknown
)


# What an engine prints in place of a command's result when the command printed nothing (opencode's bash tool). The
# step carries no output instead, so every engine's empty result reads the same, in the reader's language.
EMPTY_RESULT_PLACEHOLDERS = frozenset({"(no output)"})


def command_output(
    next_meta: Callable[[], EngineEventMeta], call_id: str, text: str, stream: Literal["stdout", "stderr"] = "stdout"
) -> list[EngineEvent]:
    """A finished command's whole result as a step, or none when it printed nothing (R25OPS-FU-2): an empty result is
    the absence of output, whatever the engine wrote for it. ``next_meta`` is the turn's sequence, taken only for a
    step, so the turn's numbering keeps no gap."""
    if not text or text.strip() in EMPTY_RESULT_PLACEHOLDERS:
        return []
    return [CommandOutput(next_meta(), call_id, text, stream)]


HELPER_STEPS = (CommandStarted, CommandOutput, CommandFinished, FileChanged, ToolCallSeen, ToolCallUpdated)


def taken_by(sub_session_id: str, events: list[EngineEvent]) -> list[EngineEvent]:
    """A helper's steps, each named as the helper's (EV-07); a mapper hands it only what a helper's steps map to."""
    return [replace(e, sub_session_id=sub_session_id) if isinstance(e, HELPER_STEPS) else e for e in events]


@dataclass(frozen=True, slots=True)
class EngineStateBlob:
    """A saved engine state as stored: sealed bytes in the object store, the run's repository commit when the
    state was captured (C2.10; ``None`` for a run without a repository, or a checkpoint taken before C2.10), and the
    sandbox runtime the turn ran on (R31-GVISO; ``None`` before it was recorded)."""

    engine: EngineVersion
    schema_version: int
    content: ObjectRef
    repository: RepositoryCommit | None = None
    isolation: RuntimeMode | None = None


@dataclass(frozen=True, slots=True)
class EngineState:
    """Engine state bytes as an adapter captures and restores them.

    The caller seals ``payload`` into an object store and keeps an
    ``EngineStateBlob``; on resume it loads the bytes back into an ``EngineState``.
    """

    engine: EngineVersion
    schema_version: int
    payload: bytes


__all__ = [
    "EMPTY_RESULT_PLACEHOLDERS",
    "ApprovalRequested",
    "CommandFinished",
    "CommandOutput",
    "CommandStarted",
    "ContextCompacted",
    "EngineError",
    "EngineEvent",
    "command_output",
    "EngineEventMeta",
    "EngineState",
    "EngineStateBlob",
    "FileChanged",
    "HELPER_STEPS",
    "MessageCompleted",
    "NativeUnknown",
    "PlanItem",
    "PlanStatus",
    "PlanUpdated",
    "ReasoningDelta",
    "SubAgentFinished",
    "SignInProgress",
    "RepositoryReady",
    "SignInState",
    "SubAgentStarted",
    "TextDelta",
    "ToolCallSeen",
    "ToolCallUpdated",
    "TurnCompleted",
    "TurnEndReason",
    "UsageReported",
    "taken_by",
    "command_id",
    "turn_id_for",
]
