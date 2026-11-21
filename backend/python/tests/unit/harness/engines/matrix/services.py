"""``RunServices`` for the matrix: the production graph provider on the lane's Neo4j, ``RetrievalService`` over it, and
the outermost I/O replaced (E2E3 §1.1).

Replaced, each for a stated reason: the agent definition read (a dict, not the agent service's graph shape), blob
storage (``BlobStorage``'s record reads answer from the seeded blocks), the embedder (a hash embedder) and the
configuration store (declared documents). The vector store is the contract fake unless a cell names a real one
(``vector_stores.on_vector_store``). Everything the access rules run on is real.
"""

from __future__ import annotations

import logging
import uuid
from contextlib import ExitStack
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any
from unittest.mock import patch

from app.config.constants.service import config_node_constants
from app.services.featureflag.config.config import CONFIG
from tests.support.config_service import FakeConfigurationService
from tests.support.in_memory_vector_db import InMemoryVectorDBService
from tests.unit.harness.engines.matrix.org import HashEmbeddings
from tests.unit.harness.engines.matrix.vendors import (
    GOOGLE_TOKEN,
    GRAPH_TOKEN,
    IMAGE_KEY,
    SLACK_TOKEN,
)

if TYPE_CHECKING:
    from app.services.vector_db.collection_registry import CollectionRegistry
    from app.services.vector_db.interface.vector_db import IVectorDBService
    from tests.unit.harness.engines.matrix.org import Org

AGENT = "agent-e2e3"
JIRA_INSTANCE = "jira-e2e3"
# P5.10's test instance id: its credential and manifest paths are keyed by it.
MCP_INSTANCE = "mcp-echo"
# The MCP server's credential (its ``ECHO_API_TOKEN``): it may exist only in the runner's environment.
MCP_SECRET = "mcp-secret-E2E3-runner-only-0123456789"
# The Jira credential the toolset resolver hands the client (a PAT: ``Bearer``); C-13-style scans look for it.
JIRA_TOKEN = "jira-pat-E2E3-not-a-real-token-0123456789"
# TG-16's OAuth toolsets: (toolset type, instance, display name, the tools the agent picked, the access token alice's
# sign-in stored). Their vendor is ``vendors.FakeVendors``.
VENDOR_TOOLSETS: tuple[tuple[str, str, str, tuple[str, ...], str], ...] = (
    ("slack", "slack-tg16", "Slack", ("get_channel_history",), SLACK_TOKEN),
    ("gmail", "gmail-tg16", "Gmail", ("search_emails",), GOOGLE_TOKEN),
    ("drive", "drive-tg16", "Google Drive", ("get_files_list",), GOOGLE_TOKEN),
    ("outlook", "outlook-tg16", "Outlook", ("get_message",), GRAPH_TOKEN),
    ("onedrive", "onedrive-tg16", "OneDrive", ("get_drives",), GRAPH_TOKEN),
)
# The org's OAuth app for each of them (its client id and secret the SDKs sign in with).
OAUTH_CLIENT_SECRET = "oauth-client-secret-TG16-not-real-0123456789"


def agent_doc(org: Org, *, mcp_tools: tuple[str, ...] = ("echo", "env_digest", "where")) -> dict[str, Any]:
    """What the agent service would answer for the matrix's agent: Jira, the MCP server, the seeded drive."""
    return {
        "_key": AGENT,
        "name": "Matrix agent",
        "systemPrompt": "You are the matrix agent.",
        "instructions": "",
        "isServiceAccount": False,
        "sendUserContext": False,
        "toolsets": [
            {
                "name": "jira",
                "instanceId": JIRA_INSTANCE,
                "displayName": "Jira",
                "tools": [
                    {"name": "search_issues", "fullName": "jira.search_issues"},
                    {"name": "create_issue", "fullName": "jira.create_issue"},
                ],
            },
            *(
                {"name": kind, "instanceId": instance, "displayName": display,
                 "tools": [{"name": t, "fullName": f"{kind}.{t}"} for t in tools]}
                for kind, instance, display, tools, _ in VENDOR_TOOLSETS
            ),
        ],
        "mcpServers": [
            {
                "instanceId": MCP_INSTANCE,
                "name": "EchoMCP",
                "displayName": "EchoMCP",
                "typeId": "echo",
                "tools": [{"name": t, "fullName": f"mcp_echo_{t}"} for t in mcp_tools],
            }
        ],
        "knowledge": [{"connectorId": org.connector_id, "type": "Drive"}],
        "models": [],
    }


def config_service(
    org: Org, *, jira_url: str = "", images_url: str = "", storage_mount: str = "", extra: dict[str, Any] | None = None
) -> FakeConfigurationService:
    """The org's configuration documents: platform flags, the Jira instance and alice's credential for it, the
    OAuth toolsets' instances, apps and alice's sign-ins (TG-16), the image model at ``images_url`` and local record
    storage under ``storage_mount``."""
    from app.agents.constants.toolset_constants import get_toolset_config_path

    images = [{
        "provider": "openAICompatible", "isDefault": True, "modelKey": "image-tg16",
        "configuration": {"model": "pxh-image-tg16", "endpoint": f"{images_url}/v1", "apiKey": IMAGE_KEY},
    }] if images_url else []
    nodes: dict[str, Any] = {
        "/services/agentHarness/approvals": None,
        config_node_constants.AI_MODELS.value: {"embedding": [], "imageGeneration": images},
        f"/services/mcp/credentials/{MCP_INSTANCE}/{org.user_id('alice')}": {
            "isAuthenticated": True, "credentials": {"apiToken": MCP_SECRET},
        },
        get_toolset_config_path(JIRA_INSTANCE, org.user_id("alice")): {
            "isAuthenticated": True,
            "authType": "API_TOKEN",
            "instanceId": JIRA_INSTANCE,
            "orgId": org.org_id,
            "auth": {"apiToken": JIRA_TOKEN},
        },
        **{
            get_toolset_config_path(instance, org.user_id("alice")): {
                "isAuthenticated": True, "authType": "OAUTH", "instanceId": instance, "orgId": org.org_id,
                "toolsetType": kind, "oauthConfigId": f"oauth-{instance}", "credentials": {"access_token": token},
            }
            for kind, instance, _, _, token in VENDOR_TOOLSETS
        },
        **{
            f"/services/oauths/toolsets/{kind}": [{
                "_id": f"oauth-{instance}", "orgId": org.org_id,
                "config": {"clientId": f"client-{instance}", "clientSecret": OAUTH_CLIENT_SECRET, "tenantId": "tenant-tg16"},
            }]
            for kind, instance, _, _, _ in VENDOR_TOOLSETS
        },
        **({config_node_constants.STORAGE.value: {"storageType": "local", "mountName": storage_mount}} if storage_mount else {}),
        **(extra or {}),
    }
    service = FakeConfigurationService(
        nodes,
        flags={CONFIG.ENABLE_KNOWLEDGE_PUBLICATIONS: True, CONFIG.ENABLE_SOFT_DELETE: True, CONFIG.ENABLE_MCP: True},
    )
    vendor_instances = [
        {"_id": instance, "toolsetType": kind, "instanceName": display, "orgId": org.org_id, "authType": "OAUTH",
         "oauthConfigId": f"oauth-{instance}", "auth": {}}
        for kind, instance, display, _, _ in VENDOR_TOOLSETS
    ]
    if jira_url:
        jira = {"_id": JIRA_INSTANCE, "toolsetType": "jira", "instanceName": "Jira", "orgId": org.org_id,
                "authType": "API_TOKEN", "auth": {"baseUrl": jira_url}}
        service.put("/services/toolset-instances", [jira, *vendor_instances])
    return service


@dataclass
class MatrixServices:
    """``RunServices`` over the lane's backends."""

    org: Org
    graph: Any  # Neo4jProvider
    config: FakeConfigurationService
    agent: dict[str, Any]
    vectors: IVectorDBService = field(default_factory=InMemoryVectorDBService)
    _logger: logging.Logger = field(default_factory=lambda: logging.getLogger("e2e3.matrix"))
    _retrieval: Any = None
    _registry: CollectionRegistry | None = None

    def logger(self) -> logging.Logger:
        return self._logger

    def config_service(self) -> Any:  # noqa: ANN401
        return self.config

    async def graph_provider(self) -> Any:  # noqa: ANN401
        return self.graph

    def collection_registry(self) -> CollectionRegistry:
        """The registry the indexer writes through and retrieval reads through: one, so the seed's collection, its
        payload indexes and its manifest entry are the ones a search resolves."""
        if self._registry is None:
            from app.services.vector_db.collection_manifest import (
                CollectionManifestStore,
            )
            from app.services.vector_db.collection_registry import CollectionRegistry
            from app.services.vector_db.models import CollectionConfig, DistanceMetric
            from app.services.vector_db.strategies.single import (
                SingleCollectionStrategy,
            )

            self._registry = CollectionRegistry(
                self.vectors, SingleCollectionStrategy(),
                lambda size: CollectionConfig(embedding_size=size, distance_metric=DistanceMetric.COSINE),
                CollectionManifestStore(self.config, self._logger), self._logger,
            )
        return self._registry

    async def retrieval_service(self) -> Any:  # noqa: ANN401
        if self._retrieval is None:
            from app.modules.retrieval.retrieval_service import RetrievalService

            service = RetrievalService(
                logger=self._logger, config_service=self.config, collection_registry=self.collection_registry(),
                vector_db_service=self.vectors, graph_provider=self.graph, blob_store=None,  # type: ignore[arg-type]
            )
            embeddings = HashEmbeddings()

            async def dense(use_cache: bool = False) -> HashEmbeddings:
                return embeddings

            service.get_embedding_model_instance = dense  # type: ignore[method-assign]
            self._retrieval = service
        return self._retrieval

    async def blob_store(self) -> Any:  # noqa: ANN401
        return None

    async def entity_vector_store(self, org_id: str | None = None) -> Any:  # noqa: ANN401
        """None: the entity index is not part of the matrix (the entity tools then stay out, as on a deployment
        without one)."""
        return None


def stub_agent_reads(graph: Any, agent: dict[str, Any]) -> None:  # noqa: ANN401
    """The agent service's reads on the real provider: the definition and the actor's permission to use it."""
    import copy

    async def get_agent(agent_key: str, org_id: str, *_: object, **__: object) -> dict[str, Any] | None:
        return copy.deepcopy(agent) if agent_key == AGENT else None

    async def check_agent_permission(*_: object, **__: object) -> bool:
        return True

    graph.get_agent = get_agent
    graph.check_agent_permission = check_agent_permission


@dataclass
class StoredFiles:
    """Node's storage service as the artifact registry reaches it: versioned documents of bytes."""

    documents: dict[str, list[bytes]] = field(default_factory=dict[str, list[bytes]])

    async def create(self, *_: object, org_id: str, conversation_id: str, file_name: str, file_bytes: bytes, **__: object) -> dict[str, Any]:
        document_id = f"doc-{uuid.uuid4().hex[:12]}"
        self.documents[document_id] = [file_bytes]
        return {"documentId": document_id, "fileName": file_name}

    async def append(self, *_: object, org_id: str, document_id: str, file_name: str, file_bytes: bytes, **__: object) -> dict[str, Any]:
        versions = self.documents.setdefault(document_id, [])
        versions.append(file_bytes)
        return {
            "documentId": document_id,
            "sizeBytes": len(file_bytes),
            "storageVersion": len(versions) - 1,
            "priorStorageVersion": len(versions) - 2 if len(versions) > 1 else None,
        }

    async def read(self, *, storage_document_id: str, version: int | None = None, **_: object) -> bytes:
        versions = self.documents[storage_document_id]
        return versions[-1 if version is None else version]


def blob_seam(org: Org, files: StoredFiles) -> ExitStack:
    """``BlobStorage`` answered in process: record reads from the seeded blocks, artifact documents in ``files``
    (storage is Node's service over S3 in a deployment)."""
    from app.modules.transformers.blob_storage import BlobStorage

    async def record(self: object, virtual_record_id: str, org_id: str, lookup_result: dict | None = None) -> dict | None:
        return org.full_record(virtual_record_id) if org_id == org.org_id else None

    async def lookups(self: object, virtual_record_ids: list[str], *_: object, **__: object) -> dict[str, dict[str, Any]]:
        return {v: {"record_doc_id": f"doc-{v}"} for v in virtual_record_ids if org.full_record(v) is not None}

    async def one(self: object, virtual_record_id: str) -> dict[str, Any] | None:
        return {"record_doc_id": f"doc-{virtual_record_id}"} if org.full_record(virtual_record_id) else None

    async def reconciliation(self: object, *_: object, **__: object) -> None:
        return None

    async def create(self: object, org_id: str, conversation_id: str, file_name: str, file_bytes: bytes, content_type: str = "") -> dict[str, Any]:
        return await files.create(org_id=org_id, conversation_id=conversation_id, file_name=file_name, file_bytes=file_bytes)

    async def append(self: object, org_id: str, document_id: str, file_name: str, file_bytes: bytes, content_type: str = "") -> dict[str, Any]:
        return await files.append(org_id=org_id, document_id=document_id, file_name=file_name, file_bytes=file_bytes)

    stack = ExitStack()
    for name, body in (
        ("get_record_from_storage", record),
        ("get_document_ids_by_virtual_record_ids", lookups),
        ("get_document_id_by_virtual_record_id", one),
        ("get_reconciliation_metadata", reconciliation),
        ("save_versioned_artifact_to_storage", create),
        ("upload_artifact_version", append),
    ):
        stack.enter_context(patch.object(BlobStorage, name, body))
    stack.enter_context(patch("app.agents.actions.util.blob_staging.fetch_blob_bytes", files.read))
    return stack


__all__ = [
    "AGENT",
    "JIRA_INSTANCE",
    "JIRA_TOKEN",
    "MCP_INSTANCE",
    "MCP_SECRET",
    "OAUTH_CLIENT_SECRET",
    "VENDOR_TOOLSETS",
    "MatrixServices",
    "StoredFiles",
    "agent_doc",
    "blob_seam",
    "config_service",
    "stub_agent_reads",
]
