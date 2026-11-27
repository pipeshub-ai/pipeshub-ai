"""The Docker proxy keeps stacks sharing one daemon apart and bounds what a
client can send: network list / inspect show only this stack's sandbox
network and containers, networks are created with this stack's label and
only those are removed, pulls name a tag, and bodies and connections are
capped (app/sandbox/docker_proxy.py)."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock, patch

import pytest

from app.sandbox import docker_proxy
from app.sandbox.docker_proxy import (
    INSTANCE_LABEL,
    MANAGED_LABEL,
    DockerApiPolicy,
    DockerSocketProxy,
    PolicyDenied,
    ProxyLimits,
    _json_depth_ok,
)
from tests.unit.sandbox.test_docker_proxy import TOKEN, with_token

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

IMAGE = "pipeshubai/pipeshub-sandbox:latest"
EGRESS = "pipeshub_sandbox_egress"
OWN = "a" * 64
OTHER = "b" * 64
ICC = "com.docker.network.bridge.enable_icc"


def _labels(instance: str) -> dict[str, str]:
    return {MANAGED_LABEL: "true", INSTANCE_LABEL: instance}


@pytest.fixture
def policy() -> DockerApiPolicy:
    return DockerApiPolicy(allowed_images=frozenset({IMAGE}), allowed_networks=frozenset({EGRESS}))


class _Daemon:
    """A daemon with this stack's egress network, another stack's container
    attached to it, and an unrelated network. It ignores list filters, as a
    Docker-compatible API might."""

    def __init__(self, *, egress_labels: dict[str, str] | None = None) -> None:
        self.requests: list[tuple[str, str]] = []
        self.bodies: dict[str, bytes] = {}
        self.containers = {
            OWN: {"Id": OWN, "Config": {"Labels": _labels("stack-a")}, "Labels": _labels("stack-a")},
            OTHER: {"Id": OTHER, "Config": {"Labels": _labels("stack-b")}, "Labels": _labels("stack-b")},
        }
        attached = {
            OWN: {"Name": "own-run", "IPv4Address": "172.30.0.2/16"},
            OTHER: {"Name": "other-stack-run", "IPv4Address": "172.30.0.3/16"},
        }
        self.networks = {
            "egressid": {
                "Id": "egressid", "Name": EGRESS, "Options": {ICC: "false"},
                "IPAM": {"Config": [{"Subnet": "172.30.0.0/16"}]},
                "Labels": egress_labels if egress_labels is not None else _labels("stack-a"),
                "Containers": attached, "Peers": [{"Name": "node", "IP": "10.0.0.9"}], "Services": {"s": {}},
            },
            "othernet": {"Id": "othernet", "Name": "stack-b_default", "Labels": {}, "Containers": {}},
        }

    def answer(self, method: str, parts: list[str]) -> tuple[str, object] | None:
        """A subclass's own ``(status, payload)`` for a request, or None."""
        return None

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        head = (await reader.readuntil(b"\r\n\r\n")).decode()
        method, target, _ = head.split("\r\n")[0].split(" ")
        self.requests.append((method, target))
        lowered = head.lower()
        body = b""
        if "transfer-encoding: chunked" in lowered:
            while True:
                size = int((await reader.readuntil(b"\r\n")).strip(), 16)
                if size == 0:
                    await reader.readuntil(b"\r\n")
                    break
                body += await reader.readexactly(size)
                await reader.readexactly(2)
        else:
            for line in head.split("\r\n")[1:]:
                if line.lower().startswith("content-length:"):
                    try:
                        body = await reader.readexactly(int(line.split(":", 1)[1]))
                    except asyncio.IncompleteReadError:
                        writer.close()
                        return
        self.bodies[f"{method} {target}"] = body
        path = target.split("?", 1)[0]
        parts = path.strip("/").split("/")
        if parts and parts[0].startswith("v1."):
            parts = parts[1:]
        status, payload = "200 OK", {"ok": True}
        answer = self.answer(method, parts)
        if answer is not None:
            status, payload = answer
        elif parts == ["networks"]:
            payload = list(self.networks.values())
        elif parts[:1] == ["networks"] and len(parts) == 2 and method == "GET":
            net = self.networks.get(parts[1]) or next(
                (n for n in self.networks.values() if n["Name"] == parts[1]), None,
            )
            status, payload = ("200 OK", net) if net else ("404 Not Found", {"message": "not found"})
        elif parts == ["containers", "json"]:
            payload = [{"Id": c["Id"], "Labels": c["Labels"]} for c in self.containers.values()]
        elif parts[:1] == ["containers"] and parts[-1] == "json":
            info = self.containers.get(parts[1])
            status, payload = ("200 OK", info) if info else ("404 Not Found", {"message": "No such container"})
        data = json.dumps(payload).encode()
        writer.write(f"HTTP/1.1 {status}\r\nContent-Length: {len(data)}\r\n\r\n".encode() + data)
        await writer.drain()
        writer.close()


async def _start(
    tmp_path, policy: DockerApiPolicy, daemon: _Daemon, limits: ProxyLimits | None = None,
) -> tuple[asyncio.Server, asyncio.Server, int]:
    sock = str(tmp_path / "d.sock")
    upstream = await asyncio.start_unix_server(daemon.handle, sock)
    proxy = DockerSocketProxy(policy, sock, caller_token=TOKEN, instance="stack-a", limits=limits)
    server = await asyncio.start_server(proxy.handle, "127.0.0.1", 0)
    return upstream, server, server.sockets[0].getsockname()[1]


@pytest.fixture
async def proxied(tmp_path, policy: DockerApiPolicy) -> AsyncIterator[tuple[_Daemon, int]]:
    daemon = _Daemon()
    upstream, server, port = await _start(tmp_path, policy, daemon)
    yield daemon, port
    server.close()
    upstream.close()


async def _request(port: int, raw: bytes, timeout: float = 10) -> bytes:
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(with_token(raw))
    await writer.drain()
    data = await asyncio.wait_for(reader.read(), timeout)
    writer.close()
    return data


def _json(resp: bytes) -> object:
    return json.loads(resp.split(b"\r\n\r\n", 1)[1])


class TestNetworkReadsShowOnlyThisStack:
    async def test_list_has_only_the_sandbox_network(self, proxied) -> None:
        daemon, port = proxied
        resp = await _request(port, b"GET /v1.43/networks?filters=%7B%7D HTTP/1.1\r\nHost: x\r\n\r\n")
        assert resp.startswith(b"HTTP/1.1 200")
        listed = _json(resp)
        assert [n["Name"] for n in listed] == [EGRESS]
        assert set(listed[0]["Containers"]) == {OWN}
        assert "Peers" not in listed[0] and "Services" not in listed[0]
        # The client's filters still reach the daemon.
        assert ("GET", "/v1.43/networks?filters=%7B%7D") in daemon.requests

    @pytest.mark.parametrize("ident", ["egressid", EGRESS])
    async def test_inspect_hides_other_stacks_containers(self, proxied, ident: str) -> None:
        daemon, port = proxied
        resp = await _request(port, f"GET /v1.43/networks/{ident}?verbose=false HTTP/1.1\r\nHost: x\r\n\r\n".encode())
        assert resp.startswith(b"HTTP/1.1 200")
        info = _json(resp)
        assert info["Name"] == EGRESS
        assert info["Containers"] == {OWN: {"Name": "own-run", "IPv4Address": "172.30.0.2/16"}}
        assert OTHER.encode() not in resp and b"other-stack-run" not in resp
        assert "Peers" not in info and "Services" not in info
        # The fields the egress firewall reads are kept.
        assert info["Options"] == {ICC: "false"} and info["IPAM"]["Config"][0]["Subnet"] == "172.30.0.0/16"
        assert ("GET", "/v1.43/networks/egressid?verbose=false") in daemon.requests

    async def test_other_networks_cannot_be_inspected(self, proxied) -> None:
        _, port = proxied
        resp = await _request(port, b"GET /networks/othernet HTTP/1.1\r\nHost: x\r\n\r\n")
        assert resp.startswith(b"HTTP/1.1 403")

    async def test_another_stacks_container_gets_the_same_refusal_as_a_missing_one(self, proxied) -> None:
        _, port = proxied
        other = await _request(port, f"GET /containers/{OTHER}/json HTTP/1.1\r\nHost: x\r\n\r\n".encode())
        missing = await _request(port, b"GET /containers/nope/json HTTP/1.1\r\nHost: x\r\n\r\n")
        assert other.startswith(b"HTTP/1.1 403")
        assert _json(other) == _json(missing)

    def test_create_may_not_name_the_container(self, policy: DockerApiPolicy) -> None:
        with pytest.raises(PolicyDenied, match="name"):
            policy.route("POST", "/v1.43/containers/create?name=other-stack-run")
        assert policy.route("POST", "/v1.43/containers/create?platform=linux%2Famd64").kind == "container_create"


class TestNetworkOwnership:
    def _body(self, **extra: object) -> dict[str, Any]:
        return {"Name": EGRESS, "Driver": "bridge", "Options": {ICC: "false"}, "CheckDuplicate": True,
                "Internal": False, "EnableIPv6": False, "Labels": {"pipeshub.sandbox": "egress"}, **extra}

    def test_create_is_labelled_with_this_stack(self, policy: DockerApiPolicy) -> None:
        body = policy.check_network_create(self._body(Labels={INSTANCE_LABEL: "stack-b"}), "stack-a")
        assert body["Labels"] == _labels("stack-a")

    @pytest.mark.parametrize("extra", [
        {"Options": {}}, {"Options": {ICC: "true"}}, {"Options": {ICC: False}}, {"Options": None},
        {"Attachable": True}, {"Unknown": 1}, {"Labels": ["x"]}, {"Options": ["x"]},
        {"Name": "pipeshub_sandbox_egress2"},
    ])
    def test_create_refuses_anything_else(self, policy: DockerApiPolicy, extra: dict[str, Any]) -> None:
        with pytest.raises(PolicyDenied):
            policy.check_network_create(self._body(**extra), "stack-a")

    async def test_create_reaches_the_daemon_with_the_label(self, proxied) -> None:
        daemon, port = proxied
        body = json.dumps(self._body()).encode()
        resp = await _request(port, b"POST /v1.43/networks/create HTTP/1.1\r\nHost: x\r\nContent-Length: "
                              + str(len(body)).encode() + b"\r\n\r\n" + body)
        assert resp.startswith(b"HTTP/1.1 200")
        sent = json.loads(daemon.bodies["POST /v1.43/networks/create"])
        assert sent["Labels"][INSTANCE_LABEL] == "stack-a"

    async def test_own_network_is_removed_by_its_id(self, proxied) -> None:
        daemon, port = proxied
        resp = await _request(port, f"DELETE /v1.43/networks/{EGRESS} HTTP/1.1\r\nHost: x\r\n\r\n".encode())
        assert resp.startswith(b"HTTP/1.1 200")
        assert daemon.requests[-1] == ("DELETE", "/v1.43/networks/egressid")

    @pytest.mark.parametrize("labels", [{}, _labels("stack-b"), {MANAGED_LABEL: "true"}])
    async def test_a_network_this_stack_did_not_create_is_kept(self, tmp_path, policy, labels) -> None:
        daemon = _Daemon(egress_labels=labels)
        upstream, server, port = await _start(tmp_path, policy, daemon)
        try:
            resp = await _request(port, f"DELETE /networks/{EGRESS} HTTP/1.1\r\nHost: x\r\n\r\n".encode())
        finally:
            server.close()
            upstream.close()
        assert resp.startswith(b"HTTP/1.1 403")
        assert b"not created by this sandbox proxy" in resp
        assert [m for m, _ in daemon.requests] == ["GET"]


class TestImagePullNamesATag:
    @pytest.mark.parametrize("target", [
        "/images/create?fromImage=pipeshubai%2Fpipeshub-sandbox",
        "/images/create?fromImage=docker.io%2Fpipeshubai%2Fpipeshub-sandbox&tag=",
        "/images/create?fromImage=localhost%3A5000%2Fpipeshubai%2Fpipeshub-sandbox",
    ])
    def test_a_bare_repository_is_refused(self, policy: DockerApiPolicy, target: str) -> None:
        with pytest.raises(PolicyDenied):
            policy.route("POST", target)

    @pytest.mark.parametrize("target", [
        "/images/create?fromImage=pipeshubai%2Fpipeshub-sandbox&tag=latest",
        "/images/create?fromImage=pipeshubai%2Fpipeshub-sandbox%3Alatest",
    ])
    def test_an_explicit_tag_is_admitted(self, policy: DockerApiPolicy, target: str) -> None:
        assert policy.route("POST", target).kind == "allow"

    def test_an_allowed_digest_is_admitted(self) -> None:
        ref = "pipeshubai/pipeshub-sandbox@sha256:" + "1" * 64
        p = DockerApiPolicy(allowed_images=frozenset({ref}), allowed_networks=frozenset({EGRESS}))
        assert p.route("POST", "/images/create?fromImage=pipeshubai%2Fpipeshub-sandbox%40sha256%3A"
                       + "1" * 64).kind == "allow"

    def test_docker_py_pull_names_the_tag(self, policy: DockerApiPolicy) -> None:
        docker = pytest.importorskip("docker")
        client = docker.APIClient(base_url="tcp://127.0.0.1:9", version="1.43")
        seen: dict[str, Any] = {}

        def fake_post(url: str, params: dict[str, Any], **_: object) -> MagicMock:
            seen["params"] = params
            return MagicMock()

        try:
            with patch.object(client, "_post", side_effect=fake_post), patch.object(client, "_raise_for_status"):
                client.pull("pipeshubai/pipeshub-sandbox")
        finally:
            client.close()
        query = "&".join(f"{k}={v}" for k, v in seen["params"].items() if v)
        assert policy.route("POST", "/images/create?" + query.replace("/", "%2F")).kind == "allow"


class TestHostileJson:
    def test_depth_counts_brackets_outside_strings_only(self) -> None:
        assert _json_depth_ok(b'{"a": "[[[[[[[[[["}', limit=2)
        assert _json_depth_ok(b'{"a": "\\"[[[["}', limit=2)
        assert not _json_depth_ok(b'{"a": [[1]]}', limit=2)
        assert _json_depth_ok(b'{"a": [1], "b": {"c": 2}}', limit=2)

    @pytest.mark.parametrize("path", ["/containers/create", "/networks/create"])
    async def test_deep_nesting_is_a_clean_403(self, proxied, caplog, path: str) -> None:
        daemon, port = proxied
        body = b'{"Image": ' + b"[" * 200_000 + b"]" * 200_000 + b"}"
        caplog.set_level(logging.WARNING, logger="docker_proxy")
        resp = await _request(port, f"POST {path} HTTP/1.1\r\nHost: x\r\nContent-Length: {len(body)}\r\n\r\n".encode()
                              + body)
        assert resp.startswith(b"HTTP/1.1 403")
        assert b"nests too deeply" in resp
        assert daemon.requests == []
        assert not [r for r in caplog.records if r.levelno >= logging.ERROR or r.exc_info]


class TestBodyLimits:
    def test_env(self, monkeypatch) -> None:
        limits = ProxyLimits.from_env({})
        assert limits.max_upload_bytes == 10 * 1024 ** 3
        assert (limits.body_idle_timeout_s, limits.body_timeout_s, limits.max_connections_per_client) == (120, 1800, 512)
        limits = ProxyLimits.from_env({
            "DOCKER_PROXY_MAX_UPLOAD_MB": "5", "DOCKER_PROXY_BODY_IDLE_TIMEOUT_S": "7",
            "DOCKER_PROXY_BODY_TIMEOUT_S": "9", "DOCKER_PROXY_MAX_CONNECTIONS_PER_CLIENT": "3",
        })
        assert limits == ProxyLimits(5 * 1024 * 1024, 7.0, 9.0, 3)
        assert ProxyLimits.from_env({"DOCKER_PROXY_MAX_UPLOAD_MB": "-1"}).max_upload_bytes == 10 * 1024 ** 3

    async def _proxy(
        self, tmp_path, policy, **limits: float,
    ) -> tuple[_Daemon, asyncio.Server, asyncio.Server, int]:
        daemon = _Daemon()
        upstream, server, port = await _start(tmp_path, policy, daemon, ProxyLimits(**limits))
        return daemon, upstream, server, port

    async def test_upload_within_the_cap_is_forwarded(self, tmp_path, policy) -> None:
        daemon, upstream, server, port = await self._proxy(tmp_path, policy, max_upload_bytes=1000)
        try:
            resp = await _request(port, f"PUT /containers/{OWN}/archive?path=/src HTTP/1.1\r\nHost: x\r\n"
                                  "Content-Length: 1000\r\n\r\n".encode() + b"x" * 1000)
        finally:
            server.close()
            upstream.close()
        assert resp.startswith(b"HTTP/1.1 200")
        assert daemon.bodies[f"PUT /containers/{OWN}/archive?path=/src"] == b"x" * 1000

    async def test_declared_upload_over_the_cap_never_reaches_the_daemon(self, tmp_path, policy) -> None:
        daemon, upstream, server, port = await self._proxy(tmp_path, policy, max_upload_bytes=1000)
        try:
            resp = await _request(port, f"PUT /containers/{OWN}/archive?path=/src HTTP/1.1\r\nHost: x\r\n"
                                  "Content-Length: 1001\r\n\r\n".encode() + b"x" * 1001)
        finally:
            server.close()
            upstream.close()
        assert resp.startswith(b"HTTP/1.1 403")
        assert b"DOCKER_PROXY_MAX_UPLOAD_MB" in resp
        assert daemon.requests == []

    async def test_chunked_upload_over_the_cap_is_cut_off(self, tmp_path, policy) -> None:
        daemon, upstream, server, port = await self._proxy(tmp_path, policy, max_upload_bytes=1000)
        chunk = b"258\r\n" + b"x" * 600 + b"\r\n"
        try:
            resp = await _request(port, f"PUT /containers/{OWN}/archive?path=/src HTTP/1.1\r\nHost: x\r\n"
                                  "Transfer-Encoding: chunked\r\n\r\n".encode() + chunk * 3 + b"0\r\n\r\n")
        finally:
            server.close()
            upstream.close()
        assert resp.startswith(b"HTTP/1.1 403")
        assert b"too large" in resp

    @pytest.mark.parametrize("limits", [
        {"body_idle_timeout_s": 0.2},
        {"body_idle_timeout_s": 5, "body_timeout_s": 0.4},
    ])
    async def test_a_stalled_upload_is_dropped(self, tmp_path, policy, limits) -> None:
        daemon, upstream, server, port = await self._proxy(tmp_path, policy, **limits)
        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            writer.write(with_token(f"PUT /containers/{OWN}/archive?path=/src HTTP/1.1\r\nHost: x\r\n"
                                    "Content-Length: 100000\r\n\r\n".encode()))
            for _ in range(20):  # a trickle that never finishes
                try:
                    writer.write(b"x")
                    await writer.drain()
                except ConnectionError:
                    break
                if limits["body_idle_timeout_s"] < 1:
                    break
                await asyncio.sleep(0.1)
            try:
                data = await asyncio.wait_for(reader.read(), 5)
            except ConnectionError:
                data = b""
            writer.close()
        finally:
            server.close()
            upstream.close()
        # Closed with no answer, well before the trickle would have ended.
        assert data == b""

    @pytest.mark.parametrize("action", ["start", "wait", "kill"])
    async def test_a_body_on_another_container_action_is_refused(self, proxied, action: str) -> None:
        daemon, port = proxied
        resp = await _request(port, f"POST /containers/{OWN}/{action} HTTP/1.1\r\nHost: x\r\n"
                              "Content-Length: 2\r\n\r\n{}".encode())
        assert resp.startswith(b"HTTP/1.1 403")
        assert daemon.requests == []

    async def test_connections_per_client_are_capped(self, tmp_path, policy) -> None:
        daemon, upstream, server, port = await self._proxy(tmp_path, policy, max_connections_per_client=2)
        held = [await asyncio.open_connection("127.0.0.1", port) for _ in range(2)]
        try:
            await asyncio.sleep(0.05)
            refused = await _request(port, b"")
            assert refused.startswith(b"HTTP/1.1 503")
            # A held connection finishing frees its place.
            reader, writer = held.pop()
            writer.write(b"GET /_ping HTTP/1.1\r\nHost: x\r\n\r\n")
            await writer.drain()
            assert (await asyncio.wait_for(reader.read(), 5)).startswith(b"HTTP/1.1 200")
            writer.close()
            await asyncio.sleep(0.05)
            assert (await _request(port, b"GET /_ping HTTP/1.1\r\nHost: x\r\n\r\n")).startswith(b"HTTP/1.1 200")
        finally:
            for _, writer in held:
                writer.close()
            server.close()
            upstream.close()


def test_listens_on_loopback_unless_told_otherwise(monkeypatch) -> None:
    monkeypatch.delenv("DOCKER_PROXY_HOST", raising=False)
    with patch.object(docker_proxy, "serve") as serve, \
         patch.object(docker_proxy, "proxy_callers", return_value={}), \
         patch.object(docker_proxy, "anonymous_caller", return_value=None), \
         patch.object(docker_proxy.asyncio, "run", side_effect=lambda coro: coro.close()), \
         patch.object(docker_proxy.logging, "basicConfig"):
        docker_proxy.main()
        monkeypatch.setenv("DOCKER_PROXY_HOST", "0.0.0.0")
        docker_proxy.main()
    assert [c.args[0] for c in serve.call_args_list] == ["127.0.0.1", "0.0.0.0"]
