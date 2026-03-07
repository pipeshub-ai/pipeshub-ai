"""
Deep Agent State

Extends ChatState with orchestrator-specific fields while remaining
fully compatible with respond_node for final response generation.
"""

from __future__ import annotations

from logging import Logger
from typing import Any, Dict, List, Optional

from langchain_core.language_models.chat_models import BaseChatModel
from typing_extensions import TypedDict

from app.config.configuration_service import ConfigurationService
from app.modules.agents.qna.chat_state import ChatState, build_initial_state
from app.modules.reranker.reranker import RerankerService
from app.modules.retrieval.retrieval_service import RetrievalService
from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider


class SubAgentTask(TypedDict, total=False):
    """A task assigned to a sub-agent."""
    task_id: str
    description: str
    tools: List[str]
    depends_on: List[str]
    status: str  # "pending" | "running" | "success" | "error" | "skipped"
    result: Optional[Dict[str, Any]]
    error: Optional[str]
    duration_ms: Optional[float]
    domains: List[str]


class DeepAgentState(ChatState, total=False):
    """
    Deep agent state that extends ChatState.

    All ChatState fields are inherited so respond_node works unchanged.
    The additional fields below support orchestrator logic.
    """
    # Orchestrator plan
    task_plan: Optional[Dict[str, Any]]
    sub_agent_tasks: List[SubAgentTask]
    completed_tasks: List[SubAgentTask]

    # Context management
    conversation_summary: Optional[str]
    context_budget_tokens: int

    # Evaluation / iteration
    evaluation: Optional[Dict[str, Any]]
    deep_iteration_count: int
    deep_max_iterations: int

    # Tool caching (persists between graph nodes)
    cached_structured_tools: Optional[List]
    schema_tool_map: Optional[Dict[str, Any]]

    # Sub-agent analyses for respond_node
    sub_agent_analyses: Optional[List[str]]


# ---------------------------------------------------------------------------
# Defaults for deep-agent-specific fields
# ---------------------------------------------------------------------------
_DEEP_DEFAULTS: Dict[str, Any] = {
    "task_plan": None,
    "sub_agent_tasks": [],
    "completed_tasks": [],
    "conversation_summary": None,
    "context_budget_tokens": 16000,
    "evaluation": None,
    "deep_iteration_count": 0,
    "deep_max_iterations": 3,
}


def build_deep_agent_state(
    chat_query: Dict[str, Any],
    user_info: Dict[str, Any],
    llm: BaseChatModel,
    logger: Logger,
    retrieval_service: RetrievalService,
    graph_provider: IGraphDBProvider,
    reranker_service: RerankerService,
    config_service: ConfigurationService,
    org_info: Dict[str, Any] | None = None,
) -> DeepAgentState:
    """
    Build a DeepAgentState by extending the standard ChatState.

    Reuses build_initial_state() for all shared fields and then
    overlays the deep-agent-specific defaults.
    """
    base: Dict[str, Any] = build_initial_state(
        chat_query,
        user_info,
        llm,
        logger,
        retrieval_service,
        graph_provider,
        reranker_service,
        config_service,
        org_info,
        graph_type="deep",
    )

    # Overlay deep-agent fields
    for key, default in _DEEP_DEFAULTS.items():
        if key not in base:
            if isinstance(default, (list, dict)):
                base[key] = type(default)()  # fresh copy
            else:
                base[key] = default

    return base  # type: ignore[return-value]
