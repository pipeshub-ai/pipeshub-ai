"""The Docker proxy's per-stack instance id and its reaper of sandbox
containers that outlive their run (app/sandbox/docker_proxy.py)."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qs, unquote, urlsplit

import pytest

from app.sandbox import docker_proxy
from app.sandbox.docker_proxy import (
    INSTANCE_LABEL,
    MANAGED_LABEL,
    DockerApiPolicy,
    DockerSocketProxy,
    Reaper,
    ReaperSettings,
    resolve_instance,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

NOW = 1_800_000_000.0


class _Daemon:
    """Unix-socket HTTP server answering the calls the reaper and the
    instance probe make, and recording each request."""

    def __init__(self) -> None:
        self.containers: list[dict[str, Any]] = []
        self.inspect: dict[str, dict[str, Any]] = {}
        self.inspect_fails: set[str] = set()
        self.requests: list[tuple[str, str]] = []
        self.list_status = 200

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        head = (await reader.readuntil(b"\r\n\r\n")).decode()
        method, target, _ = head.split("\r\n")[0].split(" ")
        self.requests.append((method, target))
        url = urlsplit(target)
        status, payload = "404 Not Found", {"message": "no such thing"}
        if method == "GET" and url.path == "/containers/json":
            if self.list_status != 200:
                status, payload = f"{self.list_status} Error", {"message": "boom"}
            else:
                wanted = json.loads(parse_qs(url.query)["filters"][0])["label"][0]
                key, value = wanted.split("=", 1)
                status = "200 OK"
                payload = [c for c in self.containers if (c.get("Labels") or {}).get(key) == value]
        elif method == "GET" and url.path.endswith("/json"):
            ident = unquote(url.path.split("/")[2])
            info = self.inspect.get(ident)
            listed = next((c for c in self.containers if c["Id"] == ident), None)
            if info is None and listed is not None and ident not in self.inspect_fails:
                # A listed container that never had its own record started when it was created.
                started = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(listed["Created"]))
                info = {"State": {"StartedAt": started, "FinishedAt": "0001-01-01T00:00:00Z"}}
            if ident in self.inspect_fails:
                status, payload = "500 Server Error", {"message": "inspect failed"}
            elif info is not None:
                status, payload = "200 OK", info
        elif method == "DELETE":
            ident = url.path.split("/")[2]
            before = len(self.containers)
            self.containers = [c for c in self.containers if c["Id"] != ident]
            status, payload = ("204 No Content", None) if len(self.containers) < before else (status, payload)
        body = b"" if payload is None else json.dumps(payload).encode()
        writer.write(f"HTTP/1.1 {status}\r\nContent-Length: {len(body)}\r\n\r\n".encode() + body)
        await writer.drain()
        writer.close()


@pytest.fixture
async def daemon(tmp_path) -> AsyncIterator[tuple[_Daemon, str]]:
    fake = _Daemon()
    sock = str(tmp_path / "d.sock")
    server = await asyncio.start_unix_server(fake.handle, sock)
    yield fake, sock
    server.close()


def _container(ident: str, age_s: float, *, instance: str | None = "stack-a", state: str = "running") -> dict:
    labels = {MANAGED_LABEL: "true"}
    if instance is not None:
        labels[INSTANCE_LABEL] = instance
    return {"Id": ident, "Created": int(NOW - age_s), "State": state, "Labels": labels}


def _reaper(
    sock: str, *, reap_after: float | None = 420, interval: float = 60, reap_unlabeled: bool = False,
) -> Reaper:
    proxy = DockerSocketProxy(DockerApiPolicy(frozenset(), frozenset()), sock, instance="stack-a")
    settings = ReaperSettings(reap_after_s=reap_after, interval_s=interval, reap_unlabeled=reap_unlabeled)
    return Reaper(proxy, settings, clock=lambda: NOW)


class TestReapOnce:
    async def test_removes_only_containers_older_than_reap_after(self, daemon) -> None:
        fake, sock = daemon
        fake.containers = [_container("old", 500), _container("young", 100), _container("dead", 900, state="exited")]

        assert await _reaper(sock).reap_once() == 2

        deletes = [t for m, t in fake.requests if m == "DELETE"]
        assert sorted(deletes) == ["/containers/dead?force=1", "/containers/old?force=1"]
        assert [c["Id"] for c in fake.containers] == ["young"]

    async def test_lists_only_this_instances_containers(self, daemon) -> None:
        fake, sock = daemon
        fake.containers = [_container("mine", 500), _container("theirs", 5000, instance="stack-b")]

        assert await _reaper(sock).reap_once() == 1

        listing = next(t for m, t in fake.requests if m == "GET")
        filters = json.loads(parse_qs(urlsplit(listing).query)["filters"][0])
        assert filters == {"label": [f"{INSTANCE_LABEL}=stack-a"]}
        assert [c["Id"] for c in fake.containers] == ["theirs"]

    async def test_an_upstream_error_skips_the_pass_and_the_next_one_runs(self, daemon, caplog) -> None:
        fake, sock = daemon
        fake.containers = [_container("old", 500)]
        fake.list_status = 500
        reaper = _reaper(sock, interval=10)
        stop = asyncio.Event()
        with caplog.at_level(logging.WARNING):
            task = asyncio.ensure_future(reaper.run(stop))
            await asyncio.sleep(0.05)
            assert "reaper: pass skipped" in caplog.text
            fake.list_status = 200
            assert await reaper.reap_once() == 1
            stop.set()
            await asyncio.wait_for(task, 2)

    async def test_disabled_reaper_never_lists(self, daemon, caplog) -> None:
        fake, sock = daemon
        with caplog.at_level(logging.WARNING):
            await asyncio.wait_for(_reaper(sock, reap_after=None).run(asyncio.Event()), 1)
        assert fake.requests == []
        assert "reaper is off" in caplog.text

    async def test_older_proxy_containers_are_removed_once_past_the_reap_age_when_enabled(self, daemon) -> None:
        # A container started before the upgrade carries no instance label:
        # this proxy will not serve it, so only the reaper can remove it.
        fake, sock = daemon
        fake.containers = [
            _container("legacy-old", 500, instance=None),
            _container("legacy-young", 100, instance=None),
            _container("theirs", 5000, instance="stack-b"),
            {"Id": "unmanaged", "Created": int(NOW - 9000), "State": "running", "Labels": {}},
        ]

        assert await _reaper(sock, reap_unlabeled=True).reap_once() == 1

        assert sorted(c["Id"] for c in fake.containers) == ["legacy-young", "theirs", "unmanaged"]

    async def test_older_proxy_containers_are_left_alone_by_default(self, daemon, caplog) -> None:
        # Another stack on the daemon may still run a proxy from before the
        # label, with runs longer than this stack's reap age.
        fake, sock = daemon
        fake.containers = [_container("legacy-old", 50_000, instance=None), _container("mine", 500)]

        with caplog.at_level(logging.WARNING):
            assert await _reaper(sock).reap_once() == 1

        assert [c["Id"] for c in fake.containers] == ["legacy-old"]
        assert "1 sandbox container(s) carry no instance label" in caplog.text
        assert "DOCKER_PROXY_REAP_UNLABELED=true" in caplog.text

    async def test_the_unlabelled_count_is_logged_at_most_once_an_hour(self, daemon, caplog) -> None:
        fake, sock = daemon
        fake.containers = [_container("legacy", 50_000, instance=None)]
        now = [NOW]
        proxy = DockerSocketProxy(DockerApiPolicy(frozenset(), frozenset()), sock, instance="stack-a")
        reaper = Reaper(proxy, ReaperSettings(reap_after_s=420, interval_s=60), clock=lambda: now[0])

        with caplog.at_level(logging.WARNING):
            for step in (0, 60, 1800, 3599):
                now[0] = NOW + step
                await reaper.reap_once()
            assert caplog.text.count("carry no instance label") == 1
            now[0] = NOW + 3601
            await reaper.reap_once()
        assert caplog.text.count("carry no instance label") == 2
        assert [c["Id"] for c in fake.containers] == ["legacy"]

    async def test_older_proxy_containers_are_reported_at_start(self, daemon, caplog) -> None:
        fake, sock = daemon
        fake.containers = [_container("legacy", 100, instance=None)]
        stop = asyncio.Event()
        with caplog.at_level(logging.INFO):
            task = asyncio.ensure_future(_reaper(sock).run(stop))
            await asyncio.sleep(0.05)
            stop.set()
            await asyncio.wait_for(task, 2)
        assert "1 sandbox container(s) carry no instance label" in caplog.text
        assert [c["Id"] for c in fake.containers] == ["legacy"]

    async def test_cancel_stops_the_loop_cleanly(self, daemon) -> None:
        _fake, sock = daemon
        task = asyncio.ensure_future(_reaper(sock).run(asyncio.Event()))
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


class TestReaperSettings:
    def test_default_follows_the_run_cap(self) -> None:
        assert ReaperSettings.from_env({}).reap_after_s == 420
        assert ReaperSettings.from_env({"SANDBOX_MAX_RUN_TIMEOUT_S": "600"}).reap_after_s == 720
        # Never below the install deadline.
        assert ReaperSettings.from_env({"SANDBOX_MAX_RUN_TIMEOUT_S": "60"}).reap_after_s == 420
        assert ReaperSettings.from_env({"SANDBOX_MAX_RUN_TIMEOUT_S": "abc"}).reap_after_s == 420

    def test_explicit_age_and_zero_disables(self) -> None:
        assert ReaperSettings.from_env({"DOCKER_PROXY_REAP_AFTER_S": "900"}).reap_after_s == 900
        assert ReaperSettings.from_env({"DOCKER_PROXY_REAP_AFTER_S": "0"}).reap_after_s is None

    @pytest.mark.parametrize("raw,run_cap,expected", [
        ("30", "", 420), ("419", "", 420), ("420", "", 420), ("600", "900", 1020), ("1020", "900", 1020),
    ])
    def test_an_age_below_the_longest_run_is_raised_with_a_warning(
        self, raw: str, run_cap: str, expected: float, caplog,
    ) -> None:
        env = {"DOCKER_PROXY_REAP_AFTER_S": raw, "SANDBOX_MAX_RUN_TIMEOUT_S": run_cap}
        with caplog.at_level(logging.WARNING):
            assert ReaperSettings.from_env(env).reap_after_s == expected
        assert ("is below the longest" in caplog.text) == (float(raw) < expected)

    @pytest.mark.parametrize("raw,expected", [
        ("", False), ("false", False), ("0", False), ("off", False), ("maybe", False),
        ("true", True), ("TRUE", True), ("1", True), ("yes", True), (" on ", True),
    ])
    def test_reaping_unlabelled_containers_is_opt_in(self, raw: str, expected: bool) -> None:
        assert ReaperSettings.from_env({"DOCKER_PROXY_REAP_UNLABELED": raw}).reap_unlabeled is expected
        assert ReaperSettings.from_env({}).reap_unlabeled is False

    def test_invalid_age_warns_and_uses_the_default(self, caplog) -> None:
        with caplog.at_level(logging.WARNING):
            assert ReaperSettings.from_env({"DOCKER_PROXY_REAP_AFTER_S": "-5"}).reap_after_s == 420
        assert "DOCKER_PROXY_REAP_AFTER_S" in caplog.text

    def test_interval_default_and_floor(self) -> None:
        assert ReaperSettings.from_env({}).interval_s == 60
        assert ReaperSettings.from_env({"DOCKER_PROXY_REAP_INTERVAL_S": "1"}).interval_s == 10
        assert ReaperSettings.from_env({"DOCKER_PROXY_REAP_INTERVAL_S": "120"}).interval_s == 120


class TestResolveInstance:
    async def test_env_wins(self, daemon) -> None:
        _fake, sock = daemon
        assert await resolve_instance({"DOCKER_PROXY_INSTANCE": "ns/release"}, sock) == "ns/release"

    async def test_invalid_env_refuses_to_start(self, daemon) -> None:
        _fake, sock = daemon
        with pytest.raises(ValueError, match="DOCKER_PROXY_INSTANCE"):
            await resolve_instance({"DOCKER_PROXY_INSTANCE": "bad value!"}, sock)

    async def test_compose_project_from_self_inspect(self, daemon, monkeypatch) -> None:
        fake, sock = daemon
        monkeypatch.setattr(docker_proxy.socket, "gethostname", lambda: "abc123")
        fake.inspect["abc123"] = {"Config": {"Labels": {"com.docker.compose.project": "pipeshub-ai"}}}
        assert await resolve_instance({}, sock) == "compose:pipeshub-ai"

    async def test_no_container_falls_back_to_default_with_a_warning(self, daemon, monkeypatch, caplog) -> None:
        _fake, sock = daemon
        monkeypatch.setattr(docker_proxy.socket, "gethostname", lambda: "not-a-container")
        with caplog.at_level(logging.WARNING):
            assert await resolve_instance({}, sock) == "default"
        assert "set DOCKER_PROXY_INSTANCE" in caplog.text

    async def test_unreachable_daemon_falls_back_to_default(self, tmp_path) -> None:
        assert await resolve_instance({}, str(tmp_path / "missing.sock")) == "default"
