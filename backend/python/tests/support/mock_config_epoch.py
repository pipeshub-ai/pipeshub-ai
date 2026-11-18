"""A fixed collection epoch for tests whose configuration service is a mock.

Every writer reads the epoch of its collection (its point ids depend on it). A mock configuration
service has no epoch keys to read, so such a test gets a fixed token; a real service is used as it is.
Unit and integration conftests both use this fixture, so it lives here, not in either test tree.
"""

import sys

import pytest


@pytest.fixture(autouse=True)
def _epoch_of_a_collection_whose_config_is_a_mock(monkeypatch) -> None:
    """Nothing is imported here: a test that never loaded the guard (the import-cost tests among them) is left as it is."""
    module = sys.modules.get("app.services.vector_db.precision_guard")
    if module is None:
        return
    from unittest.mock import NonCallableMock

    from app.services.vector_db.collection_epoch import Fence

    PrecisionGuard = module.PrecisionGuard
    real = PrecisionGuard.capture

    async def capture(self, collection):  # noqa: ANN202
        if isinstance(self._pins._config, NonCallableMock):
            return Fence(collection, "mock-token")
        return await real(self, collection)

    monkeypatch.setattr(PrecisionGuard, "capture", capture)
