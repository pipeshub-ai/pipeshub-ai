"""The identity a durable run's model calls carry to an LLM gateway (main #4428).

Main binds the caller around a legacy chat's stream (``call_scope`` in the chat and agent routes); every model HTTP
client then tells the gateway who the call is for. A durable attempt runs in the worker, outside any request, so
without this binding its calls would reach the gateway as PipesHub itself: on the service's budget and model list.
The run's actor is the person Node verified when it admitted the run.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.harness.assembly.context import RunContext
    from app.services.model_identity.call_context import ModelCallContext

logger = logging.getLogger(__name__)


async def bind_run_identity(
    run: RunContext,
    *,
    email: str | None = None,
    config_service: Any = None,  # noqa: ANN401 - ConfigurationService
    container: Any = None,  # noqa: ANN401 - a DI container, for the edition's group lookup
) -> ModelCallContext:
    """Binds the run's actor for every model call of this attempt's task, as the pinned Labs stages are bound."""
    from app.services.model_identity.call_context import (
        ModelCallContext,
        bind_call_context,
    )
    from app.services.model_identity.principal import user_principal
    from app.services.model_identity.runtime import get_identity_runtime
    from app.services.model_identity.workload import Workload

    principal = user_principal({"userId": run.actor_id, "email": email}, run.org_id)
    workload = Workload.CHAT if run.chat is not None else Workload.AGENT
    agent_id = run.agent.agent_key if run.agent is not None else None
    try:
        ctx = await get_identity_runtime(config_service, container).context(
            principal, workload, conversation_id=run.session_id, agent_id=agent_id,
        )
    except Exception:
        # As main's call_scope: identity is best effort around the call and never fails it.
        logger.warning("could not prepare the run's model-call identity; using defaults", exc_info=True)
        ctx = ModelCallContext(principal=principal, workload=workload)
    # Held for the rest of the attempt's task, never exited: the task ends with the attempt.
    return bind_call_context(ctx).__enter__()
