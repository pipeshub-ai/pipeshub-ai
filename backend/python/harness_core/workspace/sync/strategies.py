"""Mount strategies: which folder a sandbox gets for its workspace, chosen from the broker's capabilities.

Both strategies use the same ``TarWorkspaceSync``; they differ in what the
folder holds when a sandbox starts. ``tar`` asks for a scratch mount, which
starts empty, so materialize writes the whole revision; it works on every
broker. ``volume`` asks for the session's persistent ``source`` mount (a
Docker volume subpath, or the broker's copy of it), which starts with the
session's last state, so materialize writes only what changed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from harness_core.sandbox.pool import WARM_POOL
from harness_core.sandbox.types import PERSISTENT_MOUNTS, MountSpec, is_safe_source_key

if TYPE_CHECKING:
    from collections.abc import Callable, Collection, Sequence

    from harness_core.sandbox.types import BrokerHealth


def _scratch(root: str, source: str | None) -> MountSpec:
    del source  # a scratch mount has no persistent store behind it
    return MountSpec(root)


def _persistent(root: str, source: str | None) -> MountSpec:
    if source is None or not is_safe_source_key(source):
        raise ValueError("a persistent workspace mount needs a safe relative source key")
    return MountSpec(root, source=source)


@dataclass(frozen=True, slots=True)
class SyncStrategy:
    name: str
    # Capability flags the broker must report (`BrokerHealth.detail[flag] == "true"`).
    needs: frozenset[str]
    _mount: Callable[[str, str | None], MountSpec]
    # Flags under which the strategy is passed over though its needs are met.
    excluded_by: frozenset[str] = frozenset()

    def mount(self, root: str, *, source: str | None = None) -> MountSpec:
        """The mount at ``root`` a sandbox needs; ``source`` is the session's key in the broker's store."""
        return self._mount(root, source)


TAR = SyncStrategy("tar", frozenset(), _scratch)
# A warm sandbox (P5.9a) is created before its run: it can hold scratch mounts only, never a session's folder.
VOLUME = SyncStrategy("volume", frozenset({PERSISTENT_MOUNTS}), _persistent, excluded_by=frozenset({WARM_POOL}))


def broker_capabilities(health: BrokerHealth) -> frozenset[str]:
    return frozenset(flag for flag, value in health.detail.items() if value == "true")


class SyncStrategies:
    """The first strategy, in preference order, whose needs the broker meets."""

    def __init__(self, strategies: Sequence[SyncStrategy] = (VOLUME, TAR)) -> None:
        if not any(not strategy.needs and not strategy.excluded_by for strategy in strategies):
            raise ValueError("one strategy must need nothing, so every broker gets one")
        self._strategies = tuple(strategies)

    def select(self, capabilities: Collection[str]) -> SyncStrategy:
        offered = frozenset(capabilities)
        return next(
            strategy
            for strategy in self._strategies
            if strategy.needs <= offered and not strategy.excluded_by & offered
        )

    def for_broker(self, health: BrokerHealth) -> SyncStrategy:
        return self.select(broker_capabilities(health))


__all__ = ["TAR", "VOLUME", "SyncStrategies", "SyncStrategy", "broker_capabilities"]
