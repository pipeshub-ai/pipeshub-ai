"""The policy-C re-key property on the in-memory store: 40 fixed trials (see tests/support/rekey_property.py for the invariants)."""
from __future__ import annotations

import random

import pytest

from app.modules.transformers import vectorstore as vs_mod
from app.services.vector_db.models import CollectionConfig
from tests.support.in_memory_vector_db import InMemoryVectorDBService
from tests.support.rekey_property import SEED, one_trial


@pytest.mark.parametrize("trial", range(40))
async def test_property_rekey_under_policy_c(monkeypatch: pytest.MonkeyPatch, trial: int) -> None:
    monkeypatch.setattr(vs_mod._token_encoder, "_cached", None, raising=False)  # no tokenizer download
    rng = random.Random(SEED * 1000 + trial)
    db = InMemoryVectorDBService()
    for name in ("records", "fresh"):
        await db.create_collection(name, CollectionConfig(embedding_size=4))
    await one_trial(db, "records", "fresh", 4, rng, monkeypatch, unmovable=True)
