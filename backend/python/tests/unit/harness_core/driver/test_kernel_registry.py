"""F2: the worker reads RunSpec 2 and 3, and the executor picks the kernel through one registry."""

from __future__ import annotations

import ast
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from harness_core.driver import KernelRegistry, executor_kind
from harness_core.driver.capabilities import (
    SUPPORTED_SPEC_SCHEMA_VERSIONS,
    worker_capabilities,
)
from harness_core.testing.contracts.kernel_registry import (
    ENGINE,
    KernelRegistryContract,
)
from harness_core.testing.run_spec import minimal_run_spec

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

    from harness_core.testing.kernels import RecordingKernel

DRIVER = Path(__file__).resolve().parents[4] / "harness_core" / "driver"


class TestKernelRegistry(KernelRegistryContract):
    @pytest.fixture
    def make_registry(
        self,
    ) -> Callable[[Iterable[tuple[str, RecordingKernel]]], KernelRegistry[RecordingKernel]]:
        return KernelRegistry


def test_rs3_9_the_worker_reads_spec_versions_2_and_3() -> None:
    assert SUPPORTED_SPEC_SCHEMA_VERSIONS == (2, 3)
    assert [v.root for v in worker_capabilities().supported_schema_versions] == [2, 3]


@pytest.mark.parametrize(
    ("spec", "kind"),
    [
        (minimal_run_spec(), "loop"),
        (minimal_run_spec(executor={"kind": "loop"}), "loop"),
        (minimal_run_spec(executor=ENGINE), "engine"),
    ],
)
def test_rs3_9_a_v2_spec_reads_as_the_loop(spec: object, kind: str) -> None:
    assert executor_kind(spec) == kind  # type: ignore[arg-type]


def _compares_an_executor_kind(node: ast.AST) -> bool:
    if not isinstance(node, ast.Compare):
        return False
    names = [n for n in (node.left, *node.comparators) if isinstance(n, ast.Constant)]
    return any(n.value in {"loop", "engine"} for n in names)


def test_ks8_no_kernel_choice_outside_the_registry() -> None:
    offenders = [
        f"{path.name}:{node.lineno}"
        for path in sorted(DRIVER.glob("*.py"))
        if path.name != "kernels.py"
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        if _compares_an_executor_kind(node) or isinstance(node, ast.Match)
    ]
    assert offenders == []
