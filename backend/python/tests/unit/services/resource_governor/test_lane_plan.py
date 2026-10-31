"""The pure lane plan (no consumer yet)."""
from __future__ import annotations

import inspect
from typing import TYPE_CHECKING

import pytest

from app.services.resource_governor.lanes import LanePlan, lanes_health, plan_lanes

if TYPE_CHECKING:
    from collections.abc import Callable


def _legacy_width(heavy: int) -> int:
    return max(1, min(4, heavy // 2))


@pytest.mark.parametrize("heavy", range(1, 10))
def test_default_width_matches_legacy_up_to_heavy_9(heavy: int) -> None:
    assert plan_lanes(cpus=64, heavy=heavy).lanes == _legacy_width(heavy)


@pytest.mark.parametrize(("heavy", "lanes"), [(10, 5), (14, 7), (62, 31), (127, 63), (128, 64), (500, 64)])
def test_lifts_the_cap_of_four(heavy: int, lanes: int) -> None:
    assert plan_lanes(cpus=64, heavy=heavy).lanes == lanes


@pytest.mark.parametrize("cpus", [1, 2, 3, 4, 7, 8, 16, 64])
@pytest.mark.parametrize("heavy", [1, 2, 3, 4, 6, 14, 62])
def test_threads_times_lanes_within_cpus(cpus: int, heavy: int) -> None:
    plan = plan_lanes(cpus=cpus, heavy=heavy)
    assert 1 <= plan.threads_per_lane <= 4
    # The floor of one thread per lane can exceed cpus only when lanes > cpus.
    assert plan.lanes * plan.threads_per_lane <= max(cpus, plan.lanes)


@pytest.mark.parametrize(
    ("cpus", "heavy", "lanes", "threads"),
    [(1, 1, 1, 1), (2, 1, 1, 2), (4, 3, 1, 4), (4, 4, 2, 2), (8, 6, 3, 2), (16, 14, 7, 2), (64, 62, 31, 2)],
)
def test_placeholder_threads_rule_rows(cpus: int, heavy: int, lanes: int, threads: int) -> None:
    plan = plan_lanes(cpus=cpus, heavy=heavy)
    assert (plan.lanes, plan.threads_per_lane) == (lanes, threads)


def test_explicit_threads_win() -> None:
    plan = plan_lanes(cpus=8, heavy=6, explicit_threads=1)
    assert plan.threads_per_lane == 1
    assert plan.source == "explicit_threads"
    assert plan.lanes == 3


def test_explicit_pool_sizes_do_not_enter_the_plan() -> None:
    """PARSE_POOL_WORKERS and friends are private pools: the plan has no input for them."""
    params = set(inspect.signature(plan_lanes).parameters)
    assert params == {"cpus", "heavy", "cpus_per_lane", "max_lanes", "explicit_threads"}


def test_plan_is_a_value() -> None:
    assert plan_lanes(cpus=8, heavy=6) == LanePlan(8, 6, 3, 2, 2, "rule")


def test_lanes_never_exceed_cpus_even_with_a_large_heavy() -> None:
    """An explicit parse cap can lift heavy above cpus; lanes x threads <= cpus must still hold."""
    plan = plan_lanes(cpus=1, heavy=9)
    assert (plan.lanes, plan.threads_per_lane) == (1, 1)
    assert plan_lanes(cpus=3, heavy=40).lanes == 3


def test_health_plan_reflects_explicit_thread_env(monkeypatch: pytest.MonkeyPatch) -> None:
    from types import SimpleNamespace

    governor = SimpleNamespace(fleet_ceilings=SimpleNamespace(heavy=6))
    monkeypatch.setenv("PDF_TABLE_MODEL_THREADS", "1")
    plan = lanes_health(governor, 8)["plan"]  # type: ignore[arg-type]
    assert (plan["threads_per_lane"], plan["source"]) == (1, "explicit_threads")
    monkeypatch.setenv("PDF_TABLE_MODEL_THREADS", "junk")
    assert lanes_health(governor, 8)["plan"]["source"] == "rule"  # type: ignore[arg-type]


def test_health_block_opens_nothing(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Mode off: no open call of any kind, not even one that is closed again."""
    import builtins
    import io
    import os
    from types import SimpleNamespace

    calls: list[str] = []

    def tripwire(name: str, real: Callable[..., object]) -> Callable[..., object]:
        def wrapper(*args: object, **kwargs: object) -> object:
            calls.append(name)
            return real(*args, **kwargs)
        return wrapper

    governor = SimpleNamespace(fleet_ceilings=SimpleNamespace(heavy=6))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(builtins, "open", tripwire("builtins.open", builtins.open))
    monkeypatch.setattr(io, "open", tripwire("io.open", io.open))
    monkeypatch.setattr(os, "open", tripwire("os.open", os.open))
    block = lanes_health(governor, 8)  # type: ignore[arg-type]
    assert block["mode"] == "off"
    assert calls == []
    assert list(tmp_path.iterdir()) == []
