"""L0.4 X-3: a durable run never executes code on the host by omission."""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest

from app.harness.assembly.assembler import PipesHubRuntimeAssembler
from harness_core.driver import IsolationUnavailable, RunDriver
from harness_core.testing import ManualClock, ModelScript, Reply, ScriptedModel, Turn
from harness_core.testing.durable import ScriptedModelPort

from . import world as w

if TYPE_CHECKING:
    from collections.abc import Iterator

SANDBOX_TOOLS = {"run_code", "install_packages", "read_sandbox_file"}


@pytest.fixture(autouse=True)
def _deployment() -> Iterator[None]:
    # Nested patch.dict, not monkeypatch: monkeypatch would restore the deployment's "false" after
    # the deployment's own patch had already restored the environment, leaking it to later tests.
    with w.deployment(), patch.dict(os.environ, {"PIPESHUB_ENABLE_CODE_EXECUTION": "true"}):
        yield


def _assembler() -> PipesHubRuntimeAssembler:
    port = ScriptedModelPort(ScriptedModel(ModelScript(id="t", turns=[Turn(reply=Reply(text="ok"))])))
    return PipesHubRuntimeAssembler(w.Services(), model_factory=w.model_factory(port))


def _code_claim() -> object:
    return w.claim(w.pinned_spec(tool_ids=[], sandbox="agent-code"))


@pytest.mark.parametrize("mode", [None, "", "docekr"], ids=["unset", "blank", "typo"])
async def test_l4_9_a_code_run_without_an_isolating_sandbox_is_refused(
    mode: str | None, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    if mode is None:
        monkeypatch.delenv("SANDBOX_MODE", raising=False)
    else:
        monkeypatch.setenv("SANDBOX_MODE", mode)
    built: list[object] = []
    monkeypatch.setattr(
        "app.agent_loop_lib.sandbox.manager.SandboxManager.__init__",
        lambda self, *a, **k: built.append(self),  # type: ignore[misc]
    )
    caplog.set_level(logging.INFO)
    with pytest.raises(IsolationUnavailable) as refused:
        _ = await _assembler().build(_code_claim())  # type: ignore[arg-type]
    assert (refused.value.code, refused.value.reason) == ("isolation_unavailable", "sandbox_unconfigured")
    assert built == []
    (line,) = [r for r in caplog.records if getattr(r, "harness_event", None) == "run.sandbox_unavailable"]
    assert line.levelno == logging.WARNING
    assert line.harness["reason"] == "isolation_unavailable"  # type: ignore[attr-defined]
    assert line.harness["runId"] == "run-1"  # type: ignore[attr-defined]


async def test_l4_9_the_driver_fails_the_refused_run_with_the_reason_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SANDBOX_MODE", raising=False)
    clock = ManualClock()
    journal = w.pipeshub_journal(clock=clock, lease_ms=30_000)
    spec = w.pinned_spec(tool_ids=[], sandbox="agent-code")
    journal.admit("run-1", org_id=w.ORG, session_id="ses-1", spec=spec, input_text="plot it")
    driver = RunDriver(journal=journal, assembler=w.ClaimAssembler(_assembler()), clock=clock)
    failed = await driver.run_claimed(await w.claim_next(journal))
    assert (failed.status, failed.reason) == ("failed", "isolation_unavailable")
    assert journal.status("run-1") == "failed"


async def test_l4_11_local_mode_without_the_operator_opt_in_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SANDBOX_MODE", "local")
    monkeypatch.delenv("SANDBOX_ALLOW_LOCAL", raising=False)
    with pytest.raises(IsolationUnavailable):
        _ = await _assembler().build(_code_claim())  # type: ignore[arg-type]


async def test_l4_11_an_explicit_local_sandbox_is_still_available_for_development(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SANDBOX_MODE", "local")
    # Main's #3803: local mode needs the operator's explicit opt-in on both loops.
    monkeypatch.setenv("SANDBOX_ALLOW_LOCAL", "true")
    runtime = await _assembler().build(_code_claim())  # type: ignore[arg-type]
    assert "run_code" in runtime.tools.names  # type: ignore[attr-defined]
    assert runtime.close is not None
    await runtime.close()


async def test_a_run_that_did_not_pin_code_never_builds_a_sandbox(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SANDBOX_MODE", raising=False)
    runtime = await _assembler().build(w.claim(w.pinned_spec(tool_ids=[])))  # type: ignore[arg-type]
    assert not SANDBOX_TOOLS & set(runtime.tools.names)  # type: ignore[attr-defined]
