import asyncio
import json
import logging
import time
from typing import Any, Optional

from pydantic import BaseModel, Field

from app.agents.tools.decorator import tool
from app.agents.tools.enums import ParameterType
from app.agents.tools.models import ToolParameter
from app.connectors.core.registry.auth_builder import AuthBuilder, AuthType
from app.connectors.core.registry.tool_builder import (
    ToolsetBuilder,
    ToolsetCategory,
)
from app.connectors.core.registry.types import AuthField, DocumentationLink
from app.modules.agents.qna.chat_state import ChatState
from app.sources.client.redshift.redshift import RedshiftClient
from app.sources.external.redshift.redshift_ import RedshiftDataSource
from app.utils.conversation_tasks import register_task

logger = logging.getLogger(__name__)


class ListSchemasInput(BaseModel):
    """Input schema for list_schemas."""
    pass


class ListSchemasAndTablesInput(BaseModel):
    """Input schema for list_schemas_and_tables."""
    pass

class ListTablesInput(BaseModel):
    """Input schema for list_tables."""

    schema_name: str = Field(..., description="Schema name to list tables from")

class GetTableDDLInput(BaseModel):
    """Input schema for get_table_ddl."""

    schema_name: str = Field(..., description="Schema name")
    table: str = Field(..., description="Table name")


class GetSchemaDDLInput(BaseModel):
    """Input schema for get_schema_ddl."""

    schema_name: str = Field(..., description="Schema name to fetch DDL for all its tables")


class FetchDBSchemaInput(BaseModel):
    """Input schema for fetch_db_schema."""

    include_views: bool = Field(
        default=True, description="Include view definitions in the response"
    )


class ExecuteQueryInput(BaseModel):
    """Input schema for execute_query."""

    query: str = Field(..., description="SQL query to execute")


class GetTablesSchemaInput(BaseModel):
    """Input schema for get_tables_schema."""

    schema_name: str = Field(..., description="Schema name the tables belong to")
    tables: list[str] = Field(..., description="List of table names to fetch schema for")


@ToolsetBuilder("Redshift")\
    .in_group("Database")\
    .with_description("Amazon Redshift toolset for schema introspection and SQL execution")\
    .with_category(ToolsetCategory.DATABASE)\
    .with_auth([
        AuthBuilder.type(AuthType.BASIC_AUTH).fields([
            AuthField(
                name="host",
                display_name="Host",
                placeholder="cluster.xxxx.us-east-1.redshift.amazonaws.com",
                description="Redshift cluster endpoint or Serverless workgroup endpoint",
                field_type="TEXT",
                required=True,
                usage="CONFIGURE",
                max_length=500,
                is_secret=False,
            ),
            AuthField(
                name="port",
                display_name="Port",
                placeholder="5439",
                description="Redshift server port (default: 5439)",
                field_type="TEXT",
                required=True,
                usage="CONFIGURE",
                max_length=10,
                is_secret=False,
            ),
            AuthField(
                name="database",
                display_name="Database",
                placeholder="dev",
                description="Redshift database name",
                field_type="TEXT",
                required=True,
                usage="CONFIGURE",
                max_length=200,
                is_secret=False,
            ),
            AuthField(
                name="username",
                display_name="Username",
                placeholder="awsuser",
                description="Redshift username",
                field_type="TEXT",
                required=True,
                usage="AUTHENTICATE",
                max_length=200,
                is_secret=False,
            ),
            AuthField(
                name="password",
                display_name="Password",
                placeholder="Enter password",
                description="Redshift password",
                field_type="PASSWORD",
                required=True,
                usage="AUTHENTICATE",
                max_length=500,
                is_secret=True,
            ),
        ])
    ])\
    .configure(lambda builder: builder.with_icon("/assets/icons/connectors/redshift.svg")
        .add_documentation_link(DocumentationLink(
            "Amazon Redshift Documentation",
            "https://docs.aws.amazon.com/redshift/latest/gsg/new-user-serverless.html",
            "setup",
        ))
        .add_documentation_link(DocumentationLink(
            "Pipeshub Documentation",
            "https://docs.pipeshub.com/toolsets/redshift/redshift",
            "pipeshub",
        )))\
    .build_decorator()
class Redshift:
    """Redshift tools exposed to agents."""

    def __init__(self, client: RedshiftClient, state: ChatState) -> None:
        self.client = RedshiftDataSource(client)
        self.chat_state = state

    def _result(self, success: bool, payload: dict[str, Any]) -> tuple[bool, str]:
        return success, json.dumps(payload, default=str)

    # ------------------------------------------------------------------
    # list_schemas
    # ------------------------------------------------------------------

    @tool(
        app_name="redshift",
        tool_name="list_schemas",
        description="List all schemas in the connected Redshift database",
        args_schema=ListSchemasInput,
        parameters=[],
        returns="JSON list of schema names",
    )
    async def list_schemas(self) -> tuple[bool, str]:
        """List all schemas in the connected Redshift database."""
        try:
            resp = await self.client.list_schemas()
            if not resp.success:
                return self._result(False, {
                    "error": resp.error or "Failed to list schemas",
                    "message": resp.message,
                })

            schemas = resp.data if isinstance(resp.data, list) else []
            return self._result(True, {
                "message": resp.message or "Schemas fetched successfully",
                "schema_count": len(schemas),
                "schemas": schemas,
            })
        except Exception as e:
            logger.error(f"list_schemas failed: {e}")
            return self._result(False, {"error": str(e)})

    # ------------------------------------------------------------------
    # list_schemas_and_tables
    # ------------------------------------------------------------------

    @tool(
        app_name="redshift",
        tool_name="list_schemas_and_tables",
        description=(
            "List all schemas and their tables in the connected Redshift database in a single call. "
            "Use this as the first step to understand the database structure before querying."
        ),
        args_schema=ListSchemasAndTablesInput,
        parameters=[],
        returns="JSON object with schemas, each containing their list of tables",
    )
    async def list_schemas_and_tables(self) -> tuple[bool, str]:
        """List all schemas and tables in the Redshift database in one call."""
        try:
            schemas_resp = await self.client.list_schemas()
            if not schemas_resp.success:
                return self._result(False, {
                    "error": schemas_resp.error or "Failed to list schemas",
                    "message": schemas_resp.message,
                })

            schemas = schemas_resp.data if isinstance(schemas_resp.data, list) else []
            payload: list[dict[str, Any]] = []

            for schema_row in schemas:
                schema_name = schema_row.get("name")
                if not schema_name:
                    continue

                tables_resp = await self.client.list_tables(schema=schema_name)
                tables = []
                if tables_resp.success and isinstance(tables_resp.data, list):
                    tables = [t.get("name") for t in tables_resp.data if t.get("name")]

                payload.append({
                    "schema": schema_name,
                    "table_count": len(tables),
                    "tables": tables,
                })

            return self._result(True, {
                "message": "Schemas and tables fetched successfully",
                "schema_count": len(payload),
                "schemas": payload,
            })
        except Exception as e:
            logger.error(f"list_schemas_and_tables failed: {e}")
            return self._result(False, {"error": str(e)})

    # ------------------------------------------------------------------
    # get_table_ddl
    # ------------------------------------------------------------------

    @tool(
        app_name="redshift",
        tool_name="get_table_ddl",
        description="Get the CREATE TABLE statement for a specific table in a Redshift schema",
        args_schema=GetTableDDLInput,
        parameters=[
            ToolParameter(
                name="schema_name",
                type=ParameterType.STRING,
                description="Schema name",
                required=True,
            ),
            ToolParameter(
                name="table",
                type=ParameterType.STRING,
                description="Table name",
                required=True,
            ),
        ],
        returns="JSON payload with table DDL",
    )
    async def get_table_ddl(
        self,
        schema_name: str,
        table: str,
    ) -> tuple[bool, str]:
        """Fetch DDL for a specific table in a Redshift schema."""
        try:
            if not schema_name or not table:
                return self._result(False, {
                    "error": "Missing required parameters: schema_name and table",
                })

            resp = await self.client.get_table_ddl(schema=schema_name, table=table)
            if not resp.success:
                return self._result(False, {
                    "error": resp.error or "Failed to fetch table DDL",
                    "message": resp.message,
                })

            return self._result(True, {
                "message": resp.message or "Table DDL fetched successfully",
                "schema": schema_name,
                "table": table,
                "data": resp.data or {},
            })
        except Exception as e:
            logger.error(f"get_table_ddl failed: {e}")
            return self._result(False, {"error": str(e)})

    # ------------------------------------------------------------------
    # get_schema_ddl
    # ------------------------------------------------------------------

    @tool(
        app_name="redshift",
        tool_name="get_schema_ddl",
        description=(
            "Fetch CREATE TABLE statements for all tables in a given Redshift schema. "
            "Useful for understanding the full structure of a schema like 'public', 'analytics', or 'staging'."
        ),
        args_schema=GetSchemaDDLInput,
        parameters=[
            ToolParameter(
                name="schema_name",
                type=ParameterType.STRING,
                description="Schema name to fetch DDL for all its tables",
                required=True,
            ),
        ],
        returns="JSON list of table DDLs for the given schema",
    )
    async def get_schema_ddl(
        self,
        schema_name: str,
    ) -> tuple[bool, str]:
        """Fetch DDL for all tables in a Redshift schema."""
        try:
            if not schema_name:
                return self._result(False, {
                    "error": "Missing required parameter: schema_name",
                })

            tables_resp = await self.client.list_tables(schema=schema_name)
            if not tables_resp.success:
                return self._result(False, {
                    "error": tables_resp.error or "Failed to list tables in schema",
                    "message": tables_resp.message,
                })

            tables = tables_resp.data if isinstance(tables_resp.data, list) else []
            ddl_payload: list[dict[str, Any]] = []

            for table_row in tables:
                table_name = table_row.get("name")
                if not table_name:
                    continue

                ddl_resp = await self.client.get_table_ddl(schema=schema_name, table=table_name)
                if ddl_resp.success:
                    ddl_payload.append({
                        "table": table_name,
                        "ddl": (ddl_resp.data or {}).get("ddl", ""),
                    })
                else:
                    ddl_payload.append({
                        "table": table_name,
                        "error": ddl_resp.error or "Failed to fetch DDL",
                    })

            return self._result(True, {
                "message": f"DDL fetched for {len(ddl_payload)} tables in schema '{schema_name}'",
                "schema": schema_name,
                "table_count": len(ddl_payload),
                "tables": ddl_payload,
            })
        except Exception as e:
            logger.error(f"get_schema_ddl failed: {e}")
            return self._result(False, {"error": str(e)})

    # ------------------------------------------------------------------
    # get_tables_schema
    # ------------------------------------------------------------------

    @tool(
        app_name="redshift",
        tool_name="get_tables_schema",
        description="Fetch schema (columns, primary keys, foreign keys) for a specific list of tables in a Redshift schema",
        args_schema=GetTablesSchemaInput,
        parameters=[
            ToolParameter(
                name="schema_name",
                type=ParameterType.STRING,
                description="Schema name the tables belong to",
                required=True,
            ),
            ToolParameter(
                name="tables",
                type=ParameterType.LIST,
                description="List of table names to fetch schema for",
                required=True,
            ),
        ],
        returns="JSON schema payload for the requested tables",
    )
    async def get_tables_schema(
        self,
        schema_name: str,
        tables: list[str],
    ) -> tuple[bool, str]:
        """Fetch schema details for a given list of tables in a Redshift schema."""
        try:
            if not schema_name:
                return self._result(False, {"error": "Missing required parameter: schema_name"})
            if not tables:
                return self._result(False, {"error": "Missing required parameter: tables"})

            table_payload: list[dict[str, Any]] = []
            for table_name in tables:
                if not table_name:
                    continue

                info_resp = await self.client.get_table_info(schema=schema_name, table=table_name)
                if not info_resp.success:
                    table_payload.append({
                        "name": table_name,
                        "error": info_resp.error or "Failed to fetch table info",
                    })
                    continue

                table_info = (
                    info_resp.data if isinstance(info_resp.data, dict) else {"name": table_name}
                )

                pk_resp = await self.client.get_primary_keys(schema=schema_name, table=table_name)
                fk_resp = await self.client.get_foreign_keys(schema=schema_name, table=table_name)

                table_info["primary_keys"] = [
                    row.get("column_name")
                    for row in (pk_resp.data or [])
                    if isinstance(row, dict) and row.get("column_name")
                ] if pk_resp.success else []

                table_info["foreign_keys"] = fk_resp.data if fk_resp.success else []
                table_payload.append(table_info)

            return self._result(True, {
                "message": "Table schemas fetched successfully",
                "schema": schema_name,
                "table_count": len(table_payload),
                "tables": table_payload,
            })
        except Exception as e:
            logger.error(f"get_tables_schema failed: {e}")
            return self._result(False, {"error": str(e)})

    # ------------------------------------------------------------------
    # fetch_db_schema
    # ------------------------------------------------------------------

    @tool(
        app_name="redshift",
        tool_name="fetch_db_schema",
        description=(
            "Fetch the full schema of the connected Redshift database: all schemas, "
            "their tables with columns, primary/foreign keys, and optionally views."
        ),
        args_schema=FetchDBSchemaInput,
        parameters=[
            ToolParameter(
                name="include_views",
                type=ParameterType.BOOLEAN,
                description="Include view definitions in the response",
                required=False,
            ),
        ],
        returns="JSON schema payload grouped by schema",
    )
    async def fetch_db_schema(
        self,
        include_views: bool = True,
    ) -> tuple[bool, str]:
        """Fetch full schema details for the connected Redshift database."""
        try:
            if include_views is not None:
                include_views = bool(include_views)

            schemas_resp = await self.client.list_schemas()
            if not schemas_resp.success:
                return self._result(False, {
                    "error": schemas_resp.error or "Failed to list schemas",
                    "message": schemas_resp.message,
                })

            schemas = schemas_resp.data if isinstance(schemas_resp.data, list) else []
            schemas_payload: list[dict[str, Any]] = []

            for schema_row in schemas:
                schema_name = schema_row.get("name")
                if not schema_name:
                    continue

                tables_resp = await self.client.list_tables(schema=schema_name)
                if not tables_resp.success:
                    schemas_payload.append({
                        "name": schema_name,
                        "error": tables_resp.error or "Failed to list tables",
                        "tables": [],
                        "views": [],
                    })
                    continue

                table_payload: list[dict[str, Any]] = []
                for table in tables_resp.data or []:
                    table_name = table.get("name")
                    if not table_name:
                        continue

                    info_resp = await self.client.get_table_info(
                        schema=schema_name, table=table_name
                    )
                    if not info_resp.success:
                        table_payload.append({
                            "name": table_name,
                            "error": info_resp.error or "Failed to fetch table info",
                        })
                        continue

                    table_info = (
                        info_resp.data
                        if isinstance(info_resp.data, dict)
                        else {"name": table_name}
                    )

                    pk_resp = await self.client.get_primary_keys(
                        schema=schema_name, table=table_name
                    )
                    fk_resp = await self.client.get_foreign_keys(
                        schema=schema_name, table=table_name
                    )

                    table_info["primary_keys"] = [
                        row.get("column_name")
                        for row in (pk_resp.data or [])
                        if isinstance(row, dict) and row.get("column_name")
                    ] if pk_resp.success else []

                    table_info["foreign_keys"] = fk_resp.data if fk_resp.success else []
                    table_payload.append(table_info)

                view_payload: list[dict[str, Any]] = []
                if include_views:
                    views_resp = await self.client.list_views(schema=schema_name)
                    if views_resp.success and isinstance(views_resp.data, list):
                        view_payload = views_resp.data

                schemas_payload.append({
                    "name": schema_name,
                    "tables": table_payload,
                    "views": view_payload,
                    "table_count": len(table_payload),
                    "view_count": len(view_payload),
                })

            return self._result(True, {
                "message": "Schema fetched successfully",
                "schema_count": len(schemas_payload),
                "schemas": schemas_payload,
            })
        except Exception as e:
            logger.error(f"fetch_db_schema failed: {e}")
            return self._result(False, {"error": str(e)})

    # ------------------------------------------------------------------
    # execute_query
    # ------------------------------------------------------------------

    @tool(
        app_name="redshift",
        tool_name="execute_query",
        description="Execute a SQL query against the connected Redshift database",
        args_schema=ExecuteQueryInput,
        parameters=[
            ToolParameter(
                name="query",
                type=ParameterType.STRING,
                description="SQL query to execute",
                required=True,
            ),
        ],
        returns="JSON query response including row count and data",
    )
    async def execute_query(
        self,
        query: str,
    ) -> tuple[bool, str]:
        """Execute SQL query using configured Redshift client."""
        try:
            if not query:
                return self._result(False, {
                    "error": "Missing required parameter: query",
                })

            query_resp = await self.client.execute_query(query=query)
            if not query_resp.success:
                return self._result(False, {
                    "error": query_resp.error or "Query execution failed",
                    "message": query_resp.message,
                })

            rows = query_resp.data if isinstance(query_resp.data, list) else []
            DISPLAY_LIMIT = 100
            displayed_rows = rows[:DISPLAY_LIMIT]
            result_payload = {
                "message": query_resp.message or "Query executed successfully",
                "row_count": len(rows),
                "data": displayed_rows,
                **(
                    {"truncated": True, "displayed_row_count": DISPLAY_LIMIT}
                    if len(rows) > DISPLAY_LIMIT
                    else {}
                ),
            }

            # Register background CSV export for conversation tasks
            conversation_id = self.chat_state.get("conversation_id")
            blob_store = self.chat_state.get("blob_storage")
            org_id = self.chat_state.get("org_id")
            logger.info(
                "Redshift execute_query context: conversation_id=%s org_id=%s rows=%d blob_store=%s",
                conversation_id,
                org_id,
                len(rows),
                "yes" if blob_store is not None else "no",
            )

            if conversation_id and org_id and blob_store is None:
                try:
                    from app.modules.transformers.blob_storage import BlobStorage

                    blob_store = BlobStorage(
                        logger=logger,
                        config_service=self.chat_state.get("config_service"),
                        graph_provider=self.chat_state.get("graph_provider"),
                    )
                except Exception as e:
                    logger.warning(
                        "Could not initialize BlobStorage for Redshift CSV export: %s", e
                    )

            if rows and conversation_id and blob_store and org_id:
                logger.info(
                    "Redshift CSV export conditions met; scheduling background task"
                )
                columns = list(rows[0].keys()) if rows else []
                row_tuples = [tuple(r.get(c) for c in columns) for r in rows]
                raw_columns = columns
                raw_rows = row_tuples

                async def _save_csv_to_blob() -> Optional[dict[str, Any]]:
                    try:
                        from app.utils.conversation_tasks import _rows_to_csv_bytes

                        csv_bytes = _rows_to_csv_bytes(raw_columns, raw_rows)
                        file_name = f"query_result_{int(time.time())}.csv"
                        upload_info = await blob_store.save_conversation_file_to_storage(
                            org_id=org_id,
                            conversation_id=conversation_id,
                            file_name=file_name,
                            file_bytes=csv_bytes,
                        )
                        logger.info(
                            "Redshift CSV export complete for conversation %s (%d rows)",
                            conversation_id,
                            len(raw_rows),
                        )
                        return {"type": "csv_download", **upload_info}
                    except Exception:
                        logger.exception(
                            "Background Redshift CSV export failed for conversation %s",
                            conversation_id,
                        )
                        return None

                task = asyncio.create_task(_save_csv_to_blob())
                register_task(conversation_id, task)

            return self._result(True, result_payload)
        except Exception as e:
            logger.error(f"execute_query failed: {e}")
            return self._result(False, {"error": str(e)})




    @tool(
        app_name="redshift",
        tool_name="list_tables",
        description="List all tables in a specific Redshift schema",
        args_schema=ListTablesInput,
        parameters=[
            ToolParameter(
                name="schema_name",
                type=ParameterType.STRING,
                description="Schema name to list tables from",
                required=True,
            ),
        ],
        returns="JSON list of tables in the given schema",
    )
    async def list_tables(
        self,
        schema_name: str,
    ) -> tuple[bool, str]:
        """List all tables in a specific Redshift schema."""
        try:
            if not schema_name:
                return self._result(False, {"error": "Missing required parameter: schema_name"})

            resp = await self.client.list_tables(schema=schema_name)
            if not resp.success:
                return self._result(False, {
                    "error": resp.error or "Failed to list tables",
                    "message": resp.message,
                })

            tables = resp.data if isinstance(resp.data, list) else []
            return self._result(True, {
                "message": resp.message or "Tables fetched successfully",
                "schema": schema_name,
                "table_count": len(tables),
                "tables": tables,
            })
        except Exception as e:
            logger.error(f"list_tables failed: {e}")
            return self._result(False, {"error": str(e)})
