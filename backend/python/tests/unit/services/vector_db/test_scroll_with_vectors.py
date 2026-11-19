"""``scroll_with_vectors``: the default refuses with a typed error; Redis decodes a stored vector."""
from __future__ import annotations

import pytest

from app.services.vector_db.errors import VectorsNotFetchable
from app.services.vector_db.interface.vector_db import IVectorDBService
from app.services.vector_db.redis.utils import bytes_to_vector, vector_to_bytes


async def test_the_interface_default_refuses_with_a_typed_error() -> None:
    class Bare:
        def get_service_name(self) -> str:
            return "bare"

    with pytest.raises(VectorsNotFetchable):
        await IVectorDBService.scroll_with_vectors(Bare(), "c", None, 10)  # type: ignore[arg-type]


def test_the_typed_error_is_a_not_implemented_error() -> None:
    assert issubclass(VectorsNotFetchable, NotImplementedError)


@pytest.mark.parametrize("dtype", ["FLOAT32", "FLOAT16"])
def test_redis_vectors_round_trip(dtype: str) -> None:
    vector = [0.5, -1.25, 3.0, 0.0]
    assert bytes_to_vector(vector_to_bytes(vector, dtype), dtype) == pytest.approx(vector, abs=1e-3)
