"""WP-61 (G-45): the class of an answered run rises at its next claim, not inside the attempt
that is already running. Node raises the stored class on the resolve (R11-PRI-1) and serves the next claim at it
(R11D-PRI-1, execution-store contract). These tests pin the worker and gateway sides.

R11D-PRI-2 a loop attempt admits every model call at the class its claim named, for the whole attempt; the next
           claim's class is what the next attempt admits at.
R11D-PRI-3 an engine attempt's Model Gateway calls take the class from the run's model grant, which the gateway
           keeps for up to ``CACHE_TTL_S`` (60 s) per attempt: a class raised mid-attempt is seen at the next read
           past that, never sooner, and never by another attempt's cache entry.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import httpx

from app.harness.access.grants import CACHE_TTL_S, NodeRunModelGrants
from app.harness.assembly.model import ResolvedModel, admitted
from harness_core.gateway.types import GatewayClaims, RunAttemptBinding
from harness_core.testing.model_gateway import RecordedTransport

if TYPE_CHECKING:
    from harness_core.priority import PriorityClass


@dataclass
class _Slot:
    waited: bool = False

    def release(self) -> None:
        pass


@dataclass
class _Admission:
    limit: int = 1
    asked: list[PriorityClass | None] = field(default_factory=list)

    async def acquire(self, key: str, priority_class: PriorityClass | None, *, org: str | None = None) -> _Slot:
        del key, org
        self.asked.append(priority_class)
        return _Slot()


def _model() -> ResolvedModel:
    transport = RecordedTransport()
    return ResolvedModel(
        raw_llm=None,
        provider="openai",
        is_multimodal=False,
        context_length=None,
        is_reasoning=False,
        port=None,  # type: ignore[arg-type]
        transport=transport,
    )


async def test_r11d_pri_2_a_loop_attempt_keeps_its_claims_class_and_the_next_claim_brings_the_raised_one() -> None:
    gate = _Admission()
    attempt = admitted(_model(), gate, "background", "scheduled")
    for _ in range(3):
        (await attempt.transport.admit()).release()  # type: ignore[union-attr]
    # Node raised the run to approval_resume while this attempt held it; nothing reaches the running attempt.
    next_attempt = admitted(_model(), gate, "approval_resume", "scheduled")
    (await next_attempt.transport.admit()).release()  # type: ignore[union-attr]
    assert gate.asked == ["background", "background", "background", "approval_resume"]


def _claims(attempt: str) -> GatewayClaims:
    binding = RunAttemptBinding("org-1", "ses-1", "run-1", None, attempt, 1, "tools", "routes")
    return GatewayClaims(binding, "kid", expires_at=10_000_000)


async def test_r11d_pri_3_the_gateway_reads_a_raised_class_at_its_next_grant_read() -> None:
    stored = {"class": "background"}

    def answer(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(200, json={"runId": "run-1", "routes": [], "budgets": {}, "priorityClass": stored["class"]})

    now = [0.0]
    grants = NodeRunModelGrants(
        base_url="http://node",
        http_client=httpx.AsyncClient(base_url="http://node", transport=httpx.MockTransport(answer)),
        monotonic=lambda: now[0],
        wall=lambda: 0.0,
    )
    attempt = _claims("a1")
    assert (await grants.grant(attempt, "t")).priority_class == "background"
    stored["class"] = "approval_resume"
    now[0] = CACHE_TTL_S - 1
    assert (await grants.grant(attempt, "t")).priority_class == "background", "kept within the attempt's cache"
    now[0] = CACHE_TTL_S + 1
    assert (await grants.grant(attempt, "t")).priority_class == "approval_resume"
    assert (await grants.grant(_claims("a2"), "t")).priority_class == "approval_resume", "a new attempt reads it"
    await grants.aclose()
