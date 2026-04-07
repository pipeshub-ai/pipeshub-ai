"""
MariaDB DataSource - Database metadata and query operations

Provides async wrapper methods for MariaDB operations:
- Database operations
- Table and view metadata
- Column information
- Foreign key relationships
- Index information
"""

import logging
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict

from app.sources.client.mariadb.mariadb import MariaDBClient, MariaDBResponse

logger = logging.getLogger(__name__)

DEFAULT_TABLE_ROW_FETCH_LIMIT = 1000


# ---------------------------------------------------------------------------
# Pydantic models for structured data returned by MariaDB queries
# All use extra='allow' so unexpected columns from the DB are preserved.
# ---------------------------------------------------------------------------

class DatabaseInfo(BaseModel):
    model_config = ConfigDict(extra='allow')
    name: str = ""
    charset: Optional[str] = None
    collation: Optional[str] = None


class TableListEntry(BaseModel):
    model_config = ConfigDict(extra='allow')
    name: str = ""
    database: Optional[str] = None
    type: Optional[str] = None


class ColumnInfo(BaseModel):
    model_config = ConfigDict(extra='allow')
    name: str = ""
    data_type: Optional[str] = None
    column_type: Optional[str] = None
    character_maximum_length: Optional[int] = None
    numeric_precision: Optional[int] = None
    numeric_scale: Optional[int] = None
    datetime_precision: Optional[int] = None
    nullable: bool = False
    default: Optional[str] = None
    is_unique: bool = False


class CheckConstraintInfo(BaseModel):
    model_config = ConfigDict(extra='allow')
    constraint_name: str = ""
    check_clause: str = ""


class TableDetail(BaseModel):
    """Result of get_table_info: table metadata + columns + constraints."""
    model_config = ConfigDict(extra='allow')
    name: str = ""
    type: Optional[str] = None
    columns: List[ColumnInfo] = []
    check_constraints: List[CheckConstraintInfo] = []


class ViewInfo(BaseModel):
    model_config = ConfigDict(extra='allow')
    name: str = ""
    definition: Optional[str] = None


class ForeignKeyInfo(BaseModel):
    model_config = ConfigDict(extra='allow')
    constraint_name: str = ""
    column_name: str = ""
    foreign_database: str = ""
    foreign_table_name: str = ""
    foreign_column_name: str = ""


class PrimaryKeyInfo(BaseModel):
    model_config = ConfigDict(extra='allow')
    column_name: str = ""


class DDLResult(BaseModel):
    model_config = ConfigDict(extra='allow')
    ddl: str = ""


class ConnectionTestResult(BaseModel):
    model_config = ConfigDict(extra='allow')
    version: Optional[str] = None
    database: Optional[str] = None
    user: Optional[str] = None


class TableStatsEntry(BaseModel):
    model_config = ConfigDict(extra='allow')
    database_name: str = ""
    table_name: str = ""
    n_live_tup: int = 0
    last_updated: Optional[str] = None
    auto_increment: Optional[int] = None


class MariaDBDataSource:
    """MariaDB DataSource for database operations.

    Provides methods for fetching metadata and executing queries against MariaDB.

    Note: MariaDB has no separate schema concept — databases contain tables directly.
    """

    def __init__(self, client: MariaDBClient) -> None:
        """Initialize with MariaDB client.

        Args:
            client: MariaDBClient instance with configured authentication
        """
        logger.debug("🔧 [MariaDBDataSource] __init__ called")
        self._client = client
        logger.info("🔧 [MariaDBDataSource] Initialized successfully")

    def get_data_source(self) -> "MariaDBDataSource":
        """Return the data source instance."""
        return self

    def get_client(self) -> MariaDBClient:
        """Return the underlying MariaDB client."""
        return self._client

    async def list_databases(self) -> MariaDBResponse:
        """List all accessible databases.

        Returns:
            MariaDBResponse with list of databases
        """
        logger.debug("🔧 [MariaDBDataSource] list_databases called")

        query = """
            SELECT
                SCHEMA_NAME as name,
                DEFAULT_CHARACTER_SET_NAME as charset,
                DEFAULT_COLLATION_NAME as collation
            FROM information_schema.SCHEMATA
            WHERE SCHEMA_NAME NOT IN (
                'information_schema', 'performance_schema', 'mysql', 'sys'
            )
            ORDER BY SCHEMA_NAME;
        """

        try:
            results = self._client.execute_query(query)
            databases = [DatabaseInfo.model_validate(row) for row in results]
            logger.debug(f"🔧 [MariaDBDataSource] Found {len(databases)} databases")

            return MariaDBResponse(
                success=True,
                data=[db.model_dump() for db in databases],
                message=f"Successfully listed {len(databases)} databases"
            )
        except Exception as e:
            logger.error(f"🔧 [MariaDBDataSource] list_databases failed: {e}")
            return MariaDBResponse(
                success=False,
                error=str(e),
                message="Failed to list databases"
            )

    async def list_tables(self, database: Optional[str] = None) -> MariaDBResponse:
        """List all tables in a database.

        Args:
            database: Database name. If None, uses the connected database.

        Returns:
            MariaDBResponse with list of tables
        """
        database = database or self._client.database
        logger.debug(f"🔧 [MariaDBDataSource] list_tables called for database: {database}")

        query = """
            SELECT
                TABLE_NAME as name,
                TABLE_SCHEMA as `database`,
                TABLE_TYPE as type
            FROM information_schema.TABLES
            WHERE TABLE_SCHEMA = ?
              AND TABLE_TYPE = 'BASE TABLE'
            ORDER BY TABLE_NAME;
        """

        try:
            results = self._client.execute_query(query, (database,))
            tables = [TableListEntry.model_validate(row) for row in results]
            logger.debug(f"🔧 [MariaDBDataSource] Found {len(tables)} tables")

            return MariaDBResponse(
                success=True,
                data=[t.model_dump() for t in tables],
                message=f"Successfully listed {len(tables)} tables"
            )
        except Exception as e:
            logger.error(f"🔧 [MariaDBDataSource] list_tables failed: {e}")
            return MariaDBResponse(
                success=False,
                error=str(e),
                message="Failed to list tables"
            )

    async def get_table_info(self, table: str, database: Optional[str] = None) -> MariaDBResponse:
        """Get detailed information about a table.

        Args:
            table: Table name
            database: Database name. If None, uses the connected database.

        Returns:
            MariaDBResponse with table information including columns with
            complete type info (precision, scale, length), constraints, and defaults
        """
        database = database or self._client.database
        logger.debug(
            f"🔧 [MariaDBDataSource] get_table_info called for {database}.{table}"
        )

        table_query = """
            SELECT
                TABLE_NAME as name,
                TABLE_SCHEMA as `database`,
                TABLE_TYPE as type
            FROM information_schema.TABLES
            WHERE TABLE_SCHEMA = ? AND TABLE_NAME = ?;
        """

        columns_query = """
            SELECT
                c.COLUMN_NAME as name,
                c.DATA_TYPE as data_type,
                c.COLUMN_TYPE as column_type,
                c.CHARACTER_MAXIMUM_LENGTH as character_maximum_length,
                c.NUMERIC_PRECISION as numeric_precision,
                c.NUMERIC_SCALE as numeric_scale,
                c.DATETIME_PRECISION as datetime_precision,
                CASE WHEN c.IS_NULLABLE = 'YES' THEN 1 ELSE 0 END as nullable,
                c.COLUMN_DEFAULT as `default`
            FROM information_schema.COLUMNS c
            WHERE c.TABLE_SCHEMA = ? AND c.TABLE_NAME = ?
            ORDER BY c.ORDINAL_POSITION;
        """

        unique_query = """
            SELECT DISTINCT kcu.COLUMN_NAME as column_name
            FROM information_schema.TABLE_CONSTRAINTS tc
            JOIN information_schema.KEY_COLUMN_USAGE kcu
              ON tc.CONSTRAINT_NAME = kcu.CONSTRAINT_NAME
              AND tc.TABLE_SCHEMA = kcu.TABLE_SCHEMA
              AND tc.TABLE_NAME = kcu.TABLE_NAME
            WHERE tc.CONSTRAINT_TYPE = 'UNIQUE'
              AND tc.TABLE_SCHEMA = ?
              AND tc.TABLE_NAME = ?;
        """

        # CHECK constraints available in MariaDB 10.2+
        check_query = """
            SELECT
                cc.CONSTRAINT_NAME as constraint_name,
                cc.CHECK_CLAUSE as check_clause
            FROM information_schema.CHECK_CONSTRAINTS cc
            WHERE cc.CONSTRAINT_SCHEMA = ?
              AND cc.TABLE_NAME = ?;
        """

        try:
            table_info_raw = self._client.execute_query(table_query, (database, table))
            if not table_info_raw:
                return MariaDBResponse(
                    success=False,
                    error="Table not found",
                    message=f"Table {database}.{table} not found"
                )

            columns_raw = self._client.execute_query(columns_query, (database, table))
            unique_cols_raw = self._client.execute_query(unique_query, (database, table))

            try:
                check_raw = self._client.execute_query(check_query, (database, table))
            except Exception:
                check_raw = []

            unique_column_names = {row.get('column_name') for row in unique_cols_raw}

            columns = [ColumnInfo.model_validate(row) for row in columns_raw]
            for col in columns:
                col.is_unique = col.name in unique_column_names

            check_constraints = [CheckConstraintInfo.model_validate(row) for row in check_raw]

            table_detail = TableDetail.model_validate({
                **table_info_raw[0],
                'columns': columns,
                'check_constraints': check_constraints,
            })

            logger.debug(f"🔧 [MariaDBDataSource] Table has {len(columns)} columns")

            return MariaDBResponse(
                success=True,
                data=table_detail.model_dump(),
                message=f"Successfully retrieved table info for {database}.{table}"
            )
        except Exception as e:
            logger.error(f"🔧 [MariaDBDataSource] get_table_info failed: {e}")
            return MariaDBResponse(
                success=False,
                error=str(e),
                message="Failed to get table info"
            )

    async def list_views(self, database: Optional[str] = None) -> MariaDBResponse:
        """List all views in a database.

        Args:
            database: Database name. If None, uses the connected database.

        Returns:
            MariaDBResponse with list of views
        """
        database = database or self._client.database
        logger.debug(f"🔧 [MariaDBDataSource] list_views called for database: {database}")

        query = """
            SELECT
                TABLE_NAME as name,
                TABLE_SCHEMA as `database`,
                VIEW_DEFINITION as definition
            FROM information_schema.VIEWS
            WHERE TABLE_SCHEMA = ?
            ORDER BY TABLE_NAME;
        """

        try:
            results = self._client.execute_query(query, (database,))
            views = [ViewInfo.model_validate(row) for row in results]
            logger.debug(f"🔧 [MariaDBDataSource] Found {len(views)} views")

            return MariaDBResponse(
                success=True,
                data=[v.model_dump() for v in views],
                message=f"Successfully listed {len(views)} views"
            )
        except Exception as e:
            logger.error(f"🔧 [MariaDBDataSource] list_views failed: {e}")
            return MariaDBResponse(
                success=False,
                error=str(e),
                message="Failed to list views"
            )

    async def get_foreign_keys(self, table: str, database: Optional[str] = None) -> MariaDBResponse:
        """Get foreign key relationships for a table.

        Args:
            table: Table name
            database: Database name. If None, uses the connected database.

        Returns:
            MariaDBResponse with foreign key information
        """
        database = database or self._client.database
        logger.debug(
            f"🔧 [MariaDBDataSource] get_foreign_keys called for {database}.{table}"
        )

        query = """
            SELECT
                kcu.CONSTRAINT_NAME as constraint_name,
                kcu.COLUMN_NAME as column_name,
                kcu.REFERENCED_TABLE_SCHEMA as foreign_database,
                kcu.REFERENCED_TABLE_NAME as foreign_table_name,
                kcu.REFERENCED_COLUMN_NAME as foreign_column_name
            FROM information_schema.KEY_COLUMN_USAGE kcu
            JOIN information_schema.TABLE_CONSTRAINTS tc
              ON kcu.CONSTRAINT_NAME = tc.CONSTRAINT_NAME
              AND kcu.TABLE_SCHEMA = tc.TABLE_SCHEMA
              AND kcu.TABLE_NAME = tc.TABLE_NAME
            WHERE tc.CONSTRAINT_TYPE = 'FOREIGN KEY'
              AND kcu.TABLE_SCHEMA = ?
              AND kcu.TABLE_NAME = ?;
        """

        try:
            results = self._client.execute_query(query, (database, table))
            foreign_keys = [ForeignKeyInfo.model_validate(row) for row in results]
            logger.debug(f"🔧 [MariaDBDataSource] Found {len(foreign_keys)} foreign keys")

            return MariaDBResponse(
                success=True,
                data=[fk.model_dump() for fk in foreign_keys],
                message=f"Successfully retrieved foreign keys for {database}.{table}"
            )
        except Exception as e:
            logger.error(f"🔧 [MariaDBDataSource] get_foreign_keys failed: {e}")
            return MariaDBResponse(
                success=False,
                error=str(e),
                message="Failed to get foreign keys"
            )

    async def get_primary_keys(self, table: str, database: Optional[str] = None) -> MariaDBResponse:
        """Get primary key columns for a table.

        Args:
            table: Table name
            database: Database name. If None, uses the connected database.

        Returns:
            MariaDBResponse with primary key column names
        """
        database = database or self._client.database
        logger.debug(
            f"🔧 [MariaDBDataSource] get_primary_keys called for {database}.{table}"
        )

        query = """
            SELECT
                kcu.COLUMN_NAME as column_name
            FROM information_schema.TABLE_CONSTRAINTS tc
            JOIN information_schema.KEY_COLUMN_USAGE kcu
              ON tc.CONSTRAINT_NAME = kcu.CONSTRAINT_NAME
              AND tc.TABLE_SCHEMA = kcu.TABLE_SCHEMA
              AND tc.TABLE_NAME = kcu.TABLE_NAME
            WHERE tc.CONSTRAINT_TYPE = 'PRIMARY KEY'
              AND tc.TABLE_SCHEMA = ?
              AND tc.TABLE_NAME = ?
            ORDER BY kcu.ORDINAL_POSITION;
        """

        try:
            results = self._client.execute_query(query, (database, table))
            primary_keys = [PrimaryKeyInfo.model_validate(row) for row in results]
            logger.debug(
                f"🔧 [MariaDBDataSource] Found {len(primary_keys)} primary key columns"
            )

            return MariaDBResponse(
                success=True,
                data=[pk.model_dump() for pk in primary_keys],
                message=f"Successfully retrieved primary keys for {database}.{table}"
            )
        except Exception as e:
            logger.error(f"🔧 [MariaDBDataSource] get_primary_keys failed: {e}")
            return MariaDBResponse(
                success=False,
                error=str(e),
                message="Failed to get primary keys"
            )

    async def get_table_ddl(self, table: str, database: Optional[str] = None) -> MariaDBResponse:
        """Get the DDL (CREATE TABLE statement) for a table.

        Uses MariaDB's SHOW CREATE TABLE which returns the complete DDL including
        column definitions, constraints, ENGINE, and charset options.

        Args:
            table: Table name
            database: Database name. If None, uses the connected database.

        Returns:
            MariaDBResponse with complete DDL statement
        """
        database = database or self._client.database
        logger.debug(
            f"🔧 [MariaDBDataSource] get_table_ddl called for {database}.{table}"
        )

        safe_database = database.replace('`', '``')
        safe_table = table.replace('`', '``')
        query = f"SHOW CREATE TABLE `{safe_database}`.`{safe_table}`"

        try:
            results = self._client.execute_query(query)
            if not results:
                return MariaDBResponse(
                    success=False,
                    error="Table not found",
                    message=f"Table {database}.{table} not found"
                )

            row = results[0]
            ddl_text = row.get('Create Table') or row.get('Create View', '')
            ddl_result = DDLResult(ddl=ddl_text)

            return MariaDBResponse(
                success=True,
                data=ddl_result.model_dump(),
                message=f"Successfully retrieved DDL for {database}.{table}"
            )
        except Exception as e:
            logger.error(f"🔧 [MariaDBDataSource] get_table_ddl failed: {e}")
            return MariaDBResponse(
                success=False,
                error=str(e),
                message="Failed to get table DDL"
            )

    async def test_connection(self) -> MariaDBResponse:
        """Test the database connection.

        Returns:
            MariaDBResponse with connection test result
        """
        logger.debug("🔧 [MariaDBDataSource] test_connection called")

        query = """
            SELECT
                VERSION() as version,
                DATABASE() as `database`,
                CURRENT_USER() as user;
        """

        try:
            results = self._client.execute_query(query)
            logger.info("🔧 [MariaDBDataSource] Connection test successful")

            conn_info = ConnectionTestResult.model_validate(results[0]) if results else ConnectionTestResult()
            return MariaDBResponse(
                success=True,
                data=conn_info.model_dump(),
                message="Connection successful"
            )
        except Exception as e:
            logger.error(f"🔧 [MariaDBDataSource] Connection test failed: {e}")
            return MariaDBResponse(
                success=False,
                error=str(e),
                message="Connection test failed"
            )

    async def execute_query(
        self, query: str, params: Optional[tuple] = None
    ) -> MariaDBResponse:
        """Execute a custom SQL query.

        Args:
            query: SQL query to execute
            params: Optional query parameters

        Returns:
            MariaDBResponse with query results
        """
        logger.debug("🔧 [MariaDBDataSource] execute_query called")

        try:
            results = self._client.execute_query(query, params)
            logger.debug(f"🔧 [MariaDBDataSource] Query returned {len(results)} rows")

            return MariaDBResponse(
                success=True,
                data=results,
                message=f"Query executed successfully, {len(results)} rows returned"
            )
        except Exception as e:
            logger.error(f"🔧 [MariaDBDataSource] Query execution failed: {e}")
            return MariaDBResponse(
                success=False,
                error=str(e),
                message="Query execution failed"
            )

    async def fetch_table_rows(
        self,
        database_name: str,
        table_name: str,
        limit: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """Return up to ``limit`` rows from a table.

        Args:
            database_name: Database name
            table_name: Table name
            limit: Max rows; defaults to ``DEFAULT_TABLE_ROW_FETCH_LIMIT``

        Returns:
            List of row dicts, or empty list on failure.
        """
        row_limit = limit if limit is not None else DEFAULT_TABLE_ROW_FETCH_LIMIT
        safe_database = database_name.replace('`', '``')
        safe_table = table_name.replace('`', '``')
        query = f"SELECT * FROM `{safe_database}`.`{safe_table}` LIMIT {int(row_limit)}"
        try:
            response = await self.execute_query(query)
            if response.success and response.data:
                return response.data
        except Exception as e:
            logger.warning(
                "🔧 [MariaDBDataSource] fetch_table_rows failed for %s.%s: %s",
                database_name,
                table_name,
                e,
            )
        return []

    async def get_table_stats(
        self, databases: Optional[list[str]] = None
    ) -> MariaDBResponse:
        """Get table statistics for change detection.

        Fetches row count estimates and last update time from information_schema.TABLES.

        Note: UPDATE_TIME may be NULL for InnoDB tables unless innodb_file_per_table=ON.
        This is a MariaDB limitation — there are no cumulative DML counters like
        PostgreSQL's pg_stat_user_tables.

        Args:
            databases: Optional list of database names to filter.
                       If None, returns all user databases.

        Returns:
            MariaDBResponse with table stats including:
            - database_name: Database name
            - table_name: Table name
            - n_live_tup: Estimated number of live rows (TABLE_ROWS)
            - last_updated: Last modification time (may be NULL for InnoDB)
            - auto_increment: Current auto-increment value
        """
        logger.debug("🔧 [MariaDBDataSource] get_table_stats called")

        query = """
            SELECT
                TABLE_SCHEMA as database_name,
                TABLE_NAME as table_name,
                TABLE_ROWS as n_live_tup,
                UPDATE_TIME as last_updated,
                AUTO_INCREMENT as auto_increment
            FROM information_schema.TABLES
            WHERE TABLE_TYPE = 'BASE TABLE'
              AND TABLE_SCHEMA NOT IN (
                  'information_schema', 'performance_schema', 'mysql', 'sys'
              )
        """

        params = None
        if databases:
            placeholders = ', '.join(['?'] * len(databases))
            query += f" AND TABLE_SCHEMA IN ({placeholders})"
            params = tuple(databases)

        query += " ORDER BY TABLE_SCHEMA, TABLE_NAME;"

        try:
            results = self._client.execute_query(query, params)
            stats = [TableStatsEntry.model_validate(row) for row in results]
            logger.debug(
                f"🔧 [MariaDBDataSource] Found stats for {len(stats)} tables"
            )

            return MariaDBResponse(
                success=True,
                data=[s.model_dump() for s in stats],
                message=f"Successfully retrieved stats for {len(stats)} tables"
            )
        except Exception as e:
            logger.error(f"🔧 [MariaDBDataSource] get_table_stats failed: {e}")
            return MariaDBResponse(
                success=False,
                error=str(e),
                message="Failed to get table stats"
            )
