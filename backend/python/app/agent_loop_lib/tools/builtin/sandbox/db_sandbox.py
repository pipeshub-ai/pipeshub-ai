from __future__ import annotations

from typing import Any

from app.agent_loop_lib.sandbox.db_sandbox import DBSandboxError, SqliteDBSandbox
from app.agent_loop_lib.tools.base import (
    ParameterType,
    Tag,
    Tool,
    ToolOutput,
    ToolParameter,
)

"""`db_query` — the DB sandbox's tool. Risk tracks the sandbox's own mode:
no risk tag (LOW) when it's readonly (the tool literally cannot mutate
anything), MEDIUM when readwrite (still scoped/allowlisted, but mutating)."""


class DBQueryTool(Tool):
    def __init__(self, sandbox: SqliteDBSandbox) -> None:
        self._sandbox = sandbox

    @property
    def name(self) -> str:
        return "db_query"

    @property
    def short_description(self) -> str:
        return "Execute a scoped SQL query against the configured database sandbox."

    @property
    def description(self) -> str:
        return "Execute a scoped SQL query against the configured database sandbox."

    @property
    def path(self) -> str:
        return "/toolsets/db_sandbox/db_query"

    @property
    def tags(self) -> list[Tag]:
        if self._sandbox.mode == "readonly":
            return []
        return [Tag("risk", "medium"), Tag("category", "write")]

    @property
    def parameters(self) -> list[ToolParameter]:
        return [
            ToolParameter(name="sql", type=ParameterType.STRING, required=True, description="SQL statement to execute."),
            ToolParameter(
                name="params", type=ParameterType.ARRAY, required=False, default=None,
                description="Positional parameters for '?' placeholders in the SQL.",
                items={"type": "string"},
            ),
        ]

    async def execute(self, sql: str, params: list | None = None, **kwargs: Any) -> ToolOutput:
        try:
            rows = await self._sandbox.query(sql, params)
        except DBSandboxError as e:
            return ToolOutput(success=True, data={"error": str(e)})
        return ToolOutput(success=True, data={"rows": rows})
