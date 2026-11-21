"""The sandbox broker's profile of the Docker API policy proxy (R38IDENT-FU-1, SEC-D-02 fix 3, ADR-OPS-02).

The broker held the Docker socket itself (through its group), so a foothold in it was the host: one create with
``Privileged`` and a bind of ``/``, or an exec into the app container, which holds the platform's keys. Now only the
proxy (``app.sandbox.docker_proxy``) mounts the socket; the broker reaches it over TCP on a network of their own, with
its own credential (``DOCKER_PROXY_BROKER_TOKEN``), and every call is held to this profile, closed by default:

- ping, version and info; image inspect, pull (never an import) and registry data;
- container create, held to the broker's own rules (``app.harness.sandbox.docker_policy.HardeningPolicy``, the same
  object the broker checks itself with, so the two cannot drift) inside a closed HostConfig: no host path, device,
  namespace or capability, the sandbox floor, volumes only the broker's data volume or ones under its prefix, and the
  one root container the broker makes (its egress firewall helper) only with ``NET_ADMIN`` in the network namespace
  of a sandbox this install owns, running exactly the rules script ``build_firewall_rules_script`` writes;
- start, wait, kill, rename, logs, archives and removal only on the install's containers (its broker labels), and
  list filters forced to them; exec only on those, never privileged and never as root;
- named volumes only under the broker's prefix, with its labels, and only tmpfs driver options (a ``local`` volume's
  ``o=bind`` would be a host bind); the data volume is never removed;
- networks: the shared open-egress bridge on its pool, and per-sandbox internal bridges on the sandbox pool with the
  broker's labels; connect and disconnect only for the install's containers and the configured egress proxy.

A container the proxy admits carries ``STAMP_LABEL``. One the broker made before the proxy fronted it (a warm
sandbox across the upgrade) has no stamp; it is still the install's to inspect, stop and remove, and it is used
(exec, archives, start) only when its live configuration meets the same rules a create is held to.
"""

from __future__ import annotations

import ipaddress
import json
import os
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qs, quote, unquote, urlencode, urlsplit

from app.harness.sandbox.docker_policy import (
    DATA_VOLUME,
    DEFAULT_EGRESS_NETWORK,
    EGRESS_NETWORK_OPTIONS,
    LABEL_NAMESPACE,
    SANDBOX_USER,
    VOLUME_PREFIX,
    HardeningPolicy,
    owns_volume,
)
from app.harness.sandbox.images import DEFAULT_SANDBOX_IMAGE, SANDBOX_IMAGE_ENV
from app.sandbox.docker_proxy import (
    _CREATE_FIELDS,
    _DEFAULT_OPEN_EGRESS_POOL,
    _ENV_OPEN_EGRESS_SUBNETS,
    _HOST_CONFIG_FIELDS,
    _MOUNT_FIELDS,
    _VERSION_PREFIX,
    Absent,
    DockerApiPolicy,
    PolicyDenied,
    Route,
    _struct_object,
    check_ipam,
    labelled,
    refuse_host_reach,
    removing_volumes,
    with_container_id,
)
from app.sandbox.sandbox_floor import root_user
from harness_core.sandbox.egress.addresses import DEFAULT_SUBNET_POOL
from harness_core.sandbox.errors import SandboxPolicyViolation

if TYPE_CHECKING:
    from collections.abc import Mapping

    from app.sandbox.docker_proxy import Daemon

STAMP_LABEL = "ai.pipeshub.docker-proxy.install"

_ENV_INSTALL_ID = "SANDBOX_INSTALL_ID"
_ENV_COMPOSE_PROJECT = "COMPOSE_PROJECT_NAME"
_ENV_DATA_VOLUME = "SANDBOX_DATA_VOLUME"
_ENV_EGRESS_NETWORK = "SANDBOX_EGRESS_NETWORK"
_ENV_PROXY_CONTAINER = "SANDBOX_EGRESS_PROXY_CONTAINER"
_ENV_SANDBOX_SUBNETS = "SANDBOX_EGRESS_SUBNETS"

# What the broker's create_host_config calls send beyond the legacy executor's: its ulimits (no core dumps, the
# workspace's file size), the proxied sandbox's hosts file and empty resolver, and its disk write cap (SEC-A-03).
_BROKER_HOST_CONFIG_FIELDS = _HOST_CONFIG_FIELDS | {"Ulimits", "ExtraHosts", "Dns", "BlkioDeviceWriteBps"}
_ULIMIT_FIELDS = frozenset({"Name", "Soft", "Hard"})
_THROTTLE_FIELDS = frozenset({"Path", "Rate"})
_BROKER_VOLUME_OPTION_FIELDS = frozenset({"NoCopy", "Labels", "Subpath"})
_TMPFS_OPTION_FIELDS = frozenset({"SizeBytes", "Mode"})
_ENDPOINT_FIELDS = frozenset({"IPAMConfig", "Aliases", "MacAddress"})
_ENDPOINT_IPAM_FIELDS = frozenset({"IPv4Address"})
_EXEC_FIELDS = frozenset({
    "Container", "User", "Privileged", "Tty", "AttachStdin", "AttachStdout", "AttachStderr", "Cmd", "Env",
    "WorkingDir", "DetachKeys", "ConsoleSize",
})
_VOLUME_CREATE_FIELDS = frozenset({"Name", "Driver", "DriverOpts", "Labels"})
_NETWORK_CREATE_FIELDS = frozenset({
    "Name", "Driver", "Options", "IPAM", "CheckDuplicate", "Internal", "Labels", "EnableIPv6", "Attachable",
    "Ingress", "Scope", "ConfigOnly", "ConfigFrom",
})
_CONNECT_FIELDS = frozenset({"Container", "EndpointConfig"})
_DISCONNECT_FIELDS = frozenset({"Container", "Force"})
# A scratch volume is a size-capped tmpfs: these are the only mount options its ``o`` may carry.
_TMPFS_VOLUME_OPTIONS = frozenset({"size", "nr_inodes", "uid", "gid", "mode"})
_OPTION_VALUE = re.compile(r"[0-9A-Za-z]+")
_CREATE_QUERY = frozenset({"name", "platform"})
_OWNED_ACTIONS = frozenset({("GET", "/json"), ("POST", "/kill"), ("POST", "/wait"), ("DELETE", ""), ("GET", "/logs")})
_TRUSTED_ACTIONS = frozenset({
    ("POST", "/start"), ("POST", "/rename"), ("GET", "/archive"), ("HEAD", "/archive"), ("PUT", "/archive"),
})


class InstallIdUnset(RuntimeError):
    reason_code = "install_id_unset"


@dataclass(frozen=True)
class BrokerScope:
    """Whose containers, volumes and networks the broker's calls may touch: one install's, on a daemon that may serve
    several. Read from the same variables the broker reads (``app.sandbox_broker_main``)."""

    install: str
    namespace: str = LABEL_NAMESPACE
    volume_prefix: str = VOLUME_PREFIX
    data_volume: str = DATA_VOLUME
    egress_network: str = DEFAULT_EGRESS_NETWORK
    proxy_container: str = ""
    sandbox_pool: ipaddress.IPv4Network = ipaddress.IPv4Network(DEFAULT_SUBNET_POOL)
    open_egress_pool: ipaddress.IPv4Network = ipaddress.IPv4Network(_DEFAULT_OPEN_EGRESS_POOL)
    # The only image the egress firewall's root helper may run: the broker's own sandbox image (R44STOP-FU-3).
    firewall_image: str = DEFAULT_SANDBOX_IMAGE

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> BrokerScope:
        install = (env.get(_ENV_INSTALL_ID) or "").strip() or (env.get(_ENV_COMPOSE_PROJECT) or "").strip()
        if not install:
            raise InstallIdUnset(f"set {_ENV_INSTALL_ID} to the sandbox broker's install id (one per Docker daemon)")
        return cls(
            install=install,
            data_volume=(env.get(_ENV_DATA_VOLUME) or "").strip() or DATA_VOLUME,
            egress_network=(env.get(_ENV_EGRESS_NETWORK) or "").strip() or DEFAULT_EGRESS_NETWORK,
            proxy_container=(env.get(_ENV_PROXY_CONTAINER) or "").strip(),
            sandbox_pool=_ipv4_pool(env.get(_ENV_SANDBOX_SUBNETS), DEFAULT_SUBNET_POOL, _ENV_SANDBOX_SUBNETS),
            open_egress_pool=_ipv4_pool(
                env.get(_ENV_OPEN_EGRESS_SUBNETS), _DEFAULT_OPEN_EGRESS_POOL, _ENV_OPEN_EGRESS_SUBNETS
            ),
            firewall_image=(env.get(SANDBOX_IMAGE_ENV) or "").strip() or DEFAULT_SANDBOX_IMAGE,
        )

    @property
    def network_prefix(self) -> str:
        return f"{self.volume_prefix}-net-"

    def owns(self, labels: object) -> bool:
        """Whether ``labels`` are this install's broker labels."""
        if not isinstance(labels, dict):
            return False
        return labels.get(f"{self.namespace}.managed") == "1" and labels.get(f"{self.namespace}.install") == self.install

    def owns_volume(self, name: str) -> bool:
        return owns_volume(name, data_volume=self.data_volume, volume_prefix=self.volume_prefix)

    def owned_filters(self) -> list[str]:
        return [f"{self.namespace}.managed=1", f"{self.namespace}.install={self.install}"]


@dataclass
class BrokerApiPolicy:
    scope: BrokerScope
    hardening: HardeningPolicy = field(init=False)
    _open_egress: DockerApiPolicy = field(init=False)

    def __post_init__(self) -> None:
        self.hardening = HardeningPolicy(
            user=SANDBOX_USER,
            allowed_networks=frozenset({self.scope.egress_network}),
            volume_allowed=self.scope.owns_volume,
            network_allowed=lambda name: name.startswith(self.scope.network_prefix),
            firewall_image=self.scope.firewall_image,
        )
        # The shared open-egress bridge has one shape whoever asks for it: the legacy profile's network rules.
        self._open_egress = DockerApiPolicy(
            allowed_images=frozenset(),
            allowed_networks=frozenset({self.scope.egress_network}),
            egress_pool=self.scope.open_egress_pool,
        )

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> BrokerApiPolicy:
        return cls(BrokerScope.from_env(os.environ if env is None else env))

    @property
    def stamp(self) -> dict[str, str]:
        return {STAMP_LABEL: self.scope.install}

    def route(self, method: str, target: str) -> Route:
        """Classify a request. Raises ``PolicyDenied`` for anything unlisted."""
        path = _VERSION_PREFIX.sub("", urlsplit(target).path)
        if (method, path) in {("GET", "/_ping"), ("HEAD", "/_ping"), ("GET", "/version"), ("GET", "/info")}:
            return Route("allow")
        if path == "/containers/json" and method == "GET":
            return Route("container_list")
        if path == "/containers/create" and method == "POST":
            return Route("container_create", reads_body=True)
        m = re.fullmatch(r"/containers/([^/]+)(/[a-z]+)?", path)
        if m:
            ident, action = unquote(m.group(1)), m.group(2) or ""
            if (method, action) in _OWNED_ACTIONS:
                return Route("container_owned", container=ident)
            if (method, action) in _TRUSTED_ACTIONS:
                return Route("container_trusted", container=ident)
            if (method, action) == ("POST", "/exec"):
                return Route("exec_create", container=ident, reads_body=True)
            raise PolicyDenied(f"{method} /containers/{{id}}{action} is not permitted")
        m = re.fullmatch(r"/exec/([^/]+)/(start|json)", path)
        if m and (method, m.group(2)) in {("POST", "start"), ("GET", "json")}:
            start = m.group(2) == "start"
            return Route("exec_start" if start else "exec_inspect", ident=unquote(m.group(1)), hijack=start)
        if re.fullmatch(r"/(?:images|distribution)/.+/json", path) and method == "GET":
            return Route("allow")
        if path == "/images/create" and method == "POST":
            query = parse_qs(urlsplit(target).query)
            if "fromSrc" in query or "repo" in query or not query.get("fromImage"):
                raise PolicyDenied("only image pulls are permitted")
            return Route("allow")
        if path == "/volumes" and method == "GET":
            return Route("volume_list")
        if path == "/volumes/create" and method == "POST":
            return Route("volume_create", reads_body=True)
        m = re.fullmatch(r"/volumes/([^/]+)", path)
        if m and method in ("GET", "DELETE"):
            return Route("volume_owned", ident=unquote(m.group(1)))
        if path == "/networks" and method == "GET":
            return Route("allow")
        if path == "/networks/create" and method == "POST":
            return Route("network_create", reads_body=True)
        m = re.fullmatch(r"/networks/([^/]+)(/connect|/disconnect)?", path)
        if m and (method in ("GET", "DELETE") and not m.group(2) or method == "POST" and m.group(2)):
            kind = {None: "network_owned", "/connect": "network_connect", "/disconnect": "network_disconnect"}
            return Route(kind[m.group(2)], network=unquote(m.group(1)), reads_body=m.group(2) is not None)
        raise PolicyDenied(f"{method} {path} is not permitted")

    async def admit(
        self, route: Route, method: str, target: str, payload: object, daemon: Daemon,
    ) -> tuple[str, Any]:
        if route.kind == "allow":
            return target, None
        if route.kind in ("container_list", "volume_list"):
            return self._own_filters(target), None
        if route.kind == "container_create":
            return target, await self._check_create(_query(target), payload, daemon)
        if route.kind in ("container_owned", "container_trusted"):
            assert route.container is not None
            info = await self._container(daemon, route.container)
            if not (self._trusted(info) if route.kind == "container_trusted" else self.scope.owns(_labels(info))):
                raise PolicyDenied("container is not this install's sandbox")
            target = with_container_id(target, str(info["Id"]))
            return self._container_call(method, target), None
        if route.kind == "exec_create":
            assert route.container is not None
            info = await self._container(daemon, route.container)
            if not self._trusted(info):
                raise PolicyDenied("container is not this install's sandbox")
            return with_container_id(target, str(info["Id"])), self._check_exec(payload, str(info["Id"]))
        if route.kind in ("exec_start", "exec_inspect"):
            assert route.ident is not None
            await self._check_exec_instance(daemon, route.ident, start=route.kind == "exec_start")
            return target, None
        if route.kind == "volume_create":
            return target, self._check_volume_create(payload)
        if route.kind == "volume_owned":
            assert route.ident is not None
            await self._check_volume(daemon, route.ident)
            return target, None
        if route.kind == "network_create":
            return target, self._check_network_create(payload)
        if route.kind == "network_owned":
            assert route.network is not None
            info = await self._network(daemon, route.network)
            if info.get("Name") != self.scope.egress_network and not self._owned_network(info):
                raise PolicyDenied("network is not this install's sandbox network")
            return target, None
        assert route.network is not None
        connect = route.kind == "network_connect"
        return target, await self._check_attachment(daemon, route.network, payload, connect=connect)

    def _own_filters(self, target: str) -> str:
        """A list call narrowed to this install's objects, whatever filters it asked for (they still apply)."""
        split = urlsplit(target)
        query = {k: v[-1] for k, v in parse_qs(split.query).items()}
        try:
            filters = json.loads(query.get("filters") or "{}")
        except ValueError:
            raise PolicyDenied("list filters are not valid JSON") from None
        if not isinstance(filters, dict):
            raise PolicyDenied("list filters must be an object")
        labels = filters.get("label") or []
        if isinstance(labels, dict):
            labels = [key for key, on in labels.items() if on]
        if not isinstance(labels, list):
            raise PolicyDenied("the label filter must be a list")
        filters["label"] = list(dict.fromkeys([*(str(label) for label in labels), *self.scope.owned_filters()]))
        query["filters"] = json.dumps(filters)
        return f"{split.path}?{urlencode(query)}"

    def _container_call(self, method: str, target: str) -> str:
        split = urlsplit(target)
        query = parse_qs(split.query)
        if method == "DELETE":
            if any(value.lower() in ("1", "true") for value in query.get("link", [])):
                raise PolicyDenied("removing a link is not permitted")
            return removing_volumes(target)
        if split.path.endswith("/rename"):
            name = (query.get("name") or [""])[-1]
            if not name.startswith(self.scope.volume_prefix + "-"):
                raise PolicyDenied(f"a sandbox may only be named under {self.scope.volume_prefix!r}")
        return target

    async def _container(self, daemon: Daemon, ident: str) -> dict[str, Any]:
        status, info = await daemon.json(f"/containers/{quote(ident, safe='')}/json")
        if status == 404:
            raise Absent(f"No such container: {ident}")
        if status != 200 or not isinstance(info, dict) or not info.get("Id"):
            raise PolicyDenied("the container could not be inspected")
        return info

    def _trusted(self, info: dict[str, Any]) -> bool:
        """The install's container, admitted by this proxy or, made before the proxy fronted the broker, one whose
        live configuration meets the rules a create is held to now."""
        labels = _labels(info)
        if not self.scope.owns(labels):
            return False
        if labels.get(STAMP_LABEL) == self.scope.install:
            return True
        config = info.get("Config") or {}
        host = info.get("HostConfig") or {}
        if not isinstance(config, dict) or not isinstance(host, dict):
            return False
        return not self.hardening.violations(
            image=str(config.get("Image") or ""), user=str(config.get("User") or ""), host_config=host
        )

    async def _check_create(self, query: dict[str, list[str]], body: object, daemon: Daemon) -> dict[str, Any]:
        extra = set(query) - _CREATE_QUERY
        if extra:
            raise PolicyDenied(f"create parameters {sorted(extra)} are not permitted")
        name = (query.get("name") or [""])[-1]
        if name and not name.startswith(self.scope.volume_prefix + "-"):
            raise PolicyDenied(f"a sandbox may only be named under {self.scope.volume_prefix!r}")
        if not isinstance(body, dict):
            raise PolicyDenied("container create body must be a JSON object")
        _struct_object(body, _CREATE_FIELDS, "container create body", closed=True)
        for key in ("Volumes", "Runtime"):
            if body.get(key):
                raise PolicyDenied(f"{key} is not permitted")
        host = _struct_object(body.get("HostConfig"), _BROKER_HOST_CONFIG_FIELDS, "HostConfig", closed=True)
        refuse_host_reach(host)
        for raw in host.get("Mounts") or []:
            self._check_mount(raw)
        for raw in host.get("Ulimits") or []:
            _struct_object(raw, _ULIMIT_FIELDS, "HostConfig.Ulimits[]", closed=True)
        for raw in host.get("BlkioDeviceWriteBps") or []:
            _struct_object(raw, _THROTTLE_FIELDS, "HostConfig.BlkioDeviceWriteBps[]", closed=True)
        image = str(body.get("Image") or "")
        user = body.get("User")
        network = host.get("NetworkMode") or ""
        cap_add = host.get("CapAdd") or []
        try:
            if cap_add or root_user(user):
                sandbox = str(network).removeprefix("container:")
                if not str(network).startswith("container:") or not self._trusted(await self._container(daemon, sandbox)):
                    raise PolicyDenied("a root container is only the egress firewall, in an install's sandbox")
                self.hardening.check_firewall_helper(
                    image=image,
                    user=str(user),
                    host_config=host,
                    sandbox_container=sandbox,
                    command=body.get("Cmd"),
                    entrypoint=body.get("Entrypoint"),
                    env=body.get("Env"),
                )
            else:
                self.hardening.check(image=image, user=str(user), host_config=host)
        except SandboxPolicyViolation as exc:
            raise PolicyDenied(f"the broker's rules are not met: {exc}") from None
        if isinstance(network, str) and network.startswith(self.scope.network_prefix):
            await self._owned_network_named(daemon, network)
        networking = _struct_object(body.get("NetworkingConfig"), frozenset({"EndpointsConfig"}), "NetworkingConfig")
        endpoints = networking.get("EndpointsConfig") or {}
        if not isinstance(endpoints, dict):
            raise PolicyDenied("NetworkingConfig.EndpointsConfig must be an object")
        for endpoint_network, endpoint in endpoints.items():
            if not self.hardening.network_ok(endpoint_network):
                raise PolicyDenied(f"network {endpoint_network!r} is not permitted")
            if endpoint_network.startswith(self.scope.network_prefix):
                await self._owned_network_named(daemon, endpoint_network)
            _check_endpoint(endpoint)
        if not self.scope.owns(body.get("Labels")):
            raise PolicyDenied("a sandbox carries its install's broker labels")
        return labelled(body, host, self.stamp)

    def _check_mount(self, raw: object) -> None:
        mount = _struct_object(raw, _MOUNT_FIELDS, "HostConfig.Mounts[]", closed=True)
        if mount.get("Type") == "tmpfs":
            _struct_object(mount.get("TmpfsOptions"), _TMPFS_OPTION_FIELDS, "HostConfig.Mounts[].TmpfsOptions", closed=True)
            return
        if mount.get("Type") != "volume":
            raise PolicyDenied("only tmpfs mounts and the broker's volumes are permitted")
        source = mount.get("Source")
        if not isinstance(source, str) or not self.scope.owns_volume(source):
            raise PolicyDenied(f"volume {source!r} is not one of the broker's")
        options = _struct_object(
            mount.get("VolumeOptions"), _BROKER_VOLUME_OPTION_FIELDS, "HostConfig.Mounts[].VolumeOptions", closed=True
        )
        subpath = options.get("Subpath")
        if subpath is not None and (
            not isinstance(subpath, str) or subpath.startswith("/") or ".." in subpath.split("/")
        ):
            raise PolicyDenied("a volume subpath stays inside its volume")

    def _check_exec(self, body: object, container_id: str) -> dict[str, Any]:
        if not isinstance(body, dict):
            raise PolicyDenied("exec create body must be a JSON object")
        _struct_object(body, _EXEC_FIELDS, "exec create body", closed=True)
        if body.get("Privileged"):
            raise PolicyDenied("a privileged exec is not permitted")
        if body.get("User") != SANDBOX_USER:
            raise PolicyDenied("an exec runs as the sandbox user")
        return {**body, "Container": container_id} if "Container" in body else body

    async def _check_exec_instance(self, daemon: Daemon, exec_id: str, *, start: bool) -> None:
        status, info = await daemon.json(f"/exec/{quote(exec_id, safe='')}/json")
        if status == 404:
            raise Absent(f"No such exec instance: {exec_id}")
        if status != 200 or not isinstance(info, dict):
            raise PolicyDenied("the exec could not be inspected")
        process = info.get("ProcessConfig") or {}
        if start and (process.get("privileged") or root_user(process.get("user"))):
            raise PolicyDenied("a privileged or root exec is not permitted")
        container = await self._container(daemon, str(info.get("ContainerID") or ""))
        if not (self._trusted(container) if start else self.scope.owns(_labels(container))):
            raise PolicyDenied("the exec is not in this install's sandbox")

    def _check_volume_create(self, body: object) -> dict[str, Any]:
        if not isinstance(body, dict):
            raise PolicyDenied("volume create body must be a JSON object")
        _struct_object(body, _VOLUME_CREATE_FIELDS, "volume create body", closed=True)
        name = body.get("Name")
        if not isinstance(name, str) or not name.startswith(self.scope.volume_prefix + "-") \
                or name == self.scope.data_volume:
            raise PolicyDenied(f"a volume is named under {self.scope.volume_prefix!r}")
        if (body.get("Driver") or "local") != "local":
            raise PolicyDenied("only local volumes are permitted")
        opts = body.get("DriverOpts") or {}
        if opts and not _tmpfs_options(opts):
            raise PolicyDenied("a volume's only driver options are a size-capped tmpfs")
        if not self.scope.owns(body.get("Labels")):
            raise PolicyDenied("a volume carries its install's broker labels")
        return {**body, "Labels": {**body["Labels"], **self.stamp}}

    async def _check_volume(self, daemon: Daemon, name: str) -> None:
        status, info = await daemon.json(f"/volumes/{quote(name, safe='')}")
        if status == 404:
            raise Absent(f"get {name}: no such volume")
        if status != 200 or not isinstance(info, dict):
            raise PolicyDenied("the volume could not be inspected")
        found = str(info.get("Name") or "")
        if found == self.scope.data_volume or not found.startswith(self.scope.volume_prefix + "-") \
                or not self.scope.owns(info.get("Labels")):
            raise PolicyDenied("volume is not this install's")

    def _check_network_create(self, body: object) -> dict[str, Any]:
        if not isinstance(body, dict):
            raise PolicyDenied("network create body must be a JSON object")
        name = body.get("Name")
        if name == self.scope.egress_network:
            return self._open_egress.check_network_create(body)
        if not isinstance(name, str) or not name.startswith(self.scope.network_prefix):
            raise PolicyDenied(f"creating network {name!r} is not permitted")
        _struct_object(body, _NETWORK_CREATE_FIELDS, "network create body", closed=True)
        if (body.get("Driver") or "bridge") != "bridge":
            raise PolicyDenied("only bridge networks are permitted")
        if body.get("Internal") is not True:
            raise PolicyDenied("a sandbox network is internal")
        if (body.get("Options") or {}) != EGRESS_NETWORK_OPTIONS:
            raise PolicyDenied("a sandbox network has exactly the broker's bridge options")
        for key in ("EnableIPv6", "Attachable", "Ingress", "Scope", "ConfigOnly", "ConfigFrom"):
            if body.get(key):
                raise PolicyDenied(f"network {key} is not permitted")
        check_ipam(body.get("IPAM"), self.scope.sandbox_pool)
        if not self.scope.owns(body.get("Labels")):
            raise PolicyDenied("a sandbox network carries its install's broker labels")
        return {**body, "Labels": {**body["Labels"], **self.stamp}}

    async def _network(self, daemon: Daemon, ident: str) -> dict[str, Any]:
        status, info = await daemon.json(f"/networks/{quote(ident, safe='')}")
        if status == 404:
            raise Absent(f"network {ident} not found")
        if status != 200 or not isinstance(info, dict):
            raise PolicyDenied("the network could not be inspected")
        return info

    def _owned_network(self, info: dict[str, Any]) -> bool:
        return str(info.get("Name") or "").startswith(self.scope.network_prefix) and self.scope.owns(info.get("Labels"))

    async def _owned_network_named(self, daemon: Daemon, name: str) -> None:
        if not self._owned_network(await self._network(daemon, name)):
            raise PolicyDenied(f"network {name!r} is not this install's sandbox network")

    async def _check_attachment(
        self, daemon: Daemon, network: str, body: object, *, connect: bool
    ) -> dict[str, Any]:
        if not isinstance(body, dict):
            raise PolicyDenied("network attachment body must be a JSON object")
        _struct_object(body, _CONNECT_FIELDS if connect else _DISCONNECT_FIELDS, "network attachment", closed=True)
        info = await self._network(daemon, network)
        container = await self._container(daemon, str(body.get("Container") or ""))
        proxy = bool(self.scope.proxy_container) and str(container.get("Name") or "").lstrip("/") == self.scope.proxy_container
        if self._owned_network(info):
            allowed = proxy or self._trusted(container)
        else:
            # The builtin "none" network: a warm sandbox leaves it to join its own network at bind.
            allowed = not connect and info.get("Name") == "none" and self._trusted(container)
        if not allowed:
            raise PolicyDenied("only this install's sandboxes and its egress proxy join its sandbox networks")
        if connect:
            _check_endpoint(body.get("EndpointConfig"))
        return {**body, "Container": str(container["Id"])}


def _check_endpoint(raw: object) -> None:
    endpoint = _struct_object(raw, _ENDPOINT_FIELDS, "endpoint config", closed=True)
    _struct_object(endpoint.get("IPAMConfig"), _ENDPOINT_IPAM_FIELDS, "endpoint IPAMConfig", closed=True)


def _tmpfs_options(opts: object) -> bool:
    if not isinstance(opts, dict) or set(opts) != {"type", "device", "o"}:
        return False
    if opts["type"] != "tmpfs" or opts["device"] != "tmpfs" or not isinstance(opts["o"], str):
        return False
    for option in opts["o"].split(","):
        key, sep, value = option.partition("=")
        if not sep or key not in _TMPFS_VOLUME_OPTIONS or not _OPTION_VALUE.fullmatch(value):
            return False
    return True


def _labels(info: dict[str, Any]) -> dict[str, Any]:
    config = info.get("Config") or {}
    labels = config.get("Labels") if isinstance(config, dict) else None
    return labels if isinstance(labels, dict) else {}


def _query(target: str) -> dict[str, list[str]]:
    return parse_qs(urlsplit(target).query, keep_blank_values=True)


def _ipv4_pool(raw: str | None, default: str, name: str) -> ipaddress.IPv4Network:
    pool = ipaddress.ip_network((raw or "").strip() or default, strict=True)
    if not isinstance(pool, ipaddress.IPv4Network):
        raise ValueError(f"{name} must be an IPv4 network")
    return pool


__all__ = ["STAMP_LABEL", "BrokerApiPolicy", "BrokerScope", "InstallIdUnset"]
