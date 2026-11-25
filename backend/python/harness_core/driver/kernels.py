"""Kernel selection (ARCHITECTURE §4.1): the pinned spec's ``executor`` picks the kernel.

``runKind`` says why a run exists; ``executor.kind`` says how it runs. Kinds map to
kernels only here, so a new executor is a registration (E1 registers ``engine``),
never a branch in the driver.
"""

from __future__ import annotations

from types import MappingProxyType
from typing import TYPE_CHECKING, Protocol

from harness_core.driver.refusal import RunRefused

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from harness_core.contracts.generated.run_spec import RunSpec
    from harness_core.journal.types import Claim
    from harness_core.kernel.durable.driver import Disposition

LOOP = "loop"


def executor_kind(spec: RunSpec) -> str:
    # A v2 spec predates executor, and every v2 run ran on the loop (the RunSpec 2 -> 3 upcast).
    return LOOP if spec.executor is None else spec.executor.kind


class ExecutorUnavailable(RunRefused):
    """The spec's executor has no kernel on this worker: refused, never run on another one."""

    code = "executor_unavailable"

    def __init__(self, kind: str) -> None:
        super().__init__(self.code, f"no kernel runs executor {kind!r} on this worker")
        self.kind = kind


class ClaimedKernel(Protocol):
    """Runs one claimed attempt to a disposition (``DurableKernel`` and, from E1, ``EngineKernel``)."""

    async def run_claimed(self, claim: Claim, *, claim_sent_at: float) -> Disposition: ...

    def request_drain(self) -> None: ...


class KernelRegistry[K]:
    """Executor kind -> kernel; fixed at construction, one kernel per kind."""

    def __init__(self, entries: Iterable[tuple[str, K]]) -> None:
        super().__init__()
        kernels: dict[str, K] = {}
        for kind, kernel in entries:
            if kind in kernels:
                raise ValueError(f"two kernels registered for executor {kind!r}")
            kernels[kind] = kernel
        if not kernels:
            raise ValueError("a kernel registry needs at least one kernel")
        self._kernels: Mapping[str, K] = MappingProxyType(kernels)

    def kernel_for(self, spec: RunSpec) -> K:
        kind = executor_kind(spec)
        kernel = self._kernels.get(kind)
        if kernel is None:
            raise ExecutorUnavailable(kind)
        return kernel

    def kinds(self) -> frozenset[str]:
        return frozenset(self._kernels)

    def kernels(self) -> tuple[K, ...]:
        return tuple(self._kernels.values())


__all__ = [
    "LOOP",
    "ClaimedKernel",
    "ExecutorUnavailable",
    "KernelRegistry",
    "executor_kind",
]
