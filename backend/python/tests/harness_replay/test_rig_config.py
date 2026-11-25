"""DUR2-C1..C4: the rigs read configuration the way a deployment does (DUR1 open item 6, P5.11 RC-F4).

The durable driver's mocked config service answered the record fetch's platform-flag read and the demo-data read
with ``object MagicMock can't be used in 'await' expression``; both fell back to their defaults, logged as errors,
so a rig run looked like a run on a deployment whose settings are unreadable.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import pytest

from app.services.featureflag.platform_settings import (
    PLATFORM_SETTINGS_KEY,
    is_knowledge_publications_enabled,
)
from tests.contracts.golden_support import (
    _graph_provider,  # pyright: ignore[reportPrivateUsage]
)
from tests.harness_replay import durable_driver
from tests.support.config_service import (
    RIG_PLATFORM_FLAGS,
    FakeConfigurationService,
    platform_flag_names,
)
from tests.support.parity_cases.harness_cases import load_cases as harness_cases
from tests.unit.harness.assembly import world

if TYPE_CHECKING:
    from collections.abc import Iterator

CONFIG_READ_ERRORS = ("unreadable", "Failed to read", "can't be used in 'await'")


@pytest.fixture
def fakes(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[FakeConfigurationService]]:
    """Every fake the rigs build during the test."""
    built: list[FakeConfigurationService] = []
    original = FakeConfigurationService.__init__

    def recording_init(self: FakeConfigurationService, *args: object, **kwargs: object) -> None:
        original(self, *args, **kwargs)  # type: ignore[arg-type]
        built.append(self)

    monkeypatch.setattr(FakeConfigurationService, "__init__", recording_init)
    yield built


def test_dur2_c1_every_platform_flag_is_declared() -> None:
    assert set(RIG_PLATFORM_FLAGS) == platform_flag_names()


async def test_dur2_c3_an_undeclared_key_is_the_callers_default_and_recorded() -> None:
    config = FakeConfigurationService()
    assert await config.get_config("/services/nothing", default={"d": 1}) == {"d": 1}
    assert config.misses == ["/services/nothing"]
    settings = await config.get_config(PLATFORM_SETTINGS_KEY, default={}, use_cache=False)
    assert settings["featureFlags"] == RIG_PLATFORM_FLAGS
    assert config.misses == ["/services/nothing"]


async def test_dur2_c4_the_rigs_graph_carries_the_config_service() -> None:
    durable = world.Services()
    assert durable.graph.config_service is durable.config
    assert await durable.graph.get_org_apps("org-1", active_only=False) == []
    legacy = _graph_provider()
    assert isinstance(legacy.config_service, FakeConfigurationService)
    assert await is_knowledge_publications_enabled(legacy.config_service) is False
    assert await legacy.get_org_apps("org-1", active_only=False) == []


def _config_errors(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [
        f"{r.name}: {r.getMessage()[:160]}"
        for r in caplog.records
        if r.levelno >= logging.WARNING and any(marker in r.getMessage() for marker in CONFIG_READ_ERRORS)
    ]


@pytest.mark.parametrize("case_id", ["quick-01-kb-lookup", "quick-04-create-ticket-requested"])
async def test_dur2_c2_harness_cases_on_the_durable_driver_read_config_cleanly(
    case_id: str, caplog: pytest.LogCaptureFixture, fakes: list[FakeConfigurationService]
) -> None:
    case = next(c for c in harness_cases() if c.id == case_id)
    with caplog.at_level(logging.WARNING):
        result = await durable_driver.drive(case.drive_spec())
    assert result.recording.final.status in ("completed", "needs_input")
    assert _config_errors(caplog) == []
    assert fakes, "the driver builds its services with the fake"
    assert [miss for fake in fakes for miss in fake.misses] == []


@pytest.mark.parametrize("case_id", ["od-01-incident-memo", "da-02-top-region", "rp-03-globex-status"])
async def test_dur2_c2_wave1_cases_on_the_durable_loop_read_config_cleanly(
    case_id: str, caplog: pytest.LogCaptureFixture, fakes: list[FakeConfigurationService]
) -> None:
    from tests.support.parity_cases.wave1_cases import select
    from tests.support.parity_cases.wave1_durable import DurableLoopSystem
    from tests.support.parity_cases.wave1_outputs import assert_produced
    from tests.support.parity_cases.wave1_reference import script_for
    from tests.support.parity_cases.wave1_systems import task_for

    (case,) = select(ids=[case_id])
    with caplog.at_level(logging.WARNING):
        run = await DurableLoopSystem(sandbox="host").run(task_for(case, 0, script_for(case)))
    assert (run.status, run.error) == ("completed", None)
    assert_produced(case_id, run)
    assert _config_errors(caplog) == []
    assert [miss for fake in fakes for miss in fake.misses] == []
