"""The request-scoped label ledger, and the shared default that records nothing.

One ledger per chat request. It is bound in the task that runs the request,
so every task the request starts (tool calls, sub-agents, prefetch) reaches
the same one. It observes every model call at the wire: the request's
transports wrap each call (`transport.with_ledger`), and the few calls that
bypass them open a `model_call_scope` with their class. Writers that put a
source in front of a model (tools, history, attachments, the artifact
registry) tell it through the `note_*` functions here.

`LabelLedger` itself is the default: every method does nothing and `wire()`
returns None, so a build without labels sends no `provenance` at all. An
edition that labels answers returns its own from `ChatExtension.label_ledger`.
A writer never fails what it describes: every `note_*` call swallows errors,
and a real ledger records them as making its label incomplete.
"""

from __future__ import annotations

import contextvars
import logging
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence

    from app.agent_loop_lib.core.messages import Message
    from app.agents.agent_loop.context import AgentContext
    from app.modules.agents.privacy.label import (
        AgentKind,
        AnswerProvenance,
        ParentMode,
    )

logger = logging.getLogger(__name__)


class CallKind(StrEnum):
    """How a model call's input relates to the answer (see `inventory.py`)."""

    # Its prompt is the answer's own input: the ledger reads it.
    OBSERVED = "observed"
    # A tool's own call; the tool's result names what that call saw.
    INSIDE_TOOL = "inside_tool"
    # Its output is only a choice (a model key), never content.
    MODEL_CHOICE = "model_choice"
    # Runs after the label is final.
    AFTER_LABEL = "after_label"


class SkillOrigin(StrEnum):
    """Who can read a skill a run was shown, which decides what it adds to the label."""

    # Seeded with the product: the whole organization's.
    BUILTIN = "builtin"
    # Assigned to the run's agent by its creator: part of the agent's configuration.
    AGENT = "agent"
    # Created by this run: the model's own words.
    RUN = "run"
    # A person's own hand-written skill, or one whose source is not known.
    PERSON = "person"


class LabelLedger:
    """The shared default: records nothing and labels nothing."""

    # Whether this ledger produces a label. False only for the default.
    labels = False

    def wire(self) -> dict[str, Any] | None:
        """The label so far, in its wire shape; None when this build labels nothing."""
        return None

    def so_far(self) -> AnswerProvenance | None:
        """The label so far, for a check that reads it and changes nothing; None when this build labels nothing."""
        return None

    # ── model calls ─────────────────────────────────────────────────────────
    def observe_call(self, site: str, kind: CallKind, messages: Sequence[Message] | None = None) -> None:
        """A model call is about to be made with `messages`."""

    def observe_output(self, tool_calls: Iterable[tuple[str, str]]) -> None:
        """The `(id, name)` of each tool call a model response asked for."""

    def untagged_call(self, model: str) -> None:
        """A model call was made outside any scope the ledger knows."""

    # ── sources ─────────────────────────────────────────────────────────────
    def shown(
        self, *, records: Iterable[str] = (), vrids: Iterable[str] = (), nodes: Iterable[str] = (),
        call_id: str | None = None,
    ) -> None:
        """Records (by record id), their vrids, and containers a writer put in front of a model."""

    def personal(self, call_id: str | None = None) -> None:
        """A result read with the asker's own credential reached the run."""

    def agent(self, key: str, kind: AgentKind) -> None:
        """An agent's configuration or service identity shaped the run."""

    def chat_upload(self, conversation_id: str, record_id: str) -> None:
        """A file uploaded into this chat reached the run."""

    def parent(self, ref_id: str, ver: str, mode: ParentMode) -> None:
        """An earlier row, at its version, was given to the run."""

    def artifact_read(
        self, artifact_id: str, run_id: str | None = None, *, conversation_id: str | None = None,
        written_at: int | None = None,
    ) -> None:
        """The run read or listed an artifact; `run_id` is the run that wrote it, when known. For one whose own
        document names no run: its chat and when its record was last written, when known."""

    def skill_shown(self, name: str, origin: SkillOrigin, agent_key: str | None = None) -> None:
        """A skill's name, description, body or files reached the run. `agent_key` is the run's
        agent, which a skill assigned to it belongs to."""

    def untagged(self, where: str) -> None:
        """Something reached a prompt whose source the ledger cannot name."""

    # ── tool results ────────────────────────────────────────────────────────
    def tool_started(self, call_id: str, tool_path: str, name: str) -> None:
        """A tool call passed into the executor."""

    def tool_arguments(self, call_id: str, arguments: Mapping[str, Any]) -> None:
        """The arguments the model gave a call that passed into the executor (`tool_started`)."""

    def tool_finished(self, call_id: str) -> None:
        """A tool call ran: its result is what the model will be given."""

    def history_results(self, call_ids: Iterable[str]) -> None:
        """Tool results replayed from earlier rows (covered by those rows as parents)."""

    def answered(self, text: str) -> None:
        """The answer's final text, as the frame that carries its label sends it."""

    # ── request ─────────────────────────────────────────────────────────────
    async def note_request(self, context: AgentContext) -> None:
        """The request's own sources: attachments, history rows, the agent, the asker's notes."""

    def close(self) -> None:
        """The answer and its label have gone out; later calls are noted, not labelled."""


NOOP_LEDGER = LabelLedger()


# ── binding ─────────────────────────────────────────────────────────────────
class _Frame:
    __slots__ = ("value", "parent", "closed")

    def __init__(self, value: Any, parent: _Frame | None) -> None:  # noqa: ANN401
        self.value = value
        self.parent = parent
        self.closed = False


def _innermost(var: contextvars.ContextVar[_Frame | None]) -> _Frame | None:
    frame = var.get()
    while frame is not None and frame.closed:
        frame = frame.parent
    return frame


class _Binding:
    """For `with` and `async with`. Exits need not nest: a streaming generator
    can be closed from another place after something else bound on top, so a
    binding is a frame that closing marks closed, never a token reset."""

    def __init__(self, var: contextvars.ContextVar[_Frame | None], value: Any) -> None:  # noqa: ANN401
        self._var = var
        self._value = value
        self._frame: _Frame | None = None

    def __enter__(self) -> Any:  # noqa: ANN401
        self._frame = _Frame(self._value, self._var.get())
        self._var.set(self._frame)
        return self._value

    def __exit__(self, *exc_info: object) -> None:
        frame, self._frame = self._frame, None
        if frame is None:
            return
        frame.closed = True
        if self._var.get() is frame:
            parent = frame.parent
            while parent is not None and parent.closed:
                parent = parent.parent
            self._var.set(parent)

    async def __aenter__(self) -> Any:  # noqa: ANN401
        return self.__enter__()

    async def __aexit__(self, *exc_info: object) -> None:
        self.__exit__(*exc_info)


_ledger: contextvars.ContextVar[_Frame | None] = contextvars.ContextVar("label_ledger", default=None)


def open_ledger(context: AgentContext, *, service_agent: bool = False) -> LabelLedger:
    """A new ledger for the request `context` runs, from the edition. `service_agent` is the
    agent record's own flag: a service agent's run reads as the asker, but is still its."""
    from app.modules.agents.chat_extension import get_chat_extension

    try:
        return get_chat_extension().label_ledger(
            asker_id=context.artifact_owner_user_id or context.user_id,
            conversation_id=context.conversation_id,
            run_id=context.run_id,
            service_identity=bool(context.is_service_account or service_agent),
        )
    except Exception:
        logger.warning("answer label: no ledger could be opened", exc_info=True)
        return NOOP_LEDGER


async def note_request_sources(ledger: LabelLedger, context: AgentContext) -> None:
    """`ledger.note_request(context)`, which never fails the request: a failure
    leaves the label incomplete instead."""
    if not ledger.labels:
        return
    try:
        await ledger.note_request(context)
    except Exception:
        logger.warning("answer label: the request's own sources could not be read", exc_info=True)
        ledger.untagged("request")


def bind_ledger(ledger: LabelLedger) -> _Binding:
    """Bind `ledger` as the request's for the block."""
    return _Binding(_ledger, ledger)


def current_ledger() -> LabelLedger:
    frame = _innermost(_ledger)
    return frame.value if frame is not None else NOOP_LEDGER


# ── model call scopes ───────────────────────────────────────────────────────
@dataclass(frozen=True)
class ModelCall:
    site: str
    kind: CallKind


_model_call: contextvars.ContextVar[_Frame | None] = contextvars.ContextVar("label_model_call", default=None)


class model_call_scope(_Binding):
    """`with model_call_scope("grep_generation", CallKind.INSIDE_TOOL): ...` around a
    model call that does not go through the request's transports. The ledger
    (`ledger`, else the request's) is told about the call when the scope opens."""

    def __init__(
        self, site: str, kind: CallKind, messages: Sequence[Message] | None = None,
        *, ledger: LabelLedger | None = None,
    ) -> None:
        super().__init__(_model_call, ModelCall(site, kind))
        self._messages = messages
        self._ledger = ledger

    def __enter__(self) -> ModelCall:
        call = super().__enter__()
        _guarded(lambda ledger: ledger.observe_call(call.site, call.kind, self._messages), self._ledger)
        return call


def current_model_call() -> ModelCall | None:
    frame = _innermost(_model_call)
    return frame.value if frame is not None else None


# ── tool attribution ────────────────────────────────────────────────────────
_tool_call: contextvars.ContextVar[str | None] = contextvars.ContextVar("label_tool_call", default=None)


def enter_tool_call(call_id: str) -> contextvars.Token[str | None]:
    """What writers note from here on, in this task, belongs to tool call `call_id`."""
    return _tool_call.set(call_id)


def leave_tool_call(token: contextvars.Token[str | None] | None = None) -> None:
    if token is not None:
        try:
            _tool_call.reset(token)
            return
        except ValueError:
            pass
    _tool_call.set(None)


def current_tool_call() -> str | None:
    return _tool_call.get()


# ── writers ─────────────────────────────────────────────────────────────────
def _guarded(write: Any, ledger: LabelLedger | None = None) -> None:  # noqa: ANN401
    ledger = ledger or current_ledger()
    if not ledger.labels:
        return
    try:
        write(ledger)
    except Exception:
        logger.warning("answer label: a writer failed", exc_info=True)
        try:
            ledger.untagged("label_writer")
        except Exception:
            logger.debug("answer label: could not record the failure", exc_info=True)


def note_shown(*, records: Iterable[str] = (), vrids: Iterable[str] = (), nodes: Iterable[str] = ()) -> None:
    """A writer put these records (record ids), vrids and containers in front of a model."""
    records, vrids, nodes = list(records), list(vrids), list(nodes)
    _guarded(lambda ledger: ledger.shown(records=records, vrids=vrids, nodes=nodes, call_id=_tool_call.get()))


def note_personal() -> None:
    _guarded(lambda ledger: ledger.personal(call_id=_tool_call.get()))


def note_artifact_read(
    artifact_id: str, run_id: str | None = None, *, conversation_id: str | None = None, written_at: int | None = None,
) -> None:
    _guarded(
        lambda ledger: ledger.artifact_read(
            artifact_id, run_id, conversation_id=conversation_id, written_at=written_at,
        )
    )


def note_skills(shown: Iterable[tuple[str, SkillOrigin]], *, agent_key: str | None = None) -> None:
    """Skills a writer handed to the run (their names, descriptions, bodies or files), each with
    where it came from; `agent_key` is the run's agent."""
    shown = list(shown)
    if not shown:
        return

    def _write(ledger: LabelLedger) -> None:
        for name, origin in shown:
            ledger.skill_shown(name, origin, agent_key)

    _guarded(_write)


def note_untagged(where: str) -> None:
    _guarded(lambda ledger: ledger.untagged(where))


def note_tool_started(call_id: str, tool_path: str, name: str) -> None:
    """A tool call the loop makes itself, outside the executor, is tagged as the label hooks tag one
    that reaches it (`hooks.install_label_hooks`), so its result in a later prompt is accounted for."""
    _guarded(lambda ledger: ledger.tool_started(call_id, tool_path, name))


def note_tool_finished(call_id: str) -> None:
    """That call ran: its result is what the model will be given."""
    _guarded(lambda ledger: ledger.tool_finished(call_id))


def note_history_results(call_ids: Iterable[str]) -> None:
    ids = [c for c in call_ids if c]
    _guarded(lambda ledger: ledger.history_results(ids))


def record_ids_in(value: object, keys: frozenset[str] = frozenset({"record_id", "recordId"})) -> list[str]:
    """Every string under one of `keys` in a JSON-like result, for tools that print records by id."""
    found: list[str] = []
    stack = [value]
    while stack:
        item = stack.pop()
        if isinstance(item, dict):
            for key, child in item.items():
                if key in keys and isinstance(child, str) and child:
                    found.append(child)
                elif isinstance(child, (dict, list, tuple)):
                    stack.append(child)
        elif isinstance(item, (list, tuple)):
            stack.extend(item)
    return found


# ── frames ──────────────────────────────────────────────────────────────────
def provenance_wire() -> dict[str, Any] | None:
    ledger = current_ledger()
    if not ledger.labels:
        return None
    try:
        return ledger.wire()
    except Exception:
        logger.warning("answer label: could not be built", exc_info=True)
        from app.modules.agents.privacy.label import (
            LABEL_ERROR,
            AnswerProvenance,
            LabelDirect,
        )

        return AnswerProvenance(direct=LabelDirect(), complete=False, incomplete=(LABEL_ERROR,)).to_wire()


def with_provenance(data: dict[str, Any]) -> dict[str, Any]:
    """`data` with the request's label as `provenance`, when this build labels
    answers. A frame that already carries one keeps it. The ledger is told the
    answer's text first, which the label may depend on."""
    if "provenance" in data:
        return data
    answer = data.get("answer")
    if isinstance(answer, str) and answer:
        _guarded(lambda ledger: ledger.answered(answer))
    wire = provenance_wire()
    if wire is None:
        return data
    return {**data, "provenance": wire}


def with_unopened_provenance(data: dict[str, Any]) -> dict[str, Any]:
    """For an error frame written before the request's ledger was opened: the
    edition's label for "nothing was observed", or `data` unchanged."""
    if "provenance" in data:
        return data
    if current_ledger().labels:
        return with_provenance(data)
    try:
        from app.modules.agents.chat_extension import get_chat_extension

        wire = get_chat_extension().unopened_label()
    except Exception:
        logger.warning("answer label: the unopened label could not be built", exc_info=True)
        return data
    return data if wire is None else {**data, "provenance": wire}


__all__ = [
    "NOOP_LEDGER",
    "CallKind",
    "LabelLedger",
    "ModelCall",
    "SkillOrigin",
    "bind_ledger",
    "current_ledger",
    "current_model_call",
    "current_tool_call",
    "enter_tool_call",
    "leave_tool_call",
    "model_call_scope",
    "note_artifact_read",
    "note_history_results",
    "note_personal",
    "note_request_sources",
    "note_shown",
    "note_skills",
    "note_tool_finished",
    "note_tool_started",
    "note_untagged",
    "open_ledger",
    "provenance_wire",
    "record_ids_in",
    "with_provenance",
    "with_unopened_provenance",
]
