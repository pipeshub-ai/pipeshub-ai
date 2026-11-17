"""How many code sandboxes may run at once in this container, and why a run waits.

Every query worker of a container counts the same slot files (see
``SandboxResourceGovernor``), so the numbers here are container-wide. This
module holds the names the governor and its collaborators share:

- ``SandboxCapacity``: the ceiling and the floor, and the per-org cap derived
  from the current limit.
- ``LimitProvider``: the limit in force right now, at most the ceiling.
  ``FixedLimit`` keeps it at the ceiling.
- ``HoldReason`` and ``hold_reason()``: the one place a waiting run's reason
  is picked.
- ``AcquireObserver``: told when a run starts waiting, why, and how each
  acquire ended.
"""

from __future__ import annotations

import hashlib
import logging
import os
import threading
import weakref
from dataclasses import dataclass, replace
from enum import Enum
from typing import TYPE_CHECKING, Literal, Protocol

from app.services.resource_governor.shared_slots import sandbox_state_directory

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

__all__ = [
    "AcquireObserver",
    "CapacityPlan",
    "CapacitySettings",
    "FixedLimit",
    "HoldReason",
    "LimitProvider",
    "LiveView",
    "SandboxCapacity",
    "get_sandbox_capacity",
    "hold_reason",
    "org_tag",
    "reset_sandbox_capacity",
    "sandbox_state_directory",
    "slot_file_count",
]

logger = logging.getLogger(__name__)

ENV_MAX_TOTAL = "SANDBOX_MAX_TOTAL"
ENV_MAX_PER_ORG = "SANDBOX_MAX_PER_ORG"

DEFAULT_MAX_TOTAL = 50
DEFAULT_MAX_PER_ORG = 10
DEFAULT_MIN_TOTAL = 2
# Slot files every worker opens. The count must be the same in every worker
# (one that opened fewer would not see the others' high slots), so it comes
# from the environment only, never from what one worker measured.
MIN_SLOT_FILES = 256
MAX_SLOT_FILES = 1024

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


def slot_file_count(env: Mapping[str, str] | None = None) -> int:
    """How many slot files the workers share: ``SANDBOX_MAX_TOTAL`` within
    256..1024, and 1024 when it is set to zero or less."""
    env = os.environ if env is None else env
    explicit = _int_or_none(env, ENV_MAX_TOTAL)
    if explicit is not None and explicit <= 0:
        return MAX_SLOT_FILES
    return min(MAX_SLOT_FILES, max(MIN_SLOT_FILES, explicit or 0))


@dataclass(frozen=True)
class CapacitySettings:
    """The capacity variables, parsed.

    ``max_total``: an explicit ceiling, or ``None`` to size it. Zero or less
    used to mean unlimited, which a fixed set of slot files cannot offer, so
    it is the largest ceiling there is (with a warning).
    ``max_per_org``: an explicit per-org cap, ``0`` for none, or ``None`` for
    the default.
    """

    max_total: int | None = None
    max_per_org: int | None = None
    min_total: int = DEFAULT_MIN_TOTAL

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
        return cls(max_total=max_total, max_per_org=max_per_org)


@dataclass(frozen=True)
class CapacityPlan:
    ceiling: int
    floor: int
    slot_files: int
    explicit: bool
    binding: str


class SandboxCapacity:
    """The container's code sandbox ceiling and floor, and the per-org cap.

    One per process (``get_sandbox_capacity()``). The ceiling is
    ``SANDBOX_MAX_TOTAL`` when set, else the default, never above the slot
    file count.
    """

    def __init__(
        self,
        mode: Mode = "compose",
        settings: CapacitySettings | None = None,
        *,
        slot_files: int | None = None,
    ) -> None:
        self.mode: Mode = mode
        self._settings = settings or CapacitySettings()
        self._slot_files = slot_files if slot_files is not None else slot_file_count()
        self.sized = True
        self.plan = self._plan()
        self._listeners: list[weakref.WeakMethod[Callable[[], None]]] = []

    @classmethod
    def fixed(
        cls, total: int, per_org: int | None = None, *, mode: Mode = "compose",
        slot_files: int | None = None,
    ) -> SandboxCapacity:
        """A capacity with an explicit ceiling (and per-org cap; ``None`` for
        none), whatever the environment says."""
        files = slot_files if slot_files is not None else max(total, 1)
        return cls(
            mode, CapacitySettings(max_total=total, max_per_org=per_org or 0), slot_files=files,
        )

    def _plan(self) -> CapacityPlan:
        explicit = self._settings.max_total
        ceiling = explicit if explicit is not None else DEFAULT_MAX_TOTAL
        ceiling = max(1, min(ceiling, self._slot_files))
        return CapacityPlan(
            ceiling=ceiling,
            floor=min(self._settings.min_total, ceiling),
            slot_files=self._slot_files,
            explicit=explicit is not None,
            binding="explicit" if explicit is not None else "fixed",
        )

    @property
    def slot_files(self) -> int:
        return self._slot_files

    def ceiling(self) -> int:
        return self.plan.ceiling

    def floor(self) -> int:
        return self.plan.floor

    def per_org_limit(self, limit: int) -> int | None:
        """The most slots one org may hold at the current *limit*, or ``None``
        for no per-org cap."""
        explicit = self._settings.max_per_org
        if explicit == 0:
            return None
        return min(explicit if explicit is not None else DEFAULT_MAX_PER_ORG, limit)

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

    def start_sizing(self) -> None:
        """Nothing to measure: the ceiling is fixed."""

    async def ensure_sized(self, timeout: float | None = None) -> CapacityPlan:
        return self.plan

    def override(self, *, max_total: int | None, max_per_org: int | None) -> None:
        """Explicit caps from code (a caller's own config), with the same
        meaning as the environment variables. ``None`` leaves one as it is."""
        settings = self._settings
        if max_total is not None:
            settings = replace(settings, max_total=max_total if max_total > 0 else MAX_SLOT_FILES)
        if max_per_org is not None:
            settings = replace(settings, max_per_org=max(0, max_per_org))
        self._settings = settings
        self.plan = self._plan()
        self._changed()


def _resolve_mode() -> Mode:
    try:
        from app.agent_loop_lib.sandbox.coding.settings import resolve_sandbox_mode

        backend = resolve_sandbox_mode()
    except Exception:
        return "compose"
    if backend == "e2b":
        return "e2b"
    if backend == "local":
        return "local"
    return "compose"


_capacity: SandboxCapacity | None = None
_capacity_lock = threading.Lock()


def get_sandbox_capacity() -> SandboxCapacity:
    """This process's ``SandboxCapacity``, built from the environment on first use."""
    global _capacity
    if _capacity is None:
        with _capacity_lock:
            if _capacity is None:
                _capacity = SandboxCapacity(_resolve_mode(), CapacitySettings.from_env())
    return _capacity


def reset_sandbox_capacity() -> None:
    """Drop the process's ``SandboxCapacity``. For tests only."""
    global _capacity
    with _capacity_lock:
        _capacity = None


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
