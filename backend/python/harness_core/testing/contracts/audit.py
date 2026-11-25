"""Contract suite for ``RunEvidenceSource`` (RE-*): provide ``evidence_env``, whose ``run`` creates one run pinned
with ``tools`` (and the adapter's own model routes) whose journal holds an intent per entry of ``intents``."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

import pytest

if TYPE_CHECKING:
    from harness_core.audit.ports import RunEvidenceSource


@dataclass(frozen=True)
class SeededRun:
    source: RunEvidenceSource
    org_id: str
    run_id: str
    routes: frozenset[str]


class EvidenceEnv(Protocol):
    async def run(self, *, tools: tuple[str, ...], intents: tuple[str, ...]) -> SeededRun: ...


class RunEvidenceSourceContract:
    @pytest.fixture
    def evidence_env(self) -> EvidenceEnv:
        raise NotImplementedError("provide an evidence_env fixture")

    async def test_re1_the_pinned_grant_and_the_journaled_intents(self, evidence_env: EvidenceEnv) -> None:
        tools = ("crm@inst-1/create", "crm@inst-1/lookup")
        seeded = await evidence_env.run(tools=tools, intents=("crm@inst-1/create",))
        found = await seeded.source.evidence(seeded.org_id, seeded.run_id)
        assert found is not None
        assert found.grant.tool_ids == frozenset(tools)
        assert found.grant.route_ids == seeded.routes and seeded.routes
        assert [(i.source, i.subject, i.run_id) for i in found.intents] == [
            ("effect_log", "crm@inst-1/create", seeded.run_id)
        ]
        assert all(i.invocation_id for i in found.intents)

    async def test_re2_another_orgs_run_is_not_found(self, evidence_env: EvidenceEnv) -> None:
        seeded = await evidence_env.run(tools=("crm@inst-1/lookup",), intents=())
        assert await seeded.source.evidence("org-someone-else", seeded.run_id) is None

    async def test_re3_an_unknown_run_is_not_found(self, evidence_env: EvidenceEnv) -> None:
        seeded = await evidence_env.run(tools=(), intents=())
        assert await seeded.source.evidence(seeded.org_id, "run-that-does-not-exist") is None


__all__ = ["EvidenceEnv", "RunEvidenceSourceContract", "SeededRun"]
