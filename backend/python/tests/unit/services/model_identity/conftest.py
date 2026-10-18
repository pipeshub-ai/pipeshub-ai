import pytest

from app.services.model_identity import metrics
from app.services.model_identity.principal import user_principal


@pytest.fixture(autouse=True)
def _reset_metrics():
    metrics.reset()
    yield
    metrics.reset()


@pytest.fixture
def alice():
    return user_principal(
        {"userId": "u-alice", "email": "alice@example.com", "fullName": "Alice A"},
        "org-1",
        idp_object_id="tid:oid-alice",
    )


@pytest.fixture
def bob():
    return user_principal({"userId": "u-bob", "email": "bob@example.com"}, "org-1")
