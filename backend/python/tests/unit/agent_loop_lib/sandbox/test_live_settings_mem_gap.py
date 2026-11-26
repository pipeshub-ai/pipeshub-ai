"""A memory threshold gap of exactly 0.01 is valid at any level, as the chart
accepts it; a smaller one still falls back to the defaults."""

from __future__ import annotations

import pytest

from app.agent_loop_lib.sandbox.live_settings import (
    DEFAULT_SIDECAR_MEM_HARD,
    DEFAULT_SIDECAR_MEM_HIGH,
    ENV_SIDECAR_MEM_HARD,
    ENV_SIDECAR_MEM_HIGH,
    LiveSettings,
)


@pytest.mark.parametrize(("high", "hard"), [("0.93", "0.94"), ("0.70", "0.71"), ("0.57", "0.58"), ("0.94", "0.95")])
def test_an_exact_gap_of_one_hundredth_is_kept(high: str, hard: str) -> None:
    settings = LiveSettings.from_env({ENV_SIDECAR_MEM_HIGH: high, ENV_SIDECAR_MEM_HARD: hard})
    assert (settings.sidecar_mem_high, settings.sidecar_mem_hard) == (float(high), float(hard))


def test_a_smaller_gap_uses_the_defaults() -> None:
    settings = LiveSettings.from_env({ENV_SIDECAR_MEM_HIGH: "0.70", ENV_SIDECAR_MEM_HARD: "0.70995"})
    assert (settings.sidecar_mem_high, settings.sidecar_mem_hard) == (DEFAULT_SIDECAR_MEM_HIGH, DEFAULT_SIDECAR_MEM_HARD)
