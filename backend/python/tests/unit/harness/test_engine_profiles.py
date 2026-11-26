"""EP-13 (E4a): org ceilings from ``/services/agentHarness/engineProfiles`` through ``ConfigurationService``."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import pytest

from app.config.constants.service import config_node_constants
from app.harness.unpinned import (
    ConfiguredEngineCeilings,
    pipeshub_engine_profiles,
)
from harness_core.access.types import AccessRouteRef
from harness_core.engines.errors import ProfileInvalid, ProfileWidening
from harness_core.engines.profiles import constraint_from_wire
from harness_core.engines.types import EngineMode
from harness_core.testing.contracts.engine_profiles import (
    CeilingEnv,
    EngineCeilingSourceContract,
)
from harness_engines.release import release_profiles

if TYPE_CHECKING:
    from pydantic import JsonValue

PATH = config_node_constants.AGENT_HARNESS_ENGINE_PROFILES.value
ACCESS = AccessRouteRef("route-1", "d1")


@dataclass
class _FakeConfigurationService:
    """``get_config`` as the harness reads it: no cache, and a store failure raises when asked to."""

    values: dict[str, JsonValue] = field(default_factory=dict[str, "JsonValue"])
    broken: bool = False
    calls: list[tuple[str, bool, bool]] = field(default_factory=list[tuple[str, bool, bool]])

    async def get_config(
        self, key: str, default: object = None, use_cache: bool = False, *, raise_on_error: bool = False
    ) -> JsonValue:
        self.calls.append((key, use_cache, raise_on_error))
        if self.broken:
            if raise_on_error:
                raise ConnectionError("kv store down")
            return None
        return self.values.get(key)


def _document(config: _FakeConfigurationService) -> dict[str, JsonValue]:
    document = config.values.setdefault(PATH, {"orgs": {}})
    assert isinstance(document, dict)
    return document


@dataclass
class _Env:
    config: _FakeConfigurationService

    @property
    def source(self) -> ConfiguredEngineCeilings:
        return ConfiguredEngineCeilings(self.config)  # type: ignore[arg-type]

    async def store(self, org_id: str, ceiling: JsonValue) -> None:
        orgs = _document(self.config)["orgs"]
        assert isinstance(orgs, dict)
        if ceiling is None:
            orgs.pop(org_id, None)
        else:
            orgs[org_id] = {"ceiling": ceiling, "updatedAt": 1, "updatedBy": "p_admin"}

    async def store_raw(self, org_id: str, raw: JsonValue) -> None:
        await self.store(org_id, raw)

    def break_reads(self) -> None:
        self.config.broken = True


class TestConfiguredEngineCeilingsContract(EngineCeilingSourceContract):
    @pytest.fixture
    def ceiling_env(self) -> CeilingEnv:
        return _Env(_FakeConfigurationService())


async def test_ep13_reads_are_uncached_and_raise_on_store_failure() -> None:
    config = _FakeConfigurationService()
    assert await ConfiguredEngineCeilings(config).ceiling("org-1") is None  # type: ignore[arg-type]
    assert config.calls == [(PATH, False, True)]


@pytest.mark.parametrize("document", [["orgs"], {"orgs": ["org-1"]}, {"orgs": {"org-1": {"updatedAt": 1}}}])
async def test_ep13_a_document_of_the_wrong_shape_is_invalid(document: JsonValue) -> None:
    config = _FakeConfigurationService({PATH: document})
    with pytest.raises(ProfileInvalid):
        await ConfiguredEngineCeilings(config).ceiling("org-1")  # type: ignore[arg-type]


async def test_ep13_the_pipeshub_resolver_starts_from_this_release() -> None:
    config = _FakeConfigurationService()
    resolver = pipeshub_engine_profiles(config)  # type: ignore[arg-type]
    assert await resolver.resolve_engine_profile("org-1", None, None, access=ACCESS) == release_profiles(ACCESS)[0]
    await _Env(config).store("org-1", {"mode": "review"})
    profile = await resolver.resolve_engine_profile("org-1", None, None, access=ACCESS)
    assert profile.mode is EngineMode.REVIEW
    with pytest.raises(ProfileWidening) as refused:
        await resolver.resolve_engine_profile("org-1", constraint_from_wire({"mode": "plan"}), None, access=ACCESS)
    assert (refused.value.field, refused.value.layer) == ("mode", "agent")
