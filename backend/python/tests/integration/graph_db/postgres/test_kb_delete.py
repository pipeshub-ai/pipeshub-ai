"""Deleting a knowledge base on PostgreSQL when the store could not confirm the delete.

The KB service and PostgresGraphProvider are real; the KV store, broker and storage release are the recording fakes of
test_kb_delete_real_backends.Deleting. Needs a migrated database: PG_CORE_URL (a `pipeshub_connectors_login` DSN), PG_POOLER=none.

Without a caller's transaction the store deletes in batches, each its own transaction, the app in the last one
(``_finish_connector``, which also checks nothing of the KB is left). It answers ``success: False`` for a KB that is gone
when a rival delete finished it first, or when the last unit committed and its reply was lost (``partial``).
"""
from __future__ import annotations

import logging
import uuid
from types import SimpleNamespace
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest

import app.services.graph_db.postgres.delete as delete_module
from app.config.constants.arangodb import CollectionNames
from app.connectors.sources.localKB.handlers.kb_service import KnowledgeBaseService
from app.services.graph_db.entity_index_queries import APP_STATUS_DELETING
from tests.integration.graph_db.test_kb_delete_real_backends import (
    Deleting,
    one_after_the_other,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

pytestmark = [pytest.mark.integration, pytest.mark.timeout(300)]
APPS, RECORDS = CollectionNames.APPS.value, CollectionNames.RECORDS.value
RECORD_IDS = ("r-1", "r-2")


@pytest.fixture
async def env(pg_provider, pg_db, org_ctx) -> AsyncIterator[SimpleNamespace]:
    """A KB created through the KB service, with two records in it."""
    org = uuid.uuid4().hex[:24]
    with org_ctx(org):
        user_id, user_key = f"user-{org}", f"ukey-{org}"
        assert await pg_provider.batch_upsert_nodes(
            [{"_key": user_key, "userId": user_id, "orgId": org, "email": f"{user_id}@example.com", "isActive": True}], "users")
        kb_service = KnowledgeBaseService(logging.getLogger("kb-delete-pg"), pg_provider, MagicMock())
        created = await kb_service.create_knowledge_base(user_id=user_id, org_id=org, name="kb-delete")
        assert created and created.get("success") is not False, created
        kb_id = created["id"]
        assert await pg_provider.batch_upsert_nodes([
            {"_key": r, "orgId": org, "recordName": r, "externalRecordId": f"x-{r}", "connectorId": kb_id, "recordType": "FILE",
             "version": 1, "origin": "UPLOAD", "connectorName": "KB", "mimeType": "text/plain", "createdAtTimestamp": 1,
             "updatedAtTimestamp": 1, "virtualRecordId": f"v-{r}"} for r in RECORD_IDS], RECORDS)
        assert await pg_provider.batch_create_edges(
            [{"from_id": r, "from_collection": RECORDS, "to_id": kb_id, "to_collection": APPS} for r in RECORD_IDS], "belongsTo")
        yield SimpleNamespace(graph=pg_provider, kb_service=kb_service, kb_id=kb_id, user_id=user_id, org_id=org)
    with org_ctx(org):
        await pg_db.run(lambda tx: tx.execute("DELETE FROM graph.edges WHERE org_id = $1", org), write=True)
        await pg_db.run(lambda tx: tx.execute("DELETE FROM graph.nodes WHERE org_id = $1", org), write=True)


@pytest.fixture
def deleting(env, monkeypatch) -> Deleting:
    return Deleting(env, monkeypatch)


async def kb_doc(env) -> dict | None:
    return await env.graph.get_document(env.kb_id, APPS)


async def records_left(env) -> set[str]:
    return await env.graph.get_existing_record_keys(list(RECORD_IDS))


@pytest.mark.parametrize("first,second", [("owner", "owner"), ("finisher", "owner"), ("owner", "finisher")])
async def test_a_delete_whose_kb_a_rival_delete_removed_first_succeeds(env, deleting, first, second) -> None:
    if "finisher" in (first, second):
        await env.kb_service._mark_deleting(env.kb_id)  # the finisher only acts on a KB that is DELETING
    run = {"owner": deleting.delete, "finisher": deleting.finish}
    a, b = await one_after_the_other(deleting, run[first], run[second])

    assert deleting.cascade_results[-1] == {"success": False, "error": f"Connector instance {env.kb_id} not found"}
    assert await kb_doc(env) is None and await records_left(env) == set()
    assert a["success"] is True, a
    retry = await deleting.delete()
    assert b["success"] is True and b.get("code", 200) == 200, (f"a 500 for a KB that is gone; a retry got {retry.get('code')}", b)


async def test_a_delete_whose_last_unit_committed_but_lost_its_reply_succeeds(env, deleting, monkeypatch) -> None:
    real_finish, real_q = delete_module._finish_connector, env.graph._q
    state = {"finishing": False}

    async def finish(*a: object, **kw: object) -> dict | None:
        state["finishing"] = True
        return await real_finish(*a, **kw)

    async def q(fn: object, transaction: object, **kw: object) -> object:
        result = await real_q(fn, transaction, **kw)
        if state["finishing"]:
            state["finishing"] = False
            raise ConnectionError("connection reset before the commit reply")
        return result

    with monkeypatch.context() as m:
        m.setattr(delete_module, "_finish_connector", finish)
        m.setattr(env.graph, "_q", q)
        owner = await deleting.delete()
    retry = await deleting.delete()

    assert deleting.cascade_results[0]["partial"] is True, deleting.cascade_results
    assert await kb_doc(env) is None and await records_left(env) == set(), "the last unit committed"
    assert (owner["success"], owner["code"]) == (True, 200), (f"a 500 for a KB that is gone; a retry got {retry.get('code')}", owner)
    assert {e["eventType"] for e in deleting.events} <= {"deleteStoredDocuments"}, "the gone path publishes no cleanup"


async def test_a_delete_that_stopped_part_way_with_the_kb_still_there_is_500(env, deleting, monkeypatch) -> None:
    """The real cascade removes the first record in its own transaction, then fails: the KB is still there."""
    monkeypatch.setattr(delete_module, "_CHUNK", 1)
    real_records = delete_module._remove_records
    calls = {"n": 0}

    async def second_batch_fails(*a: object, **kw: object) -> int:
        calls["n"] += 1
        if calls["n"] == 2:
            raise ConnectionError("connection reset in the second record batch")
        return await real_records(*a, **kw)

    monkeypatch.setattr(delete_module, "_remove_records", second_batch_fails)
    owner = await deleting.delete()

    assert (owner["success"], owner["code"]) == (False, 500), owner
    assert deleting.cascade_results[0]["partial"] is True, deleting.cascade_results
    assert (await kb_doc(env))["status"] == APP_STATUS_DELETING
    assert await records_left(env) == {"r-2"}, "the first batch committed, the second did not"
    # the cascade took the owner's grant first, so the server's finisher completes it
    monkeypatch.setattr(delete_module, "_remove_records", real_records)
    assert (await deleting.finish())["success"] is True
    assert await kb_doc(env) is None and await records_left(env) == set()
