"""Ways around the Docker API proxy's policy (app/sandbox/docker_proxy.py):
create bodies that try to lift the resource caps or reach the host, routes
that try to reach another stack's containers or a daemon endpoint the proxy
does not list, request bodies the daemon reads as form fields, and the
proxy's behaviour under many concurrent clients."""

from __future__ import annotations

import asyncio
import json
import math
from typing import TYPE_CHECKING, Any

import pytest

from app.sandbox import docker_proxy
from app.sandbox.docker_proxy import (
    INSTANCE_LABEL,
    MANAGED_LABEL,
    DockerApiPolicy,
    DockerSocketProxy,
    PolicyDenied,
    ReaperSettings,
)
from tests.unit.sandbox.test_docker_proxy import (
    _INSTALL,
    _LIMITS,
    EGRESS,
    IMAGE,
    _FakeDaemon,
    _request,
    _sdk_create_body,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

MB = 1024 * 1024


@pytest.fixture
def policy() -> DockerApiPolicy:
    return DockerApiPolicy(allowed_images=frozenset({IMAGE}), allowed_networks=frozenset({EGRESS}))


def _body(host: dict[str, Any] | None = None, **top: object) -> dict[str, Any]:
    return {"Image": IMAGE, "HostConfig": {**_LIMITS, "NetworkMode": "none", **(host or {})}, **top}


# --- Create policy -----------------------------------------------------------


class TestResourceCapEscapes:
    @pytest.mark.parametrize("host", [
        # Memory: missing, zero, negative ("unlimited"), wrong JSON types, over the cap.
        {"Memory": None}, {"Memory": 0}, {"Memory": -1}, {"Memory": "536870912"},
        {"Memory": 536870912.0}, {"Memory": True}, {"Memory": 1024 * MB + 1}, {"Memory": 10**30},
        {"Memory": [536870912]},
        # CPU: NanoCpus missing / zero / negative / float / over; the other CPU knobs are refused.
        {"NanoCpus": None}, {"NanoCpus": 0}, {"NanoCpus": -1}, {"NanoCpus": 5e8},
        {"NanoCpus": 1_000_000_001}, {"NanoCpus": True},
        {"CpuQuota": 400000}, {"CpuPeriod": 100000}, {"CpuShares": 262144}, {"CpusetCpus": "0-63"},
        {"CpuCount": 64}, {"CpuPercent": 100}, {"CpuRealtimeRuntime": 1}, {"CpusetMems": "0"},
        # Processes: 0 and -1 are "unlimited" to Docker.
        {"PidsLimit": None}, {"PidsLimit": 0}, {"PidsLimit": -1}, {"PidsLimit": 257},
        {"PidsLimit": 256.0}, {"PidsLimit": False}, {"PidsLimit": "256"},
        # Swap: anything but unset, 0 or Memory.
        {"MemorySwap": -1}, {"MemorySwap": _LIMITS["Memory"] + 1}, {"MemorySwap": _LIMITS["Memory"] * 2},
        {"MemorySwap": float(_LIMITS["Memory"])}, {"MemorySwap": str(_LIMITS["Memory"])},
        {"MemorySwap": 1}, {"MemorySwappiness": 100},
        # Other memory knobs that would lift or bypass the limit.
        {"MemoryReservation": 4096 * MB}, {"KernelMemory": 4096 * MB}, {"KernelMemoryTCP": 1},
        {"OomKillDisable": True},
        # OOM score: may only be raised, 0..1000, as a JSON integer.
        {"OomScoreAdj": -1}, {"OomScoreAdj": -1000}, {"OomScoreAdj": 1001}, {"OomScoreAdj": "500"},
        {"OomScoreAdj": 500.0}, {"OomScoreAdj": True},
        # Other per-container limits the policy does not reason about.
        {"Ulimits": [{"Name": "nproc", "Soft": -1, "Hard": -1}]}, {"BlkioWeight": 1000},
        {"IOMaximumBandwidth": 1}, {"ShmSize": 64 * 1024 * MB},
    ])
    def test_refused(self, policy: DockerApiPolicy, host: dict[str, Any]) -> None:
        body = _body()
        for key, value in host.items():
            if value is None:
                body["HostConfig"].pop(key, None)
            else:
                body["HostConfig"][key] = value
        with pytest.raises(PolicyDenied):
            policy.check_container_create(body, "stack-a")

    @pytest.mark.parametrize("host_config", [None, [], "x", 1, {}])
    def test_host_config_missing_null_or_not_an_object(self, policy: DockerApiPolicy, host_config: object) -> None:
        body: dict[str, Any] = {"Image": IMAGE}
        if host_config != {}:
            body["HostConfig"] = host_config
        with pytest.raises(PolicyDenied):
            policy.check_container_create(body, "stack-a")

    def test_limits_at_the_top_level_are_refused(self, policy: DockerApiPolicy) -> None:
        # Old API versions read Memory and friends from the top-level config.
        with pytest.raises(PolicyDenied):
            policy.check_container_create(_body(Memory=8192 * MB), "stack-a")

    @pytest.mark.parametrize("key", [
        "memory", "MEMORY", "nanoCpus", "nanocpus", "pidslimit", "PIDSLIMIT", "memoryswap",
        "Memoryswap", "oomScoreAdj", "OOMSCOREADJ",
        # Go's case folding also equates U+017F with s and U+212A (Kelvin) with k.
        "Memoryſwap", "PidſLimit",
    ])
    def test_case_variants_of_limit_fields_are_refused(self, policy: DockerApiPolicy, key: str) -> None:
        body = _body()
        body["HostConfig"][key] = 1
        with pytest.raises(PolicyDenied):
            policy.check_container_create(body, "stack-a")

    def test_bounds_are_inclusive(self, policy: DockerApiPolicy) -> None:
        for host in (
            {"Memory": 1024 * MB, "NanoCpus": 1_000_000_000, "PidsLimit": 256, "OomScoreAdj": 1000},
            {"Memory": 1, "NanoCpus": 1, "PidsLimit": 1, "OomScoreAdj": 0},
        ):
            out = policy.check_container_create(_body(host), "stack-a")
            assert out["HostConfig"]["MemorySwap"] == host["Memory"]
            assert out["HostConfig"]["OomScoreAdj"] == host["OomScoreAdj"]

    @pytest.mark.parametrize("swap", [None, 0, _LIMITS["Memory"]])
    def test_swap_is_always_forwarded_equal_to_memory(self, policy: DockerApiPolicy, swap: int | None) -> None:
        body = _body()
        if swap is not None:
            body["HostConfig"]["MemorySwap"] = swap
        out = policy.check_container_create(body, "stack-a")
        assert out["HostConfig"]["MemorySwap"] == _LIMITS["Memory"]
        assert "OomScoreAdj" not in out["HostConfig"]

    def test_caps_follow_the_env(self) -> None:
        p = DockerApiPolicy.from_env({
            "DOCKER_PROXY_ALLOWED_IMAGES": IMAGE, "DOCKER_PROXY_MAX_MEMORY_MB": "2048",
            "DOCKER_PROXY_MAX_CPUS": "2", "DOCKER_PROXY_MAX_PIDS": "512",
        })
        p.allowed_networks = frozenset({EGRESS})
        p.check_container_create(_body({"Memory": 2048 * MB, "NanoCpus": 2_000_000_000, "PidsLimit": 512}))
        with pytest.raises(PolicyDenied):
            p.check_container_create(_body({"Memory": 2048 * MB + 1}))

    @pytest.mark.parametrize("raw", ["inf", "-inf", "nan", "Infinity", "1e400"])
    def test_non_finite_cpu_cap_falls_back_to_the_default(self, raw: str) -> None:
        # Infinity used to crash the proxy at startup (int(inf * 1e9)).
        p = DockerApiPolicy.from_env({"DOCKER_PROXY_MAX_CPUS": raw})
        assert p.max_nano_cpus == 1_000_000_000


class TestHostAccessEscapes:
    @pytest.mark.parametrize("host", [
        {"Privileged": True}, {"Privileged": "true"}, {"Privileged": 1},
        {"CapAdd": ["SYS_ADMIN"]}, {"CapAdd": ["ALL"]}, {"CapAdd": ["cap_sys_ptrace"]},
        {"CapAdd": "SYS_ADMIN"}, {"CapAdd": ["NET_ADMIN", "SYS_MODULE"]},
        {"SecurityOpt": ["seccomp=unconfined"]}, {"SecurityOpt": ["apparmor=unconfined"]},
        {"SecurityOpt": ["label=disable"]}, {"SecurityOpt": ["systempaths=unconfined"]},
        {"NetworkMode": "host"}, {"NetworkMode": "bridge"}, {"NetworkMode": "default"},
        {"NetworkMode": "container:abc"}, {"NetworkMode": ""},
        {"IpcMode": "host"}, {"IpcMode": "shareable"}, {"IpcMode": "container:abc"},
        {"PidMode": "host"}, {"PidMode": "container:abc"}, {"UsernsMode": "host"}, {"UTSMode": "host"},
        {"CgroupnsMode": "host"}, {"Runtime": "nvidia"}, {"Runtime": "io.containerd.runc.v2"},
        {"Sysctls": {"kernel.core_pattern": "|/x"}}, {"Devices": [{"PathOnHost": "/dev/mem"}]},
        {"DeviceRequests": [{"Count": -1}]}, {"DeviceCgroupRules": ["a *:* rwm"]},
        {"Binds": ["/:/host"]}, {"VolumesFrom": ["x"]}, {"Links": ["x:y"]}, {"VolumeDriver": "x"},
        {"Mounts": [{"Type": "bind", "Source": "/", "Target": "/host"}]},
        {"Mounts": [{"Type": "volume", "Source": "x", "Target": "/x"}]},
        {"Mounts": [{"Type": "TMPFS", "Target": "/x"}]},
        {"Mounts": [{"type": "bind", "Source": "/", "Target": "/host"}]},
        {"Mounts": [{"Type": "tmpfs", "type": "bind", "Target": "/x"}]},
        {"CgroupParent": "/"}, {"Cgroup": "host"}, {"Isolation": "process"},
        {"MaskedPaths": []}, {"ReadonlyPaths": []}, {"PortBindings": {"80/tcp": [{"HostPort": "80"}]}},
        {"PublishAllPorts": True}, {"RestartPolicy": {"Name": "always"}},
        {"RestartPolicy": {"name": "always"}}, {"LogConfig": {"Type": "syslog"}},
        {"LogConfig": {"Type": "local", "Config": {"max-size": "1g"}}},
        {"StorageOpt": {"size": "1g", "dm.basesize": "1"}}, {"GroupAdd": ["docker"]},
        {"ExtraHosts": ["x:1.2.3.4"]}, {"Dns": ["1.1.1.1"]}, {"Init": True},
        {"ConsoleSize": [1, 1]}, {"AutoRemove": True}, {"Annotations": {"x": "y"}},
    ])
    def test_refused(self, policy: DockerApiPolicy, host: dict[str, Any]) -> None:
        with pytest.raises(PolicyDenied):
            policy.check_container_create(_body(host), "stack-a")

    @pytest.mark.parametrize("top", [
        {"Volumes": {"/x": {}}}, {"Runtime": "nvidia"}, {"Image": "alpine"}, {"Image": ""},
        {"Image": None}, {"image": IMAGE}, {"Platform": "x"}, {"HostConfig2": {}},
    ])
    def test_refused_top_level(self, policy: DockerApiPolicy, top: dict[str, Any]) -> None:
        with pytest.raises(PolicyDenied):
            policy.check_container_create(_body(**top), "stack-a")

    @pytest.mark.parametrize("image", [{"x": 1}, [IMAGE], 1, True])
    def test_image_that_is_not_a_string_is_a_policy_refusal(self, policy: DockerApiPolicy, image: object) -> None:
        # It used to raise AttributeError in normalize_image: a 502 and a
        # stack trace in the log instead of a 403.
        with pytest.raises(PolicyDenied):
            policy.check_container_create(_body(Image=image), "stack-a")


class TestNetworkEscapes:
    @pytest.mark.parametrize("endpoint", [
        # The daemon attaches a user-defined network's endpoint by its
        # NetworkID when one is given (daemon getNetworkID), not by the key.
        {"NetworkID": "pipeshub-ai_default"}, {"NetworkID": "bridge"}, {"NetworkID": "host"},
        {"NetworkId": "x"}, {"networkid": "x"}, {"EndpointID": "x"}, {"Gateway": "1.2.3.4"},
        {"IPAddress": "1.2.3.4"}, "x", [1],
    ])
    def test_endpoint_settings_cannot_redirect_the_network(
        self, policy: DockerApiPolicy, endpoint: object,
    ) -> None:
        body = _body({"NetworkMode": EGRESS}, NetworkingConfig={"EndpointsConfig": {EGRESS: endpoint}})
        with pytest.raises(PolicyDenied):
            policy.check_container_create(body, "stack-a")

    @pytest.mark.parametrize("endpoint", [None, {}, {"Aliases": []}, {"IPAMConfig": None}])
    def test_plain_endpoint_settings_are_admitted(self, policy: DockerApiPolicy, endpoint: object) -> None:
        body = _body({"NetworkMode": EGRESS}, NetworkingConfig={"EndpointsConfig": {EGRESS: endpoint}})
        policy.check_container_create(body, "stack-a")

    def test_what_the_sdk_sends_for_a_networked_run_is_admitted(self, policy: DockerApiPolicy) -> None:
        policy.check_container_create(_sdk_create_body(**_INSTALL), "stack-a")

    @pytest.mark.parametrize("networking", [
        {"EndpointsConfig": {"pipeshub-ai_default": {}}}, {"EndpointsConfig": {"host": None}},
        {"endpointsConfig": {"host": None}}, {"EndpointsConfig": {EGRESS: None}, "endpointsconfig": {}},
        [], "x",
    ])
    def test_other_networks_are_refused(self, policy: DockerApiPolicy, networking: object) -> None:
        with pytest.raises(PolicyDenied):
            policy.check_container_create(_body({"NetworkMode": EGRESS}, NetworkingConfig=networking), "stack-a")


class TestLabels:
    @pytest.mark.parametrize("labels", [
        {MANAGED_LABEL: "false", INSTANCE_LABEL: "stack-b"}, {INSTANCE_LABEL: ""}, None, {},
    ])
    def test_ownership_labels_are_always_stamped(self, policy: DockerApiPolicy, labels: object) -> None:
        out = policy.check_container_create(_body(Labels=labels), "stack-a")
        assert out["Labels"][MANAGED_LABEL] == "true"
        assert out["Labels"][INSTANCE_LABEL] == "stack-a"

    @pytest.mark.parametrize("labels", [[], "x", [[MANAGED_LABEL, "true"]]])
    def test_labels_must_be_an_object(self, policy: DockerApiPolicy, labels: object) -> None:
        with pytest.raises(PolicyDenied):
            policy.check_container_create(_body(Labels=labels), "stack-a")


# --- Routes ------------------------------------------------------------------


class TestRouteEscapes:
    @pytest.mark.parametrize("method,target", [
        ("POST", "/containers/own/exec"), ("POST", "/containers/own/attach"),
        ("GET", "/containers/own/attach/ws"), ("POST", "/containers/own/update"),
        ("POST", "/containers/own/resize"), ("GET", "/containers/own/export"),
        ("GET", "/containers/own/stats"), ("GET", "/containers/own/top"),
        ("GET", "/containers/own/changes"), ("POST", "/containers/own/pause"),
        ("POST", "/containers/own/rename?name=x"), ("POST", "/containers/own/restart"),
        ("POST", "/commit?container=own"), ("POST", "/containers/prune"), ("GET", "/containers/json"),
        ("POST", "/containers/create/x"), ("GET", "/containers/create"),
        ("GET", "/images/json"), ("POST", "/images/load"), ("POST", "/images/own/push"),
        ("POST", "/images/own/tag?repo=x"), ("GET", "/events"), ("GET", "/system/df"),
        ("POST", "/session"), ("GET", "/distribution/x/json"), ("POST", "/auth"),
        ("POST", "/networks/x/connect"), ("POST", "/networks/x/disconnect"), ("POST", "/networks/prune"),
        ("GET", "/volumes"), ("GET", "/secrets"), ("GET", "/configs"), ("GET", "/nodes"),
        # Version prefix spellings the daemon would not take as a version.
        ("GET", "/V1.41/containers/own/json"), ("GET", "/v1.41x/containers/own/json"),
        # Empty and dot segments, encoded or not, and fragments: the daemon
        # cleans the path to another endpoint.
        ("GET", "//containers/own/json"), ("GET", "/v1.41//containers/own/json"),
        ("GET", "/containers/own/json/"), ("GET", "/containers/own/json/../exec"),
        ("POST", "/containers/own/start/../../../exec"), ("GET", "/containers/./own/json"),
        ("GET", "/containers/../containers/own/json"), ("GET", "/containers/%2e%2e/info"),
        ("GET", "/containers/own%2F..%2F..%2Finfo/json"), ("GET", "/containers/%2E/json"),
        ("GET", "/containers/own/json#/../exec"), ("DELETE", "/containers/own#/../../x"),
        ("GET", "/info#x"), ("GET", "/_ping#/../containers/json"),
        # Local routes: only GET/HEAD and only the known query.
        ("POST", "/_pipeshub/host"), ("GET", "/_pipeshub/host?x=1"), ("GET", "/_pipeshub/usage?scope=all"),
        ("GET", "/_pipeshub/usage?scope=containers&scope=containers"), ("GET", "/_pipeshub/other"),
        # Image import and other images.
        ("POST", "/images/create?fromImage=pipeshubai%2Fpipeshub-sandbox&tag=latest&fromSrc=-"),
        ("POST", "/images/create?fromImage=pipeshubai%2Fpipeshub-sandbox&repo=x"),
        ("POST", "/images/create?fromImage=evil%2Fimage&fromImage=pipeshubai%2Fpipeshub-sandbox"),
        ("POST", "/images/create?fromImage=pipeshubai%2Fpipeshub-sandbox&tag=other"),
        ("POST", "/images/create?fromImage=pipeshubai%2Fpipeshub-sandbox%3Alatest&tag=evil"),
        ("POST", "/images/create?fromImage="), ("GET", "/images/alpine/json"),
        ("GET", "/images/pipeshubai/pipeshub-sandbox:latest/history"),
    ])
    def test_denied(self, policy: DockerApiPolicy, method: str, target: str) -> None:
        with pytest.raises(PolicyDenied):
            policy.route(method, target)

    @pytest.mark.parametrize("target,container", [
        ("/v1.41/containers/abc/json?size=1", "abc"), ("/v1/containers/abc/start", "abc"),
        ("/containers/my%20name/json", "my name"),
    ])
    def test_container_ident_is_decoded_for_the_ownership_check(
        self, policy: DockerApiPolicy, target: str, container: str,
    ) -> None:
        route = policy.route("GET" if target.endswith(("json", "size=1")) else "POST", target)
        assert route.kind == "container_owned" and route.container == container

    def test_image_names_with_dots_are_still_routed(self, policy: DockerApiPolicy) -> None:
        p = DockerApiPolicy(allowed_images=frozenset({"registry.example.com/a.b/sandbox:1.2"}),
                            allowed_networks=frozenset({EGRESS}))
        assert p.route("GET", "/v1.43/images/registry.example.com/a.b/sandbox:1.2/json").kind == "image_inspect"


# --- Through the server -------------------------------------------------------


@pytest.fixture
async def served(tmp_path, policy: DockerApiPolicy) -> AsyncIterator[tuple[_FakeDaemon, int]]:
    owned = {"Id": "f" * 64, "Config": {"Labels": {MANAGED_LABEL: "true", INSTANCE_LABEL: "stack-a"}}}
    other = {"Id": "d" * 64, "Config": {"Labels": {MANAGED_LABEL: "true", INSTANCE_LABEL: "stack-b"}}}
    legacy = {"Id": "c" * 64, "Config": {"Labels": {MANAGED_LABEL: "true"}}}
    plain = {"Id": "e" * 64, "Config": {"Labels": None}}
    daemon = _FakeDaemon({"own": owned, "f" * 64: owned, "other": other, "legacy": legacy, "plain": plain})
    sock = str(tmp_path / "d.sock")
    upstream = await asyncio.start_unix_server(daemon.handle, sock)
    proxy = DockerSocketProxy(policy, sock, instance="stack-a")
    server = await asyncio.start_server(proxy.handle, "127.0.0.1", 0)
    yield daemon, server.sockets[0].getsockname()[1]
    server.close()
    upstream.close()


def _http(method: str, target: str, body: bytes = b"", headers: tuple[str, ...] = ()) -> bytes:
    head = [f"{method} {target} HTTP/1.1", "Host: docker", *headers]
    if body:
        head.append(f"Content-Length: {len(body)}")
    return ("\r\n".join(head) + "\r\n\r\n").encode() + body


PULL = "/v1.43/images/create?fromImage=pipeshubai%2Fpipeshub-sandbox&tag=latest"


class TestFormBodies:
    """The daemon parses an ``application/x-www-form-urlencoded`` body into
    the same form as the query string, ahead of it; ``/images/create`` reads
    ``fromImage``, ``fromSrc`` and ``repo`` from there. An empty body
    ``fromImage`` turns a pull of the allowed image into an import from any
    URL, tagged as the allowed image."""

    async def test_import_through_a_form_body_never_reaches_the_daemon(self, served) -> None:
        daemon, port = served
        body = b"fromImage=&fromSrc=http%3A%2F%2F169.254.169.254%2F&repo=pipeshubai%2Fpipeshub-sandbox&tag=latest"
        resp = await _request(port, _http(
            "POST", PULL, body, ("Content-Type: application/x-www-form-urlencoded",),
        ))
        assert resp.startswith(b"HTTP/1.1 403")
        assert daemon.requests == []

    @pytest.mark.parametrize("content_type", [
        "application/x-www-form-urlencoded", "Application/X-WWW-Form-Urlencoded; charset=utf-8",
        " application/x-www-form-urlencoded ;", "multipart/form-data; boundary=x",
    ])
    async def test_form_content_types_are_refused_on_every_route(self, served, content_type: str) -> None:
        daemon, port = served
        resp = await _request(port, _http(
            "POST", "/v1.43/containers/own/kill", b"signal=KILL", (f"Content-Type: {content_type}",),
        ))
        assert resp.startswith(b"HTTP/1.1 403")
        assert all(not target.endswith("/kill") for _, target, _ in daemon.requests)

    @pytest.mark.parametrize("headers,body", [
        ((), b"fromImage=evil"),
        (("Content-Type: application/octet-stream",), b"layer"),
        (("Transfer-Encoding: chunked",), b""),
    ])
    async def test_a_pull_carries_no_body(self, served, headers: tuple[str, ...], body: bytes) -> None:
        daemon, port = served
        raw = _http("POST", PULL, body, headers)
        if "Transfer-Encoding: chunked" in headers:
            raw += b"5\r\nhello\r\n0\r\n\r\n"
        resp = await _request(port, raw)
        assert resp.startswith(b"HTTP/1.1 403")
        assert daemon.requests == []

    async def test_a_plain_pull_still_works(self, served) -> None:
        daemon, port = served
        resp = await _request(port, _http("POST", PULL, headers=("Content-Length: 0",)))
        assert resp.startswith(b"HTTP/1.1 200")
        assert daemon.requests[-1][1] == PULL

    async def test_an_archive_upload_still_streams_its_body(self, served) -> None:
        daemon, port = served
        resp = await _request(port, _http(
            "PUT", "/v1.43/containers/own/archive?path=%2F", b"tar-bytes",
            ("Content-Type: application/x-tar",),
        ))
        assert resp.startswith(b"HTTP/1.1 200")
        assert daemon.requests[-1][2] == b"tar-bytes"


class TestForeignContainers:
    @pytest.mark.parametrize("name", ["other", "legacy", "plain", "missing", "d" * 12])
    @pytest.mark.parametrize("method,action", [
        ("GET", "/json"), ("POST", "/start"), ("POST", "/kill"), ("GET", "/logs"),
        ("GET", "/archive?path=/"), ("PUT", "/archive?path=/"), ("DELETE", "?force=1"),
    ])
    async def test_refused_and_never_forwarded(self, served, name: str, method: str, action: str) -> None:
        daemon, port = served
        resp = await _request(port, _http(method, f"/v1.43/containers/{name}{action}"))
        assert resp.startswith(b"HTTP/1.1 403")
        # Only the ownership inspect reached the daemon.
        assert all(target.endswith("/json") and m == "GET" for m, target, _ in daemon.requests)

    async def test_owned_container_target_is_rewritten_to_the_checked_id(self, served) -> None:
        daemon, port = served
        resp = await _request(port, _http("POST", "/v1.43/containers/own/stop?t=1"))
        assert resp.startswith(b"HTTP/1.1 200")
        assert daemon.requests[-1][1] == f"/v1.43/containers/{'f' * 64}/stop?t=1"


class TestBodyFraming:
    @pytest.mark.parametrize("size_line", [b"0x10", b"+10", b"-1", b"1_0", b" ", b"zz", b"10 10"])
    async def test_chunk_sizes_the_daemon_would_not_read_are_refused(self, served, size_line: bytes) -> None:
        daemon, port = served
        raw = _http("POST", "/v1.43/containers/create", headers=("Transfer-Encoding: chunked",))
        resp = await _request(port, raw + size_line + b"\r\n" + b"x" * 16 + b"\r\n0\r\n\r\n")
        assert resp.startswith(b"HTTP/1.1 403")
        assert daemon.requests == []

    async def test_a_create_body_that_never_arrives_times_out(self, served, monkeypatch) -> None:
        daemon, port = served
        monkeypatch.setattr(docker_proxy, "_HEAD_TIMEOUT_S", 0.3)
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        writer.write(_http("POST", "/v1.43/containers/create", headers=("Content-Length: 1000",)))
        await writer.drain()
        data = await asyncio.wait_for(reader.read(), 5)
        writer.close()
        assert data == b"" or data.startswith(b"HTTP/1.1 4")
        assert daemon.requests == []

    async def test_oversized_create_body_is_refused_before_it_is_read(self, served) -> None:
        daemon, port = served
        raw = _http("POST", "/v1.43/containers/create", headers=("Content-Length: 104857600",))
        resp = await _request(port, raw)
        assert resp.startswith(b"HTTP/1.1 403")
        assert daemon.requests == []


class TestConcurrency:
    async def test_many_clients_and_a_stalled_one_do_not_block_each_other(self, served) -> None:
        daemon, port = served
        stalled_r, stalled_w = await asyncio.open_connection("127.0.0.1", port)
        stalled_w.write(_http("POST", "/v1.43/containers/create", headers=("Content-Length: 5000",)))
        await stalled_w.drain()

        async def one(i: int) -> bytes:
            if i % 3 == 0:
                return await _request(port, _http("GET", "/v1.43/containers/own/json"))
            if i % 3 == 1:
                return await _request(port, _http("GET", "/_ping"))
            body = json.dumps(_body()).encode()
            return await _request(port, _http("POST", "/v1.43/containers/create", body))

        results = await asyncio.wait_for(asyncio.gather(*(one(i) for i in range(150))), 20)
        assert all(r.startswith(b"HTTP/1.1 200") for r in results)
        creates = [b for m, t, b in daemon.requests if t.endswith("/containers/create")]
        assert len(creates) == 50
        assert all(json.loads(b)["Labels"][INSTANCE_LABEL] == "stack-a" for b in creates)
        stalled_w.close()


# --- Reaper settings ---------------------------------------------------------


class TestReaperSettings:
    @pytest.mark.parametrize("raw", ["nan", "NaN", "inf", "-inf", "abc", "-5"])
    def test_interval_that_is_not_a_finite_number_uses_the_default(self, raw: str) -> None:
        # NaN used to reach asyncio.wait_for as the timeout: it returns at
        # once, so the reaper listed the daemon's containers in a tight loop.
        interval = ReaperSettings.from_env({"DOCKER_PROXY_REAP_INTERVAL_S": raw}).interval_s
        assert math.isfinite(interval) and interval >= 10

    @pytest.mark.parametrize("raw,expected", [
        ("nan", 420.0), ("abc", 420.0), ("-1", 420.0), ("0", None), ("600", 600.0),
    ])
    def test_reap_after(self, raw: str, expected: float | None) -> None:
        assert ReaperSettings.from_env({"DOCKER_PROXY_REAP_AFTER_S": raw}).reap_after_s == expected

    @pytest.mark.parametrize("run_timeout,expected", [
        ("", 420.0), ("60", 420.0), ("900", 1020.0), ("99999", 3720.0), ("inf", 3720.0),
        ("nan", 420.0), ("-5", 420.0), ("abc", 420.0),
    ])
    def test_reap_age_follows_the_run_cap_as_the_app_parses_it(self, run_timeout: str, expected: float) -> None:
        from app.agent_loop_lib.sandbox.coding.settings import max_run_timeout_from_env

        env = {"SANDBOX_MAX_RUN_TIMEOUT_S": run_timeout}
        assert ReaperSettings.from_env(env).reap_after_s == expected
        # The reaper must never remove a run the app still lets run.
        assert expected >= max_run_timeout_from_env(env) + 120


# --- Reaper on a daemon that does not apply the label filter -----------------


class _UnfilteredDaemon:
    """Answers every container list with every container, as a daemon (or a
    Docker-compatible API) that ignores the ``filters`` query would."""

    def __init__(self, containers: list[dict[str, Any]]) -> None:
        self.containers = containers
        self.deleted: list[str] = []

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        head = (await reader.readuntil(b"\r\n\r\n")).decode()
        method, target, _ = head.split("\r\n")[0].split(" ")
        if method == "GET" and target.startswith("/containers/json"):
            status, body = "200 OK", json.dumps(self.containers).encode()
        elif method == "GET" and target.startswith("/containers/") and target.endswith("/json"):
            # Started long ago, so the reaper ages it from a readable start.
            state = {"StartedAt": "2020-01-01T00:00:00Z", "FinishedAt": "0001-01-01T00:00:00Z"}
            status, body = "200 OK", json.dumps({"State": state}).encode()
        elif method == "DELETE":
            self.deleted.append(target.split("/")[2].split("?")[0])
            status, body = "204 No Content", b""
        else:
            status, body = "404 Not Found", b"{}"
        writer.write(f"HTTP/1.1 {status}\r\nContent-Length: {len(body)}\r\n\r\n".encode() + body)
        await writer.drain()
        writer.close()


class TestReaperChecksLabelsItself:
    @pytest.mark.parametrize("reap_unlabeled,expected", [
        (False, ["mine"]), (True, ["mine", "older-proxy"]),
    ])
    async def test_only_own_and_unlabelled_sandbox_containers_are_removed(
        self, tmp_path, reap_unlabeled: bool, expected: list[str],
    ) -> None:
        now = 1_800_000_000.0

        def c(ident: str, labels: dict[str, str] | None) -> dict[str, Any]:
            return {"Id": ident, "Created": int(now - 10_000), "State": "running", "Labels": labels}

        fake = _UnfilteredDaemon([
            c("mine", {MANAGED_LABEL: "true", INSTANCE_LABEL: "stack-a"}),
            c("theirs", {MANAGED_LABEL: "true", INSTANCE_LABEL: "stack-b"}),
            c("older-proxy", {MANAGED_LABEL: "true"}),
            c("database", {"com.docker.compose.project": "x"}),
            c("unlabelled", None),
            c("spoofed-instance-only", {INSTANCE_LABEL: "stack-a"}),
            c("managed-false", {MANAGED_LABEL: "false", INSTANCE_LABEL: "stack-a"}),
        ])
        sock = str(tmp_path / "d.sock")
        server = await asyncio.start_unix_server(fake.handle, sock)
        try:
            proxy = DockerSocketProxy(DockerApiPolicy(frozenset(), frozenset()), sock, instance="stack-a")
            settings = ReaperSettings(reap_after_s=420, interval_s=60, reap_unlabeled=reap_unlabeled)
            reaper = docker_proxy.Reaper(proxy, settings, clock=lambda: now)
            await reaper.reap_once()
        finally:
            server.close()
        assert sorted(set(fake.deleted)) == expected
