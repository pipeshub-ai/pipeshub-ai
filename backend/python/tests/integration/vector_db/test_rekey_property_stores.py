"""The policy-C re-key property on real stores (Qdrant, Redis, OpenSearch): 12 fixed trials each, failed reads injected.

Requires: docker compose -f tests/integration/compose/vector-db.yml up -d
"""
from __future__ import annotations

import contextlib
import random

import pytest

from app.modules.transformers import vectorstore as vs_mod
from tests.integration.vector_db.conftest import make_collection
from tests.integration.vector_db.helpers import DIM, make_collection_config
from tests.support.rekey_property import SEED, one_trial

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module"), pytest.mark.timeout(1800)]


@pytest.fixture(params=["qdrant", "redis", "opensearch"])
def svc(request: pytest.FixtureRequest):  # noqa: ANN201
    name = {"qdrant": "qdrant_service", "redis": "redis_service", "opensearch": "opensearch_service"}[request.param]
    return request.getfixturevalue(name)


async def _collection(svc, tag: str) -> str:
    col = make_collection(tag)
    await svc.create_collection(col, make_collection_config())
    for field in ("metadata.blockId", "metadata.virtualRecordId", "metadata.orgId"):
        with contextlib.suppress(Exception):
            await svc.create_index(col, field, {"type": "keyword"})
    return col


@pytest.mark.parametrize("trial", range(12))
async def test_property_rekey_under_policy_c_on_a_real_store(svc, monkeypatch: pytest.MonkeyPatch, trial: int) -> None:
    monkeypatch.setattr(vs_mod._token_encoder, "_cached", None, raising=False)
    rng = random.Random(SEED * 1000 + 500 + trial)
    col, fresh = await _collection(svc, "rekey_prop"), await _collection(svc, "rekey_prop_fresh")
    try:
        await one_trial(svc, col, fresh, DIM, rng, monkeypatch, unmovable=False)
    finally:
        await svc.delete_collection(col)
        await svc.delete_collection(fresh)
