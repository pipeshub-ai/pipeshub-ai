"""CI's Docker lane must not read green when it could not run: with ``PXH_REQUIRE_DOCKER`` set, a missing daemon or
sandbox image fails the lane's fixtures; unset, they skip as on a developer's machine."""

from __future__ import annotations

import pytest

from tests.unit.agent_loop_lib.sandbox.contract import conftest as lane

MISSING_IMAGE = "pxh-no-such-image:never-built"


def _outcome() -> tuple[type[BaseException], str] | None:
    """What the fixture guard did, caught here: a skip escaping a test would report it skipped, not failed."""
    try:
        lane.skip_without_docker()
    except (pytest.fail.Exception, pytest.skip.Exception) as outcome:
        return type(outcome), str(outcome)
    return None


@pytest.mark.parametrize("value", ["1", "true"])
def test_ci2_1_a_required_lane_without_its_image_fails(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setattr(lane, "DOCKER_TEST_IMAGE", MISSING_IMAGE)
    monkeypatch.setenv(lane.REQUIRE_DOCKER_ENV, value)
    outcome = _outcome()
    assert outcome is not None and outcome[0] is pytest.fail.Exception
    assert lane.REQUIRE_DOCKER_ENV in outcome[1]


@pytest.mark.parametrize("value", [None, "", "0"])
def test_ci2_1_an_optional_lane_without_its_image_skips(monkeypatch: pytest.MonkeyPatch, value: str | None) -> None:
    monkeypatch.setattr(lane, "DOCKER_TEST_IMAGE", MISSING_IMAGE)
    if value is None:
        monkeypatch.delenv(lane.REQUIRE_DOCKER_ENV, raising=False)
    else:
        monkeypatch.setenv(lane.REQUIRE_DOCKER_ENV, value)
    outcome = _outcome()
    assert outcome is not None and outcome[0] is pytest.skip.Exception
