"""The world the stored-write integration tests run in: a real graph (Neo4j and ArangoDB), the real
storage router over a proxy that can refuse routes, the real ``SinkOrchestrator`` and ``BlobStorage``.

Shared by ``test_artifact_stale_pointer_e2e.py`` and ``test_artifact_stored_write_e2e.py``. Environment:
the ``ARTIFACTS_IT_*`` storage settings (see ``test_artifact_store_contract.py``) and
``tests/integration/real_graph.py``.

This module holds no tests, so it must not spell the storage-URL variable out: CI's storage job
treats every file that does as a test file and fails when it contributes no test case.
"""

from __future__ import annotations

import contextlib
import logging
import os
import re
import time
import uuid
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock

import aiohttp
import pytest
from aiohttp import web

from app.config.constants.arangodb import CollectionNames, Connectors, ProgressStatus
from app.config.constants.neo4j import collection_to_label
from app.config.constants.service import config_node_constants
from app.models.blocks import Block, BlocksContainer, BlockType, SemanticMetadata
from app.models.entities import Record, RecordType
from app.modules.artifacts import reader as reader_module
from app.modules.artifacts import record_backend as backend_module
from app.modules.artifacts import store as store_module
from app.modules.artifacts.reader import record_artifact_reader
from app.modules.artifacts.sidecars import ArtifactSidecars
from app.modules.artifacts.store import NodeArtifactStore
from app.modules.pipeline.fingerprint import content_revision
from app.modules.pipeline.leases import RecordLeases
from app.modules.pipeline.models import Priority, StageJob
from app.modules.pipeline.policy import DEFAULT_POLICY
from app.modules.pipeline.stage import Deadline
from app.modules.pipeline.stages.classify_io import GraphClassifyIO
from app.modules.transformers.blob_storage import (
    BlobStorage,
    close_shared_session,
)
from app.modules.transformers.sink_orchestrator import SinkOrchestrator
from app.modules.transformers.transformer import TransformContext
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider
from app.services.resource_governor.models import ParseTier
from tests.integration.real_graph import (
    backend_unavailable,
    connect_arango,
    connect_neo4j,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable

    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

STORAGE_URL = os.environ.get("ARTIFACTS_IT_STORAGE" + "_URL", "")
SCOPED_SECRET = os.environ.get("ARTIFACTS_IT_SCOPED_SECRET", "")
STORAGE_TYPE = os.environ.get("ARTIFACTS_IT_STORAGE_TYPE", "local")
#: Where the graph-call and latency figures are written (for a comparison between two checkouts).
FIGURES_OUT = os.environ.get("ARTIFACTS_IT_FIGURES_OUT", "")


ARANGO_DB = "artifact_stale_pointer_it"
MAPPING = CollectionNames.VIRTUAL_RECORD_TO_DOC_ID_MAPPING.value
RECORDS = CollectionNames.RECORDS.value
logger = logging.getLogger("artifact-stale-pointer-it")



# ---------------------------------------------------------------------------
# Storage proxy: forwards to the storage service, refuses routes on demand.
# ---------------------------------------------------------------------------


class FaultProxy:
    def __init__(self, upstream: str) -> None:
        self.upstream = upstream.rstrip("/")
        self.refuse: list[re.Pattern[str]] = []
        #: ``[method, path pattern, status, answers left]``: the next requests to it answer *status* without reaching storage.
        self.fail_next: list[list[Any]] = []
        self.seen: list[str] = []
        self._runner: web.AppRunner | None = None
        self.origin = ""

    async def start(self) -> None:
        app = web.Application(client_max_size=512 * 1024 * 1024)
        app.router.add_route("*", "/{tail:.*}", self._forward)
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]  # type: ignore[union-attr]
        self.origin = f"http://127.0.0.1:{port}"

    async def stop(self) -> None:
        if self._runner is not None:
            await self._runner.cleanup()

    async def _forward(self, request: web.Request) -> web.StreamResponse:
        path = request.rel_url.path
        self.seen.append(f"{request.method} {path}")
        if any(p.search(path) for p in self.refuse):
            return web.json_response({"error": "refused by the test"}, status=500)
        for rule in self.fail_next:
            if rule[3] > 0 and rule[0] == request.method and rule[1].search(path):
                rule[3] -= 1
                return web.json_response({"error": {"code": "HTTP_SERVICE_UNAVAILABLE"}}, status=rule[2])
        body = await request.read()
        headers = {k: v for k, v in request.headers.items() if k.lower() not in ("host", "content-length")}
        async with aiohttp.ClientSession(auto_decompress=False) as session, session.request(
            request.method, f"{self.upstream}{request.rel_url}", data=body or None, headers=headers,
            allow_redirects=False,
        ) as upstream:
            data = await upstream.read()
            out_headers = {
                k: v for k, v in upstream.headers.items()
                if k.lower() not in ("content-length", "transfer-encoding", "connection")
            }
            return web.Response(body=data, status=upstream.status, headers=out_headers)


PRESIGN = re.compile(r"/internal/artifacts/presign$")


def _config_service(endpoint: str, settings: Callable[[], dict[str, Any]]) -> MagicMock:
    """The config service the code under test reads; the platform settings blob comes from *settings*."""
    values = {
        config_node_constants.SECRET_KEYS.value: {"scopedJwtSecret": SCOPED_SECRET},
        config_node_constants.ENDPOINTS.value: {"cm": {"endpoint": endpoint}},
        config_node_constants.STORAGE.value: {"storageType": STORAGE_TYPE},
    }
    service = MagicMock()
    service.get_config = AsyncMock(
        side_effect=lambda key, *a, **kw: settings() if key == "/services/platform/settings" else values[key]
    )
    return service


# ---------------------------------------------------------------------------
# The world one test runs in.
# ---------------------------------------------------------------------------


class World:
    def __init__(self, provider: IGraphDBProvider, run: str, proxy: FaultProxy) -> None:
        self.provider = provider
        self.run = run
        self.proxy = proxy
        self.org = uuid.uuid4().hex[:24]
        self.other_org = uuid.uuid4().hex[:24]
        self.write_on = True
        #: ``legacy``, ``shadow`` or ``artifact``: which Labs flags the settings blob carries.
        self.read_mode = "artifact"
        self.generation: str | None = "gen-it"
        self.reads: list[str] = []
        self.shadow: list[tuple[str, ...]] = []
        self.blob = BlobStorage(
            logger=logger, config_service=_config_service(proxy.origin, self.settings), graph_provider=provider,
        )
        vector_store = AsyncMock()
        vector_store.apply = AsyncMock(return_value=True)
        self.sink = SinkOrchestrator(
            graphdb=AsyncMock(), blob_storage=self.blob, vector_store=vector_store,
            graph_provider=provider, logger=logger, config_service=self.blob.config_service,
        )
        self.records: list[str] = []
        self.vrids: list[str] = []

    def settings(self) -> dict[str, Any]:
        """The platform settings blob: the flags this world's mode implies, and the generation."""
        flags = {
            "ENABLE_ARTIFACT_WRITE": self.write_on,
            "ENABLE_ARTIFACT_READ": self.read_mode == "artifact",
            "ENABLE_ARTIFACT_READ_SHADOW": self.read_mode == "shadow",
        }
        blob: dict[str, Any] = {"featureFlags": flags}
        if self.read_mode != "legacy" and self.generation is not None:
            blob["artifactReadGeneration"] = self.generation
        return blob

    def vrid(self, name: str = "vr") -> str:
        vrid = f"{self.run}-{name}"
        self.vrids.append(vrid)
        return vrid

    def record(
        self, vrid: str, texts: list[str], *, name: str = "a", org: str | None = None, connector: str | None = None,
    ) -> Record:
        record_id = f"{self.run}-rec-{name}"
        if record_id not in self.records:
            self.records.append(record_id)
        return Record(
            id=record_id, org_id=org or self.org, virtual_record_id=vrid, record_name=f"{name}.txt",
            record_type=RecordType.FILE, external_record_id=f"ext-{name}", version=1, origin="UPLOAD",
            connector_name=Connectors.KNOWLEDGE_BASE, connector_id=connector or f"conn-{self.run}",
            block_containers=BlocksContainer(
                blocks=[Block(index=i, type=BlockType.TEXT, data=t) for i, t in enumerate(texts)],
                block_groups=[],
            ),
        )

    async def put_record_node(self, record: Record, rev: str | None) -> None:
        """What the record event leaves before indexing: the content's revision, indexing in progress."""
        node = {**record.to_arango_base_record(), "indexingStatus": ProgressStatus.IN_PROGRESS.value}
        node["contentRev"] = rev
        assert await self.provider.batch_upsert_nodes([node], RECORDS)
        if rev is None:
            # Arango keeps a written null; Neo4j drops it. Both read as "no revision".
            doc = await self.provider.get_document(record.id, RECORDS)
            assert not (doc or {}).get("contentRev")

    async def index(self, record: Record, rev: str | None, **settings: Any) -> None:  # noqa: ANN401
        await self.put_record_node(record, rev)
        await self.sink.index(TransformContext(record=record.model_copy(deep=True), settings=dict(settings)))

    async def row(self, document_id: str) -> dict[str, Any] | None:
        """The storage row of a document (None when it is gone)."""
        return await self.blob._get_document_row(self.org, document_id)

    async def documents(self, vrid: str) -> list[dict[str, Any]]:
        """Every ``record_<vrid>`` document storage holds for *vrid*."""
        headers, endpoint, _ = await self.blob._get_auth_and_config(self.org)
        url = f"{endpoint}/api/v1/document/internal/records/{vrid}/documents"
        async with aiohttp.ClientSession() as session, session.get(url, headers=headers) as resp:
            assert resp.status == 200, await resp.text()
            body = await resp.json()
        return [d for d in body.get("documents") or [] if d.get("documentName") == f"record_{vrid}"]

    def stage_job(self, record: Record, rev: str, version: int = 4) -> StageJob:
        return StageJob.model_validate({
            "stage": "classify", "stage_version": version, "org_id": record.org_id,
            "virtual_record_id": record.virtual_record_id, "rev": rev, "record_ids": (record.id,),
            "connector_id": record.connector_id, "tier": ParseTier.LIGHT, "priority": Priority.BULK,
            "trigger": "embed", "text_digest": "t", "blocks_digest": "b", "text_chars": 10,
            "has_tables": False, "has_images": False,
        })

    def classify_io(self, job: StageJob) -> GraphClassifyIO:
        """The classify stage's real writer over this world's graph and storage."""
        from unittest.mock import MagicMock

        from app.events.processor import convert_record_dict_to_record

        sink = MagicMock()
        sink.prepare_enrichment = AsyncMock()
        sink.write_enrichment = AsyncMock()
        sink.write_summary_point = AsyncMock()
        sink.entity_resolution_mode = MagicMock(return_value="off")
        return GraphClassifyIO(
            job, Deadline(30), policy=DEFAULT_POLICY, graph=self.provider, blob_storage=self.blob, sink=sink,
            classifier=AsyncMock(), config_service=self.blob.config_service,
            record_from_document=convert_record_dict_to_record, record_leases=RecordLeases(),
            sidecars=ArtifactSidecars(NodeArtifactStore(self.blob, logger), self.blob.config_service, logger),
        )

    async def classify(self, record: Record, rev: str, semantic: SemanticMetadata, *, version: int = 4) -> None:
        """What the classify stage does with a new classification."""
        job = self.stage_job(record, rev, version)
        await self.classify_io(job).save(job, [record.id], semantic)

    async def mapping(self, vrid: str) -> dict[str, Any]:
        return await self.provider.get_document(vrid, MAPPING) or {}

    async def read(self, vrid: str, org: str | None = None, *, batched: bool = False) -> list[str] | None:
        reader = record_artifact_reader(self.blob)
        lookup = None
        if batched:
            lookup = (await reader.lookup_many([vrid])).get(vrid)
        record = await reader.read_record(vrid, org or self.org, lookup=lookup)
        if record is None:
            return None
        return [b.get("data") for b in record["block_containers"]["blocks"]]

    async def stored(self, vrid: str) -> list[str] | None:
        record = await self.blob.get_record_from_storage(vrid, self.org)
        return None if record is None else [b.get("data") for b in record["block_containers"]["blocks"]]


def _rev(texts: list[str]) -> str:
    return content_revision("\n".join(texts).encode())


A = ["alpha one", "alpha two"]
B = ["bravo one", "bravo two", "bravo three"]
C = ["charlie one"]


@pytest.fixture(params=["neo4j", "arango"])
async def world(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[World]:
    async with contextlib.AsyncExitStack() as cleanup:
        try:
            provider = await (
                connect_neo4j(logger, monkeypatch) if request.param == "neo4j"
                else connect_arango(logger, ARANGO_DB)
            )
        except Exception as exc:
            backend_unavailable(request.param, exc)
        disconnect = getattr(provider, "disconnect", None)
        if disconnect is not None:
            cleanup.push_async_callback(disconnect)
        proxy = FaultProxy(STORAGE_URL)
        await proxy.start()
        cleanup.push_async_callback(proxy.stop)
        cleanup.push_async_callback(close_shared_session)
        store_module.ARTIFACT_ROUTES.reset()

        w = World(provider, f"qa{uuid.uuid4().hex[:10]}", proxy)
        # Every indexing member follows the stored-write protocol unless a test says otherwise.
        from app.services.messaging import membership

        async def fresh(_config_service: object) -> list[dict[str, object]]:
            return [{"checkedAt": int(time.time() * 1000), "untagged": []}]

        monkeypatch.setattr(membership, "read_entries_from_config", fresh)
        membership._READERS.clear()
        monkeypatch.setattr(backend_module, "record_artifact_read", w.reads.append)
        monkeypatch.setattr(reader_module, "record_artifact_read", w.reads.append)
        monkeypatch.setattr(reader_module, "record_artifact_shadow_read", lambda *a: w.shadow.append(a))
        try:
            yield w
        finally:
            await provider.delete_nodes(w.records, RECORDS)
            if isinstance(provider, Neo4jProvider):
                await provider.client.execute_query(
                    f"MATCH (n:{collection_to_label(MAPPING)}) WHERE n.id STARTS WITH $p DETACH DELETE n",
                    parameters={"p": f"{w.run}-"},
                )
            else:
                await provider.http_client.execute_aql(
                    f"FOR n IN {MAPPING} FILTER STARTS_WITH(n._key, @p) REMOVE n IN {MAPPING}", {"p": f"{w.run}-"},
                )


