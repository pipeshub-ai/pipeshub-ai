"""The ControlPlane audit middleware (`hooks/middleware/builtin/logging.py`): what ran and how it ended,
never the tool's arguments or result, nor the run's answer or error text."""

from __future__ import annotations

import logging
from uuid import uuid4

import pytest

from app.agent_loop_lib.core.types import AgentResult, Goal
from app.agent_loop_lib.hooks.middleware.builtin import logging as audit
from app.agent_loop_lib.hooks.middleware.context import (
    AgentLifecycleContext,
    ToolCallContext,
    ToolResultContext,
)
from app.agent_loop_lib.tools.base import ToolOutput

pytestmark = pytest.mark.asyncio

_SENTINEL = "audit-sentinel-31"


async def _next() -> None:
    return None


async def test_no_arguments_results_or_answers(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)

    await audit.audit_log_pre_tool()(ToolCallContext(tool_path="/toolsets/jira/create", tool_input={"title": _SENTINEL}), _next)
    await audit.audit_log_post_tool()(
        ToolResultContext(tool_path="/toolsets/jira/create", tool_use_id=uuid4(), tool_response=ToolOutput(success=False, error=_SENTINEL)), _next,
    )
    await audit.audit_log_post_agent()(
        AgentLifecycleContext(result=AgentResult(goal=Goal(description="g"), success=False, output=_SENTINEL, error=_SENTINEL, end_event="agent_failed")),
        _next,
    )

    assert len(caplog.records) == 3 and _SENTINEL not in caplog.text
    assert caplog.records[-1].getMessage() == "post_agent: success=False end_event=agent_failed"
