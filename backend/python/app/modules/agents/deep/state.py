"""
Deep Agent State

Extends ChatState with orchestrator-specific fields while remaining
fully compatible with respond_node for final response generation.
"""

from __future__ import annotations

import logging
import os
from logging import Logger
from typing import Any, Dict, List, Optional

from langchain_core.language_models.chat_models import BaseChatModel
from typing_extensions import TypedDict

from app.config.configuration_service import ConfigurationService
from app.modules.agents.qna.chat_state import ChatState, build_initial_state
from app.modules.reranker.reranker import RerankerService
from app.modules.retrieval.retrieval_service import RetrievalService
from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

_logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Opik tracer for deep agent LLM calls (shared across all deep agent modules)
# ---------------------------------------------------------------------------
_opik_tracer = None
_opik_api_key = os.getenv("OPIK_API_KEY")
_opik_workspace = os.getenv("OPIK_WORKSPACE")
if _opik_api_key and _opik_workspace:
    try:
        from opik.integrations.langchain import OpikTracer
        _opik_tracer = OpikTracer()
        _logger.info("Deep agent Opik tracer initialized")
    except Exception as e:
        _logger.warning("Failed to initialize deep agent Opik tracer: %s", e)


def get_opik_config() -> Dict[str, Any]:
    """Return LLM invoke config with Opik callback, or empty dict if not configured."""
    if _opik_tracer:
        return {"callbacks": [_opik_tracer]}
    return {}


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

    # Complexity-aware execution hints (set by orchestrator)
    complexity: str  # "simple" | "complex" — controls sub-agent execution mode
    batch_strategy: Optional[Dict[str, Any]]
    # Example: {"page_size": 50, "max_pages": 4, "scope_query": "after:2026/03/02"}

    # Multi-step execution (3-level hierarchy)
    multi_step: bool  # If True, sub-agent acts as mini-orchestrator spawning sub-sub-agents
    sub_steps: Optional[List[str]]  # Ordered list of step descriptions (set by orchestrator or LLM)

    # Progressive summarization output (set by complex sub-agent)
    domain_summary: Optional[str]  # Consolidated domain-level summary (markdown)
    batch_summaries: Optional[List[str]]  # Intermediate batch summaries


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

    # Domain summaries from complex tasks (structured, concise)
    domain_summaries: Optional[List[Dict[str, Any]]]

    # Temporary buffer for full retrieval tool outputs (context-efficiency optimization).
    # Populated by _wrap_retrieval_tools_for_context_efficiency in sub_agent.py —
    # the wrapper stores the full result here and returns only a compact summary to
    # the react agent's message history to prevent context length explosions.
    # Consumed (and then popped) by _extract_tool_results in the same call frame.
    _deep_retrieval_buffer: Optional[List]

    critic_approved: Optional[bool]
    critic_feedback: str
    critic_issues: Optional[List[Dict[str, str]]]
    critic_done: bool
    _critic_available_domains: Optional[List[str]]

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
    "domain_summaries": [],
    "sub_agent_analyses": [],
    "_deep_retrieval_buffer": None,
    "critic_approved": None,
    "critic_feedback": "",
    "critic_issues": None,
    "critic_done": False,
    "_critic_available_domains": None,
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
    model_name: str = None,
    model_key: str = None,
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
        model_name,
        model_key,
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
