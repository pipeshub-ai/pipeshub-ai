"""The runtimes that turn a run's pinned items into tools, one per kind (03-interfaces §3.2, CPD-7)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.control_plane.runtime.types import ItemLoad
from harness_core.driver import CapabilityLost

if TYPE_CHECKING:
    from collections.abc import Sequence

    from app.control_plane.contracts.generated.effective_set import EffectiveItem
    from app.control_plane.runtime.ports import CredentialBroker, ItemRuntime
    from app.control_plane.runtime.types import RunContext


class ItemRuntimes:
    """``ItemRuntimeRegistry``: one runtime per kind; a kind no runtime serves is a lost capability."""

    def __init__(self, runtimes: Sequence[ItemRuntime] = ()) -> None:
        self._runtimes: dict[str, ItemRuntime] = {}
        for runtime in runtimes:
            self.register(runtime)

    def register(self, runtime: ItemRuntime) -> None:
        if runtime.kind in self._runtimes:
            raise ValueError(f"a runtime for kind {runtime.kind} is already registered")
        self._runtimes[runtime.kind] = runtime

    def for_kind(self, kind: str) -> ItemRuntime:
        runtime = self._runtimes.get(kind)
        if runtime is None:
            raise CapabilityLost(missing=[kind])
        return runtime

    async def load(self, items: Sequence[EffectiveItem], ctx: RunContext, credentials: CredentialBroker) -> ItemLoad:
        """Every pinned item, each by its kind's runtime; one refusal names every lost capability across kinds,
        and closes what the kinds that did load opened."""
        loads: list[ItemLoad] = []
        missing: list[str] = []
        changed: list[str] = []
        kinds = sorted({item.kind.root for item in items if item.kind is not None and item.state == "ready"})
        try:
            for kind in kinds:
                try:
                    runtime = self.for_kind(kind)
                    loads.append(await runtime.load([i for i in items if i.kind is not None and i.kind.root == kind], ctx, credentials))
                except CapabilityLost as lost:
                    missing.extend(lost.missing)
                    changed.extend(lost.changed)
            if missing or changed:
                raise CapabilityLost(missing=missing, changed=changed)
        except BaseException:
            for load in reversed(loads):
                await load.close()
            raise
        return ItemLoad(
            tools=tuple(tool for load in loads for tool in load.tools),
            mounts=tuple(mount for load in loads for mount in load.mounts),
            prompt_parts=tuple(part for load in loads for part in load.prompt_parts),
            closers=tuple(closer for load in loads for closer in load.closers),
        )


__all__ = ["ItemRuntimes"]
