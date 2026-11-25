"""An index of int8-tagged points and untagged text points is mixed: untagged points are fp32."""

import pytest

from app.services.vector_db.collection_pin import FP32, INT8, STAMP_TAG
from app.services.vector_db.precision_guard import PrecisionUnreadable
from tests.unit.services.vector_db.test_collection_pin import _FakeDb, _guard


@pytest.mark.asyncio
async def test_int8_points_beside_untagged_points_are_refused_as_mixed() -> None:
    db = _FakeDb()
    db.add(INT8, n=2)
    db.add(None, n=1)  # written before the tags existed: fp32
    guard, _, _ = _guard(db)
    with pytest.raises(PrecisionUnreadable):
        await guard.derive("c", "o1")


@pytest.mark.asyncio
async def test_int8_points_with_a_stamp_point_only_are_int8() -> None:
    db = _FakeDb()
    db.add(INT8, n=2)
    db.add(STAMP_TAG, n=1)
    guard, _, _ = _guard(db)
    assert (await guard.derive("c", "o1")).precision == INT8


@pytest.mark.asyncio
async def test_fp32_points_beside_untagged_points_are_fp32() -> None:
    db = _FakeDb()
    db.add(FP32, n=1)
    db.add(None, n=2)
    guard, _, _ = _guard(db)
    assert (await guard.derive("c", "o1")).precision == FP32


@pytest.mark.asyncio
async def test_a_batch_of_int8_is_not_written_into_an_index_of_int8_and_untagged_points() -> None:
    db = _FakeDb()
    db.add(INT8, n=1)
    db.add(None, n=1)
    guard, pins, _ = _guard(db)
    with pytest.raises(PrecisionUnreadable):
        await guard.verify_batch("c", "o1", INT8, fallback=False)
    assert await pins.get("c", "o1", fresh=True) is None  # and no pin was made from the mixture
