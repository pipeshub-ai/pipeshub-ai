"""Redis membership-scoped deletes past the FT.SEARCH offset ceiling.

Requires: docker compose -f tests/integration/compose/vector-db.yml up -d
Run: pytest tests/integration/vector_db/test_redis_membership_delete.py -m integration --timeout=600

The connector purge asks for "points this connector owns *alone*" — a value
match on ``connectorIds`` plus an array-length bound. RediSearch cannot express
the length bound, so the provider applies it per page and the co-owned points
deliberately survive. Survivors are what make this hard: they stay in the index,
so the walk cannot re-query from offset 0, and an FT.SEARCH cursor is capped by
MAXSEARCHRESULTS (10k default). Past that a connector could never be purged.

These tests seed *more than* that ceiling of retained points on purpose.
"""

import asyncio
import pytest

from app.services.vector_db.const.const import (
    CONNECTOR_IDS_FIELD,
    RECORD_GROUP_IDS_FIELD,
)
from app.services.vector_db.models import (
    FieldCondition,
    FilterExpression,
    VectorPoint,
)
from app.services.vector_db.redis.utils import escape_tag_value

from tests.integration.vector_db.helpers import make_collection_config, make_dense
from tests.integration.vector_db.conftest import make_collection

# loop_scope matches the module-scoped provider fixture in conftest; without
# it every test gets a fresh loop and the shared client raises "Event loop is
# closed" on first use.
pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]

# Above the 10k MAXSEARCHRESULTS default, so an offset-paged walk runs out.
SHARED_POINTS = 11000
EXCLUSIVE_POINTS = 500
# Real connector ids are UUIDs, and "-" is a RediSearch tag separator, so
# these double as a check that the query builder escapes them.
DYING = "6b1f0c2e-4d3a-4a91-9e77-0c5c1f2a8b34"
SURVIVOR = "1c9d7e05-2b6f-4c18-8a30-77e4b9d0af52"


def _point(idx: int, connector_ids: list[str]) -> VectorPoint:
    return VectorPoint(
        id=f"pt-{idx}",
        dense_vector=make_dense([float(idx % 7), 1.0, 0.0]),
        payload={
            "page_content": f"chunk {idx}",
            "metadata": {"orgId": "org1", "virtualRecordId": f"vr-{idx}"},
            CONNECTOR_IDS_FIELD: connector_ids,
            RECORD_GROUP_IDS_FIELD: [f"grp-{idx % 4}"],
        },
    )


async def _count(svc, collection: str, connector_id: str) -> int:
    idx = svc._index_name(collection)
    raw = await svc.client.execute_command(
        "FT.SEARCH", idx,
        f"@{CONNECTOR_IDS_FIELD}:{{{escape_tag_value(connector_id)}}}",
        "NOCONTENT", "LIMIT", "0", "0",
    )
    return int(raw[0])


async def _seed(svc, collection: str) -> None:
    await svc.create_collection(collection, make_collection_config())
    points = [_point(i, [DYING, SURVIVOR]) for i in range(SHARED_POINTS)]
    points += [
        _point(SHARED_POINTS + i, [DYING]) for i in range(EXCLUSIVE_POINTS)
    ]
    for start in range(0, len(points), 1000):
        await svc.upsert_points(collection, points[start:start + 1000])
    for _ in range(60):
        if await _count(svc, collection, DYING) == len(points):
            return
        await asyncio.sleep(1)
    raise AssertionError("seeded points never became searchable")


class TestRedisMembershipDelete:
    async def test_exclusive_delete_past_search_ceiling(self, redis_service):
        """Delete only what the dying connector owns alone, with 11k survivors."""
        col = make_collection("redis_membership")
        try:
            await _seed(redis_service, col)

            await redis_service.delete_points(
                col,
                FilterExpression(
                    must=[
                        FieldCondition(key=CONNECTOR_IDS_FIELD, value=DYING),
                        FieldCondition(key=CONNECTOR_IDS_FIELD, values_count_lte=1),
                    ]
                ),
            )
            await asyncio.sleep(0.5)

            # Every co-owned point survives; every exclusively-owned one is gone.
            assert await _count(redis_service, col, SURVIVOR) == SHARED_POINTS
            assert await _count(redis_service, col, DYING) == SHARED_POINTS
        finally:
            await redis_service.delete_collection(col)

    async def test_last_owner_delete_removes_everything(self, redis_service):
        """With no co-owner left, the same filter clears the whole set."""
        col = make_collection("redis_last_owner")
        try:
            await redis_service.create_collection(col, make_collection_config())
            points = [_point(i, [DYING]) for i in range(SHARED_POINTS)]
            for start in range(0, len(points), 1000):
                await redis_service.upsert_points(col, points[start:start + 1000])
            for _ in range(60):
                if await _count(redis_service, col, DYING) == len(points):
                    break
                await asyncio.sleep(1)
            else:
                # Without this the delete below runs on a half-empty index and
                # the "everything is gone" assertion passes on nothing.
                raise AssertionError("seeded points never became searchable")

            await redis_service.delete_points(
                col,
                FilterExpression(
                    must=[
                        FieldCondition(key=CONNECTOR_IDS_FIELD, value=DYING),
                        FieldCondition(key=CONNECTOR_IDS_FIELD, values_count_lte=1),
                    ]
                ),
            )
            await asyncio.sleep(0.5)
            assert await _count(redis_service, col, DYING) == 0
        finally:
            await redis_service.delete_collection(col)

    async def test_offset_paging_alone_cannot_finish(self, redis_service):
        """The fallback must still fail here, or the test above proves nothing.

        Pins why the cursor exists: on this exact data the offset-paged walk
        runs out of MAXSEARCHRESULTS and raises rather than reporting a
        complete delete. If this ever starts passing, the seed has dropped
        below the ceiling and the cursor test above went vacuous with it.
        """
        col = make_collection("redis_paged")
        try:
            await _seed(redis_service, col)
            idx = redis_service._index_name(col)
            query = f"@{CONNECTOR_IDS_FIELD}:{{{escape_tag_value(DYING)}}}"
            with pytest.raises(RuntimeError, match="MAXSEARCHRESULTS"):
                await redis_service._delete_filtered_paged(
                    col, idx, query, [(CONNECTOR_IDS_FIELD, 1)]
                )
        finally:
            await redis_service.delete_collection(col)

    async def test_length_only_filter_is_refused(self, redis_service):
        """An array-length bound alone matches absent fields — never delete on it."""
        col = make_collection("redis_guard")
        try:
            await redis_service.create_collection(col, make_collection_config())
            await redis_service.upsert_points(col, [_point(0, [SURVIVOR])])
            with pytest.raises(ValueError, match="array-length"):
                await redis_service.delete_points(
                    col,
                    FilterExpression(
                        must=[
                            FieldCondition(
                                key=CONNECTOR_IDS_FIELD, values_count_lte=1
                            )
                        ]
                    ),
                )
        finally:
            await redis_service.delete_collection(col)
