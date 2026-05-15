"""
Redshift DataSource - Database metadata and query operations

Provides async wrapper methods for Redshift operations:
- Database and schema operations
- Table and view metadata
- Column information
- Foreign key relationships
- Index information

Key differences from PostgreSQL DataSource:
- pg_database_size() and pg_size_pretty() are NOT available in Redshift
- format_type() and pg_get_expr() are NOT available in Redshift
- DDL reconstruction uses information_schema + SVV_TABLE_INFO instead
- Uses RedshiftResponse instead of PostgreSQLResponse
- Default port is 5439
"""

import asyncio
import logging
from typing import Any, Optional

from app.sources.client.redshift.redshift import RedshiftClient, RedshiftResponse

logger = logging.getLogger(__name__)

REDSHIFT_TABLE_ROW_LIMIT = 100000


class RedshiftDataSource:
    """Redshift DataSource for database operations.

    Provides methods for fetching metadata and executing queries against Amazon Redshift.
    """

    def __init__(self, client: RedshiftClient) -> None:
        """Initialize with Redshift client.

        Args:
            client: RedshiftClient instance with configured authentication
        """
        logger.debug("🔧 [RedshiftDataSource] __init__ called")
        self._client = client
        logger.info("🔧 [RedshiftDataSource] Initialized successfully")

    def get_data_source(self) -> "RedshiftDataSource":
        """Return the data source instance."""
        return self

    def get_client(self) -> RedshiftClient:
        """Return the underlying Redshift client."""
        return self._client

    async def list_databases(self) -> RedshiftResponse:
        """List all accessible databases.

        Note: pg_database_size() is NOT available in Redshift.
        Uses pg_database catalog directly without size information.

        Returns:
            RedshiftResponse with list of databases
        """
        logger.debug("🔧 [RedshiftDataSource] list_databases called")

        # Redshift does not support pg_database_size() or pg_encoding_to_char()
        query = """
            SELECT
                datname AS name,
                datdba::text AS owner
            FROM pg_database
            WHERE datistemplate = false
            ORDER BY datname;
        """

        try:
            results = await asyncio.to_thread(self._client.execute_query, query)
            logger.debug(f"🔧 [RedshiftDataSource] Found {len(results)} databases")

            return RedshiftResponse(
                success=True,
                data=results,
                message=f"Successfully listed {len(results)} databases",
            )
        except Exception as e:
            logger.error(f"🔧 [RedshiftDataSource] list_databases failed: {e}")
            return RedshiftResponse(
                success=False,
                error=str(e),
                message="Failed to list databases",
            )

    async def list_schemas(self, database: Optional[str] = None) -> RedshiftResponse:
        """List all schemas in the current database.

        Uses pg_namespace instead of information_schema.schemata because in
        Redshift the schemata view only returns schemas the current user
        OWNS or holds an explicit privilege on. Schemas reached via inherited
        or default ACLs (e.g. `public` for non-admin users) are invisible
        there, even though the user can SELECT from their tables. pg_namespace
        is visible to all users and reports every schema in the database.

        Args:
            database: Database name (not used, kept for API compatibility)

        Returns:
            RedshiftResponse with list of schemas
        """
        logger.debug("🔧 [RedshiftDataSource] list_schemas called")

        query = """
            SELECT
                n.nspname AS name,
                u.usename AS owner
            FROM pg_namespace n
            LEFT JOIN pg_user u ON u.usesysid = n.nspowner
            WHERE n.nspname NOT IN ('pg_catalog', 'information_schema', 'pg_toast',
                                    'pg_internal', 'catalog_history')
              AND n.nspname NOT LIKE 'pg_temp_%'
              AND n.nspname NOT LIKE 'pg_toast_temp_%'
            ORDER BY n.nspname;
        """

        try:
            results = await asyncio.to_thread(self._client.execute_query, query)
            logger.debug(f"🔧 [RedshiftDataSource] Found {len(results)} schemas")

            return RedshiftResponse(
                success=True,
                data=results,
                message=f"Successfully listed {len(results)} schemas",
            )
        except Exception as e:
            logger.error(f"🔧 [RedshiftDataSource] list_schemas failed: {e}")
            return RedshiftResponse(
                success=False,
                error=str(e),
                message="Failed to list schemas",
            )

    async def list_tables(self, schema: str = "public") -> RedshiftResponse:
        """List all tables in a schema.

        Args:
            schema: Schema name (default: public)

        Returns:
            RedshiftResponse with list of tables
        """
        logger.debug(f"🔧 [RedshiftDataSource] list_tables called for schema: {schema}")

        query = """
            SELECT
                table_name AS name,
                table_schema AS schema,
                table_type AS type
            FROM information_schema.tables
            WHERE table_schema = %s
              AND table_type = 'BASE TABLE'
            ORDER BY table_name;
        """

        try:
            results = await asyncio.to_thread(
                self._client.execute_query, query, (schema,)
            )
            logger.debug(f"🔧 [RedshiftDataSource] Found {len(results)} tables")

            return RedshiftResponse(
                success=True,
                data=results,
                message=f"Successfully listed {len(results)} tables",
            )
        except Exception as e:
            logger.error(f"🔧 [RedshiftDataSource] list_tables failed: {e}")
            return RedshiftResponse(
                success=False,
                error=str(e),
                message="Failed to list tables",
            )

    async def get_table_info(self, schema: str, table: str) -> RedshiftResponse:
        """Get detailed information about a table.

        Args:
            schema: Schema name
            table: Table name

        Returns:
            RedshiftResponse with table information including columns with
            complete type info (precision, scale, length), constraints, and defaults
        """
        logger.debug(f"🔧 [RedshiftDataSource] get_table_info called for {schema}.{table}")

        table_query = """
            SELECT
                table_name AS name,
                table_schema AS schema,
                table_type AS type
            FROM information_schema.tables
            WHERE table_schema = %s AND table_name = %s;
        """

        # Redshift supports information_schema.columns fully
        columns_query = """
            SELECT
                c.column_name AS name,
                c.data_type,
                c.udt_name,
                c.character_maximum_length,
                c.numeric_precision,
                c.numeric_scale,
                c.datetime_precision,
                CASE WHEN c.is_nullable = 'YES' THEN true ELSE false END AS nullable,
                c.column_default AS "default"
            FROM information_schema.columns c
            WHERE c.table_schema = %s AND c.table_name = %s
            ORDER BY c.ordinal_position;
        """

        # UNIQUE constraints via information_schema (works in Redshift)
        unique_query = """
            SELECT DISTINCT kcu.column_name
            FROM information_schema.table_constraints tc
            JOIN information_schema.key_column_usage kcu
              ON tc.constraint_name = kcu.constraint_name
              AND tc.table_schema = kcu.table_schema
            WHERE tc.constraint_type = 'UNIQUE'
              AND tc.table_schema = %s
              AND tc.table_name = %s;
        """

        # Note: Redshift has limited CHECK constraint support —
        # CHECK constraints are accepted in DDL but NOT enforced,
        # and may not appear reliably in information_schema.check_constraints.
        # We still query for them but results may be empty.
        check_query = """
            SELECT
                cc.constraint_name,
                cc.check_clause
            FROM information_schema.check_constraints cc
            JOIN information_schema.table_constraints tc
              ON cc.constraint_name = tc.constraint_name
              AND cc.constraint_schema = tc.table_schema
            WHERE tc.table_schema = %s
              AND tc.table_name = %s
              AND tc.constraint_type = 'CHECK'
              AND cc.check_clause NOT LIKE '%%IS NOT NULL%%';
        """

        try:
            table_info = await asyncio.to_thread(
                self._client.execute_query, table_query, (schema, table)
            )
            if not table_info:
                return RedshiftResponse(
                    success=False,
                    error="Table not found",
                    message=f"Table {schema}.{table} not found",
                )

            columns = await asyncio.to_thread(
                self._client.execute_query, columns_query, (schema, table)
            )
            unique_cols = await asyncio.to_thread(
                self._client.execute_query, unique_query, (schema, table)
            )
            check_constraints = await asyncio.to_thread(
                self._client.execute_query, check_query, (schema, table)
            )

            # Build set of unique column names
            unique_column_names = {row.get("column_name") for row in unique_cols}

            # Enrich columns with unique constraint info
            for col in columns:
                col["is_unique"] = col.get("name") in unique_column_names

            result = table_info[0]
            result["columns"] = columns
            result["check_constraints"] = check_constraints

            logger.debug(f"🔧 [RedshiftDataSource] Table has {len(columns)} columns")

            return RedshiftResponse(
                success=True,
                data=result,
                message=f"Successfully retrieved table info for {schema}.{table}",
            )
        except Exception as e:
            logger.error(f"🔧 [RedshiftDataSource] get_table_info failed: {e}")
            return RedshiftResponse(
                success=False,
                error=str(e),
                message="Failed to get table info",
            )

    async def list_views(self, schema: str = "public") -> RedshiftResponse:
        """List all views in a schema.

        Args:
            schema: Schema name (default: public)

        Returns:
            RedshiftResponse with list of views
        """
        logger.debug(f"🔧 [RedshiftDataSource] list_views called for schema: {schema}")

        query = """
            SELECT
                table_name AS name,
                table_schema AS schema,
                view_definition AS definition
            FROM information_schema.views
            WHERE table_schema = %s
            ORDER BY table_name;
        """

        try:
            results = await asyncio.to_thread(
                self._client.execute_query, query, (schema,)
            )
            logger.debug(f"🔧 [RedshiftDataSource] Found {len(results)} views")

            return RedshiftResponse(
                success=True,
                data=results,
                message=f"Successfully listed {len(results)} views",
            )
        except Exception as e:
            logger.error(f"🔧 [RedshiftDataSource] list_views failed: {e}")
            return RedshiftResponse(
                success=False,
                error=str(e),
                message="Failed to list views",
            )

    async def get_foreign_keys(self, schema: str, table: str) -> RedshiftResponse:
        """Get foreign key relationships for a table.

        Note: Redshift accepts FOREIGN KEY constraints in DDL but does NOT enforce them.
        They will still appear in information_schema if declared.

        Args:
            schema: Schema name
            table: Table name

        Returns:
            RedshiftResponse with foreign key information
        """
        logger.debug(
            f"🔧 [RedshiftDataSource] get_foreign_keys called for {schema}.{table}"
        )

        query = """
            SELECT
                tc.constraint_name,
                kcu.column_name,
                ccu.table_schema AS foreign_table_schema,
                ccu.table_name AS foreign_table_name,
                ccu.column_name AS foreign_column_name
            FROM information_schema.table_constraints AS tc
            JOIN information_schema.key_column_usage AS kcu
              ON tc.constraint_name = kcu.constraint_name
              AND tc.table_schema = kcu.table_schema
            JOIN information_schema.constraint_column_usage AS ccu
              ON ccu.constraint_name = tc.constraint_name
              AND ccu.table_schema = tc.table_schema
            WHERE tc.constraint_type = 'FOREIGN KEY'
              AND tc.table_schema = %s
              AND tc.table_name = %s;
        """

        try:
            results = await asyncio.to_thread(
                self._client.execute_query, query, (schema, table)
            )
            logger.debug(
                f"🔧 [RedshiftDataSource] Found {len(results)} foreign keys"
            )

            return RedshiftResponse(
                success=True,
                data=results,
                message=f"Successfully retrieved foreign keys for {schema}.{table}",
            )
        except Exception as e:
            logger.error(f"🔧 [RedshiftDataSource] get_foreign_keys failed: {e}")
            return RedshiftResponse(
                success=False,
                error=str(e),
                message="Failed to get foreign keys",
            )

    async def get_primary_keys(self, schema: str, table: str) -> RedshiftResponse:
        """Get primary key columns for a table.

        Args:
            schema: Schema name
            table: Table name

        Returns:
            RedshiftResponse with primary key column names
        """
        logger.debug(
            f"🔧 [RedshiftDataSource] get_primary_keys called for {schema}.{table}"
        )

        query = """
            SELECT
                kcu.column_name
            FROM information_schema.table_constraints AS tc
            JOIN information_schema.key_column_usage AS kcu
              ON tc.constraint_name = kcu.constraint_name
              AND tc.table_schema = kcu.table_schema
            WHERE tc.constraint_type = 'PRIMARY KEY'
              AND tc.table_schema = %s
              AND tc.table_name = %s
            ORDER BY kcu.ordinal_position;
        """

        try:
            results = await asyncio.to_thread(
                self._client.execute_query, query, (schema, table)
            )
            logger.debug(
                f"🔧 [RedshiftDataSource] Found {len(results)} primary key columns"
            )

            return RedshiftResponse(
                success=True,
                data=results,
                message=f"Successfully retrieved primary keys for {schema}.{table}",
            )
        except Exception as e:
            logger.error(f"🔧 [RedshiftDataSource] get_primary_keys failed: {e}")
            return RedshiftResponse(
                success=False,
                error=str(e),
                message="Failed to get primary keys",
            )

    async def get_table_ddl(self, schema: str, table: str) -> RedshiftResponse:
        """Get the DDL (CREATE TABLE statement) for a table.

        Reconstructs the CREATE TABLE statement from information_schema since
        Redshift does NOT support format_type() or pg_get_expr() which are
        used in the PostgreSQL equivalent.

        Includes:
        - Column definitions with type info derived from information_schema
        - NOT NULL constraints
        - DEFAULT values
        - PRIMARY KEY constraints
        - UNIQUE constraints
        - FOREIGN KEY constraints (declared but not enforced in Redshift)
        - CHECK constraints (declared but not enforced in Redshift)

        Args:
            schema: Schema name
            table: Table name

        Returns:
            RedshiftResponse with complete DDL statement
        """
        logger.debug(f"🔧 [RedshiftDataSource] get_table_ddl called for {schema}.{table}")

        # Redshift does not have format_type() or pg_get_expr().
        # We reconstruct the type string manually from information_schema.columns.
        columns_query = """
            SELECT
                column_name,
                data_type,
                character_maximum_length,
                numeric_precision,
                numeric_scale,
                datetime_precision,
                CASE WHEN is_nullable = 'YES' THEN false ELSE true END AS not_null,
                column_default,
                ordinal_position
            FROM information_schema.columns
            WHERE table_schema = %s AND table_name = %s
            ORDER BY ordinal_position;
        """

        pk_query = """
            SELECT
                tc.constraint_name,
                kcu.column_name,
                kcu.ordinal_position
            FROM information_schema.table_constraints tc
            JOIN information_schema.key_column_usage kcu
            ON tc.constraint_name = kcu.constraint_name
            AND tc.table_schema = kcu.table_schema
            WHERE tc.constraint_type = 'PRIMARY KEY'
            AND tc.table_schema = %s
            AND tc.table_name = %s
            ORDER BY kcu.ordinal_position;
        """

        # Redshift supports LISTAGG (string_agg equivalent)
        unique_query = """
            SELECT
                tc.constraint_name,
                kcu.column_name,
                kcu.ordinal_position
            FROM information_schema.table_constraints tc
            JOIN information_schema.key_column_usage kcu
            ON tc.constraint_name = kcu.constraint_name
            AND tc.table_schema = kcu.table_schema
            WHERE tc.constraint_type = 'UNIQUE'
            AND tc.table_schema = %s
            AND tc.table_name = %s
            ORDER BY tc.constraint_name, kcu.ordinal_position;
        """

        fk_query = """
            SELECT
                tc.constraint_name,
                kcu.column_name,
                ccu.table_schema AS foreign_table_schema,
                ccu.table_name AS foreign_table_name,
                ccu.column_name AS foreign_column_name
            FROM information_schema.table_constraints AS tc
            JOIN information_schema.key_column_usage AS kcu
              ON tc.constraint_name = kcu.constraint_name
              AND tc.table_schema = kcu.table_schema
            JOIN information_schema.constraint_column_usage AS ccu
              ON ccu.constraint_name = tc.constraint_name
              AND ccu.table_schema = tc.table_schema
            WHERE tc.constraint_type = 'FOREIGN KEY'
              AND tc.table_schema = %s
              AND tc.table_name = %s;
        """

        check_query = """
            SELECT
                cc.constraint_name,
                cc.check_clause
            FROM information_schema.check_constraints cc
            JOIN information_schema.table_constraints tc
              ON cc.constraint_name = tc.constraint_name
              AND cc.constraint_schema = tc.table_schema
            WHERE tc.table_schema = %s
              AND tc.table_name = %s
              AND tc.constraint_type = 'CHECK'
              AND cc.check_clause NOT LIKE '%%IS NOT NULL%%';
        """

        try:
            from collections import defaultdict

            columns = await asyncio.to_thread(
                self._client.execute_query, columns_query, (schema, table)
            )
            pk_rows = await asyncio.to_thread(
                self._client.execute_query, pk_query, (schema, table)
            )
            unique_rows = await asyncio.to_thread(
                self._client.execute_query, unique_query, (schema, table)
            )
            fk_result = await asyncio.to_thread(
                self._client.execute_query, fk_query, (schema, table)
            )
            check_result = await asyncio.to_thread(
                self._client.execute_query, check_query, (schema, table)
            )

            if not columns:
                return RedshiftResponse(
                    success=False,
                    error="Table not found",
                    message=f"Table {schema}.{table} not found",
                )

            # Aggregate PK columns by constraint name (Python-side, avoids LISTAGG on information_schema)
            pk_map = defaultdict(list)
            for row in pk_rows:
                pk_map[row["constraint_name"]].append(row["column_name"])

            # Aggregate UNIQUE columns by constraint name
            unique_map = defaultdict(list)
            for row in unique_rows:
                unique_map[row["constraint_name"]].append(row["column_name"])

            # Build DDL
            ddl_lines = [f"CREATE TABLE {schema}.{table} ("]
            col_defs = []

            for col in columns:
                type_str = self._build_type_string(col)
                col_def = f"  {col['column_name']} {type_str}"
                if col["not_null"]:
                    col_def += " NOT NULL"
                if col["column_default"] is not None:
                    col_def += f" DEFAULT {col['column_default']}"
                col_defs.append(col_def)

            # Add PRIMARY KEY constraint
            for constraint_name, cols in pk_map.items():
                col_defs.append(
                    f"  CONSTRAINT {constraint_name} PRIMARY KEY ({', '.join(cols)})"
                )

            # Add UNIQUE constraints
            for constraint_name, cols in unique_map.items():
                col_defs.append(
                    f"  CONSTRAINT {constraint_name} UNIQUE ({', '.join(cols)})"
                )

            # Add FOREIGN KEY constraints (declared, not enforced in Redshift)
            for fk in fk_result:
                fk_ref = (
                    f"{fk['foreign_table_schema']}.{fk['foreign_table_name']}"
                    f"({fk['foreign_column_name']})"
                )
                col_defs.append(
                    f"  CONSTRAINT {fk['constraint_name']} FOREIGN KEY "
                    f"({fk['column_name']}) REFERENCES {fk_ref}"
                )

            # Add CHECK constraints (declared, not enforced in Redshift)
            for chk in check_result:
                col_defs.append(
                    f"  CONSTRAINT {chk['constraint_name']} CHECK ({chk['check_clause']})"
                )

            ddl_lines.append(",\n".join(col_defs))
            ddl_lines.append(");")

            ddl = "\n".join(ddl_lines)
            logger.debug(
                f"🔧 [RedshiftDataSource] Generated complete DDL for {schema}.{table}"
            )

            return RedshiftResponse(
                success=True,
                data={"ddl": ddl},
                message=f"Successfully retrieved DDL for {schema}.{table}",
            )
        except Exception as e:
            logger.error(f"🔧 [RedshiftDataSource] get_table_ddl failed: {e}")
            return RedshiftResponse(
                success=False,
                error=str(e),
                message="Failed to get table DDL",
            )

    def _build_type_string(self, col: dict[str, Any]) -> str:
        """Reconstruct a SQL type string from information_schema column metadata.

        This is needed because Redshift does not support format_type() which
        PostgreSQL uses to get the full type string from system catalogs.

        Args:
            col: Column metadata dict from information_schema.columns

        Returns:
            SQL type string (e.g., 'VARCHAR(255)', 'NUMERIC(10,2)', 'TIMESTAMP')
        """
        data_type = (col.get("data_type") or "").upper()
        char_len = col.get("character_maximum_length")
        num_precision = col.get("numeric_precision")
        num_scale = col.get("numeric_scale")

        # Character types
        if data_type in ("CHARACTER VARYING", "VARCHAR", "CHARACTER", "CHAR", "NVARCHAR"):
            if char_len:
                return f"{data_type}({char_len})"
            return data_type

        # Numeric/decimal types
        if data_type in ("NUMERIC", "DECIMAL"):
            if num_precision is not None and num_scale is not None:
                return f"{data_type}({num_precision},{num_scale})"
            if num_precision is not None:
                return f"{data_type}({num_precision})"
            return data_type

        # All other types (INTEGER, BIGINT, BOOLEAN, DATE, TIMESTAMP, FLOAT, etc.)
        return data_type

    async def test_connection(self) -> RedshiftResponse:
        """Test the database connection.

        Returns:
            RedshiftResponse with connection test result
        """
        logger.debug("🔧 [RedshiftDataSource] test_connection called")

        # current_database() and current_user are available in Redshift
        # version() is also available
        query = "SELECT version() AS version, current_database() AS database, current_user AS user;"

        try:
            results = await asyncio.to_thread(self._client.execute_query, query)
            logger.info("🔧 [RedshiftDataSource] Connection test successful")

            return RedshiftResponse(
                success=True,
                data=results[0] if results else {},
                message="Connection successful",
            )
        except Exception as e:
            logger.error(f"🔧 [RedshiftDataSource] Connection test failed: {e}")
            return RedshiftResponse(
                success=False,
                error=str(e),
                message="Connection test failed",
            )

    async def execute_query(
        self, query: str, params: Optional[tuple] = None
    ) -> RedshiftResponse:
        """Execute a custom SQL query.

        Args:
            query: SQL query to execute
            params: Optional query parameters

        Returns:
            RedshiftResponse with query results
        """
        logger.debug("🔧 [RedshiftDataSource] execute_query called")

        try:
            results = await asyncio.to_thread(
                self._client.execute_query, query, params
            )
            logger.debug(
                f"🔧 [RedshiftDataSource] Query returned {len(results)} rows"
            )

            return RedshiftResponse(
                success=True,
                data=results,
                message=f"Query executed successfully, {len(results)} rows returned",
            )
        except Exception as e:
            logger.error(f"🔧 [RedshiftDataSource] Query execution failed: {e}")
            return RedshiftResponse(
                success=False,
                error=str(e),
                message="Query execution failed",
            )

    async def get_table_stats(
        self, schemas: Optional[list[str]] = None
    ) -> RedshiftResponse:
        """Get table statistics for change detection.

        Fetches row count estimates and DML counters from SVV_TABLE_INFO,
        which is the Redshift equivalent of pg_stat_user_tables.

        Note: SVV_TABLE_INFO provides estimated row counts (tbl_rows) but does
        NOT provide cumulative per-operation DML counters (n_tup_ins/upd/del)
        the way pg_stat_user_tables does in PostgreSQL. For change detection,
        tbl_rows is the best available signal without enabling STL_INSERT/STL_DELETE
        log queries which are more expensive.

        Args:
            schemas: Optional list of schemas to filter. If None, returns all user schemas.

        Returns:
            RedshiftResponse with table stats including:
            - schema_name: Schema name
            - table_name: Table name
            - tbl_rows: Estimated number of rows
            - size_mb: Table size in MB
        """
        logger.debug("🔧 [RedshiftDataSource] get_table_stats called")

        # SVV_TABLE_INFO is Redshift-specific and provides table-level stats.
        # It is only visible to superusers and table owners by default.
        query = """
            SELECT
                schema AS schema_name,
                "table" AS table_name,
                tbl_rows,
                size AS size_mb
            FROM SVV_TABLE_INFO
            WHERE schema NOT IN ('pg_catalog', 'information_schema', 'pg_toast',
                                  'pg_internal', 'catalog_history')
        """

        params = None
        if schemas:
            placeholders = ", ".join(["%s"] * len(schemas))
            query += f" AND schema IN ({placeholders})"
            params = tuple(schemas)

        query += " ORDER BY schema, \"table\";"

        try:
            results = await asyncio.to_thread(
                self._client.execute_query, query, params
            )
            logger.debug(
                f"🔧 [RedshiftDataSource] Found stats for {len(results)} tables"
            )

            return RedshiftResponse(
                success=True,
                data=results,
                message=f"Successfully retrieved stats for {len(results)} tables",
            )
        except Exception as e:
            logger.error(f"🔧 [RedshiftDataSource] get_table_stats failed: {e}")
            return RedshiftResponse(
                success=False,
                error=str(e),
                message="Failed to get table stats",
            )

    async def _fetch_table_rows(
        self,
        schema_name: str,
        table_name: str,
        limit: Optional[int] = None,
    ) -> list[dict[str, Any]]:
        """Fetch rows from a table with an optional row limit.

        Uses quoted identifiers to safely handle schema/table names that may
        contain reserved words or mixed case (Redshift folds unquoted identifiers
        to lowercase by default).

        Args:
            schema_name: Schema name
            table_name: Table name
            limit: Optional row limit. Defaults to REDSHIFT_TABLE_ROW_LIMIT.

        Returns:
            List of row dicts, or empty list on failure.
        """
        row_limit = limit if limit is not None else REDSHIFT_TABLE_ROW_LIMIT
        query = f'SELECT * FROM "{schema_name}"."{table_name}" LIMIT {row_limit}'

        try:
            response = await self.execute_query(query)
            if response.success and response.data:
                return response.data
        except Exception as e:
            logger.warning(
                f"🔧 [RedshiftDataSource] Failed to fetch rows for "
                f"{schema_name}.{table_name}: {e}"
            )
        return []
