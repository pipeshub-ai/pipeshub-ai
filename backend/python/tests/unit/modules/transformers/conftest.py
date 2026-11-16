from collections.abc import Iterator

import pytest

from tests.support.bound_org import bind_test_org


@pytest.fixture
def bound_org() -> Iterator[str]:
    yield from bind_test_org()


@pytest.fixture(autouse=True)
def _epoch_of_a_collection_whose_config_is_a_mock(monkeypatch) -> None:
    """Every writer reads the epoch of its collection (its point ids depend on it). Tests that stand in a mock for
    the configuration service have no epoch keys to read: they get a fixed token. A real service is used as it is."""
    from unittest.mock import NonCallableMock

    from app.services.vector_db.collection_epoch import Fence
    from app.services.vector_db.precision_guard import PrecisionGuard

    real = PrecisionGuard.capture

    async def capture(self, collection):  # noqa: ANN202
        if isinstance(self._pins._config, NonCallableMock):
            return Fence(collection, "mock-token")
        return await real(self, collection)

    monkeypatch.setattr(PrecisionGuard, "capture", capture)
