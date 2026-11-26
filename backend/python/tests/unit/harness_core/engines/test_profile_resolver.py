"""EP-10..EP-13 (E4a): resolving release → org ceiling → agent → request, over the ceiling port."""

from __future__ import annotations

import dataclasses
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

import pytest

from harness_core.access.types import AccessRouteRef
from harness_core.engines.errors import (
    ProfileCeilingUnavailable,
    ProfileInvalid,
    ProfileWidening,
)
from harness_core.engines.memory import InMemoryEngineCeilings
from harness_core.engines.profiles import (
    EngineProfileResolver,
    constraint_from_wire,
    profile_digest,
)
from harness_core.engines.types import (
    EngineMode,
    EngineProfile,
    EngineVersion,
    ProfileConstraint,
)
from harness_core.logs import HarnessLog
from harness_core.sandbox import RuntimeMode
from harness_core.testing.contracts.engine_profiles import (
    CeilingEnv,
    EngineCeilingSourceContract,
)

if TYPE_CHECKING:
    from pydantic import JsonValue

ACCESS = AccessRouteRef("route-1", "d1")
OPENCODE = EngineVersion("opencode", "1.18.34", "sha256:" + "1" * 64)
CODEX = EngineVersion("codex", "0.160.0", "sha256:" + "2" * 64)
LOG = HarnessLog.named("test.engine_profiles", requestId="req-1")


def _releases(access: AccessRouteRef) -> tuple[EngineProfile, ...]:
    base = EngineProfile(engine=OPENCODE, mode=EngineMode.BUILD, access=access, isolation_min=RuntimeMode.DOCKER_HARDENED)
    return (base, dataclasses.replace(base, engine=CODEX))


@dataclass
class _Env:
    ceilings: InMemoryEngineCeilings

    @property
    def source(self) -> InMemoryEngineCeilings:
        return self.ceilings

    async def store(self, org_id: str, ceiling: JsonValue) -> None:
        self.ceilings.store(org_id, ceiling)

    async def store_raw(self, org_id: str, raw: JsonValue) -> None:
        self.ceilings.stored[org_id] = raw

    def break_reads(self) -> None:
        self.ceilings.unreadable = True


class TestInMemoryEngineCeilingsContract(EngineCeilingSourceContract):
    @pytest.fixture
    def ceiling_env(self) -> CeilingEnv:
        return _Env(InMemoryEngineCeilings())


@pytest.fixture
def ceilings() -> InMemoryEngineCeilings:
    return InMemoryEngineCeilings()


@pytest.fixture
def resolver(ceilings: InMemoryEngineCeilings) -> EngineProfileResolver:
    return EngineProfileResolver(_releases, ceilings, log=LOG)


async def test_ep10_release_then_org_then_agent_then_request(
    resolver: EngineProfileResolver, ceilings: InMemoryEngineCeilings, caplog: pytest.LogCaptureFixture
) -> None:
    ceilings.store("org-1", {"engines": ["opencode", "codex"], "isolationMin": "gvisor", "limits": {"maxSteps": 30}})
    agent = constraint_from_wire({"engines": ["codex"], "limits": {"maxSteps": 20}})
    request = dataclasses.replace(
        constraint_from_wire({"mode": "plan", "tools": {"effects": ["read"]}}), result_schema={"type": "object"}
    )
    with caplog.at_level(logging.INFO):
        profile = await resolver.resolve_engine_profile("org-1", agent, request, access=ACCESS)
    assert profile.engine == CODEX
    assert profile.access == ACCESS
    assert (profile.isolation_min, profile.limits.max_steps, profile.mode) == (RuntimeMode.GVISOR, 20, EngineMode.PLAN)
    assert profile.tools.effects == frozenset({"read"})
    assert profile.result_schema == {"type": "object"}
    lines = [r.getMessage() for r in caplog.records if "engine.profile" in r.getMessage()]
    assert len(lines) == 1 and "engine.profile.resolved" in lines[0]
    assert "orgId=org-1" in lines[0] and profile_digest(profile) in lines[0] and "requestId=req-1" in lines[0]


async def test_ep10_no_ceiling_and_no_layers_is_the_first_release_default(resolver: EngineProfileResolver) -> None:
    assert await resolver.resolve_engine_profile("org-1", None, None, access=ACCESS) == _releases(ACCESS)[0]


async def test_ep11_a_changed_ceiling_reaches_the_next_resolution_only(
    resolver: EngineProfileResolver, ceilings: InMemoryEngineCeilings
) -> None:
    ceilings.store("org-1", {"limits": {"maxSteps": 30}})
    first = await resolver.resolve_engine_profile("org-1", None, None, access=ACCESS)
    ceilings.store("org-1", {"limits": {"maxSteps": 10}})
    second = await resolver.resolve_engine_profile("org-1", None, None, access=ACCESS)
    assert (first.limits.max_steps, second.limits.max_steps) == (30, 10)
    assert profile_digest(first) != profile_digest(second)


async def test_ep10_a_widening_names_the_layer_and_dimension_logged_once(
    resolver: EngineProfileResolver, ceilings: InMemoryEngineCeilings, caplog: pytest.LogCaptureFixture
) -> None:
    ceilings.store("org-1", {"approvals": {"write": "deny"}})
    with caplog.at_level(logging.INFO), pytest.raises(ProfileWidening) as refused:
        await resolver.resolve_engine_profile(
            "org-1", ProfileConstraint(), constraint_from_wire({"approvals": {"write": "ask"}}), access=ACCESS
        )
    assert (refused.value.field, refused.value.layer) == ("approvals.write", "request")
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "engine.profile.refused" in warnings[0] and "reasonCode=profile_widening" in warnings[0]
    assert "dimension=approvals.write" in warnings[0] and "layer=request" in warnings[0]


async def test_ep12_an_unreadable_ceiling_refuses_and_never_falls_back(
    resolver: EngineProfileResolver, ceilings: InMemoryEngineCeilings, caplog: pytest.LogCaptureFixture
) -> None:
    ceilings.unreadable = True
    with pytest.raises(ProfileCeilingUnavailable):
        await resolver.resolve_engine_profile("org-1", None, None, access=ACCESS)
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1 and "reasonCode=profile_ceiling_unavailable" in warnings[0]


async def test_ep12_an_invalid_ceiling_refuses(resolver: EngineProfileResolver, ceilings: InMemoryEngineCeilings) -> None:
    ceilings.stored["org-1"] = {"skills": None}
    with pytest.raises(ProfileInvalid):
        await resolver.resolve_engine_profile("org-1", None, None, access=ACCESS)
