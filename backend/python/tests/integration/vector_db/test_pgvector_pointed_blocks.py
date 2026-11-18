"""The recorded set of pointed blocks on pgvector (PG17 + PgBouncer), one collection (the same cases as test_pointed_blocks_stores.py)."""
from __future__ import annotations

import pytest

from app.core import org_context
from app.services.vector_db.pgvector.config import PgVectorConfig
from app.services.vector_db.pgvector.pgvector import PgVectorService
from tests.integration.db.conftest import cluster, env  # noqa: F401
from tests.integration.vector_db import test_pointed_blocks_stores as pb
from tests.support.index_world import ORG

pytestmark = [pytest.mark.integration, pytest.mark.timeout(600)]


@pytest.fixture
async def svc(env, monkeypatch):  # noqa: ANN201, F811
    port = env.start_bouncer()
    s = await PgVectorService.create(PgVectorConfig(dsn=env.dsn("indexing", port), jobs_dsn=env.dsn("jobs", port), pool_max=4,
                                                    placement="none"))

    async def col(_svc: object) -> str:
        await _svc.delete_points("records", await _svc.filter_collection(must={"orgId": ORG}))
        return "records"

    async def drop(_svc: object, _col: str) -> None:
        await _svc.delete_points("records", await _svc.filter_collection(must={"orgId": ORG}))
    monkeypatch.setattr(pb, "make_col", col)
    monkeypatch.setattr(pb, "drop_col", drop)
    with org_context.for_org(ORG, job="qa"):
        yield s
    await s.disconnect()


async def test_present(svc) -> None:
    await pb.test_a_legacy_multimodal_image_point_present_moves(svc)


@pytest.mark.parametrize("described", [False, True])
async def test_lost(svc, described) -> None:
    await pb.test_a_legacy_multimodal_images_lost_point_reembeds_and_returns_to_search(svc, described)


async def test_skipped(svc) -> None:
    await pb.test_a_provider_skipped_image_reembeds_once_then_is_recorded_exactly(svc)


async def test_stale_low(svc) -> None:
    await pb.test_a_point_lost_after_recording_stays_recorded_and_is_written_again(svc)
