"""
PostgreSQL DataSource - Database metadata and query operations

Provides async wrapper methods for PostgreSQL operations:
- Database and schema operations
- Table and view metadata
- Column information
- Foreign key relationships
- Index information
"""

import asyncio
import logging
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict

from app.sources.client.postgres.postgres import PostgreSQLClient, PostgreSQLResponse

logger = logging.getLogger(__name__)


DEFAULT_TABLE_ROW_FETCH_LIMIT = 1000

# Constraints are read from pg_constraint rather than information_schema: the
# information_schema views hide constraints from a user who can only SELECT the
# table, and they join a constraint to its columns by name, which mixes up
# same-named constraints and cross-multiplies multi-column foreign keys.
_CONSTRAINTS_OF_TABLE = """
    FROM pg_constraint con
    JOIN pg_class c ON c.oid = con.conrelid
    JOIN pg_namespace n ON n.oid = c.relnamespace
"""


# One row per column pair, in key order: unnest() walks conkey and confkey in step.
_FOREIGN_KEYS_QUERY = """
    SELECT
        con.conname AS constraint_name,
        a.attname AS column_name,
        fn.nspname AS foreign_table_schema,
        fc.relname AS foreign_table_name,
        fa.attname AS foreign_column_name
""" + _CONSTRAINTS_OF_TABLE + """
    JOIN pg_class fc ON fc.oid = con.confrelid
    JOIN pg_namespace fn ON fn.oid = fc.relnamespace
    CROSS JOIN LATERAL unnest(con.conkey, con.confkey) WITH ORDINALITY AS k(attnum, fattnum, ord)
    JOIN pg_attribute a ON a.attrelid = con.conrelid AND a.attnum = k.attnum
    JOIN pg_attribute fa ON fa.attrelid = con.confrelid AND fa.attnum = k.fattnum
    WHERE con.contype = 'f'
      AND n.nspname = $1
      AND c.relname = $2
    ORDER BY con.conname, k.ord;
"""


def quote_ident(name: str) -> str:
    """Quote an identifier for SQL, doubling any embedded double quote."""
    return '"' + name.replace('"', '""') + '"'


# Bookkeeping columns fetch_table_rows adds and strips again.
_ROW_NUMBER = "_pipeshub_row_number"
_RUNNING_BYTES = "_pipeshub_running_bytes"
_ROW_ALIAS = "_pipeshub_row"


def _order_list(alias: str, columns: List[str]) -> str:
    return ", ".join(f"{alias}.{quote_ident(col)}" for col in columns)


def _sample_query(
    schema_name: str,
    table_name: str,
    limit: Optional[int],
    order_by: Optional[List[str]],
    *,
    by_row_text: bool = False,
) -> str:
    """The row sample: the first ``limit`` rows, in ``order_by`` order when given.

    ``by_row_text`` orders a table without keys by each row's text form. That
    works for every column type, and rows that tie are identical, so the
    sample is the same on every read of unchanged data.
    """
    row_limit = int(limit if limit is not None else DEFAULT_TABLE_ROW_FETCH_LIMIT)
    query = f"SELECT * FROM {quote_ident(schema_name)}.{quote_ident(table_name)}"
    if order_by:
        query += " ORDER BY " + ", ".join(quote_ident(col) for col in order_by)
    elif by_row_text:
        query += f" AS {_ROW_ALIAS} ORDER BY {_ROW_ALIAS}::text"
    return query + f" LIMIT {row_limit}"


def _byte_capped_query(sample: str, max_bytes: int, row_order: str) -> str:
    """``sample``'s rows, in ``row_order``, until their text form reaches ``max_bytes``.

    ``row_order`` must repeat the sample's own order: row_number() is not
    bound to the order of the subquery it reads.
    """
    over = f"ORDER BY {row_order}" if row_order else ""
    return f"""
        SELECT * FROM (
            SELECT numbered.*,
                   sum(octet_length(numbered::text)) OVER (ORDER BY {_ROW_NUMBER}) AS {_RUNNING_BYTES}
            FROM (SELECT sampled.*, row_number() OVER ({over}) AS {_ROW_NUMBER} FROM ({sample}) sampled) numbered
        ) sized
        WHERE {_RUNNING_BYTES} <= {int(max_bytes)}
    """


# ---------------------------------------------------------------------------
# Pydantic models for structured data returned by PostgreSQL queries
# All use extra='allow' so unexpected columns from the DB are preserved.
# ---------------------------------------------------------------------------

class DatabaseInfo(BaseModel):
    model_config = ConfigDict(extra='allow')
    name: str = ""
    encoding: Optional[str] = None
    collation: Optional[str] = None
    size: Optional[str] = None


class SchemaInfo(BaseModel):
    model_config = ConfigDict(extra='allow')
    name: str = ""
    owner: Optional[str] = None


class TableListEntry(BaseModel):
    model_config = ConfigDict(extra='allow')
    name: str = ""
    type: Optional[str] = None


class ColumnInfo(BaseModel):
    model_config = ConfigDict(extra='allow')
    name: str = ""
    data_type: Optional[str] = None
    udt_name: Optional[str] = None
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
    foreign_table_schema: str = ""
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


class TableStats(BaseModel):
    model_config = ConfigDict(extra='allow')
    schema_name: str = ""
    table_name: str = ""
    # pg_class.relkind: 'r' ordinary table, 'p' partitioned table
    kind: str = "r"
    n_live_tup: int = 0
    n_tup_ins: int = 0
    n_tup_upd: int = 0
    n_tup_del: int = 0


# Internal models used only by the DDL builder
class _DDLColumnDef(BaseModel):
    model_config = ConfigDict(extra='allow')
    column_name: str = ""
    quoted_name: str = ""
    qualified_table: str = ""
    data_type: str = ""
    not_null: bool = False
    default_value: Optional[str] = None
    ordinal: int = 0


class _DDLConstraintDef(BaseModel):
    model_config = ConfigDict(extra='allow')
    constraint_name: str = ""
    quoted_name: str = ""
    definition: str = ""

class PostgreSQLDataSource:
    """PostgreSQL DataSource for database operations.
    
    Provides methods for fetching metadata and executing queries against PostgreSQL.
    """
    
    def __init__(self, client: PostgreSQLClient) -> None:
        """Initialize with PostgreSQL client.
        
        Args:
            client: PostgreSQLClient instance with configured authentication
        """
        logger.debug("🔧 [PostgreSQLDataSource] __init__ called")
        self._client = client
        logger.info("🔧 [PostgreSQLDataSource] Initialized successfully")
    
    def get_data_source(self) -> "PostgreSQLDataSource":
        """Return the data source instance."""
        return self
    
    def get_client(self) -> PostgreSQLClient:
        """Return the underlying PostgreSQL client."""
        return self._client
    
    async def list_databases(self) -> PostgreSQLResponse:
        """List all accessible databases.
        
        Returns:
            PostgreSQLResponse with list of databases
        """
        logger.debug("🔧 [PostgreSQLDataSource] list_databases called")
        
        query = """
            SELECT datname as name,
                   pg_encoding_to_char(encoding) as encoding,
                   datcollate as collation,
                   pg_size_pretty(pg_database_size(datname)) as size
            FROM pg_database
            WHERE datistemplate = false
            ORDER BY datname;
        """
        
        try:
            results = await self._client.execute_query(query)
            databases = [DatabaseInfo.model_validate(row) for row in results]
            logger.debug(f"🔧 [PostgreSQLDataSource] Found {len(databases)} databases")
            
            return PostgreSQLResponse(
                success=True,
                data=[db.model_dump() for db in databases],
                message=f"Successfully listed {len(databases)} databases"
            )
        except Exception as e:
            logger.error(f"🔧 [PostgreSQLDataSource] list_databases failed: {e}")
            return PostgreSQLResponse(
                success=False,
                error=str(e),
                message="Failed to list databases"
            )
    
    async def list_schemas(self, database: Optional[str] = None) -> PostgreSQLResponse:
        """List all schemas in the current database.
        
        Args:
            database: Database name (not used, kept for API compatibility)
            
        Returns:
            PostgreSQLResponse with list of schemas
        """
        logger.debug("🔧 [PostgreSQLDataSource] list_schemas called")
        
        query = """
            SELECT schema_name as name,
                   schema_owner as owner
            FROM information_schema.schemata
            WHERE schema_name NOT IN ('pg_catalog', 'information_schema', 'pg_toast')
              AND schema_name !~ '^pg_(toast_)?temp_'
            ORDER BY schema_name;
        """
        
        try:
            results = await self._client.execute_query(query)
            schemas = [SchemaInfo.model_validate(row) for row in results]
            logger.debug(f"🔧 [PostgreSQLDataSource] Found {len(schemas)} schemas")
            
            return PostgreSQLResponse(
                success=True,
                data=[s.model_dump() for s in schemas],
                message=f"Successfully listed {len(schemas)} schemas"
            )
        except Exception as e:
            logger.error(f"🔧 [PostgreSQLDataSource] list_schemas failed: {e}")
            return PostgreSQLResponse(
                success=False,
                error=str(e),
                message="Failed to list schemas"
            )
    
    async def list_tables(self, schema: str = "public") -> PostgreSQLResponse:
        """List all tables in a schema.
        
        Args:
            schema: Schema name (default: public)
            
        Returns:
            PostgreSQLResponse with list of tables
        """
        logger.debug(f"🔧 [PostgreSQLDataSource] list_tables called for schema: {schema}")
        
        query = """
            SELECT
                table_name as name,
                table_schema as schema,
                table_type as type
            FROM information_schema.tables
            WHERE table_schema = $1
              AND table_type = 'BASE TABLE'
            ORDER BY table_name;
        """
        
        try:
            results = await self._client.execute_query(query, (schema,))
            tables = [TableListEntry.model_validate(row) for row in results]
            logger.debug(f"🔧 [PostgreSQLDataSource] Found {len(tables)} tables")
            
            return PostgreSQLResponse(
                success=True,
                data=[t.model_dump() for t in tables],
                message=f"Successfully listed {len(tables)} tables"
            )
        except Exception as e:
            logger.error(f"🔧 [PostgreSQLDataSource] list_tables failed: {e}")
            return PostgreSQLResponse(
                success=False,
                error=str(e),
                message="Failed to list tables"
            )
    
    async def get_table_info(self, schema: str, table: str) -> PostgreSQLResponse:
        """Get detailed information about a table.
        
        Args:
            schema: Schema name
            table: Table name
            
        Returns:
            PostgreSQLResponse with table information including columns with
            complete type info (precision, scale, length), constraints, and defaults
        """
        logger.debug(f"🔧 [PostgreSQLDataSource] get_table_info called for {schema}.{table}")
        
        # Get table metadata
        table_query = """
            SELECT
                table_name as name,
                table_schema as schema,
                table_type as type
            FROM information_schema.tables
            WHERE table_schema = $1 AND table_name = $2;
        """
        
        # Enhanced column information query with full type details
        # Note: is_identity columns only exist in PostgreSQL 10+, so we omit them for compatibility
        columns_query = """
            SELECT
                c.column_name as name,
                c.data_type,
                c.udt_name,
                c.character_maximum_length,
                c.numeric_precision,
                c.numeric_scale,
                c.datetime_precision,
                CASE WHEN c.is_nullable = 'YES' THEN true ELSE false END as nullable,
                c.column_default as "default"
            FROM information_schema.columns c
            WHERE c.table_schema = $1 AND c.table_name = $2
            ORDER BY c.ordinal_position;
        """
        
        # Columns that are part of a UNIQUE constraint
        unique_query = """
            SELECT DISTINCT a.attname AS column_name
        """ + _CONSTRAINTS_OF_TABLE + """
            JOIN pg_attribute a
              ON a.attrelid = con.conrelid AND a.attnum = ANY (con.conkey)
            WHERE con.contype = 'u'
              AND n.nspname = $1
              AND c.relname = $2;
        """

        # CHECK constraints (NOT NULL is not one; it has its own contype)
        check_query = """
            SELECT
                con.conname AS constraint_name,
                pg_get_expr(con.conbin, con.conrelid) AS check_clause
        """ + _CONSTRAINTS_OF_TABLE + """
            WHERE con.contype = 'c'
              AND n.nspname = $1
              AND c.relname = $2
            ORDER BY con.conname;
        """
        
        try:
            table_info_raw = await self._client.execute_query(table_query, (schema, table))
            if not table_info_raw:
                return PostgreSQLResponse(
                    success=False,
                    error="Table not found",
                    message=f"Table {schema}.{table} not found"
                )

            columns_raw, unique_cols_raw, check_raw = await asyncio.gather(
                self._client.execute_query(columns_query, (schema, table)),
                self._client.execute_query(unique_query, (schema, table)),
                self._client.execute_query(check_query, (schema, table)),
            )

            unique_column_names = {row['column_name'] for row in unique_cols_raw}

            columns = [ColumnInfo.model_validate(row) for row in columns_raw]
            for col in columns:
                col.is_unique = col.name in unique_column_names
            
            check_constraints = [CheckConstraintInfo.model_validate(row) for row in check_raw]
            
            table_detail = TableDetail.model_validate({
                **table_info_raw[0],
                'columns': columns,
                'check_constraints': check_constraints,
            })
            
            logger.debug(f"🔧 [PostgreSQLDataSource] Table has {len(columns)} columns")
            
            return PostgreSQLResponse(
                success=True,
                data=table_detail.model_dump(),
                message=f"Successfully retrieved table info for {schema}.{table}"
            )
        except Exception as e:
            logger.error(f"🔧 [PostgreSQLDataSource] get_table_info failed: {e}")
            return PostgreSQLResponse(
                success=False,
                error=str(e),
                message="Failed to get table info"
            )
    
    async def list_views(self, schema: str = "public") -> PostgreSQLResponse:
        """List all views in a schema.
        
        Args:
            schema: Schema name (default: public)
            
        Returns:
            PostgreSQLResponse with list of views
        """
        logger.debug(f"🔧 [PostgreSQLDataSource] list_views called for schema: {schema}")
        
        query = """
            SELECT
                table_name as name,
                table_schema as schema,
                view_definition as definition
            FROM information_schema.views
            WHERE table_schema = $1
            ORDER BY table_name;
        """
        
        try:
            results = await self._client.execute_query(query, (schema,))
            views = [ViewInfo.model_validate(row) for row in results]
            logger.debug(f"🔧 [PostgreSQLDataSource] Found {len(views)} views")
            
            return PostgreSQLResponse(
                success=True,
                data=[v.model_dump() for v in views],
                message=f"Successfully listed {len(views)} views"
            )
        except Exception as e:
            logger.error(f"🔧 [PostgreSQLDataSource] list_views failed: {e}")
            return PostgreSQLResponse(
                success=False,
                error=str(e),
                message="Failed to list views"
            )
    
    async def get_foreign_keys(self, schema: str, table: str) -> PostgreSQLResponse:
        """Get foreign key relationships for a table.
        
        Args:
            schema: Schema name
            table: Table name
            
        Returns:
            PostgreSQLResponse with foreign key information
        """
        logger.debug(f"🔧 [PostgreSQLDataSource] get_foreign_keys called for {schema}.{table}")
        
        query = _FOREIGN_KEYS_QUERY

        try:
            results = await self._client.execute_query(query, (schema, table))
            foreign_keys = [ForeignKeyInfo.model_validate(row) for row in results]
            logger.debug(f"🔧 [PostgreSQLDataSource] Found {len(foreign_keys)} foreign keys")
            
            return PostgreSQLResponse(
                success=True,
                data=[fk.model_dump() for fk in foreign_keys],
                message=f"Successfully retrieved foreign keys for {schema}.{table}"
            )
        except Exception as e:
            logger.error(f"🔧 [PostgreSQLDataSource] get_foreign_keys failed: {e}")
            return PostgreSQLResponse(
                success=False,
                error=str(e),
                message="Failed to get foreign keys"
            )
    
    async def get_primary_keys(self, schema: str, table: str) -> PostgreSQLResponse:
        """Get primary key columns for a table.
        
        Args:
            schema: Schema name
            table: Table name
            
        Returns:
            PostgreSQLResponse with primary key column names
        """
        logger.debug(f"🔧 [PostgreSQLDataSource] get_primary_keys called for {schema}.{table}")
        
        query = """
            SELECT a.attname AS column_name
        """ + _CONSTRAINTS_OF_TABLE + """
            CROSS JOIN LATERAL unnest(con.conkey) WITH ORDINALITY AS k(attnum, ord)
            JOIN pg_attribute a
              ON a.attrelid = con.conrelid AND a.attnum = k.attnum
            WHERE con.contype = 'p'
              AND n.nspname = $1
              AND c.relname = $2
            ORDER BY k.ord;
        """
        
        try:
            results = await self._client.execute_query(query, (schema, table))
            primary_keys = [PrimaryKeyInfo.model_validate(row) for row in results]
            logger.debug(f"🔧 [PostgreSQLDataSource] Found {len(primary_keys)} primary key columns")
            
            return PostgreSQLResponse(
                success=True,
                data=[pk.model_dump() for pk in primary_keys],
                message=f"Successfully retrieved primary keys for {schema}.{table}"
            )
        except Exception as e:
            logger.error(f"🔧 [PostgreSQLDataSource] get_primary_keys failed: {e}")
            return PostgreSQLResponse(
                success=False,
                error=str(e),
                message="Failed to get primary keys"
            )
    
    async def get_table_ddl(self, schema: str, table: str) -> PostgreSQLResponse:
        """Get the DDL (CREATE TABLE statement) for a table.
        
        Reconstructs the CREATE TABLE statement from system catalogs including:
        - Column definitions with full type info (length, precision, scale)
        - NOT NULL constraints
        - DEFAULT values
        - PRIMARY KEY constraints
        - UNIQUE constraints
        - FOREIGN KEY constraints
        - CHECK constraints
        
        Args:
            schema: Schema name
            table: Table name
            
        Returns:
            PostgreSQLResponse with complete DDL statement
        """
        logger.debug(f"🔧 [PostgreSQLDataSource] get_table_ddl called for {schema}.{table}")
        
        # Get column definitions with full type info
        columns_query = """
            SELECT
                a.attname as column_name,
                quote_ident(a.attname) as quoted_name,
                quote_ident(n.nspname) || '.' || quote_ident(c.relname) as qualified_table,
                format_type(a.atttypid, a.atttypmod) as data_type,
                a.attnotnull as not_null,
                CASE
                    WHEN a.atthasdef THEN pg_get_expr(d.adbin, d.adrelid)
                    ELSE NULL
                END as default_value,
                a.attnum as ordinal
            FROM pg_attribute a
            JOIN pg_class c ON a.attrelid = c.oid
            JOIN pg_namespace n ON c.relnamespace = n.oid
            LEFT JOIN pg_attrdef d ON a.attrelid = d.adrelid AND a.attnum = d.adnum
            WHERE n.nspname = $1
              AND c.relname = $2
              AND a.attnum > 0
              AND NOT a.attisdropped
            ORDER BY a.attnum;
        """
        
        # Every PRIMARY KEY, UNIQUE, FOREIGN KEY and CHECK constraint, as
        # Postgres itself would write it.
        constraints_query = """
            SELECT
                con.conname AS constraint_name,
                quote_ident(con.conname) AS quoted_name,
                pg_get_constraintdef(con.oid) AS definition
        """ + _CONSTRAINTS_OF_TABLE + """
            WHERE con.contype IN ('p', 'u', 'f', 'c')
              AND n.nspname = $1
              AND c.relname = $2
            ORDER BY
                CASE con.contype WHEN 'p' THEN 0 WHEN 'u' THEN 1 WHEN 'f' THEN 2 ELSE 3 END,
                con.conname;
        """

        try:
            columns_raw, constraints_raw = await asyncio.gather(
                self._client.execute_query(columns_query, (schema, table)),
                self._client.execute_query(constraints_query, (schema, table)),
            )

            columns = [_DDLColumnDef.model_validate(c) for c in columns_raw]
            if not columns:
                return PostgreSQLResponse(
                    success=False,
                    error="Table not found",
                    message=f"Table {schema}.{table} not found"
                )

            constraints = [_DDLConstraintDef.model_validate(con) for con in constraints_raw]

            ddl_lines = [f"CREATE TABLE {columns[0].qualified_table} ("]

            col_defs = []
            for col in columns:
                col_def = f"  {col.quoted_name} {col.data_type}"
                if col.not_null:
                    col_def += " NOT NULL"
                if col.default_value:
                    col_def += f" DEFAULT {col.default_value}"
                col_defs.append(col_def)

            for con in constraints:
                col_defs.append(f"  CONSTRAINT {con.quoted_name} {con.definition}")

            ddl_lines.append(",\n".join(col_defs))
            ddl_lines.append(");")
            
            ddl = "\n".join(ddl_lines)
            logger.debug(f"🔧 [PostgreSQLDataSource] Generated complete DDL for {schema}.{table}")
            
            ddl_result = DDLResult(ddl=ddl)
            return PostgreSQLResponse(
                success=True,
                data=ddl_result.model_dump(),
                message=f"Successfully retrieved DDL for {schema}.{table}"
            )
        except Exception as e:
            logger.error(f"🔧 [PostgreSQLDataSource] get_table_ddl failed: {e}")
            return PostgreSQLResponse(
                success=False,
                error=str(e),
                message="Failed to get table DDL"
            )
    
    async def test_connection(self) -> PostgreSQLResponse:
        """Test the database connection.
        
        Returns:
            PostgreSQLResponse with connection test result
        """
        logger.debug("🔧 [PostgreSQLDataSource] test_connection called")
        
        query = "SELECT version() as version, current_database() as database, current_user as user;"
        
        try:
            results = await self._client.execute_query(query)
            logger.info("🔧 [PostgreSQLDataSource] Connection test successful")
            
            conn_info = ConnectionTestResult.model_validate(results[0]) if results else ConnectionTestResult()
            return PostgreSQLResponse(
                success=True,
                data=conn_info.model_dump(),
                message="Connection successful"
            )
        except Exception as e:
            logger.error(f"🔧 [PostgreSQLDataSource] Connection test failed: {e}")
            return PostgreSQLResponse(
                success=False,
                error=str(e),
                message="Connection test failed"
            )

    async def fetch_table_rows(
        self,
        schema_name: str,
        table_name: str,
        limit: Optional[int] = None,
        order_by: Optional[List[str]] = None,
        max_bytes: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """Return up to ``limit`` rows from a table (full row scan capped).

        Args:
            schema_name: Schema name
            table_name: Table name
            limit: Max rows; defaults to ``DEFAULT_TABLE_ROW_FETCH_LIMIT``
            order_by: Columns to order by. Without an order, which rows a capped
                read returns can change between reads of unchanged data.
            max_bytes: Stop once the rows' text form reaches this size. Counted
                by Postgres, so rows past the budget never leave the server.

        Returns:
            List of row dicts.

        Raises:
            The driver's exception. Its only caller streams the rows to the
            user, where swallowing the failure would serve a successful
            download of an empty table.
        """
        sample = _sample_query(schema_name, table_name, limit, order_by)
        if max_bytes is None:
            return await self._client.execute_query(sample)

        row_order = _order_list("sampled", order_by) if order_by else ""
        query = _byte_capped_query(sample, max_bytes, row_order) + f" ORDER BY {_ROW_NUMBER}"
        rows = await self._client.execute_query(query)
        for row in rows:
            row.pop(_ROW_NUMBER, None)
            row.pop(_RUNNING_BYTES, None)
        return rows

    async def get_sample_hash(
        self,
        schema_name: str,
        table_name: str,
        limit: Optional[int] = None,
        order_by: Optional[List[str]] = None,
        max_bytes: Optional[int] = None,
    ) -> PostgreSQLResponse:
        """MD5 of a table's row sample, capped like ``fetch_table_rows``, computed server-side.

        A change signal for where the write counters stand still, such as a hot
        standby: replayed writes do not advance them. Without ``order_by`` the
        rows are ordered by their text, since a scan-order sample (and hash)
        can change while the table does not.
        """
        sample = _sample_query(schema_name, table_name, limit, order_by, by_row_text=not order_by)
        row_order = _order_list("sampled", order_by) if order_by else "sampled::text"
        if max_bytes is None:
            query = f"""
                SELECT md5(coalesce(string_agg(sampled::text, E'\\n' ORDER BY {row_order}), '')) AS sample_hash
                FROM ({sample}) sampled
            """
        else:
            query = f"""
                SELECT md5(coalesce(string_agg(sized::text, E'\\n' ORDER BY {_ROW_NUMBER}), '')) AS sample_hash
                FROM ({_byte_capped_query(sample, max_bytes, row_order)}) sized
            """
        try:
            rows = await self._client.execute_query(query)
            return PostgreSQLResponse(success=True, data={"sample_hash": rows[0]["sample_hash"]})
        except Exception as e:
            logger.error(f"🔧 [PostgreSQLDataSource] get_sample_hash failed: {e}")
            return PostgreSQLResponse(success=False, error=str(e), message="Failed to hash table sample")

    async def is_in_recovery(self) -> PostgreSQLResponse:
        """Whether the server is a standby replaying another server's writes."""
        try:
            rows = await self._client.execute_query("SELECT pg_is_in_recovery() AS in_recovery")
            return PostgreSQLResponse(success=True, data={"in_recovery": bool(rows[0]["in_recovery"])})
        except Exception as e:
            logger.error(f"🔧 [PostgreSQLDataSource] is_in_recovery failed: {e}")
            return PostgreSQLResponse(success=False, error=str(e), message="Failed to read recovery state")

    async def get_columns(self, schemas: Optional[List[str]] = None) -> PostgreSQLResponse:
        """Columns of every table in ``schemas`` (all user schemas if None), in one query.

        Same per-column fields as ``get_table_info``, plus ``schema_name`` and
        ``table_name``. Lets change detection read a whole catalog without a
        round trip per table.
        """
        if schemas is not None and len(schemas) == 0:
            return PostgreSQLResponse(success=True, data=[])

        query = """
            WITH unique_columns AS (
                SELECT DISTINCT n.nspname, c.relname, a.attname
        """ + _CONSTRAINTS_OF_TABLE + """
                JOIN pg_attribute a
                  ON a.attrelid = con.conrelid AND a.attnum = ANY (con.conkey)
                WHERE con.contype = 'u'
            )
            SELECT
                col.table_schema AS schema_name,
                col.table_name,
                col.column_name AS name,
                col.data_type,
                col.udt_name,
                col.character_maximum_length,
                col.numeric_precision,
                col.numeric_scale,
                col.datetime_precision,
                col.is_nullable = 'YES' AS nullable,
                col.column_default AS "default",
                u.attname IS NOT NULL AS is_unique
            FROM information_schema.columns col
            LEFT JOIN unique_columns u
              ON u.nspname = col.table_schema
             AND u.relname = col.table_name
             AND u.attname = col.column_name
            WHERE col.table_schema NOT IN ('pg_catalog', 'information_schema', 'pg_toast')
        """
        if schemas:
            query += " AND col.table_schema = ANY($1::text[])"
        query += " ORDER BY col.table_schema, col.table_name, col.ordinal_position;"

        try:
            rows = await self._client.execute_query(query, (list(schemas),) if schemas else None)
            return PostgreSQLResponse(success=True, data=rows)
        except Exception as e:
            logger.error(f"🔧 [PostgreSQLDataSource] get_columns failed: {e}")
            return PostgreSQLResponse(success=False, error=str(e), message="Failed to get columns")

    async def get_primary_keys_by_table(self, schemas: Optional[List[str]] = None) -> PostgreSQLResponse:
        """Primary-key columns of every table in ``schemas``, in key order, in one query."""
        if schemas is not None and len(schemas) == 0:
            return PostgreSQLResponse(success=True, data=[])

        query = """
            SELECT n.nspname AS schema_name, c.relname AS table_name, a.attname AS column_name
        """ + _CONSTRAINTS_OF_TABLE + """
            CROSS JOIN LATERAL unnest(con.conkey) WITH ORDINALITY AS k(attnum, ord)
            JOIN pg_attribute a
              ON a.attrelid = con.conrelid AND a.attnum = k.attnum
            WHERE con.contype = 'p'
        """
        if schemas:
            query += " AND n.nspname = ANY($1::text[])"
        query += " ORDER BY n.nspname, c.relname, k.ord;"

        try:
            rows = await self._client.execute_query(query, (list(schemas),) if schemas else None)
            return PostgreSQLResponse(success=True, data=rows)
        except Exception as e:
            logger.error(f"🔧 [PostgreSQLDataSource] get_primary_keys_by_table failed: {e}")
            return PostgreSQLResponse(success=False, error=str(e), message="Failed to get primary keys")

    async def get_table_stats(self, schemas: Optional[List[str]] = None) -> PostgreSQLResponse:
        """Get table statistics for change detection.
        
        Fetches row count estimates and cumulative DML counters from pg_stat_user_tables.
        Used for incremental sync to detect which tables have changed.
        
        Args:
            schemas: Optional list of schemas to filter. If None, returns all user schemas.
            
        Returns:
            PostgreSQLResponse with table stats including:
            - schema_name: Schema name
            - table_name: Table name
            - n_live_tup: Estimated number of live rows
            - n_tup_ins: Cumulative number of rows inserted
            - n_tup_upd: Cumulative number of rows updated
            - n_tup_del: Cumulative number of rows deleted
        """
        logger.debug("🔧 [PostgreSQLDataSource] get_table_stats called")

        if schemas is not None and len(schemas) == 0:
            return PostgreSQLResponse(
                success=True,
                data=[],
                message="Successfully retrieved stats for 0 tables"
            )

        # Driven by pg_class so it holds exactly the tables list_tables sees in
        # information_schema.tables: ordinary and partitioned tables the user
        # holds some privilege on. A partitioned table's own counters never
        # move (its rows live in its partitions), so it gets their totals.
        query = """
            WITH RECURSIVE partition_tree AS (
                SELECT c.oid AS root, c.oid AS relid FROM pg_class c WHERE c.relkind = 'p'
                UNION ALL
                SELECT t.root, i.inhrelid
                FROM partition_tree t JOIN pg_inherits i ON i.inhparent = t.relid
            ),
            partition_totals AS (
                SELECT t.root,
                       sum(st.n_live_tup) AS n_live_tup,
                       sum(st.n_tup_ins) AS n_tup_ins,
                       sum(st.n_tup_upd) AS n_tup_upd,
                       sum(st.n_tup_del) AS n_tup_del
                FROM partition_tree t JOIN pg_stat_all_tables st ON st.relid = t.relid
                GROUP BY t.root
            )
            SELECT
                n.nspname AS schema_name,
                c.relname AS table_name,
                c.relkind::text AS kind,
                coalesce(pt.n_live_tup, st.n_live_tup, 0)::bigint AS n_live_tup,
                coalesce(pt.n_tup_ins, st.n_tup_ins, 0)::bigint AS n_tup_ins,
                coalesce(pt.n_tup_upd, st.n_tup_upd, 0)::bigint AS n_tup_upd,
                coalesce(pt.n_tup_del, st.n_tup_del, 0)::bigint AS n_tup_del
            FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            LEFT JOIN pg_stat_all_tables st ON st.relid = c.oid
            LEFT JOIN partition_totals pt ON pt.root = c.oid
            WHERE c.relkind IN ('r', 'p')
              AND n.nspname NOT IN ('pg_catalog', 'information_schema', 'pg_toast')
              AND n.nspname !~ '^pg_(toast_)?temp_'
              AND (
                  pg_has_role(c.relowner, 'USAGE')
                  OR has_table_privilege(c.oid, 'SELECT, INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER')
                  OR has_any_column_privilege(c.oid, 'SELECT, INSERT, UPDATE, REFERENCES')
              )
        """

        if schemas:
            query += " AND n.nspname = ANY($1::text[])"
        query += " ORDER BY n.nspname, c.relname;"

        try:
            params = (list(schemas),) if schemas else None
            results = await self._client.execute_query(query, params)
            stats = [TableStats.model_validate(row) for row in results]
            logger.debug(f"🔧 [PostgreSQLDataSource] Found stats for {len(stats)} tables")
            
            return PostgreSQLResponse(
                success=True,
                data=[s.model_dump() for s in stats],
                message=f"Successfully retrieved stats for {len(stats)} tables"
            )
        except Exception as e:
            logger.error(f"🔧 [PostgreSQLDataSource] get_table_stats failed: {e}")
            return PostgreSQLResponse(
                success=False,
                error=str(e),
                message="Failed to get table stats"
            )

