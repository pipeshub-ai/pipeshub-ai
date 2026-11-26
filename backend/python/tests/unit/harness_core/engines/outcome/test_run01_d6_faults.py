"""RUN01-D6-FAULT: what leaves an engine turn is mapped once to a typed fault, alone, inside a task group or as a
cause, and one table says what each fault does (ADR-RUN-01 D6; a long research run's refused save left the MCP task group as
an ``ExceptionGroup`` no handler caught)."""

from __future__ import annotations

import pytest

from harness_core.engines.errors import EngineGone, EngineRefusal
from harness_core.engines.outcome.faults import DEGRADE, SAVE_REFUSED, fault_of
from harness_core.sandbox.errors import BrokerUnavailable, SandboxNotFound
from harness_core.workspace.types import (
    InvalidChangeSet,
    WorkspaceRequestRefused,
    WorkspaceStoreUnavailable,
    WorkspaceWriterBusy,
)


def _caused(error: BaseException) -> Exception:
    try:
        raise RuntimeError("transport") from error
    except RuntimeError as wrapped:
        return wrapped


@pytest.mark.parametrize(
    ("error", "kind", "degrade"),
    [
        (InvalidChangeSet("WORKSPACE_TOO_LARGE"), "save_refused", "keep_last_revision"),
        (WorkspaceRequestRefused("BAD_REQUEST"), "save_refused", "keep_last_revision"),
        (WorkspaceStoreUnavailable("down"), "store_unavailable", "store_wait"),
        (BrokerUnavailable("down"), "broker_unavailable", "sandbox_wait"),
        (SandboxNotFound("gone"), "turn_lost", "release"),
    ],
)
@pytest.mark.parametrize("wrap", ["alone", "group", "nested_group", "cause"])
def test_run01_d6_fault_1_a_fault_is_found_however_it_arrives(error: Exception, kind: str, degrade: str, wrap: str) -> None:
    arrived: BaseException = {
        "alone": error,
        "group": ExceptionGroup("mcp", [error]),
        "nested_group": BaseExceptionGroup("tg", [ExceptionGroup("mcp", [ValueError("other"), error])]),
        "cause": _caused(error),
    }[wrap]
    fault = fault_of(arrived)
    assert fault is not None and fault.kind == kind and fault.degrade == degrade and fault.error is error


def test_run01_d6_fault_2_the_degrade_table_has_one_row_per_fault_and_a_refused_save_never_redrives() -> None:
    fault = fault_of(ExceptionGroup("mcp", [InvalidChangeSet("WORKSPACE_TOO_LARGE")]))
    assert fault is not None and fault.code == SAVE_REFUSED and fault.at_turn_boundary == "fail"
    assert set(DEGRADE) == {
        "refused", "out_of_memory", "writer_busy", "broker_unavailable", "store_unavailable", "turn_lost", "save_refused"
    }
    refused = fault_of(ExceptionGroup("tg", [EngineRefusal("isolation")]))
    assert refused is not None and refused.kind == "refused" and refused.degrade == "fail"
    gone = fault_of(EngineGone("the engine exited"))
    assert gone is not None and gone.kind == "turn_lost" and gone.code == "engine_gone"
    busy = fault_of(WorkspaceWriterBusy("ws"))
    assert busy is not None and busy.degrade == "release"
    assert fault_of(ExceptionGroup("tg", [WorkspaceWriterBusy("ws")])) is None, "a busy writer is matched only as itself"
    assert fault_of(ValueError("unexpected")) is None
