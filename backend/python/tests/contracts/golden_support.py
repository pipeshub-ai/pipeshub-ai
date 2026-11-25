"""Drives the legacy chat and agent streams end to end for the golden contract.

Only the outermost I/O is faked: the model (`ScriptedTransport` stands in for
`LangChainTransport`), the toolset catalog (the real `InternalTools` and
`ArtifactManager` plus `GoldenDocs` below), the artifact registry, blob-backed
retrieval, and the graph / config stores. `PipesHubAgentFactory`, hooks, the AG-UI emitter, the transcript
collector, `AnswerFinalizer` and the SSE bridges all run for real.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import uuid
from contextlib import ExitStack
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import Request

from app.agent_loop_lib.core.streaming import StreamCompleteEvent, ToolCallDeltaEvent
from app.agent_loop_lib.tools.base import ParameterType, Tag, ToolParameter
from app.agent_loop_lib.tools.decorators import tool
from app.agents.actions.artifacts.artifacts import ArtifactManager
from app.agents.actions.internal_tools.intrim_tools import InternalTools
from app.agents.actions.util.live_record import LiveCitations, live_file_record
from app.agents.actions.util.parse_file import FileContentParser
from app.agents.agent_loop.cancellation.in_process import (
    InProcessRunCancellationRegistry,
)
from app.agents.agent_loop.cancellation.registry import RunOwner
from app.config.constants.arangodb import Connectors
from app.core import org_context
from app.models.blocks import Block, BlocksContainer, BlockType, DataFormat
from app.models.entities import ArtifactType
from app.services.artifact_registry.models import ArtifactMetadata
from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider
from tests.support.config_service import FakeConfigurationService
from tests.support.record_access import InMemoryRecordAccess
from tests.unit.agents.adapter.support.scripted_transport import ScriptedTransport

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable, Iterable

    from app.agent_loop_lib.core.messages import Message
    from app.agent_loop_lib.core.streaming import StreamEvent
    from app.agent_loop_lib.core.tool_schema import ToolSchema

GOLDEN_DIR = Path(__file__).parent / "golden"
NODE_UPSTREAM_DIR = (
    Path(__file__).resolve().parents[3] / "nodejs" / "apps" / "tests" / "contracts" / "golden" / "upstream"
)

ORG_ID = "org-golden"
USER_ID = "user-golden"
CONVERSATION_ID = "conv-golden"
RUN_ID = "7a1c0e52-5b1d-4c3e-9f0a-2d4e6b8c0f11"
FRONTEND = "https://pipeshub.test"
CLIENT_NAME = "pipeshub-ai"

_OWNER = RunOwner(user_id=USER_ID, org_id=ORG_ID, conversation_id=CONVERSATION_ID)


def update_goldens() -> bool:
    return os.environ.get("UPDATE_GOLDENS") == "1"


_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_MESSAGE_ID = re.compile(r"msg_[0-9a-f]{12}")
_ISO_TS = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?")


def normalize_sse(raw: str) -> str:
    """Stable placeholders for generated ids and timestamps; nothing else changes.

    Each distinct UUID becomes `uuid-N` in order of first appearance, so a
    frame that reuses an earlier id still visibly points at the same thing.
    """
    seen: dict[str, str] = {}

    def _uuid(match: re.Match[str]) -> str:
        return seen.setdefault(match.group(0), f"uuid-{len(seen) + 1}")

    def _message_id(match: re.Match[str]) -> str:
        return seen.setdefault(match.group(0), f"msg-{len(seen) + 1}")

    text = _UUID.sub(_uuid, raw)
    text = _MESSAGE_ID.sub(_message_id, text)
    return _ISO_TS.sub("<timestamp>", text)


_TOOL_HOOK: dict[str, Callable[[], Awaitable[None]] | None] = {"before_export": None}
_LOOKUP_CALLS: list[str] = []


class GoldenDocs:
    """A small internal toolset whose results never change between runs."""

    def __init__(self, state: dict[str, Any] | None = None) -> None:
        self._state = state

    @tool(
        path="/tools/goldendocs/search_docs",
        short_description="Search the handbook",
        description="Search the company handbook for a topic.",
        parameters=[ToolParameter(name="query", type=ParameterType.STRING, description="Topic", required=True)],
    )
    async def search_docs(self, query: str) -> str:
        return json.dumps({"query": query, "hits": [{"title": "Leave policy", "excerpt": "25 days of paid leave."}]})

    @tool(
        path="/tools/goldendocs/lookup_ticket",
        short_description="Look up a support ticket",
        description="Fetch one support ticket by id.",
        parameters=[ToolParameter(name="ticket_id", type=ParameterType.STRING, description="Ticket id", required=True)],
    )
    async def lookup_ticket(self, ticket_id: str) -> str:
        _LOOKUP_CALLS.append(ticket_id)
        if ticket_id != "T-42":
            raise ValueError(f"Ticket {ticket_id} does not exist")
        return json.dumps({"id": "T-42", "status": "resolved", "owner": "Priya"})

    @tool(
        path="/tools/goldendocs/export_report",
        short_description="Export a report",
        description="Export a long report.",
        parameters=[ToolParameter(name="name", type=ParameterType.STRING, description="Report", required=True)],
    )
    async def export_report(self, name: str) -> str:
        hook = _TOOL_HOOK["before_export"]
        if hook is not None:
            await hook()
        return json.dumps({"name": name, "rows": 3})


class _GoldenFileParser(FileContentParser):
    """The real parser with the file's blocks given (no Docling) and no token count to call out for."""

    def __init__(self) -> None:
        self._logger = logging.getLogger("golden.parser")

    async def parse_raw_to_blocks(self, raw: bytes, ext: str, *, file_name: str = "document") -> BlocksContainer:
        lines = raw.decode("utf-8").splitlines()
        return BlocksContainer(
            blocks=[
                Block(id=f"block-{i}", index=i, type=BlockType.TEXT, format=DataFormat.TXT, data=line)
                for i, line in enumerate(lines)
            ],
            block_groups=[],
        )

    async def check_token_limit(self, *_: object, **__: object) -> bool:
        return True


GOLDEN_FILE = b"The Q3 launch moves to May.\nThe Q3 budget is unchanged."


class GoldenDrive:
    """A file read straight from a connector, as Drive's `get_file_content` reads one (C-19)."""

    def __init__(self, state: dict[str, Any] | None = None) -> None:
        self._state = state

    @tool(
        path="/tools/goldendrive/read_file",
        short_description="Read a Drive file",
        description="Read the content of a Google Drive file.",
        parameters=[ToolParameter(name="file_id", type=ParameterType.STRING, description="File id", required=True)],
        tags=[Tag(key="effect", value="read")],
    )
    async def read_file(self, file_id: str) -> str:
        # A live record names the bound org (WP-118); the legacy and durable worlds run in different orgs, and the
        # golden's records name one, as `_flat`'s do.
        record = live_file_record(
            self._state,
            tool="goldendrive.read_file",
            connector=Connectors.GOOGLE_DRIVE,
            external_record_id=file_id,
            record_name="Q3 plan.txt",
            mime_type="text/plain",
            extension="txt",
        ).model_copy(update={"org_id": ORG_ID})
        _, payload = await _GoldenFileParser().parse(
            record, GOLDEN_FILE, None, None, MagicMock(), citations=LiveCitations(self._state),
        )
        return json.dumps([item.model_dump() for item in payload])


def live_ids() -> Callable[[], uuid.UUID]:
    """Live record ids in order, so a recorded stream and journal are byte-stable."""
    counter = iter(range(1, 1_000_000))
    return lambda: uuid.UUID(int=next(counter))


GOLDEN_DRIVE = {
    "goldendrive": {
        "class": GoldenDrive, "isInternal": True, "provider": "platform", "essential": False, "description": "Google Drive files",
    },
}


class _GoldenToolsetRegistry:
    """What `PipesHubToolLoader` reads from the global toolset registry."""

    def get_all_toolsets(self) -> dict[str, dict[str, Any]]:
        return {
            "internaltools": {
                "class": InternalTools, "isInternal": True, "provider": "platform", "essential": True,
                "description": "Internal tools",
            },
            "artifacts": {
                "class": ArtifactManager, "isInternal": True, "provider": "platform", "essential": True,
                "description": "Artifact tools",
            },
            "goldendocs": {
                "class": GoldenDocs, "isInternal": True, "provider": "platform", "essential": False,
                "description": "Company handbook and tickets",
            },
        }


class _FakeArtifactRegistry:
    """`ArtifactRegistryService` without the graph and blob round trips."""

    def __init__(self, *_: object) -> None:
        pass

    async def promote_to_visible(self, *, actor: object, artifact_id: str) -> ArtifactMetadata:
        return ArtifactMetadata(
            artifact_id=artifact_id, org_id=ORG_ID, conversation_id=CONVERSATION_ID,
            name="q3-revenue.csv", logical_name="q3-revenue", artifact_type=ArtifactType.SPREADSHEET,
            mime_type="text/csv", version=2, size_bytes=2048, document_id="doc-q3",
        )

    async def get_download_url(self, *, actor: object, artifact_id: str) -> str:
        return f"https://storage.test/signed/{artifact_id}?sig=golden"


def _record(vrid: str, rid: str, name: str) -> dict[str, Any]:
    return {
        "virtual_record_id": vrid, "id": rid, "_key": rid, "record_name": name,
        "frontend_url": FRONTEND, "context_metadata": f"Record: {name}",
        "block_containers": {"blocks": [], "block_groups": []},
        "origin": "UPLOAD", "connector_name": "KNOWLEDGE_BASE", "mime_type": "text/plain",
        "record_type": "FILE", "version": 1,
    }


def _flat(vrid: str, index: int, content: str) -> dict[str, Any]:
    return {
        "virtual_record_id": vrid, "block_index": index, "content": content,
        "block_type": "text",
        "metadata": {
            "virtualRecordId": vrid, "recordId": "rec-refund", "recordName": "Refund policy.md",
            "orgId": ORG_ID, "mimeType": "text/markdown", "origin": "UPLOAD", "connector": "KB",
            "recordType": "FILE", "blockIndex": index,
        },
        "score": 0.9 - index / 10,
    }


async def _fake_flattened_results(
    search_results: list[dict[str, Any]], blob_store: object, org_id: str, is_multimodal_llm: bool,
    virtual_record_id_to_result: dict[str, Any], virtual_to_record_map: dict[str, Any], **_: object,
) -> list[dict[str, Any]]:
    virtual_record_id_to_result["vr-refund"] = _record("vr-refund", "rec-refund", "Refund policy.md")
    return [
        _flat("vr-refund", 0, "Customers may request a refund within 30 days of purchase."),
        _flat("vr-refund", 1, "Refunds are paid back to the original payment method."),
    ]


# A provider streams every call's arguments after the turn's text, in call
# order; legacy shows an answer tool's as the answer and re-asserts "calling
# the model" once on the first delta of any other call (C-2).
_ARGUMENT_CHUNK = 12


class GoldenTransport(ScriptedTransport):
    """``ScriptedTransport`` whose stream also carries every tool call's
    argument deltas, chunked as a provider sends them."""

    async def stream(
        self,
        messages: list[Message],
        tools: list[ToolSchema] | None = None,
        system: str | None = None,
        model: str | None = None,
        thinking_budget: int | None = None,
        effort: str | None = None,
        system_blocks: list[str] | None = None,
        *,
        tool_choice: object = None,
    ) -> AsyncIterator[StreamEvent]:
        peeked = self._script[self._index] if self._index < len(self._script) else None
        calls = (peeked.message.tool_calls or []) if peeked is not None and peeked.message is not None else []
        recorded = len(self.calls)
        async for event in super().stream(
            messages, tools=tools, system=system, model=model,
            thinking_budget=thinking_budget, effort=effort, system_blocks=system_blocks,
            tool_choice=tool_choice,  # type: ignore[arg-type]
        ):
            if len(self.calls) > recorded and self.calls[recorded]["messages"] is messages:
                # A request as it was sent: legacy's hooks go on editing these
                # message objects in place after the call (the images they inject).
                self.calls[recorded]["messages"] = [m.model_copy(deep=True) for m in messages]
            if isinstance(event, StreamCompleteEvent):
                for index, call in enumerate(calls):
                    raw = json.dumps(call.arguments)
                    for start in range(0, len(raw), _ARGUMENT_CHUNK):
                        yield ToolCallDeltaEvent(
                            index=index,
                            id=call.id if start == 0 else None,
                            name=call.name if start == 0 else None,
                            arguments_delta=raw[start : start + _ARGUMENT_CHUNK],
                        )
            yield event


async def _yield_once() -> None:
    """A tool that waits on I/O lets the batch's other calls run meanwhile."""
    await asyncio.sleep(0)


@dataclass
class Scenario:
    name: str
    route: str  # "chat" -> askAIStream, "agent" -> run_agent_loop_stream
    chat_mode: str
    query: str
    script: Callable[[ScriptedTransport], None]
    cancel_during_export: bool = False
    llm_unavailable: bool = False
    extra_body: dict[str, Any] = field(default_factory=dict)
    # Deployment switches the scenario runs under (e.g. the final_answer tool).
    env: dict[str, str] = field(default_factory=dict)
    # The export tool waits on I/O before it answers.
    export_yields: bool = False
    # Toolsets the deployment knows but the agent does not have attached.
    unattached_toolsets: dict[str, dict[str, Any]] = field(default_factory=dict)
    # Toolsets beyond the golden catalog that the agent has.
    toolsets: dict[str, dict[str, Any]] = field(default_factory=dict)

    def toolset_registry(self) -> type[_GoldenToolsetRegistry]:
        extra = {**self.unattached_toolsets, **self.toolsets}
        if not extra:
            return _GoldenToolsetRegistry

        class _WithUnattached(_GoldenToolsetRegistry):
            def get_all_toolsets(self) -> dict[str, dict[str, Any]]:
                return {**super().get_all_toolsets(), **extra}

        return _WithUnattached

    def tool_hook(self, stop: Callable[[], Awaitable[None]]) -> Callable[[], Awaitable[None]] | None:
        if self.cancel_during_export:
            return stop
        return _yield_once if self.export_yields else None


def _mock_request(body: dict[str, Any]) -> MagicMock:
    request = MagicMock(spec=Request)
    request.state.user = {"orgId": ORG_ID, "userId": USER_ID, "email": "user@corp.example"}
    request.query_params = {"sendUserInfo": True}
    request.headers = {"client-name": CLIENT_NAME}
    request.json = AsyncMock(return_value=body)
    request.app.container.logger.return_value = logging.getLogger("golden.chat")
    return request


GOLDEN_RECORD_IDS = ("rec-refund", "rec-policy")
# Each golden record's content id: the knowledge hit and the attached policy (main #4255 checks both by it).
GOLDEN_VRIDS = {"rec-refund": "vr-refund", "rec-policy": "vr-policy"}
# The replay rig's fixture knowledge source: every non-golden record lives in it, as the agent's scope expects.
KNOWLEDGE_SOURCE_ID = "kb-eval"


def owning(access: InMemoryRecordAccess, user_id: str, org_id: str, record_ids: Iterable[str]) -> InMemoryRecordAccess:
    """``access`` with ``user_id`` owning ``record_ids``, the goldens' with their content ids, and the fixture
    knowledge source they live in (which navigation opens)."""
    access.readable(user_id, org_id, KNOWLEDGE_SOURCE_ID, connectorId=KNOWLEDGE_SOURCE_ID)
    for rid in record_ids:
        if rid in GOLDEN_VRIDS:
            access.readable(user_id, org_id, rid, virtualRecordId=GOLDEN_VRIDS[rid])
        else:
            access.readable(user_id, org_id, rid, connectorId=KNOWLEDGE_SOURCE_ID)
    return access


def _graph_provider(record_ids: Iterable[str] = GOLDEN_RECORD_IDS) -> AsyncMock:
    """The fixture's user owns ``record_ids``: by default the goldens' (the knowledge hit and the attached policy)."""
    graph = AsyncMock(spec=IGraphDBProvider)
    graph.get_user_connector_instances.return_value = []
    # As a real provider: the service's config (the record fetch reads its flags through it), no Demo connector.
    graph.config_service = _config_service()
    graph.get_org_apps.return_value = []
    answer_people_lookups(graph, USER_ID)
    return owning(InMemoryRecordAccess(), USER_ID, ORG_ID, record_ids).serve(graph)


def answer_people_lookups(graph: Any, user_key: str) -> None:  # noqa: ANN401 - a provider double
    """The people tools' reads (main's ``find_records_by_person``): the actor reaches no connector app and the
    fixture names no colleague, so a person lookup answers that no readable record links them."""
    graph.get_entity_access_context = AsyncMock(
        return_value={"user_key": user_key, "apps": [], "record_group_ids": []}
    )
    graph.find_users = AsyncMock(return_value=[])


def _config_service() -> FakeConfigurationService:
    return FakeConfigurationService()


LLM_CONFIG = {"provider": "openai", "isMultimodal": False, "contextLength": 128000}


def llm_for_chat(scenario: Scenario) -> AsyncMock:
    """The route's model lookup: the org's model, or none when the scenario says so."""
    return AsyncMock(return_value=None if scenario.llm_unavailable else (MagicMock(), LLM_CONFIG, {}))


def prefetch_patches() -> list[tuple[str, object]]:
    """The prefetch's blob-backed steps, answered with the refund policy's blocks."""
    from tests.support.knowledge_builder import builder_patches

    return builder_patches("app.agents.chat_modes.prefetch.KnowledgeContextBuilder", _fake_flattened_results)


async def record_stream(
    scenario: Scenario,
    transports: list[ScriptedTransport] | None = None,
    *,
    history: list[dict[str, Any]] | None = None,
    transport: ScriptedTransport | None = None,
) -> str:
    """Runs one scenario and returns the raw SSE text the Python service sent.
    ``transports`` receives the scripted model, whose ``calls`` are the requests.
    ``history``: the conversation's earlier turns as Node sends them.
    ``transport``: a scripted model of the caller's (deep mode routes each
    agent's requests to its own script); else the scenario scripts a golden one."""
    from app.agents.agent_loop.stream_bridge import run_agent_loop_stream
    from app.api.routes.chatbot import askAIStream

    if transport is None:
        transport = GoldenTransport()
        scenario.script(transport)
    if transports is not None:
        transports.append(transport)
    registry = InProcessRunCancellationRegistry()
    _LOOKUP_CALLS.clear()

    async def _stop_button() -> None:
        await registry.cancel(RUN_ID, _OWNER)

    _TOOL_HOOK["before_export"] = scenario.tool_hook(_stop_button)
    retrieval = AsyncMock()
    retrieval.search_with_filters.return_value = {
        "status_code": 200, "searchResults": [{"metadata": {"virtualRecordId": "vr-refund"}}],
        "virtual_to_record_map": {},
    }

    with ExitStack() as stack:
        # The legacy routes run behind the auth middleware, which binds the token's org in a request task of its own;
        # the recording calls below it, in a test task a durable run before it may have bound to its own org.
        stack.callback(org_context.pop, org_context.push(None))
        stack.enter_context(org_context.bind(org_context.OrgContext(ORG_ID, USER_ID, "user", "jwt", "-")))
        for target, value in (
            ("app.agents.agent_loop.factory.LangChainTransport", lambda *a, **k: transport),
            ("app.agents.registry.toolset_registry.get_toolset_registry", scenario.toolset_registry()),
            ("app.api.routes.chatbot.get_llm_for_chat", llm_for_chat(scenario)),
            ("app.services.artifact_registry.ArtifactRegistryService", _FakeArtifactRegistry),
            ("app.models.citation_source.uuid4", live_ids()),
            *prefetch_patches(),
        ):
            stack.enter_context(patch(target, new=value))
        for key, value in (
            ("PIPESHUB_ENABLE_CODE_EXECUTION", "false"),
            ("PIPESHUB_ENABLE_SKILLS", "false"),
            ("PIPESHUB_AGENT_TRANSPORT", "langchain"),
            *scenario.env.items(),
        ):
            stack.enter_context(patch.dict(os.environ, {key: value}))

        if scenario.route == "chat":
            body = {
                "query": scenario.query, "chatMode": scenario.chat_mode, "protocol": "agui",
                "runId": RUN_ID, "conversationId": CONVERSATION_ID, **scenario.extra_body,
                **({"previousConversations": history} if history else {}),
            }
            response = await askAIStream(
                request=_mock_request(body), retrieval_service=retrieval,
                graph_provider=_graph_provider(), config_service=_config_service(),
                cancellation_registry=registry,
            )
            chunks = [chunk async for chunk in response.body_iterator]
        else:
            query_info = {
                "query": scenario.query, "chatMode": scenario.chat_mode, "runId": RUN_ID,
                "conversationId": CONVERSATION_ID, "agentToolsets": [], **scenario.extra_body,
                **({"previous_conversations": history} if history else {}),
            }
            user_info = {"userId": USER_ID, "orgId": ORG_ID, "email": "user@corp.example"}
            chunks = [
                chunk async for chunk in run_agent_loop_stream(
                    query_info, user_info, MagicMock(), logging.getLogger("golden.agent"),
                    retrieval, _graph_provider(), None, _config_service(),
                    protocol="agui", model_name="scripted-model", model_key="golden-model",
                    llm_provider="openai", client_name=CLIENT_NAME, cancellation_registry=registry,
                )
            ]
    _TOOL_HOOK["before_export"] = None
    return "".join(chunks)
