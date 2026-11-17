"""The reserve/cap/aging policy over the class numbers (pure), and its live config.

``next_grant`` answers one question: with this much capacity and these waiters,
which class gets the next free parse token, if any? It never looks at a clock;
the caller passes the waits it measured. The numbers come from the
``/services/indexing/parseAdmission`` key (changed without a restart), with env
defaults, and the last good value is kept when the store cannot be read.
"""
from __future__ import annotations

import logging
import math
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, fields, replace
from typing import Any, Protocol

from app.modules.parse_engine.cost import CostClass
from app.utils.env_config import env_float

__all__ = [
    "ADMISSION_CONFIG_KEY",
    "AdmissionNumbers",
    "AdmissionNumbersError",
    "ClassDemand",
    "Grant",
    "LiveAdmissionConfig",
    "next_grant",
    "numbers_from_env",
    "numbers_from_mapping",
    "slots",
]

_logger = logging.getLogger(__name__)

ADMISSION_CONFIG_KEY = "/services/indexing/parseAdmission"
CONFIG_CACHE_SECONDS = 30.0

_BIG = (CostClass.LARGE, CostClass.HUGE)


class AdmissionNumbersError(ValueError):
    """A config save that must be rejected; the old value stays."""


@dataclass(frozen=True)
class AdmissionNumbers:
    small_reserve: float = 0.40  # enforced by the member budget's held floors, carried for validation
    large_min: float = 0.20
    large_cap: float = 0.50  # big share while small demand on this pool is recent
    borrow_cap: float = 0.85  # big share once small demand has been absent for small_recent_s
    huge_cap: float = 0.50  # HUGE share of capacity, applied only while LARGE waits
    aging_s: float = 900.0
    aged_min: float = 0.34
    small_recent_s: float = 600.0

    def validate(self) -> AdmissionNumbers:
        for f in fields(self):
            v = getattr(self, f.name)
            if not isinstance(v, (int, float)) or isinstance(v, bool) or not math.isfinite(v):
                raise AdmissionNumbersError(f"{f.name} must be a finite number")
        if not 0.0 <= self.small_reserve < 1.0:
            raise AdmissionNumbersError("small_reserve must be in [0, 1)")
        if not 0.0 < self.large_min:
            raise AdmissionNumbersError("large_min must be positive")
        if not self.large_min <= self.aged_min <= self.large_cap <= self.borrow_cap <= 1.0:
            raise AdmissionNumbersError("need large_min <= aged_min <= large_cap <= borrow_cap <= 1")
        if not 0.0 < self.huge_cap <= 1.0:
            raise AdmissionNumbersError("huge_cap must be in (0, 1]")
        if self.small_reserve + self.large_min > 1.0:
            raise AdmissionNumbersError("small_reserve + large_min must not exceed 1")
        if self.aging_s <= 0.0:
            raise AdmissionNumbersError("aging_s must be positive")
        if self.small_recent_s < 0.0:
            raise AdmissionNumbersError("small_recent_s must not be negative")
        return self


# ConfigurationService (camelCase) and env (PARSE_ADMISSION_<NAME>) spellings of each number.
_KEYS: Mapping[str, str] = {
    "smallReserve": "small_reserve",
    "largeMin": "large_min",
    "largeCap": "large_cap",
    "borrowCap": "borrow_cap",
    "hugeCap": "huge_cap",
    "agingS": "aging_s",
    "agedMin": "aged_min",
    "smallRecentS": "small_recent_s",
}


def numbers_from_mapping(raw: object, base: AdmissionNumbers | None = None) -> AdmissionNumbers:
    """A stored value laid over ``base``. Raises ``AdmissionNumbersError`` on anything unusable."""
    if not isinstance(raw, Mapping):
        raise AdmissionNumbersError("the value must be an object")
    unknown = set(raw) - set(_KEYS)
    if unknown:
        raise AdmissionNumbersError(f"unknown keys: {sorted(unknown)}")
    return replace(base or AdmissionNumbers(), **{_KEYS[k]: raw[k] for k in raw}).validate()


def numbers_from_env() -> AdmissionNumbers:
    """The defaults with ``PARSE_ADMISSION_<NAME>`` overrides. An override set that does not validate is
    ignored as a whole, so a typo never stops the service."""
    d = AdmissionNumbers()
    values: dict[str, float] = {}
    for f in fields(d):
        default = getattr(d, f.name)
        high = 1e9 if f.name.endswith("_s") else 1.0
        values[f.name] = env_float(f"PARSE_ADMISSION_{f.name.upper()}", default, low=0.0, high=high)
    try:
        return AdmissionNumbers(**values).validate()
    except AdmissionNumbersError as exc:
        _logger.warning("Ignoring PARSE_ADMISSION_* overrides: %s", exc)
        return d


@dataclass(frozen=True)
class ClassDemand:
    running: int = 0
    waiting: int = 0
    oldest_wait_s: float = 0.0
    head_bounded: bool = True  # est <= the unit budget (or one unit of a split document)


@dataclass(frozen=True)
class Grant:
    cls: CostClass
    rule: str  # A1..A4, for the decision log and the tests


def slots(fraction: float, capacity: int) -> int:
    return max(1, math.floor(fraction * capacity))


def next_grant(
    numbers: AdmissionNumbers,
    capacity: int,
    demand: Mapping[CostClass, ClassDemand],
    *,
    since_small_s: float = math.inf,
    held_floors: int = 0,
    small_waiting_elsewhere: bool = False,
) -> Grant | None:
    """Which class's head is granted the next token, or None.

    ``capacity`` is the parse-family tokens ``P``; ``held_floors`` is ``H``, the sum of the floors the member
    budget holds for the small-file path. Nothing is granted past ``P``.

    A1 gives big its guarantee, A2 lifts it once its head has aged, A3 serves small, and A4 lets big use
    tokens above the held floors in bounded units only. Small waiting anywhere in the container blocks A4.
    """
    if capacity < 1:
        return None
    zero = ClassDemand()
    small, large, huge = (demand.get(c, zero) for c in (CostClass.SMALL, CostClass.LARGE, CostClass.HUGE))
    used = small.running + large.running + huge.running
    if used >= capacity:
        return None
    big_used = large.running + huge.running
    big_waits = large.waiting > 0 or huge.waiting > 0
    small_waits = small.waiting > 0
    above_floors = max(0, capacity - held_floors)

    big = {CostClass.LARGE: large, CostClass.HUGE: huge}

    def pick(*, bounded_only: bool, aged_first: bool = False) -> CostClass | None:
        cands = [c for c, d in big.items() if d.waiting > 0 and (d.head_bounded or not bounded_only)]
        if CostClass.HUGE in cands and large.waiting > 0:
            # HUGE only while under its own cap when LARGE waits, unless its head has aged past LARGE's.
            if huge.running >= slots(numbers.huge_cap, capacity):
                cands.remove(CostClass.HUGE)
            elif aged_first and CostClass.LARGE in cands and huge.oldest_wait_s >= numbers.aging_s > large.oldest_wait_s:
                return CostClass.HUGE
        return cands[0] if cands else None

    # A1: big's guarantee. At capacity 1 small work waiting goes first.
    if big_waits and big_used < slots(numbers.large_min, capacity) and (capacity >= 2 or not small_waits):
        cls = pick(bounded_only=False, aged_first=True)
        if cls is not None:
            return Grant(cls, "A1")
    # A2: an aged big head lifts big's guarantee, within the tokens above the held floors, bounded units only.
    if big_waits:
        aged = max((d.oldest_wait_s for d in (large, huge) if d.waiting > 0), default=0.0)
        if aged >= numbers.aging_s and big_used < min(slots(numbers.aged_min, capacity), above_floors):
            cls = pick(bounded_only=True, aged_first=True)
            if cls is not None:
                return Grant(cls, "A2")
    # A3: small.
    if small_waits:
        return Grant(CostClass.SMALL, "A3")
    # A4: big above the held floors, bounded only, and only while nobody small waits anywhere.
    if big_waits and not small_waiting_elsewhere and big_used + 1 <= above_floors:
        recent = small.running > 0 or since_small_s < numbers.small_recent_s
        cap = slots(numbers.large_cap if recent else numbers.borrow_cap, capacity)
        if big_used + 1 <= cap:
            cls = pick(bounded_only=True)
            if cls is not None:
                return Grant(cls, "A4")
    return None


class _ConfigSource(Protocol):
    async def get_config(self, key: str, default: Any = None, *args: Any, **kw: Any) -> Any: ...  # noqa: ANN401


class LiveAdmissionConfig:
    """The numbers in force: the stored value over the env defaults, re-read at most every 30 s.

    ``peek()`` is what a hot path reads; ``refresh()`` is awaited by whoever owns the loop. A store that cannot
    be read, or a value that does not validate, leaves the last good numbers in force.
    """

    def __init__(
        self,
        config_service: _ConfigSource | None,
        *,
        env_defaults: AdmissionNumbers | None = None,
        ttl_s: float = CONFIG_CACHE_SECONDS,
        retry_s: float = 5.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._svc = config_service
        self._env = env_defaults if env_defaults is not None else numbers_from_env()
        self._ttl = ttl_s
        self._retry = retry_s
        self._clock = clock
        self._current = self._env
        self._next_read = float("-inf")
        self.last_error: str | None = None

    def peek(self) -> AdmissionNumbers:
        return self._current

    async def refresh(self) -> AdmissionNumbers:
        now = self._clock()
        if self._svc is None or now < self._next_read:
            return self._current
        try:
            raw = await self._svc.get_config(
                ADMISSION_CONFIG_KEY, None, use_cache=True, raise_on_error=True, max_age_seconds=self._ttl
            )
        except Exception as exc:  # the store being down is the case this exists for
            self.last_error = f"store unreadable: {type(exc).__name__}"
            self._next_read = now + min(self._retry, self._ttl)
            _logger.warning("parseAdmission config unreadable (%s); keeping the last good value", type(exc).__name__)
            return self._current
        self._next_read = now + self._ttl
        if raw is None:
            self._current, self.last_error = self._env, None
            return self._current
        try:
            self._current = numbers_from_mapping(raw, self._env)
            self.last_error = None
        except AdmissionNumbersError as exc:
            self.last_error = f"rejected: {exc}"
            _logger.warning("parseAdmission config rejected (%s); keeping the previous value", exc)
        return self._current
