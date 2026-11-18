"""Tests for the live limit on a Docker-in-Docker sidecar."""

from __future__ import annotations

import logging
from dataclasses import replace
from typing import TYPE_CHECKING, Any

import pytest

from app.agent_loop_lib.sandbox.capacity import (
    CapacitySettings,
    HoldReason,
    MachineSize,
    SandboxCapacity,
    _docker_mode,
)
from app.agent_loop_lib.sandbox.host_reading import (
    HostSample,
    LiveMode,
    SidecarReader,
    resolve_live_mode,
)
from app.agent_loop_lib.sandbox.live_limit import (
    LiveLimit,
    host_profile,
    initial_record,
    sidecar_profile,
    step,
)
from app.agent_loop_lib.sandbox.live_settings import LiveSettings

from .live_fakes import FakeClock, FakeReader

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

MiB = 2**20
GiB = 2**30
M = 512 * MiB
SIDECAR = sidecar_profile(LiveSettings())
FOUR_GIB = 4 * GiB

SIDECAR_BLOCK: dict[str, Any] = {
    "ageS": 2.0, "seq": 5, "windowS": 15.0, "memLimitBytes": FOUR_GIB, "memWorkingSetBytes": 800 * MiB,
    "memPsiFullAvg10": 0.01, "oomEventsDelta": 0, "cpuLimitMillis": 2000, "cpuUtil": 0.4,
    "cpuThrottledRatio": 0.02, "cpuPsiSomeAvg10": 0.5, "dockerdRssBytes": 90 * MiB,
    "containerdRssBytes": 40 * MiB,
}
CONTAINERS_BLOCK = {"running": 2, "memWorkingSetBytes": 300 * MiB, "cpuCores": 1.0, "partial": False, "truncated": False}


def _proxy(sidecar: Any = SIDECAR_BLOCK, *, error: str | None = None, status: int = 200,  # noqa: ANN401
           containers: Any = CONTAINERS_BLOCK) -> Callable[[str, float], tuple[int, Any]]:  # noqa: ANN401
    calls: list[str] = []

    def http_get(path: str, timeout: float) -> tuple[int, Any]:
        calls.append(path)
        if status != 200:
            return status, {"message": "no"}
        if path.endswith("scope=containers"):
            if containers is None:
                raise ConnectionError("down")
            return 200, {"v": 1, "containers": containers, "containersError": None}
        return 200, {"v": 1, "sidecar": sidecar, "sidecarError": error}

    http_get.calls = calls  # type: ignore[attr-defined]
    return http_get


def _helm_capacity(memory: int | None = FOUR_GIB, cpu_millis: int = 2000, *, cpu_factor: float = 0.75) -> SandboxCapacity:
    settings = CapacitySettings(
        host_memory_bytes=memory, host_cpu_millis=cpu_millis if memory else None, memory_share=0.9,
        cpu_factor=cpu_factor,
    )
    cap = SandboxCapacity("helm_dind", settings, slot_files=256)
    if memory:
        cap._apply(MachineSize(memory, cpu_millis / 1000, None, "helm_env"), None)
    return cap


class TestTopology:
    def test_dind_sidecar_is_helm_dind(self, caplog: pytest.LogCaptureFixture) -> None:
        assert _docker_mode({"SANDBOX_DOCKER_TOPOLOGY": "dind-sidecar"}) == "helm_dind"
        assert _docker_mode({}) == "compose"
        with caplog.at_level(logging.WARNING):
            assert _docker_mode({"SANDBOX_DOCKER_TOPOLOGY": "pod"}) == "compose"
        assert "SANDBOX_DOCKER_TOPOLOGY" in caplog.text

    def test_default_sidecar_ceiling(self) -> None:
        assert _helm_capacity().ceiling() == 3


class TestSidecarReader:
    def test_cgroup_source_maps_the_block(self) -> None:
        sample = SidecarReader(_proxy(), "sidecar", memory_bytes=FOUR_GIB, cpu_millis=2000).read()
        assert (sample.mem_total, sample.mem_available) == (FOUR_GIB, FOUR_GIB - 800 * MiB)
        assert (sample.cpu_util, sample.cpu_throttled_ratio, sample.mem_psi_full_avg10) == (0.4, 0.02, 0.01)
        assert sample.cpu_psi_some_avg10 is None and sample.mem_oom_events == 0 and sample.source == "sidecar"

    def test_containers_source_maps_the_block(self) -> None:
        sample = SidecarReader(_proxy(), "sidecar-containers", memory_bytes=FOUR_GIB, cpu_millis=2000).read()
        total = int(FOUR_GIB * 0.9)
        assert (sample.mem_total, sample.mem_available) == (total, total - 300 * MiB)
        assert sample.cpu_util == pytest.approx(0.5)
        assert sample.cpu_throttled_ratio is None and sample.mem_psi_full_avg10 is None

    @pytest.mark.parametrize(("source", "http_get"), [
        ("sidecar", _proxy(None, error="stale")),
        ("sidecar", _proxy(status=502)),
        ("sidecar-containers", _proxy(containers={**CONTAINERS_BLOCK, "partial": True})),
        ("sidecar-containers", _proxy(containers={**CONTAINERS_BLOCK, "truncated": True})),
        ("sidecar-containers", _proxy(containers=None)),
    ])
    def test_errors_give_an_empty_sample(
        self, source: str, http_get: Callable[[str, float], tuple[int, Any]],
    ) -> None:
        sample = SidecarReader(http_get, source, memory_bytes=FOUR_GIB, cpu_millis=2000).read()  # type: ignore[arg-type]
        assert (sample.mem_total, sample.mem_available, sample.cpu_util) == (None, None, None)


def _resolve(cap: SandboxCapacity, http_get: Callable[[str, float], tuple[int, Any]], **settings: Any) -> LiveMode:  # noqa: ANN401
    return resolve_live_mode(cap, LiveSettings(**settings), http_get=http_get, platform="linux")


class TestResolveSidecarMode:
    def test_matching_limit_reads_the_sidecar_cgroup(self) -> None:
        mode = _resolve(_helm_capacity(), _proxy())
        assert (mode.mode, mode.profile, mode.retry) == ("on:sidecar", "sidecar", False)
        assert isinstance(mode.reader, SidecarReader) and mode.reader.source == "sidecar"

    def test_a_two_percent_mismatch_uses_container_stats(self) -> None:
        mode = _resolve(_helm_capacity(), _proxy({**SIDECAR_BLOCK, "memLimitBytes": int(FOUR_GIB * 1.02)}))
        assert mode.mode == "on:sidecar-containers" and mode.retry
        assert "not the sidecar limit" in mode.reason

    def test_a_missing_sidecar_uses_container_stats(self) -> None:
        assert _resolve(_helm_capacity(), _proxy(None, error="missing")).mode == "on:sidecar-containers"

    def test_nothing_readable_is_off(self) -> None:
        mode = _resolve(_helm_capacity(), _proxy(None, error="missing", containers=None))
        assert mode.mode == "off:no-sidecar-reading" and mode.retry

    def test_an_old_proxy_is_off(self) -> None:
        assert _resolve(_helm_capacity(), _proxy(status=403)).mode == "off:proxy-too-old"

    def test_no_size_is_off(self) -> None:
        assert _resolve(_helm_capacity(None), _proxy()).mode == "off:no-sidecar-size"

    def test_forced_sources(self) -> None:
        assert _resolve(_helm_capacity(), _proxy(None, error="missing"), sidecar_source="cgroup").mode == "off:no-sidecar-reading"
        assert _resolve(_helm_capacity(), _proxy(), sidecar_source="containers").mode == "on:sidecar-containers"

    def test_disabled(self) -> None:
        assert _resolve(_helm_capacity(), _proxy(), enabled=False).mode == "off:disabled"

    def test_the_node_proc_is_never_read(self) -> None:
        get = _proxy()
        _resolve(_helm_capacity(), get)
        assert "/_pipeshub/host" not in get.calls  # type: ignore[attr-defined]


def _side(avail_mib: float, total: int = FOUR_GIB, **kwargs: object) -> HostSample:
    return HostSample(mem_total=total, mem_available=int(avail_mib * MiB), source="sidecar", **kwargs)  # type: ignore[arg-type]


def _step(rec, sample, *, ceiling: int, held: int = 0, now: float = 2000.0, profile=SIDECAR):  # noqa: ANN202
    return step(rec, sample, profile=profile, m=M, ceiling=ceiling, floor=2, held=held, now=now)


class TestSidecarStep:
    def test_lines_on_4_gib(self) -> None:
        hard, soft = SIDECAR.memory_lines(FOUR_GIB, M)
        assert hard == pytest.approx(205 * MiB, abs=MiB) and soft == pytest.approx(410 * MiB, abs=MiB)

    def test_example_a_never_fires_at_the_defaults(self) -> None:
        rec = initial_record(None, _side(3846), profile=SIDECAR, m=M, ceiling=3, floor=2, held=0, now=1000.0, pid=1)
        assert rec.limit == 3
        for avail, held in ((3501, 3), (2265, 3)):
            rec = _step(rec, _side(avail), ceiling=3, held=held, now=rec.last_step_at + 15)
            assert rec.limit == 3

    def test_example_a_explicit_eight(self) -> None:
        rec = initial_record(None, _side(2926), profile=SIDECAR, m=M, ceiling=8, floor=2, held=8, now=1000.0, pid=1)
        assert rec.limit == 8
        rec = _step(rec, _side(684), ceiling=8, held=6, now=1015.0)
        assert (rec.limit, rec.rule) == (7, "mem_target")
        rec = _step(rec, _side(684), ceiling=8, held=6, now=1030.0)
        assert rec.limit == 6

    def test_credit_running_reaches_a_memory_bound_ceiling(self) -> None:
        sample = _side(4096 - 250 - 6 * 115)
        side = initial_record(None, sample, profile=SIDECAR, m=M, ceiling=7, floor=2, held=6, now=1.0, pid=1)
        host = initial_record(None, sample, profile=host_profile(LiveSettings()), m=M, ceiling=7, floor=2, held=6, now=1.0, pid=1)
        assert side.limit == 7 and host.limit < 7

    def test_hard_needs_the_floor_itself(self) -> None:
        rec = initial_record(None, _side(3000), profile=SIDECAR, m=M, ceiling=8, floor=2, held=8, now=1.0, pid=1)
        assert _step(rec, _side(300), ceiling=8, held=8).rule != "mem_hard"
        assert _step(rec, _side(150), ceiling=8, held=8).rule == "mem_hard"

    @pytest.mark.parametrize(("kwargs", "hard"), [
        ({"mem_oom_events": 1}, True), ({"mem_psi_full_avg10": 0.11}, True), ({"mem_psi_full_avg10": 0.09}, False),
    ])
    def test_oom_and_psi(self, kwargs: dict[str, object], hard: bool) -> None:
        rec = initial_record(None, _side(3000), profile=SIDECAR, m=M, ceiling=3, floor=2, held=0, now=1.0, pid=1)
        assert (_step(rec, _side(3000, **kwargs), ceiling=3).rule == "mem_hard") is hard

    def test_cpu_needs_three_samples_and_throttling_alone_fires(self) -> None:
        rec = initial_record(None, _side(3000), profile=SIDECAR, m=M, ceiling=8, floor=2, held=8, now=1.0, pid=1)
        busy = _side(3000, cpu_throttled_ratio=0.5, cpu_util=0.5)
        rules = []
        for i, sample in enumerate([busy, busy, _side(3000), busy, busy, busy]):
            rec = _step(rec, sample, ceiling=8, held=8, now=100.0 + 15 * i)
            rules.append(rec.rule)
        assert rules[-1] == "cpu" and "cpu" not in rules[:-1]
        assert rec.shrink_cause == "cpu"

    @pytest.mark.parametrize("sample", [
        _side(3000, cpu_util=0.90), _side(3000, cpu_throttled_ratio=0.10),
    ])
    def test_the_grow_band(self, sample: HostSample) -> None:
        rec = initial_record(None, _side(3000), profile=SIDECAR, m=M, ceiling=8, floor=2, held=8, now=1.0, pid=1)
        rec = replace(rec, limit=4, last_wait_limit_at=50.0, last_step_at=40.0, cooldown_until=0)
        assert _step(rec, sample, ceiling=8, held=4, now=55.0).rule == "cpu_hold"

    def test_containers_source_never_fires_the_throttle_rule(self) -> None:
        rec = initial_record(None, _side(3000), profile=SIDECAR, m=M, ceiling=8, floor=2, held=8, now=1.0, pid=1)
        for i in range(4):
            rec = _step(rec, _side(3000, cpu_util=0.5), ceiling=8, held=8, now=100.0 + 15 * i)
        assert rec.cpu_high_streak == 0


def _helm_live(tmp_path: Path, clock: FakeClock, reader: FakeReader, *, k: float = 1.0,
               mode: str = "on:sidecar") -> LiveLimit:
    cap = _helm_capacity(cpu_factor=2.0)
    live_mode = LiveMode(mode=mode, reader=reader, profile="sidecar")  # type: ignore[arg-type]
    live = LiveLimit(
        cap, None, settings=LiveSettings(memory_overcommit=k), resolver=lambda: live_mode,
        directory=tmp_path / "state", clock=clock,
    )
    live.apply_mode(live_mode)
    return live


class TestSidecarLiveLimit:
    def test_a_lost_reading_falls_back_to_the_ceiling(self, tmp_path: Path) -> None:
        clock = FakeClock()
        reader = FakeReader(_side(800), source="sidecar")
        live = _helm_live(tmp_path, clock, reader, k=2.0)
        assert live._overcommit == 2.0
        live.sample_once()
        assert live.view().limit < live.view().ceiling
        reader.sample = HostSample(source="sidecar")
        for _ in range(20):
            clock.now += 15
            live.sample_once()
        clock.now += 15
        view = live.view(fresh=True)
        assert view.mode == "off:reading-lost" and view.limit == view.ceiling == live._ceiling(1.0)
        reader.sample = _side(3800)
        live.sample_once()
        clock.now += 1
        view = live.view()
        assert view.mode == "on:sidecar" and view.ceiling == live._ceiling(2.0)

    def test_no_memory_wait_reaches_indexing(self, tmp_path: Path) -> None:
        live = _helm_live(tmp_path, FakeClock(), FakeReader(_side(800), source="sidecar"))
        calls: list[HoldReason] = []
        live.on_memory_wait = lambda reason, waited: calls.append(reason)
        live.sample_once()
        live.waiting(HoldReason.HOST_MEMORY_FLOOR, 10.0)
        live.waiting(HoldReason.LIVE_MEMORY, 10.0)
        assert calls == [] and not live.waits_for_memory(HoldReason.HOST_MEMORY_FLOOR)

    async def test_the_sidecar_floor(self, tmp_path: Path) -> None:
        clock = FakeClock()
        reader = FakeReader(_side(684), source="sidecar")
        live = _helm_live(tmp_path, clock, reader)
        assert await live.admit_and_charge(held=6) is HoldReason.HOST_MEMORY_FLOOR
        reader.sample = _side(1000)
        clock.now += 2.1
        assert await live.admit_and_charge(held=6) is None
        clock.now += 8.0
        # Charged 8 s ago, so it still counts: 1000 - 512 - 512 < 205.
        assert await live.admit_and_charge(held=7) is HoldReason.HOST_MEMORY_FLOOR
        clock.now += 2.1
        assert await live.admit_and_charge(held=7) is None

    def test_the_reading_is_cached_for_two_seconds(self, tmp_path: Path) -> None:
        clock = FakeClock()
        reader = FakeReader(_side(3000), source="sidecar")
        live = _helm_live(tmp_path, clock, reader)
        reader.memory_reads = 0
        for _ in range(3):
            live._admit_locked()
        clock.now += 1.9
        live._admit_locked()
        assert reader.memory_reads == 1
        clock.now += 0.2
        live._admit_locked()
        assert reader.memory_reads == 2

    def test_overcommit_gate(self, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level(logging.WARNING):
            assert _helm_live(tmp_path, FakeClock(), FakeReader(source="sidecar"), k=3.0)._overcommit == 2.0
            assert "capped at 2.0x" in caplog.text
            containers = _helm_live(
                tmp_path, FakeClock(), FakeReader(source="sidecar-containers"), k=2.0, mode="on:sidecar-containers",
            )
            assert containers._overcommit == 1.0 and "forced to 1.0 (on:sidecar-containers)" in caplog.text

    def test_snapshot_has_a_sidecar_block(self, tmp_path: Path) -> None:
        reader = SidecarReader(_proxy(), "sidecar", memory_bytes=FOUR_GIB, cpu_millis=2000)
        cap = _helm_capacity()
        mode = LiveMode(mode="on:sidecar", reader=reader, profile="sidecar")
        live = LiveLimit(cap, None, settings=LiveSettings(), resolver=lambda: mode, directory=tmp_path / "s")
        live.apply_mode(mode)
        live.sample_once()
        block = live.live_block(live.view())
        assert block["source"] == "sidecar" and block["mode"] == "on:sidecar"
        assert block["sidecar"]["memLimitBytes"] == FOUR_GIB
        assert block["sidecar"]["memUsedPct"] == pytest.approx(19.5, abs=0.1)


class TestSidecarSettings:
    def test_defaults(self) -> None:
        s = LiveSettings.from_env({})
        assert (s.sidecar_source, s.sidecar_mem_high, s.sidecar_mem_hard, s.sidecar_cpu_brake, s.sidecar_cpu_throttle) == (
            "auto", 0.90, 0.95, 0.95, 0.25,
        )

    @pytest.mark.parametrize(("env", "field", "value"), [
        ({"SANDBOX_SIDECAR_SOURCE": "containers"}, "sidecar_source", "containers"),
        ({"SANDBOX_SIDECAR_SOURCE": "nope"}, "sidecar_source", "auto"),
        ({"SANDBOX_SIDECAR_MEM_HIGH": "0.2"}, "sidecar_mem_high", 0.5),
        ({"SANDBOX_SIDECAR_MEM_HARD": "1.5"}, "sidecar_mem_hard", 0.99),
        ({"SANDBOX_SIDECAR_CPU_BRAKE": "x"}, "sidecar_cpu_brake", 0.95),
        ({"SANDBOX_SIDECAR_CPU_THROTTLE": "0"}, "sidecar_cpu_throttle", 0.01),
    ])
    def test_bounds(self, env: dict[str, str], field: str, value: object) -> None:
        assert getattr(LiveSettings.from_env(env), field) == value

    def test_hard_not_above_high_gives_both_defaults(self, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level(logging.WARNING):
            s = LiveSettings.from_env({"SANDBOX_SIDECAR_MEM_HIGH": "0.95", "SANDBOX_SIDECAR_MEM_HARD": "0.9"})
        assert (s.sidecar_mem_high, s.sidecar_mem_hard) == (0.90, 0.95)
        assert "SANDBOX_SIDECAR_MEM_HARD" in caplog.text
