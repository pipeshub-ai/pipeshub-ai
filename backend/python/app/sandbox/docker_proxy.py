"""Policy-enforcing proxy between the Docker daemon and everything that starts sandboxes.

Whatever holds ``/var/run/docker.sock`` is root on the host: one ``POST
/containers/create`` with ``Privileged`` and a bind of ``/`` is enough. This
proxy is the only process that touches the socket. Its callers reach it over
TCP (``DOCKER_HOST=tcp://docker-socket-proxy:2375``) and the Docker SDK works
unchanged, but each caller's credential holds it to a profile: the sandbox
broker's (``DOCKER_PROXY_BROKER_TOKEN``, ``app.sandbox.docker_proxy_broker``,
R38IDENT-FU-1) and, while an install still runs ``SANDBOX_MODE=docker``, the
legacy executor's (``DOCKER_PROXY_TOKEN``, ``DockerApiPolicy`` below). The legacy
profile lets through only the calls the coding sandbox makes:

- ping / version;
- create a container from an allowed sandbox image with no host access
  (no privileged mode, binds, devices, host namespaces, port publishing,
  unconfined security profiles, or capabilities beyond the egress firewall's);
- start / wait / kill / stop / logs / archive / inspect / remove, only on
  containers this proxy created (it stamps a label at create time);
- inspect / pull of the allowed sandbox images;
- list networks, and create / inspect / remove the sandbox egress network
  (the egress firewall recreates it when it predates its required options),
  on a subnet of ``SANDBOX_OPEN_EGRESS_SUBNETS`` or the daemon's choice.

Path-only proxies cannot express the create rules: they allow
``/containers/create`` or they don't. That is why this one reads the body.

SEC-A-04: every create must also have the floor the sandbox broker holds its
own containers to (``app.sandbox.sandbox_floor``: limits on pids, memory and
CPU with no swap, a read-only root, every capability dropped,
``no-new-privileges``, a non-root user), the firewall's install step being the
one root container, and only with the firewall script. Writable directories
are tmpfs or anonymous volumes, removed with their container. And every call
but ``/_ping`` and ``/version`` carries the caller's credential
(``Authorization: Bearer``, ``caller_token``): reaching ``sandbox-control`` is
not enough to create a container.

The transport and the legacy profile are stdlib only, so the legacy proxy starts
without importing the app's dependencies; the broker's profile is imported only
when its credential is configured. Run with ``python -m app.docker_proxy_main``.
"""

from __future__ import annotations

import asyncio
import contextlib
import hmac
import ipaddress
import json
import logging
import os
import re
import signal
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol
from urllib.parse import parse_qs, quote, unquote, urlencode, urlsplit

if TYPE_CHECKING:
    from collections.abc import Mapping

from app.sandbox.sandbox_floor import (
    FIREWALL_SETUP_CAPS,
    firewall_install,
    floor_violations,
    root_user,
)
from app.utils.security_profile import hardened_profile

logger = logging.getLogger("docker_proxy")

MANAGED_LABEL = "ai.pipeshub.docker-proxy.managed"

_DEFAULT_SANDBOX_IMAGE = "pipeshub/sandbox:latest"
_DEFAULT_EGRESS_NETWORK = "pipeshub_sandbox_egress"

# The egress firewall (agent_loop_lib/sandbox/coding/egress.py) installs
# iptables rules as root inside the container's own network namespace, then
# drops to an unprivileged user. Nothing here reaches the host: NET_ADMIN is
# scoped to the container's netns because host networking is refused. Only its
# install step adds them (``sandbox_floor.firewall_install``).
_ALLOWED_CAPS = FIREWALL_SETUP_CAPS
# The caller's credential (SEC-A-04): ``DOCKER_PROXY_TOKEN``, which install.sh generates and gives the app and this
# proxy alike. The proxy holds the Docker socket, so it never holds the platform's root key (ADR-KEY-02).
ENV_CALLER_TOKEN = "DOCKER_PROXY_TOKEN"
# The sandbox broker's credential (R38IDENT-FU-1): the broker reaches the daemon only through this proxy, held to its
# own profile, so a foothold in the broker is not the host.
ENV_BROKER_CALLER_TOKEN = "DOCKER_PROXY_BROKER_TOKEN"
# By value or by file (R38IDENT-FU-8): kept literal, the proxy starts without the app's dependencies.
_PLATFORM_KEYS = (
    "SECRET_KEY", "SECRET_KEY_PREVIOUS", "SECRET_KEY_NEXT",
    "SECRET_KEY_FILE", "SECRET_KEY_PREVIOUS_FILE", "SECRET_KEY_NEXT_FILE",
)
_OPEN_PATHS = frozenset({"/_ping", "/version"})
_ALLOWED_SECURITY_OPTS = frozenset(
    {"no-new-privileges", "no-new-privileges:true", "no-new-privileges=true"}
)
_ALLOWED_NETWORK_OPTIONS = frozenset({"com.docker.network.bridge.enable_icc"})
_ALLOWED_RUNTIMES = frozenset({"", "runc", "runsc"})

_ALLOWED_LOG_DRIVERS = frozenset({"json-file", "local"})

# Docker decodes bodies with Go's encoding/json, which matches struct fields
# case-insensitively, while the checks below read exact keys. So in every object
# Docker decodes into a struct, the fields the policy reads must be spelled
# exactly and no two keys may differ only in case.
#
# The create body and its HostConfig are also closed: any other key is refused.
# Docker has far more host-reaching fields than a deny-list can track (LogConfig
# with a network log driver, Cgroup, Isolation, ...), and daemons up to v26 copy
# some top-level fields such as VolumeDriver into HostConfig. The lists are what
# docker-py sends for the sandbox's containers.create calls, plus the fields the
# checks below validate.
_CREATE_FIELDS = frozenset({
    "Image", "HostConfig", "NetworkingConfig", "Labels",
    "Hostname", "Domainname", "User", "AttachStdin", "AttachStdout", "AttachStderr",
    "ExposedPorts", "Tty", "OpenStdin", "StdinOnce", "Env", "Cmd", "Healthcheck",
    "Volumes", "WorkingDir", "Entrypoint", "NetworkDisabled", "MacAddress",
    "StopSignal", "StopTimeout", "Runtime",
})
_HOST_CONFIG_FIELDS = frozenset({
    "Privileged", "Binds", "VolumesFrom", "Devices", "DeviceRequests",
    "DeviceCgroupRules", "Links", "Sysctls", "PortBindings", "VolumeDriver",
    "PublishAllPorts", "CgroupParent", "MaskedPaths", "ReadonlyPaths", "Mounts",
    "CapAdd", "SecurityOpt", "PidMode", "UTSMode", "UsernsMode", "IpcMode",
    "CgroupnsMode", "Runtime", "RestartPolicy", "NetworkMode", "LogConfig",
    "CapDrop", "Memory", "MemorySwap", "NanoCpus", "PidsLimit", "ReadonlyRootfs", "Tmpfs",
    "StorageOpt", "Init",
})
_LOG_CONFIG_FIELDS = frozenset({"Type", "Config"})
_RESTART_POLICY_FIELDS = frozenset({"Name"})
_MOUNT_FIELDS = frozenset({"Type", "Target", "Source", "ReadOnly", "Consistency", "VolumeOptions", "TmpfsOptions"})
# An anonymous volume (a writable directory under a read-only root): no source, no driver, no subpath.
_VOLUME_OPTION_FIELDS = frozenset({"NoCopy", "Labels"})
_NETWORKING_CONFIG_FIELDS = frozenset({"EndpointsConfig"})
_NETWORK_CREATE_FIELDS = frozenset({
    "Name", "Driver", "Options", "IPAM", "ConfigFrom", "ConfigOnly", "Ingress", "Scope",
    "EnableIPv6",
})
_IPAM_FIELDS = frozenset({"Driver", "Config", "Options"})
_IPAM_POOL_FIELDS = frozenset({"Subnet", "IPRange", "Gateway", "AuxiliaryAddresses"})
_ENV_OPEN_EGRESS_SUBNETS = "SANDBOX_OPEN_EGRESS_SUBNETS"
# egress_firewall.DEFAULT_OPEN_EGRESS_POOL, repeated because this module imports only the stdlib; a test keeps
# the two equal.
_DEFAULT_OPEN_EGRESS_POOL = "10.212.0.0/16"

_MAX_HEAD_BYTES = 64 * 1024
_MAX_POLICY_BODY_BYTES = 1024 * 1024
_HEAD_TIMEOUT_S = 30.0
_COPY_CHUNK = 64 * 1024

_VERSION_PREFIX = re.compile(r"^/v\d+(?:\.\d+)?(?=/)")
_CONTAINER_IN_TARGET = re.compile(r"^((?:/v\d+(?:\.\d+)?)?/containers/)[^/?]+")
_TOKEN = re.compile(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+")
_HTTP_VERSION = re.compile(r"HTTP/1\.[01]")
_TARGET_FORBIDDEN = re.compile(r"[\x00-\x20\x7f]")
_VALUE_FORBIDDEN = re.compile(r"[\x00-\x08\x0a-\x1f\x7f]")
_HOP_BY_HOP = frozenset(
    {"connection", "keep-alive", "proxy-connection", "proxy-authorization",
     "te", "trailer", "upgrade", "expect"}
)


class PolicyDenied(Exception):
    """Request refused by policy; the message is returned to the client."""


def _struct_object(
    obj: object, fields: frozenset[str], where: str, *, closed: bool = False,
) -> dict[str, Any]:
    """Return ``obj`` as a dict Docker will decode exactly as the policy reads it.

    ``closed`` also refuses any key that is not one of ``fields``.
    """
    if obj is None:
        return {}
    if not isinstance(obj, dict):
        raise PolicyDenied(f"{where} must be an object")
    # casefold() also folds U+017F and U+212A, which Go's EqualFold equates to s/k.
    canonical = {f.casefold(): f for f in fields}
    seen: dict[str, str] = {}
    for key in obj:
        folded = str(key).casefold()
        if folded in seen:
            raise PolicyDenied(f"{where}: keys {seen[folded]!r} and {key!r} differ only in case")
        seen[folded] = key
        expected = canonical.get(folded)
        if expected is None:
            if closed:
                raise PolicyDenied(f"{where}: field {key!r} is not permitted")
        elif key != expected:
            raise PolicyDenied(f"{where}: field {key!r} must be spelled {expected!r}")
    return obj


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    obj: dict[str, Any] = {}
    for key, value in pairs:
        if key in obj:
            raise PolicyDenied(f"duplicate key {key!r}")
        obj[key] = value
    return obj


@dataclass(frozen=True)
class Route:
    kind: str
    container: str | None = None
    network: str | None = None
    # The id or name the route acts on when it is neither a container nor a network (an exec, a volume).
    ident: str | None = None
    # The JSON body is read and checked before anything goes upstream.
    reads_body: bool = False
    # Docker hijacks the connection (an attached exec): both directions are piped once the daemon answers.
    hijack: bool = False


class Absent(Exception):
    """The object a request names does not exist: answered 404, as the daemon would, so a caller's not-found handling
    works through the proxy."""


class Daemon:
    """The Docker daemon behind the proxy, for the lookups a policy makes before it lets a request through."""

    def __init__(self, socket_path: str) -> None:
        self.socket_path = socket_path

    async def open(self) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        return await asyncio.open_unix_connection(self.socket_path)

    async def json(self, path: str) -> tuple[int, Any]:
        reader, writer = await self.open()
        try:
            writer.write(_serialize_head(
                f"GET {path} HTTP/1.1", [("Host", "docker"), ("Connection", "close")]
            ))
            await writer.drain()
            raw = await reader.read()
        finally:
            writer.close()
        head, _, body = raw.partition(b"\r\n\r\n")
        status_line, *header_lines = head.decode("latin-1").split("\r\n")
        status = int(status_line.split(" ")[1])
        if any(h.lower().startswith("transfer-encoding:") and "chunked" in h.lower()
               for h in header_lines):
            body = _dechunk(body)
        try:
            return status, json.loads(body or b"null")
        except ValueError:
            return status, None


def with_container_id(target: str, full_id: str) -> str:
    """``target`` addressing the container by the id the policy checked, so a name reused between the check and the
    call cannot redirect it."""
    return _CONTAINER_IN_TARGET.sub(rf"\g<1>{full_id}", target, count=1)


def removing_volumes(target: str) -> str:
    """A container DELETE that takes its anonymous volumes with it, whatever the client asked."""
    split = urlsplit(target)
    query = {k: v[-1] for k, v in parse_qs(split.query).items()}
    return f"{split.path}?{urlencode({**query, 'v': '1'})}"


def refuse_host_reach(host: dict[str, Any]) -> None:
    """The HostConfig rules every profile holds a create to: nothing of the host (privileged mode, binds, devices,
    published ports, host namespaces, unconfined profiles, a restart policy, another runtime), whatever else the
    profile allows. ``host`` has passed ``_struct_object``."""
    if host.get("Privileged"):
        raise PolicyDenied("privileged containers are not permitted")
    for key in ("Binds", "VolumesFrom", "Devices", "DeviceRequests",
                "DeviceCgroupRules", "Links", "Sysctls", "PortBindings",
                "VolumeDriver"):
        if host.get(key):
            raise PolicyDenied(f"HostConfig.{key} is not permitted")
    if host.get("PublishAllPorts"):
        raise PolicyDenied("HostConfig.PublishAllPorts is not permitted")
    if host.get("CgroupParent"):
        raise PolicyDenied("HostConfig.CgroupParent is not permitted")
    for key in ("MaskedPaths", "ReadonlyPaths"):
        if host.get(key) is not None:
            raise PolicyDenied(f"HostConfig.{key} is not permitted")
    # Init runs the daemon's own init as PID 1 to reap zombies under the pids limit. Docker
    # reads it as a boolean; refuse any other shape rather than let the daemon interpret it.
    if "Init" in host and host["Init"] is not None and not isinstance(host["Init"], bool):
        raise PolicyDenied("HostConfig.Init may only be true or false")
    # StorageOpt caps the container's writable layer (the per-run disk
    # quota). Only the size cap is a resource limit; refuse anything else.
    storage_opt = host.get("StorageOpt")
    if storage_opt is not None and (not isinstance(storage_opt, dict) or set(storage_opt) - {"size"}):
        raise PolicyDenied("HostConfig.StorageOpt may only set 'size'")
    for opt in host.get("SecurityOpt") or []:
        if str(opt).strip().lower() not in _ALLOWED_SECURITY_OPTS:
            raise PolicyDenied(f"security option {opt!r} is not permitted")
    for key in ("PidMode", "UTSMode", "UsernsMode"):
        if host.get(key):
            raise PolicyDenied(f"HostConfig.{key} is not permitted")
    if (host.get("IpcMode") or "private") not in ("private", "none"):
        raise PolicyDenied("HostConfig.IpcMode is not permitted")
    if (host.get("CgroupnsMode") or "private") != "private":
        raise PolicyDenied("HostConfig.CgroupnsMode is not permitted")
    if (host.get("Runtime") or "") not in _ALLOWED_RUNTIMES:
        raise PolicyDenied(f"runtime {host.get('Runtime')!r} is not permitted")
    restart_policy = _struct_object(
        host.get("RestartPolicy"), _RESTART_POLICY_FIELDS, "HostConfig.RestartPolicy",
    )
    restart = restart_policy.get("Name") or "no"
    if restart != "no":
        raise PolicyDenied("restart policies are not permitted")


def pinned_log_config(host: dict[str, Any]) -> dict[str, Any]:
    """The create's LogConfig, pinned to a local driver.

    Network log drivers (syslog, gelf, fluentd, ...) connect from the daemon's network, whatever the container's
    NetworkMode is. An unset Type takes the daemon's default driver, which may be one of them, so it is pinned;
    "local" also rotates by default, bounding a chatty sandbox's disk use.
    """
    log_config = _struct_object(
        host.get("LogConfig"), _LOG_CONFIG_FIELDS, "HostConfig.LogConfig", closed=True,
    )
    log_driver = log_config.get("Type") or "local"
    if log_driver not in _ALLOWED_LOG_DRIVERS:
        raise PolicyDenied(f"log driver {log_driver!r} is not permitted")
    if log_config.get("Config"):
        raise PolicyDenied("HostConfig.LogConfig.Config is not permitted")
    return {"Type": log_driver, "Config": {}}


def labelled(body: dict[str, Any], host: dict[str, Any], stamp: dict[str, str]) -> dict[str, Any]:
    """The checked create body with its log driver pinned and the proxy's ownership ``stamp`` among its labels."""
    raw_labels = body.get("Labels")
    if raw_labels is None:
        raw_labels = {}
    if not isinstance(raw_labels, dict):
        raise PolicyDenied("Labels must be an object")
    return {
        **body,
        "HostConfig": {**host, "LogConfig": pinned_log_config(host)},
        "Labels": {**raw_labels, **stamp},
    }


def normalize_image(ref: str) -> str:
    """Canonical ``repo:tag`` / ``repo@digest`` so equivalent spellings match."""
    ref = ref.strip()
    if "@" in ref:
        name, digest = ref.split("@", 1)
        suffix = "@" + digest
    else:
        name, suffix = ref, ""
        last = name.rsplit("/", 1)[-1]
        if ":" in last:
            name, tag = name.rsplit(":", 1)
            suffix = ":" + tag
        else:
            suffix = ":latest"
    for prefix in ("docker.io/", "index.docker.io/", "registry-1.docker.io/"):
        if name.startswith(prefix):
            name = name[len(prefix):]
            break
    if name.startswith("library/"):
        name = name[len("library/"):]
    return name + suffix


@dataclass
class DockerApiPolicy:
    allowed_images: frozenset[str]
    allowed_networks: frozenset[str]
    # The only addresses an egress network may be given: anything else could put the bridge on a range the
    # host routes to (its LAN, a VPC peer), which the sandbox would then reach through the bridge gateway.
    egress_pool: ipaddress.IPv4Network = ipaddress.IPv4Network(_DEFAULT_OPEN_EGRESS_POOL)
    _allowed_normalized: frozenset[str] = field(init=False)

    def __post_init__(self) -> None:
        self._allowed_normalized = frozenset(normalize_image(i) for i in self.allowed_images)

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> DockerApiPolicy:
        env = os.environ if env is None else env
        images = _split_csv(env.get("DOCKER_PROXY_ALLOWED_IMAGES")) or [
            env.get("SANDBOX_DOCKER_IMAGE") or _DEFAULT_SANDBOX_IMAGE
        ]
        networks = _split_csv(env.get("DOCKER_PROXY_ALLOWED_NETWORKS")) or [
            env.get("SANDBOX_EGRESS_NETWORK") or _DEFAULT_EGRESS_NETWORK
        ]
        pool = ipaddress.ip_network(
            (env.get(_ENV_OPEN_EGRESS_SUBNETS) or "").strip() or _DEFAULT_OPEN_EGRESS_POOL, strict=True,
        )
        if not isinstance(pool, ipaddress.IPv4Network):
            raise ValueError(f"{_ENV_OPEN_EGRESS_SUBNETS} must be an IPv4 network")
        return cls(allowed_images=frozenset(images), allowed_networks=frozenset(networks), egress_pool=pool)

    def image_allowed(self, ref: str) -> bool:
        return bool(ref) and normalize_image(ref) in self._allowed_normalized

    def route(self, method: str, target: str) -> Route:
        """Classify a request. Raises ``PolicyDenied`` for anything unlisted."""
        path = _VERSION_PREFIX.sub("", urlsplit(target).path)
        query = parse_qs(urlsplit(target).query)

        if path == "/_ping" and method in ("GET", "HEAD"):
            return Route("allow")
        if path == "/version" and method == "GET":
            return Route("allow")

        if path == "/containers/create" and method == "POST":
            return Route("container_create", reads_body=True)
        m = re.fullmatch(r"/containers/([^/]+)(/[a-z]+)?", path)
        if m:
            ident, action = unquote(m.group(1)), m.group(2) or ""
            allowed = {
                ("GET", "/json"), ("POST", "/start"), ("POST", "/wait"),
                ("POST", "/kill"), ("POST", "/stop"), ("GET", "/logs"),
                ("GET", "/archive"), ("HEAD", "/archive"), ("PUT", "/archive"),
                ("DELETE", ""),
            }
            if (method, action) in allowed:
                return Route("container_owned", container=ident)
            raise PolicyDenied(f"{method} /containers/{{id}}{action} is not permitted")

        if path == "/images/create" and method == "POST":
            if "fromSrc" in query or "repo" in query:
                raise PolicyDenied("image import is not permitted")
            name = (query.get("fromImage") or [""])[0]
            tag = (query.get("tag") or [""])[0]
            if tag:
                ref = f"{name}@{tag}" if tag.startswith("sha256:") else f"{name}:{tag}"
            else:
                ref = name
            if not self.image_allowed(ref):
                raise PolicyDenied(f"pulling image {ref!r} is not permitted")
            return Route("allow")
        m = re.fullmatch(r"/images/(.+)/json", path)
        if m and method == "GET":
            ref = unquote(m.group(1))
            if not self.image_allowed(ref):
                raise PolicyDenied(f"inspecting image {ref!r} is not permitted")
            return Route("allow")

        if path == "/networks" and method == "GET":
            return Route("allow")
        if path == "/networks/create" and method == "POST":
            return Route("network_create", reads_body=True)
        m = re.fullmatch(r"/networks/([^/]+)", path)
        if m and method in ("GET", "DELETE"):
            return Route("network_allowed", network=unquote(m.group(1)))

        raise PolicyDenied(f"{method} {path} is not permitted")

    async def admit(self, route: Route, method: str, target: str, payload: object, daemon: Daemon) -> tuple[str, Any]:
        """The target to send upstream and the checked body (``payload`` as ``route.reads_body`` read it)."""
        if route.kind == "container_create":
            return target, self.check_container_create(payload)
        if route.kind == "network_create":
            return target, self.check_network_create(payload)
        if route.kind == "container_owned":
            assert route.container is not None
            target = with_container_id(target, await self._owned_container_id(daemon, route.container))
            if method == "DELETE":
                target = removing_volumes(target)
        elif route.kind == "network_allowed":
            assert route.network is not None
            status, info = await daemon.json(f"/networks/{quote(route.network, safe='')}")
            if status != 200 or (info or {}).get("Name") not in self.allowed_networks:
                raise PolicyDenied("network is not a sandbox network")
        return target, None

    @staticmethod
    async def _owned_container_id(daemon: Daemon, ident: str) -> str:
        status, info = await daemon.json(f"/containers/{quote(ident, safe='')}/json")
        labels: dict[str, str] = {}
        if status == 200 and isinstance(info, dict):
            labels = (info.get("Config") or {}).get("Labels") or {}
        if labels.get(MANAGED_LABEL) != "true":
            raise PolicyDenied("container is not managed by the sandbox proxy")
        return str(info["Id"])

    def check_container_create(self, body: dict[str, Any]) -> dict[str, Any]:
        """Validate a create payload and return it with the ownership label."""
        if not isinstance(body, dict):
            raise PolicyDenied("container create body must be a JSON object")
        _struct_object(body, _CREATE_FIELDS, "container create body", closed=True)
        image = body.get("Image") or ""
        if not self.image_allowed(image):
            raise PolicyDenied(f"image {image!r} is not an allowed sandbox image")
        # docker-py always sends these, as null. Volumes would create anonymous
        # volumes; Runtime is not a Config field, so only HostConfig's is honoured.
        for key in ("Volumes", "Runtime"):
            if body.get(key):
                raise PolicyDenied(f"{key} is not permitted")

        host = _struct_object(
            body.get("HostConfig"), _HOST_CONFIG_FIELDS, "HostConfig", closed=True,
        )
        refuse_host_reach(host)

        for raw_mount in host.get("Mounts") or []:
            mount = _struct_object(raw_mount, _MOUNT_FIELDS, "HostConfig.Mounts[]", closed=True)
            kind = mount.get("Type")
            if kind == "volume":
                # A named source could be another container's data or a local-driver bind of the host.
                if mount.get("Source"):
                    raise PolicyDenied("only anonymous volumes are permitted")
                _struct_object(mount.get("VolumeOptions"), _VOLUME_OPTION_FIELDS, "HostConfig.Mounts[].VolumeOptions", closed=True)
            elif kind != "tmpfs":
                raise PolicyDenied("only tmpfs mounts and anonymous volumes are permitted")

        cap_add = {str(c).upper().removeprefix("CAP_") for c in host.get("CapAdd") or []}
        extra = cap_add - _ALLOWED_CAPS
        if extra:
            raise PolicyDenied(f"capabilities {sorted(extra)} are not permitted")
        # SEC-A-04: the broker's floor; the firewall's install step is the one root container with capabilities.
        installs_firewall = firewall_install(body.get("User"), body.get("Cmd"), sorted(cap_add))
        if not installs_firewall and (root_user(body.get("User")) or cap_add):
            raise PolicyDenied("a sandbox runs as a non-root user with no added capabilities")
        if installs_firewall and (host.get("NetworkMode") or "none") == "none":
            raise PolicyDenied("the egress firewall installs only on the egress network")
        floor = floor_violations(host)
        if floor:
            raise PolicyDenied(f"the sandbox floor is not met: {'; '.join(floor)}")

        network_mode = host.get("NetworkMode") or ""
        if network_mode != "none" and network_mode not in self.allowed_networks:
            raise PolicyDenied(f"network mode {network_mode or 'default'!r} is not permitted")
        networking = _struct_object(
            body.get("NetworkingConfig"), _NETWORKING_CONFIG_FIELDS, "NetworkingConfig",
        )
        endpoints = networking.get("EndpointsConfig") or {}
        for name in endpoints:
            if name not in self.allowed_networks:
                raise PolicyDenied(f"network {name!r} is not permitted")
        return labelled(body, host, {MANAGED_LABEL: "true"})

    def check_network_create(self, body: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(body, dict):
            raise PolicyDenied("network create body must be a JSON object")
        _struct_object(body, _NETWORK_CREATE_FIELDS, "network create body")
        name = body.get("Name") or ""
        if name not in self.allowed_networks:
            raise PolicyDenied(f"creating network {name!r} is not permitted")
        if (body.get("Driver") or "bridge") != "bridge":
            raise PolicyDenied("only bridge networks are permitted")
        extra = set((body.get("Options") or {})) - _ALLOWED_NETWORK_OPTIONS
        if extra:
            raise PolicyDenied(f"network options {sorted(extra)} are not permitted")
        # EnableIPv6: the sandbox egress firewall only installs IPv4 rules.
        for key in ("ConfigFrom", "ConfigOnly", "Ingress", "Scope", "EnableIPv6"):
            if body.get(key):
                raise PolicyDenied(f"network {key} is not permitted")
        if body.get("IPAM"):
            self._check_ipam(body["IPAM"])
        return body

    def _check_ipam(self, ipam: object) -> None:
        check_ipam(ipam, self.egress_pool)


def check_ipam(ipam: object, pool: ipaddress.IPv4Network) -> None:
    """One IPv4 subnet of ``pool`` with the default driver, its gateway inside it, nothing else."""
    ipam = _struct_object(ipam, _IPAM_FIELDS, "network IPAM", closed=True)
    if (ipam.get("Driver") or "default") != "default" or ipam.get("Options"):
        raise PolicyDenied("network IPAM driver and options are not permitted")
    configs = ipam.get("Config")
    if not isinstance(configs, list) or len(configs) != 1:
        raise PolicyDenied("network IPAM must name exactly one subnet")
    config = _struct_object(configs[0], _IPAM_POOL_FIELDS, "network IPAM pool", closed=True)
    if config.get("IPRange") or config.get("AuxiliaryAddresses"):
        raise PolicyDenied("network IPAM ranges and auxiliary addresses are not permitted")
    try:
        subnet = ipaddress.ip_network(str(config.get("Subnet") or ""), strict=True)
        gateway = ipaddress.ip_address(str(config["Gateway"])) if config.get("Gateway") else None
    except ValueError as exc:
        raise PolicyDenied(f"network IPAM address is not valid: {exc}") from None
    if not isinstance(subnet, ipaddress.IPv4Network) or not subnet.subnet_of(pool):
        raise PolicyDenied(f"network subnet {subnet} is outside {pool}")
    if gateway is not None and gateway not in subnet:
        raise PolicyDenied(f"network gateway {gateway} is outside {subnet}")


def _split_csv(value: str | None) -> list[str]:
    return [v.strip() for v in (value or "").split(",") if v.strip()]


@dataclass
class _Request:
    method: str
    target: str
    version: str
    headers: list[tuple[str, str]]

    def header(self, name: str) -> str | None:
        name = name.lower()
        for k, v in self.headers:
            if k.lower() == name:
                return v
        return None

    @property
    def chunked(self) -> bool:
        return "chunked" in (self.header("transfer-encoding") or "").lower()

    @property
    def content_length(self) -> int:
        raw = self.header("content-length")
        if raw is None:
            return 0
        try:
            n = int(raw)
        except ValueError as exc:
            raise PolicyDenied("invalid Content-Length") from exc
        if n < 0:
            raise PolicyDenied("invalid Content-Length")
        return n


async def _read_head(reader: asyncio.StreamReader) -> bytes:
    try:
        return await reader.readuntil(b"\r\n\r\n")
    except asyncio.LimitOverrunError as exc:
        raise PolicyDenied("request head too large") from exc


def _parse_request_head(raw: bytes) -> _Request:
    lines = raw.decode("latin-1").split("\r\n")
    parts = lines[0].split(" ")
    if (
        len(parts) != 3
        or not _TOKEN.fullmatch(parts[0])
        or not parts[1].startswith("/")
        or _TARGET_FORBIDDEN.search(parts[1])
        or not _HTTP_VERSION.fullmatch(parts[2])
    ):
        raise PolicyDenied("malformed request line")
    headers: list[tuple[str, str]] = []
    for line in lines[1:]:
        if not line:
            continue
        key, sep, value = line.partition(":")
        # The daemon's Go parser also splits on a bare LF, so a header line
        # carrying one would reach it as headers this proxy never saw.
        if not sep or not _TOKEN.fullmatch(key) or _VALUE_FORBIDDEN.search(value):
            raise PolicyDenied("malformed header")
        headers.append((key, value.strip(" \t")))
    framing = [k.lower() for k, _ in headers if k.lower() in ("content-length", "transfer-encoding")]
    if len(framing) > 1:
        raise PolicyDenied("ambiguous request framing")
    req = _Request(parts[0].upper(), parts[1], parts[2], headers)
    te = req.header("transfer-encoding")
    # Go ignores Transfer-Encoding on HTTP/1.0 (RFC 9112 §6.1: faulty framing),
    # so a chunked body passed through would reach the daemon as empty.
    if te is not None and (req.version == "HTTP/1.0" or te.lower() != "chunked"):
        raise PolicyDenied("unsupported Transfer-Encoding")
    return req


async def _read_chunked(reader: asyncio.StreamReader, limit: int) -> bytes:
    body = bytearray()
    while True:
        size_line = await reader.readuntil(b"\r\n")
        size = int(size_line.split(b";", 1)[0].strip() or b"0", 16)
        if size == 0:
            while (await reader.readuntil(b"\r\n")) != b"\r\n":
                pass
            return bytes(body)
        if len(body) + size > limit:
            raise PolicyDenied("request body too large")
        body += await reader.readexactly(size)
        await reader.readexactly(2)


async def _pipe_chunked(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    """Forward a chunked body verbatim, stopping after the terminating chunk."""
    while True:
        size_line = await reader.readuntil(b"\r\n")
        writer.write(size_line)
        size = int(size_line.split(b";", 1)[0].strip() or b"0", 16)
        if size == 0:
            while True:
                trailer = await reader.readuntil(b"\r\n")
                writer.write(trailer)
                if trailer == b"\r\n":
                    break
            await writer.drain()
            return
        remaining = size + 2
        while remaining:
            chunk = await reader.read(min(remaining, _COPY_CHUNK))
            if not chunk:
                raise asyncio.IncompleteReadError(b"", remaining)
            writer.write(chunk)
            remaining -= len(chunk)
        await writer.drain()


async def _pipe_exact(reader: asyncio.StreamReader, writer: asyncio.StreamWriter, n: int) -> None:
    while n:
        chunk = await reader.read(min(n, _COPY_CHUNK))
        if not chunk:
            raise asyncio.IncompleteReadError(b"", n)
        writer.write(chunk)
        n -= len(chunk)
        await writer.drain()


async def _pipe_to_eof(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    while chunk := await reader.read(_COPY_CHUNK):
        writer.write(chunk)
        await writer.drain()


def _serialize_head(first_line: str, headers: list[tuple[str, str]]) -> bytes:
    lines = [first_line, *(f"{k}: {v}" for k, v in headers), "", ""]
    return "\r\n".join(lines).encode("latin-1")


def _error_response(status: int, reason: str, message: str) -> bytes:
    body = json.dumps({"message": message}).encode()
    head = _serialize_head(
        f"HTTP/1.1 {status} {reason}",
        [("Content-Type", "application/json"), ("Content-Length", str(len(body))),
         ("Connection", "close")],
    )
    return head + body


class ProxyProfile(Protocol):
    """What one caller may ask of the daemon: the legacy executor's calls (``DockerApiPolicy``) or the sandbox
    broker's (``app.sandbox.docker_proxy_broker.BrokerApiPolicy``). The proxy picks the profile by the caller's
    credential; everything else (parsing, framing, forwarding) is the same code for both."""

    def route(self, method: str, target: str) -> Route: ...

    async def admit(self, route: Route, method: str, target: str, payload: object, daemon: Daemon) -> tuple[str, Any]: ...


class DockerSocketProxy:
    def __init__(
        self,
        policy: ProxyProfile | None,
        upstream_socket: str,
        *,
        caller_token: str | None = None,
        callers: Mapping[str, ProxyProfile] | None = None,
        anonymous: ProxyProfile | None = None,
    ) -> None:
        profiles = dict(callers or {})
        if policy is not None:
            if caller_token is None:
                raise ValueError("a profile needs its caller's credential")
            profiles[caller_token] = policy
        if anonymous is not None and profiles:
            raise ValueError("a proxy with a credentialed caller admits no caller without one")
        if not profiles and anonymous is None:
            raise ValueError("the proxy has no caller")
        if any(len(token) < 32 for token in profiles):
            raise ValueError("the caller token is too short to be a credential")
        self._callers = [(f"Bearer {token}".encode(), profile) for token, profile in profiles.items()]
        # R51-COMPAT: the compatibility profile with no credential configured, as a release before SEC-A-04 ran.
        self._anonymous = anonymous
        self._daemon = Daemon(upstream_socket)
        # The stream server holds a handler task only through its transport,
        # which the loop lets go of while reading is paused (a large body
        # arriving during the ownership lookup); a GC pass then collects the
        # request mid-flight and the client sees an empty reply.
        self._inflight: set[asyncio.Task[None]] = set()

    def _profile(self, authorization: str | None) -> ProxyProfile | None:
        """The profile whose credential the request carries. Every credential is compared, so the time taken does
        not say which one matched."""
        sent = (authorization or "").encode()
        found: ProxyProfile | None = None
        for expected, profile in self._callers:
            if hmac.compare_digest(sent, expected) and found is None:
                found = profile
        return found

    async def handle(self, client_r: asyncio.StreamReader, client_w: asyncio.StreamWriter) -> None:
        task = asyncio.current_task()
        if task is not None:
            self._inflight.add(task)
            task.add_done_callback(self._inflight.discard)
        try:
            await self._handle(client_r, client_w)
        except PolicyDenied as exc:
            logger.warning("denied: %s", exc)
            await _safe_write(client_w, _error_response(403, "Forbidden", f"docker-proxy: {exc}"))
        except Absent as exc:
            await _safe_write(client_w, _error_response(404, "Not Found", str(exc)))
        except (asyncio.IncompleteReadError, ConnectionError, asyncio.TimeoutError):
            pass
        except Exception:
            logger.exception("proxy error")
            await _safe_write(client_w, _error_response(502, "Bad Gateway", "docker-proxy: upstream error"))
        finally:
            client_w.close()

    async def _handle(self, client_r: asyncio.StreamReader, client_w: asyncio.StreamWriter) -> None:
        raw_head = await asyncio.wait_for(_read_head(client_r), _HEAD_TIMEOUT_S)
        req = _parse_request_head(raw_head)
        profile = self._profile(req.header("authorization")) or self._anonymous
        if profile is None:
            if _VERSION_PREFIX.sub("", urlsplit(req.target).path) not in _OPEN_PATHS:
                raise PolicyDenied("the caller's credential is missing or wrong")
            profile = self._callers[0][1]
        route = profile.route(req.method, req.target)
        # Clients that sent Expect hold the body back until told to continue,
        # and the checked routes read it before going upstream.
        expects_continue = (req.header("expect") or "").lower() == "100-continue"

        body: bytes | None = None
        payload: Any = None
        if route.reads_body:
            if expects_continue:
                client_w.write(b"HTTP/1.1 100 Continue\r\n\r\n")
                await client_w.drain()
            if req.chunked:
                raw = await _read_chunked(client_r, _MAX_POLICY_BODY_BYTES)
            else:
                if req.content_length > _MAX_POLICY_BODY_BYTES:
                    raise PolicyDenied("request body too large")
                raw = await client_r.readexactly(req.content_length)
            try:
                payload = json.loads(raw or b"{}", object_pairs_hook=_reject_duplicate_keys)
            except ValueError as exc:
                raise PolicyDenied("request body is not valid JSON") from exc
        target, checked = await profile.admit(route, req.method, req.target, payload, self._daemon)
        if route.reads_body:
            body = json.dumps(checked).encode()

        logger.debug("allow %s %s", req.method, target)
        dropped = _HOP_BY_HOP - {"connection", "upgrade"} if route.hijack else _HOP_BY_HOP
        headers = [(k, v) for k, v in req.headers if k.lower() not in dropped and k.lower() != "authorization"]
        if body is not None:
            headers = [(k, v) for k, v in headers
                       if k.lower() not in ("content-length", "transfer-encoding")]
            headers.append(("Content-Length", str(len(body))))
        if not route.hijack:
            headers.append(("Connection", "close"))

        if expects_continue and body is None:
            client_w.write(b"HTTP/1.1 100 Continue\r\n\r\n")
            await client_w.drain()

        up_r, up_w = await self._daemon.open()
        try:
            up_w.write(_serialize_head(f"{req.method} {target} {req.version}", headers))
            if body is not None:
                up_w.write(body)
            elif req.chunked:
                await _pipe_chunked(client_r, up_w)
            elif req.content_length:
                await _pipe_exact(client_r, up_w, req.content_length)
            await up_w.drain()

            raw_response = await up_r.readuntil(b"\r\n\r\n")
            client_w.write(raw_response if route.hijack else _parse_response_head(raw_response))
            await client_w.drain()
            if route.hijack:
                await _pipe_both(client_r, client_w, up_r, up_w)
            else:
                await _pipe_to_eof(up_r, client_w)
        finally:
            up_w.close()


async def _pipe_both(
    client_r: asyncio.StreamReader, client_w: asyncio.StreamWriter,
    up_r: asyncio.StreamReader, up_w: asyncio.StreamWriter,
) -> None:
    """A hijacked stream: the client's bytes (an exec's stdin) go up and its half-close is passed on, the daemon's come
    down until it closes, which ends the exchange."""

    async def upward() -> None:
        with contextlib.suppress(ConnectionError, RuntimeError):
            await _pipe_to_eof(client_r, up_w)
            if up_w.can_write_eof():
                up_w.write_eof()

    async def downward() -> None:
        try:
            await _pipe_to_eof(up_r, client_w)
        finally:
            # The daemon is done: closing the client's connection ends its reader, so the upward copy stops too.
            client_w.close()

    await asyncio.gather(upward(), downward())


def _parse_response_head(raw: bytes) -> bytes:
    lines = raw.decode("latin-1").split("\r\n")
    headers = []
    for line in lines[1:]:
        if not line:
            continue
        key, _, value = line.partition(":")
        if key.strip().lower() not in ("connection", "keep-alive"):
            headers.append((key.strip(), value.strip()))
    headers.append(("Connection", "close"))
    return _serialize_head(lines[0], headers)


def _dechunk(data: bytes) -> bytes:
    out = bytearray()
    while data:
        size_line, _, data = data.partition(b"\r\n")
        size = int(size_line.split(b";", 1)[0].strip() or b"0", 16)
        if size == 0:
            break
        out += data[:size]
        data = data[size + 2:]
    return bytes(out)


async def _safe_write(writer: asyncio.StreamWriter, data: bytes) -> None:
    try:
        writer.write(data)
        await writer.drain()
    except (ConnectionError, RuntimeError):
        pass


def _upstream_path(value: str) -> str:
    return value.removeprefix("unix://") if value.startswith("unix://") else value


def caller_token(env: dict[str, str] | None = None, name: str = ENV_CALLER_TOKEN) -> str:
    """A caller's credential for the proxy, ``name`` (``DOCKER_PROXY_TOKEN`` for the legacy executor,
    ``DOCKER_PROXY_BROKER_TOKEN`` for the sandbox broker); ``RuntimeError`` when it is missing or weak, so neither side
    runs with a guessable one."""
    from app.utils.encryption.secret_strength import weak_secret_reason

    env = dict(os.environ) if env is None else env
    token = env.get(name) or ""
    if not token.strip():
        raise RuntimeError(
            f"{name} is not set: the Docker API proxy and its caller share it, and install.sh generates it"
        )
    reason = weak_secret_reason(token)
    if reason is not None:
        raise RuntimeError(
            f"{name} is not random enough ({reason}): run install.sh, which generates one, or set "
            "`openssl rand -hex 32` for both the caller and the Docker API proxy"
        )
    return token


def _refuse_platform_keys(env: Mapping[str, str]) -> None:
    held = [name for name in _PLATFORM_KEYS if (env.get(name) or "").strip()]
    if held:
        raise RuntimeError(
            f"the Docker API proxy was given {', '.join(held)}; it needs only its callers' credentials (ADR-KEY-02): "
            "remove the others from its environment"
        )


def proxy_caller_token(env: dict[str, str] | None = None) -> str:
    """The legacy profile's start check: its caller credential, and no platform root key beside the Docker socket."""
    env = dict(os.environ) if env is None else env
    _refuse_platform_keys(env)
    return caller_token(env)


def proxy_callers(env: dict[str, str] | None = None) -> dict[str, ProxyProfile]:
    """Each configured caller's credential and the profile it is held to: ``DOCKER_PROXY_BROKER_TOKEN`` for the sandbox
    broker (the default mode), ``DOCKER_PROXY_TOKEN`` for the legacy executor while an install still runs
    ``SANDBOX_MODE=docker``. One is needed on a hardened install (the compatibility profile may have none:
    ``anonymous_caller``), the two must differ, and no platform root key may sit beside the socket."""
    env = dict(os.environ) if env is None else env
    _refuse_platform_keys(env)
    callers: dict[str, ProxyProfile] = {}
    if (env.get(ENV_CALLER_TOKEN) or "").strip():
        callers[caller_token(env)] = DockerApiPolicy.from_env(env)
    if (env.get(ENV_BROKER_CALLER_TOKEN) or "").strip():
        # Imported only here: the broker's profile shares the broker's rules (app.harness.sandbox.docker_policy).
        from app.sandbox.docker_proxy_broker import BrokerApiPolicy

        broker = caller_token(env, ENV_BROKER_CALLER_TOKEN)
        if broker in callers:
            raise RuntimeError(f"{ENV_BROKER_CALLER_TOKEN} and {ENV_CALLER_TOKEN} must be different credentials")
        callers[broker] = BrokerApiPolicy.from_env(env)
    if not callers and hardened_profile():
        raise RuntimeError(
            f"the Docker API proxy has no caller: set {ENV_BROKER_CALLER_TOKEN} (the sandbox broker) or "
            f"{ENV_CALLER_TOKEN} (SANDBOX_MODE=docker); install.sh generates both"
        )
    return callers


def anonymous_caller(env: dict[str, str] | None = None) -> ProxyProfile | None:
    """R51-COMPAT (ADR-OPS-07): the profile a caller without a credential is held to. Only the compatibility profile
    with no caller credential configured has one: the app's legacy executor, as a release before SEC-A-04 ran it (an
    ``.env`` from such a release under a plain ``docker compose up``). Every create still meets the legacy floor."""
    env = dict(os.environ) if env is None else env
    configured = any((env.get(name) or "").strip() for name in (ENV_CALLER_TOKEN, ENV_BROKER_CALLER_TOKEN))
    if configured or hardened_profile():
        return None
    logger.warning(
        "The Docker API proxy has no caller credential (%s, %s), so it admits the app's code sandbox without one, "
        "as releases before SEC-A-04 did (PIPESHUB_HARDENED is not true). Run ./install.sh --upgrade once to "
        "generate the credentials; the hardened profile refuses to start without them.",
        ENV_CALLER_TOKEN, ENV_BROKER_CALLER_TOKEN,
    )
    return DockerApiPolicy.from_env(env)


def executor_calls_anonymously() -> bool:
    """R51-COMPAT: whether the app's legacy executor calls the proxy with no credential (the compatibility profile with
    ``DOCKER_PROXY_TOKEN`` unset), as a release before SEC-A-04 did."""
    return not (os.environ.get(ENV_CALLER_TOKEN) or "").strip() and not hardened_profile()


async def serve(
    host: str,
    port: int,
    upstream: str,
    callers: Mapping[str, ProxyProfile],
    anonymous: ProxyProfile | None = None,
) -> None:
    proxy = DockerSocketProxy(None, upstream, callers=callers, anonymous=anonymous)
    server = await asyncio.start_server(proxy.handle, host, port, limit=_MAX_HEAD_BYTES)
    logger.info(
        "docker proxy listening on %s:%d -> %s (profiles=%s)",
        host, port, upstream,
        sorted(type(profile).__name__ for profile in [*callers.values(), *([anonymous] if anonymous else [])]),
    )
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    async with server:
        await stop.wait()


PROXY_UID = 1000


def drop_root(socket_path: str, ops: Any = os) -> int | None:  # noqa: ANN401
    """Started as root (Compose without DOCKER_GID: an .env from a release that ran this proxy as root), take the
    socket's group and become uid 1000 before serving, so a compatibility install keeps code execution and the proxy
    still runs unprivileged. Returns the group taken, or None when already unprivileged."""
    if ops.getuid() != 0:
        return None
    gid = ops.stat(socket_path).st_gid
    ops.setgroups([gid])
    ops.setgid(PROXY_UID)
    ops.setuid(PROXY_UID)
    logger.warning(
        "started as root (DOCKER_GID is not set): now uid %d with the Docker socket's group %d. "
        "./install.sh --upgrade writes DOCKER_GID so the proxy starts unprivileged.", PROXY_UID, gid,
    )
    return gid


def main() -> None:
    logging.basicConfig(
        level=os.environ.get("DOCKER_PROXY_LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    upstream = os.environ.get("DOCKER_PROXY_UPSTREAM", "/var/run/docker.sock")
    if upstream.startswith(("/", "unix://")):
        drop_root(upstream.removeprefix("unix://"))
    asyncio.run(serve(
        os.environ.get("DOCKER_PROXY_HOST", "0.0.0.0"),
        int(os.environ.get("DOCKER_PROXY_PORT", "2375")),
        _upstream_path(os.environ.get("DOCKER_PROXY_UPSTREAM", "/var/run/docker.sock")),
        proxy_callers(),
        anonymous_caller(),
    ))


if __name__ == "__main__":
    main()
