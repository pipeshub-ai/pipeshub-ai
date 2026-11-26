"""FL-1 (P5.9b): the worker takes sandbox snapshots only with ``HARNESS_ENGINE_SNAPSHOTS`` on."""

from __future__ import annotations

import pytest

from app.harness.engines.worker import controller_config
from app.utils.env_flags import InvalidEnvFlag
from harness_engines.release import release_allowlist, release_snapshot_policy


@pytest.mark.parametrize(("value", "on"), [(None, False), ("", False), ("off", False), ("on", True), ("TRUE", True)])
def test_fl1_the_release_policy_only_with_the_flag_on(value: str | None, on: bool, monkeypatch: pytest.MonkeyPatch) -> None:
    if value is None:
        monkeypatch.delenv("HARNESS_ENGINE_SNAPSHOTS", raising=False)
    else:
        monkeypatch.setenv("HARNESS_ENGINE_SNAPSHOTS", value)
    config = controller_config(release_allowlist())
    assert config.snapshot == (release_snapshot_policy() if on else None)


def test_fl1_a_malformed_value_refuses(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HARNESS_ENGINE_SNAPSHOTS", "maybe")
    with pytest.raises(InvalidEnvFlag):
        _ = controller_config(release_allowlist())
