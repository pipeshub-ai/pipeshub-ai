"""Error and fallback paths of the docker proxy that the main suites leave
unexercised: create-body shape checks, sidecar parsing edges, the
containers usage block when the daemon misbehaves, the boot id route, the
reaper's failure paths, instance resolution and ``serve`` start/stop."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from collections.abc import Awaitable, Callable
from typing import Any

import pytest

from app.sandbox import docker_proxy as dp
from app.sandbox.docker_proxy import (
    DEFAULT_INSTANCE,
    INSTANCE_LABEL,
    MANAGED_LABEL,
    DockerApiPolicy,
    DockerSocketProxy,
    PolicyDenied,
    Reaper,
    ReaperSettings,
    SidecarStats,
    boot_id_hash,
    parse_sidecar_stats,
    resolve_instance,
)
from tests.support.settle import settle
from tests.unit.sandbox.test_docker_proxy import TOKEN, with_token

IMAGE = "pipeshubai/pipeshub-sandbox:latest"
EGRESS = "pipeshub_sandbox_egress"
Responses = Callable[[str, str], Awaitable[tuple[int, Any]]]
_LIMITS: dict[str, int] = {"Memory": 512 * 1024 * 1024, "NanoCpus": 500_000_000, "PidsLimit": 256}
# The rest of the sandbox floor (SEC-A-04) a raw create body needs, beside a non-root ``User``.
_FLOOR: dict[str, Any] = {"CapDrop": ["ALL"], "SecurityOpt": ["no-new-privileges:true"], "ReadonlyRootfs": True}


@pytest.fixture
def policy() -> DockerApiPolicy:
    return DockerApiPolicy(allowed_images=frozenset({IMAGE}), allowed_networks=frozenset({EGRESS}))


# --- create body ------------------------------------------------------------


class TestEndpointsConfigShape:
    @pytest.mark.parametrize("endpoints", [["x"], "x", 5])
    def test_endpoints_config_must_be_an_object(self, policy: DockerApiPolicy, endpoints: object) -> None:
        body = {
            "Image": IMAGE, "User": "sandbox", "HostConfig": {"NetworkMode": EGRESS, **_LIMITS, **_FLOOR},
            "NetworkingConfig": {"EndpointsConfig": endpoints},
        }
        with pytest.raises(PolicyDenied, match="EndpointsConfig must be an object"):
            policy.check_container_create(body)

    def test_an_empty_endpoints_config_is_admitted(self, policy: DockerApiPolicy) -> None:
        body = {
            "Image": IMAGE, "User": "sandbox", "HostConfig": {"NetworkMode": EGRESS, **_LIMITS, **_FLOOR},
            "NetworkingConfig": {"EndpointsConfig": None},
        }
        assert policy.check_container_create(body)["Labels"][MANAGED_LABEL] == "true"


# --- boot id ------------------------------------------------------------------


class TestBootIdHash:
    def test_missing_file_is_none(self, tmp_path) -> None:
        assert boot_id_hash(str(tmp_path / "nope")) is None

    def test_empty_file_is_none(self, tmp_path) -> None:
        path = tmp_path / "boot_id"
        path.write_text("\n")
        assert boot_id_hash(str(path)) is None

    def test_hash_is_32_hex_and_stable(self, tmp_path) -> None:
        path = tmp_path / "boot_id"
        path.write_text("abc\n")
        digest = boot_id_hash(str(path))
        assert digest is not None and len(digest) == 32
        assert digest == boot_id_hash(str(path))
        assert "abc" not in digest


# --- sidecar parsing ----------------------------------------------------------


class TestSidecarParsing:
    @pytest.mark.parametrize("value", ["nan", "inf", "-inf", "NaN", "Infinity"])
    def test_non_finite_values_are_refused(self, value: str) -> None:
        with pytest.raises(ValueError):
            parse_sidecar_stats(f"memory.current {value}\n")

    @pytest.mark.parametrize("line", ["cpu.max 100000", "cpu.max", "cpu.max 1 2 3"])
    def test_cpu_max_needs_two_fields(self, line: str) -> None:
        with pytest.raises(ValueError):
            parse_sidecar_stats(line)

    def test_unlimited_values_and_unknown_keys(self) -> None:
        values = parse_sidecar_stats("memory.max max\ncpu.max max 100000\nother.key nonsense\nseq 3\n")
        assert values == {"memory.max": None, "cpu.max.quota": None, "cpu.max.period": 100000, "seq": 3}

    def test_a_float_value_is_kept(self) -> None:
        assert parse_sidecar_stats("memory.pressure.full.avg10 1.5")["memory.pressure.full.avg10"] == 1.5


def _write(path, **values: object) -> None:
    lines = [f"{k.replace('_', '.')} {v}" for k, v in values.items()]
    path.write_text("\n".join(lines) + "\n")


class TestSidecarRing:
    def test_same_seq_is_not_stored_twice(self, tmp_path) -> None:
        path = tmp_path / "stats"
        _write(path, seq=1)
        stats = SidecarStats(str(path))
        stats.read()
        stats.read()
        assert len(stats._ring) == 1

    def test_rates_fall_back_to_the_oldest_snapshot_inside_the_window(self) -> None:
        stats = SidecarStats("/unused")
        base = {"cpu.max.quota": 100000.0, "cpu.max.period": 100000.0}
        stats._block(100.0, 0.0, {**base, "seq": 1, "cpu.stat.usage_usec": 0, "cpu.stat.nr_periods": 0,
                                  "cpu.stat.nr_throttled": 0})
        block = stats._block(102.0, 0.0, {**base, "seq": 2, "cpu.stat.usage_usec": 1_000_000,
                                          "cpu.stat.nr_periods": 0, "cpu.stat.nr_throttled": 0})
        # Two seconds apart, below the 10 s window: the oldest snapshot is used.
        assert block["windowS"] == 2.0
        assert block["cpuUtil"] == pytest.approx(0.5)
        # No periods elapsed: throttled is 0, not a division error.
        assert block["cpuThrottledRatio"] == 0.0

    def test_no_cpu_limit_leaves_util_unknown(self) -> None:
        stats = SidecarStats("/unused")
        stats._block(100.0, 0.0, {"seq": 1, "cpu.stat.usage_usec": 0})
        block = stats._block(120.0, 0.0, {"seq": 2, "cpu.stat.usage_usec": 5})
        assert block["cpuUtil"] is None and block["cpuLimitMillis"] is None
        assert block["cpuThrottledRatio"] is None and block["oomEventsDelta"] is None

    def test_a_stale_file_is_reported(self, tmp_path) -> None:
        path = tmp_path / "stats"
        _write(path, seq=1)
        old = time.time() - 60
        os.utime(path, (old, old))
        assert SidecarStats(str(path)).read() == (None, "stale")

    def test_non_ascii_is_unparsable(self, tmp_path) -> None:
        path = tmp_path / "stats"
        path.write_bytes(b"seq \xff\n")
        assert SidecarStats(str(path)).read() == (None, "unparsable")

    def test_a_directory_is_unparsable(self, tmp_path) -> None:
        assert SidecarStats(str(tmp_path)).read() == (None, "unparsable")


# --- containers usage -----------------------------------------------------------


def _proxy_with(responses: Responses, policy: DockerApiPolicy) -> DockerSocketProxy:
    proxy = DockerSocketProxy(policy, "/nonexistent.sock", caller_token=TOKEN, instance="stack-a")

    async def call(method: str, path: str, timeout: float = 30.0) -> tuple[int, Any]:
        return await responses(method, path)

    proxy._upstream_call = call  # type: ignore[method-assign]
    return proxy


class TestContainersUsage:
    async def test_a_list_error_is_list_failed(self, policy: DockerApiPolicy) -> None:
        async def responses(method: str, path: str) -> tuple[int, Any]:
            raise OSError("daemon gone")

        assert await _proxy_with(responses, policy)._containers_usage(1.0) == (None, "list_failed")

    @pytest.mark.parametrize("answer", [(500, []), (200, {"not": "a list"}), (200, None)])
    async def test_a_bad_list_is_list_failed(self, policy: DockerApiPolicy, answer: tuple[int, Any]) -> None:
        async def responses(method: str, path: str) -> tuple[int, Any]:
            return answer

        assert await _proxy_with(responses, policy)._containers_usage(1.0) == (None, "list_failed")

    async def test_bad_stats_mark_the_block_partial(self, policy: DockerApiPolicy) -> None:
        async def responses(method: str, path: str) -> tuple[int, Any]:
            if path.startswith("/containers/json"):
                return 200, [{"Id": "a"}, {"Id": "b"}, {"Id": "c"}, "junk", {"Id": 5}]
            if "/a/" in path:
                return 500, None
            if "/b/" in path:
                raise OSError("boom")
            return 200, {"memory_stats": {"usage": 100, "stats": {"file": 30, "shmem": 10}},
                         "cpu_stats": {"cpu_usage": {"total_usage": "not a number"}}}

        block, error = await _proxy_with(responses, policy)._containers_usage(1.0)
        assert error is None
        assert block == {"running": 3, "memWorkingSetBytes": 80, "cpuCores": None,
                         "partial": True, "truncated": False}

    async def test_stats_slower_than_the_budget_are_cancelled(
        self, policy: DockerApiPolicy, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(dp, "_STATS_BUDGET_S", 0.05)
        cancelled = asyncio.Event()

        async def responses(method: str, path: str) -> tuple[int, Any]:
            if path.startswith("/containers/json"):
                return 200, [{"Id": "slow"}]
            try:
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                cancelled.set()
                raise
            return 200, {}

        block, _ = await _proxy_with(responses, policy)._containers_usage(1.0)
        assert block is not None and block["partial"] is True and block["running"] == 1
        await settle(cancelled.wait())

    async def test_cpu_cores_need_two_readings(self, policy: DockerApiPolicy) -> None:
        usage = {"n": 0}

        async def responses(method: str, path: str) -> tuple[int, Any]:
            if path.startswith("/containers/json"):
                return 200, [{"Id": "a"}]
            usage["n"] += 2_000_000_000
            return 200, {"cpu_stats": {"cpu_usage": {"total_usage": usage["n"]}}}

        proxy = _proxy_with(responses, policy)
        first, _ = await proxy._containers_usage(10.0)
        second, _ = await proxy._containers_usage(12.0)
        assert first is not None and first["cpuCores"] is None
        assert second is not None and second["cpuCores"] == pytest.approx(1.0)


# --- boot id route ----------------------------------------------------------------


async def test_host_route_without_a_boot_id_is_a_502(
    policy: DockerApiPolicy, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(dp, "boot_id_hash", lambda path=None: None)
    proxy = DockerSocketProxy(policy, "/nonexistent.sock", caller_token=TOKEN)
    server = await asyncio.start_server(proxy.handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        writer.write(with_token(b"GET /_pipeshub/host HTTP/1.1\r\nHost: x\r\n\r\n"))
        await writer.drain()
        resp = await reader.read()
        writer.close()
    finally:
        server.close()
    assert resp.startswith(b"HTTP/1.1 502")
    assert b"docker-proxy: boot id unavailable" in resp


# --- chunked archive upload --------------------------------------------------------


class _ChunkDaemon:
    def __init__(self) -> None:
        self.raw = b""

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        head = await reader.readuntil(b"\r\n\r\n")
        path = head.split(b" ")[1].decode()
        if path.endswith("/json"):
            payload = json.dumps({"Id": "f" * 64, "Config": {"Labels": {
                MANAGED_LABEL: "true", INSTANCE_LABEL: "stack-a"}}}).encode()
        else:
            body = b""
            while not (body.endswith(b"\r\n\r\n") and b"\r\n0\r\n" in b"\r\n" + body):
                chunk = await reader.read(1024)
                if not chunk:
                    break
                body += chunk
            self.raw = head + body
            payload = b"{}"
        writer.write(f"HTTP/1.1 200 OK\r\nContent-Length: {len(payload)}\r\n\r\n".encode() + payload)
        await writer.drain()
        writer.close()


async def _through_proxy(tmp_path, policy: DockerApiPolicy, raw: bytes) -> tuple[_ChunkDaemon, bytes]:
    daemon = _ChunkDaemon()
    sock = str(tmp_path / "d.sock")
    upstream = await asyncio.start_unix_server(daemon.handle, sock)
    proxy = DockerSocketProxy(policy, sock, caller_token=TOKEN, instance="stack-a")
    server = await asyncio.start_server(proxy.handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        writer.write(with_token(raw))
        await writer.drain()
        resp = await asyncio.wait_for(reader.read(), 5)
        writer.close()
    finally:
        server.close()
        upstream.close()
    return daemon, resp


class TestChunkedArchiveUpload:
    async def test_a_chunked_archive_is_forwarded_verbatim(self, tmp_path, policy: DockerApiPolicy) -> None:
        body = b"5\r\nhello\r\n3\r\nabc\r\n0\r\n\r\n"
        daemon, resp = await _through_proxy(
            tmp_path, policy,
            b"PUT /containers/owned/archive?path=/src HTTP/1.1\r\nHost: x\r\n"
            b"Transfer-Encoding: chunked\r\n\r\n" + body,
        )
        assert resp.startswith(b"HTTP/1.1 200")
        assert daemon.raw.endswith(body)
        assert f"/containers/{'f' * 64}/archive".encode() in daemon.raw

    async def test_a_malformed_chunk_size_is_refused(self, tmp_path, policy: DockerApiPolicy) -> None:
        daemon, resp = await _through_proxy(
            tmp_path, policy,
            b"PUT /containers/owned/archive?path=/src HTTP/1.1\r\nHost: x\r\n"
            b"Transfer-Encoding: chunked\r\n\r\n0x5\r\nhello\r\n0\r\n\r\n",
        )
        # The head already went upstream, but the forged size never does.
        assert b"0x5" not in daemon.raw
        assert b"hello" not in daemon.raw
        assert not resp.startswith(b"HTTP/1.1 200")


# --- reaper -----------------------------------------------------------------------


def _reaper(responses: Responses, policy: DockerApiPolicy, *, unlabeled: bool = False,
            after: float | None = 100.0, now: float = 1000.0) -> Reaper:
    proxy = _proxy_with(responses, policy)
    return Reaper(proxy, ReaperSettings(reap_after_s=after, interval_s=10, reap_unlabeled=unlabeled),
                  clock=lambda: now)


def _owned(ident: str, created: float, instance: str = "stack-a") -> dict[str, Any]:
    return {"Id": ident, "Created": created, "State": "running",
            "Labels": {MANAGED_LABEL: "true", INSTANCE_LABEL: instance}}


# The reaper inspects an old container for its start; these started when created (time 0).
_STARTED_AT_CREATION = {"State": {"StartedAt": "1970-01-01T00:00:00Z", "FinishedAt": "0001-01-01T00:00:00Z"}}


def _is_inspect(path: str) -> bool:
    return path.split("?")[0].count("/") == 3 and path.split("?")[0].endswith("/json")


class TestReaper:
    async def test_off_reaps_nothing(self, policy: DockerApiPolicy) -> None:
        async def responses(method: str, path: str) -> tuple[int, Any]:
            raise AssertionError("no call expected")

        assert await _reaper(responses, policy, after=None).reap_once() == 0

    async def test_a_failed_managed_listing_raises(self, policy: DockerApiPolicy) -> None:
        async def responses(method: str, path: str) -> tuple[int, Any]:
            if "instance" in path:
                return 200, []
            return 500, None

        with pytest.raises(RuntimeError, match="listing containers returned 500"):
            await _reaper(responses, policy).reap_once()

    async def test_delete_results(self, policy: DockerApiPolicy, caplog: pytest.LogCaptureFixture) -> None:
        listed = [_owned("gone", 0), _owned("ok", 0), _owned("err", 0), _owned("young", 990),
                  _owned("noid", 0) | {"Id": ""}, _owned("nocreated", 0) | {"Created": "x"},
                  "junk", _owned("other", 0, "stack-b")]
        deletes: list[str] = []

        async def responses(method: str, path: str) -> tuple[int, Any]:
            if method == "GET" and _is_inspect(path):
                return 200, _STARTED_AT_CREATION
            if method == "GET":
                return 200, listed if "instance" in path else []
            deletes.append(path)
            return {"gone": 404, "ok": 204, "err": 500}[path.split("/")[2].split("?")[0]], None

        with caplog.at_level(logging.WARNING, logger=dp.logger.name):
            removed = await _reaper(responses, policy).reap_once()
        assert removed == 1
        assert sorted(p.split("/")[2].split("?")[0] for p in deletes) == ["err", "gone", "ok"]
        assert "reaper: removing err returned 500" in caplog.text

    async def test_unlabelled_are_reaped_only_when_asked(self, policy: DockerApiPolicy) -> None:
        legacy = {"Id": "legacy", "Created": 0, "Labels": {MANAGED_LABEL: "true"}}
        deletes: list[str] = []

        async def responses(method: str, path: str) -> tuple[int, Any]:
            if method == "GET" and _is_inspect(path):
                return 200, _STARTED_AT_CREATION
            if method == "GET":
                return 200, [] if "instance" in path else [legacy, "junk"]
            deletes.append(path)
            return 204, None

        assert await _reaper(responses, policy).reap_once() == 0
        assert await _reaper(responses, policy, unlabeled=True).reap_once() == 1
        assert deletes == ["/containers/legacy?force=1"]

    async def test_legacy_report_survives_a_failed_listing(self, policy: DockerApiPolicy) -> None:
        async def responses(method: str, path: str) -> tuple[int, Any]:
            raise OSError("no daemon")

        await _reaper(responses, policy)._report_legacy()  # does not raise

    async def test_legacy_report_with_reaping_on_logs_info(
        self, policy: DockerApiPolicy, caplog: pytest.LogCaptureFixture,
    ) -> None:
        async def responses(method: str, path: str) -> tuple[int, Any]:
            return 200, [{"Id": "l", "Labels": {MANAGED_LABEL: "true"}}]

        with caplog.at_level(logging.INFO, logger=dp.logger.name):
            await _reaper(responses, policy, unlabeled=True)._report_legacy()
        assert "1 sandbox container(s) from an older proxy; removed once older than 100s" in caplog.text

    async def test_a_failed_pass_does_not_stop_the_loop(self, policy: DockerApiPolicy) -> None:
        calls = {"n": 0}
        stop = asyncio.Event()

        async def responses(method: str, path: str) -> tuple[int, Any]:
            calls["n"] += 1
            if calls["n"] >= 4:
                stop.set()
            raise OSError("daemon gone")

        reaper = _reaper(responses, policy)
        reaper._settings = ReaperSettings(reap_after_s=100.0, interval_s=0.0)
        await asyncio.wait_for(reaper.run(stop), 5)
        assert calls["n"] >= 4


# --- instance ----------------------------------------------------------------------


class TestResolveInstance:
    async def test_invalid_env_value_stops_the_proxy(self) -> None:
        with pytest.raises(ValueError, match="DOCKER_PROXY_INSTANCE must be"):
            await resolve_instance({"DOCKER_PROXY_INSTANCE": "bad value!"}, "/x.sock")

    @pytest.mark.parametrize(("answer", "expected"), [
        ((200, {"Config": {"Labels": {"com.docker.compose.project": "proj"}}}), "compose:proj"),
        # A project name that cannot be a label value falls back to the default.
        ((200, {"Config": {"Labels": {"com.docker.compose.project": "a b"}}}), DEFAULT_INSTANCE),
        ((200, {"Config": {"Labels": ["not", "a", "dict"]}}), DEFAULT_INSTANCE),
        ((404, {"Config": {"Labels": {"com.docker.compose.project": "proj"}}}), DEFAULT_INSTANCE),
        (OSError("gone"), DEFAULT_INSTANCE),
    ])
    async def test_compose_project_lookup(
        self, monkeypatch: pytest.MonkeyPatch, answer: object, expected: str,
    ) -> None:
        async def call(self: object, method: str, path: str, timeout: float = 30.0) -> tuple[int, Any]:
            if isinstance(answer, Exception):
                raise answer
            return answer  # type: ignore[return-value]

        monkeypatch.setattr(DockerSocketProxy, "_upstream_call", call)
        assert await resolve_instance({}, "/x.sock") == expected


# --- serve ---------------------------------------------------------------------------


async def test_serve_starts_and_stops_on_a_signal(
    policy: DockerApiPolicy, monkeypatch: pytest.MonkeyPatch,
) -> None:
    loop = asyncio.get_running_loop()
    handlers: dict[int, Any] = {}
    monkeypatch.setattr(loop, "add_signal_handler", lambda sig, cb: handlers.__setitem__(sig, cb))
    monkeypatch.setattr(dp, "resolve_instance", lambda env, upstream: _resolved())
    reaper_ran = asyncio.Event()

    async def fake_run(self: Reaper, stop: asyncio.Event) -> None:
        reaper_ran.set()
        await asyncio.sleep(3600)

    monkeypatch.setattr(Reaper, "run", fake_run)
    task = asyncio.create_task(dp.serve("127.0.0.1", 0, "/x.sock", {TOKEN: policy},
                                        reaper=ReaperSettings(reap_after_s=None, interval_s=60)))
    await asyncio.wait_for(reaper_ran.wait(), 5)
    assert len(handlers) == 2
    next(iter(handlers.values()))()
    await asyncio.wait_for(task, 5)


async def _resolved() -> str:
    return "stack-x"
