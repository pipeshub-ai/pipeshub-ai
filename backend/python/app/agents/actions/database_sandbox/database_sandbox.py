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
import time
from typing import Any, Optional

from pydantic import BaseModel, Field

from app.agents.tools.config import ToolCategory
from app.agents.tools.decorator import tool
from app.agents.tools.models import ToolIntent
from app.connectors.core.registry.auth_builder import AuthBuilder
from app.connectors.core.registry.tool_builder import (
    ToolsetBuilder,
    ToolsetCategory,
)
from app.modules.agents.qna.chat_state import ChatState
from app.sandbox.artifact_upload import create_artifact_record
from app.sandbox.manager import get_executor
from app.sandbox.models import SandboxLanguage
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
            try:
                blob_store = self.chat_state.get("blob_store")
                if blob_store is None:
                    from app.modules.transformers.blob_storage import BlobStorage
                    blob_store = BlobStorage(
                        logger=logger,
                        config_service=self.chat_state.get("config_service"),
                        graph_provider=graph_provider,
                    )

                csv_bytes = _rows_to_csv_bytes(columns, row_tuples)
                file_name = f"{label}_{int(time.time())}.csv"

                upload_info = await blob_store.save_conversation_file_to_storage(
                    org_id=org_id,
                    conversation_id=conversation_id,
                    file_name=file_name,
                    file_bytes=csv_bytes,
                )

                result_entry: dict[str, Any] = {
                    **upload_info,
                    "mimeType": "text/csv",
                    "sizeBytes": len(csv_bytes),
                }

                document_id = upload_info.get("documentId", "")
                if document_id and user_id and graph_provider:
                    try:
                        from app.config.constants.arangodb import Connectors

                        record_id = await create_artifact_record(
                            graph_provider=graph_provider,
                            document_id=document_id,
                            file_name=file_name,
                            mime_type="text/csv",
                            size_bytes=len(csv_bytes),
                            org_id=org_id,
                            user_id=user_id,
                            conversation_id=conversation_id,
                            connector_name=Connectors.DATABASE_SANDBOX,
                            source_tool=f"database_sandbox.{label.split('_')[0]}",
                        )
                        result_entry["recordId"] = record_id
                    except (OSError, ConnectionError, RuntimeError, ValueError):
                        logger.exception(
                            "Failed to create ArtifactRecord for CSV export %s", file_name,
                        )

                return {
                    "type": "artifacts",
                    "artifacts": [result_entry],
                }
            except (OSError, ConnectionError, RuntimeError, ValueError):
                logger.exception("Background CSV export failed for %s", label)
                return None

        task = asyncio.create_task(_save())
        register_task(conversation_id, task)

    # ------------------------------------------------------------------
    # Tools
    # ------------------------------------------------------------------

    @tool(
        app_name="database_sandbox",
        tool_name="execute_sqlite",
        args_schema=ExecuteSQLiteInput,
        llm_description=(
            "Execute SQL in an ephemeral SQLite database. "
            "The database is created fresh for each execution. "
            "Use setup_sql to create tables and insert data, then use query to SELECT. "
            "Useful for data manipulation, analysis, and demonstrating SQL concepts."
        ),
        category=ToolCategory.CODE_EXECUTION,
        is_essential=False,
        requires_auth=False,
        when_to_use=[
            "User wants to run SQL queries on sample data",
            "User wants to demonstrate a SQL concept",
            "User needs to manipulate tabular data with SQL",
            "User wants to create and query a temporary database",
        ],
        when_not_to_use=[
            "User wants to query their own production database (use MariaDB/Redshift tools)",
            "User just wants a text explanation of SQL",
        ],
        primary_intent=ToolIntent.ACTION,
        typical_queries=[
            "Run a SQL query to find the top 10 customers by revenue",
            "Create a table and demonstrate a JOIN",
            "Analyze this data using SQL",
        ],
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
                payload["stderr"] = _truncate(result.stderr, 4000)
            if result.error:
                payload["error"] = result.error

            return self._result(result.success, payload)
        except Exception as e:
            logger.exception("execute_sqlite failed")
            return self._result(False, {"error": str(e)})

    @tool(
        app_name="database_sandbox",
        tool_name="execute_postgresql",
        args_schema=ExecutePostgreSQLInput,
        llm_description=(
            "Execute a SQL query against a configured PostgreSQL sandbox instance. "
            "Requires a DATABASE_URL to be configured in the sandbox environment. "
            "Use for PostgreSQL-specific SQL features or when connecting to a sandbox PG instance."
        ),
        category=ToolCategory.CODE_EXECUTION,
        is_essential=False,
        requires_auth=False,
        when_to_use=[
            "User wants to use PostgreSQL-specific SQL features",
            "User wants to query a sandbox PostgreSQL instance",
        ],
        when_not_to_use=[
            "SQLite would suffice for the task",
            "User just wants a text explanation",
        ],
        primary_intent=ToolIntent.ACTION,
        typical_queries=[
            "Run a PostgreSQL query with window functions",
            "Demonstrate PostgreSQL JSON operations",
        ],
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
                payload["stderr"] = _truncate(result.stderr, 4000)
            if result.error:
                payload["error"] = result.error

            return self._result(result.success, payload)
        except Exception as e:
            logger.exception("execute_postgresql failed")
            return self._result(False, {"error": str(e)})


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _rows_to_csv_bytes(columns: list[str], rows: list[tuple]) -> bytes:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(columns)
    writer.writerows(rows)
    return buf.getvalue().encode("utf-8")


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
