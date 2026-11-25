"""Wiring a Docker sandbox to the egress proxy (P5.4 §1.1): one internal network per sandbox.

Each sandbox with an egress allowlist gets its own ``--internal`` bridge in
isolated gateway mode (no host address on the bridge, so no route to the
host, the metadata address or anything else), a /29 from the broker's pool,
the proxy container connected at ``.2`` and the sandbox at ``.3``. The proxy
learns the binding (addresses, policy, labels) through its control plane
before the sandbox starts, and forgets it when the network goes.
"""

from __future__ import annotations

import asyncio
import ipaddress
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any, cast

from app.harness.sandbox.binding_proof import BindingAttester
from app.harness.sandbox.docker_policy import EGRESS_NETWORK_OPTIONS
from harness_core.logs import HarnessLog
from harness_core.sandbox.egress.admission import binding_of
from harness_core.sandbox.egress.bindings import (
    EGRESS_REVOKED,
    EGRESS_STOPPED,
    KILL_SWITCH,
)
from harness_core.sandbox.egress.sites import held_for_flow
from harness_core.sandbox.egress.types import EGRESS_PROXY_HOST
from harness_core.sandbox.egress.volume import run_of
from harness_core.sandbox.errors import (
    BrokerUnavailable,
    InvalidSandboxRequest,
    SandboxError,
    SandboxNotFound,
    SandboxRefusal,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from app.harness.sandbox.docker_client import DockerClientProvider
    from harness_core.clock import Clock
    from harness_core.sandbox.egress.addresses import EgressSubnet, SubnetPool
    from harness_core.sandbox.egress.client import RemoteEgressBindings
    from harness_core.sandbox.egress.types import EgressBinding
    from harness_core.sandbox.types import SandboxSpec

NETWORK_OPTIONS = EGRESS_NETWORK_OPTIONS
# Subnets tried before an exhausted or crowded pool is reported.
_MAX_ALLOCATION_ATTEMPTS = 64


@dataclass(frozen=True, slots=True)
class EgressAttachment:
    network: str
    subnet: EgressSubnet
    extra_hosts: Mapping[str, str]


@dataclass(frozen=True, slots=True)
class EgressLabels:
    managed: str
    sandbox_id: str
    created: str
    role: str


class DockerEgressWiring:
    def __init__(
        self,
        *,
        provider: DockerClientProvider,
        clock: Clock,
        proxy_container: str,
        bindings: RemoteEgressBindings,
        pool: SubnetPool,
        network_prefix: str,
        attester: BindingAttester | None = None,
    ) -> None:
        self._provider = provider
        self._clock = clock
        self.proxy_container = proxy_container
        self.bindings = bindings
        self.pool = pool
        self.network_prefix = network_prefix
        self._attester = attester or BindingAttester(None)
        self._labels: EgressLabels | None = None
        self._subnets: dict[str, str] = {}
        self._foreign: set[str] = set()
        self._held: dict[str, EgressBinding] = {}
        self._lock = asyncio.Lock()
        self._proxy_instance: str | None = None
        self._log = HarnessLog.named("app.harness.sandbox.egress")

    def bind_labels(self, labels: EgressLabels) -> None:
        self._labels = labels

    def network_name(self, sandbox_id: str) -> str:
        return f"{self.network_prefix}{sandbox_id}"

    def owns_network(self, name: str) -> bool:
        return name.startswith(self.network_prefix)

    def _api(self) -> Any:  # noqa: ANN401 - docker-py is untyped
        return self._provider.client.api

    async def _blocking(self, fn: Any, *args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
        return await self._provider.run_blocking(fn, *args, **kwargs)

    @property
    def labels(self) -> EgressLabels:
        if self._labels is None:
            raise SandboxError("egress wiring is not bound to the broker's labels")
        return self._labels

    async def load(self) -> None:
        """Subnets the broker's existing networks hold (a restart must not hand them out again)."""
        for info in await self._networks([f"{self.labels.managed}=1", f"{self.labels.role}=network"]):
            sandbox_id = cast("dict[str, str]", info.get("Labels") or {}).get(self.labels.sandbox_id, "")
            cidr = _subnet_of(info)
            if sandbox_id and cidr:
                self._subnets[sandbox_id] = cidr

    async def _daemon_subnets(self) -> set[str]:
        found = cast("list[dict[str, Any]]", await self._blocking(self._api().networks) or [])
        return {cidr for info in found if (cidr := _subnet_of(info)) and self.pool.contains_any(cidr)}

    async def _networks(self, label_filters: list[str]) -> list[dict[str, Any]]:
        found = await self._blocking(self._api().networks, filters={"label": label_filters})
        return cast("list[dict[str, Any]]", found or [])

    async def attach(self, sandbox_id: str, spec: SandboxSpec, labels: Mapping[str, str]) -> EgressAttachment:
        """Network, proxy endpoint and binding, in that order; undone if any step fails."""
        try:
            subnet = await self.prepare(sandbox_id, labels)
            _ = await self.bind(sandbox_id, spec)
        except BaseException:
            await asyncio.shield(self.detach(sandbox_id))
            raise
        return EgressAttachment(self.network_name(sandbox_id), subnet, hosts_for(spec, subnet))

    async def prepare(self, sandbox_id: str, labels: Mapping[str, str]) -> EgressSubnet:
        """Network and proxy endpoint with no binding: the proxy refuses the address until ``bind`` (P5.9a's
        warm sandboxes are prepared before any run). Undone if a step fails."""
        try:
            subnet = await self._create_network(sandbox_id, labels)
            await self._blocking(
                self._api().connect_container_to_network,
                self.proxy_container,
                self.network_name(sandbox_id),
                ipv4_address=subnet.proxy_address,
            )
        except BaseException:
            await asyncio.shield(self.detach(sandbox_id))
            raise
        return subnet

    async def bind(self, sandbox_id: str, spec: SandboxSpec) -> EgressSubnet:
        """The proxy learns the run's binding for a prepared network (this process's or another broker's)."""
        subnet = await self._subnet_for(sandbox_id)
        binding = self._attester.attest(binding_of(sandbox_id, subnet, spec))
        await self.bindings.register(binding)
        self._held[sandbox_id] = binding
        return subnet

    async def _subnet_for(self, sandbox_id: str) -> EgressSubnet:
        cidr = self._subnets.get(sandbox_id)
        if cidr is None:
            info = await self._blocking(self._api().inspect_network, self.network_name(sandbox_id))
            cidr = _subnet_of(cast("dict[str, Any]", info))
            if not cidr:
                raise SandboxError(f"egress network of {sandbox_id} has no subnet")
            self._subnets[sandbox_id] = cidr
        return self.pool.subnet(cidr)

    async def _create_network(self, sandbox_id: str, labels: Mapping[str, str]) -> EgressSubnet:
        from docker.types import IPAMConfig, IPAMPool

        async with self._lock:
            looked = False
            for _ in range(_MAX_ALLOCATION_ATTEMPTS):
                subnet = self.pool.allocate({*self._subnets.values(), *self._foreign})
                if subnet is None:
                    break
                try:
                    await self._blocking(
                        self._api().create_network,
                        self.network_name(sandbox_id),
                        driver="bridge",
                        internal=True,
                        options=NETWORK_OPTIONS,
                        ipam=IPAMConfig(pool_configs=[IPAMPool(subnet=subnet.cidr)]),
                        labels={**labels, self.labels.role: "network"},
                    )
                except Exception as exc:
                    if not is_pool_overlap(exc):
                        raise
                    # Another network on this shared daemon holds it; never ours to reuse.
                    self._foreign.add(subnet.cidr)
                    self._log.info("egress.subnet_taken_elsewhere", cidr=subnet.cidr)
                    if not looked:
                        # One overlap means the daemon holds pool addresses this broker did not make (another
                        # deployment's, or networks left behind): learn them all at once rather than one per try.
                        looked = True
                        self._foreign |= await self._daemon_subnets()
                    continue
                self._subnets[sandbox_id] = subnet.cidr
                return subnet
        raise BrokerUnavailable("the egress subnet pool is exhausted")

    async def reattach(self, sandbox_id: str, spec: SandboxSpec) -> None:
        """An adopted sandbox: its network survived the broker; tell the proxy again."""
        _ = self._subnets.pop(sandbox_id, None)
        _ = await self.bind(sandbox_id, spec)

    async def detach(self, sandbox_id: str) -> None:
        """Binding, proxy endpoint, network; each step idempotent, each failure logged and passed."""
        _ = self._held.pop(sandbox_id, None)
        try:
            await self.bindings.unregister(sandbox_id)
        except SandboxError as exc:
            # The next resync (or the next binding on this address) clears it; the network goes anyway.
            self._log.error("egress.unbind_failed", exc=exc, sandboxId=sandbox_id, errorType=type(exc).__name__)
        name = self.network_name(sandbox_id)
        await self._quietly(self._api().disconnect_container_from_network, self.proxy_container, name, force=True)
        await self._quietly(self._api().remove_network, name)
        _ = self._subnets.pop(sandbox_id, None)

    async def _quietly(self, fn: Any, *args: Any, **kwargs: Any) -> None:  # noqa: ANN401
        try:
            await self._blocking(fn, *args, **kwargs)
        except Exception as exc:
            if not _is_absent(exc):
                raise

    async def reap_orphans(self, label_filters: list[str], now: int, older_than_s: int, live: set[str]) -> int:
        """Networks whose sandbox is gone (a broker killed between network and container)."""
        removed = 0
        filters = [f"{self.labels.managed}=1", f"{self.labels.role}=network", *label_filters]
        for info in await self._networks(filters):
            labels = cast("dict[str, str]", info.get("Labels") or {})
            sandbox_id = labels.get(self.labels.sandbox_id, "")
            if not sandbox_id or sandbox_id in live:
                continue
            if now - int(labels.get(self.labels.created, "0") or 0) < older_than_s:
                continue
            await self.detach(sandbox_id)
            removed += 1
        return removed

    async def hold_flow(self, sandbox_id: str, *, run_id: str, hold: str) -> bool:
        """SEC-A-02 mid-turn (R38EGRESS-FU-1): the binding this broker holds is narrowed as the proxy narrows its own,
        before the proxy is told, so a proxy that restarts and cannot read its state is given the narrowed binding
        back by ``resync``, not the turn's first one. Narrows only; ``SandboxNotFound`` for a sandbox this broker
        does not bind, ``InvalidSandboxRequest`` for one that serves another run."""
        if hold not in ("held", "forbidden"):
            raise InvalidSandboxRequest("a flow hold is held or forbidden")
        held = self._held.get(sandbox_id)
        if held is None:
            raise SandboxNotFound(f"no egress binding for sandbox {sandbox_id}")
        if not run_id or run_of(held.labels) != run_id:
            raise InvalidSandboxRequest("the sandbox does not serve that run")
        destinations = held_for_flow(held.policy.destinations, forbidden=hold == "forbidden")
        if destinations != held.policy.destinations:
            self._held[sandbox_id] = replace(held, policy=replace(held.policy, destinations=destinations))
        await self.bindings.hold_flow(sandbox_id=sandbox_id, run_id=run_id, hold=hold)
        self._log.info("egress.flow_held", sandboxId=sandbox_id, hold=hold, narrowed=destinations != held.policy.destinations)
        return destinations != held.policy.destinations

    async def resync(self) -> bool:
        """Re-register every held binding after a proxy restart; drop bindings no sandbox holds."""
        health = await self.bindings.health()
        if health.instance == self._proxy_instance:
            return False
        refused = 0
        for held in list(self._held.values()):
            # Signed again: a SECRET_KEY rotation since the first registration leaves the old proof unverifiable.
            binding = self._attester.attest(held)
            try:
                await self.bindings.register(binding)
            except SandboxRefusal as exc:
                if exc.reason not in (EGRESS_REVOKED, EGRESS_STOPPED, KILL_SWITCH):
                    raise
                # An incident stop ended this sandbox's egress; the rest are still given back.
                refused += 1
                _ = self._held.pop(binding.sandbox_id, None)
                self._log.warning("egress.resync_refused", sandboxId=binding.sandbox_id, reasonCode=exc.reason)
        stale = await self.bindings.bound() - self._held.keys()
        for sandbox_id in stale:
            await self.bindings.unregister(sandbox_id)
        previous, self._proxy_instance = self._proxy_instance, health.instance
        self._log.info(
            "egress.resynced",
            instance=health.instance,
            previous=previous,
            bindings=len(self._held),
            dropped=len(stale),
            revoked=refused,
        )
        return True


def hosts_for(spec: SandboxSpec, subnet: EgressSubnet) -> dict[str, str]:
    """``/etc/hosts`` of a sandbox on ``subnet``: the proxy, and every named destination at the proxy."""
    hosts = {EGRESS_PROXY_HOST: subnet.proxy_address}
    for destination in spec.egress.destinations:
        name = destination.host.strip().lower().rstrip(".")
        if not _is_ip(name):
            hosts[name] = subnet.proxy_address
    return hosts


def _is_ip(value: str) -> bool:
    try:
        _ = ipaddress.ip_address(value)
    except ValueError:
        return False
    return True


def _subnet_of(info: Mapping[str, Any]) -> str:
    ipam = cast("dict[str, Any]", info.get("IPAM") or {})
    configs = cast("list[dict[str, str]]", ipam.get("Config") or [])
    return next((c["Subnet"] for c in configs if c.get("Subnet")), "")


def is_pool_overlap(exc: BaseException) -> bool:
    """Docker refused a network because another one on the daemon holds (part of) its subnet."""
    return "overlap" in str(exc).lower()


def _is_absent(exc: Exception) -> bool:
    status = getattr(getattr(exc, "response", None), "status_code", None)
    text = str(exc).lower()
    return status == 404 or "not found" in text or "is not connected" in text


__all__ = ["NETWORK_OPTIONS", "DockerEgressWiring", "EgressAttachment", "EgressLabels", "hosts_for", "is_pool_overlap"]
