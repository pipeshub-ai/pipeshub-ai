"""A stored-record read racing a re-index, against the real storage router and a real graph.

The mapping lives in a real Neo4j or ArangoDB (``NEO4J_IT_URI`` / ``ARANGO_IT_URL``,
defaults as in ``tests/integration/real_graph.py``); storage is the Node API's
``/api/v1/document`` routes (``ARTIFACTS_IT_STORAGE_URL``, ``ARTIFACTS_IT_SCOPED_SECRET``,
``ARTIFACTS_IT_STORAGE_TYPE`` local | s3 | azureBlob, as in
``test_artifact_store_contract.py``). Complements ``test_record_reader_relookup_storage.py``
(storage with an in-memory mapping) and the graph-only file in ``graph_db/``.

Tests that need a signed URL are skipped on local storage, which serves the record
inline. S3 runs only with ``ARTIFACTS_IT_S3_VHOST`` set (see the storage test).
Every vrid and payload is fresh per run.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import logging
import os
import uuid
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import aiohttp
import pytest

from app.config.constants.service import config_node_constants
from app.modules.transformers import blob_storage as bs_mod
from app.modules.transformers.blob_storage import (
    BlobStorage,
    SignedUrlReadError,
    StorageDocumentNotFoundError,
)
from app.services.messaging.error_classifier import (
    MessageErrorClassifier,
    MessageErrorType,
)
from tests.integration.real_graph import (
    backend_unavailable,
    connect_arango,
    connect_neo4j,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

STORAGE_URL = os.environ.get("ARTIFACTS_IT_STORAGE_URL", "")
SCOPED_SECRET = os.environ.get("ARTIFACTS_IT_SCOPED_SECRET", "")
STORAGE_TYPE = os.environ.get("ARTIFACTS_IT_STORAGE_TYPE", "local")
ORG_ID = os.environ.get("ARTIFACTS_IT_ORG_ID", "64d0000000000000000a4f01")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.timeout(600),
    pytest.mark.skipif(not STORAGE_URL, reason="set ARTIFACTS_IT_STORAGE_URL to a running storage service"),
    pytest.mark.skipif(
        STORAGE_TYPE == "s3" and not os.environ.get("ARTIFACTS_IT_S3_VHOST"),
        reason="the S3 adapter reads only <bucket>.s3.<region>.amazonaws.com URLs; set "
        "ARTIFACTS_IT_S3_VHOST=1 for a virtual-hosted setup",
    ),
]

signed_only = pytest.mark.skipif(
    STORAGE_TYPE == "local", reason="not applicable on local: local serves the record inline, with no signed URL"
)
logger = logging.getLogger("relookup-e2e-it")
MAPPING = "virtualRecordToDocIdMapping"
ARANGO_DB = "record_reader_relookup_e2e_it"


def _config_service() -> MagicMock:
    values = {
        config_node_constants.SECRET_KEYS.value: {"scopedJwtSecret": SCOPED_SECRET},
        config_node_constants.ENDPOINTS.value: {"cm": {"endpoint": STORAGE_URL}},
        config_node_constants.STORAGE.value: {"storageType": STORAGE_TYPE},
    }
    service = MagicMock()
    service.get_config = AsyncMock(side_effect=lambda key, *a, **kw: values[key])
    return service


class World:
    def __init__(self, graph: IGraphDBProvider) -> None:
        self.graph = graph
        self.blob = BlobStorage(logger=logger, config_service=_config_service(), graph_provider=graph)
        self.vrids: list[str] = []
        self.lookups = 0
        real = self.blob.get_document_id_by_virtual_record_id

        async def counted(vrid: str) -> dict | None:
            self.lookups += 1
            return await real(vrid)

        self.blob.get_document_id_by_virtual_record_id = counted

    async def store(self, vrid: str, marker: str, payload: str = "") -> tuple[str, int]:
        record = {
            "record_name": marker, "virtual_record_id": vrid,
            "block_containers": {"blocks": [{"index": 0, "type": "text", "data": payload or marker}], "block_groups": []},
        }
        doc, size = await self.blob.save_record_to_storage(
            ORG_ID, f"rec-{vrid}", vrid, record, document_path=f"{ORG_ID}/relookup/{vrid}/{marker}",
        )
        assert doc
        return doc, size

    async def map(self, vrid: str, doc: str, size: int | None) -> None:
        if vrid not in self.vrids:
            self.vrids.append(vrid)
        # As a stored write switches the mapping to a document: one compare-and-set on what was read.
        node = await self.graph.get_document(vrid, MAPPING)
        fields = self.blob._switch_fields(
            node, document_id=doc, file_size_bytes=size, stored_rev=None, run="none:it", replaced=None, now=1,
        )
        assert await self.blob._switch_mapping(ORG_ID, vrid, node, fields)


@pytest.fixture(params=["neo4j", "arango"])
async def world(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[World]:
    async with contextlib.AsyncExitStack() as stack:
        try:
            graph = await (
                connect_neo4j(logger, monkeypatch) if request.param == "neo4j"
                else connect_arango(logger, ARANGO_DB)
            )
        except Exception as exc:
            backend_unavailable(request.param, exc)
        if getattr(graph, "disconnect", None):
            stack.push_async_callback(graph.disconnect)
        w = World(graph)

        async def _cleanup() -> None:
            with contextlib.suppress(Exception):
                await graph.delete_nodes(w.vrids, MAPPING)
            await bs_mod.close_shared_session()

        stack.push_async_callback(_cleanup)
        yield w


def _vrid() -> str:
    return f"relookup-{uuid.uuid4()}"


def _big(n_bytes: int) -> str:
    # incompressible text: base64 of random bytes
    return base64.b64encode(os.urandom(n_bytes * 3 // 4)).decode()


# ---------------------------------------------------------------- graph + storage together
async def test_e2e_lookup_held_across_a_re_index_reads_the_replacement(world: World) -> None:
    vrid = _vrid()
    old = await world.store(vrid, "old")
    await world.map(vrid, *old)
    held = await world.blob.get_document_id_by_virtual_record_id(vrid)
    assert held["record_doc_id"] == old[0]
    new = await world.store(vrid, "new")
    await world.map(vrid, *new)
    await world.blob.purge_document(ORG_ID, old[0])
    world.lookups = 0

    record = await world.blob.get_record_from_storage(vrid, ORG_ID, lookup_result=held)

    assert record["record_name"] == "new"
    assert world.lookups == 1




# ---------------------------------------------------------------- large ranged download
@signed_only
async def test_e2e_large_ranged_download_retired_mid_way(world: World) -> None:
    """> 3 MB, size known: ranged. The old document is purged after its first range."""
    vrid = _vrid()
    old_payload, new_payload = _big(5 * 1024 * 1024), _big(5 * 1024 * 1024)
    old = await world.store(vrid, "old", old_payload)
    new = await world.store(vrid, "new", new_payload)
    assert old[1] > 3 * 1024 * 1024, old
    await world.map(vrid, *old)
    held = await world.blob.get_document_id_by_virtual_record_id(vrid)

    real_chunk = world.blob._download_chunk_with_retry
    ranges: list[tuple[str, int, str]] = []
    purged = asyncio.Event()
    old_url: list[str] = []

    async def chunk(session, url, start, end, idx, max_retries=3) -> tuple[int, bytes]:
        if not old_url:
            old_url.append(url)
        is_old = url == old_url[0]
        if is_old and idx > 0:
            await purged.wait()  # the rest of the old document's ranges start after the retirement
        try:
            out = await real_chunk(session, url, start, end, idx, max_retries=1)
            ranges.append(("old" if is_old else "new", idx, "ok"))
        except Exception:
            ranges.append(("old" if is_old else "new", idx, "fail"))
            raise
        if is_old and idx == 0 and not purged.is_set():
            await world.map(vrid, *new)
            await world.blob.purge_document(ORG_ID, old[0])
            purged.set()
        return out

    world.blob._download_chunk_with_retry = chunk
    world.lookups = 0
    record = await world.blob.get_record_from_storage(vrid, ORG_ID, lookup_result=held)

    assert record["record_name"] == "new"
    assert record["block_containers"]["blocks"][0]["data"] == new_payload
    assert ("old", 0, "ok") in ranges, ranges
    assert any(r[0] == "old" and r[2] == "fail" for r in ranges), "a range of the old document must have failed mid-way"
    assert world.lookups == 1


@signed_only
async def test_e2e_large_ranged_transient_range_fault_falls_back_without_lookup(world: World) -> None:
    vrid = _vrid()
    payload = _big(5 * 1024 * 1024)
    doc = await world.store(vrid, "only", payload)
    await world.map(vrid, *doc)
    held = await world.blob.get_document_id_by_virtual_record_id(vrid)
    real_chunk = world.blob._download_chunk_with_retry

    async def chunk(session, url, start, end, idx, max_retries=3) -> tuple[int, bytes]:
        if idx == 1:
            raise aiohttp.ClientError("injected range fault")
        return await real_chunk(session, url, start, end, idx, max_retries=max_retries)

    world.blob._download_chunk_with_retry = chunk
    world.lookups = 0
    record = await world.blob.get_record_from_storage(vrid, ORG_ID, lookup_result=held)
    assert record["block_containers"]["blocks"][0]["data"] == payload
    assert world.lookups == 0


# ---------------------------------------------------------------- 403 attacks on the real store
def _tamper(url: str) -> str:
    """A real 403 from the store with the document intact: break the signature."""
    for key in ("X-Amz-Signature=", "sig="):
        if key in url:
            head, tail = url.split(key, 1)
            rest = tail.split("&", 1)
            sig = rest[0]
            bad = ("A" if sig[:1] != "A" else "B") + sig[1:]
            return head + key + bad + ("&" + rest[1] if len(rest) > 1 else "")
    raise AssertionError(f"no signature in {url[:80]}")


def _forbid_signed_urls(world: World) -> None:
    real = world.blob._fetch_record_envelope

    async def fetch(*args: object, **kwargs: object) -> dict:
        envelope = await real(*args, **kwargs)
        if envelope.get("signedUrl"):
            envelope = {**envelope, "signedUrl": _tamper(envelope["signedUrl"])}
        return envelope

    world.blob._fetch_record_envelope = fetch


@signed_only
@pytest.mark.parametrize("size_known", [True, False], ids=["single", "ranged"])
async def test_attack_real_403_same_document_is_not_gone_and_not_terminal(world: World, size_known: bool) -> None:
    vrid = _vrid()
    doc = await world.store(vrid, "only")
    await world.map(vrid, doc[0], doc[1] if size_known else None)
    _forbid_signed_urls(world)
    world.lookups = 0

    with pytest.raises(SignedUrlReadError) as raised:
        await world.blob.get_record_from_storage(vrid, ORG_ID)
    exc = raised.value
    kind = MessageErrorClassifier.classify_by_exception(exc)
    assert exc.http_status == 403, f"{STORAGE_TYPE} answered {exc.http_status}"
    assert world.lookups == 2, "the read's own lookup and exactly one re-lookup"
    assert kind == MessageErrorType.TRANSIENT, f"{STORAGE_TYPE} 403 classified {kind}"


@signed_only
async def test_attack_named_entity_retry_is_not_gone_on_a_real_403(world: World) -> None:
    from app.modules.indexing import named_entity_persist_retry as nep

    vrid = _vrid()
    doc = await world.store(vrid, "only")
    await world.map(vrid, *doc)
    _forbid_signed_urls(world)
    graph = MagicMock()
    graph.get_document = AsyncMock(return_value={"_key": "r1", "id": "r1", "virtualRecordId": vrid, "orgId": ORG_ID})
    sink = MagicMock()
    sink.blob_storage = world.blob
    sink.reproject_named_entities = AsyncMock(return_value="written")
    with pytest.raises(SignedUrlReadError) as raised:
        await nep._retry_one(graph, sink, "r1", ORG_ID, due=0)
    kind = MessageErrorClassifier.classify_by_exception(raised.value)
    assert kind == MessageErrorType.TRANSIENT, f"{STORAGE_TYPE} 403 classified {kind}"


# ---------------------------------------------------------------- heal on real graph + storage
async def test_e2e_heal_does_not_reindex_a_replaced_document(world: World) -> None:
    from app.connectors.core.base.data_processor.storage_cleanup import (
        StorageCleanupHelper,
    )
    from app.connectors.services.stored_content_heal import StoredContentHeal

    vrid_replaced, vrid_lost = _vrid(), _vrid()
    old = await world.store(vrid_replaced, "old")
    await world.map(vrid_replaced, *old)
    lost = await world.store(vrid_lost, "lost")
    await world.map(vrid_lost, *lost)
    await world.blob.purge_document(ORG_ID, lost[0])

    storage = StorageCleanupHelper(logger, world.graph, _config_service())
    reindexed: list[str] = []

    async def reindex(vrid, publish) -> MagicMock:
        reindexed.append(vrid)
        return MagicMock(published=True, record_key=f"k-{vrid}", org_id=ORG_ID, under_way=False)

    storage.reindex_one_holder = reindex
    real_lookups = world.blob.get_document_ids_by_virtual_record_ids
    first = True

    async def lookups(vrids, **kw: object) -> dict:
        nonlocal first
        out = await real_lookups(vrids, **kw)
        if first:
            first = False  # a re-index lands right after the page's mapping read
            new = await world.store(vrid_replaced, "new")
            await world.map(vrid_replaced, *new)
            await world.blob.purge_document(ORG_ID, old[0])
        return out

    world.blob.get_document_ids_by_virtual_record_ids = lookups
    lock = MagicMock()
    lock.refresh = AsyncMock(return_value=True)
    heal = StoredContentHeal(
        logger=logger, graph_provider=world.graph, blob_store=world.blob, storage=storage,
        publish=AsyncMock(), lock=lock,
    )
    rows = [
        {"_key": f"r-{v}", "id": f"r-{v}", "virtualRecordId": v, "orgId": ORG_ID, "indexingStatus": "COMPLETED"}
        for v in (vrid_replaced, vrid_lost)
    ]
    try:
        await heal.process_page({"orgId": ORG_ID, "_key": "app"}, "app", rows)
    finally:
        await storage.close()
    assert reindexed == [vrid_lost]


# ---------------------------------------------------------------- performance: happy path
@pytest.mark.parametrize("pass_lookup", [True, False], ids=["lookup-passed", "lookup-inside"])
async def test_perf_happy_path_no_extra_lookup_or_request(world: World, pass_lookup: bool) -> None:
    vrid = _vrid()
    doc = await world.store(vrid, "only")
    await world.map(vrid, *doc)
    held = await world.blob.get_document_id_by_virtual_record_id(vrid)
    fetches = 0
    real = world.blob._fetch_record_envelope

    async def fetch(*a: object, **kw: object) -> dict:
        nonlocal fetches
        fetches += 1
        return await real(*a, **kw)

    world.blob._fetch_record_envelope = fetch
    for _ in range(3):  # warm
        await world.blob.get_record_from_storage(vrid, ORG_ID, lookup_result=held if pass_lookup else None)
    world.lookups, fetches = 0, 0
    n = 60
    for _ in range(n):
        r = await world.blob.get_record_from_storage(vrid, ORG_ID, lookup_result=held if pass_lookup else None)
        assert r["record_name"] == "only"
    assert world.lookups == (0 if pass_lookup else n)
    assert fetches == n


# ---------------------------------------------------------------- another org
async def test_bypass_relookup_to_another_orgs_document_is_not_served(world: World) -> None:
    """The re-lookup is by vrid only; the gateway read is org-scoped. A mapping that
    now names another org's document must not be served to this org."""
    other_org = "64d0000000000000000b5f02"
    vrid = _vrid()
    old = await world.store(vrid, "old")
    await world.map(vrid, *old)
    held = await world.blob.get_document_id_by_virtual_record_id(vrid)
    record = {"record_name": "other-org-secret", "virtual_record_id": vrid,
              "block_containers": {"blocks": [], "block_groups": []}}
    foreign, size = await world.blob.save_record_to_storage(
        other_org, f"rec-{vrid}", vrid, record, document_path=f"{other_org}/relookup/{vrid}/x",
    )
    await world.map(vrid, foreign, size)
    await world.blob.purge_document(ORG_ID, old[0])
    # The reader re-raises the first 404 when the re-lookup fails or finds no other document, so the
    # raise alone does not show the foreign document was reached: record what the re-lookup returned.
    relooked: list[str | None] = []
    real_relookup = world.blob._relookup_after_failed_read

    async def spy(*args: object, **kwargs: object) -> dict | None:
        fresh = await real_relookup(*args, **kwargs)
        relooked.append((fresh or {}).get("record_doc_id"))
        return fresh

    world.blob._relookup_after_failed_read = spy
    # The first read finds the purged document and the re-lookup names the other org's one; the
    # org-scoped gateway answers 404 for it, which the reader raises. Anything else (a transport
    # error, a failed auth or graph lookup) is not this test's outcome and must fail it.
    with pytest.raises(StorageDocumentNotFoundError):
        served = await world.blob.get_record_from_storage(vrid, ORG_ID, lookup_result=held)
        assert served.get("record_name") != "other-org-secret", "over-admit across orgs"
    assert relooked == [foreign], "the re-lookup did not name the other org's document"


# ---------------------------------------------------------------- concurrency
async def test_concurrent_readers_of_a_retired_document_each_take_one_lookup(world: World) -> None:
    vrid = _vrid()
    old = await world.store(vrid, "old")
    await world.map(vrid, *old)
    held = await world.blob.get_document_id_by_virtual_record_id(vrid)
    await world.map(vrid, *await world.store(vrid, "new"))
    await world.blob.purge_document(ORG_ID, old[0])
    world.lookups = 0
    n = 24
    out = await asyncio.gather(*[
        world.blob.get_record_from_storage(vrid, ORG_ID, lookup_result=held) for _ in range(n)
    ])
    assert [r["record_name"] for r in out] == ["new"] * n
    assert world.lookups == n
