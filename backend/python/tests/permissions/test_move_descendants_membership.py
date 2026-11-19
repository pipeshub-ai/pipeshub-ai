"""Does moving a node refresh its descendants' vector membership? (Not yet: this test pins today's behaviour.)

`on_records_moved` re-points one parent edge and publishes a membership sync for
the moved record's virtual record id only. A descendant keeps its own group, so
its `recordGroupIds` (built in `services/vector_db/membership.py`) go stale when a
folder moves to another group. Stale membership loses recall, never admits: the
graph check, not the vector payload, decides access.

The test pins today's behaviour so the day a descendant refresh lands, it fails
and gets updated on purpose. It drives the real processor (`processor_harness.py`).
"""

from __future__ import annotations

import pytest

from app.config.constants.arangodb import CollectionNames, EventTypes
from app.services.vector_db.membership import resolve_virtual_record_state
from tests.integration.graph_permissions.processor_harness import build_processor
from tests.integration.graph_permissions.test_write_path import _group, _record
from tests.integration.graph_permissions.write_path_support import (
    ORG,
    _ArangoBackend,
    _Neo4jBackend,
    _seed_app,
    _suffix,
)

pytestmark = [pytest.mark.integration, pytest.mark.timeout(300)]


@pytest.fixture(params=["arango", "neo4j"])
def backend(request, arango_provider, arango_settings, neo4j_provider, neo4j_settings) -> _ArangoBackend | _Neo4jBackend:
    if request.param == "arango":
        return _ArangoBackend(arango_provider, arango_settings)
    return _Neo4jBackend(neo4j_provider, neo4j_settings)


def _published_vrids(producer) -> set[str]:
    vrids: set[str] = set()
    for _, payload in producer.messages:
        body = payload[1] if isinstance(payload, tuple) else payload
        if isinstance(body, dict) and body.get("eventType") == EventTypes.SYNC_VECTOR_MEMBERSHIP.value:
            vrids.add((body.get("payload") or {}).get("virtualRecordId") or "")
    return vrids


async def test_moving_a_folder_does_not_refresh_its_descendants(backend) -> None:
    processor, producer = build_processor(backend.provider, ORG)
    conn = f"conn-{_suffix()}"
    await _seed_app(backend, conn)
    old_group, new_group = f"space-{_suffix()}", f"space-{_suffix()}"
    await processor.on_new_record_groups([
        (_group(old_group, inherit=True, connector_id=conn), []),
        (_group(new_group, inherit=True, connector_id=conn), []),
    ])
    folder = _record(f"folder-{_suffix()}", connector_id=conn, group_external=old_group)
    child = _record(f"child-{_suffix()}", connector_id=conn, parent_external=folder.external_record_id,
                    group_external=old_group)
    await processor.on_new_records([(folder, []), (child, [])])
    # Indexing assigns the virtual record id later; the probe stamps it so membership has something to resolve.
    folder.virtual_record_id, child.virtual_record_id = f"v-folder-{_suffix()}", f"v-child-{_suffix()}"
    for record in (folder, child):
        await backend.provider.update_node(record.id, CollectionNames.RECORDS.value, {"virtualRecordId": record.virtual_record_id})
    producer.messages.clear()

    moved = _record(folder.external_record_id, connector_id=conn, group_external=new_group)
    moved.virtual_record_id = folder.virtual_record_id
    await processor.on_records_moved([(folder.external_record_id, moved, [])])

    provider = backend.provider
    old_id = (await provider.get_record_group_by_external_id(conn, old_group)).id
    new_id = (await provider.get_record_group_by_external_id(conn, new_group)).id
    folder_state = await resolve_virtual_record_state(provider, folder.virtual_record_id)
    child_state = await resolve_virtual_record_state(provider, child.virtual_record_id)

    assert new_id in folder_state.record_group_ids and old_id not in folder_state.record_group_ids, "control: the moved folder follows"
    assert folder.virtual_record_id in _published_vrids(producer), "control: the moved folder's membership is published"
    # Known gap: the child keeps the old group in the graph and in the membership it would write to the index.
    assert old_id in child_state.record_group_ids and new_id not in child_state.record_group_ids
    assert child.virtual_record_id not in _published_vrids(producer)
