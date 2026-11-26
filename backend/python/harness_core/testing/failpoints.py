"""Test-side helpers for arming :mod:`harness_core.failpoints`."""

from __future__ import annotations

import json
from contextlib import contextmanager
from typing import TYPE_CHECKING

from harness_core.failpoints import (
    Action,
    Always,
    Arm,
    Delay,
    FailpointError,
    FailpointRegistry,
    Kill9,
    Nth,
    Trigger,
    registry,
)

if TYPE_CHECKING:
    from collections.abc import Generator, Mapping
    from pathlib import Path


@contextmanager
def armed(
    arms: Mapping[str, Arm | Action],
    *,
    target: FailpointRegistry = registry,
) -> Generator[FailpointRegistry]:
    """Arm ``arms`` for the duration of the block, then disarm exactly those."""
    for name, arm in arms.items():
        target.arm(name, arm)
    try:
        yield target
    finally:
        for name in arms:
            target.disarm(name)


def _trigger_json(trigger: Trigger) -> object:
    if isinstance(trigger, Always):
        return "always"
    if isinstance(trigger, Nth):
        return {"nth": trigger.k}
    return "once"


def _action_json(action: Action) -> dict[str, object]:
    if isinstance(action, Kill9):
        return {"action": "kill9"}
    if isinstance(action, Delay):
        return {"action": "delay", "ms": action.ms}
    if action.exc_type is not FailpointError:
        raise ValueError("a failpoint file can only raise FailpointError")
    return {"action": "raise", "message": action.message}


def write_arms(path: Path, arms: Mapping[str, Arm | Action]) -> None:
    """Write a failpoint file that another process reads via ``install_file``."""
    doc: dict[str, object] = {}
    for name, arm in arms.items():
        resolved = arm if isinstance(arm, Arm) else Arm(arm)
        doc[name] = {**_action_json(resolved.action), "trigger": _trigger_json(resolved.trigger)}
    tmp = path.with_suffix(path.suffix + ".tmp")
    _ = tmp.write_text(json.dumps(doc), encoding="utf-8")
    _ = tmp.replace(path)


__all__ = ["armed", "write_arms"]
