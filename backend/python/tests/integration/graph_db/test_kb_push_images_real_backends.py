"""Who may inline a stored document, against real Neo4j and ArangoDB (the push service, processor, graph provider and KB service are real).

``get_ref_access`` must grant exactly what ``check_record_access_with_details`` grants for the record that links the document
(the one-record check is the product's reference), grants only from links the platform wrote itself, and the push re-checks access
just before it claims.

Environment: as ``test_kb_push_real_backends.py``. These cells need only the graph; the real-storage cells are in
``tests/integration/test_kb_push_storage_e2e.py``.
"""

from __future__ import annotations

import asyncio
import base64
import json
import uuid
from unittest.mock import AsyncMock

import pytest

from app.config.constants.arangodb import CollectionNames
from app.connectors.sources.localKB.push.contract import PushRefusal
from app.connectors.sources.localKB.push.image_admission import ImageAdmission
from app.services.graph_db.interface.graph_db_provider import REF_LINKS_PER_DOC
from tests.integration.graph_db import test_kb_push_real_backends as base
from tests.integration.graph_db.test_kb_push_real_backends import Env, record_keys

pytestmark = [pytest.mark.integration, pytest.mark.timeout(600)]

# The two-engine fixture of the push suite, registered here under its own name.
env = base.env

PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")


async def add_user(env: Env, role: str | None, *, org_id: str | None = None) -> str:
    user_id = f"u-{uuid.uuid4().hex[:8]}"
    key = f"ukey-{user_id}"
    env.seeded[CollectionNames.USERS.value].append(key)
    assert await env.graph.batch_upsert_nodes(
        [{"_key": key, "userId": user_id, "orgId": org_id or env.org_id, "email": f"{user_id}@example.com", "isActive": True}], CollectionNames.USERS.value)
    if role:
        granted = await env.kb_service.create_kb_permissions(env.kb_id, env.user_id, user_ids=[user_id], team_ids=[], role=role)
        assert granted.get("success") is not False, granted
    return user_id


async def pushed_document(env: Env, ext: str = "rec-1") -> tuple[str, str]:
    """A knowledge-base record the platform linked to a storage document: ``(record id, document id)``."""
    record_keys(env, ext)
    await env.push(env.body(ext=ext))
    rec = await env.record(ext)
    return rec["id"], rec["externalRecordId"]


async def details_allows(env: Env, user_id: str, record_id: str) -> bool:
    return await env.graph.check_record_access_with_details(user_id, env.org_id, record_id) is not None


@pytest.mark.parametrize("role", ["OWNER", "WRITER", "READER", None])
async def test_ref_access_equivalence(env: Env, role) -> None:
    record_id, doc = await pushed_document(env)
    user = env.user_id if role == "OWNER" else await add_user(env, role)
    got = await env.graph.get_ref_access(user, env.org_id, [doc])
    assert doc in got.linked
    assert (doc in got.readable) == await details_allows(env, user, record_id), role
    assert (doc in got.readable) == (role is not None)


async def test_ref_access_equivalence_trashed_record_other_org_and_deleting_kb(env: Env) -> None:
    record_id, doc = await pushed_document(env)
    outsider = await add_user(env, None, org_id=f"other-org-{uuid.uuid4().hex[:6]}")
    got = await env.graph.get_ref_access(outsider, env.org_id, [doc])
    assert doc not in got.readable and not await details_allows(env, outsider, record_id)
    # A KB in DELETING status is admitted by both paths (neither reads the status): this cell fails if either changes.
    await env.graph.update_node(env.kb_id, CollectionNames.APPS.value, {"status": "DELETING"})
    assert (doc in (await env.graph.get_ref_access(env.user_id, env.org_id, [doc])).readable) == await details_allows(env, env.user_id, record_id)
    await env.graph.update_node(env.kb_id, CollectionNames.APPS.value, {"status": "ACTIVE"})
    await env.graph.update_node(record_id, CollectionNames.RECORDS.value, {"isDeleted": True})
    trashed = await env.graph.get_ref_access(env.user_id, env.org_id, [doc])
    assert doc in trashed.linked and doc not in trashed.readable and not await details_allows(env, env.user_id, record_id)


@pytest.mark.parametrize("kind", ["externalRecordId", "files.path"])
async def test_forged_link_never_grants(env: Env, kind) -> None:
    """A readable record of the caller's KB that a connector write (or a client-reported path) pointed at a victim's document."""
    victim_doc = uuid.uuid4().hex[:24]
    _record_id, own_doc = await pushed_document(env)
    if kind == "externalRecordId":
        # a connector-origin record naming the victim's document
        rid = f"forged-{uuid.uuid4().hex[:8]}"
        env.seeded[CollectionNames.RECORDS.value].append(rid)
        await env.graph.batch_upsert_nodes([{"id": rid, "orgId": env.org_id, "externalRecordId": victim_doc, "origin": "CONNECTOR", "connectorName": "WEB",
                                             "connectorId": "c1", "recordName": "forged", "recordType": "FILE", "createdAtTimestamp": 1}], CollectionNames.RECORDS.value)
    else:
        rid = f"forged-{uuid.uuid4().hex[:8]}"
        env.seeded[CollectionNames.FILES.value].append(rid)
        await env.graph.batch_upsert_nodes([{"id": rid, "orgId": env.org_id, "path": f"storage://{victim_doc}", "name": "forged"}], CollectionNames.FILES.value)
    got = await env.graph.get_ref_access(env.user_id, env.org_id, [victim_doc, own_doc])
    assert victim_doc in got.linked and victim_doc not in got.readable and own_doc in got.readable


async def test_a_flooded_document_cannot_hide_another_documents_link(env: Env) -> None:
    """More linking records for one document than any call-wide row limit must not drop the only link of another."""
    flooded, victim_doc = uuid.uuid4().hex[:24], uuid.uuid4().hex[:24]
    _record_id, own_doc = await pushed_document(env)
    rows = [{"id": f"flood-{i}-{uuid.uuid4().hex[:6]}", "orgId": env.org_id, "externalRecordId": flooded, "origin": "CONNECTOR", "connectorName": "WEB",
             "connectorId": "c1", "recordName": "flood", "recordType": "FILE", "createdAtTimestamp": 1} for i in range(1_100)]
    rows.append({"id": f"lone-{uuid.uuid4().hex[:6]}", "orgId": env.org_id, "externalRecordId": victim_doc, "origin": "CONNECTOR", "connectorName": "WEB",
                 "connectorId": "c1", "recordName": "lone", "recordType": "FILE", "createdAtTimestamp": 1})
    env.seeded[CollectionNames.RECORDS.value].extend(r["id"] for r in rows)
    assert await env.graph.batch_upsert_nodes(rows, CollectionNames.RECORDS.value)
    got = await env.graph.get_ref_access(env.user_id, env.org_id, [flooded, victim_doc, own_doc])
    assert {flooded, victim_doc, own_doc} <= got.linked and got.readable == {own_doc}


async def test_upload_records_without_a_connector_name_cannot_hide_the_kb_upload(env: Env) -> None:
    """Neo4j sorts null first in a descending order, and ``origin = upload AND connectorName = kb``
    is null, not false, for a record with no connector name: a full page of those must not push
    the document's own knowledge-base upload out of its per-document rows."""
    if not type(env.graph).__name__.startswith("Neo4j"):
        pytest.skip("ArangoDB's record schema requires connectorName, and its == on a missing field is false")
    _record_id, own_doc = await pushed_document(env)
    rows = [{"id": f"noname-{i}-{uuid.uuid4().hex[:6]}", "orgId": env.org_id, "externalRecordId": own_doc, "origin": "UPLOAD",
             "recordName": "noname", "recordType": "FILE", "createdAtTimestamp": 1} for i in range(REF_LINKS_PER_DOC)]
    env.seeded[CollectionNames.RECORDS.value].extend(r["id"] for r in rows)
    assert await env.graph.batch_upsert_nodes(rows, CollectionNames.RECORDS.value)
    got = await env.graph.get_ref_access(env.user_id, env.org_id, [own_doc])
    assert got.readable == {own_doc}


async def test_one_check_access_call_per_push(env: Env) -> None:
    _, doc = await pushed_document(env)
    spy = AsyncMock(wraps=env.graph.check_access)
    env.graph.check_access = spy
    await env.graph.get_ref_access(env.user_id, env.org_id, [doc, "f" * 24])
    assert spy.await_count == 1


def _neo4j_operators(plan: dict) -> list[str]:
    ops = [plan["operatorType"].split("@")[0]]
    for child in plan.get("children", []):
        ops.extend(_neo4j_operators(child))
    return ops


async def _sent_link_queries(env: Env, monkeypatch: pytest.MonkeyPatch, ids: list[str]) -> list[tuple[str, dict]]:
    """The exact statements ``_ref_link_rows`` sends (one for records, one for files)."""
    sent: list[tuple[str, dict]] = []
    neo4j = type(env.graph).__name__.startswith("Neo4j")
    target = env.graph.client if neo4j else env.graph
    original = target.execute_query

    async def record(query: str, *args: object, **kwargs: object) -> object:
        sent.append((query, dict(kwargs.get("parameters" if neo4j else "bind_vars") or {})))
        return await original(query, *args, **kwargs)

    monkeypatch.setattr(target, "execute_query", record)
    await env.graph._ref_link_rows(ids)
    monkeypatch.undo()
    assert len(sent) == 2, sent
    return sent


def _is_neo4j(env: Env) -> bool:
    return type(env.graph).__name__.startswith("Neo4j")


async def _ensure_twice_neo4j(env: Env) -> None:
    statement = next(s for s in env.graph._generate_performance_indexes() if "record_external_record_id" in s)
    for _ in range(2):
        await env.graph._apply_schema([statement], "performance indexes")
    rows = await env.graph.client.execute_query(
        "SHOW INDEXES YIELD name, properties WHERE name = 'record_external_record_id' RETURN name, properties")
    assert [(r["name"], r["properties"]) for r in rows] == [("record_external_record_id", ["externalRecordId"])]


async def _ensure_arango(env: Env) -> None:
    """No index is added on Arango: the existing persistent index on (externalRecordId, connectorId) serves a leading-field lookup."""
    client = env.graph.http_client
    http = await client._get_session()
    url = f"{client.base_url}/_db/{client.database}/_api/index?collection={CollectionNames.RECORDS.value}"
    async with http.get(url) as resp:
        fields = [ix["fields"] for ix in (await resp.json())["indexes"]]
    assert ["externalRecordId", "connectorId"] in fields and ["externalRecordId"] not in fields, fields


async def test_index_ensure_twice_is_a_noop(env: Env) -> None:
    await {True: _ensure_twice_neo4j, False: _ensure_arango}[_is_neo4j(env)](env)


async def _seeks_neo4j(env: Env, sent: list[tuple[str, dict]]) -> None:
    await _ensure_twice_neo4j(env)
    for _ in range(60):  # a new index populates in the background; the planner ignores it until ONLINE
        states = await env.graph.client.execute_query(
            "SHOW INDEXES YIELD name, state WHERE name IN ['record_external_record_id', 'file_path'] RETURN state")
        if states and all(r["state"] == "ONLINE" for r in states):
            break
        await asyncio.sleep(1)
    for query, params in sent:
        async with env.graph.client.driver.session(database=env.graph.client.database) as session:
            result = await session.run(f"EXPLAIN {query}", params)
            operators = _neo4j_operators((await result.consume()).plan)
        assert any(o in ("NodeIndexSeek", "NodeUniqueIndexSeek") for o in operators), operators
        assert "NodeByLabelScan" not in operators and "AllNodesScan" not in operators, operators


async def _seeks_arango(env: Env, sent: list[tuple[str, dict]]) -> None:
    await _ensure_arango(env)
    client = env.graph.http_client
    http = await client._get_session()
    for query, params in sent:
        async with http.post(f"{client.base_url}/_db/{client.database}/_api/explain", json={"query": query, "bindVars": params}) as resp:
            assert resp.status == 200, await resp.text()
            nodes = (await resp.json())["plan"]["nodes"]
        kinds = [n["type"] for n in nodes]
        assert "EnumerateCollectionNode" not in kinds, kinds
        assert any(n["type"] == "IndexNode" for n in nodes), kinds


async def test_link_query_uses_index_seeks(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    """Plan assertion over a seeded graph: each branch of the link query is an index seek, not a label or collection scan."""
    ids = [uuid.uuid4().hex[:24] for _ in range(3)]
    rows = [{"id": f"seed-{env.org_id}-{i}", "orgId": env.org_id, "externalRecordId": f"seed{i}", "connectorId": "seed-c", "recordName": f"seed{i}",
             "recordType": "FILE", "origin": "CONNECTOR", "connectorName": "WEB", "createdAtTimestamp": 1} for i in range(20000)]
    env.seeded[CollectionNames.RECORDS.value] += [r["id"] for r in rows]
    for start in range(0, len(rows), 1000):  # small batches: one 20k batch disconnected the shared test server
        assert await env.graph.batch_upsert_nodes(rows[start:start + 1000], CollectionNames.RECORDS.value)
    sent = await _sent_link_queries(env, monkeypatch, ids)
    await {True: _seeks_neo4j, False: _seeks_arango}[_is_neo4j(env)](env, sent)


class SplicingMaterialiser:
    """Splices a PNG for every sentinel (the child is covered by its own tests) and runs ``during`` while the images are 'fetched'."""

    def __init__(self, during=None) -> None:
        self.during, self.runs = during, 0

    def make_dir(self, spool_dir: str) -> str:
        import os
        path = os.path.join(spool_dir, f"img-{uuid.uuid4().hex[:8]}")
        os.makedirs(path)
        return path

    def remove_dir(self, path: str | None) -> None:
        import shutil
        shutil.rmtree(path or "", ignore_errors=True)

    async def run(self, task: dict) -> object:
        import hashlib
        import re

        from app.connectors.sources.localKB.push.materialiser import Materialised
        self.runs += 1
        if self.during:
            await self.during()
        data = open(task["sentinel_path"], "rb").read()
        data = re.sub(rb'"\\u0000pb-img:[0-9a-f]{32}:\d+"', b'"data:image/png;base64,' + base64.b64encode(PNG) + b'"', data)
        open(task["out_path"], "wb").write(data)
        return Materialised(len(data), hashlib.sha256(data).hexdigest()[:16])


def image_capable(env: Env, doc_id: str, mat) -> None:
    """Give the env's service image support over the env's fake storage: one verified, trusted image document ``doc_id``."""
    from app.services.storage_documents import parse_document_info

    storage = env.storage

    async def info_many(org_id, ids, *, verify=False, concurrency=8) -> object:
        body = {"document": {"_id": doc_id, "isDeleted": False, "sizeInBytes": len(PNG), "storageVendor": "local", "mimeType": "image/png", "initiatorUserId": "someone-else"},
                "storageFingerprint": {"version": 1, "value": "v" * 32, "settled": True, "verified": True, "object": {"size": len(PNG), "etag": "e" * 32},
                                       "objectUnverifiable": False, "trusted": True}}
        return {i: parse_document_info(body, verify=verify) if i == doc_id else None for i in ids}

    storage.info_many = info_many
    storage.upload_limit = AsyncMock(return_value=30 << 20)
    storage.mint_token = AsyncMock(return_value="t")
    env.service.materialiser, env.service.image_admission = mat, ImageAdmission()


def image_push_body(env: Env, doc_id: str, ext: str = "img-1") -> bytes:
    blocks = [{"index": 0, "type": "text", "format": "txt", "data": "t"},
              {"index": 1, "type": "image", "format": "base64", "data": {"uri": f"storage://{doc_id}"}}]
    return json.dumps(
        {"recordName": "Img", "mimeType": "application/blocks", "externalRecordId": ext, "version": 1, "blocksFormat": 1, "blocks": {"blocks": blocks}}).encode()


async def test_a_readable_linked_document_is_inlined_on_real_graph(env: Env) -> None:
    _, doc = await pushed_document(env)
    image_capable(env, doc, SplicingMaterialiser())
    record_keys(env, "img-1")
    r = await env.push(image_push_body(env, doc))
    assert r.status == "created"
    stored = env.storage.docs[(await env.record("img-1"))["externalRecordId"]]
    assert b"data:image/png;base64," in stored and b"storage://" not in stored


async def test_access_revoked_during_materialisation_is_uniform_422_and_cleaned(env: Env) -> None:
    """The linking record is trashed while the images are fetched: the re-check before the claim denies, the stored copy is cleaned and no record exists."""
    record_id, doc = await pushed_document(env)

    async def revoke() -> None:
        await env.graph.update_node(record_id, CollectionNames.RECORDS.value, {"isDeleted": True})

    image_capable(env, doc, SplicingMaterialiser(during=revoke))
    record_keys(env, "img-1")
    before = set(env.storage.docs)
    with pytest.raises(PushRefusal) as e:
        await env.push(image_push_body(env, doc))
    assert (e.value.status, e.value.reason) == (422, "IMAGE_REF_INVALID")
    assert await env.record("img-1") is None
    assert (set(env.storage.docs) - before) <= env.cleaned()
