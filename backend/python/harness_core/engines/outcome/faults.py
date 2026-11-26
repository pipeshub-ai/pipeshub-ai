"""Failures as values at the turn boundary (ADR-RUN-01 D6, the slice D1 needs).

An exception that leaves an engine turn is mapped once, by ``fault_of``, to a typed ``RunFault``, whether it arrives
alone, inside a task group's ``BaseExceptionGroup`` (a refused save left the MCP task group as an
``ExceptionGroup`` no typed handler caught) or as another error's cause. ``DEGRADE`` is the one table of what each
fault does; the kernel and the controller read it and keep no handler lists of their own.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, Literal, cast

from harness_core.access import errors as access_errors
from harness_core.budget import errors as budget_errors
from harness_core.engines.errors import EngineGone, EngineRefusal
from harness_core.engines.rate import EngineEventFlood
from harness_core.gateway import errors as gateway_errors
from harness_core.refusals import HarnessRefusal
from harness_core.sandbox.egress.errors import SECRET_BLOCKED
from harness_core.sandbox.errors import (
    BrokerUnavailable,
    IsolationUnavailable,
    SandboxConnectError,
    SandboxNotFound,
    SandboxOutOfMemory,
    SandboxRefusal,
)
from harness_core.skills.types import SkillBundleError
from harness_core.workspace.types import (
    SAVE_REFUSALS,
    WorkspaceStoreUnavailable,
    WorkspaceWriterBusy,
)

if TYPE_CHECKING:
    from collections.abc import Mapping
    from types import ModuleType

FaultKind = Literal[
    "refused", "out_of_memory", "writer_busy", "broker_unavailable", "store_unavailable", "turn_lost", "save_refused"
]
Degrade = Literal["fail", "release", "sandbox_wait", "store_wait", "keep_last_revision", "cut_short"]
SAVE_REFUSED: Final = "workspace_save_refused"
TURN_LOST: Final = "engine_turn_lost"
ENGINE_FAILED: Final = "engine_failed"
# How deep a fault is looked for under ``__cause__`` (an engine's HTTP client re-raises a gone engine as its own).
CAUSE_DEPTH: Final = 4

# What each fault does (ADR-RUN-01 D6). A save the store refused as sent keeps the last saved revision and saves the
# deliverables on their own (WP-82); one that still reaches the turn boundary fails the run, which keeps that
# revision, rather than re-driving it from scratch.
DEGRADE: Final[Mapping[FaultKind, Degrade]] = {
    "refused": "fail",
    # The sandbox's memory limit ended the turn: a new sandbox of the same size would meet it again.
    "out_of_memory": "fail",
    "writer_busy": "release",
    "broker_unavailable": "sandbox_wait",
    "store_unavailable": "store_wait",
    "turn_lost": "release",
    "save_refused": "keep_last_revision",
}

REFUSALS: Final[tuple[type[Exception], ...]] = (
    EngineRefusal, IsolationUnavailable, SkillBundleError, SandboxRefusal, EngineEventFlood,
)
SAVES_REFUSED: Final[tuple[type[Exception], ...]] = SAVE_REFUSALS


def _refusal_codes(*modules: ModuleType) -> frozenset[str]:
    return frozenset(
        code
        for module in modules
        for value in vars(module).values()
        if isinstance(value, type)
        and issubclass(value, HarnessRefusal)
        and isinstance(code := value.__dict__.get("reason_code"), str)
    )


# A stop PipesHub's gateways made by the run's rules (a model or route it was not granted, its budget or the org's, a
# forbidden credential, the egress cap): built from the error classes, so a refusal added later is one without anyone
# remembering this list.
PIPESHUB_STOP_CODES: Final[frozenset[str]] = _refusal_codes(gateway_errors, access_errors, budget_errors)


def _model_access_codes() -> frozenset[str]:
    """The codes of every model access refusal defined so far, an edition's included (its port refuses with its own
    ``ModelAccessRefused``): read when a turn ends, after the edition's modules were imported."""
    found: set[str] = set()
    pending: list[type] = [access_errors.ModelAccessRefused]
    while pending:
        cls = pending.pop()
        code = cls.__dict__.get("reason_code")
        if isinstance(code, str):
            found.add(code)
        pending.extend(cls.__subclasses__())
    return frozenset(found)


# WP-103: an engine's model call PipesHub refused as carrying what looked like a secret: the egress proxy's code for
# its secret refusals (``[gateway:secret_blocked]``) and the model gateway's for a vendor key in a call's header.
SECRET_BLOCKED_CODES: Final[frozenset[str]] = frozenset({SECRET_BLOCKED, "forbidden_credential"})


def engine_stop_cut(code: str) -> Literal["engine_error", "secret_blocked"] | None:
    """The limit an engine turn that ended on error ``code`` is cut short by, so what it gathered is answered from
    (with nothing gathered the cut still fails), or ``None`` when it fails the run with its code at once: PipesHub's
    own stops fail (WP-84); a model call blocked as carrying a secret is told as that (WP-103: a long research run read "the
    coding engine ran into an error"); anything else is the engine's own error."""
    if code in SECRET_BLOCKED_CODES:
        return "secret_blocked"
    return None if code in PIPESHUB_STOP_CODES or code in _model_access_codes() else "engine_error"


@dataclass(frozen=True, slots=True)
class RunFault:
    """A failure as a value: its ``kind``, the reason ``code`` the run records, and the exception it came from."""

    kind: FaultKind
    code: str
    error: BaseException

    @property
    def degrade(self) -> Degrade:
        return DEGRADE[self.kind]

    @property
    def at_turn_boundary(self) -> Degrade:
        """What the fault does when it ends the turn: the save degrade ran inside the turn, if it could."""
        return "fail" if self.degrade == "keep_last_revision" else self.degrade


@dataclass(frozen=True, slots=True)
class _Rule:
    kind: FaultKind
    types: tuple[type[BaseException], ...]
    # Whether the rule looks inside groups and causes; a writer that is busy is matched only as itself.
    nested: bool = True


# In order: the first rule that matches decides.
_RULES: Final[tuple[_Rule, ...]] = (
    _Rule("refused", REFUSALS),
    _Rule("out_of_memory", (SandboxOutOfMemory,)),
    _Rule("writer_busy", (WorkspaceWriterBusy,), nested=False),
    _Rule("broker_unavailable", (BrokerUnavailable,)),
    _Rule("store_unavailable", (WorkspaceStoreUnavailable,)),
    _Rule("turn_lost", (EngineGone, SandboxNotFound, SandboxConnectError)),
    _Rule("save_refused", SAVES_REFUSED),
)


def fault_of(exc: BaseException) -> RunFault | None:
    """The fault ``exc`` is, alone, inside a task group or as a cause; ``None``: not one the harness expects."""
    for rule in _RULES:
        found = _find(exc, rule.types, 0) if rule.nested else (exc if isinstance(exc, rule.types) else None)
        if found is not None:
            return RunFault(rule.kind, _code(rule.kind, found), found)
    return None


def _find(exc: BaseException, types: tuple[type[BaseException], ...], depth: int) -> BaseException | None:
    if isinstance(exc, types):
        return exc
    if isinstance(exc, BaseExceptionGroup):
        inner: tuple[BaseException, ...] = cast("BaseExceptionGroup[BaseException]", exc).exceptions
        return next((found for e in inner if (found := _find(e, types, depth)) is not None), None)
    cause = exc.__cause__
    return _find(cause, types, depth + 1) if cause is not None and depth < CAUSE_DEPTH else None


def _code(kind: FaultKind, error: BaseException) -> str:
    if kind == "save_refused":
        return SAVE_REFUSED
    if kind == "turn_lost" and not isinstance(error, EngineGone):
        return TURN_LOST
    for name in ("reason_code", "error_type", "code"):
        value = getattr(error, name, None)
        if isinstance(value, str) and value:
            return value
    return TURN_LOST if kind == "turn_lost" else ENGINE_FAILED


__all__ = [
    "DEGRADE",
    "PIPESHUB_STOP_CODES",
    "SECRET_BLOCKED_CODES",
    "engine_stop_cut",
    "ENGINE_FAILED",
    "REFUSALS",
    "SAVES_REFUSED",
    "SAVE_REFUSED",
    "TURN_LOST",
    "Degrade",
    "FaultKind",
    "RunFault",
    "fault_of",
]
