import asyncio
import io
import json
import logging
import time
from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel, Field

from app.utils.conversation_tasks import register_task
from app.modules.agents.qna.chat_state import ChatState

from app.agents.tools.decorator import tool
from app.agents.tools.enums import ParameterType
from app.agents.tools.models import ToolParameter
from app.connectors.core.registry.auth_builder import AuthBuilder, AuthType
from app.connectors.core.registry.tool_builder import (
	ToolsetBuilder,
	ToolsetCategory,
)
from app.connectors.core.registry.types import AuthField
from app.sources.client.mariadb.mariadb import MariaDBClient
from app.sources.external.mariadb.mariadb_ import MariaDBDataSource

logger = logging.getLogger(__name__)


class GetTableDDLInput(BaseModel):
	"""Input schema for get_table_ddl."""

	table: str = Field(..., description="Table name")


class FetchDBSchemaInput(BaseModel):
	"""Input schema for fetch_db_schema."""

	include_views: bool = Field(default=True, description="Include view definitions in the response")


class ExecuteQueryInput(BaseModel):
	"""Input schema for execute_query."""

	query: str = Field(..., description="SQL query to execute")

class GetTablesSchemaInput(BaseModel):
	"""Input schema for get_tables_schema."""

	tables: List[str] = Field(..., description="List of table names to fetch schema for")

@ToolsetBuilder("MariaDB")\
	.in_group("Database")\
	.with_description("MariaDB toolset for schema introspection and SQL execution")\
	.with_category(ToolsetCategory.DATABASE)\
	.with_auth([
		AuthBuilder.type(AuthType.BASIC_AUTH).fields([
			AuthField(
				name="host",
				display_name="Host",
				placeholder="localhost",
				description="MariaDB server host",
				field_type="TEXT",
				required=True,
				usage="CONFIGURE",
				max_length=500,
				is_secret=False,
			),
			AuthField(
				name="port",
				display_name="Port",
				placeholder="3306",
				description="MariaDB server port",
				field_type="TEXT",
				required=True,
				usage="CONFIGURE",
				max_length=10,
				is_secret=False,
			),
			AuthField(
				name="database",
				display_name="Database",
				placeholder="mydb",
				description="Default MariaDB database (optional but recommended)",
				field_type="TEXT",
				required=False,
				usage="CONFIGURE",
				max_length=200,
				is_secret=False,
			),
			AuthField(
				name="username",
				display_name="Username",
				placeholder="root",
				description="MariaDB username",
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
				description="MariaDB password",
				field_type="PASSWORD",
				required=True,
				usage="AUTHENTICATE",
				max_length=500,
				is_secret=True,
			),
		])
	])\
	.configure(lambda builder: builder.with_icon("/assets/icons/connectors/mariadb.svg"))\
	.build_decorator()
class MariaDB:
	"""MariaDB tools exposed to agents."""

	def __init__(self, client: MariaDBClient, state: ChatState) -> None:
		self.client = MariaDBDataSource(client)
		self.chat_state = state
	
	def _result(self, success: bool, payload: Dict[str, Any]) -> Tuple[bool, str]:
		return success, json.dumps(payload, default=str)

	def _get_default_database(self) -> Optional[str]:
		"""Safely read default database from client for legacy and toolset paths."""
		client = self.client.get_client()
		if isinstance(client, dict):
			value = client.get("database")
			return str(value) if value not in (None, "") else None
		return getattr(client, "database", None)
	
	@tool(
		app_name="mariadb",
		tool_name="list_tables",
		description="List tables in the default MariaDB database configured in connector auth",
		parameters=[],
		returns="JSON list of tables",
	)
	async def list_tables(
		self,
	) -> Tuple[bool, str]:
		"""List tables in a single MariaDB database context."""
		try:
			db_name = self._get_default_database()
			if not db_name:
				return self._result(False, {
					"error": "No database selected",
					"message": "Configure default database in connector auth",
				})

			resp = await self.client.list_tables(database=db_name)
			if not resp.success:
				return self._result(False, {
					"error": resp.error or "Failed to list tables",
					"message": resp.message,
				})

			tables = resp.data if isinstance(resp.data, list) else []
			return self._result(True, {
				"message": resp.message or "Tables fetched successfully",
				"database": db_name,
				"table_count": len(tables),
				"tables": tables,
			})
		except Exception as e:
			logger.error(f"list_tables failed: {e}")
			return self._result(False, {"error": str(e)})

	@tool(
		app_name="mariadb",
		tool_name="get_table_ddl",
		description="Get CREATE TABLE statement for a table in the default auth-configured database",
		args_schema=GetTableDDLInput,
		parameters=[
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
		table: str,
	) -> Tuple[bool, str]:
		"""Fetch DDL for a table in a single MariaDB database context."""
		try:
			if not table:
				return self._result(False, {
					"error": "Missing required parameter: table",
				})

			db_name = self._get_default_database()
			if not db_name:
				return self._result(False, {
					"error": "No database selected",
					"message": "Configure default database in connector auth",
				})

			resp = await self.client.get_table_ddl(table=table, database=db_name)
			if not resp.success:
				return self._result(False, {
					"error": resp.error or "Failed to fetch table DDL",
					"message": resp.message,
				})

			return self._result(True, {
				"message": resp.message or "Table DDL fetched successfully",
				"database": db_name,
				"table": table,
				"data": resp.data or {},
			})
		except Exception as e:
			logger.error(f"get_table_ddl failed: {e}")
			return self._result(False, {"error": str(e)})

	@tool(
		app_name="mariadb",
		tool_name="get_tables_schema",
		description="Fetch schema (columns, primary keys, foreign keys) for a specific list of tables in the default auth-configured database",
		args_schema=GetTablesSchemaInput,
		parameters=[
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
		tables: List[str],
	) -> Tuple[bool, str]:
		"""Fetch schema details for a given list of tables in the default MariaDB database."""
		try:
			if not tables:
				return self._result(False, {
					"error": "Missing required parameter: tables",
				})

			db_name = self._get_default_database()
			if not db_name:
				return self._result(False, {
					"error": "No database selected",
					"message": "Configure default database in connector auth",
				})

			table_payload: List[Dict[str, Any]] = []
			for table_name in tables:
				if not table_name:
					continue

				info_resp = await self.client.get_table_info(table=table_name, database=db_name)
				if not info_resp.success:
					table_payload.append({
						"name": table_name,
						"error": info_resp.error or "Failed to fetch table info",
					})
					continue

				table_info = info_resp.data if isinstance(info_resp.data, dict) else {"name": table_name}

				pk_resp = await self.client.get_primary_keys(table=table_name, database=db_name)
				fk_resp = await self.client.get_foreign_keys(table=table_name, database=db_name)

				table_info["primary_keys"] = [
					row.get("column_name")
					for row in (pk_resp.data or [])
					if isinstance(row, dict) and row.get("column_name")
				] if pk_resp.success else []

				table_info["foreign_keys"] = fk_resp.data if fk_resp.success else []
				table_payload.append(table_info)

			return self._result(True, {
				"message": "Table schemas fetched successfully",
				"database": db_name,
				"table_count": len(table_payload),
				"tables": table_payload,
			})
		except Exception as e:
			logger.error(f"get_tables_schema failed: {e}")
			return self._result(False, {"error": str(e)})

	@tool(
		app_name="mariadb",
		tool_name="fetch_db_schema",
		description="Fetch schema for the default auth-configured database: tables, columns, primary/foreign keys and views",
		args_schema=FetchDBSchemaInput,
		parameters=[
			ToolParameter(
				name="include_views",
				type=ParameterType.BOOLEAN,
				description="Include view definitions in the response",
				required=False,
			),
		],
		returns="JSON schema payload grouped by database",
	)
	async def fetch_db_schema(
		self,
		include_views: bool = True,
	) -> Tuple[bool, str]:
		"""Fetch schema details for one MariaDB database context."""
		try:
			if include_views is not None:
				include_views = bool(include_views)

			target_db = self._get_default_database()

			if not target_db:
				return self._result(False, {
					"error": "No database selected",
					"message": "Configure a default database in connector auth",
				})

			databases_payload: List[Dict[str, Any]] = []

			for db_name in [target_db]:
				tables_resp = await self.client.list_tables(database=db_name)
				if not tables_resp.success:
					databases_payload.append({
						"name": db_name,
						"error": tables_resp.error or "Failed to list tables",
						"tables": [],
						"views": [],
					})
					continue

				table_payload: List[Dict[str, Any]] = []
				for table in tables_resp.data or []:
					table_name = table.get("name")
					if not table_name:
						continue

					info_resp = await self.client.get_table_info(table=table_name, database=db_name)
					if not info_resp.success:
						table_payload.append({
							"name": table_name,
							"error": info_resp.error or "Failed to fetch table info",
						})
						continue

					table_info = info_resp.data if isinstance(info_resp.data, dict) else {"name": table_name}

					pk_resp = await self.client.get_primary_keys(table=table_name, database=db_name)
					fk_resp = await self.client.get_foreign_keys(table=table_name, database=db_name)

					table_info["primary_keys"] = [
						row.get("column_name")
						for row in (pk_resp.data or [])
						if isinstance(row, dict) and row.get("column_name")
					] if pk_resp.success else []

					table_info["foreign_keys"] = fk_resp.data if fk_resp.success else []
					table_payload.append(table_info)

				view_payload: List[Dict[str, Any]] = []
				if include_views:
					views_resp = await self.client.list_views(database=db_name)
					if views_resp.success and isinstance(views_resp.data, list):
						view_payload = views_resp.data

				databases_payload.append({
					"name": db_name,
					"tables": table_payload,
					"views": view_payload,
					"table_count": len(table_payload),
					"view_count": len(view_payload),
				})

			return self._result(True, {
				"message": "Schema fetched successfully",
				"database_count": 1,
				"databases": databases_payload,
			})
		except Exception as e:
			logger.error(f"fetch_db_schema failed: {e}")
			return self._result(False, {"error": str(e)})

	@tool(
		app_name="mariadb",
		tool_name="execute_query",
		description="Execute a SQL query against the default auth-configured MariaDB database",
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
	) -> Tuple[bool, str]:
		"""Execute SQL query using configured MariaDB client."""
		try:
			if not query:
				return self._result(False, {
					"error": "Missing required parameter: query",
				})

			db_name = self._get_default_database()
			if not db_name:
				return self._result(False, {
					"error": "No database selected",
					"message": "Configure default database in connector auth",
				})

			safe_db = db_name.replace("`", "``")
			use_resp = await self.client.execute_query(f"USE `{safe_db}`")
			if not use_resp.success:
				return self._result(False, {
					"error": use_resp.error or "Failed to switch to default database",
					"message": use_resp.message,
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
				"database": db_name,
				"row_count": len(rows),
				"data": displayed_rows,
				**({"truncated": True, "displayed_row_count": DISPLAY_LIMIT} if len(rows) > DISPLAY_LIMIT else {}),
			}

			# Register background CSV export for conversation tasks (same as execute_query tool)
			conversation_id = self.chat_state.get("conversation_id")
			blob_store = self.chat_state.get("blob_storage")
			org_id = self.chat_state.get("org_id")
			logger.info(
				"MariaDB execute_query context: conversation_id=%s org_id=%s rows=%d blob_store=%s",
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
						"Could not initialize BlobStorage for MariaDB CSV export: %s",
						e,
					)
			if (
				rows
				and conversation_id
				and blob_store
				and org_id
			):
				logger.info("MariaDB CSV export conditions met; scheduling background task")
				columns = list(rows[0].keys()) if rows else []
				row_tuples = [tuple(r.get(c) for c in columns) for r in rows]
				raw_columns = columns
				raw_rows = row_tuples

				async def _save_csv_to_blob() -> Optional[Dict[str, Any]]:
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
							"MariaDB CSV export complete for conversation %s (%d rows)",
							conversation_id,
							len(raw_rows),
						)
						return {"type": "csv_download", **upload_info}
					except Exception:
						logger.exception(
							"Background MariaDB CSV export failed for conversation %s",
							conversation_id,
						)
						return None

				task = asyncio.create_task(_save_csv_to_blob())
				register_task(conversation_id, task)

			return self._result(True, result_payload)
		except Exception as e:
			logger.error(f"execute_query failed: {e}")
			return self._result(False, {"error": str(e)})





















