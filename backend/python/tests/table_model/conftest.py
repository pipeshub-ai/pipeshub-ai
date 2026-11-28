"""Fixtures of the real-model tests. These load the structure model: they need the weights (``DOCLING_ARTIFACTS_PATH``)
and fail, not skip, when CI or ``REQUIRE_TABLE_MODEL`` says they must run."""
from __future__ import annotations

import pytest

from app.services.docling.table_structure import TableSupervisor
from tests.table_model import support


@pytest.fixture(scope="session")
def weights() -> str:
    return support.weights_dir()


@pytest.fixture(scope="session")
def harness(weights):  # noqa: ANN201
    return support.Harness(weights)


@pytest.fixture(scope="session")
def supervisor(weights):  # noqa: ANN201
    sup = TableSupervisor(weights_dir=lambda _mode: weights, memory_available=lambda: 10**12, model_timeout_ceiling=120.0)
    yield sup
    support.run_async(sup.shutdown())
