"""Cleanup audit, Opik item: an attempt's trace names its conversation and run, so erasing the conversation can find
and delete it; an agent run inside it adds no second root trace; the operator is told what Opik receives."""

from __future__ import annotations

import contextlib
import logging
from types import SimpleNamespace
from typing import TYPE_CHECKING
from unittest.mock import patch

from harness_core.models.transports.opik_tracing import (
    maybe_start_conversation_trace,
    maybe_start_run_trace,
)

if TYPE_CHECKING:
    import pytest


@contextlib.contextmanager
def _trace_cm(**_kwargs: object):  # noqa: ANN202
    yield SimpleNamespace()


def test_off_opens_nothing() -> None:
    with patch("opik.start_as_current_trace") as start:
        with maybe_start_conversation_trace(enabled=False, org_id="o", conversation_id="c", run_id="r"):
            pass
    start.assert_not_called()


def test_on_opens_one_trace_threaded_by_the_conversation_naming_the_run() -> None:
    with patch("opik.start_as_current_trace", return_value=_trace_cm()) as start:
        with maybe_start_conversation_trace(
            enabled=True, org_id="org-1", conversation_id="conv-1", run_id="run-1", project_name="proj",
        ):
            pass
    start.assert_called_once()
    kwargs = start.call_args.kwargs
    assert kwargs["thread_id"] == "conv-1"
    assert kwargs["metadata"] == {"org_id": "org-1", "conversation_id": "conv-1", "run_id": "run-1"}
    assert kwargs["project_name"] == "proj"
    assert "input" not in kwargs, "the trace itself carries ids only"


def test_an_opik_failure_never_breaks_the_attempt() -> None:
    with patch("opik.start_as_current_trace", side_effect=RuntimeError("opik down")):
        with maybe_start_conversation_trace(enabled=True, org_id="o", conversation_id="c", run_id="r"):
            ran = True
    assert ran


def test_an_agent_run_inside_the_attempts_trace_opens_no_second_root_trace() -> None:
    from app.agent_loop_lib.core.context import RunContext
    from app.agent_loop_lib.core.types import Goal

    run_ctx = RunContext(role_name="assistant", model="m")
    with patch("opik.opik_context.get_current_trace_data", return_value=SimpleNamespace(id="t-1")), \
            patch("opik.start_as_current_trace") as start:
        with maybe_start_run_trace(enabled=True, run_ctx=run_ctx, goal=Goal(description="g")):
            pass
    start.assert_not_called()


def test_enabling_opik_warns_what_is_sent_there(caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.utils.opik import warn_what_opik_receives

    monkeypatch.setenv("OPIK_URL_OVERRIDE", "http://opik.example")
    with caplog.at_level(logging.WARNING):
        warn_what_opik_receives()
    [record] = [r for r in caplog.records if "opik.content_sent" in r.getMessage()]
    assert record.levelno == logging.WARNING
    assert "prompts" in record.getMessage() and "erased" in record.getMessage()
    caplog.clear()
    monkeypatch.delenv("OPIK_URL_OVERRIDE")
    monkeypatch.delenv("OPIK_API_KEY", raising=False)
    with caplog.at_level(logging.WARNING):
        warn_what_opik_receives()
    assert not [r for r in caplog.records if "opik.content_sent" in r.getMessage()]


def test_cleanup_opik_the_claimer_does_not_load_the_run_assembly() -> None:
    """The egress proxy's small container imports the claimer; loading the run assembly there kept it from starting."""
    import subprocess
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parents[3]
    probe = "import sys, app.harness.worker.claimer; print('app.harness.assembly.loop' in sys.modules)"
    loaded = subprocess.run(
        [sys.executable, "-c", probe], cwd=root, capture_output=True, text=True, check=True, timeout=120,
    ).stdout.strip()
    assert loaded == "False"
