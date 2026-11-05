"""A connector's stamps stop being current in the transaction that writes a structural change, on a real graph (Neo4j and ArangoDB), and
a change that moves no structure leaves them current. The pass is written onto the app document as a finished back-fill would leave it.

Needs the stores of the graph-permissions harness (`KH_PERM_*`).
"""

from __future__ import annotations

import pytest

from app.config.constants.arangodb import CollectionNames, DeleteSource
from app.models.entities import AccessRule
from app.services.permissions import structure_generation as sg
from app.services.permissions.currency import StampCurrency, app_is_current
from app.services.permissions.scope_fields import (
    SCOPE_FIELDS_VERSION,
    current_scope_generation,
)
from app.services.vector_db.scope_backfill import BACKFILL_KEYS as K
from app.services.vector_db.scope_backfill import DONE
from tests.integration.graph_permissions.test_write_path import _record
from tests.permissions.test_generation_paths import (  # noqa: F401  (fixtures)
    _gen,
    _scope_fields_on,
    _world,
    backend,
)

pytestmark = [pytest.mark.integration, pytest.mark.timeout(300)]


async def finish_pass(provider, conn: str) -> None:
    """What a finished back-fill leaves on the app document: done, the current field version and ACL generation, and the structure
    generation the connector holds."""
    updates = {K["state"]: DONE, K["version"]: SCOPE_FIELDS_VERSION, K["generation"]: current_scope_generation(),
               K["structure_generation"]: await _gen(provider, conn)}
    assert await provider.update_node(conn, CollectionNames.APPS.value, updates) is not False


async def current(provider, conn: str) -> bool:
    app = await provider.get_document(conn, CollectionNames.APPS.value)
    assert (await StampCurrency(provider).current([conn]) == {conn}) is app_is_current(app)
    return app_is_current(app)


async def test_a_connector_with_a_finished_pass_is_current_and_a_restriction_makes_it_not_current(backend) -> None:  # noqa: F811
    processor, conn, space, page, *_ = await _world(backend)
    await finish_pass(backend.provider, conn)
    assert await current(backend.provider, conn)
    restricted = _record(page.external_record_id, connector_id=conn, group_external=space, rule=AccessRule.RESTRICTED)
    restricted.virtual_record_id = page.virtual_record_id
    await processor.on_new_records([(restricted, [])])
    assert not await current(backend.provider, conn)                  # the stamps of the connector are out of use at once
    await finish_pass(backend.provider, conn)                         # and a new pass makes them current again
    assert await current(backend.provider, conn)


async def test_a_move_a_delete_and_a_permission_update_each_make_a_current_connector_not_current(backend) -> None:  # noqa: F811
    processor, conn, space, page, other, child = await _world(backend)
    moved = _record(child.external_record_id, connector_id=conn, parent_external=other.external_record_id, group_external=space)
    moved.virtual_record_id = child.virtual_record_id
    restricted = _record(page.external_record_id, connector_id=conn, group_external=space, rule=AccessRule.RESTRICTED)
    restricted.id = page.id
    for change in (lambda: processor.on_new_records([(moved, [])]),
                   lambda: processor.on_updated_record_permissions(restricted, []),
                   lambda: processor.on_records_soft_deleted([child.id], conn, delete_source=DeleteSource.CONNECTOR)):
        await finish_pass(backend.provider, conn)
        assert await current(backend.provider, conn)
        await change()
        assert not await current(backend.provider, conn)


async def test_a_new_record_and_an_unchanged_resend_leave_the_connector_current(backend) -> None:  # noqa: F811
    processor, conn, space, page, *_ = await _world(backend)
    await finish_pass(backend.provider, conn)
    fresh = _record(f"fresh-{page.external_record_id}", connector_id=conn, group_external=space)
    await processor.on_new_records([(fresh, [])])
    again = _record(page.external_record_id, connector_id=conn, group_external=space)
    again.virtual_record_id = page.virtual_record_id
    await processor.on_new_records([(again, [])])
    assert await current(backend.provider, conn)


async def test_a_change_that_could_not_move_the_generation_marks_the_connector_unsafe_and_not_current(backend, monkeypatch) -> None:  # noqa: F811
    processor, conn, *_ = await _world(backend)
    await finish_pass(backend.provider, conn)

    async def refuse(*args: object, **kwargs: object) -> None:
        raise RuntimeError("no bump")

    monkeypatch.setattr(backend.provider, "increment_node_field", refuse)
    await sg.mark_scope_dirty(backend.provider, conn, attempts=1)
    assert (await backend.provider.get_document(conn, CollectionNames.APPS.value)).get(sg.UNSAFE_KEY)
    assert not await current(backend.provider, conn)


async def test_a_second_mark_after_the_pass_read_the_first_is_told_from_it_on_both_stores(backend, monkeypatch) -> None:  # noqa: F811
    processor, conn, *_ = await _world(backend)

    async def refuse(*args: object, **kwargs: object) -> None:
        raise RuntimeError("no bump")

    monkeypatch.setattr(backend.provider, "increment_node_field", refuse)
    await sg.mark_scope_dirty(backend.provider, conn, attempts=1)
    first = (await backend.provider.get_document(conn, CollectionNames.APPS.value))[sg.UNSAFE_KEY]
    assert isinstance(first, int | float) and first > 1
    await finish_pass(backend.provider, conn)
    assert await backend.provider.update_node(conn, CollectionNames.APPS.value, {K["unsafe"]: first}) is not False
    assert await current(backend.provider, conn)                          # the pass read this mark
    await sg.mark_scope_dirty(backend.provider, conn, attempts=1)          # a second mark, while the first is still set
    assert (await backend.provider.get_document(conn, CollectionNames.APPS.value))[sg.UNSAFE_KEY] != first
    assert not await current(backend.provider, conn)
