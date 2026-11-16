"""Who may inline a stored document into a knowledge-base push (``get_ref_access``) on PostgreSQL, against a real database.

The cells of ``tests/integration/graph_db/test_kb_push_images_real_backends.py`` (Neo4j and ArangoDB), on a graph written here: a document is readable
exactly when ``check_record_access_with_details`` opens the knowledge-base upload that links it, only links the platform wrote grant, and one document's
many links cannot hide another's. Links are the caller's org's only: another org's records are never answered, nor anything of their rows.

Needs a migrated database: PG_CORE_URL (a `pipeshub_connectors_login` DSN), PG_POOLER=none.
"""
from __future__ import annotations

import uuid
from unittest.mock import AsyncMock

import pytest

from app.services.graph_db.interface.graph_db_provider import REF_LINKS_PER_DOC

from .test_access import APP, World, _org, rec
from .test_access import w as w
from .test_grants import grant, in_org

pytestmark = pytest.mark.integration

KB = "kb1"


def _doc() -> str:
    return uuid.uuid4().hex[:24]


async def kb_world(w: World) -> tuple[str, str]:
    """A knowledge base owned by ``uid-own`` with one upload record linking a document: ``(record id, document id)``."""
    doc = _doc()
    await w.nodes("users", {"_key": "own", "userId": "uid-own"}, {"_key": "wr", "userId": "uid-wr"}, {"_key": "rd", "userId": "uid-rd"},
                  {"_key": "none", "userId": "uid-none"})
    await w.nodes("apps", {"_key": KB, "type": "KB", "name": "KB1"})
    await w.nodes("records", rec("up", connectorId=KB, origin="UPLOAD", connectorName="KB", externalRecordId=doc, recordType="FILE"))
    await w.child(("apps", KB), ("records", "up"))
    for who, role in (("own", "OWNER"), ("wr", "WRITER"), ("rd", "READER")):
        await grant(w, who, "apps", KB, role)
    return "up", doc


async def details_allows(w: World, user_id: str, record_id: str, org: str | None = None) -> bool:
    return await w.p.check_record_access_with_details(user_id, org or w.org, record_id) is not None


@pytest.mark.parametrize("user", ["uid-own", "uid-wr", "uid-rd", "uid-none"])
async def test_ref_access_equivalence(w: World, user: str) -> None:
    record_id, doc = await kb_world(w)
    got = await w.p.get_ref_access(user, w.org, [doc])
    assert doc in got.linked
    assert (doc in got.readable) == await details_allows(w, user, record_id), user
    assert (doc in got.readable) == (user != "uid-none")


async def test_ref_access_equivalence_trashed_record_other_org_and_deleting_kb(w: World) -> None:
    record_id, doc = await kb_world(w)
    outsider_org = _org()
    with in_org(outsider_org):
        await w.p.batch_upsert_nodes([{"_key": "out", "userId": "uid-out", "orgId": outsider_org}], "users")
    got = await w.p.get_ref_access("uid-out", w.org, [doc])
    assert doc not in got.readable and not await details_allows(w, "uid-out", record_id)
    await w.p.update_node(KB, "apps", {"status": "DELETING"})
    assert (doc in (await w.p.get_ref_access("uid-own", w.org, [doc])).readable) == await details_allows(w, "uid-own", record_id)
    await w.p.update_node(KB, "apps", {"status": "ACTIVE"})
    await w.p.update_node(record_id, "records", {"isDeleted": True})
    trashed = await w.p.get_ref_access("uid-own", w.org, [doc])
    assert doc in trashed.linked and doc not in trashed.readable and not await details_allows(w, "uid-own", record_id)


@pytest.mark.parametrize("kind", ["externalRecordId", "files.path"])
async def test_forged_link_never_grants(w: World, kind: str) -> None:
    """A readable record that a connector write (or a client-reported path) pointed at a victim's document."""
    _record_id, own_doc = await kb_world(w)
    victim_doc = _doc()
    await w.edge("permission", ("users", "own"), ("apps", APP), type="USER", role="READER")
    if kind == "externalRecordId":
        await w.nodes("records", rec("forged", externalRecordId=victim_doc, origin="CONNECTOR", connectorName="WEB"))
        await w.child(("apps", APP), ("records", "forged"))
    else:
        await w.nodes("files", {"_key": "forged", "path": f"storage://{victim_doc}", "name": "forged"})
    got = await w.p.get_ref_access("uid-own", w.org, [victim_doc, own_doc])
    assert victim_doc in got.linked and victim_doc not in got.readable and own_doc in got.readable


async def test_a_flooded_document_cannot_hide_another_documents_link(w: World) -> None:
    _record_id, own_doc = await kb_world(w)
    flooded, victim_doc = _doc(), _doc()
    await w.nodes("records", *[rec(f"flood-{i}", externalRecordId=flooded, origin="CONNECTOR", connectorName="WEB") for i in range(1_100)],
                  rec("lone", externalRecordId=victim_doc, origin="CONNECTOR", connectorName="WEB"))
    got = await w.p.get_ref_access("uid-own", w.org, [flooded, victim_doc, own_doc])
    assert {flooded, victim_doc, own_doc} <= got.linked and got.readable == {own_doc}


async def test_upload_records_without_a_connector_name_cannot_hide_the_kb_upload(w: World) -> None:
    """``origin = upload AND connectorName = kb`` is NULL for a record with no connector name, and a descending order puts NULL first."""
    _record_id, own_doc = await kb_world(w)
    await w.nodes("records", *[rec(f"noname-{i}", externalRecordId=own_doc, origin="UPLOAD", connectorId=f"other-{KB}") for i in range(REF_LINKS_PER_DOC)])
    assert (await w.p.get_ref_access("uid-own", w.org, [own_doc])).readable == {own_doc}


async def test_one_check_access_call_per_push(w: World, monkeypatch: pytest.MonkeyPatch) -> None:
    _, doc = await kb_world(w)
    spy = AsyncMock(wraps=w.p.check_access)
    monkeypatch.setattr(w.p, "check_access", spy)
    await w.p.get_ref_access("uid-own", w.org, [doc, "f" * 24])
    assert spy.await_count == 1


async def test_another_orgs_links_are_never_read_and_its_rows_never_returned(w: World, pg_db) -> None:
    """Org B holds a knowledge-base upload, a connector record and a file naming one document id, and a granted user with the same user id:
    from org A the id is unlinked and unreadable, and no row of org B comes back. B still answers for itself."""
    _record_id, own_doc = await kb_world(w)
    other, doc = _org(), _doc()
    with in_org(other):
        b = World(w.p, other)
        await b.nodes("users", {"_key": "own", "userId": "uid-own"})
        await b.nodes("apps", {"_key": KB, "type": "KB", "name": "B"})
        await b.nodes("records", rec("b-up", connectorId=KB, origin="UPLOAD", connectorName="KB", externalRecordId=doc),
                      rec("b-conn", externalRecordId=own_doc, origin="CONNECTOR", connectorName="WEB"))
        await b.nodes("files", {"_key": "b-file", "path": f"storage://{doc}", "name": "b"})
        await b.child(("apps", KB), ("records", "b-up"))
        await grant(b, "own", "apps", KB, "OWNER")
        try:
            assert (await w.p.get_ref_access("uid-own", other, [doc])).readable == {doc}
            assert {r["recordId"] for r in await w.p._ref_link_rows([doc, own_doc])} == {"b-up", "b-file", "b-conn"}
            with in_org(w.org):
                got = await w.p.get_ref_access("uid-own", w.org, [doc, own_doc])
                rows = await w.p._ref_link_rows([doc, own_doc])
        finally:
            await pg_db.run(lambda tx: tx.execute("DELETE FROM graph.edges WHERE org_id = $1", other), write=True)
            await pg_db.run(lambda tx: tx.execute("DELETE FROM graph.nodes WHERE org_id = $1", other), write=True)
    assert got.linked == {own_doc} and got.readable == {own_doc}
    assert [(r["docId"], r["recordId"]) for r in rows] == [(own_doc, "up")], rows
