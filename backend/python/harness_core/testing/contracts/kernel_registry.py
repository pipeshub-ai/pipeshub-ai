"""Contract suite for kernel selection (ARCHITECTURE §4.1): executor kind -> kernel.

Subclass :class:`KernelRegistryContract` and provide a ``make_registry``
fixture: a callable that builds the registry under test from ``(kind, kernel)``
entries. ``harness_core.driver.KernelRegistry`` runs it in
``tests/unit/harness_core/driver/test_kernel_registry.py``; another host (Loom)
runs it against its own selection the same way.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

import pytest

from harness_core.driver.kernels import ExecutorUnavailable
from harness_core.testing.kernels import RecordingKernel
from harness_core.testing.run_spec import minimal_run_spec

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

    from pydantic import JsonValue

    from harness_core.contracts.generated.run_spec import RunSpec


class Registry(Protocol):
    def kernel_for(self, spec: RunSpec) -> RecordingKernel: ...

    def kinds(self) -> frozenset[str]: ...

    def kernels(self) -> tuple[RecordingKernel, ...]: ...


ENGINE: dict[str, JsonValue] = {
    "kind": "engine",
    "engine": {
        "kind": "engine-a",
        "version": "1.0.0",
        "binaryDigest": "sha256:" + "d" * 64,
        "profileDigest": "sha256:" + "e" * 64,
    },
}


class KernelRegistryContract:
    def test_ks1_a_v2_spec_without_executor_runs_on_the_loop(
        self, make_registry: Callable[[Iterable[tuple[str, RecordingKernel]]], Registry]
    ) -> None:
        loop = RecordingKernel()
        registry = make_registry([("loop", loop)])
        spec = minimal_run_spec()
        assert (spec.schema_version, spec.executor) == (2, None)
        assert registry.kernel_for(spec) is loop

    def test_ks1_a_v3_loop_spec_runs_on_the_loop(
        self, make_registry: Callable[[Iterable[tuple[str, RecordingKernel]]], Registry]
    ) -> None:
        loop = RecordingKernel()
        registry = make_registry([("loop", loop)])
        assert registry.kernel_for(minimal_run_spec(executor={"kind": "loop"})) is loop

    def test_ks2_an_executor_without_a_kernel_is_refused_with_its_code(
        self, make_registry: Callable[[Iterable[tuple[str, RecordingKernel]]], Registry]
    ) -> None:
        registry = make_registry([("loop", RecordingKernel())])
        with pytest.raises(ExecutorUnavailable) as refused:
            _ = registry.kernel_for(minimal_run_spec(executor=ENGINE))
        assert refused.value.error_type == "executor_unavailable"
        assert refused.value.kind == "engine"

    def test_ks3_a_registered_engine_kernel_is_chosen_and_the_loop_is_unaffected(
        self, make_registry: Callable[[Iterable[tuple[str, RecordingKernel]]], Registry]
    ) -> None:
        loop, engine = RecordingKernel(), RecordingKernel()
        registry = make_registry([("loop", loop), ("engine", engine)])
        assert registry.kernel_for(minimal_run_spec(executor=ENGINE)) is engine
        assert registry.kernel_for(minimal_run_spec()) is loop
        assert registry.kinds() == frozenset({"loop", "engine"})
        assert set(map(id, registry.kernels())) == {id(loop), id(engine)}

    def test_ks4_two_kernels_for_one_kind_are_refused(
        self, make_registry: Callable[[Iterable[tuple[str, RecordingKernel]]], Registry]
    ) -> None:
        with pytest.raises(ValueError, match="two kernels"):
            _ = make_registry([("loop", RecordingKernel()), ("loop", RecordingKernel())])

    def test_ks4_an_empty_registry_is_refused(
        self, make_registry: Callable[[Iterable[tuple[str, RecordingKernel]]], Registry]
    ) -> None:
        with pytest.raises(ValueError, match="at least one"):
            _ = make_registry([])


__all__ = ["ENGINE", "KernelRegistryContract", "Registry"]
