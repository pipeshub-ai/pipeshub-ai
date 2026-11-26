"""SS3 WS-1 (Python side): Node re-seals a session's objects for a fork, so both sides hold to one vector."""

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any

import pytest

from harness_core.workspace.memory import InMemoryBinaryObjectStore
from harness_core.workspace.ports import DataKey
from harness_core.workspace.sealing import SealedObjectStore
from harness_core.workspace.types import ObjectRef, ObjectScope

VECTORS: list[dict[str, Any]] = json.loads(
    (Path(__file__).resolve().parents[6] / "contracts/harness/vectors/workspace-sealing.json").read_text(
        encoding="utf-8"
    )
)["objects"]


class _Keys:
    def __init__(self, key: DataKey) -> None:
        self._key = key

    async def data_key(self, tenant_id: str, key_domain: str) -> DataKey:
        del tenant_id, key_domain
        return self._key


def _store(case: dict[str, Any]) -> tuple[SealedObjectStore, InMemoryBinaryObjectStore, ObjectScope]:
    inner = InMemoryBinaryObjectStore()
    key = DataKey(case["keyId"], base64.b64decode(case["key"]))
    return SealedObjectStore(inner, _Keys(key)), inner, ObjectScope(case["tenantId"], case["partition"])


@pytest.mark.parametrize("case", VECTORS, ids=[case["name"] for case in VECTORS])
async def test_python_seals_the_vector(case: dict[str, Any]) -> None:
    store, inner, scope = _store(case)
    ref = await store.put(scope, base64.b64decode(case["plaintext"]))
    assert (ref.object_id, ref.content_hash, ref.size_bytes) == (case["objectId"], case["contentHash"], case["sizeBytes"])
    assert inner.stored_bytes() == [base64.b64decode(case["stored"])]


@pytest.mark.parametrize("case", VECTORS, ids=[case["name"] for case in VECTORS])
async def test_python_opens_the_vector_bytes(case: dict[str, Any]) -> None:
    store, inner, scope = _store(case)
    stored = base64.b64decode(case["stored"])
    _ = await inner.put(scope, stored)
    ref = ObjectRef(case["objectId"], case["contentHash"], case["sizeBytes"])
    assert await store.get(scope, ref) == base64.b64decode(case["plaintext"])
