"""Host reading fallbacks: PSI parsing, the default cgroup probe, a sidecar
reading that crashes, the Docker GET helper, and resolving with the proxy
unreachable or the live limit off."""

from __future__ import annotations

import time
from types import SimpleNamespace
from typing import Any

import pytest

from app.agent_loop_lib.sandbox import host_reading as hr
from app.agent_loop_lib.sandbox.capacity import (
    CapacitySettings,
    MachineSize,
    SandboxCapacity,
)
from app.agent_loop_lib.sandbox.host_reading import (
    CgroupReader,
    HostSample,
    SidecarReader,
    _psi_avg10,
    docker_http_get,
    resolve_live_mode,
    resolve_sidecar_mode,
)
from app.agent_loop_lib.sandbox.live_settings import LiveSettings

GiB = 2**30


class TestPsi:
    @pytest.mark.parametrize(("text", "kind", "expected"), [
        ("some avg10=1.50 avg60=0.00 avg300=0.00 total=1\nfull avg10=0.25 avg60=0 avg300=0 total=1", "some", 0.015),
        ("some avg10=1.50 avg60=0.00\nfull avg10=0.25 avg60=0", "full", 0.0025),
        ("some avg60=1.0 avg300=2.0 total=3", "some", None),     # no avg10 field
        ("some avg10=abc avg60=1.0", "some", None),              # not a number
        ("full avg10=3.0", "some", None),                        # no such line
        ("somewhat avg10=3.0", "some", None),                    # prefix only
        ("", "some", None),
        (None, "some", None),
    ])
    def test_avg10(self, text, kind, expected) -> None:
        result = _psi_avg10(text, kind)
        assert result == (pytest.approx(expected) if expected is not None else None)


class TestCgroupReader:
    def test_default_probe_is_the_system_probe(self, monkeypatch) -> None:
        from app.services.resource_governor import probe

        made: list[object] = []

        class _Probe:
            def __init__(self) -> None:
                made.append(self)

            def snapshot(self) -> SimpleNamespace:
                return SimpleNamespace(
                    mem_limit_bytes=4 * GiB, mem_working_set_raw_bytes=1 * GiB,
                    cpu_utilisation=0.5, cpu_pressure=0.1,
                )

        monkeypatch.setattr(probe, "SystemResourceProbe", _Probe)
        reader = CgroupReader()
        assert len(made) == 2  # one for the sampler, one for the per-run check
        assert reader.memory() == (3 * GiB, 4 * GiB)
        sample = reader.read()
        assert (sample.mem_available, sample.mem_total, sample.source) == (3 * GiB, 4 * GiB, "cgroup")


class TestSidecarMemory:
    def test_a_crashing_read_answers_nothing_and_is_tried_again(self) -> None:
        reader = SidecarReader(lambda p, t: (200, {}), "sidecar", memory_bytes=4 * GiB, cpu_millis=2000)
        calls = {"n": 0}

        def read() -> HostSample:
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("bad block")
            return HostSample(mem_total=4 * GiB, mem_available=3 * GiB, source="sidecar")

        reader.read = read  # type: ignore[method-assign]
        assert reader.memory() == (None, None)
        for _ in range(100):  # the background refresh finishes and clears its marker
            if reader._memory_pending is None:
                break
            time.sleep(0.01)
        assert reader._memory_pending is None
        assert reader.memory() == (3 * GiB, 4 * GiB)
        assert calls["n"] == 2


class TestOwnCgroupLimit:
    def test_no_cgroup_file(self, monkeypatch) -> None:
        from app.services.resource_governor import probe

        monkeypatch.setattr(probe, "_resolve_cgroup_path", lambda *a: None)
        assert hr._own_cgroup_memory_limit() is None

    def test_reads_the_resolved_file(self, monkeypatch, tmp_path) -> None:
        from app.services.resource_governor import probe

        path = tmp_path / "memory.max"
        path.write_text("8589934592\n")
        monkeypatch.setattr(probe, "_resolve_cgroup_path", lambda *a: path)
        assert hr._own_cgroup_memory_limit() == 8 * GiB


class TestDockerHttpGet:
    def _provider(self, response: object) -> SimpleNamespace:
        seen: list[tuple[str, float]] = []

        def get(url: str, timeout: float) -> object:
            seen.append((url, timeout))
            return response

        api = SimpleNamespace(get=get, _url=lambda path: "http://proxy" + path)
        return SimpleNamespace(client=SimpleNamespace(api=api), seen=seen)

    def test_json_body(self, monkeypatch) -> None:
        from app.agent_loop_lib.sandbox.coding import docker_client

        provider = self._provider(SimpleNamespace(status_code=200, json=lambda: {"bootIdHash": "x"}))
        monkeypatch.setattr(docker_client, "get_default_provider", lambda: provider)
        assert docker_http_get("/_pipeshub/host", 2.0) == (200, {"bootIdHash": "x"})
        assert provider.seen == [("http://proxy/_pipeshub/host", 2.0)]

    def test_a_body_that_is_not_json_is_none(self, monkeypatch) -> None:
        from app.agent_loop_lib.sandbox.coding import docker_client

        def bad_json() -> object:
            raise ValueError("not json")

        provider = self._provider(SimpleNamespace(status_code=403, json=bad_json))
        monkeypatch.setattr(docker_client, "get_default_provider", lambda: provider)
        assert docker_http_get("/_pipeshub/usage", 1.0) == (403, None)


def _helm(memory: int = 4 * GiB) -> SandboxCapacity:
    cap = SandboxCapacity(
        "helm_dind", CapacitySettings(host_memory_bytes=memory, host_cpu_millis=2000), slot_files=64,
    )
    cap._apply(MachineSize(memory, 2.0, None, "helm_env"), None)
    return cap


class TestResolve:
    def test_off_by_setting(self) -> None:
        def unused(*a: object) -> tuple[int, object]:
            raise AssertionError("the proxy must not be asked")

        settings = LiveSettings(enabled=False)
        mode = resolve_live_mode(SandboxCapacity.fixed(2), settings, http_get=unused)
        assert (mode.mode, mode.reason) == ("off:disabled", "disabled by SANDBOX_LIVE_LIMIT")
        assert resolve_sidecar_mode(_helm(), settings, http_get=unused).mode == "off:disabled"

    def test_sidecar_proxy_unreachable_falls_back_to_container_stats(self) -> None:
        paths: list[str] = []

        def http_get(path: str, timeout: float) -> tuple[int, Any]:
            paths.append(path)
            if path == "/_pipeshub/usage":
                raise ConnectionError("refused")
            return 200, {"containers": {"running": 0, "memWorkingSetBytes": 0, "cpuCores": 0.0}}

        mode = resolve_sidecar_mode(_helm(), LiveSettings(), http_get=http_get)
        assert mode.mode == "on:sidecar-containers" and mode.retry
        assert "(the proxy did not answer)" in mode.reason
        assert paths == ["/_pipeshub/usage", "/_pipeshub/usage?scope=containers"]

    def test_sidecar_http_error_is_named(self) -> None:
        def http_get(path: str, timeout: float) -> tuple[int, Any]:
            return (500, None) if path == "/_pipeshub/usage" else (500, None)

        mode = resolve_sidecar_mode(_helm(), LiveSettings(), http_get=http_get)
        assert mode.mode == "off:no-sidecar-reading" and mode.retry
        assert mode.reason == "no sidecar reading (HTTP 500)"

    def test_sidecar_cgroup_only_with_both_reads_failing(self) -> None:
        def http_get(path: str, timeout: float) -> tuple[int, Any]:
            raise TimeoutError("slow")

        mode = resolve_sidecar_mode(_helm(), LiveSettings(sidecar_source="cgroup"), http_get=http_get)
        assert mode.mode == "off:no-sidecar-reading"
        assert mode.reason == "sidecar cgroup reading unavailable (the proxy did not answer)"

    def test_containers_read_raising_is_no_reading(self) -> None:
        def http_get(path: str, timeout: float) -> tuple[int, Any]:
            if path == "/_pipeshub/usage":
                return 200, {"sidecar": None, "sidecarError": "no cgroup export"}
            raise ConnectionError("down")

        mode = resolve_sidecar_mode(_helm(), LiveSettings(), http_get=http_get)
        assert mode.mode == "off:no-sidecar-reading"
        assert mode.reason == "no sidecar reading (no cgroup export)"
