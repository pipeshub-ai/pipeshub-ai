"""WorkflowDrafter — registers an existing workflow as a callable tool.

This closes the composition loop: workflows can call other workflows as tools
via ctx.tool("workflows/my_workflow", **inputs).

The generated ToolDefinition wraps workflow_manage(action="run_now") and:
- Uses the workflow's input schema (from WorkflowVersion.ir) as parameter schema
- Sets requires_approval=True if the workflow has any WRITE side-effect steps
- Returns the run result (output_summary) as the tool result
"""
from __future__ import annotations

import logging
from typing import Any

from app.services.workflows.tool_authoring.base import ToolDraftResult, ToolSource

logger = logging.getLogger(__name__)

__all__ = ["WorkflowDrafter"]


class WorkflowDrafter:
    """Drafts a ToolDefinition from an existing Workflow."""

    source = ToolSource.WORKFLOW

    async def draft(
        self,
        spec: dict[str, Any],
        *,
        org_id: str,
        user_id: str,
    ) -> list[ToolDraftResult]:
        """Draft a tool from a workflow spec.

        spec should contain:
            workflow_id: str
            name: str
            description: str
            input_schema: dict (JSON Schema of inputs)
            has_write_steps: bool
        """
        workflow_id = spec.get("workflow_id", "")
        name = spec.get("name", workflow_id)
        description = spec.get("description", f"Run workflow: {name}")
        input_schema = spec.get("input_schema", {"type": "object", "properties": {}})
        has_write = spec.get("has_write_steps", False)

        # Normalize tool name: workflows/snake_case
        tool_name = f"workflows/{name.lower().replace(' ', '_').replace('-', '_')}"

        result = ToolDraftResult(
            name=tool_name,
            description=f"{description}\n\nThis tool runs a PipesHub workflow and returns its output.",
            source=ToolSource.WORKFLOW,
            parameters_schema=input_schema,
            is_write=has_write,
            requires_approval=has_write,
            test_snippet=f"ctx.tool('{tool_name}', **{{'input': 'value'}})",
            metadata={"workflow_id": workflow_id},
        )
        return [result]
