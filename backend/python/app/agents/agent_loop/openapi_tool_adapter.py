"""`OpenAPIToolAdapter`: one API toolset operation as an agent-loop `Tool`.

The schema the model fills is the one compiled from the org's spec; a call goes through
the toolset's executor, which already holds the caller's credential for this request.
"""
from __future__ import annotations

import copy
import json
import logging
from typing import TYPE_CHECKING, Any

from app.agent_loop_lib.tools.base import Tool, ToolOutput
from app.agents.actions.util.tool_summaries import first_line, inline_args
from app.agents.agent_loop.tool_adapter import (
    SchemaShallowValidationMixin,
    _params_from_schema,
    resolve_json_schema_refs,
)
from app.agents.openapi.auth import ApiToolsetAuthError
from app.agents.openapi.executor import ApiCallError
from app.agents.openapi.request_builder import RequestBuildError

if TYPE_CHECKING:
    from app.agent_loop_lib.core.types import ToolResult
    from app.agent_loop_lib.tools.base import ToolParameter
    from app.agents.openapi.executor import ApiResponse, OpenAPIHttpExecutor
    from app.agents.openapi.models import ApiToolsetInstance, CompiledOperation

logger = logging.getLogger(__name__)

__all__ = ["OpenAPIToolAdapter"]

_SHORT_DESCRIPTION_MAX_LEN = 160
# How much of an error answer the model sees: enough to name the field it got wrong.
_MAX_ERROR_BODY_CHARS = 2000
_MAX_SUMMARY_CHARS = 200


def connect_hint(instance: ApiToolsetInstance) -> str:
    """Who can connect `instance`: a shared credential is only an administrator's to store."""
    if instance.use_shared_credential:
        return "ask an administrator to connect it under Workspace → Actions"
    return "connect it under Workspace → Your actions"


class OpenAPIToolAdapter(SchemaShallowValidationMixin, Tool):
    def __init__(
        self,
        instance: ApiToolsetInstance,
        operation: CompiledOperation,
        executor: OpenAPIHttpExecutor,
        *,
        name: str,
    ) -> None:
        self._instance = instance
        self._operation = operation
        self._executor = executor
        self._name = name
        self._parameters: list[ToolParameter] | None = None
        self._raw_input_schema: dict[str, Any] | None = None

    @property
    def name(self) -> str:
        return self._name

    @property
    def instance(self) -> ApiToolsetInstance:
        return self._instance

    @property
    def operation(self) -> CompiledOperation:
        return self._operation

    @property
    def short_description(self) -> str:
        line = first_line(self._operation.summary or self._operation.description or self._operation.tool_name)
        return line if len(line) <= _SHORT_DESCRIPTION_MAX_LEN else line[: _SHORT_DESCRIPTION_MAX_LEN - 3] + "..."

    @property
    def description(self) -> str:
        op = self._operation
        parts = [p for p in (op.summary, op.description) if p]
        parts.append(f"Calls {op.method} {op.path} on the {self._instance.name} API.")
        return "\n\n".join(parts)

    @property
    def path(self) -> str:
        return f"/api-toolsets/{self._instance.id}/{self._operation.tool_name}"

    @property
    def display_name(self) -> str:
        return f"{self._instance.name} · {self._operation.summary or self._operation.tool_name}"

    @property
    def parameters(self) -> list[ToolParameter]:
        if self._parameters is None:
            self._parameters = _params_from_schema(self.raw_input_schema, self.name)
        return list(self._parameters)

    @property
    def raw_input_schema(self) -> dict[str, Any] | None:
        """The compiled schema with its `$defs` inlined once; each caller gets its own copy."""
        if self._raw_input_schema is None:
            self._raw_input_schema = resolve_json_schema_refs(self._operation.input_schema)
        return copy.deepcopy(self._raw_input_schema)

    def summarize_args(self, args: dict[str, Any]) -> str | None:
        return inline_args(args)

    def summarize_result(self, args: dict[str, Any], result: ToolResult) -> str | None:
        text = first_line(str(result.content or "")).strip()
        prefix = "Failed: " if result.is_error else ""
        return f"{prefix}{text[:_MAX_SUMMARY_CHARS]}" if text else None

    async def execute(self, **kwargs: Any) -> ToolOutput:  # noqa: ANN401
        try:
            response = await self._executor.call(self._operation, kwargs)
        except (RequestBuildError, ApiCallError) as e:
            return ToolOutput(success=False, error=str(e))
        except ApiToolsetAuthError as e:
            hint = connect_hint(self._instance)
            # Not `.capitalize()`: it lowercases the rest, and the menu names must match the UI.
            return ToolOutput(success=False, error=f"{e} {hint[0].upper()}{hint[1:]}.")
        except Exception as e:
            logger.warning("API toolset call %s failed: %s", self.name, type(e).__name__, exc_info=True)
            return ToolOutput(success=False, error=f"The call to {self._instance.name} failed ({type(e).__name__}).")
        return self._to_output(response)

    def _to_output(self, response: ApiResponse) -> ToolOutput:
        if response.ok:
            data = response.body if response.body is not None else f"HTTP {response.status}, no content."
            if response.truncated:
                data = f"{data}\n\n(The answer was cut short: it was larger than PipesHub passes on.)"
            return ToolOutput(success=True, data=data)
        body = response.body
        text = body if isinstance(body, str) else json.dumps(body, default=str) if body is not None else ""
        error = f"{self._instance.name} returned HTTP {response.status}"
        if 300 <= response.status < 400:
            error += " (a redirect, which PipesHub doesn't follow)"
        if response.status == 401:
            error += ". The stored credential was refused; it may need updating under Workspace → Your actions"
        return ToolOutput(success=False, error=f"{error}: {text[:_MAX_ERROR_BODY_CHARS]}" if text else f"{error}.")
