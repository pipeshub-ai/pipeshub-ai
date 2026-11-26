"""ACP engines with no org ceiling: by default every released ACP engine is opted in, so an agent may name one and
it resolves, still under any ceiling that lists engines. The org's own opt-in stays a choice an edition makes."""

from __future__ import annotations

import pytest

from app.agents.engine_settings import EngineSettingsRefused, agent_engine_settings
from app.harness.unpinned import edition_acp_opt_in
from harness_core.access.types import AccessRouteRef
from harness_core.edition import NO_HARNESS_EDITION, HarnessEdition
from harness_core.engines.errors import ProfileWidening
from harness_core.engines.memory import InMemoryEngineCeilings
from harness_core.engines.ports import ORG_NAMED_ACP_ENGINES, AcpEngineOptIn
from harness_core.engines.profiles import EngineProfileResolver
from harness_core.engines.types import ProfileConstraint, is_acp_kind
from harness_engines.release import release_profiles

ORG = "org-1"
ACCESS = AccessRouteRef("route", "digest")
ACP_KINDS = sorted({p.engine.kind for p in release_profiles(ACCESS) if is_acp_kind(p.engine.kind)})


@pytest.fixture(autouse=True)
def _default_edition(monkeypatch: pytest.MonkeyPatch) -> None:
    """The default ports, whatever edition the process is built as: the checks are of the shared default."""
    import app.edition_services as edition_services

    monkeypatch.setattr(edition_services, "harness_edition", lambda: NO_HARNESS_EDITION)


def test_the_release_ships_the_acp_engines_people_pick() -> None:
    assert {"acp-opencode", "acp-gemini", "acp-goose", "acp-qwen", "acp-dsh"} <= set(ACP_KINDS)


def test_the_default_edition_opts_in_every_released_acp_engine() -> None:
    assert edition_acp_opt_in() == "release"


def test_an_edition_may_leave_the_opt_in_to_the_org(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.edition_services as edition_services

    monkeypatch.setattr(
        edition_services, "harness_edition", lambda: HarnessEdition({AcpEngineOptIn: ORG_NAMED_ACP_ENGINES})
    )
    assert edition_acp_opt_in() == "org"


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ACP_KINDS)
async def test_each_acp_engine_resolves_with_no_ceiling(kind: str) -> None:
    resolver = EngineProfileResolver(release_profiles, InMemoryEngineCeilings(), acp_opt_in=edition_acp_opt_in())
    profile = await resolver.resolve_engine_profile(
        ORG, ProfileConstraint(engines=frozenset({kind})), None, access=ACCESS
    )
    assert profile.engine.kind == kind


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ACP_KINDS)
async def test_an_agent_naming_an_acp_engine_is_saved_with_no_ceiling(kind: str) -> None:
    settings = await agent_engine_settings(
        ORG,
        "agent-1",
        {"engineProfile": {"engines": [kind]}},
        None,
        lambda: EngineProfileResolver(release_profiles, InMemoryEngineCeilings(), acp_opt_in=edition_acp_opt_in()),
        caller_is_admin=True,
    )
    assert settings is not None
    assert settings.engine_profile == {"engines": [kind]}


@pytest.mark.asyncio
async def test_a_ceiling_that_lists_engines_still_leaves_out_an_acp_engine_it_does_not_name() -> None:
    ceilings = InMemoryEngineCeilings()
    ceilings.store(ORG, {"engines": ["opencode"]})
    resolver = EngineProfileResolver(release_profiles, ceilings)
    with pytest.raises(ProfileWidening) as refused:
        await resolver.resolve_engine_profile(
            ORG, ProfileConstraint(engines=frozenset({"acp-gemini"})), None, access=ACCESS
        )
    assert (refused.value.field, refused.value.layer) == ("engine", "agent")


@pytest.mark.asyncio
async def test_where_the_org_opts_in_an_acp_engine_it_never_named_is_refused() -> None:
    with pytest.raises(EngineSettingsRefused) as refused:
        await agent_engine_settings(
            ORG,
            "agent-1",
            {"engineProfile": {"engines": ["acp-gemini"]}},
            None,
            lambda: EngineProfileResolver(release_profiles, InMemoryEngineCeilings(), acp_opt_in="org"),
            caller_is_admin=True,
        )
    assert refused.value.code == "ENGINE_PROFILE_WIDENING"
