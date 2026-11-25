"""QA15-9 (ERA-19, graph half): a conversation's artifacts erased on a real Neo4j and a real ArangoDB;
QA15F-9 (ERA-27): its chat attachments too, as the upload wrote them, and nothing the sender does not own;
QA15G-4, QA15G-5 (ERA-28, ERA-29): a service account's uploads, and an uploader deactivated or gone from the graph.

The production providers (``Neo4jProvider``, ``ArangoHTTPProvider``) in containers of this lane's own
(``pxh-qa15-*``, label ``pxh.qa15``). A conversation gets artifacts with versions, provenance, a code
artifact and its DERIVED_FROM edge, owner and type edges, and a copy of one version into another
conversation (SS3); then ``ArtifactEraser`` erases it, as Node's erasure step asks, until nothing
remains. A census then reads every node and edge of the graph (and every stored blob) for the
conversation's id, its artifacts' keys and a canary planted in their content: nothing of the erased
conversation may remain but the text the user copied into the other conversation, whose copy and blob
stay whole.

The blob store is the registry's in-memory fake (Node storage's purge has its own tests).
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import uuid
from typing import TYPE_CHECKING, Any
from unittest.mock import patch

import pytest

from app.config.constants.arangodb import CollectionNames
from app.models.entities import ArtifactType
from app.services.artifact_registry.copies import ArtifactCopier, CopyRequest
from app.services.artifact_registry.erasure import ArtifactEraser
from app.services.artifact_registry.models import Actor, ArtifactProvenance
from app.services.artifact_registry.registry import ArtifactRegistryService
from app.services.chat_attachments.erasure import AttachmentEraser, AttachmentRef
from app.services.chat_attachments.uploads import (
    UPLOADER_AGENT_FIELD,
    UPLOADER_KIND_FIELD,
    UPLOADER_USER_FIELD,
    AttachmentFile,
    Uploader,
    UploaderKind,
    attachment_documents,
    write_attachment_graph,
)

from .fakes import FakeBlobStore

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator

pytestmark = [pytest.mark.docker, pytest.mark.timeout(600, method="thread")]

REQUIRE_DOCKER_ENV = "PXH_REQUIRE_DOCKER"
SUFFIX = uuid.uuid4().hex[:8]
LABELS = {"pipeshub.harness.owner": "x-lead", "pipeshub.agent": "QA15", "pxh.qa15": SUFFIX}
NOW = 1_700_000_000_000


def _image_or_skip(image: str) -> None:
    """Skips without a daemon or the image; with PXH_REQUIRE_DOCKER set, pulls the image or fails."""
    try:
        import docker

        client = docker.from_env()
        client.ping()
    except Exception as exc:
        if os.environ.get(REQUIRE_DOCKER_ENV, "").strip().lower() in {"1", "true", "yes"}:
            pytest.fail(f"{REQUIRE_DOCKER_ENV} is set, but no Docker daemon answers: {type(exc).__name__}")
        pytest.skip(f"docker daemon unreachable: {type(exc).__name__}")
    try:
        client.images.get(image)
    except Exception:
        if os.environ.get(REQUIRE_DOCKER_ENV, "").strip().lower() not in {"1", "true", "yes"}:
            pytest.skip(f"image {image!r} not present")
        client.images.pull(image)
    finally:
        client.close()


@pytest.fixture(scope="module", params=["neo4j", "arango"])
def graph_server(request: pytest.FixtureRequest) -> Iterator[tuple[str, Any]]:
    if request.param == "neo4j":
        from tests.unit.harness.engines.matrix import graph as neo4j

        _image_or_skip(neo4j.NEO4J_IMAGE)
        server = neo4j.start_neo4j(SUFFIX, name="pxh-qa15-neo4j", labels=LABELS)
        try:
            yield "neo4j", server
        finally:
            neo4j.stop_neo4j(server)
    else:
        from tests.unit.harness.engines.matrix import arango

        _image_or_skip(arango.ARANGO_IMAGE)
        server = arango.start_arango(SUFFIX, name="pxh-qa15-arango", labels=LABELS)
        try:
            yield "arango", server
        finally:
            arango.stop_arango(server)


@pytest.fixture
async def graph(graph_server: tuple[str, Any]) -> AsyncIterator[Any]:
    kind, server = graph_server
    logger = logging.getLogger("qa15.graph")
    if kind == "neo4j":
        from tests.unit.harness.engines.matrix import graph as neo4j

        provider = await neo4j.connect(server, logger, None)
        await provider.execute_query("MATCH (n) DETACH DELETE n")
        try:
            yield provider
        finally:
            with contextlib.suppress(Exception):
                await provider.client.disconnect()
    else:
        from tests.unit.harness.engines.matrix import arango

        provider = await arango.connect(server, logger)
        for name in await _arango_collections(provider):
            await provider.execute_query("FOR d IN @@c REMOVE d IN @@c", bind_vars={"@c": name})
        try:
            yield provider
        finally:
            await arango.disconnect(provider)


async def _arango_collections(provider: Any) -> list[str]:  # noqa: ANN401 - ArangoHTTPProvider
    names = await provider.execute_query("FOR c IN COLLECTIONS() FILTER !STARTS_WITH(c.name, '_') RETURN c.name")
    return [str(name) for name in names or []]


async def graph_documents(provider: Any) -> list[tuple[str, str, str]]:  # noqa: ANN401 - IGraphDBProvider
    """Every node and edge of the graph as (node or edge, label or collection, JSON); an edge with its endpoints."""
    if hasattr(provider, "client") and hasattr(provider.client, "driver"):
        nodes = await provider.execute_query("MATCH (n) RETURN labels(n) AS labels, properties(n) AS props") or []
        edges = await provider.execute_query(
            "MATCH (a)-[r]->(b) RETURN type(r) AS type, properties(r) AS props, "
            "properties(a) AS source, properties(b) AS target"
        ) or []
        return [("node", str(row["labels"]), json.dumps(row, default=str)) for row in nodes] + [
            ("edge", str(row["type"]), json.dumps(row, default=str)) for row in edges
        ]
    found: list[tuple[str, str, str]] = []
    for name in await _arango_collections(provider):
        documents = await provider.execute_query("FOR d IN @@c RETURN d", bind_vars={"@c": name}) or []
        found.extend(("edge" if "_from" in d else "node", name, json.dumps(d, default=str)) for d in documents)
    return found


async def census(provider: Any, blobs: FakeBlobStore, needles: list[str]) -> list[tuple[str, str, str]]:  # noqa: ANN401
    """(node, edge or blob; where; needle) for every needle still found in the graph or the blob store."""
    traces = [(kind, where, needle) for kind, where, text in await graph_documents(provider) for needle in needles if needle in text]
    for document_id, document in blobs.documents.items():
        text = json.dumps({key: value.decode("utf-8", "replace") if isinstance(value, bytes) else str(value) for key, value in document.items()})
        traces += [("blob", document_id, needle) for needle in needles if needle in text]
    return traces


async def _seed_user(provider: Any, org_id: str, user_id: str) -> None:  # noqa: ANN401
    await provider.batch_upsert_nodes(
        [{"id": f"ukey-{user_id}", "userId": user_id, "orgId": org_id, "email": f"{user_id}@corp.example",
          "fullName": user_id.title(), "isActive": True, "createdAtTimestamp": NOW, "updatedAtTimestamp": NOW}],
        collection=CollectionNames.USERS.value,
    )


class Conversation:
    """Artifacts of one conversation, written through the production registry."""

    def __init__(self, registry: ArtifactRegistryService, actor: Actor) -> None:
        self.registry = registry
        self.actor = actor
        self.id = f"conv-{uuid.uuid4().hex}"
        self.canary = f"SENTINEL-artifact:{uuid.uuid4()}"
        self.artifact_ids: list[str] = []

    async def artifact(self, name: str, *, versions: int = 1, artifact_type: ArtifactType = ArtifactType.DOCUMENT) -> str:
        made = await self.registry.register(
            actor=self.actor, name=name, artifact_type=artifact_type, mime_type="text/plain",
            content=f"{self.canary} v1".encode(), conversation_id=self.id,
        )
        for version in range(2, versions + 1):
            await self.registry.add_version(actor=self.actor, artifact_id=made.artifact_id, content=f"{self.canary} v{version}".encode())
        self.artifact_ids.append(made.artifact_id)
        return made.artifact_id

    def needles(self) -> list[str]:
        return [self.id, self.canary, *self.artifact_ids]


@pytest.fixture
def blobs() -> Iterator[FakeBlobStore]:
    store = FakeBlobStore()

    async def fetch(*, org_id: str, config_service: object, storage_document_id: str, version: int | None = None, **_: object) -> bytes:
        del config_service
        document = store.documents.get(storage_document_id)
        if document is None or document["org_id"] != org_id:
            raise FileNotFoundError(storage_document_id)
        return await store.get_buffer(org_id, storage_document_id, version)

    with patch("app.agents.actions.util.blob_staging.fetch_blob_bytes", new=fetch):
        yield store


async def _erase_all(eraser: ArtifactEraser, org_id: str, conversation_id: str) -> int:
    erased = 0
    for _ in range(20):
        report = await eraser.erase_conversation(org_id, conversation_id)
        erased += report.artifacts
        if not report.remaining:
            return erased
    raise AssertionError("erasure never reported nothing remaining")


async def test_qa15_9_an_erased_conversation_leaves_no_node_edge_or_blob(graph: Any, blobs: FakeBlobStore) -> None:  # noqa: ANN401
    org_id = f"org-{uuid.uuid4().hex[:12]}"
    actor = Actor(org_id=org_id, user_id=f"user-{uuid.uuid4().hex[:8]}")
    await _seed_user(graph, org_id, actor.user_id)
    registry = ArtifactRegistryService(graph, blobs)
    erased = Conversation(registry, actor)
    kept = Conversation(registry, actor)

    report = await erased.artifact("report.md", versions=3)
    code = await erased.artifact("make_chart.py", artifact_type=ArtifactType.CODE)
    chart = await erased.artifact("chart.txt", versions=2)
    await registry.record_derivation(
        output_artifact_id=chart, output_version=2, code_artifact_id=code, code_version=1,
        provenance=ArtifactProvenance(version=2, run_id="run-1", task_id="task-1", source_record_ids=["rec-source-1"]),
    )
    await kept.artifact("theirs.md")
    copies = await ArtifactCopier(registry, graph).copy(
        actor=actor, from_conversation_id=erased.id, to_conversation_id=kept.id, refs=[CopyRequest(report, 2)]
    )
    assert copies[0].copy_id is not None
    assert {kind for kind, _, _ in await census(graph, blobs, erased.needles())} == {"node", "edge", "blob"}

    eraser = ArtifactEraser(graph, blobs, page=2)
    assert await _erase_all(eraser, org_id, erased.id) == 3

    # The private copy holds the copied text by design (SS3), and nothing else of the erased conversation.
    assert await census(graph, blobs, erased.needles()) == [("blob", copies[0].document_id, erased.canary)]
    kept_after = await census(graph, blobs, [kept.id, kept.canary])
    assert kept_after, "the other conversation's artifacts and its copy are gone"
    assert await registry.get_content(actor=actor, artifact_id=copies[0].copy_id) == f"{erased.canary} v2".encode()


async def test_qa15_9_erasing_another_org_s_conversation_of_the_same_id_touches_nothing(graph: Any, blobs: FakeBlobStore) -> None:  # noqa: ANN401
    org_id = f"org-{uuid.uuid4().hex[:12]}"
    actor = Actor(org_id=org_id, user_id=f"user-{uuid.uuid4().hex[:8]}")
    await _seed_user(graph, org_id, actor.user_id)
    registry = ArtifactRegistryService(graph, blobs)
    theirs = Conversation(registry, actor)
    await theirs.artifact("a.md", versions=2)
    before = await census(graph, blobs, theirs.needles())

    assert await _erase_all(ArtifactEraser(graph, blobs), f"org-{uuid.uuid4().hex[:12]}", theirs.id) == 0

    assert await census(graph, blobs, theirs.needles()) == before


class FailingOnce:
    """The blob store failing the first virtual-record purge, as storage down mid-erasure."""

    def __init__(self, store: FakeBlobStore) -> None:
        self.store = store
        self.failed = False

    async def purge_document(self, org_id: str, document_id: str) -> bool:
        return await self.store.purge_document(org_id, document_id)

    async def purge_virtual_record_documents(self, org_id: str, virtual_record_id: str) -> int:
        if not self.failed:
            self.failed = True
            raise ConnectionError("storage unavailable")
        return await self.store.purge_virtual_record_documents(org_id, virtual_record_id)


async def _upload(
    provider: Any, blobs: FakeBlobStore, *, org_id: str, uploader: Uploader, canary: str,  # noqa: ANN401
    user_key: str | None = None, reader_key: str | None = None, recorded: bool = True, conversation_id: str | None = None,
) -> list[str]:
    """A chat attachment written by the upload route's own writer; returns its needles (key, vrid).

    ``recorded=False`` is an upload written before uploaders were recorded: the same documents and grant, no uploader.
    """
    record_id = str(uuid.uuid4())
    vrid = str(uuid.uuid4())
    record, file = attachment_documents(
        org_id,
        AttachmentFile(
            record_id=record_id, virtual_record_id=vrid, file_name=f"{canary}.txt", mime_type="text/plain",
            size=len(canary), extension="txt", external_record_id=blobs.store(org_id, canary.encode()),
        ),
        uploader,
        NOW,
        tracked=True,
        conversation_id=conversation_id,
    )
    record["isVLMOcrProcessed"] = False
    if not recorded:
        for field in (UPLOADER_USER_FIELD, UPLOADER_KIND_FIELD, UPLOADER_AGENT_FIELD):
            record.pop(field, None)
    await write_attachment_graph(provider, org_id, [record], [file], uploader, user_key, NOW)
    if reader_key:
        await provider.batch_create_edges(
            [{"from_id": reader_key, "from_collection": CollectionNames.USERS.value, "to_id": record_id,
              "to_collection": CollectionNames.RECORDS.value, "type": "USER", "role": "READER",
              "createdAtTimestamp": NOW, "updatedAtTimestamp": NOW}],
            CollectionNames.PERMISSION.value,
        )
    await provider.batch_upsert_nodes(
        [{"id": vrid, "orgId": org_id, "virtualRecordId": vrid, "documentId": blobs.store(org_id, canary.encode(), virtual_record_id=vrid),
          "record_doc_id": "", "updatedAt": NOW}],
        CollectionNames.VIRTUAL_RECORD_TO_DOC_ID_MAPPING.value,
    )
    return [record_id, vrid]


def _member(user_id: str) -> dict[str, Any]:
    return {"uploader": Uploader(user_id, UploaderKind.PERSON), "user_key": f"ukey-{user_id}"}


async def _seed_org(provider: Any, org_id: str) -> None:  # noqa: ANN401
    await provider.batch_upsert_nodes(
        [{"id": org_id, "accountType": "enterprise", "name": org_id, "isActive": True,
          "createdAtTimestamp": NOW, "updatedAtTimestamp": NOW}],
        CollectionNames.ORGS.value,
    )


async def _knowledge_record(provider: Any, org_id: str, canary: str) -> str:  # noqa: ANN401
    knowledge = str(uuid.uuid4())
    await provider.batch_upsert_nodes(
        [{"_key": knowledge, "id": knowledge, "orgId": org_id, "recordName": canary, "externalRecordId": knowledge,
          "recordType": "FILE", "origin": "CONNECTOR", "connectorId": "kb", "connectorName": "KB", "createdAtTimestamp": NOW,
          "updatedAtTimestamp": NOW, "isDeleted": False, "isArchived": False, "version": 1}],
        CollectionNames.RECORDS.value,
    )
    return knowledge


def _attachment_eraser(provider: Any, blobs: Any) -> AttachmentEraser:  # noqa: ANN401
    from app.api.routes.chatbot import _records_owned_by

    return AttachmentEraser(provider, blobs, lambda user_id, ids: _records_owned_by(provider, user_id, ids))


async def test_qa15f_9_a_conversations_attachments_leave_no_node_edge_or_blob_and_others_are_refused(graph: Any, blobs: FakeBlobStore) -> None:  # noqa: ANN401
    org_id = f"org-{uuid.uuid4().hex[:12]}"
    owner, collaborator, stranger = (f"user-{uuid.uuid4().hex[:8]}" for _ in range(3))
    for user in (owner, collaborator, stranger):
        await _seed_user(graph, org_id, user)
    canary = f"SENTINEL-attachment:{uuid.uuid4()}"
    mine = await _upload(graph, blobs, org_id=org_id, canary=canary, reader_key=f"ukey-{collaborator}", **_member(owner))
    theirs_canary = f"SENTINEL-kept:{uuid.uuid4()}"
    theirs = await _upload(graph, blobs, org_id=org_id, canary=theirs_canary, **_member(stranger))
    other_org = await _upload(graph, blobs, org_id=f"org-{uuid.uuid4().hex[:12]}", canary=theirs_canary, **_member(owner))
    knowledge = await _knowledge_record(graph, org_id, theirs_canary)
    assert {kind for kind, _, _ in await census(graph, blobs, [canary, *mine])} == {"node", "edge", "blob"}
    kept_before = await census(graph, blobs, [theirs_canary, *theirs, *other_org, knowledge])

    flaky = FailingOnce(blobs)
    eraser = _attachment_eraser(graph, flaky)
    refs = [AttachmentRef(mine[0], owner), AttachmentRef(theirs[0], owner), AttachmentRef(other_org[0], owner), AttachmentRef(knowledge, owner)]
    with pytest.raises(ConnectionError):
        await eraser.erase(org_id, refs)
    report = await eraser.erase(org_id, refs)
    assert (report.attachments, report.refused, report.removed) == (1, 3, (mine[0],))
    assert await census(graph, blobs, [canary, *mine]) == []
    assert await census(graph, blobs, [theirs_canary, *theirs, *other_org, knowledge]) == kept_before
    again = await eraser.erase(org_id, refs)
    assert (again.attachments, again.blobs, again.refused, again.removed) == (0, 0, 3, (mine[0],))


async def test_qa15g_4_a_service_accounts_uploads_go_with_their_persons_conversation(graph: Any, blobs: FakeBlobStore) -> None:  # noqa: ANN401
    org_id = f"org-{uuid.uuid4().hex[:12]}"
    await _seed_org(graph, org_id)
    member = f"user-{uuid.uuid4().hex[:8]}"
    await _seed_user(graph, org_id, member)
    agent = f"agent-{uuid.uuid4().hex[:8]}"
    person, other_person = (f"slack-{uuid.uuid4().hex[:24]}" for _ in range(2))
    canary = f"SENTINEL-service-upload:{uuid.uuid4()}"
    mine = [
        *await _upload(graph, blobs, org_id=org_id, uploader=Uploader(person, UploaderKind.SERVICE_ACCOUNT, agent), canary=canary),
        *await _upload(graph, blobs, org_id=org_id, uploader=Uploader(person, UploaderKind.SERVICE_ACCOUNT), canary=canary),
    ]
    kept_canary = f"SENTINEL-kept:{uuid.uuid4()}"
    others = await _upload(graph, blobs, org_id=org_id, uploader=Uploader(other_person, UploaderKind.SERVICE_ACCOUNT, agent), canary=kept_canary)
    members = await _upload(graph, blobs, org_id=org_id, canary=kept_canary, **_member(member))
    older = await _upload(
        graph, blobs, org_id=org_id, uploader=Uploader(person, UploaderKind.SERVICE_ACCOUNT, agent), canary=kept_canary, recorded=False,
    )
    knowledge = await _knowledge_record(graph, org_id, kept_canary)
    async def org_grant(record_id: str) -> object:
        return await graph.get_edge(
            from_id=org_id, from_collection=CollectionNames.ORGS.value, to_id=record_id,
            to_collection=CollectionNames.RECORDS.value, collection=CollectionNames.PERMISSION.value,
        )

    assert await org_grant(mine[0]) is not None
    assert {kind for kind, _, _ in await census(graph, blobs, [canary, *mine])} == {"node", "edge", "blob"}
    kept = [kept_canary, *others, *members, *older, knowledge]
    kept_before = await census(graph, blobs, kept)

    refs = [AttachmentRef(record_id, person) for record_id in (mine[0], mine[2], others[0], members[0], older[0], knowledge)]
    report = await _attachment_eraser(graph, blobs).erase(org_id, refs)

    assert (report.attachments, report.refused, report.unrecorded) == (2, 4, 1)
    assert report.removed == (mine[0], mine[2])
    assert await census(graph, blobs, [canary, *mine]) == []
    assert await org_grant(mine[0]) is None
    assert await census(graph, blobs, kept) == kept_before


async def test_qa15g_5_a_deactivated_uploader_and_one_gone_from_the_graph(graph: Any, blobs: FakeBlobStore) -> None:  # noqa: ANN401
    from app.services.messaging.kafka.handlers.entity import EntityEventService

    org_id = f"org-{uuid.uuid4().hex[:12]}"
    deactivated, gone, reader = (f"user-{uuid.uuid4().hex[:8]}" for _ in range(3))
    for user in (deactivated, gone, reader):
        await _seed_user(graph, org_id, user)
    canary = f"SENTINEL-erased:{uuid.uuid4()}"
    deactivated_older = await _upload(graph, blobs, org_id=org_id, canary=canary, recorded=False, **_member(deactivated))
    gone_recorded = await _upload(graph, blobs, org_id=org_id, canary=canary, **_member(gone))
    kept_canary = f"SENTINEL-kept:{uuid.uuid4()}"
    gone_older = await _upload(
        graph, blobs, org_id=org_id, canary=kept_canary, recorded=False, reader_key=f"ukey-{reader}", **_member(gone),
    )
    handler = EntityEventService(logging.getLogger("qa15g.entity"), graph, app_container=None)  # type: ignore[arg-type]
    assert await handler.process_event("userDeleted", {"email": f"{deactivated}@corp.example", "orgId": org_id})
    await graph.delete_nodes_and_edges([f"ukey-{gone}"], CollectionNames.USERS.value)
    kept_before = await census(graph, blobs, [kept_canary, *gone_older])
    eraser = _attachment_eraser(graph, blobs)

    erased = await eraser.erase(org_id, [AttachmentRef(deactivated_older[0], deactivated), AttachmentRef(gone_recorded[0], gone)])
    # The older upload of a user gone from the graph proves nothing, even named on its owner's own turn.
    refused = await eraser.erase(org_id, [AttachmentRef(gone_older[0], gone), AttachmentRef(gone_older[0], reader)])

    assert (erased.attachments, erased.refused) == (2, 0)
    assert (refused.attachments, refused.refused, refused.unrecorded, refused.removed) == (0, 2, 2, ())
    assert await census(graph, blobs, [canary, *deactivated_older, *gone_recorded]) == []
    assert await census(graph, blobs, [kept_canary, *gone_older]) == kept_before


async def test_cleanup_g5_a_conversations_unsent_uploads_go_with_it_and_abandoned_ones_are_listed(graph: Any, blobs: FakeBlobStore) -> None:  # noqa: ANN401
    """CLEANUP-G5 on each backend: the marker, conversation and org filters, the key paging and the erases."""
    from app.services.chat_attachments.erasure import list_unsent
    from app.services.chat_attachments.uploads import mark_sent

    org_id = f"org-{uuid.uuid4().hex[:12]}"
    owner = f"user-{uuid.uuid4().hex[:8]}"
    await _seed_user(graph, org_id, owner)
    canary = f"SENTINEL-unsent:{uuid.uuid4()}"
    kept_canary = f"SENTINEL-kept:{uuid.uuid4()}"
    unsent = [
        await _upload(graph, blobs, org_id=org_id, canary=canary, conversation_id="conv-1", **_member(owner))
        for _ in range(2)
    ]
    sent = await _upload(graph, blobs, org_id=org_id, canary=kept_canary, conversation_id="conv-1", **_member(owner))
    await mark_sent(graph, org_id, [sent[0]], "conv-1")
    abandoned = await _upload(graph, blobs, org_id=org_id, canary=canary, **_member(owner))
    other_org = await _upload(
        graph, blobs, org_id=f"org-{uuid.uuid4().hex[:12]}", canary=kept_canary, **_member(owner),
    )
    kept_before = await census(graph, blobs, [kept_canary, *sent, *other_org])
    eraser = _attachment_eraser(graph, blobs)

    erased = 0
    for _ in range(5):
        report = await AttachmentEraser(graph, blobs, lambda user_id, ids: _no_owner(), page=1).erase_conversation_uploads(
            org_id, "conv-1",
        )
        erased += report.attachments
        if not report.remaining:
            break
    assert erased == 2
    assert await census(graph, blobs, [needle for upload in unsent for needle in upload]) == []

    assert (await list_unsent(graph, org_id, NOW, 50)).uploads == (), "an upload is kept for its grace period"
    listed, cursor = [], None
    for _ in range(5):
        page = await list_unsent(graph, org_id, NOW + 1, 1, after=cursor, page=1)
        listed += page.uploads
        cursor = page.next
        if not page.remaining:
            break
    assert [(u.record_id, u.owner_id, u.conversation_id) for u in listed] == [(abandoned[0], owner, None)]
    # Node finds no message naming it and erases it as a message's attachment is erased.
    report = await eraser.erase(org_id, [AttachmentRef(u.record_id, u.owner_id) for u in listed])
    assert report.attachments == 1
    assert await census(graph, blobs, [canary, *abandoned]) == []
    assert await census(graph, blobs, [kept_canary, *sent, *other_org]) == kept_before


async def _no_owner() -> set[str]:
    return set()


async def test_cleanup_g1_an_erased_org_leaves_no_harness_node_edge_or_blob_and_another_org_stays(graph: Any, blobs: FakeBlobStore) -> None:  # noqa: ANN401
    """CLEANUP-G1 on each backend: page by page, an org's artifacts, chat attachments (sent or not) and skill candidates go;
    the same kinds of another org, and the org's own knowledge records, stay whole."""
    from app.agent_loop_lib.modules.providers.skills.base import SkillCandidate
    from app.agents.agent_loop.skills.graph_store import GraphSkillStore
    from app.services.org_erasure import erase_org_page

    async def seed(org_id: str, canary: str) -> list[str]:
        user = f"user-{uuid.uuid4().hex[:8]}"
        await _seed_user(graph, org_id, user)
        registry = ArtifactRegistryService(graph, blobs)
        conversation = Conversation(registry, Actor(org_id=org_id, user_id=user))
        conversation.canary = canary
        await conversation.artifact("report.md", versions=2)
        await conversation.artifact("chart.py", artifact_type=ArtifactType.CODE)
        uploads = [
            *await _upload(graph, blobs, org_id=org_id, canary=canary, conversation_id=conversation.id, **_member(user)),
            *await _upload(graph, blobs, org_id=org_id, canary=canary, **_member(user)),
        ]
        await GraphSkillStore(graph, org_id, user).queue_candidate(SkillCandidate(
            candidate_id=f"cand-{uuid.uuid4().hex[:8]}", name="learned", description="d", body=canary,
            source_session_id=conversation.id, source_trajectory_summary=canary, created_at="now",
        ))
        return [canary, *conversation.needles(), *uploads]

    erased_org = f"org-{uuid.uuid4().hex[:12]}"
    kept_org = f"org-{uuid.uuid4().hex[:12]}"
    erased = await seed(erased_org, f"SENTINEL-org:{uuid.uuid4()}")
    kept = await seed(kept_org, f"SENTINEL-kept-org:{uuid.uuid4()}")
    knowledge = await _knowledge_record(graph, erased_org, f"SENTINEL-knowledge:{uuid.uuid4()}")
    assert {kind for kind, _, _ in await census(graph, blobs, erased)} == {"node", "edge", "blob"}
    kept_before = await census(graph, blobs, [*kept, knowledge])

    totals = {"attachments": 0, "artifacts": 0, "skillCandidates": 0}
    for _ in range(20):
        report = await erase_org_page(graph, blobs, erased_org, page=1)
        totals["attachments"] += report.attachments
        totals["artifacts"] += report.artifacts
        totals["skillCandidates"] += report.skill_candidates
        if not report.remaining:
            break
    else:
        raise AssertionError("org erasure never reported nothing remaining")
    assert totals == {"attachments": 2, "artifacts": 2, "skillCandidates": 1}
    assert await census(graph, blobs, erased) == []
    assert await census(graph, blobs, [*kept, knowledge]) == kept_before
    again = await erase_org_page(graph, blobs, erased_org, page=1)
    assert (again.attachments, again.artifacts, again.skill_candidates, again.remaining) == (0, 0, 0, False)


async def test_cleanup_g7_a_conversations_skill_candidates_are_erased_with_it(graph: Any) -> None:  # noqa: ANN401
    """CLEANUP-G7 on each backend: a candidate carries its conversation, and only that conversation's go."""
    from app.agent_loop_lib.modules.providers.skills.base import SkillCandidate
    from app.agents.agent_loop.skills.graph_store import (
        GraphSkillStore,
        erase_session_skill_candidates,
    )

    org_id = f"org-{uuid.uuid4().hex[:12]}"
    store = GraphSkillStore(graph, org_id, "user-1")
    canary = f"SENTINEL-candidate:{uuid.uuid4()}"
    for key, session in (("learned", "conv-1"), ("learned-too", "conv-1"), ("elsewhere", "conv-2")):
        await store.queue_candidate(SkillCandidate(
            candidate_id=f"{key}-{uuid.uuid4().hex[:8]}", name=key, description="d", body=canary,
            source_session_id=session, source_trajectory_summary=canary, created_at="now",
        ))
    erased = 0
    for _ in range(5):
        count, more = await erase_session_skill_candidates(graph, org_id, "conv-1", page=1)
        erased += count
        if not more:
            break
    assert erased == 2
    assert [c.name for c in await store.get_pending_candidates()] == ["elsewhere"]
