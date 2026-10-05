"""DatabaseSandbox toolset -- execute SQL in ephemeral SQLite or PostgreSQL sandboxes.

Exposes ``execute_sqlite`` and ``execute_postgresql`` tools to the agent.
Query results are returned directly and optionally exported as CSV artifacts.
"""

from __future__ import annotations

import asyncio
import csv
import io
import json
import logging
import os
from typing import Any, Optional
from uuid import uuid4

from pydantic import BaseModel, Field

from app.agent_loop_lib.tools.base import ParameterType, Tag, ToolParameter
from app.agent_loop_lib.tools.decorators import tool
from app.connectors.core.registry.auth_builder import AuthBuilder
from app.connectors.core.registry.tool_builder import (
    ToolsetBuilder,
    ToolsetCategory,
)
from app.modules.agents.qna.chat_state import ChatState
from app.sandbox.artifact_upload import save_query_result_csv
from app.sandbox.manager import get_executor
from app.sandbox.models import SandboxLanguage
from app.sandbox.redact import redact_sandbox_paths
from app.utils.conversation_tasks import register_task

logger = logging.getLogger(__name__)


# Pydantic schemas for tool inputs

class ExecuteSQLiteInput(BaseModel):
    query: str = Field(
        ...,
        description="SQL statement(s) to execute. Can include CREATE TABLE, INSERT, and SELECT in a single block separated by semicolons.",
    )
    setup_sql: str | None = Field(
        default=None,
        description="Optional setup SQL to run first (e.g. CREATE TABLE / INSERT statements to populate the database before the main query).",
    )


class ExecutePostgreSQLInput(BaseModel):
    query: str = Field(
        ...,
        description="SQL query to execute against the configured PostgreSQL sandbox instance.",
    )


# -------------------------------------------------------------------
# Toolset registration
# -------------------------------------------------------------------

@ToolsetBuilder("Database Sandbox")\
    .in_group("Internal Tools")\
    .with_description("Execute SQL queries in ephemeral SQLite or PostgreSQL sandbox databases for data analysis and exploration")\
    .with_category(ToolsetCategory.DATABASE)\
    .with_auth([
        AuthBuilder.type("NONE").fields([])
    ])\
    .as_internal()\
    .configure(lambda builder: builder.with_icon("/assets/icons/toolsets/database_sandbox.svg"))\
    .build_decorator()
class DatabaseSandbox:
    """Database sandbox tools exposed to agents."""

    def __init__(self, state: ChatState) -> None:
        self.chat_state = state

    def _result(self, success: bool, payload: dict[str, Any]) -> tuple[bool, str]:
        return success, json.dumps(payload, default=str)

    # ------------------------------------------------------------------
    # CSV artifact helper
    # ------------------------------------------------------------------

    def _schedule_csv_export(self, rows: list[dict[str, Any]], label: str) -> None:
        """Export query results as a CSV artifact in the background."""
        conversation_id = self.chat_state.get("conversation_id")
        org_id = self.chat_state.get("org_id")
        user_id = self.chat_state.get("user_id")
        graph_provider = self.chat_state.get("graph_provider")

        if not (conversation_id and org_id and rows):
            return

        columns = list(rows[0].keys()) if rows else []
        row_tuples = [tuple(r.get(c) for c in columns) for r in rows]

        async def _save() -> Optional[dict[str, Any]]:
            blob_store = self.chat_state.get("blob_store")
            if blob_store is None:
                from app.modules.transformers.blob_storage import BlobStorage
                blob_store = BlobStorage(
                    logger=logger,
                    config_service=self.chat_state.get("config_service"),
                    graph_provider=graph_provider,
                )
            return await save_query_result_csv(
                blob_store=blob_store,
                graph_provider=graph_provider,
                org_id=org_id,
                user_id=user_id,
                conversation_id=conversation_id,
                columns=columns,
                rows=row_tuples,
                file_name=f"{label}_{uuid4().hex[:8]}.csv",
                source_tool=f"database_sandbox.{label.split('_')[0]}",
            )

        task = asyncio.create_task(_save())
        register_task(conversation_id, task)

    # ------------------------------------------------------------------
    # Tools
    # ------------------------------------------------------------------

    @tool(
        path="/tools/database_sandbox/execute_sqlite",
        short_description="Execute SQL in an ephemeral SQLite database",
        description=(
            "Execute SQL in an ephemeral SQLite database. "
            "The database is created fresh for each execution. "
            "Use setup_sql to create tables and insert data, then use query to SELECT. "
            "Useful for data manipulation, analysis, and demonstrating SQL concepts."
        ),
        parameters=[
            ToolParameter(
                name="query",
                type=ParameterType.STRING,
                description="SQL statement(s) to execute. Can include CREATE TABLE, INSERT, and SELECT in a single block separated by semicolons.",
                required=True,
            ),
            ToolParameter(
                name="setup_sql",
                type=ParameterType.STRING,
                description="Optional setup SQL to run first (e.g. CREATE TABLE / INSERT statements to populate the database before the main query).",
                required=False,
            ),
        ],
        tags=[Tag(key="category", value="code_execution"), Tag(key="type", value="action")],
    )
    async def execute_sqlite(self, query: str, setup_sql: str | None = None) -> tuple[bool, str]:
        """Execute SQL in an ephemeral SQLite sandbox."""
        try:
            full_sql = ""
            if setup_sql:
                full_sql = setup_sql.rstrip().rstrip(";") + ";\n"
            full_sql += query

            executor = get_executor()
            result = await executor.execute(
                code=full_sql,
                language=SandboxLanguage.SQLITE,
            )

            rows = _parse_csv_output(result.stdout)
            DISPLAY_LIMIT = 100
            displayed_rows = rows[:DISPLAY_LIMIT]

            if rows:
                self._schedule_csv_export(rows, "sqlite_result")

            payload: dict[str, Any] = {
                "message": "Query executed successfully" if result.success else "Query execution failed",
                "row_count": len(rows),
                "data": displayed_rows,
                "execution_time_ms": result.execution_time_ms,
            }
            if len(rows) > DISPLAY_LIMIT:
                payload["truncated"] = True
                payload["displayed_row_count"] = DISPLAY_LIMIT
            if result.stderr:
                payload["stderr"] = redact_sandbox_paths(_truncate(result.stderr, 4000))
            if result.error:
                payload["error"] = redact_sandbox_paths(result.error)

            return self._result(result.success, payload)
        except Exception as e:
            logger.exception("execute_sqlite failed")
            return self._result(False, {"error": redact_sandbox_paths(str(e))})

    @tool(
        path="/tools/database_sandbox/execute_postgresql",
        short_description="Execute SQL against a configured PostgreSQL sandbox instance",
        description=(
            "Execute a SQL query against a configured PostgreSQL sandbox instance. "
            "Requires a DATABASE_URL to be configured in the sandbox environment. "
            "Use for PostgreSQL-specific SQL features or when connecting to a sandbox PG instance."
        ),
        parameters=[
            ToolParameter(
                name="query",
                type=ParameterType.STRING,
                description="SQL query to execute against the configured PostgreSQL sandbox instance.",
                required=True,
            ),
        ],
        tags=[Tag(key="category", value="code_execution"), Tag(key="type", value="action")],
    )
    async def execute_postgresql(self, query: str) -> tuple[bool, str]:
        """Execute SQL against a PostgreSQL sandbox instance."""
        try:
            pg_url = os.environ.get("SANDBOX_PG_URL", "")
            if not pg_url:
                return self._result(False, {
                    "error": "PostgreSQL sandbox not configured",
                    "message": "Set SANDBOX_PG_URL environment variable to enable PostgreSQL sandbox",
                })

            executor = get_executor()
            result = await executor.execute(
                code=query,
                language=SandboxLanguage.POSTGRESQL,
                env={"DATABASE_URL": pg_url},
            )

            rows = _parse_csv_output(result.stdout)
            DISPLAY_LIMIT = 100
            displayed_rows = rows[:DISPLAY_LIMIT]

            if rows:
                self._schedule_csv_export(rows, "pg_result")

            payload: dict[str, Any] = {
                "message": "Query executed successfully" if result.success else "Query execution failed",
                "row_count": len(rows),
                "data": displayed_rows,
                "execution_time_ms": result.execution_time_ms,
            }
            if len(rows) > DISPLAY_LIMIT:
                payload["truncated"] = True
                payload["displayed_row_count"] = DISPLAY_LIMIT
            if result.stderr:
                payload["stderr"] = redact_sandbox_paths(_truncate(result.stderr, 4000))
            if result.error:
                payload["error"] = redact_sandbox_paths(result.error)

            return self._result(result.success, payload)
        except Exception as e:
            logger.exception("execute_postgresql failed")
            return self._result(False, {"error": redact_sandbox_paths(str(e))})


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _parse_csv_output(stdout: str) -> list[dict[str, Any]]:
    """Parse CSV-formatted stdout (from sqlite3 -header -csv or psql --csv) into dicts."""
    if not stdout.strip():
        return []
    try:
        reader = csv.DictReader(io.StringIO(stdout))
        return list(reader)
    except (csv.Error, ValueError):
        logger.debug(
            "Failed to parse CSV output (%d chars): %s",
            len(stdout), stdout[:200],
        )
        return []


def _truncate(text: str, max_len: int) -> str:
    if len(text) <= max_len:
        return text
    return text[:max_len] + f"\n... (truncated, {len(text)} total chars)"
