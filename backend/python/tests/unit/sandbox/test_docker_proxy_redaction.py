"""What the Docker proxy passes through and what it refuses, beyond create
policy basics: /info keeps the storage-driver facts the disk watchdog needs,
image and network inspect hide other stacks and host paths, endpoint
settings cannot alias or link, log options are bounded, and chunked bodies
are framed strictly and counted byte for byte (app/sandbox/docker_proxy.py)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from app.sandbox.docker_proxy import (
    INSTANCE_LABEL,
    DockerApiPolicy,
    PolicyDenied,
    ProxyLimits,
    filter_image,
    filter_info,
)
from tests.unit.sandbox.test_docker_proxy import TOKEN
from tests.unit.sandbox.test_docker_proxy_isolation import (
    EGRESS,
    IMAGE,
    OWN,
    _Daemon,
    _json,
    _labels,
    _request,
    _start,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

_LIMITS: dict[str, int] = {"Memory": 512 * 1024 * 1024, "NanoCpus": 500_000_000, "PidsLimit": 256}
# The rest of the sandbox floor (SEC-A-04) a raw create body needs, beside a non-root ``User``.
_FLOOR: dict[str, Any] = {"CapDrop": ["ALL"], "SecurityOpt": ["no-new-privileges:true"], "ReadonlyRootfs": True}

_RAW_INFO: dict[str, Any] = {
    "NCPU": 4, "MemTotal": 8 << 30, "Driver": "btrfs",
    "DriverStatus": [
        ["Backing Filesystem", "btrfs"], ["Build Version", "x"], ["Data file", "/var/lib/docker/devicemapper/data"],
        ["driver-type", "io.containerd.snapshotter.v1"], ["bad"], "x", [1, 2],
    ],
    "HttpProxy": "http://user:secret@proxy:3128", "Labels": ["owner=someone"],
}
_RAW_IMAGE: dict[str, Any] = {
    "Id": "sha256:" + "c" * 64, "RepoTags": [IMAGE], "RepoDigests": [], "Created": "2026-01-01T00:00:00Z",
    "Architecture": "amd64", "Os": "linux", "Size": 123, "Config": {"Labels": {"a": "b"}, "User": "sandbox"},
    "Container": "d" * 64, "ContainerConfig": {"Hostname": "d" * 12}, "Parent": "sha256:" + "e" * 64,
    "GraphDriver": {"Name": "overlay2", "Data": {"UpperDir": "/var/lib/docker/overlay2/abc/diff"}},
    "Metadata": {"LastTagTime": "x"}, "RootFS": {"Type": "layers", "Layers": []},
}


@pytest.fixture
def policy() -> DockerApiPolicy:
    return DockerApiPolicy(allowed_images=frozenset({IMAGE}), allowed_networks=frozenset({EGRESS}))


class _RichDaemon(_Daemon):
    """Also answers /info and image inspect with raw daemon payloads."""

    def answer(self, method: str, parts: list[str]) -> tuple[str, object] | None:
        if parts == ["info"]:
            return "200 OK", _RAW_INFO
        if parts[:1] == ["images"] and parts[-1] == "json":
            if "missing" in "/".join(parts):
                return "404 Not Found", {"message": "No such image"}
            return "200 OK", _RAW_IMAGE
        return None


@pytest.fixture
async def served(tmp_path, policy: DockerApiPolicy) -> AsyncIterator[tuple[_RichDaemon, int]]:
    daemon = _RichDaemon()
    upstream, server, port = await _start(tmp_path, policy, daemon, ProxyLimits(max_upload_bytes=100))
    yield daemon, port
    server.close()
    upstream.close()


def _get(target: str) -> bytes:
    return f"GET {target} HTTP/1.1\r\nHost: x\r\n\r\n".encode()


def _create_body(host: dict[str, Any] | None = None, **top: object) -> dict[str, Any]:
    return {"Image": IMAGE, "User": "sandbox", "HostConfig": {"NetworkMode": "none", **_LIMITS, **_FLOOR, **(host or {})}, **top}


# --- /info --------------------------------------------------------------------

class TestInfoKeepsTheStorageDriverFacts:
    def test_driver_status_keeps_only_backing_filesystem_and_driver_type(self) -> None:
        info = filter_info(_RAW_INFO)
        assert info["Driver"] == "btrfs"
        assert info["DriverStatus"] == [
            ["Backing Filesystem", "btrfs"], ["driver-type", "io.containerd.snapshotter.v1"],
        ]
        assert "HttpProxy" not in info and "Labels" not in info

    @pytest.mark.parametrize("status", [None, "x", {}, [["Pool Name", "docker-pool"]]])
    def test_driver_status_is_always_a_list(self, status: object) -> None:
        raw = {"Driver": "overlay2"} if status is None else {"Driver": "overlay2", "DriverStatus": status}
        assert filter_info(raw)["DriverStatus"] == []

    async def test_through_the_proxy(self, served) -> None:
        _, port = served
        resp = await _request(port, _get("/v1.43/info"))
        assert resp.startswith(b"HTTP/1.1 200")
        info = _json(resp)
        assert info["DriverStatus"] == [
            ["Backing Filesystem", "btrfs"], ["driver-type", "io.containerd.snapshotter.v1"],
        ]
        assert b"devicemapper" not in resp and b"secret" not in resp


# --- Image inspect ------------------------------------------------------------

class TestImageInspectIsFiltered:
    def test_filter(self) -> None:
        image = filter_image(_RAW_IMAGE)
        assert isinstance(image, dict)
        assert set(image) == {"Id", "RepoTags", "RepoDigests", "Created", "Architecture", "Os", "Size", "Config"}

    def test_route(self, policy: DockerApiPolicy) -> None:
        assert policy.route("GET", f"/v1.43/images/{IMAGE}/json").kind == "image_inspect"

    async def test_through_the_proxy(self, served) -> None:
        daemon, port = served
        resp = await _request(port, _get(f"/v1.43/images/{IMAGE}/json"))
        assert resp.startswith(b"HTTP/1.1 200")
        image = _json(resp)
        assert image["Id"] == _RAW_IMAGE["Id"] and image["RepoTags"] == [IMAGE]
        assert image["Config"] == {"Labels": {"a": "b"}, "User": "sandbox"}
        for leak in (b"/var/lib/docker", b"d" * 64, b"GraphDriver", b"ContainerConfig", b"Parent"):
            assert leak not in resp
        assert ("GET", f"/v1.43/images/{IMAGE}/json") in daemon.requests

    async def test_docker_py_reads_the_filtered_image(self, served) -> None:
        docker = pytest.importorskip("docker")
        _, port = served
        import asyncio

        def get() -> object:
            client = docker.DockerClient(base_url=f"tcp://127.0.0.1:{port}", version="1.43")
            client.api.headers["Authorization"] = f"Bearer {TOKEN}"
            try:
                return client.images.get(IMAGE)
            finally:
                client.close()

        image = await asyncio.to_thread(get)
        assert image.id == _RAW_IMAGE["Id"] and image.tags == [IMAGE] and image.labels == {"a": "b"}

    async def test_a_missing_image_is_still_a_404(self, tmp_path) -> None:
        missing = "pipeshubai/pipeshub-sandbox:missing"
        policy = DockerApiPolicy(allowed_images=frozenset({missing}), allowed_networks=frozenset({EGRESS}))
        daemon = _RichDaemon()
        upstream, server, port = await _start(tmp_path, policy, daemon)
        try:
            resp = await _request(port, _get(f"/v1.43/images/{missing}/json"))
        finally:
            server.close()
            upstream.close()
        assert resp.startswith(b"HTTP/1.1 404")
        assert _json(resp) == {"message": "No such image"}


# --- Network inspect labels ---------------------------------------------------

class TestForeignNetworkLabels:
    @pytest.mark.parametrize("path", ["/v1.43/networks/egressid", "/v1.43/networks"])
    async def test_another_stacks_labels_are_dropped(self, tmp_path, policy, path: str) -> None:
        daemon = _Daemon(egress_labels={**_labels("stack-b"), "owner": "someone"})
        upstream, server, port = await _start(tmp_path, policy, daemon)
        try:
            resp = await _request(port, _get(path))
        finally:
            server.close()
            upstream.close()
        assert resp.startswith(b"HTTP/1.1 200")
        network = _json(resp)
        network = network[0] if isinstance(network, list) else network
        assert network["Labels"] == {}
        assert b"stack-b" not in resp and b"someone" not in resp
        # What the app reads is still there.
        assert network["IPAM"]["Config"][0]["Subnet"] == "172.30.0.0/16"

    async def test_own_labels_are_kept(self, served) -> None:
        _, port = served
        resp = await _request(port, _get("/v1.43/networks/egressid"))
        assert _json(resp)["Labels"][INSTANCE_LABEL] == "stack-a"


class TestNetworkCreatePinsIPv6Off:
    def test_create_carries_an_explicit_false(self, policy: DockerApiPolicy) -> None:
        out = policy.check_network_create(
            {"Name": EGRESS, "Driver": "bridge", "Options": {"com.docker.network.bridge.enable_icc": "false"}},
            "stack-a",
        )
        assert out["EnableIPv6"] is False


# --- Endpoint settings --------------------------------------------------------

class TestEndpointSettings:
    @pytest.mark.parametrize("endpoint", [
        {"Aliases": ["db"]}, {"Links": ["other:db"]}, {"Aliases": ["a"], "Links": None},
    ])
    def test_aliases_and_links_are_refused(self, policy: DockerApiPolicy, endpoint: dict[str, Any]) -> None:
        body = _create_body({"NetworkMode": EGRESS}, NetworkingConfig={"EndpointsConfig": {EGRESS: endpoint}})
        with pytest.raises(PolicyDenied, match="Aliases|Links"):
            policy.check_container_create(body, "stack-a")

    @pytest.mark.parametrize("endpoint", [
        None, {}, {"Aliases": None, "Links": None}, {"Aliases": [], "Links": []}, {"IPAMConfig": None},
    ])
    def test_empty_ones_are_admitted(self, policy: DockerApiPolicy, endpoint: object) -> None:
        body = _create_body({"NetworkMode": EGRESS}, NetworkingConfig={"EndpointsConfig": {EGRESS: endpoint}})
        policy.check_container_create(body, "stack-a")


# --- Log options --------------------------------------------------------------

class TestLogOptions:
    @pytest.mark.parametrize("config", [
        {"max-size": "10m"}, {"max-size": "100m", "max-file": "5"}, {"max-size": "102400k"},
        {"max-file": "1"}, {"max-size": "1048576"}, {"max-size": "100M", "max-file": "3"},
        {"max-size": "10mb"}, {"max-size": "1.5m"}, {"max-size": "100MiB"}, {"max-size": "10 m"},
    ])
    @pytest.mark.parametrize("driver", ["local", "json-file"])
    def test_bounded_rotation_is_forwarded(self, policy, driver: str, config: dict[str, str]) -> None:
        out = policy.check_container_create(_create_body({"LogConfig": {"Type": driver, "Config": config}}))
        expected = dict(config)
        if driver == "json-file":
            # json-file never rotates on its own, so a missing cap is filled in.
            expected.setdefault("max-size", "10m")
            expected.setdefault("max-file", "3")
        assert out["HostConfig"]["LogConfig"] == {"Type": driver, "Config": expected}

    @pytest.mark.parametrize("config", [
        {"max-size": "101m"}, {"max-size": "1g"}, {"max-size": "0"}, {"max-size": "-1"},
        {"max-size": "0m"}, {"max-size": "0.5"}, {"max-size": "100.1m"}, {"max-size": " 10m"},
        {"max-size": "1.2.3m"}, {"max-size": "10t"}, {"max-size": "1e3"}, {"max-size": "10m\n"},
        {"max-size": 10}, {"max-size": None, "max-file": "6"}, {"max-file": "0"}, {"max-file": "-1"},
        {"max-file": 3}, {"max-file": "100"}, {"compress": "true"}, {"labels": "a"}, {"tag": "{{.ID}}"},
        {"Max-Size": "10m"}, {"mode": "non-blocking"}, {"max-buffer-size": "1m"}, "max-size=10m", [],
    ])
    def test_anything_else_is_refused(self, policy, config: object) -> None:
        with pytest.raises(PolicyDenied):
            policy.check_container_create(_create_body({"LogConfig": {"Type": "local", "Config": config}}))

    def test_docker_py_log_config_is_admitted(self, policy) -> None:
        pytest.importorskip("docker")
        from docker.types import LogConfig

        from tests.unit.sandbox.test_docker_proxy import _RUN, _sdk_create_body

        body = _sdk_create_body(**{**_RUN, "log_config": LogConfig(
            type="local", config={"max-size": "20m", "max-file": "2"},
        )})
        out = policy.check_container_create(body)
        assert out["HostConfig"]["LogConfig"] == {"Type": "local", "Config": {"max-size": "20m", "max-file": "2"}}


# --- Chunked framing ----------------------------------------------------------

def _put(body: bytes, *, framing: str = "Transfer-Encoding: chunked") -> bytes:
    return (f"PUT /v1.43/containers/{OWN}/archive?path=/src HTTP/1.1\r\nHost: x\r\n{framing}\r\n\r\n"
            .encode() + body)


def _create(body: bytes) -> bytes:
    return b"POST /v1.43/containers/create HTTP/1.1\r\nHost: x\r\nTransfer-Encoding: chunked\r\n\r\n" + body


class TestChunkedFraming:
    async def test_plain_chunks_are_forwarded(self, served) -> None:
        daemon, port = served
        resp = await _request(port, _put(b"5\r\nhello\r\n3\r\nabc\r\n0\r\n\r\n"))
        assert resp.startswith(b"HTTP/1.1 200")
        assert daemon.bodies[f"PUT /v1.43/containers/{OWN}/archive?path=/src"] == b"helloabc"

    @pytest.mark.parametrize("body", [
        b"5;ext=1\r\nhello\r\n0\r\n\r\n",  # extension
        b"5;a\nb\r\nhello\r\n0\r\n\r\n",  # bare LF inside an extension
        b"5 \r\nhello\r\n0\r\n\r\n",  # trailing whitespace
        b"5\r\nhello\r\n0;x\r\n\r\n",  # extension on the last chunk
        b"5\r\nhello\r\n0\r\nX-Trailer: 1\r\n\r\n",  # trailer
        b"5\r\nhelloXY0\r\n\r\n",  # data not followed by CRLF
        b"5\r\nhello\n0\r\n\r\n",
    ])
    async def test_extensions_trailers_and_bad_framing_are_refused(self, served, body: bytes) -> None:
        _, port = served
        resp = await _request(port, _put(body))
        assert resp.startswith(b"HTTP/1.1 403")

    async def test_an_overlong_size_line_is_refused(self, served) -> None:
        _, port = served
        resp = await _request(port, _put(b"1" * 70_000 + b"\r\n"))
        assert resp.startswith(b"HTTP/1.1 403")

    async def test_framing_counts_toward_the_cap(self, served) -> None:
        # 40 data bytes, 160 bytes on the wire: over the 100-byte cap.
        daemon, port = served
        resp = await _request(port, _put(b"1\r\nx\r\n" * 40 + b"0\r\n\r\n"))
        assert resp.startswith(b"HTTP/1.1 403") and b"too large" in resp

    @pytest.mark.parametrize("body", [
        b"5;ext\r\n{}xyz\r\n0\r\n\r\n", b"2\r\n{}\r\n0\r\nX: y\r\n\r\n", b"2\r\n{}XX0\r\n\r\n",
    ])
    async def test_create_bodies_are_framed_as_strictly(self, served, body: bytes) -> None:
        daemon, port = served
        resp = await _request(port, _create(body))
        assert resp.startswith(b"HTTP/1.1 403")
        assert not any("create" in target for _, target in daemon.requests)

    @pytest.mark.parametrize("value", ["+5", "0x5", "5_0", "٥", "-0", "1" * 20, ""])
    async def test_content_length_is_plain_decimal(self, served, value: str) -> None:
        daemon, port = served
        resp = await _request(port, _put(b"hello", framing=f"Content-Length: {value}"))
        assert resp.startswith(b"HTTP/1.1 403")
        assert daemon.requests == []


class TestTheAppsLogRotationIsAdmitted:
    def test_default_rotation(self, policy, monkeypatch) -> None:
        from app.agent_loop_lib.sandbox.coding import docker as coding_docker

        if not hasattr(coding_docker, "_bounded_log_config"):
            pytest.skip("this checkout sends no log rotation")
        monkeypatch.delenv("SANDBOX_LOG_MAX_SIZE", raising=False)
        monkeypatch.delenv("SANDBOX_LOG_MAX_FILES", raising=False)
        sent = coding_docker._bounded_log_config(0)
        out = policy.check_container_create(
            _create_body({"LogConfig": {"Type": sent["type"], "Config": sent["config"]}}),
        )
        assert out["HostConfig"]["LogConfig"] == {"Type": sent["type"], "Config": sent["config"]}
