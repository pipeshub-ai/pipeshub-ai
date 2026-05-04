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
    n_live_tup: int = 0
    n_tup_ins: int = 0
    n_tup_upd: int = 0
    n_tup_del: int = 0


# Internal models used only by the DDL builder
class _DDLColumnDef(BaseModel):
    model_config = ConfigDict(extra='allow')
    column_name: str = ""
    data_type: str = ""
    not_null: bool = False
    default_value: Optional[str] = None
    ordinal: int = 0


class _DDLConstraintDef(BaseModel):
    model_config = ConfigDict(extra='allow')
    constraint_name: str = ""
    columns: str = ""

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
            results = await asyncio.to_thread(self._client.execute_query,query)
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
            ORDER BY schema_name;
        """
        
        try:
            results = await asyncio.to_thread(self._client.execute_query,query)
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
            WHERE table_schema = %s
              AND table_type = 'BASE TABLE'
            ORDER BY table_name;
        """
        
        try:
            results = await asyncio.to_thread(self._client.execute_query,query, (schema,))
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
            WHERE table_schema = %s AND table_name = %s;
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
            WHERE c.table_schema = %s AND c.table_name = %s
            ORDER BY c.ordinal_position;
        """
        
        # Get UNIQUE constraints (columns that are part of unique constraints)
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
        
        # Get CHECK constraints
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
            table_info_raw = await asyncio.to_thread(self._client.execute_query,table_query, (schema, table))
            if not table_info_raw:
                return PostgreSQLResponse(
                    success=False,
                    error="Table not found",
                    message=f"Table {schema}.{table} not found"
                )
            
            columns_raw = await asyncio.to_thread(self._client.execute_query,columns_query, (schema, table))
            unique_cols_raw = await asyncio.to_thread(self._client.execute_query,unique_query, (schema, table))
            check_raw = await asyncio.to_thread(self._client.execute_query,check_query, (schema, table))
            
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
            WHERE table_schema = %s
            ORDER BY table_name;
        """
        
        try:
            results = await asyncio.to_thread(self._client.execute_query,query, (schema,))
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
            results = await asyncio.to_thread(self._client.execute_query,query, (schema, table))
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
            results = await asyncio.to_thread(self._client.execute_query,query, (schema, table))
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
            WHERE n.nspname = %s 
              AND c.relname = %s
              AND a.attnum > 0 
              AND NOT a.attisdropped
            ORDER BY a.attnum;
        """
        
        # Get PRIMARY KEY constraint
        pk_query = """
            SELECT 
                tc.constraint_name,
                string_agg(kcu.column_name, ', ' ORDER BY kcu.ordinal_position) as columns
            FROM information_schema.table_constraints tc
            JOIN information_schema.key_column_usage kcu
              ON tc.constraint_name = kcu.constraint_name
              AND tc.table_schema = kcu.table_schema
            WHERE tc.constraint_type = 'PRIMARY KEY'
              AND tc.table_schema = %s
              AND tc.table_name = %s
            GROUP BY tc.constraint_name;
        """
        
        # Get UNIQUE constraints (not already covered by primary key)
        unique_query = """
            SELECT 
                tc.constraint_name,
                string_agg(kcu.column_name, ', ' ORDER BY kcu.ordinal_position) as columns
            FROM information_schema.table_constraints tc
            JOIN information_schema.key_column_usage kcu
              ON tc.constraint_name = kcu.constraint_name
              AND tc.table_schema = kcu.table_schema
            WHERE tc.constraint_type = 'UNIQUE'
              AND tc.table_schema = %s
              AND tc.table_name = %s
            GROUP BY tc.constraint_name;
        """
        
        # Get FOREIGN KEY constraints
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
        
        # Get CHECK constraints
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
            columns_raw = await asyncio.to_thread(self._client.execute_query,columns_query, (schema, table))
            pk_raw = await asyncio.to_thread(self._client.execute_query,pk_query, (schema, table))
            unique_raw = await asyncio.to_thread(self._client.execute_query,unique_query, (schema, table))
            fk_raw = await asyncio.to_thread(self._client.execute_query,fk_query, (schema, table))
            check_raw = await asyncio.to_thread(self._client.execute_query,check_query, (schema, table))
            
            columns = [_DDLColumnDef.model_validate(c) for c in columns_raw]
            if not columns:
                return PostgreSQLResponse(
                    success=False,
                    error="Table not found",
                    message=f"Table {schema}.{table} not found"
                )
            
            pk_constraints = [_DDLConstraintDef.model_validate(pk) for pk in pk_raw]
            unique_constraints = [_DDLConstraintDef.model_validate(uq) for uq in unique_raw]
            fk_entries = [ForeignKeyInfo.model_validate(fk) for fk in fk_raw]
            check_entries = [CheckConstraintInfo.model_validate(chk) for chk in check_raw]
            
            ddl_lines = [f"CREATE TABLE {schema}.{table} ("]
            
            col_defs = []
            for col in columns:
                col_def = f"  {col.column_name} {col.data_type}"
                if col.not_null:
                    col_def += " NOT NULL"
                if col.default_value:
                    col_def += f" DEFAULT {col.default_value}"
                col_defs.append(col_def)
            
            if pk_constraints:
                pk = pk_constraints[0]
                col_defs.append(f"  CONSTRAINT {pk.constraint_name} PRIMARY KEY ({pk.columns})")
            
            for uq in unique_constraints:
                col_defs.append(f"  CONSTRAINT {uq.constraint_name} UNIQUE ({uq.columns})")
            
            for fk in fk_entries:
                fk_ref = f"{fk.foreign_table_schema}.{fk.foreign_table_name}({fk.foreign_column_name})"
                col_defs.append(f"  CONSTRAINT {fk.constraint_name} FOREIGN KEY ({fk.column_name}) REFERENCES {fk_ref}")
            
            for chk in check_entries:
                col_defs.append(f"  CONSTRAINT {chk.constraint_name} CHECK ({chk.check_clause})")
            
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
            results = await asyncio.to_thread(self._client.execute_query,query)
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
    
    async def execute_query(self, query: str, params: Optional[tuple] = None) -> PostgreSQLResponse:
        """Execute a custom SQL query.
        
        Args:
            query: SQL query to execute
            params: Optional query parameters
            
        Returns:
            PostgreSQLResponse with query results
        """
        logger.debug(f"🔧 [PostgreSQLDataSource] execute_query called")
        
        try:
            results = await asyncio.to_thread(self._client.execute_query,query, params)
            logger.debug(f"🔧 [PostgreSQLDataSource] Query returned {len(results)} rows")
            
            return PostgreSQLResponse(
                success=True,
                data=results,
                message=f"Query executed successfully, {len(results)} rows returned"
            )
        except Exception as e:
            logger.error(f"🔧 [PostgreSQLDataSource] Query execution failed: {e}")
            return PostgreSQLResponse(
                success=False,
                error=str(e),
                message="Query execution failed"
            )

    async def fetch_table_rows(
        self,
        schema_name: str,
        table_name: str,
        limit: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """Return up to ``limit`` rows from a table (full row scan capped).

        Args:
            schema_name: Schema name
            table_name: Table name
            limit: Max rows; defaults to ``DEFAULT_TABLE_ROW_FETCH_LIMIT``

        Returns:
            List of row dicts, or empty list on failure.
        """
        row_limit = limit if limit is not None else DEFAULT_TABLE_ROW_FETCH_LIMIT
        query = f'SELECT * FROM "{schema_name}"."{table_name}" LIMIT {row_limit}'
        try:
            response = await self.execute_query(query)
            if response.success and response.data:
                return response.data
        except Exception as e:
            logger.warning(
                "🔧 [PostgreSQLDataSource] fetch_table_rows failed for %s.%s: %s",
                schema_name,
                table_name,
                e,
            )
        return []

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
        
        query = """
            SELECT 
                schemaname as schema_name,
                relname as table_name,
                n_live_tup,
                n_tup_ins,
                n_tup_upd,
                n_tup_del
            FROM pg_stat_user_tables
            WHERE schemaname NOT IN ('pg_catalog', 'information_schema', 'pg_toast')
        """
        
        params = None
        if schemas:
            placeholders = ', '.join(['%s'] * len(schemas))
            query += f" AND schemaname IN ({placeholders})"
            params = tuple(schemas)
        
        query += " ORDER BY schemaname, relname;"
        
        try:
            results = await asyncio.to_thread(self._client.execute_query,query, params)
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
