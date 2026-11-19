# ruff: noqa: F811, ANN201
"""The rewrite on a real graph (Neo4j and ArangoDB): the page that moved under a restricted parent is rewritten from the real subtree query,
the real membership read and the real projector; the roots come off by a real compare-and-set; and the result equals what a fresh back-fill
of the same graph writes. Needs the stores of the graph-permissions harness (`KH_PERM_*`).
"""

from __future__ import annotations

import copy
import logging

import pytest

from app.config.constants.arangodb import CollectionNames
from app.models.entities import AccessRule
from app.services.permissions.currency import app_is_current
from app.services.permissions.pending import parse, with_roots
from app.services.permissions.structure_generation import GENERATION_KEY
from app.services.vector_db.collection_locator import StaticCollectionLocator
from app.services.vector_db.models import VectorPoint
from app.services.vector_db.scope_backfill import DONE, ScopeBackfill
from app.services.vector_db.scope_rewrite import PENDING_KEY, ScopeRewriteJob
from tests.integration.graph_permissions.processor_harness import build_processor
from tests.integration.graph_permissions.test_write_path import _group, _record
from tests.integration.graph_permissions.write_path_support import (
    ORG,
    _seed_app,
    _suffix,
)
from tests.integration.vector_db.helpers import make_collection_config, make_dense
from tests.permissions.rewrite_kit import FencedMemoryVectorDB
from tests.permissions.test_generation_paths import backend  # noqa: F401  (fixture)

pytestmark = [pytest.mark.integration, pytest.mark.timeout(600)]
OTHER_ORG = "cd" * 12
COLL = "rewrite_real"
log = logging.getLogger("rewrite-real")
SCOPE = ("scopeStamp", "pathIds", "aclScope", "scopeFieldsVersion", "scopeGeneration", "connectorIds", "recordGroupIds", "rootRecordGroupIds")


@pytest.fixture(autouse=True)
def _scope_fields_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ACL_SCOPE_FIELDS", "on")


async def seed(backend):
    """A connector with a space, an open page with a child, and a restricted page; one point per record."""
    p = backend.provider
    processor, _ = build_processor(p, ORG)
    conn, space = f"conn-{_suffix()}", f"space-{_suffix()}"
    await _seed_app(backend, conn)
    await processor.on_new_record_groups([(_group(space, inherit=True, connector_id=conn), [])])
    page = _record(f"page-{_suffix()}", connector_id=conn, group_external=space)
    other = _record(f"other-{_suffix()}", connector_id=conn, group_external=space, rule=AccessRule.RESTRICTED)
    child = _record(f"child-{_suffix()}", connector_id=conn, parent_external=page.external_record_id, group_external=space)
    await processor.on_new_records([(page, []), (other, []), (child, [])])
    ids, vrids = {}, {}
    for name, r in (("page", page), ("other", other), ("child", child)):
        doc = await p.get_record_by_external_id(connector_id=conn, external_id=r.external_record_id)
        ids[name], vrids[name] = doc.id, f"v-{name}-{conn}"
        await p.update_node(doc.id, CollectionNames.RECORDS.value, {"virtualRecordId": vrids[name]})
    vdb = FencedMemoryVectorDB()
    await vdb.create_collection(COLL, make_collection_config())
    points = [VectorPoint(id=f"00000000-0000-4000-8000-{i:012d}", dense_vector=make_dense([1.0]), payload={
        "page_content": v, "metadata": {"orgId": ORG, "virtualRecordId": v}, "connectorIds": [conn], "recordGroupIds": [], "rootRecordGroupIds": []})
        for i, v in enumerate(vrids.values())]
    await vdb.upsert_points(COLL, points)
    return processor, conn, space, ids, vrids, vdb, [pt.id for pt in points], other


async def test_a_moved_page_is_rewritten_on_a_real_graph_to_what_a_fresh_back_fill_writes(backend) -> None:
    p = backend.provider
    processor, conn, space, ids, vrids, vdb, pids, other = await seed(backend)
    locator = StaticCollectionLocator([COLL])
    assert (await ScopeBackfill(p, vdb, locator, log).run_connector(ORG, conn)).state == DONE
    theirs = copy.deepcopy(next(iter((await vdb.retrieve_points(COLL, pids[:1])))))
    theirs.id = "00000000-0000-4000-8000-0000000000aa"
    theirs.payload["metadata"]["orgId"] = OTHER_ORG
    theirs.payload["pathIds"] = ["theirs"]
    await vdb.upsert_points(COLL, [VectorPoint(id=theirs.id, dense_vector=make_dense([1.0]), payload=theirs.payload)])
    before_theirs = copy.deepcopy((await vdb.retrieve_points(COLL, [theirs.id]))[0].payload)

    moved = _record(f"x-{_suffix()}", connector_id=conn, parent_external=other.external_record_id, group_external=space)
    moved.external_record_id = (await p.get_document(ids["child"], CollectionNames.RECORDS.value))["externalRecordId"]
    moved.virtual_record_id = vrids["child"]
    await processor.on_new_records([(moved, [])])             # the sync writes the move and moves the structure generation
    gen = int((await p.get_document(conn, CollectionNames.APPS.value))[GENERATION_KEY])
    await p.update_node(conn, CollectionNames.APPS.value, {PENDING_KEY: with_roots(None, [ids["child"]], gen)})   # (what the registry writes in that transaction)
    assert not app_is_current(await p.get_document(conn, CollectionNames.APPS.value))

    res = await ScopeRewriteJob(p, vdb, locator, settle_seconds=0.2).run_connector(ORG, conn)
    app = await p.get_document(conn, CollectionNames.APPS.value)
    assert res.points_written >= 1, "the move changed the child's point"
    assert res.state == "done" and res.released and not parse(app.get(PENDING_KEY)) and app_is_current(app), (res, app)

    stored = {x.id: {k: x.payload.get(k) for k in SCOPE} for x in await vdb.retrieve_points(COLL, pids)}
    assert (await vdb.retrieve_points(COLL, [theirs.id]))[0].payload == before_theirs      # another organisation's point with the same shape: untouched
    assert (await ScopeBackfill(p, vdb, locator, log).run_connector(ORG, conn, restart=True)).state == DONE
    fresh = {x.id: {k: x.payload.get(k) for k in SCOPE} for x in await vdb.retrieve_points(COLL, pids)}
    assert stored == fresh, "the rewrite did not write what a fresh back-fill of the same graph writes"
