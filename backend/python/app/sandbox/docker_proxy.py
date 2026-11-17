"""Policy-enforcing proxy between the coding sandbox and the Docker daemon.

The app used to mount ``/var/run/docker.sock`` directly, so anything that
compromised the app process was root on the host: one ``POST
/containers/create`` with ``Privileged`` and a bind of ``/`` is enough. This
proxy is the only process that touches the socket. The app reaches it over
TCP (``DOCKER_HOST=tcp://docker-socket-proxy:2375``) and the Docker SDK works
unchanged, but only the calls the sandbox makes get through:

- ping / version, and a filtered daemon ``/info`` (only the fields that size
  the sandbox ceiling: no labels, proxy settings, registry config or
  container counts);
- ``GET /_pipeshub/host``, answered by the proxy itself: a hash of the
  kernel's boot id, so the app can tell whether the daemon shares its
  machine (the proxy mounts the daemon's socket, so it runs on that machine);
- ``GET /_pipeshub/usage``, also answered here: how full the daemon's own
  container is, from the cgroup counters a loop inside a Docker-in-Docker
  sidecar writes to the socket volume (``DOCKER_PROXY_SIDECAR_STATS_FILE``),
  or with ``?scope=containers`` the summed stats of this proxy's running
  containers. Only totals leave the proxy, never ids or names;
- create a container from an allowed sandbox image with no host access
  (no privileged mode, binds, devices, host namespaces, port publishing,
  unconfined security profiles, or capabilities beyond the egress firewall's)
  and with bounded resources (memory, CPU and process limits are required and
  capped, swap is off, and the OOM score can only be raised);
- start / wait / kill / stop / logs / archive / inspect / remove, only on
  containers this proxy created (it stamps labels at create time, one of
  them naming this stack, so stacks sharing a daemon cannot reach each
  other's containers);
- inspect / pull of the allowed sandbox images, by an explicit tag or digest;
- list and inspect the sandbox egress network (other networks are left out
  of the list, and only this stack's containers are shown attached to it),
  create it with inter-container traffic off, and remove it only when this
  proxy created it (the egress firewall recreates it when it predates its
  required options).

Request bodies are bounded: a create body by size and nesting depth, an
archive upload by ``DOCKER_PROXY_MAX_UPLOAD_MB`` and by idle and total
timeouts. Each client address may hold at most
``DOCKER_PROXY_MAX_CONNECTIONS_PER_CLIENT`` connections at once. It listens
on ``DOCKER_PROXY_HOST`` (default 127.0.0.1; Compose sets 0.0.0.0 on its
internal control network) and has no client authentication of its own, so
keep it off any network a user can reach.

Path-only proxies cannot express the create rules: they allow
``/containers/create`` or they don't. That is why this one reads the body.

It also removes its own sandbox containers that outlive any run (a worker
that crashed mid-run leaves its container behind), see ``Reaper``.

Stdlib only, so the proxy starts without importing the app's dependencies.
Run with ``python -m app.sandbox.docker_proxy``.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import itertools
import json
import logging
import math
import os
import re
import signal
import socket
import stat
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any, TypeVar
from urllib.parse import parse_qs, quote, unquote, urlsplit

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping

_T = TypeVar("_T")

logger = logging.getLogger("docker_proxy")

MANAGED_LABEL = "ai.pipeshub.docker-proxy.managed"
# Which stack's proxy created a container. Every stack stamps the same
# MANAGED_LABEL, so on a daemon shared by several stacks only this label
# keeps one stack's proxy (and its reaper) away from another's containers.
INSTANCE_LABEL = "ai.pipeshub.docker-proxy.instance"
DEFAULT_INSTANCE = "default"
_INSTANCE_RE = re.compile(r"[A-Za-z0-9_.:/-]{1,128}")
_UPSTREAM_TIMEOUT_S = 30.0
_DEFAULT_HOST = "127.0.0.1"

_BOOT_ID_PATH = "/proc/sys/kernel/random/boot_id"

_DEFAULT_SANDBOX_IMAGE = "pipeshub/sandbox:latest"
_DEFAULT_EGRESS_NETWORK = "pipeshub_sandbox_egress"

# The egress firewall (agent_loop_lib/sandbox/coding/egress.py) installs
# iptables rules as root inside the container's own network namespace, then
# drops to an unprivileged user. Nothing here reaches the host: NET_ADMIN is
# scoped to the container's netns because host networking is refused.
_ALLOWED_CAPS = frozenset({"NET_ADMIN", "NET_RAW", "SETUID", "SETGID", "SETPCAP"})
_ALLOWED_SECURITY_OPTS = frozenset(
    {"no-new-privileges", "no-new-privileges:true", "no-new-privileges=true"}
)
_ALLOWED_NETWORK_OPTIONS = frozenset({"com.docker.network.bridge.enable_icc"})
_ALLOWED_RUNTIMES = frozenset({"", "runc", "runsc"})

_ALLOWED_LOG_DRIVERS = frozenset({"json-file", "local"})
_DEFAULT_JSON_LOG_SIZE = "10m"
_DEFAULT_JSON_LOG_FILES = "3"
# LogConfig.Config may only bound the log files' size: both drivers rotate on
# these two keys, and the bounds keep one container's logs at 500 MiB at most.
_LOG_MAX_SIZE_BYTES = 100 * 1024 * 1024
_LOG_MAX_FILES = 5
# The daemon's own size syntax (go-units RAMInBytes), binary units.
_LOG_SIZE = re.compile(r"([0-9]{1,12}(?:\.[0-9]{1,6})?) ?([kKmMgG]?)[iI]?[bB]?")
_LOG_FILES = re.compile(r"[0-9]{1,2}")
_LOG_SIZE_UNITS = {"": 1, "k": 1024, "m": 1024**2, "g": 1024**3}

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
    "CapDrop", "Memory", "NanoCpus", "PidsLimit", "ReadonlyRootfs", "Tmpfs",
    "StorageOpt", "MemorySwap", "OomScoreAdj",
})
_LOG_CONFIG_FIELDS = frozenset({"Type", "Config"})
_RESTART_POLICY_FIELDS = frozenset({"Name"})
_MOUNT_FIELDS = frozenset({"Type"})
_NETWORKING_CONFIG_FIELDS = frozenset({"EndpointsConfig"})
# What docker-py's EndpointConfig can send. Closed: the daemon attaches a
# user-defined network's endpoint by its NetworkID when one is given, not by
# the EndpointsConfig key the policy checks, so a NetworkID would join any
# network on the daemon (another stack's, the default bridge).
_ENDPOINT_FIELDS = frozenset({"Aliases", "Links", "IPAMConfig", "DriverOpts", "MacAddress"})
# Non-empty values of these are refused: an alias registers a DNS name on a
# bridge other stacks may share, and a link names a container the daemon
# looks up at start, whichever stack owns it.
_ENDPOINT_EMPTY_FIELDS = ("Aliases", "Links")
# Closed: what docker-py's networks.create can send.
_NETWORK_CREATE_FIELDS = frozenset({
    "Name", "CheckDuplicate", "Driver", "Options", "IPAM", "Internal", "Labels",
    "Attachable", "Ingress", "Scope", "ConfigOnly", "ConfigFrom", "EnableIPv6",
})
_ICC_OPTION = "com.docker.network.bridge.enable_icc"
# Network inspect keys that describe other endpoints on the network (swarm
# peers and services); "Containers" is narrowed to this stack's instead.
_NETWORK_FOREIGN_KEYS = ("Peers", "Services")
# Query parameters a container create may carry. ``name`` is refused: a
# conflicting name makes the daemon answer with the id of the container
# that holds it, whichever stack that is.
_CREATE_QUERY = frozenset({"platform"})

_MAX_HEAD_BYTES = 64 * 1024
_MAX_POLICY_BODY_BYTES = 1024 * 1024
# Docker's own bodies nest a few levels deep; this only stops a body built
# to exhaust the parser's recursion.
_MAX_JSON_DEPTH = 32
# A filtered response (network list / inspect) is read whole before it is
# rewritten.
_MAX_FILTERED_RESPONSE_BYTES = 16 * 1024 * 1024
_HEAD_TIMEOUT_S = 30.0
_COPY_CHUNK = 64 * 1024

_VERSION_PREFIX = re.compile(r"^/v\d+(?:\.\d+)?(?=/)")
# Request bodies the daemon parses into the same form as the query string
# (Go's Request.ParseForm / ParseMultipartForm), ahead of the query values the
# policy checks: a form body on /images/create turns a pull of the allowed
# image into an import from any URL, tagged as the allowed image.
_FORM_CONTENT_TYPES = ("application/x-www-form-urlencoded", "multipart/form-data")
# A chunk-size line is the hex size and CRLF only: chunk extensions and
# trailers are refused (Docker clients send neither), so every byte the proxy
# forwards is either data or this fixed framing.
_CHUNK_SIZE_LINE = re.compile(rb"([0-9A-Fa-f]{1,16})\r\n")
_CONTENT_LENGTH = re.compile(r"[0-9]{1,19}")
_CONTAINER_IN_TARGET = re.compile(r"^((?:/v\d+(?:\.\d+)?)?/containers/)[^/?]+")
_NETWORK_IN_TARGET = re.compile(r"^((?:/v\d+(?:\.\d+)?)?/networks/)[^/?]+")
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
    scope: str | None = None


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


_DEFAULT_MAX_MEMORY_MB = 1024
_DEFAULT_MAX_CPUS = 1.0
# Equal to the sandbox's own `pids_limit`: the process budget per container
# that capacity planning assumes is only true if the proxy enforces it.
_DEFAULT_MAX_PIDS = 256
_MAX_OOM_SCORE_ADJ = 1000


def _positive_env(env: Mapping[str, str], key: str, default: float, cast: Callable[[str], float]) -> float:
    raw = (env.get(key) or "").strip()
    if not raw:
        return default
    try:
        value = cast(raw)
    except ValueError:
        value = 0
    if not math.isfinite(value) or value <= 0:
        logger.warning("%s=%r is not a positive number; using %g", key, raw, default)
        return default
    return value


def _log_options(raw: object) -> dict[str, str]:
    """``HostConfig.LogConfig.Config`` as forwarded: only ``max-size`` (a
    size of 1 byte to 100 MiB, as the daemon writes it: ``10m``, ``1.5MiB``)
    and ``max-file`` (1 to 5), as strings. Anything else is refused."""
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise PolicyDenied("HostConfig.LogConfig.Config must be an object")
    extra = set(raw) - {"max-size", "max-file"}
    if extra:
        raise PolicyDenied(f"log options {sorted(map(str, extra))} are not permitted")
    out: dict[str, str] = {}
    size = raw.get("max-size")
    if size is not None:
        m = _LOG_SIZE.fullmatch(size) if isinstance(size, str) else None
        n = float(m.group(1)) * _LOG_SIZE_UNITS[m.group(2).lower()] if m else 0
        if not 1 <= n <= _LOG_MAX_SIZE_BYTES:
            raise PolicyDenied(
                f"log option max-size must be a size of at most {_LOG_MAX_SIZE_BYTES // (1024 * 1024)}m"
            )
        out["max-size"] = size
    files = raw.get("max-file")
    if files is not None:
        if not (isinstance(files, str) and _LOG_FILES.fullmatch(files) and 0 < int(files) <= _LOG_MAX_FILES):
            raise PolicyDenied(f"log option max-file must be 1 to {_LOG_MAX_FILES}")
        out["max-file"] = files
    return out


def _json_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


@dataclass
class DockerApiPolicy:
    allowed_images: frozenset[str]
    allowed_networks: frozenset[str]
    # Per-container ceilings. A create must set all three, within them.
    max_memory_bytes: int = _DEFAULT_MAX_MEMORY_MB * 1024 * 1024
    max_nano_cpus: int = int(_DEFAULT_MAX_CPUS * 1e9)
    max_pids: int = _DEFAULT_MAX_PIDS
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
        max_mb = _positive_env(env, "DOCKER_PROXY_MAX_MEMORY_MB", _DEFAULT_MAX_MEMORY_MB, int)
        max_cpus = _positive_env(env, "DOCKER_PROXY_MAX_CPUS", _DEFAULT_MAX_CPUS, float)
        max_pids = _positive_env(env, "DOCKER_PROXY_MAX_PIDS", _DEFAULT_MAX_PIDS, int)
        return cls(
            allowed_images=frozenset(images), allowed_networks=frozenset(networks),
            max_memory_bytes=int(max_mb) * 1024 * 1024, max_nano_cpus=int(max_cpus * 1e9),
            max_pids=int(max_pids),
        )

    def limits_text(self) -> str:
        return (
            f"memory<= {self.max_memory_bytes // (1024 * 1024)} MB (no swap), "
            f"cpus<= {self.max_nano_cpus / 1e9:g}, pids<= {self.max_pids}, "
            f"oom_score_adj 0..{_MAX_OOM_SCORE_ADJ}"
        )

    def image_allowed(self, ref: object) -> bool:
        return isinstance(ref, str) and bool(ref) and normalize_image(ref) in self._allowed_normalized

    def route(self, method: str, target: str) -> Route:
        """Classify a request. Raises ``PolicyDenied`` for anything unlisted."""
        # The daemon keeps a '#' in the path and redirects a path with empty
        # or dot segments to its cleaned form; neither is ever needed here.
        if "#" in target:
            raise PolicyDenied("a fragment in the request target is not permitted")
        path = _VERSION_PREFIX.sub("", urlsplit(target).path)
        if any(seg in ("", ".", "..") for seg in unquote(path).split("/")[1:]):
            raise PolicyDenied("empty or dot path segments are not permitted")
        query = parse_qs(urlsplit(target).query, keep_blank_values=True)

        if path.startswith("/_pipeshub/"):
            return _local_route(method, path, query)
        if path == "/_ping" and method in ("GET", "HEAD"):
            return Route("allow")
        if path == "/version" and method == "GET":
            return Route("allow")
        if path == "/info" and method == "GET":
            return Route("info")

        if path == "/containers/create" and method == "POST":
            extra = set(query) - _CREATE_QUERY
            if extra:
                raise PolicyDenied(f"container create parameters {sorted(extra)} are not permitted")
            return Route("container_create")
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
                # The daemon pulls every tag of a repository named without
                # a tag or digest.
                if "@" not in name and ":" not in name.rsplit("/", 1)[-1]:
                    raise PolicyDenied(f"pulling {name!r} needs an explicit tag or digest")
            if not self.image_allowed(ref):
                raise PolicyDenied(f"pulling image {ref!r} is not permitted")
            return Route("allow")
        m = re.fullmatch(r"/images/(.+)/json", path)
        if m and method == "GET":
            ref = unquote(m.group(1))
            if not self.image_allowed(ref):
                raise PolicyDenied(f"inspecting image {ref!r} is not permitted")
            return Route("image_inspect")

        if path == "/networks" and method == "GET":
            return Route("network_list")
        if path == "/networks/create" and method == "POST":
            return Route("network_create")
        m = re.fullmatch(r"/networks/([^/]+)", path)
        if m and method in ("GET", "DELETE"):
            return Route("network_allowed", network=unquote(m.group(1)))

        raise PolicyDenied(f"{method} {path} is not permitted")

    def check_container_create(self, body: dict[str, Any], instance: str = DEFAULT_INSTANCE) -> dict[str, Any]:
        """Validate a create payload and return it with the ownership labels
        (``instance`` names the stack; see ``INSTANCE_LABEL``)."""
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

        for raw_mount in host.get("Mounts") or []:
            mount = _struct_object(raw_mount, _MOUNT_FIELDS, "HostConfig.Mounts[]")
            if mount.get("Type") != "tmpfs":
                raise PolicyDenied("only tmpfs mounts are permitted")

        # StorageOpt caps the container's writable layer (the per-run disk
        # quota). Only the size cap is a resource limit; refuse anything else.
        storage_opt = host.get("StorageOpt")
        if storage_opt is not None and (not isinstance(storage_opt, dict) or set(storage_opt) - {"size"}):
            raise PolicyDenied("HostConfig.StorageOpt may only set 'size'")


        cap_add = {str(c).upper().removeprefix("CAP_") for c in host.get("CapAdd") or []}
        extra = cap_add - _ALLOWED_CAPS
        if extra:
            raise PolicyDenied(f"capabilities {sorted(extra)} are not permitted")

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
        # Network log drivers (syslog, gelf, fluentd, ...) connect from the
        # daemon's network, whatever the container's NetworkMode is. An unset
        # Type takes the daemon's default driver, which may be one of them, so
        # pin it. json-file is the default because docker-py's non-detached
        # ``containers.run`` only returns output for it; json-file never rotates
        # on its own, so it always gets a size and file cap.
        log_config = _struct_object(
            host.get("LogConfig"), _LOG_CONFIG_FIELDS, "HostConfig.LogConfig", closed=True,
        )
        log_driver = log_config.get("Type") or "json-file"
        if log_driver not in _ALLOWED_LOG_DRIVERS:
            raise PolicyDenied(f"log driver {log_driver!r} is not permitted")
        log_options = _log_options(log_config.get("Config"))
        if log_driver == "json-file":
            log_options.setdefault("max-size", _DEFAULT_JSON_LOG_SIZE)
            log_options.setdefault("max-file", _DEFAULT_JSON_LOG_FILES)

        network_mode = host.get("NetworkMode") or ""
        if network_mode != "none" and network_mode not in self.allowed_networks:
            raise PolicyDenied(f"network mode {network_mode or 'default'!r} is not permitted")
        networking = _struct_object(
            body.get("NetworkingConfig"), _NETWORKING_CONFIG_FIELDS, "NetworkingConfig",
        )
        endpoints = networking.get("EndpointsConfig") or {}
        if not isinstance(endpoints, dict):
            raise PolicyDenied("NetworkingConfig.EndpointsConfig must be an object")
        for name, endpoint in endpoints.items():
            if name not in self.allowed_networks:
                raise PolicyDenied(f"network {name!r} is not permitted")
            settings = _struct_object(endpoint, _ENDPOINT_FIELDS, f"EndpointsConfig[{name!r}]", closed=True)
            for key in _ENDPOINT_EMPTY_FIELDS:
                if settings.get(key):
                    raise PolicyDenied(f"EndpointsConfig[{name!r}].{key} is not permitted")

        swap = self._check_resources(host)

        raw_labels = body.get("Labels")
        if raw_labels is None:
            raw_labels = {}
        if not isinstance(raw_labels, dict):
            raise PolicyDenied("Labels must be an object")
        labels = dict(raw_labels)
        labels[MANAGED_LABEL] = "true"
        labels[INSTANCE_LABEL] = instance
        return {
            **body,
            "HostConfig": {**host, "MemorySwap": swap, "LogConfig": {"Type": log_driver, "Config": log_options}},
            "Labels": labels,
        }

    def _check_resources(self, host: dict[str, Any]) -> int:
        """Refuse a create without bounded memory, CPU and process limits,
        and return the ``MemorySwap`` to forward. Docker reads an unset or
        zero ``MemorySwap`` as "the same again in swap", so it is set equal
        to ``Memory`` (no swap) rather than required, which keeps older
        clients working. ``OomScoreAdj`` may only make a container more
        likely to be killed, never protect it."""
        max_mb = self.max_memory_bytes // (1024 * 1024)
        memory = host.get("Memory")
        if not _json_int(memory) or memory <= 0:
            raise PolicyDenied(f"HostConfig.Memory is required (bytes, at most {max_mb} MB)")
        if memory > self.max_memory_bytes:
            raise PolicyDenied(
                f"HostConfig.Memory {memory} exceeds the maximum of {max_mb} MB "
                "(DOCKER_PROXY_MAX_MEMORY_MB)"
            )
        max_cpus = f"{self.max_nano_cpus / 1e9:g}"
        nano_cpus = host.get("NanoCpus")
        if not _json_int(nano_cpus) or nano_cpus <= 0:
            raise PolicyDenied(f"HostConfig.NanoCpus is required (at most {max_cpus} CPUs)")
        if nano_cpus > self.max_nano_cpus:
            raise PolicyDenied(
                f"HostConfig.NanoCpus {nano_cpus} exceeds the maximum of {max_cpus} CPUs "
                "(DOCKER_PROXY_MAX_CPUS)"
            )
        # 0 and -1 mean "unlimited" to Docker.
        pids = host.get("PidsLimit")
        if not _json_int(pids) or pids <= 0:
            raise PolicyDenied(f"HostConfig.PidsLimit is required (1..{self.max_pids})")
        if pids > self.max_pids:
            raise PolicyDenied(
                f"HostConfig.PidsLimit {pids} exceeds the maximum of {self.max_pids} (DOCKER_PROXY_MAX_PIDS)"
            )
        swap = host.get("MemorySwap")
        if swap is not None and swap != 0 and (not _json_int(swap) or swap != memory):
            raise PolicyDenied("HostConfig.MemorySwap must equal Memory: sandboxes may not use swap")
        oom = host.get("OomScoreAdj")
        if oom is not None:
            if not _json_int(oom) or oom < 0:
                raise PolicyDenied(
                    "HostConfig.OomScoreAdj may not be negative: a sandbox must never be "
                    "protected from the OOM killer"
                )
            if oom > _MAX_OOM_SCORE_ADJ:
                raise PolicyDenied(f"HostConfig.OomScoreAdj may not be above {_MAX_OOM_SCORE_ADJ}")
        return memory

    def check_network_create(self, body: dict[str, Any], instance: str = DEFAULT_INSTANCE) -> dict[str, Any]:
        """Validate a network create and return it with the ownership labels:
        only a network carrying this ``instance`` may later be removed."""
        if not isinstance(body, dict):
            raise PolicyDenied("network create body must be a JSON object")
        _struct_object(body, _NETWORK_CREATE_FIELDS, "network create body", closed=True)
        name = body.get("Name") or ""
        if name not in self.allowed_networks:
            raise PolicyDenied(f"creating network {name!r} is not permitted")
        if (body.get("Driver") or "bridge") != "bridge":
            raise PolicyDenied("only bridge networks are permitted")
        options = body.get("Options") or {}
        if not isinstance(options, dict):
            raise PolicyDenied("network Options must be an object")
        extra = set(options) - _ALLOWED_NETWORK_OPTIONS
        if extra:
            raise PolicyDenied(f"network options {sorted(extra)} are not permitted")
        # Sandboxes on the bridge must not reach each other directly.
        if options.get(_ICC_OPTION) != "false":
            raise PolicyDenied(f"network option {_ICC_OPTION} must be \"false\"")
        # EnableIPv6: the sandbox egress firewall only installs IPv4 rules.
        for key in ("IPAM", "ConfigFrom", "ConfigOnly", "Ingress", "Scope", "EnableIPv6", "Attachable"):
            if body.get(key):
                raise PolicyDenied(f"network {key} is not permitted")
        raw_labels = body.get("Labels")
        if raw_labels is None:
            raw_labels = {}
        if not isinstance(raw_labels, dict):
            raise PolicyDenied("network Labels must be an object")
        labels = dict(raw_labels)
        labels[MANAGED_LABEL] = "true"
        labels[INSTANCE_LABEL] = instance
        # An explicit false: docker-py omits a false EnableIPv6, and newer
        # daemons then apply their default-network-opts, which may turn it on.
        return {**body, "EnableIPv6": False, "Labels": labels}


def _local_route(method: str, path: str, query: Mapping[str, list[str]]) -> Route:
    """Routes the proxy answers itself; none is ever forwarded."""
    if method not in ("GET", "HEAD"):
        raise PolicyDenied(f"{method} {path} is not permitted")
    if path == "/_pipeshub/host" and not query:
        return Route("local_host_id")
    if path == "/_pipeshub/usage" and set(query) <= {"scope"}:
        scope = query.get("scope")
        if scope is None:
            return Route("local_usage")
        if scope == ["containers"]:
            return Route("local_usage", scope="containers")
    raise PolicyDenied(f"{method} {path} is not permitted")


def boot_id_hash(path: str | None = None) -> str | None:
    """The first 32 hex digits of the SHA-256 of the kernel's boot id, the
    same for every container on one kernel. Only the hash leaves the proxy."""
    try:
        with open(path or _BOOT_ID_PATH) as f:
            boot_id = f.read().strip()
    except OSError:
        return None
    return hashlib.sha256(boot_id.encode()).hexdigest()[:32] if boot_id else None


# --- Sidecar usage (/_pipeshub/usage) ----------------------------------------

_SIDECAR_STATS_MAX_BYTES = 16 * 1024
# Four of the exporter's 5 s intervals.
_SIDECAR_STALE_S = 20.0
_RATE_WINDOW_S = 10.0
_SIDECAR_KEYS = frozenset({
    "v", "seq", "memory.max", "memory.current", "cpu.max", "memory.stat.file",
    "memory.stat.shmem", "memory.stat.anon", "memory.pressure.full.avg10",
    "cpu.pressure.some.avg10", "dockerd.rss_kb", "containerd.rss_kb",
})
_SIDECAR_PREFIXES = ("memory.events.local.", "cpu.stat.")
_CONTAINERS_CACHE_S = 5.0
_CONTAINERS_MAX = 256
_STATS_CONCURRENCY = 8
_STATS_CALL_TIMEOUT_S = 2.0
_STATS_BUDGET_S = 4.0
_LIST_TIMEOUT_S = 2.0


def _number(raw: str) -> float:
    try:
        return int(raw)
    except ValueError:
        value = float(raw)
    if value != value or value in (float("inf"), float("-inf")):
        raise ValueError(raw)
    return value


def parse_sidecar_stats(text: str) -> dict[str, float | None]:
    """The exporter's ``key value`` lines. Lines with other keys are ignored;
    a known key with a value that is not a number raises ``ValueError``.
    ``memory.max`` / the ``cpu.max`` quota are ``None`` when unlimited."""
    values: dict[str, float | None] = {}
    for line in text.splitlines():
        key, _, rest = line.strip().partition(" ")
        rest = rest.strip()
        if key not in _SIDECAR_KEYS and not key.startswith(_SIDECAR_PREFIXES):
            continue
        if key == "cpu.max":
            parts = rest.split()
            if len(parts) != 2:
                raise ValueError(line)
            values["cpu.max.quota"] = None if parts[0] == "max" else _number(parts[0])
            values["cpu.max.period"] = _number(parts[1])
        elif key == "memory.max" and rest == "max":
            values[key] = None
        else:
            values[key] = _number(rest)
    return values


def _delta(new: Mapping[str, float | None], old: Mapping[str, float | None], key: str) -> float | None:
    a, b = new.get(key), old.get(key)
    return None if a is None or b is None else a - b


class SidecarStats:
    """The sidecar block of ``/_pipeshub/usage``, read from the exporter's
    file. Keeps the last few snapshots to turn its cumulative counters into
    rates; there is no background task, the ring fills on requests."""

    def __init__(self, path: str | None, *, clock: Callable[[], float] = time.time) -> None:
        self._path = path
        self._clock = clock
        self._ring: deque[tuple[float, dict[str, float | None]]] = deque(maxlen=4)

    def read(self) -> tuple[dict[str, Any] | None, str | None]:
        """``(block, None)`` or ``(None, error)``."""
        if not self._path:
            return None, "not_configured"
        try:
            fd = os.open(self._path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
        except FileNotFoundError:
            return None, "missing"
        except OSError:
            return None, "unparsable"
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_size > _SIDECAR_STATS_MAX_BYTES:
                return None, "unparsable"
            data = os.read(fd, _SIDECAR_STATS_MAX_BYTES + 1)
        finally:
            os.close(fd)
        age = self._clock() - info.st_mtime
        if age > _SIDECAR_STALE_S:
            return None, "stale"
        try:
            if len(data) > _SIDECAR_STATS_MAX_BYTES:
                raise ValueError("too large")
            values = parse_sidecar_stats(data.decode("ascii"))
        except (ValueError, UnicodeDecodeError):
            return None, "unparsable"
        return self._block(info.st_mtime, max(0.0, age), values), None

    def _remember(self, taken_at: float, values: dict[str, float | None]) -> None:
        if self._ring:
            last_at, last = self._ring[-1]
            if values.get("seq") is not None and values.get("seq") == last.get("seq"):
                return
            went_back = any(
                (d := _delta(values, last, key)) is not None and d < 0
                for key in ("seq", "cpu.stat.usage_usec", "cpu.stat.nr_periods")
            )
            if went_back or taken_at < last_at:
                # The exporter or the sidecar restarted: counters start over.
                self._ring.clear()
        self._ring.append((taken_at, values))

    def _block(self, taken_at: float, age: float, values: dict[str, float | None]) -> dict[str, Any]:
        self._remember(taken_at, values)
        ring = list(self._ring)
        new_at, new = ring[-1]
        older = next((snap for snap in reversed(ring[:-1]) if new_at - snap[0] >= _RATE_WINDOW_S), None)
        if older is None and len(ring) > 1:
            older = ring[0]
        quota, period = new.get("cpu.max.quota"), new.get("cpu.max.period")
        cpus = quota / period if quota and period else None
        window = cpu_util = throttled = oom = None
        if older is not None and new_at > older[0]:
            window = new_at - older[0]
            used = _delta(new, older[1], "cpu.stat.usage_usec")
            if used is not None and cpus:
                cpu_util = max(0.0, used / (window * 1e6 * cpus))
            periods = _delta(new, older[1], "cpu.stat.nr_periods")
            hits = _delta(new, older[1], "cpu.stat.nr_throttled")
            if periods is not None and hits is not None:
                throttled = max(0.0, hits / periods) if periods > 0 else 0.0
            oom_delta = _delta(new, older[1], "memory.events.local.oom")
            oom = None if oom_delta is None else max(0, int(oom_delta))
        current = new.get("memory.current")
        cache = (new.get("memory.stat.file") or 0) - (new.get("memory.stat.shmem") or 0)
        mem_psi, cpu_psi = new.get("memory.pressure.full.avg10"), new.get("cpu.pressure.some.avg10")
        dockerd, containerd = new.get("dockerd.rss_kb"), new.get("containerd.rss_kb")
        return {
            "ageS": round(age, 1), "seq": new.get("seq"), "windowS": window,
            "memLimitBytes": new.get("memory.max"),
            # Page cache is reclaimable; tmpfs (shmem, counted in file) is not.
            "memWorkingSetBytes": None if current is None else max(0, int(current - max(0.0, cache))),
            "memPsiFullAvg10": None if mem_psi is None else mem_psi / 100.0,
            "oomEventsDelta": oom,
            "cpuLimitMillis": None if cpus is None else round(cpus * 1000),
            "cpuUtil": cpu_util, "cpuThrottledRatio": throttled,
            "cpuPsiSomeAvg10": None if cpu_psi is None else cpu_psi / 100.0,
            "dockerdRssBytes": None if dockerd is None else int(dockerd * 1024),
            "containerdRssBytes": None if containerd is None else int(containerd * 1024),
        }


def _split_csv(value: str | None) -> list[str]:
    return [v.strip() for v in (value or "").split(",") if v.strip()]


# The sandbox uploads a run's input files and its installed dependencies
# (a Python or Node tree that can reach a few GB) through PUT /archive; the
# writable layer that receives them defaults to 10 GB (SANDBOX_DISK_QUOTA).
_DEFAULT_MAX_UPLOAD_MB = 10 * 1024
_DEFAULT_BODY_IDLE_TIMEOUT_S = 120.0
_DEFAULT_BODY_TIMEOUT_S = 1800.0
# Every running sandbox holds a few connections (wait, logs, archive); a
# Helm pod's processes all reach the proxy from 127.0.0.1.
_DEFAULT_MAX_CONNECTIONS_PER_CLIENT = 512


@dataclass(frozen=True)
class ProxyLimits:
    """Bounds on what one client can hold open or send through the proxy."""

    max_upload_bytes: int = _DEFAULT_MAX_UPLOAD_MB * 1024 * 1024
    # Longest wait for the next piece of a forwarded body, and for all of it.
    body_idle_timeout_s: float = _DEFAULT_BODY_IDLE_TIMEOUT_S
    body_timeout_s: float = _DEFAULT_BODY_TIMEOUT_S
    max_connections_per_client: int = _DEFAULT_MAX_CONNECTIONS_PER_CLIENT

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> ProxyLimits:
        """``DOCKER_PROXY_MAX_UPLOAD_MB`` (default 10240),
        ``DOCKER_PROXY_BODY_IDLE_TIMEOUT_S`` (120), ``DOCKER_PROXY_BODY_TIMEOUT_S``
        (1800) and ``DOCKER_PROXY_MAX_CONNECTIONS_PER_CLIENT`` (512). A value
        that is not a positive number warns and keeps the default."""
        env = os.environ if env is None else env
        upload_mb = _positive_env(env, "DOCKER_PROXY_MAX_UPLOAD_MB", _DEFAULT_MAX_UPLOAD_MB, int)
        return cls(
            max_upload_bytes=int(upload_mb) * 1024 * 1024,
            body_idle_timeout_s=_positive_env(
                env, "DOCKER_PROXY_BODY_IDLE_TIMEOUT_S", _DEFAULT_BODY_IDLE_TIMEOUT_S, float,
            ),
            body_timeout_s=_positive_env(env, "DOCKER_PROXY_BODY_TIMEOUT_S", _DEFAULT_BODY_TIMEOUT_S, float),
            max_connections_per_client=int(_positive_env(
                env, "DOCKER_PROXY_MAX_CONNECTIONS_PER_CLIENT", _DEFAULT_MAX_CONNECTIONS_PER_CLIENT, int,
            )),
        )

    def text(self) -> str:
        return (
            f"uploads<= {self.max_upload_bytes // (1024 * 1024)} MB, body idle<= {self.body_idle_timeout_s:g}s, "
            f"body total<= {self.body_timeout_s:g}s, connections per client<= {self.max_connections_per_client}"
        )


def _json_depth_ok(raw: bytes, limit: int = _MAX_JSON_DEPTH) -> bool:
    """Whether no array or object in ``raw`` nests deeper than ``limit``,
    checked before parsing so a hostile body never reaches the recursive
    decoder. Brackets inside strings do not count."""
    depth = 0
    in_string = escaped = False
    for byte in raw:
        if in_string:
            if escaped:
                escaped = False
            elif byte == 0x5C:  # backslash
                escaped = True
            elif byte == 0x22:  # quote
                in_string = False
        elif byte == 0x22:
            in_string = True
        elif byte in (0x5B, 0x7B):  # [ {
            depth += 1
            if depth > limit:
                return False
        elif byte in (0x5D, 0x7D):  # ] }
            depth -= 1
    return True


def _parse_policy_body(raw: bytes) -> object:
    if not _json_depth_ok(raw):
        raise PolicyDenied("request body nests too deeply")
    try:
        return json.loads(raw or b"{}", object_pairs_hook=_reject_duplicate_keys)
    except (ValueError, RecursionError) as exc:
        raise PolicyDenied("request body is not valid JSON") from exc


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
    def form_body(self) -> bool:
        """Whether the daemon would read the body as form fields."""
        for k, v in self.headers:
            if k.lower() == "content-type":
                media = v.split(";", 1)[0].strip().lower()
                if media in _FORM_CONTENT_TYPES:
                    return True
        return False

    @property
    def chunked(self) -> bool:
        return "chunked" in (self.header("transfer-encoding") or "").lower()

    @property
    def content_length(self) -> int:
        raw = self.header("content-length")
        if raw is None:
            return 0
        # Decimal digits only: int() would also take "+1", "1_0" and
        # non-ASCII digits, which the daemon reads differently or not at all.
        if not _CONTENT_LENGTH.fullmatch(raw):
            raise PolicyDenied("invalid Content-Length")
        return int(raw)


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


def _chunk_size(size_line: bytes) -> int:
    """The size of a chunk from its CRLF-terminated size line. Stricter than
    the daemon: Python's ``int(x, 16)`` would also take ``0x``, ``+``, ``-``
    and ``_``, and chunk extensions, whitespace and bare CR or LF are
    refused outright."""
    m = _CHUNK_SIZE_LINE.fullmatch(size_line)
    if not m:
        raise PolicyDenied("malformed chunk size line (chunk extensions are not permitted)")
    return int(m.group(1), 16)


async def _size_line(reader: asyncio.StreamReader, idle_s: float | None = None) -> bytes:
    try:
        return await _idle(reader.readuntil(b"\r\n"), idle_s)
    except asyncio.LimitOverrunError as exc:
        raise PolicyDenied("malformed chunk size line") from exc


async def _end_of_chunks(reader: asyncio.StreamReader, idle_s: float | None = None) -> None:
    """Read the CRLF after the last chunk; trailers are refused."""
    if await _idle(reader.readexactly(2), idle_s) != b"\r\n":
        raise PolicyDenied("chunked trailers are not permitted")


async def _end_of_chunk(reader: asyncio.StreamReader, idle_s: float | None = None) -> None:
    if await _idle(reader.readexactly(2), idle_s) != b"\r\n":
        raise PolicyDenied("malformed chunk: data is not followed by CRLF")


async def _read_chunked(reader: asyncio.StreamReader, limit: int) -> bytes:
    body = bytearray()
    while True:
        size = _chunk_size(await _size_line(reader))
        if size == 0:
            await _end_of_chunks(reader)
            return bytes(body)
        if len(body) + size > limit:
            raise PolicyDenied("request body too large")
        body += await reader.readexactly(size)
        await _end_of_chunk(reader)


class BodyTooLarge(PolicyDenied):
    """A forwarded body went past ``ProxyLimits.max_upload_bytes``."""


async def _idle(awaitable: Awaitable[_T], idle_s: float | None) -> _T:
    return await (asyncio.wait_for(awaitable, idle_s) if idle_s else awaitable)


async def _pipe_chunked(
    reader: asyncio.StreamReader, writer: asyncio.StreamWriter, *,
    limit: int | None = None, idle_s: float | None = None,
) -> None:
    """Forward a chunked body, stopping after the terminating chunk. Every
    forwarded byte, framing included, counts toward ``limit``; past it
    ``BodyTooLarge`` is raised. ``idle_s`` bounds each wait for the client."""
    total = 0

    def count(n: int) -> None:
        nonlocal total
        total += n
        if limit is not None and total > limit:
            raise BodyTooLarge("request body too large")

    while True:
        # A valid size line is at most 16 hex digits and CRLF; anything else
        # is refused before it is forwarded.
        size_line = await _size_line(reader, idle_s)
        size = _chunk_size(size_line)
        count(len(size_line) + size + 2)
        writer.write(size_line)
        if size == 0:
            await _end_of_chunks(reader, idle_s)
            writer.write(b"\r\n")
            await writer.drain()
            return
        remaining = size
        while remaining:
            chunk = await _idle(reader.read(min(remaining, _COPY_CHUNK)), idle_s)
            if not chunk:
                raise asyncio.IncompleteReadError(b"", remaining)
            writer.write(chunk)
            remaining -= len(chunk)
            await writer.drain()
        await _end_of_chunk(reader, idle_s)
        writer.write(b"\r\n")
        await writer.drain()


async def _pipe_exact(
    reader: asyncio.StreamReader, writer: asyncio.StreamWriter, n: int, *, idle_s: float | None = None,
) -> None:
    while n:
        chunk = await _idle(reader.read(min(n, _COPY_CHUNK)), idle_s)
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


# The /info fields the app sizes the sandbox ceiling from, and the storage
# driver it decides disk-quota enforcement from. Raw /info also carries daemon
# labels, registry mirrors and HTTP(S)_PROXY settings (which can hold
# credentials), swarm and plugin details, and every container's count.
INFO_FIELDS = (
    "NCPU", "MemTotal", "KernelVersion", "OperatingSystem", "OSType", "Architecture",
    "ServerVersion", "CgroupVersion", "CgroupDriver", "Driver", "DriverStatus",
)
# The DriverStatus entries passed through: the backing filesystem, and the
# driver type that marks a containerd snapshotter (which accepts a size quota
# without enforcing it). The rest can name host paths and pool devices.
_DRIVER_STATUS_KEYS = frozenset({"backing filesystem", "driver-type"})


def filter_info(info: Mapping[str, Any]) -> dict[str, Any]:
    """The client's view of ``/info``: ``INFO_FIELDS`` only. ``DriverStatus``
    is always present (a list, possibly empty) and keeps only the entries in
    ``_DRIVER_STATUS_KEYS``."""
    out = {k: info[k] for k in INFO_FIELDS if k in info and k != "DriverStatus"}
    status = info.get("DriverStatus")
    out["DriverStatus"] = [
        [pair[0], pair[1]] for pair in status if isinstance(pair, (list, tuple)) and len(pair) == 2
        and isinstance(pair[0], str) and isinstance(pair[1], str)
        and pair[0].lower() in _DRIVER_STATUS_KEYS
    ] if isinstance(status, list) else []
    return out


# Image inspect as answered to clients: what docker-py's Image object reads
# (id, tags, labels in Config) and the platform. Dropped: GraphDriver (host
# paths of the layers), the build's Container id and ContainerConfig, Parent,
# Metadata and RootFS.
IMAGE_FIELDS = (
    "Id", "RepoTags", "RepoDigests", "Created", "Architecture", "Variant", "Os",
    "OsVersion", "Size", "Config",
)


def filter_image(image: object) -> object:
    if not isinstance(image, dict):
        return image
    return {k: image[k] for k in IMAGE_FIELDS if k in image}


_REASONS = {200: "OK", 400: "Bad Request", 403: "Forbidden", 404: "Not Found", 500: "Internal Server Error"}


def _json_response(payload: object, status: int = 200) -> bytes:
    body = json.dumps(payload).encode()
    head = _serialize_head(
        f"HTTP/1.1 {status} {_REASONS.get(status, 'Error')}",
        [("Content-Type", "application/json"), ("Content-Length", str(len(body))),
         ("Connection", "close")],
    )
    return head + body


class DockerSocketProxy:
    def __init__(
        self, policy: DockerApiPolicy, upstream_socket: str, *, instance: str = DEFAULT_INSTANCE,
        sidecar_stats_file: str | None = None, limits: ProxyLimits | None = None,
    ) -> None:
        self._policy = policy
        self._upstream = upstream_socket
        self.instance = instance
        self.limits = limits or ProxyLimits()
        # Open connections per client address.
        self._connections: dict[str, int] = {}
        self._sidecar = SidecarStats(sidecar_stats_file)
        self._containers_lock = asyncio.Lock()
        self._containers_cache: tuple[float, dict[str, Any] | None, str | None] | None = None
        # Last cumulative CPU time (ns) and when it was read, per container.
        self._containers_cpu: dict[str, tuple[float, float]] = {}
        # The stream server holds a handler task only through its transport,
        # which the loop lets go of while reading is paused (a large body
        # arriving during the ownership lookup); a GC pass then collects the
        # request mid-flight and the client sees an empty reply.
        self._inflight: set[asyncio.Task[None]] = set()

    async def _upstream_json(self, path: str) -> tuple[int, Any]:
        return await self._upstream_call("GET", path)

    async def _upstream_call(
        self, method: str, path: str, timeout: float = _UPSTREAM_TIMEOUT_S,
    ) -> tuple[int, Any]:
        """One bodiless request to the daemon, returning ``(status, json)``;
        bounded by ``timeout`` so a hung daemon cannot hang the caller."""
        return await asyncio.wait_for(self._upstream_request(method, path), timeout)

    async def _upstream_request(
        self, method: str, path: str, max_bytes: int | None = None,
    ) -> tuple[int, Any]:
        reader, writer = await asyncio.open_unix_connection(self._upstream)
        try:
            writer.write(_serialize_head(
                f"{method} {path} HTTP/1.1", [("Host", "docker"), ("Connection", "close")]
            ))
            await writer.drain()
            if max_bytes is None:
                raw = await reader.read()
            else:
                raw = b""
                while chunk := await reader.read(_COPY_CHUNK):
                    raw += chunk
                    if len(raw) > max_bytes:
                        raise ValueError("upstream response too large")
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

    async def containers_usage(self) -> tuple[dict[str, Any] | None, str | None]:
        """The summed stats of this proxy's running containers, cached briefly
        so a slow daemon is not asked again at once."""
        async with self._containers_lock:
            now = time.monotonic()
            cached = self._containers_cache
            if cached is not None and now - cached[0] < _CONTAINERS_CACHE_S:
                return cached[1], cached[2]
            block, error = await self._containers_usage(now)
            self._containers_cache = (time.monotonic(), block, error)
            return block, error

    async def _containers_usage(self, now: float) -> tuple[dict[str, Any] | None, str | None]:
        filters = json.dumps(
            {"label": [f"{MANAGED_LABEL}=true", f"{INSTANCE_LABEL}={self.instance}"], "status": ["running"]},
            separators=(",", ":"),
        )
        try:
            status, items = await self._upstream_call(
                "GET", f"/containers/json?filters={quote(filters, safe='')}", _LIST_TIMEOUT_S,
            )
        except (OSError, asyncio.TimeoutError, ValueError):
            return None, "list_failed"
        if status != 200 or not isinstance(items, list):
            return None, "list_failed"
        ids = [item["Id"] for item in items if isinstance(item, dict) and isinstance(item.get("Id"), str)]
        truncated = len(ids) > _CONTAINERS_MAX
        ids = ids[:_CONTAINERS_MAX]
        gate = asyncio.Semaphore(_STATS_CONCURRENCY)

        async def one(ident: str) -> tuple[int, Any]:
            async with gate:
                return await self._upstream_call(
                    "GET", f"/containers/{quote(ident, safe='')}/stats?stream=false&one-shot=true",
                    _STATS_CALL_TIMEOUT_S,
                )

        tasks = {asyncio.ensure_future(one(ident)): ident for ident in ids}
        done, pending = await asyncio.wait(tasks, timeout=_STATS_BUDGET_S) if tasks else (set(), set())
        for task in pending:
            task.cancel()
        partial = bool(pending)
        memory = 0
        cores, seen_twice = 0.0, False
        cpu_now: dict[str, tuple[float, float]] = {}
        for task in done:
            ident = tasks[task]
            try:
                status, data = task.result()
            except Exception:
                partial = True
                continue
            if status != 200 or not isinstance(data, dict):
                partial = True
                continue
            mem = data.get("memory_stats") or {}
            mem_stats = mem.get("stats") or {}
            memory += max(0, int(mem.get("usage") or 0) - int(mem_stats.get("file") or 0) + int(mem_stats.get("shmem") or 0))
            usage = ((data.get("cpu_stats") or {}).get("cpu_usage") or {}).get("total_usage")
            if not isinstance(usage, (int, float)):
                continue
            cpu_now[ident] = (float(usage), now)
            previous = self._containers_cpu.get(ident)
            if previous is not None and now > previous[1] and usage >= previous[0]:
                cores += (usage - previous[0]) / 1e9 / (now - previous[1])
                seen_twice = True
        # Containers that are gone drop out; ones not read this time keep their last value.
        self._containers_cpu = {
            ident: cpu_now.get(ident) or self._containers_cpu[ident]
            for ident in ids if ident in cpu_now or ident in self._containers_cpu
        }
        return {
            "running": len(ids), "memWorkingSetBytes": memory,
            "cpuCores": cores if seen_twice else None, "partial": partial, "truncated": truncated,
        }, None

    async def _owned_container_id(self, ident: str) -> str:
        status, info = await self._upstream_json(f"/containers/{quote(ident, safe='')}/json")
        labels: dict[str, str] = {}
        if status == 200 and isinstance(info, dict):
            labels = (info.get("Config") or {}).get("Labels") or {}
        # One answer for missing, unmanaged and another stack's containers,
        # so the refusal says nothing about what exists on the daemon.
        if labels.get(MANAGED_LABEL) != "true" or labels.get(INSTANCE_LABEL) != self.instance:
            raise PolicyDenied("container is not managed by this sandbox proxy")
        return str(info["Id"])

    async def _check_network(self, ident: str, *, owned: bool = False) -> str:
        """The id of sandbox network ``ident``. ``owned`` also requires that
        this proxy created it (it carries this stack's instance label)."""
        status, info = await self._upstream_json(f"/networks/{quote(ident, safe='')}")
        if status != 200 or not isinstance(info, dict) or info.get("Name") not in self._policy.allowed_networks:
            raise PolicyDenied("network is not a sandbox network")
        if owned:
            labels = info.get("Labels") or {}
            if not isinstance(labels, dict) or labels.get(INSTANCE_LABEL) != self.instance:
                raise PolicyDenied("network was not created by this sandbox proxy")
        if not isinstance(info.get("Id"), str) or not info["Id"]:
            raise PolicyDenied("network is not a sandbox network")
        return info["Id"]

    async def _own_container_ids(self) -> set[str]:
        status, items = await self._upstream_call(
            "GET", _containers_query(f"{INSTANCE_LABEL}={self.instance}"), _LIST_TIMEOUT_S,
        )
        if status != 200 or not isinstance(items, list):
            raise RuntimeError(f"listing containers returned {status}")
        return {str(item["Id"]) for item in items if _owned_by(item, self.instance) and item.get("Id")}

    async def _redact_network(self, network: object, own: set[str] | None) -> tuple[object, set[str] | None]:
        """``network`` with only this stack's containers attached and no swarm
        peers or services. ``own`` caches this stack's container ids."""
        if not isinstance(network, dict):
            return network, own
        out = {k: v for k, v in network.items() if k not in _NETWORK_FOREIGN_KEYS}
        # Labels name the stack that created the network (its instance).
        labels = network.get("Labels")
        if "Labels" in network and not (isinstance(labels, dict) and labels.get(INSTANCE_LABEL) == self.instance):
            out["Labels"] = {}
        attached = network.get("Containers")
        if isinstance(attached, dict) and attached:
            if own is None:
                own = await self._own_container_ids()
            out["Containers"] = {k: v for k, v in attached.items() if k in own}
        elif attached is not None:
            out["Containers"] = {}
        return out, own

    async def _filtered_networks(self, path: str, *, single: bool) -> tuple[int, Any]:
        """Network list or inspect: only the sandbox networks, each redacted."""
        status, payload = await self._upstream_call_capped(path)
        if status != 200:
            return status, payload
        own: set[str] | None = None
        if single:
            if not isinstance(payload, dict) or payload.get("Name") not in self._policy.allowed_networks:
                raise PolicyDenied("network is not a sandbox network")
            redacted, _ = await self._redact_network(payload, own)
            return status, redacted
        if not isinstance(payload, list):
            raise ValueError("network list is not a list")
        kept = []
        for item in payload:
            if isinstance(item, dict) and item.get("Name") in self._policy.allowed_networks:
                redacted, own = await self._redact_network(item, own)
                kept.append(redacted)
        return status, kept

    async def _upstream_call_capped(self, path: str) -> tuple[int, Any]:
        return await asyncio.wait_for(
            self._upstream_request("GET", path, _MAX_FILTERED_RESPONSE_BYTES), _UPSTREAM_TIMEOUT_S,
        )

    async def handle(self, client_r: asyncio.StreamReader, client_w: asyncio.StreamWriter) -> None:
        task = asyncio.current_task()
        if task is not None:
            self._inflight.add(task)
            task.add_done_callback(self._inflight.discard)
        peer = client_w.get_extra_info("peername")
        client = str(peer[0]) if isinstance(peer, tuple) and peer else str(peer or "")
        open_now = self._connections.get(client, 0)
        if open_now >= self.limits.max_connections_per_client:
            logger.warning("refused a connection from %s: %d already open", client, open_now)
            await _safe_write(client_w, _error_response(
                503, "Service Unavailable", "docker-proxy: too many connections from this client",
            ))
            client_w.close()
            return
        self._connections[client] = open_now + 1
        try:
            await self._handle(client_r, client_w)
        except PolicyDenied as exc:
            logger.warning("denied: %s", exc)
            await _safe_write(client_w, _error_response(403, "Forbidden", f"docker-proxy: {exc}"))
        except (asyncio.IncompleteReadError, ConnectionError, asyncio.TimeoutError):
            pass
        except Exception:
            logger.exception("proxy error")
            await _safe_write(client_w, _error_response(502, "Bad Gateway", "docker-proxy: upstream error"))
        finally:
            left = self._connections.get(client, 1) - 1
            if left > 0:
                self._connections[client] = left
            else:
                self._connections.pop(client, None)
            client_w.close()

    async def _handle(self, client_r: asyncio.StreamReader, client_w: asyncio.StreamWriter) -> None:
        raw_head = await asyncio.wait_for(_read_head(client_r), _HEAD_TIMEOUT_S)
        req = _parse_request_head(raw_head)
        route = self._policy.route(req.method, req.target)
        target = req.target
        if req.form_body:
            raise PolicyDenied("form-encoded request bodies are not permitted")
        # Only the create routes and an archive upload carry a body; the
        # daemon would read one on /images/create.
        takes_body = route.kind in ("container_create", "network_create") or (
            route.kind == "container_owned" and req.method == "PUT"
        )
        if not takes_body and (req.chunked or req.content_length):
            raise PolicyDenied("a request body is not permitted on this endpoint")
        if route.kind == "container_owned" and req.content_length > self.limits.max_upload_bytes:
            raise BodyTooLarge(
                f"request body is larger than {self.limits.max_upload_bytes // (1024 * 1024)} MB "
                "(DOCKER_PROXY_MAX_UPLOAD_MB)"
            )
        # Clients that sent Expect hold the body back until told to continue,
        # and the create routes read it before going upstream.
        expects_continue = (req.header("expect") or "").lower() == "100-continue"

        if route.kind == "local_host_id":
            digest = boot_id_hash()
            if digest is None:
                await _safe_write(client_w, _error_response(502, "Bad Gateway", "docker-proxy: boot id unavailable"))
                return
            await _safe_write(client_w, _json_response({"v": 1, "bootIdHash": digest}))
            return

        if route.kind == "local_usage":
            if route.scope == "containers":
                block, error = await self.containers_usage()
                payload = {"v": 1, "containers": block, "containersError": error}
            else:
                block, error = self._sidecar.read()
                payload = {"v": 1, "sidecar": block, "sidecarError": error}
            await _safe_write(client_w, _json_response(payload))
            return

        if route.kind == "info":
            status, info = await self._upstream_json("/info")
            if status != 200 or not isinstance(info, dict):
                await _safe_write(client_w, _error_response(502, "Bad Gateway", "docker-proxy: upstream /info failed"))
                return
            await _safe_write(client_w, _json_response(filter_info(info)))
            return

        body: bytes | None = None
        if route.kind in ("container_create", "network_create"):
            if expects_continue:
                client_w.write(b"HTTP/1.1 100 Continue\r\n\r\n")
                await client_w.drain()
            if req.chunked:
                raw = await asyncio.wait_for(_read_chunked(client_r, _MAX_POLICY_BODY_BYTES), _HEAD_TIMEOUT_S)
            else:
                if req.content_length > _MAX_POLICY_BODY_BYTES:
                    raise PolicyDenied("request body too large")
                raw = await asyncio.wait_for(client_r.readexactly(req.content_length), _HEAD_TIMEOUT_S)
            payload = _parse_policy_body(raw)
            if route.kind == "container_create":
                payload = self._policy.check_container_create(payload, self.instance)
            else:
                payload = self._policy.check_network_create(payload, self.instance)
            body = json.dumps(payload).encode()
        elif route.kind == "container_owned":
            assert route.container is not None
            full_id = await self._owned_container_id(route.container)
            # Address the container by the id we checked, so a name reused
            # between the check and the call cannot redirect it.
            target = _CONTAINER_IN_TARGET.sub(rf"\g<1>{full_id}", target, count=1)
        elif route.kind == "image_inspect":
            status, image = await self._upstream_call_capped(target)
            await _safe_write(client_w, _json_response(filter_image(image) if status == 200 else image, status))
            return
        elif route.kind == "network_list":
            status, listed = await self._filtered_networks(target, single=False)
            await _safe_write(client_w, _json_response(listed, status))
            return
        elif route.kind == "network_allowed":
            assert route.network is not None
            network_id = await self._check_network(route.network, owned=req.method == "DELETE")
            target = _NETWORK_IN_TARGET.sub(rf"\g<1>{network_id}", target, count=1)
            if req.method == "GET":
                status, info = await self._filtered_networks(target, single=True)
                await _safe_write(client_w, _json_response(info, status))
                return

        logger.debug("allow %s %s", req.method, target)
        headers = [(k, v) for k, v in req.headers if k.lower() not in _HOP_BY_HOP]
        if body is not None:
            headers = [(k, v) for k, v in headers
                       if k.lower() not in ("content-length", "transfer-encoding")]
            headers.append(("Content-Length", str(len(body))))
        headers.append(("Connection", "close"))

        if expects_continue and body is None:
            client_w.write(b"HTTP/1.1 100 Continue\r\n\r\n")
            await client_w.drain()

        up_r, up_w = await asyncio.open_unix_connection(self._upstream)
        try:
            up_w.write(_serialize_head(f"{req.method} {target} {req.version}", headers))
            if body is not None:
                up_w.write(body)
            elif req.chunked or req.content_length:
                await self._forward_body(req, client_r, up_w)
            await up_w.drain()

            resp_head = _parse_response_head(await up_r.readuntil(b"\r\n\r\n"))
            client_w.write(resp_head)
            await client_w.drain()
            await _pipe_to_eof(up_r, client_w)
        finally:
            up_w.close()


    async def _forward_body(
        self, req: _Request, client_r: asyncio.StreamReader, up_w: asyncio.StreamWriter,
    ) -> None:
        """Pipe an archive upload to the daemon within the size cap and the
        idle and total timeouts. On a breach the daemon connection is closed
        mid-body, so the daemon drops the upload."""
        limits = self.limits
        if req.chunked:
            pipe = _pipe_chunked(
                client_r, up_w, limit=limits.max_upload_bytes, idle_s=limits.body_idle_timeout_s,
            )
        else:
            pipe = _pipe_exact(client_r, up_w, req.content_length, idle_s=limits.body_idle_timeout_s)
        try:
            await asyncio.wait_for(pipe, limits.body_timeout_s)
        except asyncio.TimeoutError:
            logger.warning("dropped a request body: the client stalled or took too long")
            raise


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


# --- Which stack this proxy serves ------------------------------------------


async def resolve_instance(env: Mapping[str, str], upstream: str) -> str:
    """The value stamped in ``INSTANCE_LABEL``, resolved once at startup.

    1. ``DOCKER_PROXY_INSTANCE`` when set. An invalid value stops the proxy:
       guessing would let two stacks share a label.
    2. Else the Compose project of the proxy's own container
       (``compose:<project>``), found by inspecting its hostname, which is
       the container id unless the service sets ``hostname:``.
    3. Else ``default``, with a warning.
    """
    raw = (env.get("DOCKER_PROXY_INSTANCE") or "").strip()
    if raw:
        if not _INSTANCE_RE.fullmatch(raw):
            raise ValueError(
                "DOCKER_PROXY_INSTANCE must be 1-128 characters from A-Z a-z 0-9 _ . : / -"
            )
        return raw
    probe = DockerSocketProxy(DockerApiPolicy(frozenset(), frozenset()), upstream)
    try:
        status, info = await probe._upstream_call(
            "GET", f"/containers/{quote(socket.gethostname(), safe='')}/json", timeout=10,
        )
    except (OSError, asyncio.TimeoutError, ValueError, IndexError):
        status, info = 0, None
    labels = ((info or {}).get("Config") or {}).get("Labels") or {} if status == 200 else {}
    project = labels.get("com.docker.compose.project") if isinstance(labels, dict) else None
    if project:
        candidate = f"compose:{project}"
        if _INSTANCE_RE.fullmatch(candidate):
            return candidate
    logger.warning(
        "docker proxy: no instance id (set DOCKER_PROXY_INSTANCE); if several stacks share "
        "this Docker daemon they will see and reap each other's sandbox containers"
    )
    return DEFAULT_INSTANCE


# --- Removing sandbox containers that outlive their run ---------------------

_DEFAULT_MAX_RUN_TIMEOUT_S = 300.0
_INSTALL_DEADLINE_S = 300.0
# Slack on top of the run cap; a container is aged from its latest start or
# exit, and one not started yet also gets the proxy's body timeout for its upload.
_REAP_MARGIN_S = 120.0
_DEFAULT_REAP_INTERVAL_S = 60.0
_MIN_REAP_INTERVAL_S = 10.0
_REAP_PASS_TIMEOUT_S = 30.0
# How often the count of unlabelled sandbox containers left alone is logged.
_UNLABELED_LOG_EVERY_S = 3600.0
_TRUE = frozenset({"1", "true", "yes", "on"})
_FALSE = frozenset({"", "0", "false", "no", "off"})


def _max_run_timeout_s(env: Mapping[str, str]) -> float:
    """``SANDBOX_MAX_RUN_TIMEOUT_S``, parsed exactly as the app parses it
    (``agent_loop_lib/sandbox/coding/settings.py``; this module is stdlib-only
    and cannot import it): missing, invalid or ``<= 0`` → 300, else clamped
    to 10..3600."""
    raw = (env.get("SANDBOX_MAX_RUN_TIMEOUT_S") or "").strip()
    try:
        value = float(raw) if raw else _DEFAULT_MAX_RUN_TIMEOUT_S
    except ValueError:
        return _DEFAULT_MAX_RUN_TIMEOUT_S
    if value != value or value <= 0:  # NaN
        return _DEFAULT_MAX_RUN_TIMEOUT_S
    return min(max(value, 10.0), 3600.0)


@dataclass(frozen=True)
class ReaperSettings:
    reap_after_s: float | None  # None: the reaper is off
    interval_s: float
    # Also remove managed containers that carry no instance label at all.
    reap_unlabeled: bool = False

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> ReaperSettings:
        """``DOCKER_PROXY_REAP_AFTER_S`` (``0`` turns the reaper off) or, by
        default, the longest a run or an install may take plus a margin; an
        age below that default is raised to it, so the reaper never removes
        a run still in time. ``DOCKER_PROXY_REAP_INTERVAL_S``, default 60, at
        least 10. ``DOCKER_PROXY_REAP_UNLABELED``, default off."""
        env = os.environ if env is None else env
        default_after = max(_max_run_timeout_s(env), _INSTALL_DEADLINE_S) + _REAP_MARGIN_S
        reap_after: float | None = default_after
        raw = (env.get("DOCKER_PROXY_REAP_AFTER_S") or "").strip()
        if raw:
            try:
                value = float(raw)
            except ValueError:
                value = math.nan
            if value == 0:
                reap_after = None
            elif math.isfinite(value) and value >= default_after:
                reap_after = value
            elif math.isfinite(value) and value > 0:
                logger.warning(
                    "DOCKER_PROXY_REAP_AFTER_S=%r is below the longest a run or an install may take "
                    "plus %gs (SANDBOX_MAX_RUN_TIMEOUT_S); using %gs",
                    raw, _REAP_MARGIN_S, default_after,
                )
            else:
                logger.warning("DOCKER_PROXY_REAP_AFTER_S=%r is not valid; using %gs", raw, default_after)
        interval = _DEFAULT_REAP_INTERVAL_S
        raw = (env.get("DOCKER_PROXY_REAP_INTERVAL_S") or "").strip()
        if raw:
            try:
                value = float(raw)
            except ValueError:
                value = math.nan
            if math.isfinite(value):
                interval = max(value, _MIN_REAP_INTERVAL_S)
            else:
                logger.warning(
                    "DOCKER_PROXY_REAP_INTERVAL_S=%r is not valid; using %gs", raw, _DEFAULT_REAP_INTERVAL_S,
                )
        raw = (env.get("DOCKER_PROXY_REAP_UNLABELED") or "").strip().lower()
        reap_unlabeled = raw in _TRUE
        if not reap_unlabeled and raw not in _FALSE:
            logger.warning("DOCKER_PROXY_REAP_UNLABELED=%r is not true or false; leaving it off", raw)
        return cls(reap_after_s=reap_after, interval_s=interval, reap_unlabeled=reap_unlabeled)


def _docker_time(value: object) -> float | None:
    """Seconds since the epoch of a daemon timestamp such as
    ``2026-01-02T03:04:05.123456789Z``; None when unset or unreadable."""
    if not isinstance(value, str) or not value or value.startswith("0001-"):
        return None
    head, _, rest = value.partition(".")
    if rest:
        digits = "".join(itertools.takewhile(str.isdigit, rest))
        zone = rest[len(digits):]
        value = f"{head}.{digits[:6]}{zone}"
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


class Reaper:
    """Removes this stack's sandbox containers older than ``reap_after_s``.

    A run's container normally lives for at most its timeout plus the time
    to copy files in and out, and the app removes it when the run ends. One
    left behind (the query worker crashed, or was killed mid-run) is not
    counted anywhere and may run until its in-container deadline, or forever
    in an image without ``timeout``. It runs here because the proxy is the
    one process that holds the socket and outlives the query workers.
    Only containers carrying this proxy's ``INSTANCE_LABEL`` are listed, so
    other stacks on the same daemon are never touched.

    A managed container with no instance label at all was started by a proxy
    from before the label: one left over from before an upgrade, or one of
    another stack on the same daemon that still runs such a proxy, whose
    runs may be longer than this stack allows. Those are only removed (past
    the same age) with ``reap_unlabeled``; otherwise their count is logged,
    at most once an hour.
    """

    def __init__(
        self, proxy: DockerSocketProxy, settings: ReaperSettings, *,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._proxy = proxy
        self._settings = settings
        self._clock = clock
        self._unlabeled_logged_at: float | None = None

    async def run(self, stop: asyncio.Event) -> None:
        reap_after = self._settings.reap_after_s
        if reap_after is None:
            logger.warning(
                "docker proxy: the sandbox container reaper is off (DOCKER_PROXY_REAP_AFTER_S=0); "
                "containers left behind by a crashed worker are not removed"
            )
            return
        logger.info(
            "docker proxy reaper: removes instance %r sandbox containers older than %gs, every %gs",
            self._proxy.instance, reap_after, self._settings.interval_s,
        )
        await self._report_legacy()
        while not stop.is_set():
            try:
                await asyncio.wait_for(self.reap_once(), _REAP_PASS_TIMEOUT_S)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("reaper: pass skipped: %s", exc)
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(stop.wait(), self._settings.interval_s)

    async def reap_once(self) -> int:
        """One pass; returns how many containers it removed."""
        reap_after = self._settings.reap_after_s
        if reap_after is None:
            return 0
        status, listed = await self._proxy._upstream_call(
            "GET", _containers_query(f"{INSTANCE_LABEL}={self._proxy.instance}"),
        )
        if status != 200 or not isinstance(listed, list):
            raise RuntimeError(f"listing containers returned {status}")
        status, managed = await self._proxy._upstream_call("GET", _containers_query(f"{MANAGED_LABEL}=true"))
        if status != 200 or not isinstance(managed, list):
            raise RuntimeError(f"listing containers returned {status}")
        # The label filters are checked again here: a daemon (or a
        # Docker-compatible API) that ignored them would otherwise hand every
        # container on it, databases included, to the reaper.
        candidates = [item for item in listed if _owned_by(item, self._proxy.instance)]
        unlabeled = [item for item in managed if _legacy(item)]
        now = self._clock()
        if self._settings.reap_unlabeled:
            candidates += unlabeled
        else:
            self._log_unlabeled(len(unlabeled), now)
        removed = 0
        for item in candidates:
            created = item.get("Created")
            ident = str(item.get("Id") or "")
            if not ident or not isinstance(created, (int, float)) or now - created <= reap_after:
                continue
            since = await self._active_since(item, ident, created)
            if since is None or now - since <= reap_after:
                continue
            delete_status, _ = await self._proxy._upstream_call(
                "DELETE", f"/containers/{quote(ident, safe='')}?force=1",
            )
            if delete_status in (200, 204, 404):
                removed += delete_status != 404
                if delete_status != 404:
                    logger.warning(
                        "reaped sandbox container %.12s (state=%s, age=%ds > %ds)",
                        ident, item.get("State"), int(now - created), int(reap_after),
                    )
            else:
                logger.warning("reaper: removing %.12s returned %s", ident, delete_status)
        if removed:
            logger.info("reaper: removed %d container(s)", removed)
        return removed

    async def _active_since(self, item: dict[str, Any], ident: str, created: float) -> float | None:
        """When the container's age starts counting, or None to skip it this
        pass. A container that is not started yet may still be receiving its
        input archive, for up to the proxy's body timeout; one that started
        (or exited) counts from its latest start or exit, so a slow upload
        does not use up its run. A started container whose start and exit
        times cannot be read is skipped: a client may upload several archives
        before starting it, so its creation time says nothing about its run."""
        state = item.get("State")
        upload_bound = created + self._proxy.limits.body_timeout_s
        if state == "created":
            return upload_bound
        try:
            status, info = await self._proxy._upstream_call("GET", f"/containers/{quote(ident, safe='')}/json")
        except Exception:
            return None
        times = info.get("State") if status == 200 and isinstance(info, dict) else None
        if not isinstance(times, dict):
            return None
        stamps = [t for t in (_docker_time(times.get(key)) for key in ("StartedAt", "FinishedAt")) if t is not None]
        if not stamps:
            return None
        return max([created, *stamps])

    def _log_unlabeled(self, count: int, now: float) -> None:
        if not count:
            return
        last = self._unlabeled_logged_at
        if last is not None and 0 <= now - last < _UNLABELED_LOG_EVERY_S:
            return
        self._unlabeled_logged_at = now
        logger.warning(
            "%d sandbox container(s) carry no instance label (created by an older docker proxy, "
            "possibly another stack's on this daemon) and are not removed. Remove leftovers by hand, "
            "or set DOCKER_PROXY_REAP_UNLABELED=true once every stack on this daemon is upgraded",
            count,
        )

    async def _report_legacy(self) -> None:
        """Say how many containers from a proxy that predates the instance
        label there are, and whether they will be reaped."""
        try:
            status, listed = await self._proxy._upstream_call(
                "GET", _containers_query(f"{MANAGED_LABEL}=true"),
            )
        except Exception as exc:
            logger.debug("reaper: legacy count failed: %s", exc)
            return
        if status != 200 or not isinstance(listed, list):
            return
        legacy = sum(1 for item in listed if _legacy(item))
        if not legacy:
            return
        if self._settings.reap_unlabeled:
            logger.info(
                "%d sandbox container(s) from an older proxy; removed once older than %gs "
                "(DOCKER_PROXY_REAP_UNLABELED)",
                legacy, self._settings.reap_after_s,
            )
        else:
            self._log_unlabeled(legacy, self._clock())


def _owned_by(item: object, instance: str) -> bool:
    """A sandbox container this stack's proxy created."""
    if not isinstance(item, dict):
        return False
    labels = item.get("Labels") or {}
    return isinstance(labels, dict) and labels.get(MANAGED_LABEL) == "true" and labels.get(INSTANCE_LABEL) == instance


def _legacy(item: object) -> bool:
    """A managed container that carries no instance label at all."""
    if not isinstance(item, dict):
        return False
    labels = item.get("Labels") or {}
    return isinstance(labels, dict) and labels.get(MANAGED_LABEL) == "true" and INSTANCE_LABEL not in labels


def _containers_query(label: str) -> str:
    filters = json.dumps({"label": [label]}, separators=(",", ":"))
    return f"/containers/json?all=1&filters={quote(filters, safe='')}"


async def serve(
    host: str, port: int, upstream: str, policy: DockerApiPolicy, *,
    instance: str | None = None, reaper: ReaperSettings | None = None,
    limits: ProxyLimits | None = None,
) -> None:
    if instance is None:
        instance = await resolve_instance(os.environ, upstream)
    limits = limits or ProxyLimits.from_env()
    proxy = DockerSocketProxy(
        policy, upstream, instance=instance,
        sidecar_stats_file=os.environ.get("DOCKER_PROXY_SIDECAR_STATS_FILE") or None,
        limits=limits,
    )
    server = await asyncio.start_server(proxy.handle, host, port, limit=_MAX_HEAD_BYTES)
    logger.info(
        "docker proxy listening on %s:%d -> %s (instance=%s images=%s networks=%s)",
        host, port, upstream, instance, sorted(policy.allowed_images), sorted(policy.allowed_networks),
    )
    logger.info("docker proxy limits: %s; %s", policy.limits_text(), limits.text())
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    reaper_task = asyncio.create_task(
        Reaper(proxy, reaper or ReaperSettings.from_env()).run(stop), name="docker-proxy-reaper",
    )
    try:
        async with server:
            await stop.wait()
    finally:
        reaper_task.cancel()
        await asyncio.gather(reaper_task, return_exceptions=True)


def main() -> None:
    logging.basicConfig(
        level=os.environ.get("DOCKER_PROXY_LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    asyncio.run(serve(
        # Loopback unless told otherwise: the proxy has no client
        # authentication, so listening on every interface must be a choice.
        os.environ.get("DOCKER_PROXY_HOST") or _DEFAULT_HOST,
        int(os.environ.get("DOCKER_PROXY_PORT", "2375")),
        _upstream_path(os.environ.get("DOCKER_PROXY_UPSTREAM", "/var/run/docker.sock")),
        DockerApiPolicy.from_env(),
    ))


if __name__ == "__main__":
    main()
