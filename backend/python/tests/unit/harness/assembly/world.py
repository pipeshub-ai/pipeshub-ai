"""A deployment for assembler tests: one agent, a small toolset catalog, fake stores.

Only the outermost I/O is faked: the graph and configuration stores, MCP
discovery and instances, the connector client factory, and the model. The
tool loader, instance creator, MCP provider, prompt builder, credential
resolution and ``build_initial_state`` are the real ones.
"""

from __future__ import annotations

import copy
import dataclasses
import logging
from contextlib import ExitStack
from typing import TYPE_CHECKING, Any, NoReturn, cast
from unittest.mock import AsyncMock, MagicMock, patch

from app.agent_loop_lib.tools.base import ParameterType, Tag, ToolParameter
from app.agent_loop_lib.tools.decorators import tool
from app.agents.actions.artifacts.artifacts import ArtifactManager
from app.agents.actions.internal_tools.intrim_tools import InternalTools
from app.agents.mcp.errors import MCPRequestNotSentError
from app.agents.mcp.models import MCPToolInfo
from app.agents.run_definition import MAX_CHILDREN_PER_RUN
from app.agents.versions import version_document
from app.config.constants.arangodb import CollectionNames
from app.harness.adapters.content_store import InMemorySealedContent
from app.harness.assembly.model import ResolvedModel
from app.harness.contracts.state import HarnessRun
from app.harness.journal_node import NodeClaim
from app.services.graph_db.interface.graph_db_provider import (
    AccessCheck,
    IGraphDBProvider,
)
from harness_core.contracts import validate_contract
from harness_core.contracts.generated.run_spec import RunSpec
from harness_core.journal import InMemoryJournal
from harness_core.journal.types import Claim
from tests.support.config_service import FakeConfigurationService

if TYPE_CHECKING:
    from collections.abc import Callable

    from app.harness.assembly.context import RunContext
    from harness_core.kernel.durable.ports import DurableModelPort

ORG = "org-1"
ACTOR = "user-1"
AGENT = "agent-1"
JIRA = "inst-42"
MCP = "mcp-9"
DIGEST = "sha256:" + "0" * 64

DESCRIPTIONS = {"search_issues": "Search Jira issues."}


class FakeJira:
    """An external connector toolset (it has a client factory)."""

    def __init__(self, client: object) -> None:
        self.client = client

    @tool(
        path="/tools/jira/search_issues",
        short_description="Search issues",
        description="Search Jira issues.",
        parameters=[ToolParameter(name="jql", type=ParameterType.STRING, description="JQL", required=True)],
        tags=[Tag("type", "search")],
    )
    async def search_issues(self, jql: str) -> str:
        return f'{{"issues": ["{jql}"]}}'

    @tool(
        path="/tools/jira/create_issue",
        short_description="Create an issue",
        description="Create a Jira issue.",
        parameters=[ToolParameter(name="title", type=ParameterType.STRING, description="Title", required=True)],
        tags=[Tag("type", "create")],
    )
    async def create_issue(self, title: str) -> str:
        return '{"id": "J-1"}'


class FakeKnowledgeGraph:
    """Stands in for the internal knowledge toolset (gated on attached knowledge)."""

    def __init__(self, *_: object, **__: object) -> None:
        pass

    @tool(
        path="/tools/knowledgegraph/search",
        short_description="Search knowledge",
        description="Search the organization's knowledge.",
        parameters=[ToolParameter(name="query", type=ParameterType.STRING, description="Query", required=True)],
        tags=[Tag("category", "read")],
    )
    async def search(self, query: str) -> str:
        return f'{{"hits": ["{query}"]}}'


def replace_tool(patcher: Any, owner: type, name: str, body: Callable[..., Any]) -> None:  # noqa: ANN401
    """Swaps a ``@tool`` method's body, keeping its tool metadata.

    The toolset builder caches each class's tool methods once per process
    (``harness_core.tools.toolset._TOOL_ATTR_CACHE``). A bare replacement is
    not a tool: if it is what the cache sees first, the tool is missing from
    that class for every later test in the process (C-14).
    """
    body.__dict__.update(getattr(owner, name).__dict__)
    patcher.setattr(owner, name, body)


class _JiraFactory:
    async def create_client(self, config_service: object, logger: object, config: dict, state: dict) -> dict:
        return {"config": config}


class ToolsetCatalog:
    """What ``get_toolset_registry()`` answers for the loader."""

    def get_all_toolsets(self) -> dict[str, dict[str, Any]]:
        return {
            "internaltools": {"class": InternalTools, "isInternal": True, "provider": "platform", "essential": True, "description": "Internal"},
            "artifacts": {"class": ArtifactManager, "isInternal": True, "provider": "platform", "essential": True, "description": "Artifacts"},
            "knowledgegraph": {"class": FakeKnowledgeGraph, "isInternal": True, "provider": "platform", "essential": True, "description": "Knowledge"},
            "jira": {"class": FakeJira, "isInternal": False, "provider": "connector", "essential": False, "description": "Jira"},
        }

    def get_toolset_metadata(self, toolset_name: str, serialize: bool = True) -> dict[str, Any] | None:
        """As the registry answers it: the tools its own discovery finds on the toolset's class."""
        from app.agents.registry.toolset_registry import ToolsetRegistry

        entry = self.get_all_toolsets().get(toolset_name)
        if entry is None:
            return None
        return {"name": toolset_name, "tools": ToolsetRegistry()._discover_tools_from_class(entry["class"])}  # pyright: ignore[reportPrivateUsage]


def agent_doc(**overrides: object) -> dict[str, Any]:
    doc: dict[str, Any] = {
        "_key": AGENT,
        "name": "Golden agent",
        "systemPrompt": "You are the golden agent.",
        "instructions": "Be brief.",
        "isServiceAccount": False,
        "toolsets": [
            {
                "name": "jira",
                "instanceId": JIRA,
                "displayName": "Jira",
                "tools": [
                    {"name": "search_issues", "fullName": "jira.search_issues"},
                    {"name": "create_issue", "fullName": "jira.create_issue"},
                ],
            }
        ],
        "mcpServers": [
            {
                "instanceId": MCP,
                "name": "GitHubMCP",
                "displayName": "GitHub",
                "typeId": "github",
                "tools": [{"name": "list_prs", "fullName": "mcp_github_list_prs"}],
            }
        ],
        "knowledge": [{"connectorId": "kb-1", "type": "KB"}],
        "models": [],
    }
    doc.update(overrides)
    return doc


# The children Node pins a deep turn of an agent whose definition sets none (``DEEP_MAX_CHILDREN``).
DEEP_CHILDREN = MAX_CHILDREN_PER_RUN


def pinned_spec(
    *,
    tool_ids: list[str],
    mcp: list[str] = (),  # type: ignore[assignment]
    skills: list[str] = (),  # type: ignore[assignment]
    kb: list[str] = ("kb-1",),  # type: ignore[assignment]
    apps: list[str] = (),  # type: ignore[assignment]
    agent: bool = True,
    sandbox: str = "none",
    chat: dict[str, Any] | None = None,
    strict_scope: bool = True,
    executor: dict[str, Any] | None = None,
    whole: list[str] = (),  # type: ignore[assignment]
    max_children: int = 0,
) -> RunSpec:
    """What Node pins; with ``executor``, the v3 spec that carries it (else v2, as the goldens were recorded).
    ``whole``: MCP servers granted whole (D-LAZY-2, ``tools: at_first_use``). ``max_children``: a deep turn of an
    agent that sets none is pinned ``DEEP_CHILDREN`` (Node's ``modeMaxChildren``); 0 is a run that delegates nothing."""
    extension: dict[str, Any] = {
        "model": {"modelKey": "model-1", "modelName": "gpt-x"},
        "knowledge": {"apps": list(apps), "kb": list(kb), "strictScope": strict_scope},
        "mcp": [{"instanceId": i, "manifestDigest": DIGEST} for i in mcp]
        + [{"instanceId": i, "manifestDigest": DIGEST, "tools": "at_first_use"} for i in whole],
        "skills": [{"name": s, "bundleDigest": DIGEST} for s in skills],
        "mounts": [],
        "via": "session",
    }
    if agent:
        extension["agent"] = {"agentKey": AGENT, "definitionVersion": 7, "mode": "quick"}
    if chat is not None:
        extension["chat"] = chat
    return validate_contract(
        RunSpec,
        {
            "schemaVersion": 2 if executor is None else 3,
            **({} if executor is None else {"executor": executor}),
            "harnessMajor": 2,
            "foldVersion": 1,
            "runKind": "interactive",
            "principalRef": f"p_{ACTOR}",
            "model": {"policy": [{"provider": "openai", "model": "gpt-x"}], "promptVersion": "pipeshub-agent-2026-09"},
            "grants": {
                "tools": [{"toolId": t, "descriptorDigest": DIGEST} for t in sorted(tool_ids)],
                "sandboxProfile": sandbox,
                "egress": "none",
            },
            "budgets": {
                "maxTurns": 15,
                "maxTokens": 1_000_000,
                "maxCostUsd": 5,
                "activeTimeS": 3600,
                "calendarDeadline": "2027-01-01T00:00:00Z",
                "maxChildren": max_children,
            },
            "capabilitiesDigest": DIGEST,
            "extensions": {"x.pipeshub": extension},
        },
    )


def claim(
    spec: RunSpec,
    *,
    query: str = "find the bug",
    run_id: str = "run-1",
    chat_mode: str = "quick",
    attachments: list[dict[str, Any]] | None = None,
    previous: list[dict[str, Any]] | None = None,
    filters: dict[str, Any] | None = None,
    timezone: str | None = "UTC",
    caller: dict[str, Any] | None = None,
    current_time: str | None = None,
    created_at: int = 0,
    trigger: dict[str, Any] | None = None,
    locale: str | None = None,
    keyword_search_budget: str | None = None,
) -> NodeClaim:
    run = HarnessRun.model_validate(
        {
            "runId": run_id,
            "orgId": ORG,
            "sessionId": "ses-1",
            "actorId": ACTOR,
            **({"agentKey": AGENT} if "agent" in ((spec.extensions or {}).get("x.pipeshub") or {}) else {}),
            "userMessageKey": "m1",
            "idempotencyKey": "k1",
            "payloadHash": "sha256:x",
            "harnessMajor": 2,
            "profile": "inline",
            "status": "running",
            "outcome": None,
            "revision": 1,
            "fence": 1,
            "attempts": 1,
            "attemptId": "att-1",
            "workerId": "w1",
            "leaseExpiresAt": 5,
            "absoluteDeadlineAt": 10,
            "checkpoint": None,
            "input": {
                "query": query,
                "recordIds": [],
                "filters": filters or {},
                "attachments": attachments or [],
                "previousConversations": previous or [],
                "model": {"chatMode": chat_mode},
                **({"timezone": timezone} if timezone is not None else {}),
                **({"caller": caller} if caller is not None else {}),
                **({"currentTime": current_time} if current_time is not None else {}),
                **({"locale": locale} if locale is not None else {}),
                **({"keywordSearchBudget": keyword_search_budget} if keyword_search_budget is not None else {}),
            },
            "createdAt": created_at,
            "updatedAt": 0,
        }
    )
    return NodeClaim(
        run_id=run_id,
        org_id=ORG,
        session_id="ses-1",
        stream_id="ses-1",
        attempt_id="att-1",
        attempt=1,
        fence=1,
        lease_ms=30_000,
        last_seq=3,
        spec=spec,
        input_text=query,
        capabilities={"journal.durable": True},
        run=run,
        trigger_context=trigger,
    )


class FakeBlobs:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    async def save_binary_to_storage(
        self, org_id: str, record_id: str, file_name: str, extension: str, content_type: str, binary_data: bytes
    ) -> tuple[str, int]:
        self.objects[record_id] = binary_data
        return record_id, len(binary_data)

    async def read_binary_from_storage(self, org_id: str, document_id: str, *, version: int | None = None) -> bytes:
        return self.objects[document_id]


class Services:
    """``RunServices`` over fakes; ``agent`` is what the graph holds now; ``org`` owns the toolset instances."""

    def __init__(self, agent: dict[str, Any] | None = None, *, org: str = ORG) -> None:
        self.agent = agent if agent is not None else agent_doc()
        self.org = org
        self.permitted = True
        self.blobs = FakeBlobs()
        self._logger = logging.getLogger("assembly.tests")
        self.config = FakeConfigurationService(
            # No admin approval policy: the node a deployment that never set one reads as absent.
            {
                "/services/agentHarness/approvals": None,
                # The org's toolset instances, as the toolsets admin writes them: a credential resolves only for its org.
                "/services/toolset-instances": [{"_id": JIRA, "orgId": org, "toolsetType": "jira"}],
                # An org whose admin set web search up (main #5722: no stored default means web search is off).
                "/services/webSearch": {"providers": [{"provider": "duckduckgo", "isDefault": True, "configuration": {}}]},
            },
            flags={"ENABLE_MCP": True, "ENABLE_SKILLS": False},
            prefixes={
                "/services/toolsets/": lambda _key: {"isAuthenticated": True, "auth": {"token": "secret-jira-token"}}
            },
        )
        self.graph = AsyncMock(spec=IGraphDBProvider)
        # A real graph provider carries the service's config: the record fetch reads its flags through it.
        self.graph.config_service = self.config
        # No Demo connector in the org: the demo-data reads find nothing to exclude.
        self.graph.get_org_apps = AsyncMock(return_value=[])
        self.graph.get_agent = AsyncMock(side_effect=lambda key, org: copy.deepcopy(self.agent) if self.agent else None)
        self.graph.get_user_by_user_id = AsyncMock(
            return_value={"_key": "ukey-1", "userId": ACTOR, "email": "user@corp.example", "fullName": "Uma"}
        )
        self.graph.check_agent_permission = AsyncMock(side_effect=lambda *a: self.permitted)
        # Main's one access check (#4255): the actor reads what a test names here, by virtual record id
        # (attachments are checked by their content id, the client's recordId replaced by the record found).
        self.readable_vrids: dict[str, str] = {}
        self.graph.check_access = AsyncMock(side_effect=self._check_access)
        # Every document the graph answers that is not an agent version: the org's.
        self.org_document: dict[str, Any] | None = {"accountType": "enterprise", "name": "Org"}
        self.graph.get_document = AsyncMock(side_effect=self._document)
        self.graph.get_user_connector_instances = AsyncMock(return_value=[])
        self.retrieval = MagicMock()
        # The knowledge-graph entity store; None, as on a deployment with no entities collection yet.
        self.entity_store: Any = None

    def _document(self, key: str, collection: str, *_: object, **__: object) -> dict[str, Any] | None:
        """Every published version of the agent is the agent as the graph holds it now (OWD-14), so a run reads
        the definition a test set, whatever version it pinned; any other document is the org."""
        if collection == CollectionNames.AGENT_VERSIONS.value:
            prefix = f"{AGENT}-v"
            if self.agent is None or not key.startswith(prefix):
                return None
            doc = version_document(self.org, AGENT, int(key[len(prefix):]), self.agent, published_by=None)
            return {**{k: v for k, v in doc.items() if k != "id"}, "_key": key}
        return copy.deepcopy(self.org_document)

    async def _check_access(
        self, user_key: str, org_id: str, *, node_ids: Any = (), virtual_record_ids: Any = (), **_: Any  # noqa: ANN401
    ) -> AccessCheck:
        readable = {v: r for v, r in self.readable_vrids.items() if v in set(virtual_record_ids)}
        return AccessCheck(node_ids=frozenset(node_ids), records_by_vrid=readable, node_ids_in_scope=frozenset(node_ids))

    def logger(self) -> logging.Logger:
        return self._logger

    def config_service(self) -> Any:  # noqa: ANN401
        return self.config

    async def graph_provider(self) -> Any:  # noqa: ANN401
        return self.graph

    async def entity_vector_store(self, org_id: str | None = None) -> Any:  # noqa: ANN401
        return self.entity_store

    async def retrieval_service(self) -> Any:  # noqa: ANN401
        return self.retrieval

    async def blob_store(self) -> Any:  # noqa: ANN401
        return self.blobs


def model_factory(port: DurableModelPort) -> Any:  # noqa: ANN401
    from tests.unit.agents.adapter.conftest import FakeChatModel

    async def build(run: RunContext, services: object) -> ResolvedModel:
        return ResolvedModel(
            raw_llm=FakeChatModel(),
            provider="openai",
            is_multimodal=False,
            context_length=128_000,
            is_reasoning=False,
            port=port,
        )

    return build


MCP_TOOLS: dict[str, list[MCPToolInfo]] = {
    MCP: [
        MCPToolInfo(
            name="list_prs",
            namespaced_name="mcp_github_list_prs",
            description="List pull requests.",
            input_schema={"type": "object", "properties": {"repo": {"type": "string"}}},
        )
    ]
}


def session_tools(discover: Any) -> Any:  # noqa: ANN401
    """A `discover_tools`-shaped fake as the turn's tool load (`MCPSessionManager.tools`, main #4003)."""

    async def tools(self: Any, server: Any, namespace: str) -> tuple[list[MCPToolInfo], None]:  # noqa: ANN401
        from app.agents.mcp.service import (
            credentials_to_discovery_dict,
            instance_config_from_dict,
        )

        config = instance_config_from_dict(server.instance)
        return await discover(config, credentials_to_discovery_dict(server.instance.get("authMode", ""), server.auth)), None

    return tools


def deployment(*, org: str = ORG) -> ExitStack:
    """Patches the outermost I/O the real loaders reach; ``org`` owns the MCP server instance."""
    from app.agents.tools.factories.registry import ClientFactoryRegistry

    async def discover(
        config: Any, credentials: dict, timeout_seconds: float = 30.0, *, runner: object = None,  # noqa: ANN401
    ) -> list[MCPToolInfo]:
        instance = getattr(config, "instance_id", None) or getattr(config, "id", None) or MCP
        tools = MCP_TOOLS.get(str(instance), MCP_TOOLS.get(MCP, []))
        if not tools:
            raise RuntimeError("connection refused")
        return [tool.model_copy() for tool in tools]

    async def instance(instance_id: str, config_service: object, *_: object) -> dict[str, Any] | None:
        return {
            "_id": instance_id, "orgId": org, "createdBy": ACTOR, "name": "GitHubMCP",
            "typeId": "github", "transport": "sse", "authMode": "none", "createdAt": 0, "updatedAt": 0,
        }

    async def unreachable(self: object) -> NoReturn:
        # Main's client (#4003) dials github's catalog URL; a test's session is faked or fails here, never dials.
        raise MCPRequestNotSentError("Couldn't reach the MCP server")

    stack = ExitStack()
    for target, value in (
        ("app.agents.registry.toolset_registry.get_toolset_registry", ToolsetCatalog),
        ("app.agents.agent_loop.mcp_session.MCPSessionManager.tools", session_tools(discover)),
        ("app.edition_config.get_mcp_instance_resolved", instance),
        ("app.agents.mcp.client.MCPClientManager._start_session", unreachable),
        ("app.agents.mcp.service.resolve_effective_user_auth", AsyncMock(return_value={})),
        ("app.agents.mcp.service.is_effective_auth_authenticated", lambda auth: True),
        ("app.services.artifact_registry.ArtifactRegistryService", MagicMock()),
    ):
        stack.enter_context(patch(target, new=value))
    stack.enter_context(patch.object(ClientFactoryRegistry, "_factories", {"jira": _JiraFactory()}))
    stack.enter_context(patch.object(ClientFactoryRegistry, "_initialized", True))
    # MCP_TOOL_APPROVALS off: the fake server's tool declares no hints, so main's MCP rules (#4003) would ask
    # for every call; these worlds test the run, not those rules (test_sync3_tool_rules covers the durable ask).
    stack.enter_context(patch.dict("os.environ", {
        "PIPESHUB_ENABLE_SKILLS": "false", "PIPESHUB_ENABLE_CODE_EXECUTION": "false", "MCP_TOOL_APPROVALS": "false",
    }))
    return stack


@dataclasses.dataclass(frozen=True)
class _ContentClaim(Claim):
    """An in-memory claim that carries its attempt's content route, as ``NodeJournal``'s do."""

    content: InMemorySealedContent | None = dataclasses.field(default=None, compare=False, repr=False)


class PipesHubInMemoryJournal(InMemoryJournal):
    """Every attempt shares one in-memory content route: a re-claim reads what a crashed attempt wrote."""

    def __init__(self, **kwargs: Any) -> None:  # noqa: ANN401
        super().__init__(**kwargs)
        self.content = InMemorySealedContent()

    async def claim(self, run_id: str | None = None, *, worker_id: str, org_id: str | None = None) -> Claim | None:
        claim = await super().claim(run_id, worker_id=worker_id, org_id=org_id)
        if claim is None:
            return None
        values = {f.name: getattr(claim, f.name) for f in dataclasses.fields(claim)}
        return _ContentClaim(**values, content=self.content)


def pipeshub_journal(**kwargs: object) -> PipesHubInMemoryJournal:
    """An in-memory journal that accepts (and validates) the x.pipeshub events
    the PipesHub presenter journals, as Node's catalog does."""
    from app.harness.contracts.generated.pipeshub.event_catalog import (
        EVENT_MODELS,
        EVENT_WRITERS,
    )

    # A caller's own event types (an engine lane's test presenter) join the catalog's.
    writers = {**EVENT_WRITERS, **cast("dict[str, Any]", kwargs.pop("extension_writers", {}))}
    return PipesHubInMemoryJournal(
        extension_writers=cast("Any", writers),
        extension_models=EVENT_MODELS,
        **kwargs,
    )


def node_claim(attempt: Claim) -> NodeClaim:
    """An in-memory journal's claim with the run snapshot the world admits, as Node's claims carry one."""
    node = claim(attempt.spec, query=attempt.input_text, run_id=attempt.run_id)
    values = {f.name: getattr(attempt, f.name) for f in dataclasses.fields(attempt)}
    return type(node)(**values, run=node.run)


class ClaimAssembler:
    """InMemoryJournal claims carry no run snapshot; attach the one the world admits."""

    def __init__(self, inner: Any) -> None:  # noqa: ANN401
        self._inner = inner

    async def build(self, claim: Claim) -> Any:  # noqa: ANN401
        return await self._inner.build(node_claim(claim))


async def claim_next(journal: InMemoryJournal, run_id: str = "run-1") -> Claim:
    claimed = await journal.claim(run_id, worker_id="w1")
    assert claimed is not None
    return claimed


async def first_use(runtime: Any, *names: str) -> Any:  # noqa: ANN401 - an AssembledRuntime
    """``runtime`` after the run's first use of ``names`` (every granted tool when none is named): an MCP server's
    tools are built when the kernel is about to plan a call of one (D-LAZY-4), never at the claim."""
    if runtime.prepare_calls is not None:
        await runtime.prepare_calls(list(names or runtime.tools.names))
    return runtime


def claim_listing() -> Any:  # noqa: ANN401 - a patch context
    """A run whose MCP servers are listed at the claim: where lazy disclosure is off, the model reaches a tool only
    if it is listed (``disclosure.mcp_at_first_use``), so the claim's listing and its drift checks apply as before."""
    return patch.dict("os.environ", {"PIPESHUB_ENABLE_LAZY_TOOLS": "false"})
