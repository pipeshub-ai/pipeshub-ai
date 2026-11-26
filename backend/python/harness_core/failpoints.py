"""Named crash points.

Call sites (``failpoint("worker.tool.after_intent")``) are no-ops unless a test
arms them, either in-process through :data:`registry` or cross-process through
a JSON file installed with :meth:`FailpointRegistry.install_file`. There is no
environment variable: a production process never installs a file source.

File format::

    {"worker.tool.after_intent": {"action": "kill9", "trigger": {"nth": 2}}}

Actions: ``raise`` (optional ``message``), ``delay`` (``ms``), ``kill9``.
Triggers: ``once`` (default), ``{"nth": k}``, ``always``.
"""

from __future__ import annotations

import asyncio
import json
import os
import signal
import threading
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal, cast

if TYPE_CHECKING:
    from pathlib import Path


class FailpointError(RuntimeError):
    """Raised by an armed ``Raise`` action unless the test chose another type."""


@dataclass(frozen=True)
class Raise:
    exc_type: type[BaseException] = FailpointError
    message: str = ""


@dataclass(frozen=True)
class Delay:
    ms: int


@dataclass(frozen=True)
class Kill9:
    pass


Action = Raise | Delay | Kill9


@dataclass(frozen=True)
class Once:
    pass


@dataclass(frozen=True)
class Nth:
    k: int

    def __post_init__(self) -> None:
        if self.k < 1:
            raise ValueError("Nth trigger is 1-based")


@dataclass(frozen=True)
class Always:
    pass


Trigger = Once | Nth | Always


@dataclass(frozen=True)
class Arm:
    action: Action
    trigger: Trigger = field(default_factory=Once)


def _fires(trigger: Trigger, hit: int) -> bool:
    if isinstance(trigger, Always):
        return True
    if isinstance(trigger, Nth):
        return hit == trigger.k
    return hit == 1


@dataclass
class _ArmedPoint:
    arm: Arm
    hits: int = 0


class _FileSource:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._mtime_ns: int | None = None

    def changed(self) -> bool:
        try:
            mtime_ns = self.path.stat().st_mtime_ns
        except FileNotFoundError:
            mtime_ns = None
        if mtime_ns == self._mtime_ns:
            return False
        self._mtime_ns = mtime_ns
        return True

    def read(self) -> dict[str, Arm]:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        return parse_arms(raw)


class FailpointRegistry:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._points: dict[str, _ArmedPoint] = {}
        self._file: _FileSource | None = None

    @property
    def active(self) -> bool:
        return bool(self._points) or self._file is not None

    def arm(self, name: str, arm: Arm | Action) -> None:
        resolved = arm if isinstance(arm, Arm) else Arm(arm)
        with self._lock:
            self._points[name] = _ArmedPoint(resolved)

    def disarm(self, name: str) -> None:
        with self._lock:
            _ = self._points.pop(name, None)

    def reset(self) -> None:
        with self._lock:
            self._points.clear()
            self._file = None

    def hits(self, name: str) -> int:
        with self._lock:
            point = self._points.get(name)
            return point.hits if point else 0

    def install_file(self, path: Path) -> None:
        """Arm from ``path`` and re-read it whenever its mtime changes."""
        with self._lock:
            self._file = _FileSource(path)
            self._reload_file_locked()

    def _reload_file_locked(self) -> None:
        source = self._file
        if source is None or not source.changed():
            return
        self._points = {name: _ArmedPoint(arm) for name, arm in source.read().items()}

    def take(self, name: str) -> Action | None:
        """Count a hit at ``name`` and return the action if it fires now."""
        with self._lock:
            self._reload_file_locked()
            point = self._points.get(name)
            if point is None:
                return None
            point.hits += 1
            return point.arm.action if _fires(point.arm.trigger, point.hits) else None


registry = FailpointRegistry()


def _kill9() -> None:
    os.kill(os.getpid(), signal.SIGKILL)


def _raise(action: Raise, name: str) -> None:
    raise action.exc_type(action.message or f"failpoint {name}")


def failpoint(name: str) -> None:
    if not registry.active:
        return
    action = registry.take(name)
    if action is None:
        return
    if isinstance(action, Kill9):
        _kill9()
    elif isinstance(action, Delay):
        time.sleep(action.ms / 1000)
    else:
        _raise(action, name)


async def afailpoint(name: str) -> None:
    """Async variant: ``delay`` yields to the event loop instead of blocking it."""
    if not registry.active:
        return
    action = registry.take(name)
    if action is None:
        return
    if isinstance(action, Kill9):
        _kill9()
    elif isinstance(action, Delay):
        await asyncio.sleep(action.ms / 1000)
    else:
        _raise(action, name)


def _parse_trigger(raw: object) -> Trigger:
    if raw is None or raw == "once":
        return Once()
    if raw == "always":
        return Always()
    if isinstance(raw, dict) and "nth" in raw:
        return Nth(int(cast("dict[str, int]", raw)["nth"]))
    raise ValueError(f"unknown failpoint trigger: {raw!r}")


def _parse_action(spec: dict[str, object]) -> Action:
    kind = cast("Literal['raise', 'delay', 'kill9'] | None", spec.get("action"))
    if kind == "kill9":
        return Kill9()
    if kind == "delay":
        return Delay(int(cast("int", spec["ms"])))
    if kind == "raise":
        return Raise(message=str(spec.get("message", "")))
    raise ValueError(f"unknown failpoint action: {kind!r}")


def parse_arms(raw: object) -> dict[str, Arm]:
    if not isinstance(raw, dict):
        raise ValueError("failpoint file must hold a JSON object")
    arms: dict[str, Arm] = {}
    for name, spec in cast("dict[str, dict[str, object]]", raw).items():
        arms[name] = Arm(_parse_action(spec), _parse_trigger(spec.get("trigger")))
    return arms


__all__ = [
    "Action",
    "Always",
    "Arm",
    "Delay",
    "FailpointError",
    "FailpointRegistry",
    "Kill9",
    "Nth",
    "Once",
    "Raise",
    "Trigger",
    "afailpoint",
    "failpoint",
    "parse_arms",
    "registry",
]
