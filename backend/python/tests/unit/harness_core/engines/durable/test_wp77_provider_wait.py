"""WP77-PW-0..3: an engine turn the model provider failed is waited out, then ends as a labelled partial.

A research run: Azure failed two calls a second apart (the Model Gateway answered
``provider_unavailable``), Codex's one immediate retry failed too, and the engine kernel failed the child at once,
so the 2.5 minutes of research it had gathered ended in a fixed notice. The loop already waits a transient model
failure out (P4.9, ``llm_backoff``); the engine kernel now does too: the run yields with its checkpoint and a bounded
backoff that never counts as a crash, and the next attempt re-drives the turn on the engine's restored session.
Past the bound the run is cut short by the provider (``truncatedBy: provider``), as a spent budget cuts it: it
completes with what it gathered, labelled partial, and its parent gets that answer to present.
"""

from __future__ import annotations

import pytest

from harness_core.engines.durable.kernel import TRANSIENT_PROVIDER_CODES
from harness_core.kernel.durable.provider_wait import PROVIDER_RECHECK_S
from harness_core.testing.engines.durable import (
    AFTER_TOOLS,
    EngineWorld,
    ScriptedEngineAdapter,
)

RUN = "run-pw"
LLM_BACKOFF = "llm_backoff"


def _world(*codes: str | None) -> tuple[EngineWorld, ScriptedEngineAdapter]:
    world = EngineWorld()
    adapter = world.adapter
    assert isinstance(adapter, ScriptedEngineAdapter)
    adapter.fail_with.extend(codes)
    world.admit(RUN, "say hello")
    return world, adapter


def _yields(world: EngineWorld) -> list[str]:
    return [str(e.data["reason"]) for e in world.events(RUN) if e.type == "run.yielded"]


def test_wp77_pw_0_the_transient_codes_are_the_gateways_retryable_ones() -> None:
    assert frozenset({"provider_unavailable", "rate_limited"}) == TRANSIENT_PROVIDER_CODES
    assert sum(PROVIDER_RECHECK_S) <= 300, "a provider outage holds the run's slot at most five minutes"


@pytest.mark.parametrize("code", sorted(TRANSIENT_PROVIDER_CODES))
async def test_wp77_pw_1_a_provider_that_recovers_completes_the_run(code: str) -> None:
    world, adapter = _world(f"{AFTER_TOOLS}{code}", f"{AFTER_TOOLS}{code}")
    for delay in PROVIDER_RECHECK_S[:2]:
        disposition = await world.attempt(RUN)
        assert (disposition.status, disposition.reason) == ("yielded", LLM_BACKOFF), disposition
        assert world.journal.status(RUN) == "queued"
        _ = world.clock.advance(delay)
    final = await world.attempt(RUN)
    assert final.status == "completed", final
    assert _yields(world) == [LLM_BACKOFF, LLM_BACKOFF]
    types = [e.type for e in world.events(RUN)]
    assert "run.failed" not in types and "run.quarantined" not in types
    completed = [e.data for e in world.events(RUN) if e.type == "run.completed"]
    assert completed and "truncatedBy" not in completed[0], completed
    # The re-driven turns continue the engine's own session, restored from the checkpoint the wait saved.
    assert adapter.restored[1:] and all(state is not None for state in adapter.restored[1:]), adapter.restored


async def test_wp77_pw_2_a_provider_that_stays_down_ends_as_a_partial_cut_short_by_the_provider() -> None:
    world, _ = _world(*[f"{AFTER_TOOLS}provider_unavailable"] * (len(PROVIDER_RECHECK_S) + 1))
    for delay in PROVIDER_RECHECK_S:
        disposition = await world.attempt(RUN)
        assert (disposition.status, disposition.reason) == ("yielded", LLM_BACKOFF), disposition
        _ = world.clock.advance(delay)
    final = await world.attempt(RUN)
    assert final.status == "completed", final
    completed = [e.data for e in world.events(RUN) if e.type == "run.completed"]
    assert completed == [{"outcome": "answered", "truncatedBy": "provider"}], completed
    assert "run.failed" not in [e.type for e in world.events(RUN)]


async def test_wp77_pw_3_a_provider_refusal_that_waiting_cannot_fix_still_fails_at_once() -> None:
    world, _ = _world(f"{AFTER_TOOLS}provider_auth_failed")
    final = await world.attempt(RUN)
    assert (final.status, final.reason) == ("failed", "provider_auth_failed")
    assert _yields(world) == []

