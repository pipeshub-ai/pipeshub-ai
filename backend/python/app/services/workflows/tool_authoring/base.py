"""Tool authoring base types.

A 'drafter' takes a source (OpenAPI spec, connector config, workflow definition,
agent config, etc.) and produces a ToolDefinition that can be registered in
ToolRegistry.

Key design constraints:
- Mandatory result projection: all tools must truncate to a safe size
- Write/destructive tools ALWAYS require human approval (via ctx.request_approval)
- Sandboxed test-invoke before registration
- Review gate: write tools require explicit confirmation
"""
from __future__ import annotations

import json
from enum import Enum
from typing import Any, Protocol

from pydantic import BaseModel, model_validator


class ToolSource(str, Enum):
    """Where the tool definition originates."""
    OPENAPI = "openapi"
    CONNECTOR = "connector"
    MCP = "mcp"
    WORKFLOW = "workflow"    # a workflow registered as a callable tool
    AGENT = "agent"          # an agent exposed as a tool
    KNOWLEDGE = "knowledge"  # knowledge base search as a tool
    CODE = "code"            # raw Python function


class ToolDraftResult(BaseModel):
    """Result of drafting a tool definition."""
    name: str
    description: str
    source: ToolSource
    parameters_schema: dict[str, Any]
    is_destructive: bool = False
    is_write: bool = False
    requires_approval: bool = False  # auto-set to True if is_write or is_destructive
    test_snippet: str | None = None  # suggested test call
    review_notes: list[str] = []
    metadata: dict[str, Any] = {}

    @model_validator(mode="after")
    def _set_requires_approval(self) -> "ToolDraftResult":
        if self.is_write or self.is_destructive:
            self.requires_approval = True
        return self


class IToolDrafter(Protocol):
    """Strategy for drafting tools from a specific source type."""
    source: ToolSource

    async def draft(self, spec: dict[str, Any], *, org_id: str, user_id: str) -> list[ToolDraftResult]: ...


_MAX_RESULT_SIZE_CHARS = 8000  # Mandatory result projection limit


def truncate_result(result: Any, *, max_chars: int = _MAX_RESULT_SIZE_CHARS) -> Any:
    """Truncate a tool result to the max size. Applied by all tool wrappers."""
    if isinstance(result, str) and len(result) > max_chars:
        return result[:max_chars] + f"\n[TRUNCATED: {len(result)} chars total]"
    if isinstance(result, (dict, list)):
        serialized = json.dumps(result)
        if len(serialized) > max_chars:
            return {"_truncated": True, "preview": serialized[:max_chars], "total_chars": len(serialized)}
    return result
