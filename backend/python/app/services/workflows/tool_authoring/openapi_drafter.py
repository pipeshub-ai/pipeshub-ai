"""OpenAPIDrafter — infers ToolDefinitions from an OpenAPI spec.

For each operation in the spec:
- Extracts name, description, parameters
- Infers is_write/is_destructive from HTTP method (PUT/POST/DELETE/PATCH)
- Sets mandatory result projection
- Generates a test snippet
"""
from __future__ import annotations

import logging
import re
from typing import Any

from app.services.workflows.tool_authoring.base import ToolDraftResult, ToolSource

logger = logging.getLogger(__name__)

_WRITE_METHODS = {"post", "put", "patch"}
_DESTRUCTIVE_METHODS = {"delete"}

__all__ = ["OpenAPIDrafter"]


def _snake_case(s: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "_", s)
    return re.sub(r"_+", "_", s).strip("_").lower()


def _infer_params_schema(operation: dict) -> dict[str, Any]:
    """Infer a JSON Schema for tool parameters from an OpenAPI operation."""
    properties: dict = {}
    required: list = []

    for param in operation.get("parameters", []):
        pname = param.get("name", "")
        schema = param.get("schema", {"type": "string"})
        properties[pname] = {
            "type": schema.get("type", "string"),
            "description": param.get("description", ""),
        }
        if param.get("required"):
            required.append(pname)

    # Request body — take the first application/json content
    request_body = operation.get("requestBody", {})
    for content_type, content in request_body.get("content", {}).items():
        if "json" in content_type:
            body_schema = content.get("schema", {})
            for prop_name, prop_schema in body_schema.get("properties", {}).items():
                properties[prop_name] = prop_schema
            required.extend(body_schema.get("required", []))
            break

    return {"type": "object", "properties": properties, "required": list(set(required))}


class OpenAPIDrafter:
    """Drafts ToolDefinitions from an OpenAPI 3.x spec dict."""

    source = ToolSource.OPENAPI

    async def draft(
        self,
        spec: dict[str, Any],
        *,
        org_id: str,
        user_id: str,
    ) -> list[ToolDraftResult]:
        results = []
        paths = spec.get("paths", {})
        api_title = spec.get("info", {}).get("title", "api")

        for path, path_item in paths.items():
            for method, operation in path_item.items():
                if method not in {"get", "post", "put", "patch", "delete"}:
                    continue
                if not isinstance(operation, dict):
                    continue

                op_id = operation.get("operationId", "")
                name = _snake_case(op_id or f"{method}_{path}")
                tool_name = f"{_snake_case(api_title)}/{name}"
                description = operation.get(
                    "summary", operation.get("description", f"{method.upper()} {path}")
                )

                params_schema = _infer_params_schema(operation)
                is_write = method in _WRITE_METHODS
                is_destructive = method in _DESTRUCTIVE_METHODS

                required_params = list(params_schema.get("required", []))[:3]
                example_kwargs = ", ".join(f"{param}=..." for param in required_params)
                test_snippet = f"ctx.tool('{tool_name}', {example_kwargs})"

                results.append(ToolDraftResult(
                    name=tool_name,
                    description=description[:500],
                    source=ToolSource.OPENAPI,
                    parameters_schema=params_schema,
                    is_write=is_write,
                    is_destructive=is_destructive,
                    requires_approval=is_write or is_destructive,
                    test_snippet=test_snippet,
                    metadata={"method": method, "path": path, "operation_id": op_id},
                ))

        return results
