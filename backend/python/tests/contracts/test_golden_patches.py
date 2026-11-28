"""Every golden driver applies the build's ``edition_defaults.golden_contract_patches()``.

The drivers (``golden_support.record_stream``, ``test_durable_goldens.run_durably``, ``test_durable_deep_goldens.run_deep_durably``)
all take their stubs from ``golden_support.prefetch_patches()``, so the build's patches ride along with it: a new driver that
applies the prefetch stubs cannot leave them out.
"""

from __future__ import annotations

import inspect

import pytest

from tests.contracts import (
    golden_support,
    test_durable_deep_goldens,
    test_durable_goldens,
)
from tests.support import edition_defaults

SENTINEL = ("tests.contracts.test_golden_patches.SENTINEL", object())


def test_the_prefetch_stubs_carry_the_builds_golden_patches(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(edition_defaults, "golden_contract_patches", lambda: (SENTINEL,))
    assert SENTINEL in golden_support.prefetch_patches()


@pytest.mark.parametrize(
    "driver",
    [golden_support.record_stream, test_durable_goldens.run_durably, test_durable_deep_goldens.run_deep_durably],
    ids=["record_stream", "run_durably", "run_deep_durably"],
)
def test_every_golden_driver_applies_the_prefetch_stubs(driver: object) -> None:
    assert "prefetch_patches()" in inspect.getsource(driver)  # type: ignore[arg-type]
