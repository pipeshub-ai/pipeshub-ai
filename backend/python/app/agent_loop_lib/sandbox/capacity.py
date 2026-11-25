"""How many code sandboxes may run at once in this container, and why a run waits.

Every query worker of a container counts the same slot files (see
``SandboxResourceGovernor``), so the numbers here are container-wide. This
module holds the names the governor and its collaborators share:

- ``SandboxCapacity``: the ceiling, sized from the machine the sandboxes run
  on, the floor, and the per-org cap derived from the current limit.
- ``LimitProvider``: the limit in force right now, at most the ceiling.
  ``FixedLimit`` keeps it at the ceiling.
- ``HoldReason`` and ``hold_reason()``: the one place a waiting run's reason
  is picked.
- ``AcquireObserver``: told when a run starts waiting, why, and how each
  acquire ended.

The ceiling is the smallest of three terms, each a share of the machine
divided by what one run may use: memory (``SANDBOX_MEMORY_SHARE``, 25 %),
CPU (``SANDBOX_CPU_FACTOR``, 75 %) and processes (25 % of the kernel's
limit). It is clamped to ``SANDBOX_MIN_TOTAL`` (2) below and to the slot file
count above. ``SANDBOX_MAX_TOTAL`` replaces it. The machine is read once at
startup: in docker mode from the daemon's ``/info`` (through the Docker
proxy, which answers a filtered copy), or from ``SANDBOX_HOST_*`` when a
deployment passes the sandbox runner's limits in; in broker mode from the
sandbox broker, which sizes its own ceiling from its daemon with these same
functions and reports the machine and that ceiling (``broker_detail``); in
local mode from this container's cgroup. E2B runs elsewhere and has a fixed
ceiling of 100. Until the machine answers, the ceiling is the minimum.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import logging
import math
import os
import threading
import weakref
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import TYPE_CHECKING, Any, Literal, Protocol

from app.core.org_context import spawn_detached
from app.services.resource_governor.shared_slots import sandbox_state_directory

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping

__all__ = [
    "AcquireObserver",
    "CapacityPlan",
    "CapacitySettings",
    "DockerInfo",
    "DockerUnreachable",
    "FixedLimit",
    "HoldReason",
    "LimitProvider",
    "LiveView",
    "MachineSize",
    "RunSize",
    "SandboxCapacity",
    "broker_detail",
    "build_daemon_capacity",
    "build_sandbox_capacity",
    "compute_plan",
    "fingerprint_same_machine",
    "get_sandbox_capacity",
    "hold_reason",
    "machine_from_broker",
    "max_per_request",
    "org_tag",
    "read_broker_machine_size",
    "read_machine_size",
    "reset_sandbox_capacity",
    "run_size",
    "sandbox_state_directory",
    "slot_file_count",
]

logger = logging.getLogger(__name__)

ENV_MAX_TOTAL = "SANDBOX_MAX_TOTAL"
ENV_MAX_PER_ORG = "SANDBOX_MAX_PER_ORG"
ENV_MIN_TOTAL = "SANDBOX_MIN_TOTAL"
ENV_MEMORY_SHARE = "SANDBOX_MEMORY_SHARE"
ENV_CPU_FACTOR = "SANDBOX_CPU_FACTOR"
ENV_HOST_MEMORY_BYTES = "SANDBOX_HOST_MEMORY_BYTES"
ENV_HOST_CPU_MILLIS = "SANDBOX_HOST_CPU_MILLIS"
ENV_HOST_PID_LIMIT = "SANDBOX_HOST_PID_LIMIT"
ENV_CAPACITY_DIVISOR = "SANDBOX_CAPACITY_DIVISOR"
ENV_MAX_PER_REQUEST = "SANDBOX_MAX_PER_REQUEST"

# The fewest slots a per-org share leaves one org.
MIN_ORG_SHARE = 2
DEFAULT_MIN_TOTAL = 2
# The most slots the calls of one request (a chat turn and its sub-agents)
# hold at once.
DEFAULT_MAX_PER_REQUEST = 2
DEFAULT_MEMORY_SHARE = 0.25
DEFAULT_CPU_FACTOR = 0.75
# Share of the kernel's process limit the sandboxes may use together.
PID_SHARE = 0.25
# E2B runs on the provider's machines; its plans cap concurrent sandboxes
# themselves (100 on Pro). Smaller plans set SANDBOX_MAX_TOTAL.
E2B_DEFAULT_TOTAL = 100
MAX_CAPACITY_DIVISOR = 64
# Slot files every worker opens. The count must be the same in every worker
# (one that opened fewer would not see the others' high slots), so it comes
# from the environment only, never from what one worker measured.
MIN_SLOT_FILES = 256
MAX_SLOT_FILES = 1024
# Retries while Docker does not answer: after 5 s, 15 s, 30 s, then every 60 s.
_SIZING_RETRY_S = (5.0, 15.0, 30.0)
_SIZING_RETRY_EVERY_S = 60.0

Mode = Literal["compose", "helm_dind", "local", "e2b"]

_warned: set[tuple[str, str]] = set()


def _warn_once(key: str, raw: str, message: str, *args: object) -> None:
    if (key, raw) in _warned:
        return
    _warned.add((key, raw))
    logger.warning(message, *args)


def _int_or_none(env: Mapping[str, str], key: str) -> int | None:
    """An int from *env*; unset, empty and malformed values are ``None``."""
    raw = (env.get(key) or "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        _warn_once(key, raw, "Ignoring malformed %s=%r", key, raw)
        return None


def _positive_int(env: Mapping[str, str], key: str) -> int | None:
    value = _int_or_none(env, key)
    if value is not None and value <= 0:
        _warn_once(key, str(value), "Ignoring %s=%s: it must be above 0", key, value)
        return None
    return value


# A machine size above this is a typo: the terms are float arithmetic, and a
# larger int would overflow it.
_MAX_HOST_VALUE = 2**53


def _host_int(env: Mapping[str, str], key: str) -> int | None:
    value = _positive_int(env, key)
    if value is not None and value > _MAX_HOST_VALUE:
        _warn_once(key, str(value), "Ignoring %s: %d digits is not a machine size", key, len(str(value)))
        return None
    return value


def _float_in(env: Mapping[str, str], key: str, default: float, low: float, high: float) -> float:
    raw = (env.get(key) or "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        value = math.nan
    if not math.isfinite(value):
        _warn_once(key, raw, "Ignoring malformed %s=%r; using %g", key, raw, default)
        return default
    clamped = min(max(value, low), high)
    if clamped != value:
        _warn_once(key, raw, "%s=%s is outside %g..%g; using %g", key, raw, low, high, clamped)
    return clamped


def slot_file_count(env: Mapping[str, str] | None = None) -> int:
    """How many slot files the workers share: ``SANDBOX_MAX_TOTAL`` within
    256..1024, and 1024 when it is set to zero or less."""
    env = os.environ if env is None else env
    explicit = _int_or_none(env, ENV_MAX_TOTAL)
    if explicit is not None and explicit <= 0:
        return MAX_SLOT_FILES
    return min(MAX_SLOT_FILES, max(MIN_SLOT_FILES, explicit or 0))


def max_per_request(env: Mapping[str, str] | None = None) -> int:
    """The most slots the calls of one request may hold at once:
    ``SANDBOX_MAX_PER_REQUEST``, 2 when unset, 0 or invalid (warned), and
    never above the slot files. The ceiling still applies on top of it."""
    env = os.environ if env is None else env
    value = _int_or_none(env, ENV_MAX_PER_REQUEST)
    if value is None or value == 0:
        return DEFAULT_MAX_PER_REQUEST
    if value < 0:
        _warn_once(
            ENV_MAX_PER_REQUEST, str(value), "Ignoring %s=%s: it must be above 0; using %d",
            ENV_MAX_PER_REQUEST, value, DEFAULT_MAX_PER_REQUEST,
        )
        return DEFAULT_MAX_PER_REQUEST
    return min(value, MAX_SLOT_FILES)


@dataclass(frozen=True)
class CapacitySettings:
    """The capacity variables, parsed.

    ``max_total``: an explicit ceiling, or ``None`` to size it. Zero or less
    used to mean unlimited, which a fixed set of slot files cannot offer, so
    it is the largest ceiling there is (with a warning).
    ``max_per_org``: an explicit per-org cap, ``0`` for none, or ``None`` to
    leave it to the edition's per-org share.
    ``divisor``: how many app copies share one Docker daemon; each sizes
    itself to its share of it.
    """

    max_total: int | None = None
    max_per_org: int | None = None
    min_total: int = DEFAULT_MIN_TOTAL
    memory_share: float = DEFAULT_MEMORY_SHARE
    cpu_factor: float = DEFAULT_CPU_FACTOR
    host_memory_bytes: int | None = None
    host_cpu_millis: int | None = None
    host_pid_limit: int | None = None
    divisor: int = 1

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> CapacitySettings:
        env = os.environ if env is None else env
        max_total = _int_or_none(env, ENV_MAX_TOTAL)
        if max_total is not None and max_total <= 0:
            _warn_once(
                ENV_MAX_TOTAL, str(max_total),
                "%s=%s: unlimited is no longer supported; using %d",
                ENV_MAX_TOTAL, max_total, MAX_SLOT_FILES,
            )
            max_total = MAX_SLOT_FILES
        max_per_org = _int_or_none(env, ENV_MAX_PER_ORG)
        if max_per_org is not None and max_per_org <= 0:
            max_per_org = 0
        min_total = _positive_int(env, ENV_MIN_TOTAL) or DEFAULT_MIN_TOTAL
        host_memory = _host_int(env, ENV_HOST_MEMORY_BYTES)
        host_cpu = _host_int(env, ENV_HOST_CPU_MILLIS)
        if (host_memory is None) != (host_cpu is None):
            _warn_once(
                "host", f"{host_memory}/{host_cpu}",
                "Ignoring %s / %s: set both or neither", ENV_HOST_MEMORY_BYTES, ENV_HOST_CPU_MILLIS,
            )
            host_memory = host_cpu = None
        divisor = _int_or_none(env, ENV_CAPACITY_DIVISOR)
        if divisor is not None and not 1 <= divisor <= MAX_CAPACITY_DIVISOR:
            _warn_once(
                ENV_CAPACITY_DIVISOR, str(divisor), "Ignoring %s=%s: it must be 1..%d",
                ENV_CAPACITY_DIVISOR, divisor, MAX_CAPACITY_DIVISOR,
            )
            divisor = None
        return cls(
            max_total=max_total,
            max_per_org=max_per_org,
            min_total=min_total,
            memory_share=_float_in(env, ENV_MEMORY_SHARE, DEFAULT_MEMORY_SHARE, 0.05, 0.95),
            cpu_factor=_float_in(env, ENV_CPU_FACTOR, DEFAULT_CPU_FACTOR, 0.1, 2.0),
            host_memory_bytes=host_memory,
            host_cpu_millis=host_cpu,
            host_pid_limit=_host_int(env, ENV_HOST_PID_LIMIT),
            divisor=divisor or 1,
        )


@dataclass(frozen=True)
class RunSize:
    """What one run may use."""

    memory_bytes: int
    cpus: float
    pids: int | None


@dataclass(frozen=True)
class DockerInfo:
    """The fields of the daemon's ``/info`` the Docker proxy passes on."""

    mem_total_bytes: int | None = None
    ncpus: int | None = None
    kernel_version: str | None = None
    operating_system: str | None = None
    os_type: str | None = None
    architecture: str | None = None
    server_version: str | None = None
    cgroup_version: str | None = None
    cgroup_driver: str | None = None
    driver: str | None = None

    @classmethod
    def from_api(cls, info: Mapping[str, Any]) -> DockerInfo:
        def num(key: str) -> int | None:
            value = info.get(key)
            return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None

        def text(key: str) -> str | None:
            value = info.get(key)
            return value if isinstance(value, str) and value else None

        return cls(
            mem_total_bytes=num("MemTotal"), ncpus=num("NCPU"),
            kernel_version=text("KernelVersion"), operating_system=text("OperatingSystem"),
            os_type=text("OSType"), architecture=text("Architecture"),
            server_version=text("ServerVersion"), cgroup_version=text("CgroupVersion"),
            cgroup_driver=text("CgroupDriver"), driver=text("Driver"),
        )


MachineSource = Literal["docker_info", "proc_fallback", "helm_env", "app_cgroup", "broker", "fixed", "unknown"]


@dataclass(frozen=True)
class MachineSize:
    """The machine the sandboxes run on."""

    memory_bytes: int | None
    cpus: float | None
    pid_limit: int | None
    source: MachineSource
    # fingerprint_same_machine(); None when not checked.
    same_machine: bool | None = None
    # The ceiling the sandbox broker enforces on every sandbox of its daemon
    # (source "broker"): this process's share of it is one more term.
    ceiling: int | None = None


UNKNOWN_MACHINE = MachineSize(None, None, None, "unknown")


@dataclass(frozen=True)
class CapacityPlan:
    ceiling: int
    floor: int
    slot_files: int
    explicit: bool
    # "memory", "cpu", "pids" (and "broker" in broker mode): each term after
    # the divisor, None when unknown.
    terms: Mapping[str, int | None] = field(default_factory=dict)
    # memory | cpu | pids | broker | explicit | fixed | unknown
    binding: str = "unknown"
    machine: MachineSize = UNKNOWN_MACHINE
    run: RunSize | None = None
    divisor: int = 1


def _term(available: float | None, share: float, per_run: float | None, divisor: int) -> int | None:
    if available is None or not per_run:
        return None
    # The epsilon keeps 4 CPUs x 0.75 / 0.5 from landing on 5.999.
    return math.floor(available * share / per_run + 1e-9) // divisor


def compute_plan(
    mode: Mode, machine: MachineSize, run: RunSize | None, settings: CapacitySettings,
    slot_files: int,
) -> CapacityPlan:
    """The ceiling and floor for *machine*. Pure."""
    explicit = settings.max_total
    if mode == "e2b":
        ceiling = min(explicit or E2B_DEFAULT_TOTAL, slot_files)
        return CapacityPlan(
            ceiling=ceiling, floor=min(settings.min_total, ceiling), slot_files=slot_files,
            explicit=explicit is not None, terms={"memory": None, "cpu": None, "pids": None},
            binding="explicit" if explicit is not None else "fixed", machine=machine, run=run,
        )
    divisor = settings.divisor
    terms: dict[str, int | None] = {
        "memory": _term(machine.memory_bytes, settings.memory_share, run and run.memory_bytes, divisor),
        "cpu": _term(machine.cpus, settings.cpu_factor, run and run.cpus, divisor),
        "pids": _term(machine.pid_limit, PID_SHARE, run and run.pids, divisor),
    }
    if machine.ceiling is not None:
        terms["broker"] = machine.ceiling // divisor
    known = {name: value for name, value in terms.items() if value is not None}
    min_total = settings.min_total
    if explicit is not None:
        ceiling, binding = min(explicit, slot_files), "explicit"
    elif not known:
        ceiling, binding = min(min_total, slot_files), "unknown"
    else:
        binding = min(known, key=lambda name: known[name])
        # A minimum above what the machine holds would raise the ceiling past
        # it and pin the floor there, so the live limit could never shrink: it
        # is held to the machine's terms (never below the default minimum).
        min_total = min(min_total, max(DEFAULT_MIN_TOTAL, known[binding]))
        ceiling = max(min_total, min(known[binding], slot_files))
        ceiling = min(ceiling, slot_files)
    return CapacityPlan(
        ceiling=ceiling, floor=min(min_total, ceiling), slot_files=slot_files,
        explicit=explicit is not None, terms=terms, binding=binding, machine=machine, run=run,
        divisor=divisor,
    )


def fingerprint_same_machine(info: DockerInfo | None, own_mem_total: int | None) -> bool:
    """Whether the Docker daemon runs on this machine's kernel: the same
    memory total, within 0.5 %, and the same kernel release."""
    if info is None or info.mem_total_bytes is None or not own_mem_total:
        return False
    if abs(info.mem_total_bytes - own_mem_total) > own_mem_total * 0.005:
        return False
    return info.kernel_version == os.uname().release


def run_size(mode: Mode) -> RunSize | None:
    """One run's limits, from the backend's own config so they cannot drift."""
    if mode == "e2b":
        return None
    if mode == "local":
        from app.agent_loop_lib.sandbox.coding.factories.local import LocalFactoryConfig

        return RunSize(LocalFactoryConfig().max_memory_bytes, 1.0, None)
    from app.agent_loop_lib.sandbox.coding.egress_firewall import CONTAINER_HARDENING
    from app.agent_loop_lib.sandbox.coding.factories.docker import DockerFactoryConfig

    config = DockerFactoryConfig()
    return RunSize(config.memory_limit_mb * 2**20, config.cpu_limit, CONTAINER_HARDENING["pids_limit"])


def _read_int(path: str) -> int | None:
    try:
        with open(path) as f:
            return int(f.read().strip())
    except (OSError, ValueError):
        return None


def read_pid_limit() -> int | None:
    """The kernel's process limit as this container sees it."""
    values = [v for v in (_read_int("/proc/sys/kernel/pid_max"), _read_int("/proc/sys/kernel/threads-max")) if v]
    return min(values) if values else None


def read_own_mem_total() -> int | None:
    """``MemTotal`` from ``/proc/meminfo``: not namespaced, so the host's."""
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    return int(line.split()[1]) * 1024
    except (OSError, ValueError, IndexError):
        return None
    return None


class DockerUnreachable(Exception):
    """Docker did not answer; sizing is retried."""


async def _docker_info() -> dict[str, Any]:
    from app.agent_loop_lib.sandbox.coding.docker_client import get_default_provider

    provider = get_default_provider()
    return await provider.run_blocking(lambda: provider.client.info())


def _refused(exc: BaseException) -> bool:
    return getattr(exc, "status_code", None) == 403


async def read_machine_size(
    mode: Mode,
    settings: CapacitySettings,
    *,
    docker_info: Callable[[], Awaitable[Mapping[str, Any]]] | None = None,
) -> tuple[MachineSize, DockerInfo | None]:
    """The machine the sandboxes of *mode* run on, and the daemon's filtered
    ``/info`` when it was read. Raises ``DockerUnreachable`` when Docker does
    not answer."""
    if mode == "e2b":
        return MachineSize(None, None, None, "fixed"), None
    if mode == "local":
        from app.services.resource_governor.probe import SystemResourceProbe

        snap = await asyncio.to_thread(SystemResourceProbe().snapshot)
        return MachineSize(snap.mem_limit_bytes, snap.cpu_quota or None, None, "app_cgroup"), None
    if settings.host_memory_bytes is not None and settings.host_cpu_millis is not None:
        pid_limit = settings.host_pid_limit or await asyncio.to_thread(read_pid_limit)
        machine = MachineSize(
            settings.host_memory_bytes, settings.host_cpu_millis / 1000, pid_limit, "helm_env",
        )
        return machine, None
    try:
        raw = await (docker_info or _docker_info)()
    except Exception as exc:
        if not _refused(exc):
            raise DockerUnreachable(f"{type(exc).__name__}: {exc}") from exc
        _warn_once(
            "info", "403",
            "sandbox capacity: /info refused by the docker proxy (an older proxy image?); "
            "sizing from this container's view",
        )
        own = await asyncio.to_thread(read_own_mem_total)
        return MachineSize(own, float(os.cpu_count() or 0) or None, None, "proc_fallback"), None
    info = DockerInfo.from_api(raw if isinstance(raw, dict) else {})
    same = fingerprint_same_machine(info, await asyncio.to_thread(read_own_mem_total))
    pid_limit = settings.host_pid_limit
    if pid_limit is None and same:
        pid_limit = await asyncio.to_thread(read_pid_limit)
    machine = MachineSize(
        info.mem_total_bytes, float(info.ncpus) if info.ncpus else None, pid_limit, "docker_info", same,
    )
    return machine, info


# What the sandbox broker reports about its ceiling on its health answer
# (``broker_detail``), and the app reads back (``machine_from_broker``).
BROKER_CEILING = "sandboxCeiling"
BROKER_MEASURED = "sandboxMeasured"
BROKER_MEMORY_BYTES = "sandboxHostMemoryBytes"
BROKER_CPUS = "sandboxHostCpus"
BROKER_PID_LIMIT = "sandboxHostPidLimit"


def broker_detail(capacity: SandboxCapacity) -> dict[str, str]:
    """The broker's ceiling and the machine it was sized from, for its health
    answer. The machine is left out until it has been read."""
    plan = capacity.plan
    detail = {BROKER_CEILING: str(plan.ceiling), BROKER_MEASURED: str(capacity.measured).lower()}
    if not capacity.measured:
        return detail
    machine = plan.machine
    if machine.memory_bytes is not None:
        detail[BROKER_MEMORY_BYTES] = str(machine.memory_bytes)
    if machine.cpus is not None:
        detail[BROKER_CPUS] = f"{machine.cpus:g}"
    if machine.pid_limit is not None:
        detail[BROKER_PID_LIMIT] = str(machine.pid_limit)
    return detail


def _detail_float(detail: Mapping[str, str], key: str) -> float | None:
    """A positive machine size from the broker's answer; anything else is unknown."""
    try:
        value = float(detail.get(key) or "nan")
    except ValueError:
        return None
    return value if 0 < value <= _MAX_HOST_VALUE else None


def _detail_int(detail: Mapping[str, str], key: str) -> int | None:
    value = _detail_float(detail, key)
    return int(value) if value is not None and value == int(value) else None


def machine_from_broker(detail: Mapping[str, str]) -> MachineSize | None:
    """The machine the broker's sandboxes run on and the broker's ceiling, from
    its health answer; ``None`` when the broker does not report them (a release
    before it). Raises ``DockerUnreachable`` while the broker has not read its
    daemon yet."""
    ceiling = _detail_int(detail, BROKER_CEILING)
    if ceiling is None:
        return None
    if detail.get(BROKER_MEASURED) != "true":
        raise DockerUnreachable("the sandbox broker has not read its Docker daemon yet")
    return MachineSize(
        _detail_int(detail, BROKER_MEMORY_BYTES),
        _detail_float(detail, BROKER_CPUS),
        _detail_int(detail, BROKER_PID_LIMIT),
        "broker",
        ceiling=ceiling,
    )


async def _broker_health_detail() -> Mapping[str, str]:
    from app.harness.sandbox.client import process_broker_client

    provider = await process_broker_client.get()
    return (await provider.health()).detail


async def read_broker_machine_size(
    mode: Mode,
    settings: CapacitySettings,
    *,
    health_detail: Callable[[], Awaitable[Mapping[str, str]]] | None = None,
) -> tuple[MachineSize, DockerInfo | None]:
    """In broker mode the broker alone reaches the daemon that runs the
    sandboxes, so the machine comes from it. A broker that does not report it
    leaves the machine to ``read_machine_size``, as before it did. Raises
    ``DockerUnreachable`` while the broker does not answer."""
    try:
        detail = await (health_detail or _broker_health_detail)()
    except Exception as exc:
        raise DockerUnreachable(f"the sandbox broker did not answer ({type(exc).__name__})") from exc
    machine = machine_from_broker(detail)
    if machine is None:
        return await read_machine_size(mode, settings)
    return machine, None


_UNREAD = object()


def _gib(value: int | None) -> str:
    return "?" if value is None else f"{value / 2**30:.1f}GiB"


class SandboxCapacity:
    """The container's code sandbox ceiling and floor, and the per-org cap.

    One per process (``get_sandbox_capacity()``). It starts on the minimum
    (``sized`` False) unless the ceiling needs no measuring (explicit, or
    E2B), and ``start_sizing()`` reads the machine in the background, retrying
    while Docker does not answer. Each worker sizes itself; they agree
    because they read the same environment and the same daemon.
    """

    def __init__(
        self,
        mode: Mode = "compose",
        settings: CapacitySettings | None = None,
        *,
        slot_files: int | None = None,
        machine_reader: Callable[[], Awaitable[tuple[MachineSize, DockerInfo | None]]] | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.mode: Mode = mode
        self._settings = settings or CapacitySettings()
        self._slot_files = slot_files if slot_files is not None else slot_file_count()
        self._run = run_size(mode)
        self._reader = machine_reader or (lambda: read_machine_size(self.mode, self._settings))
        self._sleep = sleep
        self.docker_info: DockerInfo | None = None
        self._machine = MachineSize(None, None, None, "fixed") if mode == "e2b" else UNKNOWN_MACHINE
        self.plan = compute_plan(mode, self._machine, self._run, self._settings, self._slot_files)
        self.sized = mode == "e2b" or self._settings.max_total is not None
        self._listeners: list[weakref.WeakMethod[Callable[[], None]]] = []
        self._task: asyncio.Task[None] | None = None
        self._first_attempt: asyncio.Event | None = None
        self._measured = mode == "e2b"
        self._share: object = _UNREAD

    @classmethod
    def fixed(
        cls, total: int, org_cap: int | None = None, *, mode: Mode = "compose",
        slot_files: int | None = None,
    ) -> SandboxCapacity:
        """A capacity with an explicit ceiling (and per-org cap; ``None``
        leaves it to the per-org share) that never reads the machine. A
        total of zero or less is the largest ceiling, as in the environment."""
        if total <= 0:
            total = MAX_SLOT_FILES
        files = slot_files if slot_files is not None else total
        capacity = cls(
            mode, CapacitySettings(max_total=total, max_per_org=org_cap), slot_files=files,
        )
        capacity._measured = True
        return capacity

    @property
    def slot_files(self) -> int:
        return self._slot_files

    @property
    def settings(self) -> CapacitySettings:
        return self._settings

    def ceiling(self) -> int:
        return self.plan.ceiling

    def floor(self) -> int:
        return self.plan.floor

    def mem_term(self) -> int | None:
        """The memory term, or ``None`` when it is unknown or the ceiling is
        explicit or fixed."""
        if self.plan.binding in ("explicit", "fixed", "unknown"):
            return None
        return self.plan.terms.get("memory")

    def other_term(self) -> int:
        """The smaller of the CPU and process terms, or the ceiling when it
        is explicit, fixed or unknown; never above the slot files."""
        if self.plan.binding in ("explicit", "fixed", "unknown"):
            return self.plan.ceiling
        values = [v for k, v in self.plan.terms.items() if k != "memory" and v is not None]
        return min([*values, self._slot_files])

    def per_org_limit(self, limit: int) -> int | None:
        """The most slots one org may hold at the current *limit*, or ``None``
        for no per-org cap."""
        explicit = self._settings.max_per_org
        if explicit is not None:
            return min(explicit, limit) if explicit > 0 else None
        share = self._org_share()
        if share is None:
            return None
        # The current limit, not the ceiling: when the limit shrinks, one org
        # still cannot take every slot left.
        return min(limit, max(MIN_ORG_SHARE, math.floor(share * limit)))

    def _org_share(self) -> float | None:
        if self._share is _UNREAD:
            from app import edition

            self._share = edition.sandbox_org_share()
        return self._share  # type: ignore[return-value]

    def add_listener(self, callback: Callable[[], None]) -> None:
        """Call bound method *callback* whenever the ceiling may have risen.
        Held weakly, so a dropped governor is not kept alive."""
        self._listeners.append(weakref.WeakMethod(callback))  # type: ignore[arg-type]

    def _changed(self) -> None:
        for ref in list(self._listeners):
            callback = ref()
            if callback is None:
                self._listeners.remove(ref)
                continue
            try:
                callback()
            except Exception:
                logger.debug("sandbox capacity: listener failed", exc_info=True)

    def override(self, *, max_total: int | None, max_per_org: int | None) -> None:
        """Explicit caps from code (a caller's own config), with the same
        meaning as the environment variables. ``None`` leaves one as it is."""
        settings = self._settings
        if max_total is not None:
            settings = replace(settings, max_total=max_total if max_total > 0 else MAX_SLOT_FILES)
            self.sized = True
        if max_per_org is not None:
            settings = replace(settings, max_per_org=max(0, max_per_org))
        self._settings = settings
        self.plan = compute_plan(self.mode, self._machine, self._run, settings, self._slot_files)
        self._changed()

    def start_sizing(self) -> None:
        """Read the machine in a background task on the running loop. Safe to
        call more than once."""
        if self._measured:
            return
        loop = asyncio.get_running_loop()
        if self._task is not None and self._task.get_loop() is loop:
            return
        self._first_attempt = asyncio.Event()
        self._task = spawn_detached(self._size_until_known(), name="sandbox-capacity-sizing")

    async def ensure_sized(self, timeout: float | None = None) -> CapacityPlan:
        """Start sizing if needed and wait up to *timeout* for its first
        attempt, so a run arriving at startup does not take the minimum
        needlessly. Returns the plan in force. An explicit ceiling needs no
        measuring here; ``start_sizing()`` at startup still reads the machine
        for the log and for ``docker_info``."""
        if self.sized:
            return self.plan
        self.start_sizing()
        if self._first_attempt is not None:
            try:
                await asyncio.wait_for(self._first_attempt.wait(), timeout)
            except TimeoutError:
                pass
        return self.plan

    @property
    def measured(self) -> bool:
        """True once the machine has been read (or never needs reading)."""
        return self._measured

    async def wait_measured(self, timeout: float | None = None) -> None:
        """Start sizing if needed and wait up to *timeout* for its first
        attempt, even with an explicit ceiling: ``docker_info`` is only known
        after it."""
        if self._measured:
            return
        self.start_sizing()
        if self._first_attempt is not None:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._first_attempt.wait(), timeout)

    async def _size_until_known(self) -> None:
        delays = iter(_SIZING_RETRY_S)
        warned = False
        while True:
            try:
                machine, info = await self._reader()
            except DockerUnreachable as exc:
                if not warned:
                    warned = True
                    logger.warning(
                        "sandbox capacity: Docker unreachable (%s); using the minimum of %d "
                        "until it answers", exc, self.plan.ceiling,
                    )
                if self._first_attempt is not None:
                    self._first_attempt.set()
                await self._sleep(next(delays, _SIZING_RETRY_EVERY_S))
                continue
            except Exception:
                logger.warning("sandbox capacity: reading the machine size failed", exc_info=True)
                machine, info = UNKNOWN_MACHINE, None
            self._apply(machine, info)
            if self._first_attempt is not None:
                self._first_attempt.set()
            return

    def _apply(self, machine: MachineSize, info: DockerInfo | None) -> None:
        self._machine = machine
        self.docker_info = info
        self._measured = True
        plan = compute_plan(self.mode, machine, self._run, self._settings, self._slot_files)
        self.plan = plan
        self.sized = True
        self._log(plan)
        self._changed()

    def _log(self, plan: CapacityPlan) -> None:
        machine = plan.machine
        terms = " ".join(f"{k}={v if v is not None else '-'}" for k, v in plan.terms.items())
        split = f" (each / {plan.divisor} pods sharing the daemon)" if plan.divisor > 1 else ""
        org_cap = self.per_org_limit(plan.ceiling)
        logger.info(
            "sandbox capacity: mode=%s source=%s mem=%s cpus=%s pid_limit=%s same_machine=%s "
            "-> terms %s%s -> ceiling=%d (%s) floor=%d slot_files=%d org_cap=%s",
            self.mode, machine.source, _gib(machine.memory_bytes),
            f"{machine.cpus:g}" if machine.cpus is not None else "?", machine.pid_limit,
            machine.same_machine, terms, split, plan.ceiling, plan.binding, plan.floor,
            plan.slot_files, org_cap if org_cap is not None else "all",
        )
        known = {k: v for k, v in plan.terms.items() if v is not None}
        if plan.explicit and known:
            name = min(known, key=lambda k: known[k])
            if plan.ceiling > known[name]:
                logger.warning(
                    "%s=%d exceeds what this machine supports by %s (%d)",
                    ENV_MAX_TOTAL, plan.ceiling, name, known[name],
                )
        elif plan.binding == "unknown" and self.mode != "e2b":
            logger.warning(
                "sandbox capacity: the machine size is unknown; using the minimum of %d",
                plan.ceiling,
            )
        elif not plan.explicit and known and min(known.values()) < self._settings.min_total:
            minimum = self._settings.min_total
            if plan.floor < minimum:
                # One warning per value, not one per resize.
                _warn_once(
                    ENV_MIN_TOTAL, str(minimum),
                    "%s=%d is above the %d code sandboxes this machine supports by %s; "
                    "using %d: a larger minimum would run more than the machine holds",
                    ENV_MIN_TOTAL, minimum, min(known.values()), plan.binding, plan.floor,
                )
            else:
                logger.warning(
                    "sandbox capacity: this machine supports %d code sandboxes by %s, fewer than "
                    "the minimum of %d; using %d", min(known.values()), plan.binding,
                    minimum, plan.ceiling,
                )


ENV_DOCKER_TOPOLOGY = "SANDBOX_DOCKER_TOPOLOGY"


def _docker_mode(env: Mapping[str, str]) -> Mode:
    """``helm_dind`` when the deployment says the daemon is a Docker-in-Docker
    sidecar of this pod, which then holds only its sandboxes."""
    raw = (env.get(ENV_DOCKER_TOPOLOGY) or "").strip().lower()
    if raw == "dind-sidecar":
        return "helm_dind"
    if raw:
        _warn_once(ENV_DOCKER_TOPOLOGY, raw, "Ignoring %s=%r: use dind-sidecar or leave it unset", ENV_DOCKER_TOPOLOGY, raw)
    return "compose"


def _resolve_backend() -> str | None:
    try:
        from app.agent_loop_lib.sandbox.coding.settings import resolve_sandbox_mode

        return resolve_sandbox_mode()
    except Exception:
        return None


def _resolve_mode(backend: str | None = None) -> Mode:
    if backend == "e2b":
        return "e2b"
    if backend == "local":
        return "local"
    return _docker_mode(os.environ)


def build_sandbox_capacity(backend: str | None, env: Mapping[str, str] | None = None) -> SandboxCapacity:
    """The capacity for *backend* (a ``SANDBOX_MODE`` backend name) from *env*."""
    env = os.environ if env is None else env
    mode = _resolve_mode(backend)
    settings = CapacitySettings.from_env(env)
    reader: Callable[[], Awaitable[tuple[MachineSize, DockerInfo | None]]] | None = None
    if backend == "broker":
        reader = lambda: read_broker_machine_size(mode, settings)
    return SandboxCapacity(mode, settings, slot_files=slot_file_count(env), machine_reader=reader)


def build_daemon_capacity(
    docker_info: Callable[[], Awaitable[Mapping[str, Any]]], env: Mapping[str, str] | None = None,
) -> SandboxCapacity:
    """The capacity of the daemon *docker_info* reads, from *env*: the sandbox
    broker's, which starts every sandbox there. The same variables, defaults and
    sizing as the app's, so one ``SANDBOX_MAX_TOTAL`` means one thing."""
    env = os.environ if env is None else env
    mode = _docker_mode(env)
    settings = CapacitySettings.from_env(env)
    return SandboxCapacity(
        mode, settings, slot_files=slot_file_count(env),
        machine_reader=lambda: read_machine_size(mode, settings, docker_info=docker_info),
    )


_capacity: SandboxCapacity | None = None
_capacity_lock = threading.Lock()


def get_sandbox_capacity() -> SandboxCapacity:
    """This process's ``SandboxCapacity``, built from the environment on first use."""
    global _capacity
    if _capacity is None:
        with _capacity_lock:
            if _capacity is None:
                _capacity = build_sandbox_capacity(_resolve_backend())
    return _capacity


def reset_sandbox_capacity() -> None:
    """Drop the process's ``SandboxCapacity``. For tests only."""
    global _capacity
    with _capacity_lock:
        capacity, _capacity = _capacity, None
    if capacity is not None and capacity._task is not None and not capacity._task.done():
        loop = capacity._task.get_loop()
        if not loop.is_closed():
            loop.call_soon_threadsafe(capacity._task.cancel)


def org_tag(org_id: str | None) -> bytes:
    """What a slot file records about its holder's org: a short hash, so the
    files never hold an org id. Empty for no org."""
    if not org_id:
        return b""
    return hashlib.sha256(org_id.encode()).hexdigest()[:16].encode()


class HoldReason(str, Enum):
    """Why a run is waiting for a code sandbox."""

    SLOTS_FULL = "slots_full"
    ORG_SHARE = "org_share"
    LIVE_MEMORY = "live_memory"
    LIVE_CPU = "live_cpu"
    HOST_MEMORY_FLOOR = "host_memory_floor"


@dataclass(frozen=True)
class LiveView:
    """The limit in force now and how it was reached."""

    limit: int
    ceiling: int
    floor: int
    rule: str
    # Set only while the limit is below the ceiling.
    shrink_cause: Literal["memory", "cpu"] | None
    target: int | None
    mode: str
    source: Literal["host", "cgroup", "sidecar", "sidecar-containers"] | None
    sampled_age_s: float | None
    mem_total_bytes: int | None
    mem_available_bytes: int | None


class LimitProvider(Protocol):
    def view(self) -> LiveView:
        """The current limit. Cheap and never blocks or raises."""
        ...

    async def admit_and_charge(self, *, held: int) -> HoldReason | None:
        """Called once a slot is taken (*held* were held before it): a reason
        to give the slot back, or ``None`` to keep it."""
        ...

    def waiting(self, reason: HoldReason, waited_s: float) -> None:
        """A run is waiting for *reason*. Never blocks."""
        ...

    def wait_ended(self, *, granted: bool) -> None:
        """A run that waited stopped waiting. Never blocks."""
        ...


class FixedLimit:
    """The limit is always the ceiling."""

    def __init__(self, capacity: SandboxCapacity) -> None:
        self._capacity = capacity

    def view(self) -> LiveView:
        ceiling = self._capacity.ceiling()
        return LiveView(
            limit=ceiling, ceiling=ceiling, floor=self._capacity.floor(), rule="off",
            shrink_cause=None, target=None, mode="off:fixed", source=None,
            sampled_age_s=None, mem_total_bytes=None, mem_available_bytes=None,
        )

    async def admit_and_charge(self, *, held: int) -> HoldReason | None:
        return None

    def waiting(self, reason: HoldReason, waited_s: float) -> None:
        return None

    def wait_ended(self, *, granted: bool) -> None:
        return None


class AcquireObserver(Protocol):
    """Told about every acquire: ``wait_start`` at its first hold,
    ``wait_reason`` when the reason changes, and ``outcome`` exactly once."""

    def wait_start(self, reason: HoldReason) -> None: ...

    def wait_reason(self, reason: HoldReason) -> None: ...

    def outcome(
        self, outcome: Literal["granted", "refused", "cancelled"],
        waited_s: float, reason: HoldReason | None,
    ) -> None: ...


def hold_reason(
    view: LiveView, *, denied: Literal["full", "tag_full"] | None, floor: HoldReason | None,
) -> HoldReason:
    """Why a run that did not get a slot is waiting. The governor's only
    source of a reason."""
    if floor is not None:
        return floor
    if denied == "tag_full":
        return HoldReason.ORG_SHARE
    if view.limit >= view.ceiling:
        return HoldReason.SLOTS_FULL
    return HoldReason.LIVE_CPU if view.shrink_cause == "cpu" else HoldReason.LIVE_MEMORY
