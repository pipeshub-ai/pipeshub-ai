"""Policy-enforcing proxy between the coding sandbox and the Docker daemon.

The app used to mount ``/var/run/docker.sock`` directly, so anything that
compromised the app process was root on the host: one ``POST
/containers/create`` with ``Privileged`` and a bind of ``/`` is enough. This
proxy is the only process that touches the socket. The app reaches it over
TCP (``DOCKER_HOST=tcp://docker-socket-proxy:2375``) and the Docker SDK works
unchanged, but only the calls the sandbox makes get through:

- ping / version;
- create a container from an allowed sandbox image with no host access
  (no privileged mode, binds, devices, host namespaces, port publishing,
  unconfined security profiles, or capabilities beyond the egress firewall's);
- start / wait / kill / stop / logs / archive / inspect / remove, only on
  containers this proxy created (it stamps a label at create time);
- inspect / pull of the allowed sandbox images;
- list networks, and create / inspect / remove the sandbox egress network
  (the egress firewall recreates it when it predates its required options).

Path-only proxies cannot express the create rules: they allow
``/containers/create`` or they don't. That is why this one reads the body.

Stdlib only, so the proxy starts without importing the app's dependencies.
Run with ``python -m app.sandbox.docker_proxy``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import signal
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs, quote, unquote, urlsplit

logger = logging.getLogger("docker_proxy")

MANAGED_LABEL = "ai.pipeshub.docker-proxy.managed"

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

_ALLOWED_LOG_DRIVERS = frozenset({"", "json-file", "local"})

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
})
_LOG_CONFIG_FIELDS = frozenset({"Type", "Config"})
_RESTART_POLICY_FIELDS = frozenset({"Name"})
_MOUNT_FIELDS = frozenset({"Type"})
_NETWORKING_CONFIG_FIELDS = frozenset({"EndpointsConfig"})
_NETWORK_CREATE_FIELDS = frozenset({
    "Name", "Driver", "Options", "IPAM", "ConfigFrom", "ConfigOnly", "Ingress", "Scope",
    "EnableIPv6",
})

_MAX_HEAD_BYTES = 64 * 1024
_MAX_POLICY_BODY_BYTES = 1024 * 1024
_HEAD_TIMEOUT_S = 30.0
_COPY_CHUNK = 64 * 1024

_VERSION_PREFIX = re.compile(r"^/v\d+(?:\.\d+)?(?=/)")
_CONTAINER_IN_TARGET = re.compile(r"^((?:/v\d+(?:\.\d+)?)?/containers/)[^/?]+")
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
        return cls(allowed_images=frozenset(images), allowed_networks=frozenset(networks))

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
            return Route("network_create")
        m = re.fullmatch(r"/networks/([^/]+)", path)
        if m and method in ("GET", "DELETE"):
            return Route("network_allowed", network=unquote(m.group(1)))

        raise PolicyDenied(f"{method} {path} is not permitted")

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
        # daemon's network, whatever the container's NetworkMode is.
        log_config = _struct_object(
            host.get("LogConfig"), _LOG_CONFIG_FIELDS, "HostConfig.LogConfig", closed=True,
        )
        if (log_config.get("Type") or "") not in _ALLOWED_LOG_DRIVERS:
            raise PolicyDenied(f"log driver {log_config.get('Type')!r} is not permitted")
        if log_config.get("Config"):
            raise PolicyDenied("HostConfig.LogConfig.Config is not permitted")

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

        raw_labels = body.get("Labels")
        if raw_labels is None:
            raw_labels = {}
        if not isinstance(raw_labels, dict):
            raise PolicyDenied("Labels must be an object")
        labels = dict(raw_labels)
        labels[MANAGED_LABEL] = "true"
        return {**body, "Labels": labels}

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
        for key in ("IPAM", "ConfigFrom", "ConfigOnly", "Ingress", "Scope", "EnableIPv6"):
            if body.get(key):
                raise PolicyDenied(f"network {key} is not permitted")
        return body


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
    if len(parts) != 3 or not parts[2].startswith("HTTP/1.") or not parts[1].startswith("/"):
        raise PolicyDenied("malformed request line")
    headers: list[tuple[str, str]] = []
    for line in lines[1:]:
        if not line:
            continue
        key, sep, value = line.partition(":")
        if not sep:
            raise PolicyDenied("malformed header")
        headers.append((key.strip(), value.strip()))
    return _Request(parts[0].upper(), parts[1], parts[2], headers)


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


class DockerSocketProxy:
    def __init__(self, policy: DockerApiPolicy, upstream_socket: str) -> None:
        self._policy = policy
        self._upstream = upstream_socket

    async def _upstream_json(self, path: str) -> tuple[int, Any]:
        reader, writer = await asyncio.open_unix_connection(self._upstream)
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

    async def _owned_container_id(self, ident: str) -> str:
        status, info = await self._upstream_json(f"/containers/{quote(ident, safe='')}/json")
        labels: dict[str, str] = {}
        if status == 200 and isinstance(info, dict):
            labels = (info.get("Config") or {}).get("Labels") or {}
        if labels.get(MANAGED_LABEL) != "true":
            raise PolicyDenied("container is not managed by the sandbox proxy")
        return str(info["Id"])

    async def _check_network(self, ident: str) -> None:
        status, info = await self._upstream_json(f"/networks/{quote(ident, safe='')}")
        if status != 200 or (info or {}).get("Name") not in self._policy.allowed_networks:
            raise PolicyDenied("network is not a sandbox network")

    async def handle(self, client_r: asyncio.StreamReader, client_w: asyncio.StreamWriter) -> None:
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
            client_w.close()

    async def _handle(self, client_r: asyncio.StreamReader, client_w: asyncio.StreamWriter) -> None:
        raw_head = await asyncio.wait_for(_read_head(client_r), _HEAD_TIMEOUT_S)
        req = _parse_request_head(raw_head)
        route = self._policy.route(req.method, req.target)
        target = req.target
        # Clients that sent Expect hold the body back until told to continue,
        # and the create routes read it before going upstream.
        expects_continue = (req.header("expect") or "").lower() == "100-continue"

        body: bytes | None = None
        if route.kind in ("container_create", "network_create"):
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
            if route.kind == "container_create":
                payload = self._policy.check_container_create(payload)
            else:
                payload = self._policy.check_network_create(payload)
            body = json.dumps(payload).encode()
        elif route.kind == "container_owned":
            assert route.container is not None
            full_id = await self._owned_container_id(route.container)
            # Address the container by the id we checked, so a name reused
            # between the check and the call cannot redirect it.
            target = _CONTAINER_IN_TARGET.sub(rf"\g<1>{full_id}", target, count=1)
        elif route.kind == "network_allowed":
            assert route.network is not None
            await self._check_network(route.network)

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
            elif req.chunked:
                await _pipe_chunked(client_r, up_w)
            elif req.content_length:
                await _pipe_exact(client_r, up_w, req.content_length)
            await up_w.drain()

            resp_head = _parse_response_head(await up_r.readuntil(b"\r\n\r\n"))
            client_w.write(resp_head)
            await client_w.drain()
            await _pipe_to_eof(up_r, client_w)
        finally:
            up_w.close()


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


async def serve(host: str, port: int, upstream: str, policy: DockerApiPolicy) -> None:
    proxy = DockerSocketProxy(policy, upstream)
    server = await asyncio.start_server(proxy.handle, host, port, limit=_MAX_HEAD_BYTES)
    logger.info(
        "docker proxy listening on %s:%d -> %s (images=%s networks=%s)",
        host, port, upstream, sorted(policy.allowed_images), sorted(policy.allowed_networks),
    )
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    async with server:
        await stop.wait()


def main() -> None:
    logging.basicConfig(
        level=os.environ.get("DOCKER_PROXY_LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    asyncio.run(serve(
        os.environ.get("DOCKER_PROXY_HOST", "0.0.0.0"),
        int(os.environ.get("DOCKER_PROXY_PORT", "2375")),
        _upstream_path(os.environ.get("DOCKER_PROXY_UPSTREAM", "/var/run/docker.sock")),
        DockerApiPolicy.from_env(),
    ))


if __name__ == "__main__":
    main()
