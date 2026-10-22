from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from app.services.model_identity import metrics
from app.services.model_identity.principal import user_principal

if TYPE_CHECKING:
    from collections.abc import Iterator

    from app.services.model_identity.principal import Principal


@pytest.fixture(autouse=True)
def _reset_metrics() -> Iterator[None]:
    metrics.reset()
    yield
    metrics.reset()


@pytest.fixture
def alice() -> Principal:
    return user_principal(
        {"userId": "u-alice", "email": "alice@example.com", "fullName": "Alice A"},
        "org-1",
        idp_object_id="tid:oid-alice",
    )


@pytest.fixture
def bob() -> Principal:
    return user_principal({"userId": "u-bob", "email": "bob@example.com"}, "org-1")
