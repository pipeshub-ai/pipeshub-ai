"""Reading the machine the code sandboxes run on, for the live limit.

Docker does not namespace ``/proc/meminfo``, ``/proc/stat`` or
``/proc/pressure/*``, so in an app container they describe the whole kernel,
which is the Docker host when the daemon runs on the same machine.
``resolve_live_mode()`` decides whether that is so, and which reader applies:

- docker mode (a Compose install, a source install, or an app pointed at a
  remote daemon): ``HostReader`` on this container's ``/proc``, but only once
  the daemon is shown to share this kernel (the Docker proxy's boot id hash,
  or failing that the ``/info`` fingerprint);
- local mode: ``CgroupReader`` on this container's own cgroup, which the
  sandbox subprocesses share;
- a Docker-in-Docker sidecar (``SANDBOX_DOCKER_TOPOLOGY=dind-sidecar``):
  ``SidecarReader``, the sidecar's own cgroup counters served by the Docker
  proxy, or failing that the summed stats of the proxy's containers. The
  node's ``/proc`` would pass the boot id check there but describe the whole
  node, so this mode never reads it;
- E2B runs elsewhere: no reading.

Every reader returns a ``HostSample`` and never raises; a field it cannot
read is ``None``.
"""

from __future__ import annotations

import hashlib
import logging
import math
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, Protocol

from app.agent_loop_lib.sandbox.capacity import fingerprint_same_machine

if TYPE_CHECKING:
    from collections.abc import Callable

    from app.agent_loop_lib.sandbox.capacity import SandboxCapacity
    from app.agent_loop_lib.sandbox.live_settings import LiveSettings

__all__ = [
    "CgroupReader",
    "HostReader",
    "HostSample",
    "LiveMode",
    "Reader",
    "SidecarReader",
    "boot_id_hash",
    "docker_http_get",
    "resolve_live_mode",
    "resolve_sidecar_mode",
]

logger = logging.getLogger(__name__)

_PROC = Path("/proc")
# lxcfs bind-mounted over /proc/meminfo reports the container's own limit.
_LXCFS_TOLERANCE_BYTES = 2**20
_HOST_ROUTE_TIMEOUT_S = 2.0
_SIDECAR_ROUTE_TIMEOUT_S = 2.0
_CONTAINERS_ROUTE_TIMEOUT_S = 5.0
# The sidecar's memory.max must match the limit the chart passes in, or the
# cgroup the exporter sees is not the sidecar's (cgroup v1, a host namespace).
_SIDECAR_LIMIT_TOLERANCE = 0.01
# dockerd, containerd, shims and page cache cannot be seen from container
# stats; their share of the sidecar is left unmeasured.
_CONTAINERS_SHARE = 0.9
# The per-run check's sidecar reading: reused for this long, then refreshed
# by one request that concurrent checks share. A check waits for it at most
# _SIDECAR_MEMORY_WAIT_S, then uses a reading up to _SIDECAR_MEMORY_STALE_S
# old (the exporter writes every 5 s), or none, and the run is admitted.
_SIDECAR_MEMORY_FRESH_S = 1.0
_SIDECAR_MEMORY_WAIT_S = 0.05
_SIDECAR_MEMORY_STALE_S = 5.0

Source = Literal["host", "cgroup", "sidecar", "sidecar-containers"]


@dataclass(frozen=True)
class HostSample:
    mem_total: int | None = None
    mem_available: int | None = None
    cpu_util: float | None = None
    cpu_psi_some_avg10: float | None = None
    mem_psi_full_avg10: float | None = None
    cpu_throttled_ratio: float | None = None
    mem_oom_events: int | None = None
    source: Source | None = None
    # ``(busy, total)`` jiffies, so the live limit can measure CPU use since
    # the last step, which another worker may have taken.
    cpu_counters: tuple[int, int] | None = None


class Reader(Protocol):
    source: Source

    def read(self) -> HostSample:
        """One full sample; for the sampler. Never raises."""
        ...

    def memory(self) -> tuple[int | None, int | None]:
        """``(available, total)`` now, for the per-run check. Never raises."""
        ...


def _read(path: Path) -> str | None:
    try:
        return path.read_text()
    except (OSError, ValueError):
        return None


def _psi_avg10(text: str | None, kind: str) -> float | None:
    """``avg10`` of the ``some`` or ``full`` line of a PSI file, as a fraction."""
    if not text:
        return None
    for line in text.splitlines():
        if line.startswith(kind + " "):
            for field in line.split()[1:]:
                key, _, value = field.partition("=")
                if key == "avg10":
                    try:
                        return float(value) / 100.0
                    except ValueError:
                        return None
    return None


def parse_meminfo(text: str | None) -> tuple[int | None, int | None]:
    """``(MemTotal, MemAvailable)`` in bytes."""
    total = available = None
    for line in (text or "").splitlines():
        key, _, rest = line.partition(":")
        if key not in ("MemTotal", "MemAvailable"):
            continue
        try:
            value = int(rest.split()[0]) * 1024
        except (IndexError, ValueError):
            continue
        if key == "MemTotal":
            total = value
        else:
            available = value
    return total, available


def parse_proc_stat_cpu(text: str | None) -> tuple[int, int] | None:
    """``(busy, total)`` jiffies from the ``cpu`` line. Busy leaves out idle
    and iowait, which a sum of every field would count as busy."""
    for line in (text or "").splitlines():
        if line.startswith("cpu "):
            try:
                fields = [int(v) for v in line.split()[1:9]]
            except ValueError:
                return None
            if len(fields) < 5:
                return None
            total = sum(fields)
            return total - fields[3] - fields[4], total
    return None


class HostReader:
    """The kernel's view through ``/proc``: the Docker host, seen from a
    container on it."""

    source: Source = "host"

    def __init__(self, proc: Path = _PROC) -> None:
        self._proc = proc
        self._lock = threading.Lock()
        self._prev_cpu: tuple[int, int] | None = None

    def memory(self) -> tuple[int | None, int | None]:
        total, available = parse_meminfo(_read(self._proc / "meminfo"))
        return available, total

    def read(self) -> HostSample:
        available, total = self.memory()
        cpu = parse_proc_stat_cpu(_read(self._proc / "stat"))
        util = None
        with self._lock:
            prev, self._prev_cpu = self._prev_cpu, cpu
        if cpu is not None and prev is not None:
            d_busy, d_total = cpu[0] - prev[0], cpu[1] - prev[1]
            if d_total > 0 and d_busy >= 0:
                util = min(1.0, d_busy / d_total)
        pressure = self._proc / "pressure"
        return HostSample(
            mem_total=total, mem_available=available, cpu_util=util,
            cpu_psi_some_avg10=_psi_avg10(_read(pressure / "cpu"), "some"),
            mem_psi_full_avg10=_psi_avg10(_read(pressure / "memory"), "full"),
            source="host", cpu_counters=cpu,
        )


class CgroupReader:
    """This container's own cgroup, which local-mode sandboxes share."""

    source: Source = "cgroup"

    def __init__(self, probe_factory: Callable[[], Any] | None = None) -> None:
        if probe_factory is None:
            from app.services.resource_governor.probe import SystemResourceProbe

            probe_factory = SystemResourceProbe
        # Two probes: each keeps the previous CPU counter, so a memory read for
        # the per-run check must not shorten the sampler's CPU window.
        self._probe = probe_factory()
        self._memory_probe = probe_factory()

    @staticmethod
    def _memory_of(snap: Any) -> tuple[int | None, int | None]:
        limit, used = snap.mem_limit_bytes, snap.mem_working_set_raw_bytes
        if limit is None or used is None:
            return None, limit
        return max(0, limit - used), limit

    def memory(self) -> tuple[int | None, int | None]:
        try:
            return self._memory_of(self._memory_probe.snapshot())
        except Exception:
            return None, None

    def read(self) -> HostSample:
        try:
            snap = self._probe.snapshot()
        except Exception:
            return HostSample(source="cgroup")
        available, total = self._memory_of(snap)
        return HostSample(
            mem_total=total, mem_available=available, cpu_util=snap.cpu_utilisation,
            cpu_psi_some_avg10=snap.cpu_pressure, source="cgroup",
        )


def _num(value: object) -> float | None:
    """A finite number from a JSON value, else ``None``. JSON from the proxy
    may hold ``NaN`` or ``Infinity``, which ``int()`` would raise on."""
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


class SidecarReader:
    """The Docker-in-Docker sidecar, read through the proxy's
    ``/_pipeshub/usage``: its own cgroup (``source="sidecar"``), or the stats of
    the proxy's containers (``"sidecar-containers"``)."""

    def __init__(
        self, http_get: Callable[[str, float], tuple[int, Any]],
        source: Literal["sidecar", "sidecar-containers"], *, memory_bytes: int,
        cpu_millis: int | None,
    ) -> None:
        self.source: Source = source
        self._http_get = http_get
        self._memory_bytes = memory_bytes
        self._cpus = cpu_millis / 1000 if cpu_millis else None
        # The last block read, for the governor's snapshot.
        self.last_block: dict[str, Any] | None = None
        self._clock = time.monotonic
        self._memory_lock = threading.Lock()
        # (read at, (available, total)) of the last answered request.
        self._memory_cache: tuple[float, tuple[int | None, int | None]] | None = None
        self._memory_pending: threading.Event | None = None

    def _fetch(self) -> dict[str, Any] | None:
        containers = self.source == "sidecar-containers"
        path = "/_pipeshub/usage?scope=containers" if containers else "/_pipeshub/usage"
        try:
            status, body = self._http_get(
                path, _CONTAINERS_ROUTE_TIMEOUT_S if containers else _SIDECAR_ROUTE_TIMEOUT_S,
            )
        except Exception:
            return None
        if status != 200 or not isinstance(body, dict):
            return None
        block = body.get("containers" if containers else "sidecar")
        # Truncated: more containers run than the proxy reads, so the total
        # leaves some out, as a partial one does.
        if not isinstance(block, dict) or (containers and (block.get("partial") or block.get("truncated"))):
            return None
        return block

    def read(self) -> HostSample:
        block = self._fetch()
        self.last_block = block
        if block is None:
            return HostSample(source=self.source)
        used = _num(block.get("memWorkingSetBytes"))
        if self.source == "sidecar":
            limit = _num(block.get("memLimitBytes"))
            oom = _num(block.get("oomEventsDelta"))
            return HostSample(
                mem_total=None if limit is None else int(limit),
                mem_available=None if limit is None or used is None else max(0, int(limit - used)),
                mem_psi_full_avg10=_num(block.get("memPsiFullAvg10")),
                mem_oom_events=None if oom is None else int(oom),
                cpu_util=_num(block.get("cpuUtil")),
                cpu_throttled_ratio=_num(block.get("cpuThrottledRatio")),
                source="sidecar",
            )
        total = int(self._memory_bytes * _CONTAINERS_SHARE)
        cores = _num(block.get("cpuCores"))
        return HostSample(
            mem_total=total,
            mem_available=None if used is None else max(0, int(total - used)),
            cpu_util=None if cores is None or not self._cpus else cores / self._cpus,
            source="sidecar-containers",
        )

    def memory(self) -> tuple[int | None, int | None]:
        """``(available, total)`` for the per-run check. Never waits more
        than ``_SIDECAR_MEMORY_WAIT_S`` for the proxy."""
        now = self._clock()
        with self._memory_lock:
            cached = self._memory_cache
            if cached is not None and 0 <= now - cached[0] < _SIDECAR_MEMORY_FRESH_S:
                return cached[1]
            pending = self._memory_pending
            if pending is None:
                pending = self._memory_pending = threading.Event()
                threading.Thread(
                    target=self._refresh_memory, args=(pending,), name="sandbox-sidecar-reading", daemon=True,
                ).start()
        pending.wait(_SIDECAR_MEMORY_WAIT_S)
        with self._memory_lock:
            cached = self._memory_cache
        if cached is not None and 0 <= self._clock() - cached[0] < _SIDECAR_MEMORY_STALE_S:
            return cached[1]
        return None, None

    def _refresh_memory(self, done: threading.Event) -> None:
        try:
            sample = self.read()
            answer = (sample.mem_available, sample.mem_total)
        except Exception:
            answer = (None, None)
        with self._memory_lock:
            if answer[0] is not None:
                self._memory_cache = (self._clock(), answer)
            self._memory_pending = None
        done.set()


@dataclass(frozen=True)
class LiveMode:
    """What the live limit reads, or why it is off.

    ``mode`` is ``on:<source>`` or ``off:<reason>``. ``note`` says how the
    same-machine check passed. ``retry`` asks for a fresh resolve later (the
    answer may change, such as a proxy that did not answer yet).
    """

    mode: str
    reader: Reader | None = None
    profile: Literal["host", "cgroup", "sidecar"] | None = None
    note: str = ""
    reason: str = ""
    retry: bool = False

    @property
    def on(self) -> bool:
        return self.reader is not None

    @classmethod
    def off(cls, mode: str, reason: str, *, retry: bool = False) -> LiveMode:
        return cls(mode=f"off:{mode}", reason=reason, retry=retry)


def boot_id_hash(proc: Path = _PROC) -> str | None:
    """The proxy's ``/_pipeshub/host`` hash, computed here."""
    text = _read(proc / "sys" / "kernel" / "random" / "boot_id")
    boot_id = (text or "").strip()
    return hashlib.sha256(boot_id.encode()).hexdigest()[:32] if boot_id else None


def _own_cgroup_memory_limit() -> int | None:
    from app.services.resource_governor.probe import _read_int, _resolve_cgroup_path

    path = _resolve_cgroup_path("memory", "memory.limit_in_bytes", "memory.max")
    return _read_int(path) if path is not None else None


def docker_http_get(path: str, timeout: float) -> tuple[int, Any]:
    """GET *path* on the Docker endpoint the sandbox uses (the proxy in
    Compose and Helm). Blocking: call it off the event loop."""
    from app.agent_loop_lib.sandbox.coding.docker_client import get_default_provider

    api = get_default_provider().client.api
    response = api.get(api._url(path), timeout=timeout)
    try:
        body = response.json()
    except ValueError:
        body = None
    return response.status_code, body


def resolve_live_mode(
    capacity: SandboxCapacity,
    settings: LiveSettings,
    *,
    http_get: Callable[[str, float], tuple[int, Any]] = docker_http_get,
    host_reader: HostReader | None = None,
    cgroup_reader: Callable[[], Reader] = CgroupReader,
    own_cgroup_limit: Callable[[], int | None] = _own_cgroup_memory_limit,
    platform: str = sys.platform,
    proc: Path = _PROC,
) -> LiveMode:
    """Which reading the live limit uses for *capacity*'s mode, if any.
    Blocking: it may ask the Docker proxy. Never raises."""
    if not settings.enabled:
        return LiveMode.off("disabled", "disabled by SANDBOX_LIVE_LIMIT")
    if capacity.mode == "e2b":
        return LiveMode.off("remote", "remote backend")
    if capacity.mode == "local":
        return LiveMode(mode="on:cgroup", reader=cgroup_reader(), profile="cgroup", note="own cgroup")
    if capacity.mode == "helm_dind":
        return resolve_sidecar_mode(capacity, settings, http_get=http_get)
    if not platform.startswith("linux"):
        return LiveMode.off("not-linux", "app not on Linux: cannot read the Docker host")
    reader = host_reader or HostReader(proc)
    _, own_total = reader.memory()
    if own_total is None:
        return LiveMode.off("no-meminfo", "no /proc/meminfo")
    limit = own_cgroup_limit()
    if limit is not None and abs(own_total - limit) <= _LXCFS_TOLERANCE_BYTES:
        return LiveMode.off("lxcfs", "/proc/meminfo describes this container (lxcfs)")
    own_hash = boot_id_hash(proc)
    try:
        status, body = http_get("/_pipeshub/host", _HOST_ROUTE_TIMEOUT_S)
    except Exception as exc:
        status, body = 0, None
        logger.debug("sandbox live limit: /_pipeshub/host failed: %s", exc)
    remote_hash = body.get("bootIdHash") if status == 200 and isinstance(body, dict) else None
    if isinstance(remote_hash, str) and own_hash is not None:
        if remote_hash == own_hash:
            return LiveMode(mode="on:host", reader=reader, profile="host", note="boot id")
        return LiveMode.off("other-machine", "Docker daemon is on another machine")
    # An older proxy (403), no proxy (a unix socket straight to the daemon),
    # or no answer: fall back to comparing the daemon's /info with ours.
    if fingerprint_same_machine(capacity.docker_info, own_total):
        return LiveMode(mode="on:host", reader=reader, profile="host", note="fingerprint")
    if capacity.docker_info is None and not capacity.measured:
        # The daemon's /info has not been read yet (or Docker has not
        # answered): the sampler tries again soon.
        return LiveMode.off("unread", "the Docker daemon has not been read yet", retry=True)
    return LiveMode.off("unverified", "cannot prove the Docker daemon shares this machine", retry=True)


def resolve_sidecar_mode(
    capacity: SandboxCapacity,
    settings: LiveSettings,
    *,
    http_get: Callable[[str, float], tuple[int, Any]] = docker_http_get,
) -> LiveMode:
    """The reading for a Docker-in-Docker sidecar: its own cgroup when the
    proxy serves it and its limit matches the sidecar's, else container
    stats, else none. Blocking. Never raises."""
    if not settings.enabled:
        return LiveMode.off("disabled", "disabled by SANDBOX_LIVE_LIMIT")
    size = capacity.settings.host_memory_bytes
    if size is None:
        return LiveMode.off("no-sidecar-size", "SANDBOX_HOST_MEMORY_BYTES is not set")
    cpu_millis = capacity.settings.host_cpu_millis
    try:
        status, body = http_get("/_pipeshub/usage", _SIDECAR_ROUTE_TIMEOUT_S)
    except Exception as exc:
        status, body = 0, None
        logger.debug("sandbox live limit: /_pipeshub/usage failed: %s", exc)
    if status == 403:
        return LiveMode.off(
            "proxy-too-old", "the docker proxy does not serve /_pipeshub/usage; upgrade its image",
            retry=True,
        )
    sidecar = body.get("sidecar") if status == 200 and isinstance(body, dict) else None
    if isinstance(sidecar, dict):
        limit = _num(sidecar.get("memLimitBytes"))
        if limit is not None and abs(limit - size) <= _SIDECAR_LIMIT_TOLERANCE * size:
            if settings.sidecar_source != "containers":
                return LiveMode(
                    mode="on:sidecar", profile="sidecar", note="sidecar cgroup",
                    reader=SidecarReader(http_get, "sidecar", memory_bytes=size, cpu_millis=cpu_millis),
                )
            why = "SANDBOX_SIDECAR_SOURCE=containers"
        else:
            why = f"its memory.max {limit} is not the sidecar limit {size}"
    elif isinstance(body, dict) and body.get("sidecarError"):
        why = str(body["sidecarError"])
    else:
        why = f"HTTP {status}" if status else "the proxy did not answer"
    if settings.sidecar_source == "cgroup":
        return LiveMode.off("no-sidecar-reading", f"sidecar cgroup reading unavailable ({why})", retry=True)
    try:
        status, body = http_get("/_pipeshub/usage?scope=containers", _CONTAINERS_ROUTE_TIMEOUT_S)
    except Exception:
        status, body = 0, None
    if status == 200 and isinstance(body, dict) and isinstance(body.get("containers"), dict):
        return LiveMode(
            mode="on:sidecar-containers", profile="sidecar", note="container stats", retry=True,
            reason=f"sidecar cgroup reading unavailable ({why}); using container stats "
                   "(dockerd and page cache not counted)",
            reader=SidecarReader(http_get, "sidecar-containers", memory_bytes=size, cpu_millis=cpu_millis),
        )
    return LiveMode.off("no-sidecar-reading", f"no sidecar reading ({why})", retry=True)
