"""Every text point carries the weight precision of the batch that made it, fp32 included."""

import pytest

from app.services.vector_db.collection_pin import FP32, INT8
from tests.unit.modules.transformers.test_vectorstore_precision_guard import (
    _batch,
    _store,
)
from tests.unit.services.vector_db.test_collection_pin import _FakeDb


@pytest.mark.asyncio
@pytest.mark.parametrize("precision", [FP32, INT8])
async def test_each_point_is_tagged_with_its_batchs_precision(precision: str) -> None:
    store, uploaded = _store(_FakeDb(), [(precision, False)])
    await _batch(store)
    assert [p.payload["metadata"]["embedPrecision"] for p in uploaded] == [precision]
