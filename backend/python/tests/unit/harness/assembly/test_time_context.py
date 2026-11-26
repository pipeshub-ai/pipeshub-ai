"""P4.5 P45-2: the time a durable run's prompt shows is fixed for the run (EV4: 0% cache reads with the flag off)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.harness.assembly.context import RunContext
from tests.contracts.test_durable_goldens import AGENT_SCENARIOS, run_durably

from . import world as w

if TYPE_CHECKING:
    import pytest

    from tests.unit.agents.adapter.support.scripted_transport import ScriptedTransport

ADMITTED = 1_791_244_800_000  # 2026-10-06T00:00:00Z


def _run(**kwargs: object) -> RunContext:
    return RunContext.from_claim(w.claim(w.pinned_spec(tool_ids=[], kb=[]), **kwargs))  # type: ignore[arg-type]


def test_p45_2_a_client_time_is_shown_as_today() -> None:
    run = _run(current_time="2026-10-06T09:15:00+02:00", created_at=ADMITTED)
    assert run.prompt_time == "2026-10-06T09:15:00+02:00"


def test_p45_2_a_time_zone_without_a_client_time_shows_the_admission_time() -> None:
    run = _run(timezone="Europe/Berlin", created_at=ADMITTED)
    assert run.prompt_time == "2026-10-06T00:00:00Z"
    assert _run(timezone="Europe/Berlin", created_at=ADMITTED).prompt_time == run.prompt_time


def test_p45_2_no_time_zone_and_no_client_time_shows_no_time_as_today() -> None:
    assert _run(timezone=None, created_at=ADMITTED).prompt_time is None


async def test_p45_2_every_turn_of_a_run_sends_the_same_system_prompt(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HARNESS_CONTEXT_COMPILER", raising=False)
    scenario = next(s for s in AGENT_SCENARIOS if s.name == "agent_quick_tool_call")
    transports: list[ScriptedTransport] = []
    _ = await run_durably(scenario, transports=transports)
    systems = [c["system"] for c in transports[0].calls]
    assert len(systems) >= 2
    assert len(set(systems)) == 1
    # The golden world admits its runs at epoch 0 with a time zone and no client time.
    assert "**Current time**: 1970-01-01T00:00:00Z" in systems[0]
