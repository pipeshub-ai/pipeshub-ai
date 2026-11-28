"""``KnowledgeService``: knowledge search and record reads as a stateless service API (C3.17).

A call names everything it runs as: who asks (``KnowledgePrincipal``, the actor or the service account the run
pinned), what it may search (``PinnedKnowledge``, the pinned scope), the model it answers (``KnowledgeModel``), and the
session's citation state (``session``: the refs, the passages already shown, the records the session may read). The
service keeps nothing between calls, so any worker answers any call, and the same call gives the same results and
the same ACL whoever routes it: the native loop's ``knowledgegraph__search``/``fetch_record`` and an engine's call
through the tool gateway both come here.

The search and read operations (``knowledge_graph/ops``) read a ``ChatState``-shaped mapping. Each call makes it
from the session and the call: the principal, the scope, the backends and the model are the call's and win over
anything the session holds. A session belongs to one principal and scope (a run's), so for a run's own tools that
changes nothing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from app.core.org_context import bound_org_id

if TYPE_CHECKING:
    from collections.abc import Mapping, MutableMapping

    from app.agent_loop_lib.core.messages import Part
    from app.agent_loop_lib.tools.base import ToolOutput


@dataclass(frozen=True, slots=True)
class KnowledgePrincipal:
    """Who the call searches as. ``user_info`` carries a service account's real caller (``callerUserId``)."""

    org_id: str
    user_id: str
    user_info: Mapping[str, Any] = field(default_factory=dict[str, Any])
    user_email: str = ""
    is_service_account: bool = False


@dataclass(frozen=True, slots=True)
class PinnedKnowledge:
    """What the call may search: the run's pinned filters and the agent's knowledge (``universal``: the built-in
    assistant, whose filters are synthetic and which searches its configured ``apps``/``kb``)."""

    filters: Mapping[str, Any]
    agent_knowledge: tuple[Mapping[str, Any], ...] = ()
    apps: tuple[str, ...] = ()
    kb: tuple[str, ...] = ()
    universal: bool = False
    # Whether the run has knowledge at all, as its pin decided; unset, any source in the scope.
    has_knowledge: bool | None = None


@dataclass(frozen=True, slots=True)
class KnowledgeBackends:
    """The process's backends, through their factories (``IGraphDBProvider``, the retrieval service over
    ``IVectorDBService``); shared by every call."""

    retrieval: Any
    graph: Any
    config_service: Any
    logger: Any = None


@dataclass(frozen=True, slots=True)
class KnowledgeModel:
    """The model the result is for: how many images it reads and how large a read may be."""

    llm: Any = None
    multimodal: bool = False
    multipart_results: bool = False
    context_length: int | None = None


@dataclass(frozen=True, slots=True)
class KnowledgeTurn:
    """What a read is for: the person's question (which blocks of a long record matter), whether it must read
    records whole, and whether the run measures what reached the model (``includeRetrievalContext``)."""

    query: str = ""
    needs_whole_document: bool = False
    include_retrieval_context: bool = False


@dataclass(frozen=True, slots=True)
class KnowledgeCall:
    principal: KnowledgePrincipal
    scope: PinnedKnowledge
    model: KnowledgeModel
    # The session's citation state: read and extended by the call, never its principal or scope.
    session: MutableMapping[str, Any]
    turn: KnowledgeTurn = field(default_factory=KnowledgeTurn)

    @classmethod
    def from_context(cls, context: Any) -> KnowledgeCall:  # noqa: ANN401 - a run's AgentContext
        """The call a run's tool makes from its typed context (``AgentContext``), its tool state as the session."""
        state = context.tool_state
        return cls(
            KnowledgePrincipal(
                org_id=str(context.org_id or ""),
                user_id=str(context.user_id or ""),
                user_info=dict(getattr(context, "user_info", None) or {}),
                user_email=str(getattr(context, "user_email", "") or ""),
                is_service_account=bool(getattr(context, "is_service_account", False)),
            ),
            PinnedKnowledge(
                filters=dict(getattr(context, "filters", None) or state.get("filters") or {}),
                agent_knowledge=tuple(getattr(context, "agent_knowledge", None) or state.get("agent_knowledge") or ()),
                apps=tuple(getattr(context, "apps", None) or state.get("apps") or ()),
                kb=tuple(getattr(context, "kb", None) or state.get("kb") or ()),
                universal=bool(state.get("is_placeholder_agent", False)),
                has_knowledge=bool(getattr(context, "has_knowledge", state.get("has_knowledge", False))),
            ),
            KnowledgeModel(
                llm=getattr(context, "llm", None),
                multimodal=bool(getattr(context, "is_multimodal_llm", False)),
                multipart_results=bool(state.get("supports_multipart_tool_result", False)),
                context_length=getattr(context, "context_length", None),
            ),
            state,
            KnowledgeTurn(
                query=str(getattr(context, "query", "") or ""),
                needs_whole_document=bool(getattr(context, "needs_whole_document", False)),
                include_retrieval_context=bool(getattr(context, "include_retrieval_context", False)),
            ),
        )

    @classmethod
    def from_state(cls, state: MutableMapping[str, Any], *, context_length: int | None = None) -> KnowledgeCall:
        """The call a run's tool makes: its pinned principal and scope, and its citation state as the session."""
        return cls(
            KnowledgePrincipal(
                org_id=bound_org_id() or "",
                user_id=str(state.get("user_id") or ""),
                user_info=dict(state.get("user_info") or {}),
                user_email=str(state.get("user_email") or ""),
                is_service_account=bool(state.get("is_service_account", False)),
            ),
            PinnedKnowledge(
                filters=dict(state.get("filters") or {}),
                agent_knowledge=tuple(state.get("agent_knowledge") or ()),
                apps=tuple(state.get("apps") or ()),
                kb=tuple(state.get("kb") or ()),
                universal=bool(state.get("is_placeholder_agent", False)),
                has_knowledge=bool(state.get("has_knowledge", False)),
            ),
            KnowledgeModel(
                llm=state.get("llm"),
                multimodal=bool(state.get("is_multimodal_llm", False)),
                multipart_results=bool(state.get("supports_multipart_tool_result", False)),
                context_length=context_length,
            ),
            state,
            KnowledgeTurn(
                query=str(state.get("query") or ""),
                needs_whole_document=bool(state.get("needs_whole_document", False)),
                include_retrieval_context=bool(state.get("include_retrieval_context", False)),
            ),
        )


@dataclass
class _ReadContext:
    """What a record read takes from its run (``execute_fetch_record``'s ``context``), from the call alone."""

    tool_state: MutableMapping[str, Any]
    org_id: str
    user_id: str
    user_info: Mapping[str, Any]
    graph_provider: Any
    retrieval_service: Any
    is_multimodal_llm: bool
    context_length: int | None
    query: str
    needs_whole_document: bool
    include_retrieval_context: bool
    full_records_fetched: set[str]


@dataclass(frozen=True, slots=True)
class SearchRequest:
    query: str | None
    source_ids: list[str] | None = None
    created_after: str | None = None
    created_before: str | None = None
    modified_after: str | None = None
    modified_before: str | None = None
    entity_ids: list[str] | None = None
    response_format: str | None = None


@dataclass(frozen=True, slots=True)
class FetchRequest:
    record_ids: list[str]
    reason: str
    start_block: int = 0
    max_blocks: int | None = None
    start_block_sent: bool = False
    query: str | None = None


class KnowledgeService:
    def __init__(self, backends: KnowledgeBackends) -> None:
        self._backends = backends

    def _fixed(self, call: KnowledgeCall) -> dict[str, Any]:
        principal, scope, model, backends = call.principal, call.scope, call.model, self._backends
        fixed: dict[str, Any] = {
            "org_id": principal.org_id,
            "user_id": principal.user_id,
            "user_email": principal.user_email,
            "user_info": dict(principal.user_info),
            "is_service_account": principal.is_service_account,
            "filters": dict(scope.filters),
            "agent_knowledge": [dict(k) for k in scope.agent_knowledge],
            "apps": list(scope.apps),
            "kb": list(scope.kb),
            "has_knowledge": (
                scope.has_knowledge
                if scope.has_knowledge is not None
                else bool(scope.agent_knowledge or scope.apps or scope.kb or scope.universal)
            ),
            "is_placeholder_agent": scope.universal,
            "retrieval_service": backends.retrieval,
            "graph_provider": backends.graph,
            "config_service": backends.config_service,
            "llm": model.llm,
            "is_multimodal_llm": model.multimodal,
            "supports_multipart_tool_result": model.multipart_results,
        }
        if backends.logger is not None:
            fixed["logger"] = backends.logger
        return fixed

    def _state(self, call: KnowledgeCall) -> MutableMapping[str, Any]:
        """The session, holding the call's principal, scope, backends and model: a key the session holds with
        another value takes the call's. The operations write their citation state straight into it, as calls
        running side by side (the model's parallel searches) must see each other's refs at once."""
        session = call.session
        for key, value in self._fixed(call).items():
            if key not in session or session[key] != value:
                session[key] = value
        return session

    async def search(self, call: KnowledgeCall, request: SearchRequest) -> str | list[Part]:
        from app.agents.actions.knowledge_graph.ops.search import execute_search

        state = self._state(call)
        return await execute_search(
            state,  # type: ignore[arg-type]  # a ChatState-shaped mapping
            query=request.query,
            source_ids=request.source_ids,
            created_after=request.created_after,
            created_before=request.created_before,
            modified_after=request.modified_after,
            modified_before=request.modified_before,
            entity_ids=request.entity_ids,
            response_format=request.response_format,
        )

    async def fetch(self, call: KnowledgeCall, request: FetchRequest) -> ToolOutput:
        """Reads records the session may read; ``full_records_fetched`` in the session names those read whole."""
        from app.agents.actions.knowledge_graph.ops.fetch import execute_fetch_record

        state = self._state(call)
        fetched = state.get("full_records_fetched")
        if not isinstance(fetched, set):
            fetched = state["full_records_fetched"] = set()
        context = _ReadContext(
            tool_state=state,
            org_id=call.principal.org_id,
            user_id=call.principal.user_id,
            user_info=dict(call.principal.user_info),
            graph_provider=self._backends.graph,
            retrieval_service=self._backends.retrieval,
            is_multimodal_llm=call.model.multimodal,
            context_length=call.model.context_length,
            query=call.turn.query,
            needs_whole_document=call.turn.needs_whole_document,
            include_retrieval_context=call.turn.include_retrieval_context,
            full_records_fetched=fetched,
        )
        records = state.get("virtual_record_id_to_result")
        if not isinstance(records, dict):
            records = state["virtual_record_id_to_result"] = {}
        # The session's one mapper, in the session before the read awaits anything: reads the model runs side by
        # side must number their blocks in it. Each read making its own would number both from ref1, and the
        # last one stored would turn the other's refs into citations of its own blocks.
        mapper_in = state.get("citation_ref_mapper")
        if mapper_in is None:
            from app.utils.chat_helpers import CitationRefMapper

            mapper_in = state["citation_ref_mapper"] = CitationRefMapper()
        output, mapper = await execute_fetch_record(
            context=context,  # type: ignore[arg-type]  # the attributes a read takes from its run
            virtual_records=records,
            citation_ref_mapper=mapper_in,
            record_ids=request.record_ids,
            reason=request.reason,
            start_block=request.start_block,
            max_blocks=request.max_blocks,
            start_block_sent=request.start_block_sent,
            query=request.query,
        )
        if mapper is not mapper_in:
            state["citation_ref_mapper"] = mapper
        return output


def backends_of(source: Any) -> KnowledgeBackends:  # noqa: ANN401 - a run's state mapping or its context
    """The backends a run was built with (the factories' clients it already holds), from its state or its context."""

    def read(key: str) -> Any:  # noqa: ANN401
        if isinstance(source, dict):
            return source.get(key)
        return getattr(source, key, None)

    return KnowledgeBackends(
        retrieval=read("retrieval_service"),
        graph=read("graph_provider"),
        config_service=read("config_service"),
        logger=read("logger"),
    )


__all__ = [
    "FetchRequest",
    "KnowledgeBackends",
    "KnowledgeCall",
    "KnowledgeModel",
    "KnowledgePrincipal",
    "PinnedKnowledge",
    "KnowledgeService",
    "KnowledgeTurn",
    "SearchRequest",
    "backends_of",
]
