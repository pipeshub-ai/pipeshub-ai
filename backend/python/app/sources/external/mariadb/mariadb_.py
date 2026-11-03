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
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, field_validator

from app.connectors.core.sql.key_flags import (
    KeyDefinition,
    key_flags,
    primary_key_columns,
)
from app.sources.client.mariadb.mariadb import MariaDBClient, MariaDBResponse
from app.sources.external.mariadb.innodb import decode_innodb_name

logger = logging.getLogger(__name__)

DEFAULT_TABLE_ROW_FETCH_LIMIT = 1000


# ---------------------------------------------------------------------------
# Pydantic models for structured data returned by MariaDB queries
# All use extra='allow' so unexpected columns from the DB are preserved.
# ---------------------------------------------------------------------------

class DatabaseInfo(BaseModel):
    model_config = ConfigDict(extra='allow')
    name: str = ""
    charset: str | None = None
    collation: str | None = None


class TableListEntry(BaseModel):
    model_config = ConfigDict(extra='allow')
    name: str = ""
    database: str | None = None
    type: str | None = None


class ColumnInfo(BaseModel):
    model_config = ConfigDict(extra='allow')
    name: str = ""
    data_type: str | None = None
    column_type: str | None = None
    character_maximum_length: int | None = None
    numeric_precision: int | None = None
    numeric_scale: int | None = None
    datetime_precision: int | None = None
    nullable: bool = False
    default: str | None = None
    is_unique: bool = False
    is_primary_key: bool = False
    is_part_of_composite_primary_key: bool = False
    is_part_of_composite_unique: bool = False


class CheckConstraintInfo(BaseModel):
    model_config = ConfigDict(extra='allow')
    constraint_name: str = ""
    check_clause: str = ""


class TableDetail(BaseModel):
    """Result of get_table_info: table metadata + columns + constraints."""
    model_config = ConfigDict(extra='allow')
    name: str = ""
    type: str | None = None
    columns: list[ColumnInfo] = []
    check_constraints: list[CheckConstraintInfo] = []


class ViewInfo(BaseModel):
    model_config = ConfigDict(extra='allow')
    name: str = ""
    definition: str | None = None


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
    version: str | None = None
    database: str | None = None
    user: str | None = None


class TableStatsEntry(BaseModel):
    model_config = ConfigDict(extra='allow')
    database_name: str = ""
    table_name: str = ""
    n_live_tup: int = 0
    last_updated: str | None = None
    create_time: str | None = None
    auto_increment: int | None = None

    @field_validator("last_updated", "create_time", mode="before")
    @classmethod
    def _update_time_as_text(cls, value: object) -> object:
        # The driver returns UPDATE_TIME as a datetime whenever it is set.
        return value.isoformat() if isinstance(value, datetime) else value


# The columns get_table_info and read_catalog both read; one list keeps the
# ColumnInfo they build, and so each table's structure hash, the same.
_COLUMN_FIELDS = """
                c.COLUMN_NAME as name,
                c.DATA_TYPE as data_type,
                c.COLUMN_TYPE as column_type,
                c.CHARACTER_MAXIMUM_LENGTH as character_maximum_length,
                c.NUMERIC_PRECISION as numeric_precision,
                c.NUMERIC_SCALE as numeric_scale,
                c.DATETIME_PRECISION as datetime_precision,
                CASE WHEN c.IS_NULLABLE = 'YES' THEN 1 ELSE 0 END as nullable,
                c.COLUMN_DEFAULT as `default`"""


class CatalogTable(BaseModel):
    """One table as read_catalog reads it."""
    columns: list[ColumnInfo] = []
    primary_keys: list[str] = []
    foreign_keys: list[ForeignKeyInfo] = []
    # Distinct-value estimates by column, from the first column of any index.
    cardinality: dict[str, int] = {}


def keys_from_statistics(rows: list[dict[str, Any]]) -> list[KeyDefinition]:
    """Unique indexes as keys, each in index order; a prefix index is no key.

    ``rows`` are STATISTICS rows of unique indexes, ordered by index and sequence.
    """
    columns_by_index: dict[str, list[str]] = {}
    prefixed: set[str] = set()
    for row in rows:
        name, column = row.get("index_name"), row.get("column_name")
        if not (name and column):
            continue
        columns_by_index.setdefault(name, []).append(column)
        # UNIQUE(name(10)) makes the first 10 characters unique, not the values.
        if row.get("sub_part") is not None:
            prefixed.add(name)
    return [
        KeyDefinition("primary" if name == "PRIMARY" else "unique", tuple(columns))
        for name, columns in columns_by_index.items()
        if name not in prefixed
    ]


def flagged_columns(rows: list[dict[str, Any]], keys: list[KeyDefinition]) -> list[ColumnInfo]:
    columns = [ColumnInfo.model_validate(row) for row in rows]
    flags = key_flags([c.name for c in columns], keys)
    for col in columns:
        col_flags = flags.get(col.name)
        if col_flags is not None:
            col.is_primary_key = col_flags.is_primary_key
            col.is_unique = col_flags.is_unique
            col.is_part_of_composite_primary_key = col_flags.is_part_of_composite_primary_key
            col.is_part_of_composite_unique = col_flags.is_part_of_composite_unique
    return columns


def leading_cardinality(index_rows: list[dict[str, Any]]) -> dict[str, int]:
    """Each indexed column's distinct estimate, from an index it leads, unique or not.

    A prefix index counts prefixes, and NULL is unknown: both are left out.
    The largest estimate wins when several indexes lead with the column.
    """
    estimates: dict[str, int] = {}
    for row in index_rows:
        column, value = row.get("column_name"), row.get("cardinality")
        if row.get("seq_in_index") != 1 or row.get("sub_part") is not None or value is None or not column:
            continue
        estimates[column] = max(estimates.get(column, 0), int(value))
    return estimates


# Samples and value counts: bounded by the server, on a connection of their own.
SAMPLE_TIMEOUT_S = 60
_RUNNING_BYTES = "_pipeshub_running_bytes"
_ROW_NUMBER = "_pipeshub_row_number"


def quote_identifier(name: str) -> str:
    return "`" + name.replace("`", "``") + "`"


def _cap_bytes(rows: list[dict[str, Any]], max_bytes: int | None) -> list[dict[str, Any]]:
    """The first rows whose values' text fits ``max_bytes``; the first row always."""
    if not max_bytes:
        return rows
    kept: list[dict[str, Any]] = []
    total = 0
    for row in rows:
        total += sum(len(v) if isinstance(v, (bytes, bytearray)) else len(str(v).encode()) for v in row.values()
                     if v is not None)
        if kept and total > max_bytes:
            break
        kept.append(row)
    return kept


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
            results = await self._client.execute_query(query)
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

    async def list_tables(self, database: str | None = None) -> MariaDBResponse:
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
            results = await self._client.execute_query(query, (database,))
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

    async def get_table_info(self, table: str, database: str | None = None) -> MariaDBResponse:
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

        columns_query = f"""
            SELECT {_COLUMN_FIELDS}
            FROM information_schema.COLUMNS c
            WHERE c.TABLE_SCHEMA = ? AND c.TABLE_NAME = ?
            ORDER BY c.ORDINAL_POSITION;
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
            table_info_raw = await self._client.execute_query(
                table_query, (database, table)
            )
            if not table_info_raw:
                return MariaDBResponse(
                    success=False,
                    error="Table not found",
                    message=f"Table {database}.{table} not found"
                )

            columns_raw = await self._client.execute_query(
                columns_query, (database, table)
            )
            keys = await self.key_definitions(table, database)

            try:
                check_raw = await self._client.execute_query(
                    check_query, (database, table)
                )
            except Exception:
                check_raw = []

            columns = flagged_columns(columns_raw, keys)

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

    async def list_views(self, database: str | None = None) -> MariaDBResponse:
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
            results = await self._client.execute_query(query, (database,))
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

    async def get_foreign_keys(self, table: str, database: str | None = None) -> MariaDBResponse:
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
            WHERE kcu.REFERENCED_TABLE_NAME IS NOT NULL
              AND kcu.TABLE_SCHEMA = ?
              AND kcu.TABLE_NAME = ?
            ORDER BY kcu.CONSTRAINT_NAME, kcu.ORDINAL_POSITION;
        """

        try:
            results = await self._client.execute_query(query, (database, table))
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

    async def key_definitions(self, table: str, database: str | None = None) -> list[KeyDefinition]:
        """The table's primary and unique keys, each in index order.

        Read from ``STATISTICS``, one row per column of each unique index
        (``NON_UNIQUE = 0``); the primary key is always the index named
        ``PRIMARY``. Raises on a failed read, so a caller never takes a table
        whose keys could not be read for one without keys.
        """
        database = database or self._client.database
        query = """
            SELECT INDEX_NAME as index_name, COLUMN_NAME as column_name, SUB_PART as sub_part
            FROM information_schema.STATISTICS
            WHERE TABLE_SCHEMA = ? AND TABLE_NAME = ? AND NON_UNIQUE = 0
            ORDER BY INDEX_NAME, SEQ_IN_INDEX;
        """
        rows = await self._client.execute_query(query, (database, table))
        return keys_from_statistics(rows)

    async def read_catalog(self, database: str | None = None) -> dict[str, "CatalogTable"]:
        """Every table's columns (with key flags), keys and foreign keys, in three queries.

        Keyed by table name. Columns are built as ``get_table_info`` builds
        them, so a table's structure hash is the same either way. Index
        cardinality, an estimate the server refreshes on its own, is kept
        apart from the columns. Raises on a failed read: a caller must not
        take an unread catalog for empty tables.
        """
        database = database or self._client.database
        columns_rows = await self._client.execute_query(
            f"""
            SELECT c.TABLE_NAME as table_name, {_COLUMN_FIELDS}
            FROM information_schema.COLUMNS c
            WHERE c.TABLE_SCHEMA = ?
            ORDER BY c.TABLE_NAME, c.ORDINAL_POSITION;
            """,
            (database,),
        )
        index_rows = await self._client.execute_query(
            """
            SELECT TABLE_NAME as table_name, INDEX_NAME as index_name, COLUMN_NAME as column_name,
                   NON_UNIQUE as non_unique, SEQ_IN_INDEX as seq_in_index, SUB_PART as sub_part,
                   CARDINALITY as cardinality
            FROM information_schema.STATISTICS
            WHERE TABLE_SCHEMA = ?
            ORDER BY TABLE_NAME, INDEX_NAME, SEQ_IN_INDEX;
            """,
            (database,),
        )
        # Foreign keys from KEY_COLUMN_USAGE alone: TABLE_CONSTRAINTS shows a
        # SELECT-only user nothing, and a join on it dropped every key.
        fk_rows = await self._client.execute_query(
            """
            SELECT kcu.TABLE_NAME as table_name, kcu.CONSTRAINT_NAME as constraint_name,
                   kcu.COLUMN_NAME as column_name, kcu.REFERENCED_TABLE_SCHEMA as foreign_database,
                   kcu.REFERENCED_TABLE_NAME as foreign_table_name, kcu.REFERENCED_COLUMN_NAME as foreign_column_name
            FROM information_schema.KEY_COLUMN_USAGE kcu
            WHERE kcu.REFERENCED_TABLE_NAME IS NOT NULL AND kcu.TABLE_SCHEMA = ?
            ORDER BY kcu.TABLE_NAME, kcu.CONSTRAINT_NAME, kcu.ORDINAL_POSITION;
            """,
            (database,),
        )

        columns_by_table: dict[str, list[dict[str, Any]]] = {}
        for row in columns_rows:
            columns_by_table.setdefault(row.pop("table_name"), []).append(row)
        indexes_by_table: dict[str, list[dict[str, Any]]] = {}
        for row in index_rows:
            indexes_by_table.setdefault(row.get("table_name"), []).append(row)
        fks_by_table: dict[str, list[ForeignKeyInfo]] = {}
        for row in fk_rows:
            fks_by_table.setdefault(row.pop("table_name"), []).append(ForeignKeyInfo.model_validate(row))

        catalog: dict[str, CatalogTable] = {}
        for table in set(columns_by_table) | set(indexes_by_table) | set(fks_by_table):
            index_rows_of = indexes_by_table.get(table, [])
            keys = keys_from_statistics([r for r in index_rows_of if not r.get("non_unique")])
            catalog[table] = CatalogTable(
                columns=flagged_columns(columns_by_table.get(table, []), keys),
                primary_keys=primary_key_columns(keys),
                foreign_keys=fks_by_table.get(table, []),
                cardinality=leading_cardinality(index_rows_of),
            )
        return catalog

    async def read_table_ids(self, database: str | None = None) -> dict[str, int]:
        """InnoDB's id of each table in the database, by table name.

        The id stays with a table through a rename and is new after a rebuild
        or TRUNCATE. Tables of other engines, partitioned tables and names the
        decoder does not know have none, and so do two names that decode the
        same. Needs the PROCESS privilege; raises without it (1227) or on any
        other failure.
        """
        database = database or self._client.database
        # The server builds the whole table before any filter, so it is read
        # whole and filtered by the decoded name.
        rows = await self._client.execute_query(
            "SELECT NAME as name, TABLE_ID as table_id FROM information_schema.INNODB_SYS_TABLES"
        )
        ids: dict[str, int] = {}
        seen: set[str] = set()
        for row in rows:
            names = decode_innodb_name(str(row.get("name") or ""))
            if not names or names[0] != database or row.get("table_id") is None:
                continue
            if names[1] in seen:
                ids.pop(names[1], None)
                continue
            seen.add(names[1])
            ids[names[1]] = int(row["table_id"])
        return ids

    async def get_primary_keys(self, table: str, database: str | None = None) -> MariaDBResponse:
        """Get primary key columns for a table, in key order.

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

        try:
            keys = await self.key_definitions(table, database)
            primary_keys = [PrimaryKeyInfo(column_name=name) for name in primary_key_columns(keys)]
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

    async def get_table_ddl(self, table: str, database: str | None = None) -> MariaDBResponse:
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
            results = await self._client.execute_query(query)
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
            results = await self._client.execute_query(query)
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
        self,
        query: str,
        params: tuple | None = None,
        *,
        read_only: bool = False,
        timeout_s: int | None = None,
        max_rows: int | None = None,
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
            results = await self._client.execute_query(
                query, params, read_only=read_only, timeout_s=timeout_s, max_rows=max_rows
            )
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
        limit: int | None = None,
        *,
        order_by: list[str] | None = None,
        byte_columns: list[str] | None = None,
        max_bytes: int | None = None,
        timeout_s: float = SAMPLE_TIMEOUT_S,
    ) -> list[dict[str, Any]]:
        """Up to ``limit`` rows, in ``order_by`` order, stopping at ``max_bytes``.

        Read on a bounded, read-only connection of its own (the async pool
        returns a statement stopped by ``max_statement_time`` as no rows).
        With an order, the byte cap is applied by the server: a running sum of
        ``byte_columns``' lengths over the first ``limit`` rows, so rows past
        it never leave the server; the first row is always kept. Without an
        order (the sample may then differ between reads) it is applied here.

        Raises:
            The driver's exception. Its only caller streams the rows to the
            user, where swallowing the failure would serve a successful
            download of an empty table.
        """
        row_limit = int(limit if limit is not None else DEFAULT_TABLE_ROW_FETCH_LIMIT)
        table = f"{quote_identifier(database_name)}.{quote_identifier(table_name)}"
        if not order_by:
            columns, rows, _ = await self._client.execute_read_only_query(
                f"SELECT * FROM {table} LIMIT {row_limit}", timeout_s, max_rows=row_limit,
            )
            return _cap_bytes([dict(zip(columns, row)) for row in rows], max_bytes)

        order = ", ".join(quote_identifier(c) for c in order_by)
        if not max_bytes or not byte_columns:
            query = f"SELECT * FROM {table} ORDER BY {order} LIMIT {row_limit}"
            columns, rows, _ = await self._client.execute_read_only_query(query, timeout_s, max_rows=row_limit)
            return [dict(zip(columns, row)) for row in rows]

        # OCTET_LENGTH per column: CONCAT of a row past max_allowed_packet is NULL.
        row_bytes = " + ".join(f"COALESCE(OCTET_LENGTH({quote_identifier(c)}), 0)" for c in byte_columns)
        query = (
            f"SELECT * FROM ("
            f"SELECT s.*, SUM({row_bytes}) OVER (ORDER BY {order} ROWS UNBOUNDED PRECEDING) AS {_RUNNING_BYTES}, "
            f"ROW_NUMBER() OVER (ORDER BY {order}) AS {_ROW_NUMBER} "
            f"FROM (SELECT * FROM {table} ORDER BY {order} LIMIT {row_limit}) s"
            f") w WHERE {_RUNNING_BYTES} <= {int(max_bytes)} OR {_ROW_NUMBER} = 1 ORDER BY {_ROW_NUMBER}"
        )
        columns, rows, _ = await self._client.execute_read_only_query(query, timeout_s, max_rows=row_limit)
        records = [dict(zip(columns, row)) for row in rows]
        for record in records:
            record.pop(_RUNNING_BYTES, None)
            record.pop(_ROW_NUMBER, None)
        return records

    async def column_value_counts(
        self,
        database_name: str,
        table_name: str,
        column_name: str,
        limit: int,
        *,
        timeout_s: float = SAMPLE_TIMEOUT_S,
    ) -> list[dict[str, Any]]:
        """A column's ``limit`` most common values as ``{"value", "count"}``; NULL counts as a value.

        Bounded and read-only, like the samples. Raises on failure: an empty
        list would read as a column with no values.
        """
        column = quote_identifier(column_name)
        table = f"{quote_identifier(database_name)}.{quote_identifier(table_name)}"
        query = (
            f"SELECT {column} AS value, COUNT(*) AS count FROM {table} "
            f"GROUP BY {column} ORDER BY 2 DESC, ({column} IS NULL), 1 LIMIT {int(limit)}"
        )
        columns, rows, _ = await self._client.execute_read_only_query(query, timeout_s, max_rows=int(limit))
        return [dict(zip(columns, row)) for row in rows]

    async def get_table_stats(
        self, databases: list[str] | None = None
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
                CREATE_TIME as create_time,
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
            results = await self._client.execute_query(query, params)
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
