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
- E2B runs elsewhere: no reading.

Every reader returns a ``HostSample`` and never raises; a field it cannot
read is ``None``.
"""

from __future__ import annotations

import hashlib
import logging
import sys
import threading
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
    "boot_id_hash",
    "docker_http_get",
    "resolve_live_mode",
]

logger = logging.getLogger(__name__)

_PROC = Path("/proc")
# lxcfs bind-mounted over /proc/meminfo reports the container's own limit.
_LXCFS_TOLERANCE_BYTES = 2**20
_HOST_ROUTE_TIMEOUT_S = 2.0

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
            source="host",
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
    return LiveMode.off(
        "unverified", "cannot prove the Docker daemon shares this machine", retry=status == 0,
    )
