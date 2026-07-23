"""Tests for app.services.parsing.concurrency:

- classify_format: heavy vs. light vs. unknown format routing
- compute_parse_slots: auto-sizing from cpu/memory and the numeric override
- memory_pressure_high: admission-guard threshold behaviour
"""
from __future__ import annotations

import pytest

from app.services.parsing.concurrency import (
    HEAVY_FORMATS,
    LIGHT_FORMATS,
    ParseTier,
    classify_format,
    compute_parse_slots,
    memory_pressure_high,
)


class TestClassifyFormat:
    @pytest.mark.parametrize("ext", sorted(HEAVY_FORMATS))
    def test_known_heavy_extensions(self, ext: str) -> None:
        assert classify_format(ext, "") is ParseTier.HEAVY

    @pytest.mark.parametrize("ext", sorted(LIGHT_FORMATS))
    def test_known_light_extensions(self, ext: str) -> None:
        assert classify_format(ext, "") is ParseTier.LIGHT

    def test_extension_case_and_dot_insensitive(self) -> None:
        assert classify_format(".PDF", "") is ParseTier.HEAVY
        assert classify_format("MD", "") is ParseTier.LIGHT

    def test_falls_back_to_mime_when_extension_unknown(self) -> None:
        assert classify_format("", "application/pdf") is ParseTier.HEAVY
        assert classify_format("", "image/png") is ParseTier.HEAVY
        assert classify_format("", "text/plain") is ParseTier.LIGHT
        assert classify_format("", "application/json") is ParseTier.LIGHT

    def test_unknown_format_defaults_to_heavy(self) -> None:
        assert classify_format("xyz", "application/x-unknown") is ParseTier.HEAVY
        assert classify_format("", "") is ParseTier.HEAVY

    def test_extension_takes_priority_over_mime(self) -> None:
        # A light extension with a generic/unknown mime type should still
        # resolve to LIGHT via the extension map.
        assert classify_format("csv", "application/octet-stream") is ParseTier.LIGHT


class TestComputeParseSlots:
    def test_numeric_override_pins_heavy_and_scales_light(self) -> None:
        heavy, light = compute_parse_slots(cpu_count=8, mem_limit_bytes=None, override="3")
        assert heavy == 3
        assert light == 12  # 3 * LIGHT_TO_HEAVY_RATIO(4)

    def test_numeric_override_light_slots_capped(self) -> None:
        heavy, light = compute_parse_slots(cpu_count=8, mem_limit_bytes=None, override="10")
        assert heavy == 10
        assert light == 16  # clamped at MAX_LIGHT_SLOTS

    def test_invalid_override_falls_back_to_auto(self) -> None:
        auto_heavy, auto_light = compute_parse_slots(cpu_count=4, mem_limit_bytes=None, override=None)
        heavy, light = compute_parse_slots(cpu_count=4, mem_limit_bytes=None, override="not-a-number")
        assert (heavy, light) == (auto_heavy, auto_light)

    def test_auto_none_and_auto_string_are_equivalent(self) -> None:
        assert compute_parse_slots(cpu_count=4, mem_limit_bytes=None, override=None) == (
            compute_parse_slots(cpu_count=4, mem_limit_bytes=None, override="auto")
        )
        assert compute_parse_slots(cpu_count=4, mem_limit_bytes=None, override=None) == (
            compute_parse_slots(cpu_count=4, mem_limit_bytes=None, override="AUTO")
        )

    def test_auto_sizing_small_container(self) -> None:
        # 2 cpu, 4 GiB: heavy = min(2, 4/1.5=2) = 2, light = clamp(2*2, 4, 16) = 4
        heavy, light = compute_parse_slots(
            cpu_count=2, mem_limit_bytes=4 * 1024**3, override=None
        )
        assert heavy == 2
        assert light == 4

    def test_auto_sizing_large_container(self) -> None:
        # 8 cpu, 16 GiB: heavy = min(8, 16/1.5=10) -> clamp to MAX_HEAVY_SLOTS(4)
        heavy, light = compute_parse_slots(
            cpu_count=8, mem_limit_bytes=16 * 1024**3, override=None
        )
        assert heavy == 4
        assert light == 16  # clamp(8*2, 4, 16)

    def test_auto_sizing_memory_constrained(self) -> None:
        # 8 cpu but only 1 GiB memory: heavy_by_memory = 0 -> clamped to MIN_HEAVY_SLOTS(1)
        heavy, light = compute_parse_slots(
            cpu_count=8, mem_limit_bytes=1 * 1024**3, override=None
        )
        assert heavy == 1
        assert light == 16

    def test_auto_sizing_no_memory_signal_uses_cpu_only(self) -> None:
        heavy, light = compute_parse_slots(cpu_count=3, mem_limit_bytes=None, override=None)
        assert heavy == 3
        assert light == 6

    def test_heavy_slots_never_below_one(self) -> None:
        heavy, _light = compute_parse_slots(cpu_count=1, mem_limit_bytes=1, override=None)
        assert heavy >= 1


class TestMemoryPressureHigh:
    def test_returns_false_when_limit_unavailable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "app.services.parsing.concurrency.get_memory_limit_bytes", lambda: None
        )
        monkeypatch.setattr(
            "app.services.parsing.concurrency.get_memory_usage_bytes", lambda: 100
        )
        assert memory_pressure_high() is False

    def test_returns_false_when_usage_unavailable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "app.services.parsing.concurrency.get_memory_limit_bytes", lambda: 100
        )
        monkeypatch.setattr(
            "app.services.parsing.concurrency.get_memory_usage_bytes", lambda: None
        )
        assert memory_pressure_high() is False

    def test_returns_true_above_threshold(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "app.services.parsing.concurrency.get_memory_limit_bytes", lambda: 1000
        )
        monkeypatch.setattr(
            "app.services.parsing.concurrency.get_memory_usage_bytes", lambda: 950
        )
        assert memory_pressure_high(threshold=0.9) is True

    def test_returns_false_below_threshold(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "app.services.parsing.concurrency.get_memory_limit_bytes", lambda: 1000
        )
        monkeypatch.setattr(
            "app.services.parsing.concurrency.get_memory_usage_bytes", lambda: 500
        )
        assert memory_pressure_high(threshold=0.9) is False
