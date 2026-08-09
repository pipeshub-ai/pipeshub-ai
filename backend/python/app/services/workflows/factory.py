"""WorkflowService factory — wire the workflow layer into an existing DI container.

Intended to be called from containers/query.py during service startup.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.services.tasks.application.engine import TaskEngine
    from app.services.workflows.application.workflow_service import WorkflowService
    from app.services.workflows.codegen.agent import WorkflowBuilderAgent
    from app.services.workflows.interface.code_store import ICodeStore
    from app.services.workflows.interface.conversation_writer import IConversationWriter
    from app.services.workflows.interface.journal import IExecutionJournal
    from app.services.workflows.interface.version_store import IWorkflowVersionStore

logger = logging.getLogger(__name__)


def build_workflow_service(
    *,
    task_engine: "TaskEngine",
    version_store: "IWorkflowVersionStore | Any | None" = None,
    code_store: "ICodeStore | Any | None" = None,
    journal: "IExecutionJournal | Any | None" = None,
    builder_agent: "WorkflowBuilderAgent | None" = None,
    llm_caller: Any | None = None,
    graph_provider: Any | None = None,
    conversation_writer: "IConversationWriter | None" = None,
) -> "WorkflowService":
    """Wire a WorkflowService.

    Pass `llm_caller` (or a ready `builder_agent`) to enable the REST edit
    path; without one, `POST /workflows/{id}/edit` has no code generator and
    reports that rather than failing as "not found".
    """
    from app.services.workflows.application.workflow_service import WorkflowService

    if builder_agent is None and llm_caller is not None:
        from app.services.workflows.codegen.agent import WorkflowBuilderAgent

        builder_agent = WorkflowBuilderAgent(llm_caller=llm_caller)

    return WorkflowService(
        task_engine=task_engine,
        version_store=version_store,
        code_store=code_store,
        journal=journal,
        builder_agent=builder_agent,
        graph_provider=graph_provider,
        conversation_writer=conversation_writer,
        logger=logging.getLogger("app.services.workflows"),
    )
