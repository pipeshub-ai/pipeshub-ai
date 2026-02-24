"""Execute SQL Query Tool for chatbot agent.

This module provides a tool for executing SQL queries against external data sources
like PostgreSQL and Snowflake. The tool takes a SQL query and source name,
determines the appropriate client to use, executes the query, and returns
results as markdown.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, TYPE_CHECKING

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from app.utils.logger import create_logger

if TYPE_CHECKING:
    from app.config.configuration_service import ConfigurationService

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
        description="Name of the data source to query (e.g., 'PostgreSQL', 'Snowflake'). Case-insensitive."
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
    else:
        return "unknown"

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
        if keyword in query_clean:
            return False, f"Blocked: Query contains prohibited keyword '{keyword}'"
    
    # Check for semicolon followed by dangerous statements (stacked queries)
    if ';' in query_clean:
        # Split by semicolon and check each statement
        statements = query_clean.split(';')
        for stmt in statements:
            stmt = stmt.strip()
            if not stmt:
                continue
            # Each statement must start with allowed keyword
            if not any(stmt.startswith(start) for start in [
                'SELECT', 'SHOW', 'DESCRIBE', 'DESC', 'EXPLAIN', 'WITH'
            ]):
                return False, "Blocked: Multi-statement queries must all be read-only"
    
    # Ensure query starts with allowed read-only operations
    allowed_starts = [
        # Standard SQL
        'SELECT',      # Standard queries
        'SHOW',        # Show tables, databases, etc.
        'DESCRIBE',    # Describe table structure
        'DESC',        # Short form of DESCRIBE
        'EXPLAIN',     # Query execution plan
        'WITH',        # CTEs (Common Table Expressions)
        
        # PostgreSQL specific
        'TABLE',       # TABLE table_name (equivalent to SELECT *)
        
        # MySQL specific
        'CHECK',       # CHECK TABLE (diagnostic)
        
        # Snowflake specific
        'LIST',        # List stages
        
        # SQL Server specific
        'DBCC',        # Database Console Commands (some are read-only)
        
        # Oracle specific
        'DUMP',        # DUMP (memory inspection, read-only)
        
        # BigQuery specific
        'DECLARE',     # Variable declaration (for scripting)
        'SET',         # Variable assignment in scripts
        
        # Multiple dialects
        'USE',         # Switch database/schema context (read-only)
        'VALUES',      # VALUES clause (can be used standalone for SELECT)
    ]
    
    if not any(query_clean.startswith(start) for start in allowed_starts):
        return False, f"Blocked: Query must start with read-only operation. Got: {query_clean.split()[0] if query_clean else 'empty'}"
    
    # Additional check: Detect write operations even inside CTEs or subqueries
    # Look for patterns like "INSERT INTO", "UPDATE SET", "DELETE FROM"
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
        if pattern in query_clean:
            return False, f"Blocked: Query contains write operation pattern '{pattern}'"
    
    # Detect xp_ stored procedures (SQL Server - can be dangerous)
    if 'XP_' in query_clean:
        return False, "Blocked: SQL Server extended stored procedures (xp_) are not allowed"
    
    # Detect eval/execute dynamic SQL attempts
    dynamic_sql_patterns = ['EXEC(', 'EXECUTE(', 'SP_EXECUTESQL', 'EXEC @', 'EXECUTE @']
    for pattern in dynamic_sql_patterns:
        if pattern in query_clean:
            return False, f"Blocked: Dynamic SQL execution is not allowed"
    
    return True, ""

def _source_type_to_connector_type(source_type: str) -> Optional[str]:
    """Map normalized source type to ArangoDB connector type.
    
    NOTE: Must match the 'type' field stored in ArangoDB apps collection.
    """
    mapping = {
        "postgres": "PostgreSQL",  # Matches app.type in ArangoDB
        "snowflake": "Snowflake",  # Matches app.type in ArangoDB
    }
    return mapping.get(source_type)


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
            logger.info(f"🔍 [_execute_postgres_query] Connected to PostgreSQL: host={connection_info.get('host')}, port={connection_info.get('port')}, database={connection_info.get('database')}, user={connection_info.get('user')}")
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


async def _execute_query_impl(
    query: str,
    source_name: str,
    config_service: "ConfigurationService",
    graph_provider: Optional["IGraphDBProvider"] = None,
    connector_instance_id: Optional[str] = None,
    org_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Main implementation for executing SQL queries.
    
    Args:
        query: SQL query to execute
        source_name: Name of the data source
        config_service: Configuration service
        graph_provider: Optional GraphDB service for looking up connector details
        connector_instance_id: Optional connector instance ID
        org_id: Optional organization ID for looking up connector
        
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
            "error": f"Unknown data source type: '{source_name}'. Supported types: PostgreSQL, Snowflake."
        }
    
    # Look up connector_instance_id if not provided
    logger.debug(
        f"Connector lookup check: connector_instance_id={connector_instance_id}, "
        f"graph_provider={'present' if graph_provider else 'None'}, org_id={org_id}"
    )
    if not connector_instance_id and graph_provider and org_id:
        connector_type = _source_type_to_connector_type(source_type)
        logger.debug(f"Looking up connector: source_type={source_type}, connector_type={connector_type}, org_id={org_id}")
        if connector_type:
            try:
                connector_instance_id = await graph_provider.get_connector_id_by_type(
                    org_id, connector_type
                )
                if connector_instance_id:
                    logger.info(f"Resolved connector_instance_id={connector_instance_id} for type={connector_type}")
                else:
                    logger.warning(f"No active {connector_type} connector found for org {org_id}")
            except Exception as e:
                logger.warning(f"Failed to lookup connector ID: {e}")
    elif not connector_instance_id:
        logger.warning(
            f"Skipping connector lookup: graph_provider={'present' if graph_provider else 'MISSING'}, "
            f"org_id={'present' if org_id else 'MISSING'}"
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
        }
        logger.info(f"🔍 [_execute_query_impl] Final result: ok=True, row_count={len(rows)}, column_count={len(columns)}")
        return final_result
    else:
        logger.warning(f"🔍 [_execute_query_impl] Query failed, returning error: {result.get('error')}")
        return result


def create_execute_query_tool(
    config_service: "ConfigurationService",
    graph_provider: Optional["IGraphDBProvider"] = None,
    connector_instance_id: Optional[str] = None,
    org_id: Optional[str] = None,
) -> Callable:
    """Factory function to create the execute_query tool with runtime dependencies.
    
    Args:
        config_service: Configuration service for retrieving connection details
        graph_provider: Optional GraphDB service for connector lookups
        connector_instance_id: Optional connector instance ID
        org_id: Optional organization ID for looking up connector by type
        
    Returns:
        A langchain tool for executing SQL queries
    """
    
    @tool("execute_sql_query", args_schema=ExecuteQueryArgs)
    async def execute_sql_query_tool(
        query: str,
        source_name: str,
        reason: str = "Executing SQL query to retrieve data",
    ) -> Dict[str, Any]:
        """Execute a SQL query against an external data source (PostgreSQL, Snowflake, etc.).
        
        Use this tool when you need to:
        - Query a database directly to retrieve specific data
        - Execute SQL queries provided in the context or generated based on table schemas
        - Get live data from connected data sources
        
        Args:
            query: The SQL query to execute (SELECT queries only for safety)
            source_name: Name of the data source (e.g., 'PostgreSQL', 'Snowflake')
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
        logger.info(f"🔍 [execute_sql_query_tool] Called with source_name={source_name}, reason={reason}")
        logger.debug(f"🔍 [execute_sql_query_tool] Query: {query}")
        
        try:
            result = await _execute_query_impl(
                query=query,
                source_name=source_name,
                config_service=config_service,
                graph_provider=graph_provider,
                connector_instance_id=connector_instance_id,
                org_id=org_id,
            )
            
            logger.info(f"🔍 [execute_sql_query_tool] Got result: ok={result.get('ok')}, row_count={result.get('row_count')}, column_count={result.get('column_count')}")
            if result.get('ok'):
                logger.debug(f"🔍 [execute_sql_query_tool] Result markdown (first 500 chars): {result.get('markdown_result', '')[:500]}")
            else:
                logger.warning(f"🔍 [execute_sql_query_tool] Error in result: {result.get('error')}")
            
            return result
        except Exception as e:
            logger.exception("execute_sql_query_tool failed")
            return {
                "ok": False,
                "error": f"Query execution failed: {str(e)}"
            }
    
    return execute_sql_query_tool
