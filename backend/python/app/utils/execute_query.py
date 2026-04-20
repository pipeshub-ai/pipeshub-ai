"""Execute SQL Query Tool for chatbot agent.

This module provides a tool for executing SQL queries against external data sources
like PostgreSQL, Snowflake, and MariaDB. The tool takes a SQL query and source name,
determines the appropriate client to use, executes the query, and returns
results as markdown.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any, Callable, Dict, List, Optional, TYPE_CHECKING

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from app.utils.conversation_tasks import register_task, _rows_to_csv_bytes
from app.utils.logger import create_logger

if TYPE_CHECKING:
    from app.config.configuration_service import ConfigurationService
    from app.modules.transformers.blob_storage import BlobStorage

from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

logger = create_logger("execute_query")


class ExecuteQueryArgs(BaseModel):
    """Required tool args for executing SQL queries."""
    
    query: str = Field(
        ...,
        description="The SQL query to execute against the data source."
    )
    source_name: str = Field(
        ...,
        description="Name of the data source to query (e.g., 'PostgreSQL', 'Snowflake', 'MariaDB'). Case-insensitive."
    )
    connector_id: str = Field(
        ...,
        description=(
            "The connector instance ID identifying which database connector to execute against. "
            "REQUIRED: Extract this from the 'Connector Id' field shown in the record context. "
            "Multiple connectors of the same type (e.g. two PostgreSQL instances) may exist, "
            "so always use the connector_id associated with the tables you are querying."
        ),
    )
    reason: str = Field(
        default="Executing SQL query to retrieve data",
        description="Why this query is needed to answer the user's question."
    )


def _detect_source_type(source_name: str) -> str:
    source_lower = source_name.lower()
    if "postgres" in source_lower:
        return "postgres"
    elif "snowflake" in source_lower:
        return "snowflake"
    elif "mariadb" in source_lower:
        return "mariadb"
    else:
        return "unknown"

import re
def _is_keyword_present(keyword: str, query: str) -> bool:
    """Check if a keyword exists as a standalone word/phrase, not as part of another word."""
    pattern = r'\b' + re.escape(keyword) + r'\b'
    return bool(re.search(pattern, query))


def _is_query_safe(query: str) -> tuple[bool, str]:
    """Validate that query is read-only across all SQL dialects.

    Returns:
        (is_safe, error_message) tuple
    """
    query_upper = query.upper().strip()

    # Remove comments and extra whitespace
    query_clean = ' '.join(query_upper.split())

    # Dangerous keywords that should never appear (cross-dialect)
    dangerous_keywords = [
        # DML write operations
        'INSERT', 'UPDATE', 'DELETE', 'MERGE', 'REPLACE', 'UPSERT',
        # DDL operations
        'DROP', 'CREATE', 'ALTER', 'TRUNCATE', 'RENAME',
        # DCL operations
        'GRANT', 'REVOKE',
        # Procedure/function execution
        'EXEC', 'EXECUTE', 'CALL', 'DO',
        # File operations (MySQL, PostgreSQL)
        'INTO OUTFILE', 'INTO DUMPFILE', 'LOAD_FILE', 'LOAD DATA', 'LOAD XML',
        # Copy operations (PostgreSQL, Snowflake)
        'COPY', 'PUT', 'GET', 'REMOVE',
        # System operations
        'SHUTDOWN', 'KILL', 'RESET MASTER', 'RESET SLAVE',
        # Transaction control (potentially dangerous with write access)
        'COMMIT', 'ROLLBACK', 'SAVEPOINT', 'BEGIN', 'START TRANSACTION',
        # Lock operations
        'LOCK', 'UNLOCK',
        # Import/Export (SQL Server, Oracle)
        'BULK INSERT', 'OPENROWSET', 'OPENDATASOURCE',
        # External operations (Oracle)
        'UTL_FILE', 'DBMS_',
        # Snowflake specific dangerous operations
        'CLONE', 'SWAP', 'UNDROP',
        # BigQuery specific
        'EXPORT DATA',
        # Administrative commands
        'VACUUM', 'ANALYZE', 'REINDEX', 'CLUSTER',
        # Security bypass attempts
        'PRAGMA', 'SET SQL_LOG_BIN', 'SET GLOBAL',
    ]

    for keyword in dangerous_keywords:
        if _is_keyword_present(keyword, query_clean):
            return False, f"Blocked: Query contains prohibited keyword '{keyword}'"

    # Check for semicolon followed by dangerous statements (stacked queries)
    if ';' in query_clean:
        statements = query_clean.split(';')
        for stmt in statements:
            stmt = stmt.strip()
            if not stmt:
                continue
            if not any(stmt.startswith(start) for start in [
                'SELECT', 'SHOW', 'DESCRIBE', 'DESC', 'EXPLAIN', 'WITH'
            ]):
                return False, "Blocked: Multi-statement queries must all be read-only"

    # Ensure query starts with allowed read-only operations
    allowed_starts = [
        # Standard SQL
        'SELECT', 'SHOW', 'DESCRIBE', 'DESC', 'EXPLAIN', 'WITH',
        # PostgreSQL specific
        'TABLE',
        # MySQL specific
        'CHECK',
        # Snowflake specific
        'LIST',
        # SQL Server specific
        'DBCC',
        # Oracle specific
        'DUMP',
        # BigQuery specific
        'DECLARE', 'SET',
        # Multiple dialects
        'USE', 'VALUES',
    ]

    if not any(query_clean.startswith(start) for start in allowed_starts):
        return False, f"Blocked: Query must start with read-only operation. Got: {query_clean.split()[0] if query_clean else 'empty'}"

    # Additional check: Detect write operations even inside CTEs or subqueries
    dangerous_patterns = [
        'INSERT INTO',
        'INSERT OVERWRITE',  # Hive/Spark
        'UPDATE SET',
        'UPDATE ',
        'DELETE FROM',
        'DELETE WHERE',
        'MERGE INTO',
        'REPLACE INTO',
    ]

    for pattern in dangerous_patterns:
        if _is_keyword_present(pattern, query_clean):
            return False, f"Blocked: Query contains write operation pattern '{pattern}'"

    # Detect xp_ stored procedures (SQL Server - can be dangerous)
    if _is_keyword_present('XP_', query_clean):
        return False, "Blocked: SQL Server extended stored procedures (xp_) are not allowed"

    # Detect eval/execute dynamic SQL attempts
    dynamic_sql_patterns = ['EXEC(', 'EXECUTE(', 'SP_EXECUTESQL', 'EXEC @', 'EXECUTE @']
    for pattern in dynamic_sql_patterns:
        if pattern in query_clean:
            return False, "Blocked: Dynamic SQL execution is not allowed"

    return True, ""




def _results_to_markdown(columns: List[str], rows: List[tuple]) -> str:
    """Convert query results to a markdown table.
    
    Args:
        columns: List of column names
        rows: List of row tuples
        
    Returns:
        Markdown formatted table string
    """
    if not columns:
        return "_No results returned._"
    
    if not rows:
        return f"_Query executed successfully but returned no rows._\n\nColumns: {', '.join(columns)}"
    
    # Limit rows for readability
    max_rows = 100
    truncated = len(rows) > max_rows
    display_rows = rows[:max_rows]
    
    # Build markdown table
    lines = []
    
    # Header row
    header = "| " + " | ".join(str(col) for col in columns) + " |"
    lines.append(header)
    
    # Separator row
    separator = "| " + " | ".join("---" for _ in columns) + " |"
    lines.append(separator)
    
    # Data rows
    for row in display_rows:
        # Handle None values and escape pipe characters
        formatted_cells = []
        for cell in row:
            if cell is None:
                formatted_cells.append("NULL")
            else:
                # Convert to string and escape pipes
                cell_str = str(cell).replace("|", "\\|")
                # Truncate very long values
                if len(cell_str) > 100:
                    cell_str = cell_str[:97] + "..."
                formatted_cells.append(cell_str)
        
        line = "| " + " | ".join(formatted_cells) + " |"
        lines.append(line)
    
    result = "\n".join(lines)
    
    if truncated:
        result += f"\n\n_Showing {max_rows} of {len(rows)} total rows._"
    else:
        result += f"\n\n_Total: {len(rows)} rows._"
    
    return result


async def _execute_postgres_query(
    query: str,
    config_service: "ConfigurationService",
    connector_instance_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Execute a query against PostgreSQL.
    
    Args:
        query: SQL query to execute
        config_service: Configuration service for retrieving connection details
        connector_instance_id: Optional connector instance ID
        
    Returns:
        Dict with 'ok', 'columns', 'rows' or 'error'
    """
    logger.info(f"🔍 [_execute_postgres_query] Starting execution with connector_id={connector_instance_id}")
    logger.debug(f"🔍 [_execute_postgres_query] Query: {query}")
    
    try:
        from app.sources.client.postgres.postgres import PostgreSQLClientBuilder
        
        logger.debug("🔍 [_execute_postgres_query] Building client from services...")
        client_builder = await PostgreSQLClientBuilder.build_from_services(
            logger=logger,
            config_service=config_service,
            connector_instance_id=connector_instance_id,
        )
        
        client = client_builder.get_client()
        logger.debug(f"🔍 [_execute_postgres_query] Client built: {client.get_connection_info()}")
        
        with client:
            connection_info = client.get_connection_info()
            logger.debug(f"🔍 [_execute_postgres_query] Connected to PostgreSQL: host={connection_info.get('host')}, port={connection_info.get('port')}, database={connection_info.get('database')}, user={connection_info.get('user')}")
            logger.debug(f"🔍 [_execute_postgres_query] Full connection info: {connection_info}")
            logger.info(f"🔍 [_execute_postgres_query] Executing query: {query}")
            
            columns, rows = client.execute_query_raw(query)
            
            # Log what we got from the database
            logger.info(f"🔍 [_execute_postgres_query] Query returned {len(columns)} columns, {len(rows)} rows")
            logger.debug(f"🔍 [_execute_postgres_query] Columns: {columns}")
            
            if rows:
                logger.debug(f"🔍 [_execute_postgres_query] First row: {rows[0]}")
                logger.debug(f"🔍 [_execute_postgres_query] Row types: {[type(cell).__name__ for cell in rows[0]]}")
            else:
                # IMPORTANT: Log warning when no rows returned - this helps debug "empty result" issues
                logger.warning(f"🔍 [_execute_postgres_query] ⚠️ QUERY RETURNED NO ROWS!")
                logger.warning(f"🔍 [_execute_postgres_query] Query was: {query}")
                logger.warning(f"🔍 [_execute_postgres_query] Database: {connection_info.get('database')} on {connection_info.get('host')}")
            
            result = {
                "ok": True,
                "columns": columns,
                "rows": rows,
            }
            logger.info(f"🔍 [_execute_postgres_query] Returning result with ok=True")
            return result
            
    except Exception as e:
        logger.error(f"PostgreSQL query execution failed: {e}", exc_info=True)
        return {
            "ok": False,
            "error": f"PostgreSQL query failed: {str(e)}"
        }


async def _execute_snowflake_query(
    query: str,
    config_service: "ConfigurationService",
    connector_instance_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Execute a query against Snowflake.
    
    Args:
        query: SQL query to execute
        config_service: Configuration service for retrieving connection details
        connector_instance_id: Optional connector instance ID
        
    Returns:
        Dict with 'ok', 'columns', 'rows' or 'error'
    """
    try:
        from app.sources.client.snowflake.snowflake import (
            SnowflakeSDKClient,
            SnowflakeClient,
            SnowflakeConnectorConfig,
        )
        
        # Get connection config
        config_dict = await SnowflakeClient._get_connector_config(
            logger=logger,
            config_service=config_service,
            connector_instance_id=connector_instance_id,
        )
        
        config = SnowflakeConnectorConfig.model_validate(config_dict)
        account_identifier = config.accountIdentifier
        warehouse = config.warehouse
        
        # Build SDK client based on auth type
        auth_config = config.auth
        auth_type = auth_config.authType.value if hasattr(auth_config.authType, 'value') else str(auth_config.authType)
        
        if auth_type == "PAT":
            # PAT auth - use SDK client with token
            pat_token = auth_config.patToken
            if not pat_token:
                return {
                    "ok": False,
                    "error": "PAT token not configured for Snowflake connector"
                }
            
            sdk_client = SnowflakeSDKClient(
                account_identifier=account_identifier,
                warehouse=warehouse,
                oauth_token=pat_token,  # PAT tokens work with oauth authenticator
            )
        elif auth_type == "OAUTH":
            # OAuth auth
            credentials = config.credentials
            if not credentials or not credentials.access_token:
                return {
                    "ok": False,
                    "error": "OAuth access token not configured for Snowflake connector"
                }
            
            sdk_client = SnowflakeSDKClient(
                account_identifier=account_identifier,
                warehouse=warehouse,
                oauth_token=credentials.access_token,
            )
        else:
            return {
                "ok": False,
                "error": f"Unsupported Snowflake auth type: {auth_type}"
            }
        
        with sdk_client:
            columns, rows = sdk_client.execute_query_raw(query)
            return {
                "ok": True,
                "columns": columns,
                "rows": rows,
            }
            
    except Exception as e:
        logger.error(f"Snowflake query execution failed: {e}")
        return {
            "ok": False,
            "error": f"Snowflake query failed: {str(e)}"
        }


async def _execute_mariadb_query(
    query: str,
    config_service: "ConfigurationService",
    connector_instance_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Execute a query against MariaDB.
    
    Args:
        query: SQL query to execute
        config_service: Configuration service
        connector_instance_id: Optional connector instance ID
        
    Returns:
        Dict with 'ok', 'columns', 'rows' or 'error'
    """
    try:
        from app.sources.client.mariadb.mariadb import MariaDBClientBuilder

        logger.debug("🔍 [_execute_mariadb_query] Building client from services...")
        client_builder = await MariaDBClientBuilder.build_from_services(
            logger=logger,
            config_service=config_service,
            connector_instance_id=connector_instance_id,
        )

        client = client_builder.get_client()
        logger.debug(f"🔍 [_execute_mariadb_query] Client built: {client.get_connection_info()}")

        with client:
            connection_info = client.get_connection_info()
            logger.debug(
                "🔍 [_execute_mariadb_query] Connected to MariaDB: "
                f"host={connection_info.get('host')}, "
                f"port={connection_info.get('port')}, "
                f"database={connection_info.get('database')}, "
                f"user={connection_info.get('user')}"
            )
            logger.info(f"🔍 [_execute_mariadb_query] Executing query: {query}")

            columns, rows = client.execute_query_raw(query)

            logger.info(
                f"🔍 [_execute_mariadb_query] Query returned {len(columns)} columns, {len(rows)} rows"
            )

            return {
                "ok": True,
                "columns": columns,
                "rows": rows,
            }
    except Exception as e:
        logger.error(f"MariaDB query execution failed: {e}", exc_info=True)
        return {
            "ok": False,
            "error": f"MariaDB query failed: {str(e)}"
        }

async def _execute_query_impl(
    query: str,
    source_name: str,
    config_service: "ConfigurationService",
    connector_instance_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Main implementation for executing SQL queries.
    
    Args:
        query: SQL query to execute
        source_name: Name of the data source
        config_service: Configuration service
        connector_instance_id: Connector instance ID (should be provided by the agent
            from the record context; required when multiple connectors of the same
            type exist)
        
    Returns:
        Dict with 'ok', 'markdown_result' or 'error'
    """
    is_safe, error_msg = _is_query_safe(query)
    if not is_safe:
        logger.warning(f"Query blocked for security: {error_msg}")
        return {
            "ok": False,
            "error": error_msg
        }
    source_type = _detect_source_type(source_name)
    
    if source_type == "unknown":
        return {
            "ok": False,
            "error": f"Unknown data source type: '{source_name}'. Supported types: PostgreSQL, Snowflake, MariaDB."
        }
    
    connector_instance_id = (connector_instance_id or "").strip() or None
    if not connector_instance_id:
        logger.warning(
            "No connector_instance_id provided for source_type=%s. "
            "The underlying client will attempt default config resolution, "
            "which may fail or target the wrong instance when multiple connectors exist.",
            source_type,
        )
    
    logger.info(f"Executing {source_type} query: {query[:100]}...")
    
    # Execute query based on source type
    if source_type == "postgres":
        result = await _execute_postgres_query(
            query=query,
            config_service=config_service,
            connector_instance_id=connector_instance_id,
        )
    elif source_type == "snowflake":
        result = await _execute_snowflake_query(
            query=query,
            config_service=config_service,
            connector_instance_id=connector_instance_id,
        )
    elif source_type == "mariadb":
        result = await _execute_mariadb_query(
            query=query,
            config_service=config_service,
            connector_instance_id=connector_instance_id,
        )
    else:
        return {
            "ok": False,
            "error": f"Source type '{source_type}' is not yet implemented."
        }
    
    # Convert results to markdown
    if result.get("ok"):
        columns = result.get("columns", [])
        rows = result.get("rows", [])
        
        logger.info(f"🔍 [_execute_query_impl] Converting to markdown: {len(columns)} cols, {len(rows)} rows")
        logger.debug(f"🔍 [_execute_query_impl] Columns: {columns}")
        logger.debug(f"🔍 [_execute_query_impl] Rows: {rows}")
        
        markdown = _results_to_markdown(columns, rows)
        
        logger.info(f"🔍 [_execute_query_impl] Markdown generated (length={len(markdown)})")
        logger.debug(f"🔍 [_execute_query_impl] Markdown content:\n{markdown}")
        
        final_result = {
            "ok": True,
            "markdown_result": markdown,
            "row_count": len(rows),
            "column_count": len(columns),
            "raw_columns": columns,
            "raw_rows": rows,
        }
        logger.info(f"🔍 [_execute_query_impl] Final result: ok=True, row_count={len(rows)}, column_count={len(columns)}")
        return final_result
    else:
        logger.warning(f"🔍 [_execute_query_impl] Query failed, returning error: {result.get('error')}")
        return result


def create_execute_query_tool(
    config_service: "ConfigurationService",
    graph_provider: Optional["IGraphDBProvider"] = None,
    org_id: Optional[str] = None,
    conversation_id: Optional[str] = None,
    blob_store: Optional["BlobStorage"] = None,
) -> Callable:
    """Factory function to create the execute_query tool with runtime dependencies.
    
    Args:
        config_service: Configuration service for retrieving connection details
        graph_provider: Optional GraphDB service (used for BlobStorage fallback)
        org_id: Optional organization ID for background CSV export
        conversation_id: Optional conversation ID for background CSV export
        blob_store: Optional blob storage for saving full result CSVs
        
    Returns:
        A langchain tool for executing SQL queries
    """
    
    @tool("execute_sql_query", args_schema=ExecuteQueryArgs)
    async def execute_sql_query_tool(
        query: str,
        source_name: str,
        connector_id: str = "",
        reason: str = "Executing SQL query to retrieve data",
    ) -> Dict[str, Any]:
        """Execute a read-only SQL query against an external data source.

        Supported sources: PostgreSQL, Snowflake, MariaDB.

        IMPORTANT — connector_id is required:
        - Each table in the context includes a 'Connector Id' field.
        - You MUST pass the connector_id associated with the tables you are querying.
        - Multiple connectors of the same database type may exist; the connector_id
          ensures the query runs against the correct instance.

        Orchestration rules:
        - One connector per call. If tables live in different connectors, make
          separate calls and merge results yourself.
        - Only SELECT / read-only queries are allowed.
        
        Args:
            query: The SQL query to execute (SELECT queries only for safety)
            source_name: Name of the data source (e.g., 'PostgreSQL', 'Snowflake', 'MariaDB')
            connector_id: Connector instance ID from the record context (required)
            reason: Explanation of why this query is needed
            
        Returns:
            {
                "ok": true,
                "markdown_result": "| col1 | col2 |\\n|---|---|\\n| val1 | val2 |",
                "row_count": N,
                "column_count": M
            }
            or {"ok": false, "error": "..."}
        """
        logger.info(
            "🔍 [execute_sql_query_tool] Called with source_name=%s, connector_id=%s",
            source_name,
            connector_id,
        )
        logger.debug(f"🔍 [execute_sql_query_tool] Query: {query}")
        
        try:
            result = await _execute_query_impl(
                query=query,
                source_name=source_name,
                config_service=config_service,
                connector_instance_id=(connector_id or "").strip() or None,
            )
            
            logger.info(f"🔍 [execute_sql_query_tool] Got result: ok={result.get('ok')}, row_count={result.get('row_count')}, column_count={result.get('column_count')}")
            if result.get('ok'):
                logger.debug(f"🔍 [execute_sql_query_tool] Result markdown (first 500 chars): {result.get('markdown_result', '')[:500]}")
            else:
                logger.warning(f"🔍 [execute_sql_query_tool] Error in result: {result.get('error')}")

            # Fire background CSV export task when all prerequisites are met
            raw_columns = result.pop("raw_columns", None)
            raw_rows = result.pop("raw_rows", None)
            resolved_blob_store = blob_store
            if not conversation_id:
                logger.warning("Cannot register background CSV export task: conversation_id is missing")
            if not org_id:
                logger.warning("Cannot register background CSV export task: org_id is missing")
            if not resolved_blob_store:
                from app.modules.transformers.blob_storage import BlobStorage

                resolved_blob_store = BlobStorage(
                    logger=logger,
                    config_service=config_service,
                    graph_provider=graph_provider,
                )
            if (
                result.get("ok")
                and raw_columns
                and raw_rows
                and conversation_id
                and resolved_blob_store
                and org_id
            ):
                async def _save_csv_to_blob() -> Optional[Dict[str, Any]]:
                    try:
                        csv_bytes = _rows_to_csv_bytes(raw_columns, raw_rows)
                        file_name = f"query_result_{int(time.time())}.csv"
                        upload_info = await resolved_blob_store.save_conversation_file_to_storage(
                            org_id=org_id,
                            conversation_id=conversation_id,
                            file_name=file_name,
                            file_bytes=csv_bytes,
                        )
                        logger.info(
                            "CSV export complete for conversation %s (%d rows)",
                            conversation_id,
                            len(raw_rows),
                        )
                        return {"type": "csv_download", **upload_info}
                    except Exception:
                        logger.exception(
                            "Background CSV export failed for conversation %s",
                            conversation_id,
                        )
                        return None

                task = asyncio.create_task(_save_csv_to_blob())
                register_task(conversation_id, task)
            
            return result
        except Exception as e:
            logger.exception("execute_sql_query_tool failed")
            return {
                "ok": False,
                "error": f"Query execution failed: {str(e)}"
            }
    
    return execute_sql_query_tool
