"""WP-118: each claimed run acts for its own org, whatever context the claimer was started or hinted from.

A run task copied from the poll loop or the hint consumer would serve its whole attempt as whatever org that task
had bound (main #4703's ``spawn_detached`` rule); the run binds the org Node's claim names instead."""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.core import org_context
from app.harness.worker.claimer import RunClaimer
from harness_core import bindings
from harness_core.driver import AssembledRuntime, RunDriver
from harness_core.journal import InMemoryContentStore, InMemoryJournal
from harness_core.kernel.core.messages import AssistantMessage, TextPart
from harness_core.kernel.durable import SampledTurn
from harness_core.testing import ManualClock
from harness_core.testing.clock import settle
from harness_core.testing.durable import FunctionToolPort
from harness_core.testing.run_spec import minimal_run_spec

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from harness_core.journal import Claim
    from harness_core.kernel.durable import DeltaKind, ModelCall


class _Model:
    async def sample(self, call: ModelCall, on_delta: Callable[[DeltaKind, str], Awaitable[None]]) -> SampledTurn:
        return SampledTurn(message=AssistantMessage(content=[TextPart(text="done")]))

    def restore(self, raw: bytes, *, provider: str, transport: str) -> AssistantMessage | None:
        return None


class _Assembler:
    def __init__(self) -> None:
        self.seen: dict[str, org_context.OrgContext | None] = {}

    async def build(self, claim: Claim) -> AssembledRuntime:
        self.seen[claim.run_id] = org_context._ctx.get()  # pyright: ignore[reportPrivateUsage]
        return AssembledRuntime(
            model=_Model(), tools=FunctionToolPort(), content=InMemoryContentStore(), bindings=bindings.NEUTRAL
        )


def _claimer(orgs: dict[str, str]) -> tuple[RunClaimer, InMemoryJournal, _Assembler]:
    clock = ManualClock()
    journal = InMemoryJournal(clock=clock)
    for run_id, org in orgs.items():
        journal.admit(run_id, org_id=org, session_id=f"ses-{run_id}", spec=minimal_run_spec(), input_text="q")
    assembler = _Assembler()
    driver = RunDriver(journal=journal, assembler=assembler, clock=clock)
    claimer = RunClaimer(
        journal=journal, driver=driver, worker_id="w1", slots=4, clock=clock, sleep=clock.sleep,
        idle_poll_s=0.01, max_idle_poll_s=0.02,
    )
    return claimer, journal, assembler


def _intruder() -> org_context.OrgContext:
    return org_context.OrgContext("org-intruder", "someone", "user", "jwt", "-")


async def test_a_polled_run_binds_its_claims_org_not_the_starting_context() -> None:
    claimer, journal, assembler = _claimer({"run-a": "org-a", "run-b": "org-b"})
    with org_context.bind(_intruder()):
        claimer.start()
    await settle(lambda: journal.status("run-a") == "completed" and journal.status("run-b") == "completed", turns=20_000)
    await claimer.stop()
    for run_id, org in (("run-a", "org-a"), ("run-b", "org-b")):
        seen = assembler.seen[run_id]
        assert seen is not None and (seen.org_id, seen.kind, seen.source) == (org, "job", "job:harness-run")


async def test_a_hinted_run_binds_its_claims_org_not_the_hints_context() -> None:
    claimer, journal, assembler = _claimer({"run-h": "org-h"})
    with org_context.bind(_intruder()):
        assert await claimer.claim_run("org-h", "run-h")
    await settle(lambda: journal.status("run-h") == "completed", turns=20_000)
    await claimer.stop()
    seen = assembler.seen["run-h"]
    assert seen is not None and seen.org_id == "org-h"
