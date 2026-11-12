# ruff: noqa: F811, ANN201, ANN202
"""The pending registry through the real processor and transaction layer, on Neo4j and ArangoDB (`KH_PERM_*`).

A change of one record's place, inheritance, group, rule or first/last grant registers that record as a pending root in the
transaction that bumps the connector's structure generation. A change that reaches more than a subtree (a delete, a group) bumps and
registers nothing, as does a grant-only change (not even a bump).
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from app.config.constants.arangodb import CollectionNames, DeleteSource
from app.connectors.core.base.data_store.graph_data_store import GraphDataStore
from app.models.entities import AccessRule
from app.models.permission import EntityType, Permission, PermissionType
from app.services.permissions.errors import ViewerScopesUnavailable
from app.services.permissions.pending import MAX_PENDING_ROOTS, parse
from app.services.permissions.structure_generation import (
    CLOSED_KEY,
    GENERATION_KEY,
    OPENED_KEY,
    PENDING_KEY,
    ScopeGenerationConflict,
    ScopeGenerationError,
    clear_pending_covered,
    leaving_vrid,
)
from tests.integration.graph_permissions.processor_harness import build_processor
from tests.integration.graph_permissions.test_write_path import _group, _record
from tests.integration.graph_permissions.write_path_support import (
    ORG,
    _seed_user,
    _suffix,
)

from .test_currency_real_graph import current, finish_pass
from .test_generation_paths import _scope_fields_on, _writers_world, backend  # noqa: F401

pytestmark = [pytest.mark.integration, pytest.mark.timeout(900)]


@pytest.fixture(autouse=True)
async def _no_roots_left_by_other_tests(backend) -> None:
    """The stores are shared by every test of the run, and `pending_scopes` answers for the whole organisation."""
    if hasattr(backend, "_aql"):
        await backend._aql("FOR a IN apps FILTER a.orgId == @org UPDATE a WITH {scopeProjectionPendingRoots: null} IN apps OPTIONS {keepNull: false}", {"org": ORG})
    else:
        await backend.provider.client.execute_query("MATCH (a:App {orgId: $org}) REMOVE a.scopeProjectionPendingRoots", parameters={"org": ORG})


async def flat(view, org: str) -> frozenset[str]:
    return frozenset().union(*(await view.pending_scopes(org)).values())


async def app_of(backend, conn: str) -> dict:
    return await backend.provider.get_document(conn, CollectionNames.APPS.value) or {}


async def marks(backend, conn: str) -> tuple[int, dict[str, int]]:
    app = await app_of(backend, conn)
    return int(app.get(GENERATION_KEY) or 0), parse(app.get(PENDING_KEY))


def grants(emails):
    return [Permission(email=e, type=PermissionType.READ, entity_type=EntityType.USER) for e in emails]


async def test_a_move_registers_the_moved_record_and_not_the_whole_connector(backend) -> None:
    processor, conn, _, space2, _, child, _ = await _writers_world(backend)
    g0, _ = await marks(backend, conn)
    moved = _record(child.external_record_id, connector_id=conn, group_external=space2)
    await processor.on_records_moved([(child.external_record_id, moved, [])])
    g1, pending = await marks(backend, conn)
    assert g1 != g0 and pending == {child.id: g1}


async def test_a_new_parent_registers_the_record(backend) -> None:
    processor, conn, space, _, page, _, lone = await _writers_world(backend)
    upd = _record(lone.external_record_id, connector_id=conn, parent_external=page.external_record_id, group_external=space)
    await processor.on_record_metadata_update(upd)
    assert set((await marks(backend, conn))[1]) == {lone.id}


async def test_a_restriction_registers_the_restricted_record(backend) -> None:
    processor, conn, space, _, page, *_ = await _writers_world(backend)
    restricted = _record(page.external_record_id, connector_id=conn, group_external=space, rule=AccessRule.RESTRICTED)
    restricted.virtual_record_id = page.virtual_record_id
    await processor.on_new_records([(restricted, [])])
    assert set((await marks(backend, conn))[1]) == {page.id}


async def test_detaching_and_attaching_a_record_registers_it(backend) -> None:
    processor, conn, space, _, page, child, lone = await _writers_world(backend)
    await processor.on_records_detached_from_parent([child.id])
    assert set((await marks(backend, conn))[1]) == {child.id}
    await processor.on_records_attached_to_parent(page.id, [lone.id])
    assert set((await marks(backend, conn))[1]) == {child.id, lone.id}


@pytest.mark.parametrize("what", ["delete", "group_rule"])
async def test_a_change_that_reaches_more_than_a_subtree_bumps_and_registers_nothing(backend, what: str) -> None:
    processor, conn, space, _, page, child, _ = await _writers_world(backend)
    g0, _ = await marks(backend, conn)
    if what == "delete":
        await processor.on_records_soft_deleted([child.id], conn, delete_source=DeleteSource.CONNECTOR)
    else:
        await processor.on_new_record_groups([(_group(space, inherit=False, connector_id=conn, rule=AccessRule.RESTRICTED), [])])
    g1, pending = await marks(backend, conn)
    assert g1 != g0 and not pending


async def test_a_whole_connector_change_beside_a_subtree_change_in_one_transaction_registers_nothing(backend) -> None:
    processor, conn, space, space2, page, child, lone = await _writers_world(backend)
    store = GraphDataStore(MagicMock(), backend.provider)
    async with store.transaction() as tx:
        await tx.delete_single_record(lone.id)
        await tx.delete_parent_child_edge_to_record(child.id)
    assert not (await marks(backend, conn))[1]


async def test_a_grant_only_change_registers_nothing_and_bumps_nothing(backend) -> None:
    processor, conn, space, _, page, *_ = await _writers_world(backend)
    before = await marks(backend, conn)
    same = _record(page.external_record_id, connector_id=conn, group_external=space)
    same.id = page.id
    await processor.on_updated_record_permissions(same, [])
    assert await marks(backend, conn) == before


async def test_resending_unchanged_grants_registers_nothing(backend) -> None:
    processor, conn, space, _, page, *_ = await _writers_world(backend)
    emails = [f"m{i}-{_suffix()}@example.com" for i in range(2)]
    for e in emails:
        await _seed_user(backend, e)
    rec = _record(page.external_record_id, connector_id=conn, group_external=space)
    rec.id = page.id
    await processor.on_updated_record_permissions(rec, grants(emails))
    before = await marks(backend, conn)
    await processor.on_updated_record_permissions(rec, grants(emails))
    assert await marks(backend, conn) == before


@pytest.mark.parametrize("before,after", [(0, 1), (1, 0), (2, 0), (1, 2), (2, 1)])
@pytest.mark.parametrize("path", ["on_new_records", "on_updated_record_permissions"])
async def test_the_first_and_the_last_grant_register_the_record_and_nothing_else_does(backend, path, before, after) -> None:
    processor, conn, space, _, page, child, lone = await _writers_world(backend)
    emails = [f"g{i}-{_suffix()}@example.com" for i in range(2)]
    for e in emails:
        await _seed_user(backend, e)
    rec = _record(page.external_record_id, connector_id=conn, group_external=space)
    rec.id = page.id

    async def send(n: int) -> None:
        if path == "on_new_records":
            await processor.on_new_records([(rec, grants(emails[:n]))])
        else:
            await processor.on_updated_record_permissions(rec, grants(emails[:n]))

    async def n_edges() -> int:
        return len(await backend.provider.get_edges_to_node(f"records/{page.id}", CollectionNames.PERMISSION.value) or [])

    await send(before)
    g0, _ = await marks(backend, conn)
    await clear_pending_covered(backend.provider, conn, g0)
    e0 = await n_edges()
    await send(after)
    g1, pending = await marks(backend, conn)
    flipped = (e0 == 0) != (await n_edges() == 0)
    assert (set(pending) == {page.id}) if flipped else (not pending and g1 == g0), (flipped, g0, g1, pending)


async def test_more_roots_than_the_cap_in_one_sync_bump_and_register_none(backend) -> None:
    processor, conn, space, space2, *_ = await _writers_world(backend)
    n = MAX_PENDING_ROOTS + 5
    recs = [_record(f"cap-{i}-{_suffix()}", connector_id=conn, group_external=space) for i in range(n)]
    await processor.on_new_records([(r, []) for r in recs])
    g0, _ = await marks(backend, conn)
    moved = [_record(r.external_record_id, connector_id=conn, group_external=space2) for r in recs]
    await processor.on_records_moved([(m.external_record_id, m, []) for m in moved])
    g1, pending = await marks(backend, conn)
    assert g1 != g0 and not pending


@pytest.mark.parametrize("n", [8, 24, 60])
async def test_many_syncs_registering_roots_at_once_lose_none(backend, n) -> None:
    processor, conn, space, space2, *_ = await _writers_world(backend)
    recs = [_record(f"m{i}-{_suffix()}", connector_id=conn, group_external=space) for i in range(n)]
    await processor.on_new_records([(r, []) for r in recs])
    ids = {r.external_record_id: (await backend.provider.get_record_by_external_id(connector_id=conn, external_id=r.external_record_id)).id for r in recs}
    procs = [build_processor(backend.provider, ORG)[0] for _ in recs]
    moved = [_record(r.external_record_id, connector_id=conn, group_external=space2) for r in recs]

    async def sync(p, m) -> None:
        # A burst can use up one batch's retries: it rolled back whole, and the sync runs it again.
        for attempt in range(4):
            try:
                await p.on_records_moved([(m.external_record_id, m, [])])
                return
            except ScopeGenerationConflict:
                if attempt == 3:
                    raise
                await asyncio.sleep(1)

    results = await asyncio.gather(*(sync(p, m) for p, m in zip(procs, moved, strict=True)), return_exceptions=True)
    errors = [r for r in results if isinstance(r, BaseException)]
    _, pending = await marks(backend, conn)
    assert not errors, errors[:2]
    assert set(ids.values()) <= set(pending), f"{len(set(ids.values()) - set(pending))} of {n} roots lost"


async def test_a_value_that_does_not_parse_makes_the_connector_not_current_and_the_viewer_unavailable_until_a_pass_repairs_it(backend) -> None:
    processor, conn, space, space2, _, child, _ = await _writers_world(backend)
    await finish_pass(backend.provider, conn)
    assert await current(backend.provider, conn)
    assert await backend.provider.update_node(conn, CollectionNames.APPS.value, {PENDING_KEY: "garbage"})
    assert not await current(backend.provider, conn)
    with pytest.raises(ViewerScopesUnavailable):
        await backend.provider.permission_hierarchy_view().pending_scopes(ORG)
    g, _ = (await marks_raw(backend, conn))
    await clear_pending_covered(backend.provider, conn, g - 1)      # a pass from before the last change cannot tell what was registered
    assert (await app_of(backend, conn))[PENDING_KEY] == "garbage"
    await finish_pass(backend.provider, conn)
    await clear_pending_covered(backend.provider, conn, g)
    assert await current(backend.provider, conn)
    assert await flat(backend.provider.permission_hierarchy_view(), ORG) == frozenset()


async def marks_raw(backend, conn: str) -> tuple[int, object]:
    app = await app_of(backend, conn)
    return int(app.get(GENERATION_KEY) or 0), app.get(PENDING_KEY)


async def test_the_registry_is_what_the_hierarchy_view_returns_for_the_organisation_and_only_for_it(backend) -> None:
    processor, conn, _, space2, _, child, _ = await _writers_world(backend)
    other, foreign = f"conn-{_suffix()}", f"foreign-{_suffix()}"
    await backend.provider.batch_upsert_nodes(
        [{"_key": other, "orgId": "another-org", "name": "x", "type": "Confluence", "appGroup": "Atlassian", "scope": "team",
          "isActive": True, "createdAtTimestamp": 1700000000000, PENDING_KEY: f'{{"{foreign}":1}}'}],
        collection=CollectionNames.APPS.value)
    view = backend.provider.permission_hierarchy_view()
    assert await flat(view, ORG) == frozenset()
    moved = _record(child.external_record_id, connector_id=conn, group_external=space2)
    await processor.on_records_moved([(child.external_record_id, moved, [])])
    assert await flat(view, ORG) == {child.id} and set(await view.pending_scopes(ORG)) == {conn}      # by connector
    assert foreign in await flat(view, "another-org") and foreign not in await flat(view, ORG)


async def test_a_pending_record_is_visible_again_when_the_pass_completes(backend) -> None:
    """Recovery: a registered root hides its subtree, a pass from the generation it was registered at takes it off, and a root registered
    after that pass read its generation stays."""
    processor, conn, _, space2, _, child, lone = await _writers_world(backend)
    view = backend.provider.permission_hierarchy_view()
    moved = _record(child.external_record_id, connector_id=conn, group_external=space2)
    await processor.on_records_moved([(child.external_record_id, moved, [])])
    g, pending = await marks(backend, conn)
    assert await flat(view, ORG) == {child.id}
    moved2 = _record(lone.external_record_id, connector_id=conn, group_external=(await _writers_world_space(backend, conn, lone)))
    await processor.on_records_moved([(lone.external_record_id, moved2, [])])
    g2, pending2 = await marks(backend, conn)
    assert set(pending2) == {child.id, lone.id} and g2 > g
    await clear_pending_covered(backend.provider, conn, g)                  # a pass that read generation g
    assert await flat(view, ORG) == {lone.id}                       # the root registered after it stays
    await clear_pending_covered(backend.provider, conn, g2)
    assert await flat(view, ORG) == frozenset() and not (await marks(backend, conn))[1]


async def _writers_world_space(backend, conn: str, lone) -> str:
    """The external id of a record group of the connector the record is not in."""
    own = (await backend.provider.get_document(lone.id, CollectionNames.RECORDS.value)).get("recordGroupId")
    groups = await backend.provider.permission_hierarchy_view().record_group_ids(ORG, conn)
    other = next(g for g in sorted(groups) if g != own)
    return (await backend.provider.get_document(other, CollectionNames.RECORD_GROUPS.value))["externalGroupId"]


async def _shared(backend):
    processor, conn, space, _, page, child, lone = await _writers_world(backend)
    shared = f"vr-{_suffix()}"
    for rec in (page, child):
        assert await backend.provider.update_node(rec.id, CollectionNames.RECORDS.value, {"virtualRecordId": shared})
    return conn, page, child, shared


def _events(backend):
    from app.events.events import EventProcessor

    ep = object.__new__(EventProcessor)
    ep.graph_provider, ep.logger = backend.provider, MagicMock()
    return ep


async def test_a_record_that_leaves_a_shared_virtual_record_registers_the_records_that_stay(backend) -> None:
    conn, page, child, shared = await _shared(backend)
    g0, _ = await marks(backend, conn)
    doc = {"_key": page.id, "virtualRecordId": shared}
    assert await _events(backend).update_record_fields(doc, {"virtualRecordId": f"new-{_suffix()}"})
    g1, pending = await marks(backend, conn)
    assert g1 != g0 and set(pending) == {child.id}
    app = await app_of(backend, conn)
    assert app.get(OPENED_KEY) == app.get(CLOSED_KEY) == 1                      # opened around the write and closed once registered


async def test_a_write_that_does_not_change_the_virtual_record_registers_nothing(backend) -> None:
    conn, page, child, shared = await _shared(backend)
    before = await marks(backend, conn)
    ep = _events(backend)
    assert await ep.update_record_fields({"_key": page.id, "virtualRecordId": shared}, {"virtualRecordId": shared, "indexingStatus": "COMPLETED"})
    assert await ep.update_record_fields({"_key": page.id}, {"indexingStatus": "COMPLETED"})        # a partial doc: nothing known to leave
    assert await marks(backend, conn) == before


async def test_a_crash_after_the_write_leaves_the_connector_not_current_and_the_registration_finishes_the_job(backend) -> None:
    conn, page, child, shared = await _shared(backend)
    await finish_pass(backend.provider, conn)
    assert await current(backend.provider, conn)
    leaving = leaving_vrid(backend.provider, page.id, shared)
    await leaving.__aenter__()
    assert await backend.provider.update_node(page.id, CollectionNames.RECORDS.value, {"virtualRecordId": f"new-{_suffix()}"})
    # the process dies here: the registration after the write never runs
    await finish_pass(backend.provider, conn)                                   # even a pass that read everything after the opening
    assert not await current(backend.provider, conn)
    await leaving.__aexit__(None, None, None)                                    # recovery: the registration is done by whoever finishes it
    assert set((await marks(backend, conn))[1]) == {child.id} and not await current(backend.provider, conn)
    await finish_pass(backend.provider, conn)
    await clear_pending_covered(backend.provider, conn, (await marks(backend, conn))[0])
    assert await current(backend.provider, conn) and not (await marks(backend, conn))[1]


async def test_a_record_with_no_other_sharer_registers_nothing_and_opens_nothing(backend) -> None:
    conn, page, child, shared = await _shared(backend)
    before = await app_of(backend, conn)
    async with leaving_vrid(backend.provider, page.id, f"nobody-{_suffix()}"):
        pass
    assert await app_of(backend, conn) == before


async def test_a_sharer_read_that_fails_marks_the_connectors_and_does_not_fail_the_write(backend, monkeypatch) -> None:
    from app.core.org_context import for_org

    conn, page, child, shared = await _shared(backend)

    async def broken(*a, **k):
        raise RuntimeError("graph down")

    async def apps(*a, **k):
        return [{"_key": conn}]       # the seeded graph has no organisation-to-app edges for the listing to follow

    monkeypatch.setattr(backend.provider, "get_nodes_by_field_in", broken)
    monkeypatch.setattr(backend.provider, "get_org_apps", apps)
    g0, _ = await marks(backend, conn)
    with for_org(ORG, job="test"):
        async with leaving_vrid(backend.provider, page.id, shared):
            pass
    assert (await marks(backend, conn))[0] > g0                                  # fail closed: the connector is not current


async def test_a_skipped_file_that_detaches_its_virtual_record_registers_the_records_that_stay(backend) -> None:
    from unittest.mock import AsyncMock

    from app.events.processor import Processor

    conn, page, child, shared = await _shared(backend)
    proc = object.__new__(Processor)
    proc.graph_provider, proc.logger = backend.provider, MagicMock()
    proc.indexing_pipeline, proc.sink_orchestrator = AsyncMock(), MagicMock(blob_storage=AsyncMock())
    await proc._release_skipped_content(ORG, page.id, shared)
    assert set((await marks(backend, conn))[1]) == {child.id}


async def test_a_connector_save_keeps_the_registry(backend) -> None:
    processor, conn, _, space2, _, child, _ = await _writers_world(backend)
    moved = _record(child.external_record_id, connector_id=conn, group_external=space2)
    await processor.on_records_moved([(child.external_record_id, moved, [])])
    before = (await app_of(backend, conn)).get(PENDING_KEY)
    assert before
    await backend.provider.batch_upsert_nodes([{"id": conn, "isLocked": False, "updatedAtTimestamp": 1700000000001}], CollectionNames.APPS.value)
    assert (await app_of(backend, conn)).get(PENDING_KEY) == before


async def test_with_the_setting_off_the_app_document_is_not_touched(backend, monkeypatch) -> None:
    processor, conn, _, space2, _, child, _ = await _writers_world(backend)
    monkeypatch.delenv("ACL_SCOPE_FIELDS")
    before = await app_of(backend, conn)
    moved = _record(child.external_record_id, connector_id=conn, group_external=space2)
    await processor.on_records_moved([(child.external_record_id, moved, [])])
    assert await app_of(backend, conn) == before


async def test_a_failed_registration_rolls_the_sync_back(backend, monkeypatch) -> None:
    processor, conn, _, space2, _, child, _ = await _writers_world(backend)
    before = await app_of(backend, conn)
    real = backend.provider.update_node

    async def refuse(key, collection, updates, *a, **k):
        if collection == CollectionNames.APPS.value and PENDING_KEY in updates:
            return False
        return await real(key, collection, updates, *a, **k)

    monkeypatch.setattr(backend.provider, "update_node", refuse)
    moved = _record(child.external_record_id, connector_id=conn, group_external=space2)
    with pytest.raises(ScopeGenerationError):
        await processor.on_records_moved([(child.external_record_id, moved, [])])
    assert await app_of(backend, conn) == before
    stored = await backend.provider.get_document(child.id, CollectionNames.RECORDS.value)
    assert stored.get("recordGroupId") != space2


