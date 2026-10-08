"""Labs "Delete all embeddings" and "Recreate all embeddings", and changing the embedding model.

The test list asks for four things here:

  * Delete all embeddings via Labs: every point goes, while the records, their
    stored files and their MongoDB documents stay, and each record reads as
    waiting to be indexed again.
  * Recreate all embeddings from blob storage via Labs, with the old model and
    with a new one: the points come back, at the right size, and search finds
    the documents again.
  * An embedding model change with nothing indexed: allowed, and the new model
    is the one used from then on.
  * An embedding model change with records indexed: the product refuses it with
    a message that says what to do.

Deleting all embeddings drops the vector collection for the whole deployment,
not one org or knowledge base, and changing the model rebuilds it at another
size. Anything else indexing on the stack at the same time would lose its
vectors or be refused. So this module carries the resilience marker and runs
alone, after everything else, like the tests that restart services. It runs as
one ordered journey, and the last step puts the org back as it found it: the
original default embedding model, the Labs flag, and every record re-embedded
from blob storage with the original model. A module finalizer does the same if
the journey stops early.

The second model is the product's own "Default (System Provided)" model,
BAAI/bge-large-en-v1.5, which the image ships so the local embedding server
serves it with no download. Its vectors are 1024 wide; the suite's cloud model's
are 1536. No smaller local model is baked into the image.

    RESILIENCE_VECTOR_REBUILD_TIMEOUT_SEC  how long one job or re-embedding may take (default 1800)
"""

from __future__ import annotations

import asyncio
import logging
import os
import uuid
from dataclasses import dataclass, field
from typing import Any

import pytest
import pytest_asyncio

from helper.clients.kb_client import KBClient
from helper.indexing_progress import record_fields, wait_until_enriched, wait_until_finished
from helper.mongo_store import records_folder
from helper.vector_rebuild import (
    DELETE_REFUSED_PHRASE,
    LOCAL_MODEL_DIMENSION,
    LOCAL_MODEL_NAME,
    MODEL_CHANGE_REFUSED,
    VECTOR_STORE_REBUILD_FLAG,
    EmbeddingModel,
    PlatformSettings,
    add_local_embedding_model,
    agents_using_model,
    default_model,
    delete_embedding_model,
    error_message,
    list_embedding_models,
    model_key_from_add,
    post_vector_store_job,
    read_platform_settings,
    rebuild_lock_held,
    records_in_flight,
    set_default_embedding_model,
    set_rebuild_flag,
    start_vector_store_job,
    write_platform_settings,
)
from retrieval.ranking import virtual_id_of

logger = logging.getLogger("vector-store-rebuild")

pytestmark = [pytest.mark.resilience, pytest.mark.asyncio(loop_scope="session")]

TIMEOUT = int(os.getenv("RESILIENCE_VECTOR_REBUILD_TIMEOUT_SEC", "1800"))
# The product gives the drop itself 600s (VECTOR_STORE_REBUILD_DELETE_WAIT_SECONDS)
# before it marks the cleanup failed.
DROP_TIMEOUT = 660
POLL = 5
COLLECTION = "records"
# Search answers 404 with this message, not an empty 200, when the knowledge base
# holds no indexed record, as it does right after "Delete all embeddings".
NOTHING_INDEXED_MESSAGE = "No documents are available for you to search yet"


class CollectionSizeMismatch(AssertionError):
    """The vector collection is not the size the default embedding model writes."""


@dataclass
class Document:
    name: str
    token: str
    record_id: str
    virtual_record_id: str
    storage_prefix: str
    storage_vendor: str
    content_chunks: int = 0


@dataclass
class Journey:
    """What the org looked like before, and what each step has done so far."""

    kb_id: str
    org_id: str
    settings_before: PlatformSettings
    models_before: list[EmbeddingModel]
    default_before: EmbeddingModel
    size_before: int
    documents: list[Document] = field(default_factory=list)
    blob_counts: dict[str, int] = field(default_factory=dict)
    mongo_counts: dict[str, int] = field(default_factory=dict)
    local_model_key: str | None = None
    local_model_unavailable: str | None = None
    done: set[str] = field(default_factory=set)
    restored: bool = False

    def require(self, *steps: str) -> None:
        missing = [s for s in steps if s not in self.done]
        if missing:
            pytest.skip(f"an earlier step did not finish: {', '.join(missing)}")

    def require_local_model(self) -> None:
        if self.local_model_unavailable:
            pytest.skip(self.local_model_unavailable)


def _document_body(title: str, token: str) -> bytes:
    return (
        f"# {title}\n\n"
        f"Reference {token} identifies this note and no other.\n\n"
        + "\n\n".join(
            f"## Part {n}\n\nOperating detail {n} for {title.lower()}. " + "Routine upkeep. " * 10
            for n in range(1, 6)
        )
        + "\n"
    ).encode()


def _status(kb_client: KBClient, record_id: str) -> str:
    return record_fields(kb_client.get_record(record_id)).get("indexingStatus") or "UNKNOWN"


async def _wait_for(predicate, what: str, timeout: float = TIMEOUT) -> Any:
    deadline = asyncio.get_event_loop().time() + timeout
    while True:
        value = await predicate()
        if value:
            return value
        if asyncio.get_event_loop().time() >= deadline:
            raise AssertionError(f"Timed out after {timeout:.0f}s waiting for {what}.")
        await asyncio.sleep(POLL)


async def _upload(
    kb_client: KBClient, vector_store, mongo_store, org_id: str, kb_id: str, title: str
) -> Document:
    token = f"quillon{uuid.uuid4().hex[:8]}"
    name = f"{title.lower().replace(' ', '-')}-{token}.md"
    upload = kb_client.upload_file(kb_id, name, _document_body(title, token), mimetype="text/markdown")
    assert upload["summary"]["failed"] == 0, f"Upload of {name} failed: {upload}"
    record_id = upload["records"][0]["recordId"]

    final = await wait_until_finished(kb_client, [record_id], timeout=TIMEOUT)
    assert final.get(record_id) == "COMPLETED", (
        f"{name} did not finish indexing (status {final.get(record_id)}), so there is "
        "nothing for the rebuild to act on."
    )
    virtual_id = str(record_fields(kb_client.get_record(record_id)).get("virtualRecordId") or "")
    assert virtual_id, f"{name} finished indexing without a virtualRecordId."
    # The record reads COMPLETED before enrichment rewrites its summary vector, so a
    # point count taken earlier is not the one the later steps compare against.
    await wait_until_enriched(kb_client, record_id, timeout=TIMEOUT)
    await _wait_for(
        lambda: vector_store.count_for_virtual_record(virtual_id), f"embeddings for {name}"
    )
    # Indexing files processed content under the record's place in its collection, not its
    # virtual record id; the flat folder would count nothing before and after alike.
    prefix = await mongo_store.envelope_path(org_id, virtual_id, within=records_folder(org_id, kb_id))
    vendor = await mongo_store.storage_vendor_under_path(prefix) or "local"
    return Document(
        name=name,
        token=token,
        record_id=record_id,
        virtual_record_id=virtual_id,
        storage_prefix=prefix,
        storage_vendor=vendor,
        content_chunks=await vector_store.count_content_chunks(virtual_id),
    )


def _hits(search_client, kb_id: str, query: str, *, nothing_indexed_ok: bool = False) -> set[str]:
    resp = search_client.search(query, limit=10, filters={"kb": [kb_id]})
    if nothing_indexed_ok and resp.status_code == 404 and NOTHING_INDEXED_MESSAGE in error_message(resp):
        return set()
    assert resp.status_code == 200, f"Search failed with HTTP {resp.status_code}: {resp.text[:400]}"
    results = (resp.json().get("searchResponse") or {}).get("searchResults") or []
    return {vid for vid in (virtual_id_of(hit) for hit in results) if vid}


def _assert_found(search_client, journey: Journey, documents: list[Document]) -> None:
    missing = {
        doc.name: sorted(hits)
        for doc in documents
        if doc.virtual_record_id not in (hits := _hits(search_client, journey.kb_id, doc.token))
    }
    assert not missing, (
        "Search for each document's unique reference did not return it "
        f"(document -> virtual ids that came back instead): {missing}"
    )


def _start(pipeshub_client, operation: str) -> None:
    set_rebuild_flag(pipeshub_client, True)
    resp = start_vector_store_job(pipeshub_client, operation, timeout=TIMEOUT)
    assert resp.status_code == 202, (
        f"The Labs {operation} job was not accepted: HTTP {resp.status_code} "
        f"{error_message(resp)}"
    )
    body = resp.json()
    assert body.get("accepted") is True and body.get("operation") == operation, body


async def _delete_all_embeddings(pipeshub_client, vector_store, org_id: str) -> None:
    _start(pipeshub_client, "cleanup")

    # The cleanup drops the records collection only; the entity index is a projection
    # of the graph that its own rebuild re-embeds for the current model (#3858).
    async def emptied() -> bool:
        return await vector_store.count_for_org(org_id, collection=COLLECTION) == 0

    await _wait_for(
        emptied, "the org's vector points to reach 0 after the Labs cleanup", DROP_TIMEOUT
    )
    await _wait_for(
        lambda: vector_store.dense_size(COLLECTION),
        f"the {COLLECTION!r} collection to be recreated after the Labs cleanup",
        DROP_TIMEOUT,
    )


async def _recreate_all_embeddings(
    pipeshub_client, kb_client: KBClient, vector_store, documents: list[Document]
) -> dict[str, str]:
    _start(pipeshub_client, "reindex")
    ids = [doc.record_id for doc in documents]
    final = await wait_until_finished(kb_client, ids, timeout=TIMEOUT)
    for doc in documents:
        if final.get(doc.record_id) == "COMPLETED":
            await _wait_for(
                lambda d=doc: vector_store.count_for_virtual_record(d.virtual_record_id),
                f"embeddings for {doc.name} after the Labs reindex",
            )
    return final


async def _wait_until_deployment_idle(graph_provider) -> None:
    """Block until no Labs job holds the lock and no record anywhere is queued or indexing.

    The Labs reindex re-embeds every record in the org, not only this module's,
    and the suites that run next on this stack (storage repoints blob storage)
    must not start while it is still reading from blob storage.
    """
    deadline = asyncio.get_event_loop().time() + TIMEOUT
    while True:
        locked = await rebuild_lock_held()
        busy = await records_in_flight(graph_provider)
        if not locked and not busy:
            return
        if asyncio.get_event_loop().time() >= deadline:
            shown = [
                {k: r.get(k) for k in ("_key", "recordName", "connectorId", "indexingStatus")}
                for r in busy
            ]
            raise AssertionError(
                f"The deployment was still busy {TIMEOUT}s after the Labs reindex began "
                f"(rebuild lock held: {locked}; records still queued or indexing, first "
                f"{len(shown)}: {shown}). The next suites on this stack cannot start safely."
            )
        await asyncio.sleep(POLL)


async def _restore(journey: Journey, pipeshub_client, kb_client, vector_store, graph_provider) -> None:
    """Put back the default model, the collection size, the vectors and the flag.

    Every model change needs an empty store, so the store is emptied first; the
    records are re-embedded from blob storage last, with the original model.
    """
    await _delete_all_embeddings(pipeshub_client, vector_store, journey.org_id)

    if default_model(list_embedding_models(pipeshub_client)) != journey.default_before:
        resp = set_default_embedding_model(pipeshub_client, journey.default_before.model_key)
        assert resp.status_code == 200, (
            f"Could not make {journey.default_before.model} the default embedding model "
            f"again: HTTP {resp.status_code} {error_message(resp)}"
        )
    original_keys = {m.model_key for m in journey.models_before}
    for model in list_embedding_models(pipeshub_client):
        if model.model_key not in original_keys:
            resp = delete_embedding_model(pipeshub_client, model.model_key)
            assert resp.status_code == 200, (
                f"Could not delete the test embedding model {model.model}: "
                f"HTTP {resp.status_code} {error_message(resp)}"
            )
    journey.local_model_key = None

    if await vector_store.dense_size(COLLECTION) != journey.size_before:
        # The collection was rebuilt at the default model's size by the
        # cleanup above, so a second pass now rebuilds it at the original's.
        await _delete_all_embeddings(pipeshub_client, vector_store, journey.org_id)

    await _recreate_all_embeddings(pipeshub_client, kb_client, vector_store, journey.documents)
    await _wait_until_deployment_idle(graph_provider)
    write_platform_settings(
        pipeshub_client,
        read_platform_settings(pipeshub_client).with_flag(
            VECTOR_STORE_REBUILD_FLAG, journey.settings_before.flag(VECTOR_STORE_REBUILD_FLAG)
        ),
    )
    journey.restored = True


@pytest_asyncio.fixture(scope="module", loop_scope="session")
async def journey(pipeshub_client, kb_client: KBClient, vector_store, blob_store, mongo_store, graph_provider):
    org_id = pipeshub_client.org_id
    settings_before = read_platform_settings(pipeshub_client)
    models_before = list_embedding_models(pipeshub_client)
    default_before = default_model(models_before)
    assert default_before is not None, "The org has no embedding model configured."

    kb_id = kb_client.create_kb(f"vector-rebuild-{uuid.uuid4().hex[:8]}")["id"]
    state = Journey(
        kb_id=kb_id,
        org_id=org_id,
        settings_before=settings_before,
        models_before=models_before,
        default_before=default_before,
        size_before=0,
    )
    try:
        for title in ("Falconry Ledger", "Orchard Rota"):
            state.documents.append(
                await _upload(kb_client, vector_store, mongo_store, org_id, kb_id, title)
            )
        size = await vector_store.dense_size(COLLECTION)
        assert size, f"No {COLLECTION!r} collection exists after indexing two documents."
        state.size_before = size
        for doc in state.documents:
            state.blob_counts[doc.name] = await blob_store.count_under(doc.storage_prefix, doc.storage_vendor)
            state.mongo_counts[doc.name] = await mongo_store.count_documents_under_path(doc.storage_prefix)
            # Zero before would make "unchanged after the rebuild" true of nothing.
            assert state.blob_counts[doc.name] and state.mongo_counts[doc.name], (
                f"{doc.name} has no stored files ({state.blob_counts[doc.name]}) or storage documents "
                f"({state.mongo_counts[doc.name]}) under {doc.storage_prefix}; the after-rebuild check would prove nothing."
            )
        logger.info(
            "Before: default model %s, collection size %d, flag %s",
            default_before.model,
            size,
            settings_before.flag(VECTOR_STORE_REBUILD_FLAG),
        )
        yield state
    finally:
        restore_error: Exception | None = None
        if not state.restored:
            try:
                await _restore(state, pipeshub_client, kb_client, vector_store, graph_provider)
            except Exception as exc:  # noqa: BLE001 - re-raised below, after the flag
                restore_error = exc
                logger.error("Could not put the org's embeddings and models back: %s", exc)
                # Never leave the flag on for the rest of the stack's tests.
                try:
                    write_platform_settings(
                        pipeshub_client,
                        read_platform_settings(pipeshub_client).with_flag(
                            VECTOR_STORE_REBUILD_FLAG,
                            settings_before.flag(VECTOR_STORE_REBUILD_FLAG),
                        ),
                    )
                except Exception as flag_exc:  # noqa: BLE001
                    logger.error("Could not restore the Labs flag: %s", flag_exc)
        if restore_error is not None:
            # The rebuild may still be reading these records; keep them, and
            # the evidence, rather than delete underneath it.
            raise restore_error
        try:
            kb_client.delete_kb(kb_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not delete knowledge base %s: %s", kb_id, exc)


@pytest.mark.order(1)
async def test_the_labs_jobs_are_refused_while_the_flag_is_off(
    journey: Journey, pipeshub_client, vector_store
) -> None:
    set_rebuild_flag(pipeshub_client, False)
    try:
        for operation in ("cleanup", "reindex"):
            resp = post_vector_store_job(pipeshub_client, operation)
            assert resp.status_code == 403, (
                f"With {VECTOR_STORE_REBUILD_FLAG} off, the Labs {operation} job answered "
                f"HTTP {resp.status_code} ({error_message(resp)}) instead of 403."
            )
            assert "disabled" in error_message(resp).lower(), error_message(resp)
    finally:
        set_rebuild_flag(pipeshub_client, True)
    for doc in journey.documents:
        assert await vector_store.count_for_virtual_record(doc.virtual_record_id) > 0, (
            f"A refused cleanup still removed the embeddings of {doc.name}."
        )


@pytest.mark.order(2)
async def test_the_embedding_model_cannot_change_while_records_are_indexed(
    journey: Journey, pipeshub_client, vector_store, search_client
) -> None:
    """Scenario 2 of the model change: records from other sources are already indexed."""
    points_before = {d.name: await vector_store.count_for_virtual_record(d.virtual_record_id) for d in journey.documents}

    resp = add_local_embedding_model(pipeshub_client, is_default=True)
    if resp.status_code == 200:
        journey.local_model_key = model_key_from_add(resp)
    elif resp.status_code >= 500:
        # The health check embeds a sample before the collection guard runs,
        # so a 5xx here means the local model itself could not be served.
        journey.local_model_unavailable = (
            f"the local embedding server could not serve {LOCAL_MODEL_NAME}: "
            f"HTTP {resp.status_code} {error_message(resp)}"
        )
        pytest.skip(journey.local_model_unavailable)

    assert resp.status_code == 400, (
        f"Adding {LOCAL_MODEL_NAME} as the default embedding model while records are "
        f"indexed with {journey.default_before.model} answered HTTP {resp.status_code} "
        f"({error_message(resp)}). The existing vectors would no longer match queries."
    )
    assert MODEL_CHANGE_REFUSED in error_message(resp), error_message(resp)

    models = list_embedding_models(pipeshub_client)
    assert models == journey.models_before, (
        f"A refused model change still altered the saved embedding models: {models}"
    )
    points_after = {d.name: await vector_store.count_for_virtual_record(d.virtual_record_id) for d in journey.documents}
    assert points_after == points_before
    assert await vector_store.dense_size(COLLECTION) == journey.size_before
    _assert_found(search_client, journey, journey.documents)
    journey.done.add("refused")


@pytest.mark.order(3)
async def test_deleting_all_embeddings_empties_the_vector_store_and_keeps_everything_else(
    journey: Journey, pipeshub_client, kb_client, vector_store, blob_store, mongo_store, search_client
) -> None:
    await _delete_all_embeddings(pipeshub_client, vector_store, journey.org_id)

    assert await vector_store.dense_size(COLLECTION) == journey.size_before, (
        "After the cleanup the collection was recreated at another size, with the "
        "embedding model unchanged."
    )
    statuses = {d.name: _status(kb_client, d.record_id) for d in journey.documents}
    assert set(statuses.values()) == {"NOT_STARTED"}, (
        "After all embeddings were deleted, the records should read as waiting to be "
        f"indexed again (NOT_STARTED), not as done or failed: {statuses}"
    )
    for doc in journey.documents:
        assert await blob_store.count_under(doc.storage_prefix, doc.storage_vendor) == journey.blob_counts[doc.name], (
            f"Deleting embeddings changed what blob storage holds for {doc.name}."
        )
        assert await mongo_store.count_documents_under_path(doc.storage_prefix) == journey.mongo_counts[doc.name], (
            f"Deleting embeddings changed {doc.name}'s storage documents in MongoDB."
        )
        found = _hits(search_client, journey.kb_id, doc.token, nothing_indexed_ok=True)
        assert doc.virtual_record_id not in found, (
            f"Search still returns {doc.name} after every embedding was deleted."
        )
    journey.done.add("cleanup")


@pytest.mark.order(4)
async def test_recreating_embeddings_with_the_same_model_brings_them_back(
    journey: Journey, pipeshub_client, kb_client, vector_store, search_client
) -> None:
    journey.require("cleanup")
    final = await _recreate_all_embeddings(pipeshub_client, kb_client, vector_store, journey.documents)
    assert set(final.values()) == {"COMPLETED"}, f"Records after the Labs reindex: {final}"

    assert await vector_store.dense_size(COLLECTION) == journey.size_before
    chunks = {d.name: await vector_store.count_content_chunks(d.virtual_record_id) for d in journey.documents}
    expected = {d.name: d.content_chunks for d in journey.documents}
    assert chunks == expected, (
        "Re-embedding from blob storage with the same model should write the same "
        f"chunks the first indexing did. Before: {expected}, after: {chunks}"
    )
    _assert_found(search_client, journey, journey.documents)
    journey.done.add("reindex_same_model")


@pytest.mark.order(5)
async def test_deleting_all_embeddings_again_before_the_model_change(
    journey: Journey, pipeshub_client, vector_store
) -> None:
    """The supported way to change models: empty the store first."""
    journey.require("reindex_same_model")
    journey.require_local_model()
    await _delete_all_embeddings(pipeshub_client, vector_store, journey.org_id)
    assert await vector_store.count_for_org(journey.org_id, collection=COLLECTION) == 0
    journey.done.add("emptied")


@pytest.mark.order(6)
async def test_adding_a_model_that_is_not_the_default_leaves_the_collection_alone(
    journey: Journey, pipeshub_client, vector_store
) -> None:
    journey.require("emptied")
    journey.require_local_model()
    resp = add_local_embedding_model(pipeshub_client, is_default=False)
    assert resp.status_code == 200, (
        f"Adding {LOCAL_MODEL_NAME} (not as default) to an empty store answered "
        f"HTTP {resp.status_code}: {error_message(resp)}"
    )
    journey.local_model_key = model_key_from_add(resp)
    journey.done.add("local_model_added")
    size = await vector_store.dense_size(COLLECTION)
    if size != journey.size_before:
        raise CollectionSizeMismatch(
            f"The default embedding model is still {journey.default_before.model} "
            f"({journey.size_before} wide), but adding {LOCAL_MODEL_NAME} as a "
            f"non-default model rebuilt the collection at {size}."
        )


@pytest.mark.order(7)
async def test_changing_the_model_with_nothing_indexed_is_allowed_and_used(
    journey: Journey, pipeshub_client, kb_client, vector_store, mongo_store, search_client
) -> None:
    """Scenario 1 of the model change: no records indexed."""
    journey.require("emptied")
    journey.require_local_model()
    if journey.local_model_key:
        resp = set_default_embedding_model(pipeshub_client, journey.local_model_key)
    else:
        resp = add_local_embedding_model(pipeshub_client, is_default=True)
        if resp.status_code == 200:
            journey.local_model_key = model_key_from_add(resp)
    assert resp.status_code == 200, (
        f"Making {LOCAL_MODEL_NAME} the default embedding model with an empty vector "
        f"store answered HTTP {resp.status_code}: {error_message(resp)}"
    )
    current = default_model(list_embedding_models(pipeshub_client))
    assert current is not None and current.model_key == journey.local_model_key, current
    assert await vector_store.dense_size(COLLECTION) == LOCAL_MODEL_DIMENSION, (
        f"After switching to {LOCAL_MODEL_NAME} the collection should be "
        f"{LOCAL_MODEL_DIMENSION} wide."
    )

    doc = await _upload(
        kb_client, vector_store, mongo_store, journey.org_id, journey.kb_id, "Lantern Inventory"
    )
    journey.documents.append(doc)
    assert await vector_store.dense_size(COLLECTION) == LOCAL_MODEL_DIMENSION
    _assert_found(search_client, journey, [doc])
    journey.done.add("new_model")


@pytest.mark.order(8)
async def test_recreating_embeddings_with_the_new_model(
    journey: Journey, pipeshub_client, kb_client, vector_store, search_client
) -> None:
    journey.require("new_model")
    final = await _recreate_all_embeddings(pipeshub_client, kb_client, vector_store, journey.documents)
    assert set(final.values()) == {"COMPLETED"}, f"Records after the Labs reindex: {final}"
    assert await vector_store.dense_size(COLLECTION) == LOCAL_MODEL_DIMENSION
    for doc in journey.documents:
        assert await vector_store.count_content_chunks(doc.virtual_record_id) > 0, doc.name
    _assert_found(search_client, journey, journey.documents)
    journey.done.add("reindex_new_model")


@pytest.mark.order(9)
async def test_deleting_the_default_model_while_its_vectors_are_stored_is_refused(
    journey: Journey, pipeshub_client, vector_store
) -> None:
    journey.require("reindex_new_model")
    model_key = journey.local_model_key or ""
    # The delete is also refused (409) when an agent uses the model, which is
    # not the refusal this test is about.
    assert agents_using_model(pipeshub_client, model_key) == [], (
        f"An agent uses {LOCAL_MODEL_NAME}, so its delete would be refused for that reason."
    )
    size_before = await vector_store.dense_size(COLLECTION)

    resp = delete_embedding_model(pipeshub_client, model_key)
    if resp.status_code < 400:
        # The model is gone; the module's restore puts the original default back.
        journey.local_model_key = None
        now_default = default_model(list_embedding_models(pipeshub_client))
        size = await vector_store.dense_size(COLLECTION)
        pytest.fail(
            f"Deleting {LOCAL_MODEL_NAME}, the default embedding model, while the vector "
            f"store holds its vectors answered HTTP {resp.status_code} instead of being "
            f"refused. The default is now {now_default}, and the collection is {size} wide."
        )
    message = error_message(resp)
    still_there = any(m.model_key == model_key for m in list_embedding_models(pipeshub_client))
    assert DELETE_REFUSED_PHRASE in message.lower(), (
        f"Deleting the default embedding model was refused with HTTP {resp.status_code} "
        f"({message}), but not because the vector store holds its vectors."
    )
    assert still_there and await vector_store.dense_size(COLLECTION) == size_before, (
        "The delete was refused, yet the model or the collection changed anyway."
    )


@pytest.mark.order(10)
async def test_the_org_is_left_with_its_original_model_and_embeddings(
    journey: Journey, pipeshub_client, kb_client, vector_store, search_client, graph_provider
) -> None:
    await _restore(journey, pipeshub_client, kb_client, vector_store, graph_provider)

    assert list_embedding_models(pipeshub_client) == journey.models_before
    assert await vector_store.dense_size(COLLECTION) == journey.size_before
    assert read_platform_settings(pipeshub_client).flag(VECTOR_STORE_REBUILD_FLAG) == (
        journey.settings_before.flag(VECTOR_STORE_REBUILD_FLAG)
    )
    statuses = {d.name: _status(kb_client, d.record_id) for d in journey.documents}
    assert set(statuses.values()) == {"COMPLETED"}, statuses
    _assert_found(search_client, journey, journey.documents)
