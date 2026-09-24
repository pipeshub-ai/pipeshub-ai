"""
PostgreSQL Connector

Syncs schemas, tables and their rows from PostgreSQL.
"""
import asyncio
import hashlib
import json
import os
import uuid
from collections import defaultdict
from dataclasses import dataclass
from logging import Logger
from typing import Any, AsyncGenerator, Dict, List, Optional, Set, Tuple

from aiolimiter import AsyncLimiter
from pydantic import BaseModel

from app.config.configuration_service import ConfigurationService
from app.config.constants.arangodb import (
    CollectionNames,
    Connectors,
    MimeTypes,
    OriginTypes,
    PermissionModel,
    RecordRelations,
)
from app.connectors.core.constants import IconPaths
from app.connectors.core.base.connector.connector_service import BaseConnector
from app.connectors.core.base.error.sql_stream_errors import (
    to_sql_response_error,
    to_sql_stream_error,
)
from app.connectors.core.base.error.stream_errors import connector_not_ready
from app.connectors.core.base.data_processor.data_source_entities_processor import (
    DataSourceEntitiesProcessor,
)
from app.connectors.core.base.data_store.data_store import DataStoreProvider
from app.connectors.core.registry.connector_builder import (
    AuthField,
    CommonFields,
    ConnectorBuilder,
    ConnectorScope,
    DocumentationLink,
    SyncStrategy,
)
from app.connectors.core.registry.auth_builder import AuthBuilder, AuthType
from app.connectors.core.registry.filters import (
    FilterCategory,
    FilterCollection,
    FilterField,
    FilterOption,
    FilterOptionsResponse,
    FilterType,
    IndexingFilterKey,
    MultiselectOperator,
    NumberOperator,
    OptionSourceType,
    load_connector_filters,
)
from app.connectors.core.base.sync_point.sync_point import (
    SyncDataPointType,
    SyncPoint,
)
from app.connectors.sources.postgres.apps import PostgreSQLApp
from app.models.entities import (
    AppUser,
    ProgressStatus,
    Record,
    RecordGroup,
    RecordGroupType,
    RecordType,
    RelatedExternalRecord,
    SQLTableRecord,
    User,
)
from app.models.permission import EntityType, Permission, PermissionType
from app.sources.client.postgres.postgres import PostgreSQLConfig
from app.sources.external.postgres.postgres_ import (
    PostgreSQLDataSource,
    ColumnInfo,
    CheckConstraintInfo,
    DDLResult,
    ForeignKeyInfo,
    PrimaryKeyInfo,
    SchemaInfo,
    TableDetail,
    TableListEntry,
    TableStats,
)
from app.utils.streaming import create_stream_record_response
from app.utils.time_conversion import get_epoch_timestamp_in_ms
from fastapi import HTTPException
from fastapi.responses import StreamingResponse

MAX_ROWS_PER_TABLE_LIMIT = 10000
# Cap on the rows' text in one table's document; Postgres stops sending rows
# past it, so a table of wide rows can't exhaust the connector's memory.
MAX_TABLE_DOCUMENT_BYTES = 8 * 1024 * 1024

SYNC_STATE_KEY = "postgres_tables_state"
# Bump when a saved state can no longer be trusted to describe what is indexed;
# a state saved under another version sends the next run to a full sync.
# 2: states saved before it could list tables that were never synced.
SYNC_STATE_VERSION = 2


def table_fqn(schema_name: str, table_name: str) -> str:
    """A table's id, ``schema.table``.

    A part containing a dot, or starting with a double quote, is written
    quoted, so schema ``a.b`` with table ``c`` and schema ``a`` with table
    ``b.c`` get different ids. Every other name is written as is.
    """
    return f"{_fqn_part(schema_name)}.{_fqn_part(table_name)}"


def _fqn_part(name: str) -> str:
    if "." in name or name.startswith('"'):
        return '"' + name.replace('"', '""') + '"'
    return name


def split_table_fqn(fqn: str) -> Tuple[str, str]:
    """Inverse of ``table_fqn``. Raises ValueError for anything it didn't write."""
    schema_name, rest = _read_fqn_part(fqn)
    if not rest.startswith("."):
        raise ValueError(f"Not a table id: {fqn!r}")
    table_name, rest = _read_fqn_part(rest[1:])
    if rest:
        raise ValueError(f"Not a table id: {fqn!r}")
    return schema_name, table_name


def _read_fqn_part(text: str) -> Tuple[str, str]:
    """Read one part off the front of ``text``; returns it and what follows."""
    if not text.startswith('"'):
        dot = text.find(".")
        return (text, "") if dot == -1 else (text[:dot], text[dot:])
    chars: List[str] = []
    i = 1
    while i < len(text):
        if text[i] == '"':
            if text[i + 1:i + 2] == '"':
                chars.append('"')
                i += 2
                continue
            return "".join(chars), text[i + 1:]
        chars.append(text[i])
        i += 1
    raise ValueError(f"Unterminated quoted name in {text!r}")


@dataclass
class PostgresSchema:
    name: str
    owner: Optional[str] = None


class PostgresTableState(BaseModel):
    column_hash: str = ""
    n_tup_ins: int = 0
    n_tup_upd: int = 0
    n_tup_del: int = 0
    # An estimate that ANALYZE moves without any write, so revision() ignores it.
    n_live_tup: int = 0
    schema_name: str = ""
    table_name: str = ""
    # Hash of the sampled rows, set only on a standby, where replayed writes
    # leave the counters above unchanged.
    sample_hash: str = ""

    def revision(self) -> str:
        key = f"{self.column_hash}:{self.n_tup_ins}:{self.n_tup_upd}:{self.n_tup_del}"
        if self.sample_hash:
            key += f":{self.sample_hash}"
        return hashlib.md5(key.encode()).hexdigest()


@dataclass
class PostgresTable:
    name: str
    schema_name: str
    row_count: Optional[int] = None
    owner: Optional[str] = None
    columns: List[ColumnInfo] = None
    foreign_keys: List[ForeignKeyInfo] = None
    primary_keys: List[str] = None
    state: Optional[PostgresTableState] = None

    def __post_init__(self):
        if self.columns is None:
            self.columns = []
        if self.foreign_keys is None:
            self.foreign_keys = []
        if self.primary_keys is None:
            self.primary_keys = []
    
    @property
    def fqn(self) -> str:
        return table_fqn(self.schema_name, self.name)


def _column_hash(columns: List[ColumnInfo]) -> str:
    """MD5 of a table's column definitions, for spotting schema changes."""
    column_str = json.dumps([col.model_dump() for col in columns], sort_keys=True, default=str)
    return hashlib.md5(column_str.encode()).hexdigest()


def _json_default(value: Any) -> Any:
    """Serialize the row values asyncpg returns that json cannot."""
    if isinstance(value, (bytes, bytearray, memoryview)):
        # bytea in Postgres's own hex text form, not a Python bytes repr.
        return "\\x" + bytes(value).hex()
    if all(hasattr(value, attr) for attr in ("lower", "upper", "lower_inc", "upper_inc", "isempty")):
        return {
            "lower": value.lower,
            "upper": value.upper,
            "lower_inc": value.lower_inc,
            "upper_inc": value.upper_inc,
            "empty": value.isempty,
        }
    if callable(getattr(value, "items", None)):
        # Composite-type values arrive as asyncpg Records.
        return dict(value.items())
    return str(value)


def _decode_json_columns(rows: List[Dict[str, Any]], columns: List[ColumnInfo]) -> List[Dict[str, Any]]:
    """Parse json/jsonb values, which asyncpg returns as text.

    Left as text they would be encoded a second time, as a quoted string.
    """
    json_columns = {c.name for c in columns if c.data_type in ("json", "jsonb")}
    if not json_columns:
        return rows

    decoded = []
    for row in rows:
        row = dict(row)
        for name in json_columns & row.keys():
            if isinstance(row[name], str):
                try:
                    row[name] = json.loads(row[name])
                except ValueError:
                    pass
        decoded.append(row)
    return decoded


@dataclass
class SyncStats:
    schemas_synced: int = 0
    tables_new: int = 0
    errors: int = 0
    
    def to_dict(self) -> Dict[str, int]:
        return {
            'schemas_synced': self.schemas_synced,
            'tables_new': self.tables_new,
            'errors': self.errors,
        }
    
    def log_summary(self, logger) -> None:
        logger.info(
            f"📊 Sync Stats: "
            f"Schemas={self.schemas_synced}, Tables(new={self.tables_new}) | "
            f"Errors={self.errors}"
        )


@ConnectorBuilder("PostgreSQL")\
    .in_group("PostgreSQL")\
    .with_description("Sync schemas and tables from PostgreSQL")\
    .with_categories(["Database"])\
    .with_scopes([ConnectorScope.TEAM.value])\
    .with_permission_model(PermissionModel.APP_LEVEL)\
    .with_auth([
        # Option 1: Individual connection fields
        AuthBuilder.type(AuthType.BASIC_AUTH).fields([
            AuthField(
                name="host",
                display_name="Host",
                placeholder="localhost",
                description="PostgreSQL server host",
                field_type="TEXT",
                max_length=500,
                is_secret=False,
                required=True
            ),
            AuthField(
                name="port",
                display_name="Port",
                placeholder="5432",
                description="PostgreSQL server port",
                field_type="TEXT",
                max_length=10,
                is_secret=False,
                required=True
            ),
            AuthField(
                name="database",
                display_name="Database",
                placeholder="mydb",
                description="Database name to connect to",
                field_type="TEXT",
                max_length=200,
                is_secret=False,
                required=True
            ),
            AuthField(
                name="username",
                display_name="Username",
                placeholder="postgres",
                description="Database username",
                field_type="TEXT",
                max_length=200,
                is_secret=False,
                required=True
            ),
            AuthField(
                name="password",
                display_name="Password",
                placeholder="Enter password",
                description="Database password",
                field_type="PASSWORD",
                max_length=500,
                is_secret=True,
                required=True
            ),
        ]),
        # Option 2: Connection string
        AuthBuilder.type(AuthType.CONNECTION_STRING).fields([
            AuthField(
                name="connectionString",
                display_name="Connection String",
                placeholder="postgresql://user:password@localhost:5432/mydb",
                description="PostgreSQL connection string (postgresql://user:password@host:port/database)",
                field_type="TEXT",
                max_length=1000,
                is_secret=True,
                required=True
            ),
        ])
    ])\
    .configure(lambda builder: builder
        .with_icon(IconPaths.connector_icon(Connectors.POSTGRESQL.value))
        .add_documentation_link(DocumentationLink(
            "PostgreSQL Setup",
            "https://www.postgresql.org/docs/",
            "setup"
        ))
        .add_filter_field(FilterField(
            name="schemas",
            display_name="Schemas",
            filter_type=FilterType.MULTISELECT,
            category=FilterCategory.SYNC,
            description="Select specific schemas to sync",
            option_source_type=OptionSourceType.DYNAMIC,
            default_operator=MultiselectOperator.IN.value
        ))
        .add_filter_field(FilterField(
            name="tables",
            display_name="Tables",
            filter_type=FilterType.MULTISELECT,
            category=FilterCategory.SYNC,
            description="Select specific tables to sync",
            option_source_type=OptionSourceType.DYNAMIC,
            default_operator=MultiselectOperator.IN.value
        ))
        .add_filter_field(FilterField(
            name=IndexingFilterKey.TABLES.value,
            display_name="Index Tables",
            filter_type=FilterType.BOOLEAN,
            category=FilterCategory.INDEXING,
            description="Enable indexing of tables",
            default_value=True
        ))
        .add_filter_field(FilterField(
            name=IndexingFilterKey.MAX_ROWS_PER_TABLE.value,
            display_name="Max Rows Per Table",
            filter_type=FilterType.NUMBER,
            category=FilterCategory.SYNC,
            description="Maximum number of rows to index per table (max 10000)",
            default_value=1000,
            default_operator=NumberOperator.LESS_THAN_OR_EQUAL.value
        ))
        .add_filter_field(CommonFields.enable_manual_sync_filter())
        .with_sync_strategies([SyncStrategy.SCHEDULED, SyncStrategy.MANUAL])
        .with_scheduled_config(True, 120)
        .with_sync_support(True)
        .with_agent_support(False)
    )\
    .build_decorator()
class PostgreSQLConnector(BaseConnector):

    def __init__(
        self,
        logger: Logger,
        data_entities_processor: DataSourceEntitiesProcessor,
        data_store_provider: DataStoreProvider,
        config_service: ConfigurationService,
        connector_id: str,
        scope: str = ConnectorScope.TEAM.value,
        created_by: Optional[str] = None,
    ) -> None:
        super().__init__(
            PostgreSQLApp(connector_id),
            logger,
            data_entities_processor,
            data_store_provider,
            config_service,
            connector_id,
            scope,
            created_by,
        )
        self.connector_id = connector_id
        self.connector_name = Connectors.POSTGRESQL
        self.data_source: Optional[PostgreSQLDataSource] = None
        self.database_name: Optional[str] = None
        self.batch_size = 100
        self.rate_limiter = AsyncLimiter(25, 1)
        self.connector_scope: Optional[str] = None
        self.created_by: Optional[str] = None
        self.sync_filters: FilterCollection = FilterCollection()
        self.indexing_filters: FilterCollection = FilterCollection()
        self.sync_stats: SyncStats = SyncStats()
        self._frontend_url: str = os.getenv("FRONTEND_PUBLIC_URL", "").rstrip("/")

        self._schema_filter_cache: List[FilterOption] = []
        self._table_filter_cache:  List[FilterOption] = []
        self._filter_cache_rebuild_event: Optional[asyncio.Event] = None

        # Initialize sync point for incremental sync
        org_id = self.data_entities_processor.org_id
        self.tables_sync_point = SyncPoint(
            connector_id=self.connector_id,
            org_id=org_id,
            sync_data_point_type=SyncDataPointType.RECORDS,
            data_store_provider=data_store_provider
        )

    def get_app_users(self, users: List[User]) -> List[AppUser]:
        """Convert User objects to AppUser objects for PostgreSQL connector."""
        return [
            AppUser(
                app_name=self.connector_name,
                connector_id=self.connector_id,
                source_user_id=user.source_user_id or user.id or user.email,
                org_id=user.org_id or self.data_entities_processor.org_id,
                email=user.email,
                full_name=user.full_name or user.email,
                is_active=user.is_active if user.is_active is not None else True,
                title=user.title,
            )
            for user in users
            if user.email
        ]

    async def _create_app_users(self) -> None:
        """Establish user/team relationships with this PostgreSQL connector app.

        For TEAM scope: creates a single team-app edge so all org members get access.
        For PERSONAL scope: creates a user-app edge only for the creator.
        """
        try:
            if self.scope == ConnectorScope.TEAM.value:
                await self.data_entities_processor.ensure_team_app_edge(
                    self.connector_id
                )
                self.logger.info("Ensured team-app edge for PostgreSQL connector")
            else:
                if self.created_by:
                    creator_user = await self.data_entities_processor.get_user_by_user_id(self.created_by)
                    if creator_user and getattr(creator_user, "email", None):
                        app_users = self.get_app_users([creator_user])
                        await self.data_entities_processor.on_new_app_users(app_users)
                        self.logger.info(
                            "Created user-app edge for PostgreSQL connector creator %s",
                            self.created_by,
                        )
                    else:
                        self.logger.warning(
                            "Creator user not found or has no email for created_by %s; skipping user-app edges.",
                            self.created_by,
                        )
                else:
                    self.logger.warning(
                        "Personal PostgreSQL connector has no created_by; skipping user-app edges."
                    )
        except Exception as e:
            self.logger.error(f"Error creating app users: {e}", exc_info=True)
            raise

    async def init(self) -> bool:
        try:
            config = await self.config_service.get_config(
                f"/services/connectors/{self.connector_id}/config"
            )
            if not config:
                self.logger.error("PostgreSQL configuration not found")
                return False

            auth_config = config.get("auth") or {}

            # Check if using connection string or individual fields
            connection_string = auth_config.get("connectionString")
            sslmode = auth_config.get("sslmode", "prefer")

            if connection_string:
                # Parse connection string (postgresql://user:password@host:port/database)
                try:
                    from urllib.parse import parse_qs, unquote, urlparse
                    parsed = urlparse(connection_string)

                    if parsed.scheme not in ("postgresql", "postgres"):
                        self.logger.error(
                            f"Invalid PostgreSQL connection string scheme: {parsed.scheme!r}"
                        )
                        return False

                    host = parsed.hostname
                    port = parsed.port or 5432
                    database = unquote(parsed.path.lstrip('/')) if parsed.path else ""
                    user = unquote(parsed.username) if parsed.username else None
                    password = unquote(parsed.password) if parsed.password else ""
                    # Dropping this silently would connect without the
                    # certificate checks the string asked for.
                    sslmode = parse_qs(parsed.query).get("sslmode", [sslmode])[0]

                    if not all([host, database, user]):
                        self.logger.error("Invalid PostgreSQL connection string")
                        return False

                except Exception as e:
                    self.logger.error(f"Failed to parse connection string: {e}")
                    return False
            else:
                # Use individual fields
                host = auth_config.get("host")
                port = int(auth_config.get("port", 5432))
                database = auth_config.get("database")
                user = auth_config.get("username")
                password = auth_config.get("password", "")

                if not all([host, database, user]):
                    self.logger.error("Missing required PostgreSQL configuration")
                    return False

            self.database_name = database
            self.scope = config.get("scope", self.scope or ConnectorScope.TEAM.value)
            self.connector_scope = self.scope
            self.created_by = config.get("created_by", self.created_by)

            pg_config_kwargs: Dict[str, Any] = {
                "host": host,
                "port": port,
                "database": database,
                "user": user,
                "password": password,
                "timeout": int(config.get("timeout", 30)),
                "sslmode": sslmode,
            }
            if config.get("min_pool_size") is not None:
                pg_config_kwargs["min_pool_size"] = int(config["min_pool_size"])
            if config.get("max_pool_size") is not None:
                pg_config_kwargs["max_pool_size"] = int(config["max_pool_size"])
            if config.get("pool_acquire_timeout") is not None:
                pg_config_kwargs["pool_acquire_timeout"] = float(config["pool_acquire_timeout"])

            pg_config = PostgreSQLConfig(
                **pg_config_kwargs,
            )
            client = pg_config.create_client()
            await client.connect()

            self.data_source = PostgreSQLDataSource(client)


            self.logger.info("PostgreSQL connector initialized successfully")
            return True

        except Exception as e:
            self.logger.error(f"Failed to initialize PostgreSQL connector: {e}", exc_info=True)
            return False

    async def run_sync(self) -> None:
        try:
            self.logger.info("📦 [Sync] Starting PostgreSQL sync...")

            if not self.data_source:
                raise ConnectionError("PostgreSQL connector not initialized")

            self.sync_filters, self.indexing_filters = await load_connector_filters(
                self.config_service, "postgresql", self.connector_id, self.logger
            )

            self.sync_stats = SyncStats()

            stored_state = await self.tables_sync_point.read_sync_point(SYNC_STATE_KEY)

            if self._has_usable_state(stored_state):
                self.logger.info("📦 [Sync] Found existing sync state, running incremental sync...")
                await self.run_incremental_sync()
            else:
                self.logger.info("📦 [Sync] No usable sync state, running full sync...")
                await self._run_full_sync_internal()

            self.sync_stats.log_summary(self.logger)

        except Exception as e:
            self.logger.error(f"❌ [Sync] Error: {e}", exc_info=True)
            raise

    @staticmethod
    def _has_usable_state(stored_state: Optional[Dict[str, Any]]) -> bool:
        return bool(
            stored_state
            and stored_state.get("table_states")
            and stored_state.get("state_version") == SYNC_STATE_VERSION
        )

    @staticmethod
    def _passes_filter(value: str, selected: Optional[List[str]], operator: str) -> bool:
        if not selected:
            return True
        if operator == MultiselectOperator.NOT_IN.value:
            return value not in selected
        return value in selected

    def _get_filter_values(
        self,
    ) -> Tuple[Optional[List[str]], str, Optional[List[str]], str]:
        schema_filter = self.sync_filters.get("schemas")
        if schema_filter and schema_filter.value:
            selected_schemas = schema_filter.value
            schemas_op = schema_filter.operator_value
        else:
            selected_schemas = None
            schemas_op = MultiselectOperator.IN.value

        table_filter = self.sync_filters.get("tables")
        if table_filter and table_filter.value:
            selected_tables = table_filter.value
            tables_op = table_filter.operator_value
        else:
            selected_tables = None
            tables_op = MultiselectOperator.IN.value

        return selected_schemas, schemas_op, selected_tables, tables_op

    async def _run_full_sync_internal(self) -> None:
        try:
            self.logger.info("📦 [Full Sync] Starting full sync...")

            # Create AppUser entries for all active users
            await self._create_app_users()

            await self._create_database_record_group()

            selected_schemas, schemas_op, selected_tables, tables_op = self._get_filter_values()

            # Taken before listing, so a table written to while the sync runs has
            # counters past this snapshot and the next run picks the change up.
            snapshot, _ = await self._get_current_table_states(
                selected_schemas, schemas_op, selected_tables, tables_op
            )

            schemas = await self._fetch_schemas()

            schemas = [
                s for s in schemas if self._passes_filter(s.name, selected_schemas, schemas_op)
            ]

            await self._sync_schemas(schemas)
            self.sync_stats.schemas_synced = len(schemas)
            if any(s.name == self.database_name for s in schemas):
                await self._remove_database_group_self_links()

            listed: Set[str] = set()
            synced: Set[str] = set()
            for schema in schemas:
                table_names = [
                    n for n in await self._list_table_names(schema.name)
                    if self._passes_filter(table_fqn(schema.name, n), selected_tables, tables_op)
                ]

                listed.update(table_fqn(schema.name, n) for n in table_names)
                tables = await self._load_tables(schema.name, table_names, snapshot)
                synced |= await self._sync_tables(schema.name, tables)

            self.sync_stats.tables_new += len(synced)

            undeleted = await self._remove_stale_tables(listed)
            await self._remove_stale_schema_groups(synced_schemas=[s.name for s in schemas])

            # Only tables that were synced: one left out is found as new next run.
            # A stale table whose delete failed stays in, so incremental sync finds
            # it missing and retries the delete.
            await self._save_tables_sync_state(
                {**undeleted, **{fqn: snapshot[fqn] for fqn in synced if fqn in snapshot}}
            )

            self.logger.info("✅ [Full Sync] PostgreSQL full sync completed")
        except Exception as e:
            self.sync_stats.errors += 1
            self.logger.error(f"❌ [Full Sync] Error: {e}", exc_info=True)
            raise

    async def _create_database_record_group(self) -> None:
        permissions = await self._get_permissions()
        rg = RecordGroup(
            name=self.database_name,
            external_group_id=self.database_name,
            group_type=RecordGroupType.SQL_DATABASE,
            connector_name=self.connector_name,
            connector_id=self.connector_id,
            description=f"PostgreSQL Database: {self.database_name}",
        )
        await self.data_entities_processor.on_new_record_groups([(rg, permissions)])
        self.logger.info(f"Created database record group: {self.database_name}")

    def _schema_group_id(self, schema_name: str) -> str:
        # Record groups are looked up by external id alone, so a schema named after
        # its database would otherwise be merged into the database's group.
        if schema_name == self.database_name:
            return f"{self.database_name}.{schema_name}"
        return schema_name

    async def _fetch_schemas(self) -> List[PostgresSchema]:
        response = await self.data_source.list_schemas()
        if not response.success:
            raise RuntimeError(f"Failed to list schemas: {response.error}")

        schemas = []
        for item in response.data:
            info = SchemaInfo.model_validate(item)
            schemas.append(PostgresSchema(
                name=info.name,
                owner=info.owner,
            ))
        return schemas

    async def _list_table_names(self, schema: str) -> List[str]:
        response = await self.data_source.list_tables(schema=schema)
        if not response.success:
            raise RuntimeError(f"Failed to list tables in {schema}: {response.error}")
        return [TableListEntry.model_validate(item).name for item in response.data]

    async def _load_table(
        self,
        schema_name: str,
        table_name: str,
        state: Optional[PostgresTableState] = None,
    ) -> PostgresTable:
        """Read a table's columns and keys.

        Raises if any of them can't be read: syncing the table without them would
        index it as though it had none.
        """
        info, fks, pks = await asyncio.gather(
            self.data_source.get_table_info(schema_name, table_name),
            self.data_source.get_foreign_keys(schema_name, table_name),
            self.data_source.get_primary_keys(schema_name, table_name),
        )
        for what, response in (("columns", info), ("foreign keys", fks), ("primary keys", pks)):
            if not response.success:
                raise RuntimeError(
                    f"Failed to read {what} of {schema_name}.{table_name}: {response.error}"
                )

        return PostgresTable(
            name=table_name,
            schema_name=schema_name,
            row_count=state.n_live_tup if state else None,
            columns=TableDetail.model_validate(info.data).columns,
            foreign_keys=[ForeignKeyInfo.model_validate(fk) for fk in (fks.data or [])],
            primary_keys=[PrimaryKeyInfo.model_validate(pk).column_name for pk in (pks.data or [])],
            state=state,
        )

    async def _load_tables(
        self,
        schema_name: str,
        table_names: List[str],
        states: Dict[str, PostgresTableState],
    ) -> List[PostgresTable]:
        tables = []
        for name in table_names:
            try:
                tables.append(
                    await self._load_table(schema_name, name, states.get(table_fqn(schema_name, name)))
                )
            except Exception as e:
                self.sync_stats.errors += 1
                self.logger.error(f"Skipping table {schema_name}.{name}: {e}")
        return tables

    async def _get_permissions(self) -> List[Permission]:
        return [Permission(
            type=PermissionType.OWNER,
            entity_type=EntityType.ORG,
        )]

    async def _build_table_record(
        self,
        schema_name: str,
        table: PostgresTable,
    ) -> Tuple[SQLTableRecord, bool]:
        """Build the record for a table; the flag says whether one is already stored."""
        fqn = table_fqn(schema_name, table.name)
        existing = await self.data_entities_processor.get_record_by_external_id(
            connector_id=self.connector_id,
            external_record_id=fqn,
        )

        current_time = get_epoch_timestamp_in_ms()
        # Derived from the table's own state, so syncing an unchanged table again
        # finds the revision it stored last time and does not re-index it.
        revision = table.state.revision() if table.state else str(current_time)
        record_id = existing.id if existing else str(uuid.uuid4())
        changed = existing is None or existing.external_revision_id != revision

        record = SQLTableRecord(
            id=record_id,
            record_name=table.name,
            record_type=RecordType.SQL_TABLE,
            record_group_type=RecordGroupType.SQL_NAMESPACE.value,
            external_record_group_id=self._schema_group_id(schema_name),
            external_record_id=fqn,
            external_revision_id=revision,
            origin=OriginTypes.CONNECTOR.value,
            connector_name=self.connector_name,
            connector_id=self.connector_id,
            mime_type=MimeTypes.SQL_TABLE.value,
            weburl=f"{self._frontend_url}/record/{record_id}" if self._frontend_url else "",
            source_created_at=existing.source_created_at if existing else current_time,
            source_updated_at=current_time if changed else existing.source_updated_at,
            row_count=table.row_count,
            # 0 on an existing record lets the save bump the stored version only
            # when the revision changed.
            version=0 if existing else 1,
            inherit_permissions=True,
        )

        for fk in table.foreign_keys:
            if not fk.foreign_table_name:
                continue
            target_schema = fk.foreign_table_schema or schema_name
            target_fqn = table_fqn(target_schema, fk.foreign_table_name)
            record.related_external_records.append(
                RelatedExternalRecord(
                    external_record_id=target_fqn,
                    record_type=RecordType.SQL_TABLE,
                    record_name=fk.foreign_table_name,
                    relation_type=RecordRelations.FOREIGN_KEY,
                    source_column=fk.column_name,
                    target_column=fk.foreign_column_name,
                    child_table_name=fqn,
                    parent_table_name=target_fqn,
                    constraint_name=fk.constraint_name,
                )
            )

        if self.indexing_filters and not self.indexing_filters.is_enabled(IndexingFilterKey.TABLES.value):
            record.indexing_status = ProgressStatus.AUTO_INDEX_OFF.value

        return record, existing is not None

    async def _process_tables_generator(
        self,
        schema_name: str,
        tables: List[PostgresTable],
    ) -> AsyncGenerator[Tuple[Record, List[Permission]], None]:

        for table in tables:
            try:
                record, _ = await self._build_table_record(schema_name, table)
            except Exception as e:
                self.sync_stats.errors += 1
                self.logger.error(f"Error processing table {table.name}: {e}", exc_info=True)
                continue

            yield (record, [])
            await asyncio.sleep(0)

    async def _sync_schemas(self, schemas: List[PostgresSchema]) -> None:
        if not schemas:
            return
        groups = []
        for schema in schemas:
            rg = RecordGroup(
                name=schema.name,
                external_group_id=self._schema_group_id(schema.name),
                group_type=RecordGroupType.SQL_NAMESPACE,
                connector_name=self.connector_name,
                connector_id=self.connector_id,
                description=f"PostgreSQL Schema: {schema.name}",
                parent_external_group_id=self.database_name,
                inherit_permissions=True,
            )
            groups.append((rg, []))
        await self.data_entities_processor.on_new_record_groups(groups)
        self.logger.info(f"Synced {len(groups)} schemas")

    async def _sync_tables(self, schema_name: str, tables: List[PostgresTable]) -> Set[str]:
        """Save records for these tables; returns the ones that were saved."""
        synced: Set[str] = set()
        if not tables:
            return synced

        batch: List[Tuple[Record, List[Permission]]] = []

        async for record, perms in self._process_tables_generator(schema_name, tables):
            batch.append((record, perms))

            if len(batch) >= self.batch_size:
                self.logger.debug(f"Processing batch of {len(batch)} tables")
                await self.data_entities_processor.on_new_records(batch)
                synced.update(r.external_record_id for r, _ in batch)
                batch = []

        if batch:
            await self.data_entities_processor.on_new_records(batch)
            synced.update(r.external_record_id for r, _ in batch)

        self.logger.info(f"Synced {len(synced)} tables in {schema_name}")
        return synced

    async def _sync_updated_tables(self, schema_name: str, tables: List[PostgresTable]) -> Set[str]:
        """Re-sync tables whose content or columns changed; returns the ones saved."""
        synced: Set[str] = set()
        if not tables:
            return synced

        self.logger.info(f"Processing {len(tables)} updated tables in {schema_name}")

        for table in tables:
            fqn = table_fqn(schema_name, table.name)
            try:
                record, existed = await self._build_table_record(schema_name, table)
                if existed:
                    await self.data_entities_processor.on_record_content_update(record)
                else:
                    # Known to the saved state but never stored: create it rather
                    # than skip it, or nothing would ever create it.
                    self.logger.warning(f"No record for changed table {fqn}; creating it")
                    await self.data_entities_processor.on_new_records([(record, [])])
                synced.add(fqn)
                self.logger.debug(f"Published content update for table: {fqn}")
            except Exception as e:
                self.sync_stats.errors += 1
                self.logger.error(f"Error syncing updated table {fqn}: {e}", exc_info=True)

        self.logger.info(f"Completed syncing {len(synced)} of {len(tables)} updated tables in {schema_name}")
        return synced

    async def stream_record(
        self,
        record: Record,
        user_id: Optional[str] = None,
        convertTo: Optional[str] = None
    ) -> StreamingResponse:
        try:
            if not self.data_source:
                raise connector_not_ready(self.display_name)

            if record.record_type == RecordType.SQL_TABLE:
                schema, table = self._split_table_fqn(record)

                table_info_response = await self.data_source.get_table_info(schema, table)
                if not table_info_response.success:
                    self.logger.error(f"❌ Failed to get table info for {schema}.{table}: {table_info_response.error}")
                    raise to_sql_response_error(
                        table_info_response.error, connector=self.display_name
                    )
                detail = TableDetail.model_validate(table_info_response.data)
                columns: List[ColumnInfo] = detail.columns
                self.logger.info(f"✅ Retrieved {len(columns)} columns for {schema}.{table}")

                # These queries only fail on a driver error — a table with no
                # constraints succeeds with an empty list — so degrading here
                # would stream a table whose keys were refused, not absent.
                fks_response = await self.data_source.get_foreign_keys(schema, table)
                if not fks_response.success:
                    self.logger.error(f"❌ Failed to get foreign keys for {schema}.{table}: {fks_response.error}")
                    raise to_sql_response_error(
                        fks_response.error, connector=self.display_name
                    )
                foreign_keys = [
                    ForeignKeyInfo.model_validate(fk) for fk in (fks_response.data or [])
                ]

                pks_response = await self.data_source.get_primary_keys(schema, table)
                if not pks_response.success:
                    self.logger.error(f"❌ Failed to get primary keys for {schema}.{table}: {pks_response.error}")
                    raise to_sql_response_error(
                        pks_response.error, connector=self.display_name
                    )
                primary_keys: List[str] = [
                    PrimaryKeyInfo.model_validate(pk).column_name
                    for pk in (pks_response.data or [])
                ]

                sync_filters, _ = await load_connector_filters(
                    self.config_service, "postgresql", self.connector_id, self.logger
                )
                max_rows = self._max_rows(sync_filters)
                try:
                    rows = await self.data_source.fetch_table_rows(
                        schema, table, limit=max_rows, order_by=primary_keys or None,
                        max_bytes=MAX_TABLE_DOCUMENT_BYTES,
                    )
                except Exception as e:
                    self.logger.error(f"❌ Failed to read rows for {schema}.{table}: {e}")
                    raise to_sql_stream_error(e, connector=self.display_name) from e

                ddl_response = await self.data_source.get_table_ddl(schema, table)
                if not ddl_response.success:
                    self.logger.error(f"❌ Failed to get DDL for {schema}.{table}: {ddl_response.error}")
                    raise to_sql_response_error(
                        ddl_response.error, connector=self.display_name
                    )
                ddl = DDLResult.model_validate(ddl_response.data).ddl

                data = {
                    "table_name": table,
                    "schema_name": schema,
                    "database_name": self.database_name,
                    "columns": [col.model_dump() for col in columns],
                    "rows": _decode_json_columns(rows, columns),
                    "foreign_keys": [fk.model_dump() for fk in foreign_keys],
                    "primary_keys": primary_keys,
                    "ddl": ddl,
                    "connector_name": self.connector_name.value if hasattr(self.connector_name, "value") else str(self.connector_name),
                }

                json_bytes = json.dumps(data, default=_json_default).encode("utf-8")

                async def json_iterator():
                    yield json_bytes

                return create_stream_record_response(
                    json_iterator(), filename=f"{table}.json", mime_type=MimeTypes.SQL_TABLE.value
                )

            raise HTTPException(status_code=400, detail="Unsupported record type")

        except Exception as e:
            self.logger.error(f"Error streaming record: {e}", exc_info=True)
            raise

    def _split_table_fqn(self, record: Record) -> Tuple[str, str]:
        """Schema and table of a table record."""
        fqn = record.external_record_id or ""
        try:
            return split_table_fqn(fqn)
        except ValueError:
            pass

        # Ids written before dotted names were quoted: the record's schema
        # group says where the schema name ends.
        group_id = record.external_record_group_id
        if group_id:
            schema = (
                self.database_name
                if group_id == self._schema_group_id(self.database_name or "")
                else group_id
            )
            if fqn.startswith(f"{schema}.") and len(fqn) > len(schema) + 1:
                return schema, fqn[len(schema) + 1:]
        raise HTTPException(status_code=500, detail="Invalid table FQN")

    async def test_connection_and_access(self) -> bool:
        if not self.data_source:
            return False
        try:
            response = await self.data_source.test_connection()
            if response.success:
                self.logger.info("PostgreSQL connection test successful")
                return True
            self.logger.error(f"Connection test failed: {response.error}")
            return False
        except Exception as e:
            self.logger.error(f"Connection test failed: {e}", exc_info=True)
            return False

    async def cleanup(self) -> None:
        try:
            self.logger.info("Starting PostgreSQL connector cleanup...")

            if self.data_source:
                client = self.data_source.get_client()
                if client:
                    await client.close()
                self.data_source = None

            self.database_name = None

            self.logger.info("PostgreSQL connector cleanup completed")

        except Exception as e:
            self.logger.error(f"Error during PostgreSQL connector cleanup: {e}", exc_info=True)

    def get_signed_url(self, record: Record) -> Optional[str]:
        """
        Get a signed URL for a record.
        
        PostgreSQL doesn't support signed URLs for direct file access.
        
        Returns:
            None - not supported for PostgreSQL
        """
        return None

    def handle_webhook_notification(self, notification: Dict) -> None:
        """
        Handle webhook notifications from PostgreSQL.

        PostgreSQL does not support webhooks for data change notifications.
        This method raises NotImplementedError as per the base class contract.
        """
        raise NotImplementedError(
            "PostgreSQL does not support webhook notifications. "
            "Use scheduled sync or PostgreSQL logical replication for change tracking."
        )

    async def reindex_records(self, records: List[Record]) -> None:
        """
        Reindex records for PostgreSQL.

        Checks if records still exist and have updated content at the source,
        then triggers reindexing for changed records.

        Args:
            records: List of Record objects to reindex
        """
        try:
            if not records:
                self.logger.info("No records to reindex")
                return

            self.logger.info(f"Starting reindex for {len(records)} PostgreSQL records")

            if not self.data_source:
                self.logger.error("Data source not initialized. Call init() first.")
                raise Exception("PostgreSQL data source not initialized")

            # For PostgreSQL, we just reindex all records
            # since we don't have efficient change detection
            await self.data_entities_processor.reindex_existing_records(records)
            self.logger.info(f"Published reindex events for {len(records)} records")

        except Exception as e:
            self.logger.error(f"Error during PostgreSQL reindex: {e}", exc_info=True)
            raise

    async def run_incremental_sync(self) -> None:
        """
        Run incremental sync for PostgreSQL using cumulative DML counters.

        Compares current table states (n_tup_ins, n_tup_upd, n_tup_del, column_hash)
        with previously stored states to detect changes.

        Change detection:
        - New tables: Not in stored state → full sync for table
        - Schema changes: Column hash differs → reindex
        - Data changes: Any DML counter increased → reindex
        - Stats reset: Any counter decreased → assume table changed and resync
        - Deleted tables: In stored state but not in DB → handle deletion

        The state saved for the next run is the snapshot taken here for every
        table this run handled, and the previous state for any it could not, so
        the next run finds those again.
        """
        self.logger.info("📦 [Incremental Sync] Starting PostgreSQL incremental sync...")

        if not self.data_source:
            raise ConnectionError("PostgreSQL connector not initialized")

        self.sync_filters, self.indexing_filters = await load_connector_filters(
            self.config_service, "postgresql", self.connector_id, self.logger
        )

        try:
            stored_state = await self.tables_sync_point.read_sync_point(SYNC_STATE_KEY)

            if not self._has_usable_state(stored_state):
                self.logger.info("No usable sync state, running full sync")
                await self._run_full_sync_internal()
                return

            stored: Dict[str, PostgresTableState] = {
                fqn: PostgresTableState.model_validate(state)
                for fqn, state in json.loads(stored_state["table_states"]).items()
            }

            filters = self._get_filter_values()
            current, unreadable = await self._get_current_table_states(*filters)

            new_tables = sorted(current.keys() - stored.keys())
            missing = stored.keys() - current.keys() - unreadable
            deleted_tables = await self._confirm_dropped(sorted(missing), stored, filters)
            changed_tables = sorted(
                fqn for fqn in current.keys() & stored.keys()
                if self._has_table_changed(current[fqn], stored[fqn])
            )

            self.logger.info(
                f"📊 Change detection: new={len(new_tables)}, "
                f"changed={len(changed_tables)}, deleted={len(deleted_tables)}, "
                f"unreadable={len(unreadable)}"
            )

            # Unreadable, or missing from the stats but still listed: keep what
            # was known and look again next run.
            next_state: Dict[str, PostgresTableState] = {
                fqn: stored[fqn]
                for fqn in (unreadable & stored.keys()) | (missing - set(deleted_tables))
            }
            for fqn in current.keys() & stored.keys():
                next_state[fqn] = current[fqn]

            for fqn in await self._sync_new_tables(new_tables, current):
                next_state[fqn] = current[fqn]

            synced_changed = await self._sync_changed_tables(changed_tables, current)
            for fqn in changed_tables:
                if fqn not in synced_changed:
                    next_state[fqn] = stored[fqn]

            removed = await self._handle_deleted_tables(deleted_tables)
            for fqn in deleted_tables:
                if fqn not in removed:
                    next_state[fqn] = stored[fqn]
            if removed:
                await self._remove_stale_schema_groups(filters=filters)

            await self._save_tables_sync_state(next_state)

            self.logger.info("✅ [Incremental Sync] PostgreSQL incremental sync completed")

        except Exception as e:
            self.logger.error(f"❌ [Incremental Sync] Error: {e}", exc_info=True)
            raise

    async def _confirm_dropped(
        self,
        candidates: List[str],
        stored: Dict[str, PostgresTableState],
        filters: Tuple[Optional[List[str]], str, Optional[List[str]], str],
    ) -> List[str]:
        """Of the tables missing from the stats, those that are really gone.

        Gone means the sync filters now exclude the table, or the table listing
        a full sync uses no longer has it. The stats come from another catalog,
        so deleting on their word alone would let an empty or partial answer
        remove tables that still exist. Raises if a listing fails.
        """
        selected_schemas, schemas_op, selected_tables, tables_op = filters
        dropped: List[str] = []
        to_check: Dict[str, List[Tuple[str, str]]] = defaultdict(list)

        for fqn in candidates:
            state = stored[fqn]
            if state.schema_name:
                schema_name, table_name = state.schema_name, state.table_name
            else:
                schema_name, table_name = split_table_fqn(fqn)
            if not (
                self._passes_filter(schema_name, selected_schemas, schemas_op)
                and self._passes_filter(fqn, selected_tables, tables_op)
            ):
                dropped.append(fqn)
                continue
            to_check[schema_name].append((fqn, table_name))

        for schema_name, tables in to_check.items():
            listed = set(await self._list_table_names(schema_name))
            dropped.extend(fqn for fqn, table_name in tables if table_name not in listed)

        if len(dropped) < len(candidates):
            self.logger.warning(
                f"{len(candidates) - len(dropped)} tables are missing from the stats "
                f"but still listed; keeping them"
            )
        return sorted(dropped)

    async def _get_current_table_states(
        self,
        selected_schemas: Optional[List[str]],
        schemas_op: str = MultiselectOperator.IN.value,
        selected_tables: Optional[List[str]] = None,
        tables_op: str = MultiselectOperator.IN.value,
    ) -> Tuple[Dict[str, PostgresTableState], Set[str]]:
        """Fetch current table states from PostgreSQL for comparison.

        Retrieves cumulative DML counters (n_tup_ins, n_tup_upd, n_tup_del) along with
        column hash for reliable change detection that survives ANALYZE runs. On a
        standby, where replayed writes leave the counters still, each table's row
        sample is hashed as well.

        Returns the states and, separately, the tables that could not be read this
        run: those are unknown, not dropped. Raises if the stats or columns can't
        be read at all, since an empty answer would read as every table dropped.
        """
        table_states: Dict[str, PostgresTableState] = {}
        unreadable: Set[str] = set()

        # For IN, push schema filter down to SQL; for NOT_IN, fetch all and exclude client-side.
        schemas_exclude = schemas_op == MultiselectOperator.NOT_IN.value
        scope = None if schemas_exclude else selected_schemas
        stats_response = await self.data_source.get_table_stats(scope)
        if not stats_response.success:
            raise RuntimeError(f"Failed to get table stats: {stats_response.error}")

        stats_by_fqn: Dict[str, TableStats] = {}
        for stat_dict in stats_response.data:
            stat = TableStats.model_validate(stat_dict)
            fqn = table_fqn(stat.schema_name, stat.table_name)
            if not (
                self._passes_filter(stat.schema_name, selected_schemas, schemas_op)
                and self._passes_filter(fqn, selected_tables, tables_op)
            ):
                continue
            stats_by_fqn[fqn] = stat

        if not stats_by_fqn:
            return table_states, unreadable

        columns_response = await self.data_source.get_columns(scope)
        if not columns_response.success:
            raise RuntimeError(f"Failed to get columns: {columns_response.error}")
        columns_by_table: Dict[Tuple[str, str], List[ColumnInfo]] = defaultdict(list)
        for row in columns_response.data:
            row = dict(row)
            key = (row.pop("schema_name"), row.pop("table_name"))
            columns_by_table[key].append(ColumnInfo.model_validate(row))

        primary_keys = await self._primary_keys_if_standby(scope)
        max_rows = self._max_rows(self.sync_filters)

        for fqn, stat in stats_by_fqn.items():
            key = (stat.schema_name, stat.table_name)
            state = PostgresTableState(
                column_hash=_column_hash(columns_by_table.get(key, [])),
                n_tup_ins=stat.n_tup_ins or 0,
                n_tup_upd=stat.n_tup_upd or 0,
                n_tup_del=stat.n_tup_del or 0,
                n_live_tup=stat.n_live_tup or 0,
                schema_name=stat.schema_name,
                table_name=stat.table_name,
            )
            if primary_keys is not None:
                sample = await self.data_source.get_sample_hash(
                    stat.schema_name, stat.table_name,
                    limit=max_rows, order_by=primary_keys.get(key) or None,
                    max_bytes=MAX_TABLE_DOCUMENT_BYTES,
                )
                if not sample.success:
                    self.logger.warning(f"Could not sample {fqn}: {sample.error}")
                    unreadable.add(fqn)
                    continue
                state.sample_hash = sample.data["sample_hash"]
            table_states[fqn] = state

        return table_states, unreadable

    async def _primary_keys_if_standby(
        self,
        scope: Optional[List[str]],
    ) -> Optional[Dict[Tuple[str, str], List[str]]]:
        """Primary keys per table when the server is a standby, else None."""
        recovery = await self.data_source.is_in_recovery()
        if not recovery.success:
            raise RuntimeError(f"Failed to read recovery state: {recovery.error}")
        if not recovery.data["in_recovery"]:
            return None

        response = await self.data_source.get_primary_keys_by_table(scope)
        if not response.success:
            raise RuntimeError(f"Failed to get primary keys: {response.error}")
        keys: Dict[Tuple[str, str], List[str]] = defaultdict(list)
        for row in response.data:
            keys[(row["schema_name"], row["table_name"])].append(row["column_name"])
        return keys

    @staticmethod
    def _max_rows(sync_filters: FilterCollection) -> int:
        value = sync_filters.get_value(IndexingFilterKey.MAX_ROWS_PER_TABLE, default=1000)
        return max(1, min(int(value), MAX_ROWS_PER_TABLE_LIMIT))

    def _has_table_changed(
        self,
        current: PostgresTableState,
        stored: PostgresTableState
    ) -> bool:
        """Check if table has changed by comparing metadata.

        Uses cumulative DML counters (n_tup_ins, n_tup_upd, n_tup_del) for reliable
        change detection. Also detects if stats were reset (e.g., pg_stat_reset()
        or server restart) and triggers resync in that case.
        """
        if current.column_hash != stored.column_hash:
            return True

        stats_were_reset = (
            current.n_tup_ins < stored.n_tup_ins or
            current.n_tup_upd < stored.n_tup_upd or
            current.n_tup_del < stored.n_tup_del
        )

        if stats_were_reset:
            self.logger.info("Stats reset detected, triggering resync")
            return True

        return (
            current.n_tup_ins != stored.n_tup_ins or
            current.n_tup_upd != stored.n_tup_upd or
            current.n_tup_del != stored.n_tup_del or
            current.sample_hash != stored.sample_hash
        )

    async def _sync_new_tables(
        self,
        table_fqns: List[str],
        states: Dict[str, PostgresTableState],
    ) -> Set[str]:
        """Sync newly discovered tables; returns the ones saved.

        Also ensures parent schema RecordGroups exist for any new schemas
        that weren't present during the initial full sync.
        """
        synced: Set[str] = set()
        if not table_fqns:
            return synced

        self.logger.info(f"Syncing {len(table_fqns)} new tables")

        new_schemas = sorted({states[fqn].schema_name for fqn in table_fqns})
        await self._sync_schemas([PostgresSchema(name=s) for s in new_schemas])

        for fqn in table_fqns:
            state = states[fqn]
            try:
                table = await self._load_table(state.schema_name, state.table_name, state)
                synced |= await self._sync_tables(state.schema_name, [table])
            except Exception as e:
                self.sync_stats.errors += 1
                self.logger.error(f"Error syncing new table {fqn}: {e}", exc_info=True)

        self.sync_stats.tables_new += len(synced)
        return synced

    async def _sync_changed_tables(
        self,
        table_fqns: List[str],
        states: Dict[str, PostgresTableState],
    ) -> Set[str]:
        """Sync changed tables; returns the ones saved."""
        synced: Set[str] = set()
        if not table_fqns:
            return synced

        self.logger.info(f"Syncing {len(table_fqns)} changed tables")

        for fqn in table_fqns:
            state = states[fqn]
            try:
                table = await self._load_table(state.schema_name, state.table_name, state)
            except Exception as e:
                self.sync_stats.errors += 1
                self.logger.error(f"Error reading changed table {fqn}: {e}")
                continue
            synced |= await self._sync_updated_tables(state.schema_name, [table])

        return synced

    async def _handle_deleted_tables(self, table_fqns: List[str]) -> Set[str]:
        """Delete records of tables that no longer exist; returns the ones handled."""
        handled: Set[str] = set()
        if not table_fqns:
            return handled

        self.logger.info(f"Handling {len(table_fqns)} deleted tables")

        for fqn in table_fqns:
            try:
                record = await self.data_entities_processor.get_record_by_external_id(
                    connector_id=self.connector_id,
                    external_record_id=fqn
                )
                if record and record.id:
                    await self.data_entities_processor.on_record_deleted(record.id)
                    self.logger.debug(f"Deleted record for table: {fqn}")
                handled.add(fqn)
            except Exception as e:
                self.sync_stats.errors += 1
                self.logger.warning(f"Failed to delete record for {fqn}: {e}")

        return handled

    async def _remove_stale_tables(self, listed_fqns: Set[str]) -> Dict[str, PostgresTableState]:
        """Delete records of tables that were dropped or are now filtered out.

        Returns a placeholder state for each table whose delete failed.
        """
        records = await self.data_entities_processor.get_records_by_record_type(
            self.connector_id, RecordType.SQL_TABLE
        )
        stale = [r for r in records if r.external_record_id not in listed_fqns]
        undeleted: Dict[str, PostgresTableState] = {}
        if not stale:
            return undeleted

        self.logger.info(f"Removing {len(stale)} tables that are no longer synced")
        for record in stale:
            try:
                await self.data_entities_processor.on_record_deleted(record.id)
            except Exception as e:
                self.sync_stats.errors += 1
                self.logger.warning(f"Failed to delete record for {record.external_record_id}: {e}")
                try:
                    schema_name, table_name = self._split_table_fqn(record)
                except HTTPException:
                    schema_name, table_name = "", ""
                undeleted[record.external_record_id] = PostgresTableState(
                    schema_name=schema_name, table_name=table_name
                )
        return undeleted

    async def _remove_stale_schema_groups(
        self,
        synced_schemas: Optional[List[str]] = None,
        filters: Optional[Tuple[Optional[List[str]], str, Optional[List[str]], str]] = None,
    ) -> None:
        """Delete the groups of schemas that were dropped or are now filtered out.

        Pass the schemas just synced, or the filters to list them with. Best
        effort: their tables are already gone, so a leftover group is empty and
        makes nothing searchable.
        """
        try:
            if synced_schemas is None:
                selected_schemas, schemas_op, _, _ = filters or self._get_filter_values()
                synced_schemas = [
                    schema.name for schema in await self._fetch_schemas()
                    if self._passes_filter(schema.name, selected_schemas, schemas_op)
                ]
            keep = {self._schema_group_id(name) for name in synced_schemas}
            # Older versions saved the database group as SQL_NAMESPACE when a schema
            # shared its name; incremental sync doesn't recreate it, so never delete it here.
            if self.database_name:
                keep.add(self.database_name)
            async with self.data_store_provider.transaction() as tx_store:
                groups = await tx_store.get_nodes_by_filters(
                    collection=CollectionNames.RECORD_GROUPS.value,
                    filters={
                        "connectorId": self.connector_id,
                        "groupType": RecordGroupType.SQL_NAMESPACE.value,
                    },
                )
            stale = sorted({g.get("externalGroupId") for g in groups} - keep - {None})
            for external_group_id in stale:
                await self.data_entities_processor.on_record_group_deleted(
                    external_group_id, self.connector_id
                )
            if stale:
                self.logger.info(f"Removed {len(stale)} schema groups that are no longer synced")
        except Exception as e:
            self.sync_stats.errors += 1
            self.logger.warning(f"Failed to remove stale schema groups: {e}")

    async def _remove_database_group_self_links(self) -> None:
        """Remove links from the database group to itself.

        Left by versions that gave a schema named after its database the
        database's group id, which linked that group to itself as its own parent.
        """
        async with self.data_store_provider.transaction() as tx_store:
            group = await tx_store.get_record_group_by_external_id(
                connector_id=self.connector_id, external_id=self.database_name
            )
            if not group:
                return
            for edge_collection in (CollectionNames.BELONGS_TO.value, CollectionNames.INHERIT_PERMISSIONS.value):
                await tx_store.delete_edge(
                    group.id, CollectionNames.RECORD_GROUPS.value,
                    group.id, CollectionNames.RECORD_GROUPS.value,
                    edge_collection,
                )

    async def _save_tables_sync_state(self, states: Dict[str, PostgresTableState]) -> None:
        """Save table states for the next incremental sync to compare against."""
        serialized_states = json.dumps(
            {fqn: state.model_dump() for fqn, state in states.items()}
        )
        await self.tables_sync_point.update_sync_point(
            SYNC_STATE_KEY,
            {
                "last_sync_time": get_epoch_timestamp_in_ms(),
                "state_version": SYNC_STATE_VERSION,
                "table_states": serialized_states,
            }
        )
        self.logger.debug(f"Saved sync state for {len(states)} tables")

    async def _populate_filter_cache(self) -> None:
        """
        Fetch all schemas and tables from PostgreSQL and rebuild the in-memory
        filter caches.  Race-condition safe: concurrent callers wait on the
        rebuild event instead of each issuing their own DB round-trip.
        """
        if self._filter_cache_rebuild_event is not None:
            await self._filter_cache_rebuild_event.wait()
            return

        self._filter_cache_rebuild_event = asyncio.Event()
        try:
            if not self.data_source:
                raise RuntimeError("PostgreSQL data source not initialized")

            schemas_resp = await self.data_source.list_schemas()
            if not schemas_resp.success:
                raise RuntimeError(schemas_resp.error or "Failed to fetch schemas")

            schema_cache: List[FilterOption] = []
            table_cache:  List[FilterOption] = []

            for schema_dict in schemas_resp.data:
                schema_info = SchemaInfo.model_validate(schema_dict)
                if not schema_info.name:
                    continue
                schema_cache.append(FilterOption(id=schema_info.name, label=schema_info.name))

                tables_resp = await self.data_source.list_tables(schema=schema_info.name)
                if tables_resp.success:
                    for table_dict in tables_resp.data:
                        table_entry = TableListEntry.model_validate(table_dict)
                        if not table_entry.name:
                            continue
                        fqn = table_fqn(schema_info.name, table_entry.name)
                        table_cache.append(FilterOption(id=fqn, label=fqn))

            # Atomic swap so readers never see a partially-built cache
            self._schema_filter_cache = schema_cache
            self._table_filter_cache  = table_cache
            self.logger.info(
                "Filter cache rebuilt: %d schemas, %d tables",
                len(schema_cache), len(table_cache),
            )
        finally:
            ev = self._filter_cache_rebuild_event
            ev.set()                         
            self._filter_cache_rebuild_event = None

    async def get_filter_options(
        self,
        filter_key: str,
        page: int = 1,
        limit: int = 20,
        search: Optional[str] = None,
        cursor: Optional[str] = None,
    ) -> FilterOptionsResponse:
        try:
            page  = max(1, page)
            limit = max(1, min(limit, 100))
            if filter_key not in ("schemas", "tables"):
                return FilterOptionsResponse(
                    success=False,
                    options=[],
                    page=page,
                    limit=limit,
                    has_more=False,
                    message=f"Unknown filter key: {filter_key}",
                )

            # Rebuild when empty, and when the list is first opened so it is fresh;
            # later pages and searches read the cache.
            cache_empty = not self._schema_filter_cache and not self._table_filter_cache
            first_open = not (search and search.strip()) and not cursor and page == 1
            if cache_empty or first_open:
                await self._populate_filter_cache()

            if filter_key == "schemas":
                items = list(self._schema_filter_cache)
            else:
                items = list(self._table_filter_cache)

            if search and search.strip():
                sl = search.strip().lower()
                items = [i for i in items if sl in i.label.lower()]

            if cursor:
                try:
                    offset = max(0, int(cursor))
                except ValueError:
                    offset = 0
            else:
                offset = (page - 1) * limit

            page_items  = items[offset : offset + limit]
            options     = list(page_items)
            next_offset = offset + len(page_items)
            has_more    = next_offset < len(items)
            next_cursor = str(next_offset) if has_more else None

            return FilterOptionsResponse(
                success=True,
                options=options,
                page=page,
                limit=limit,
                has_more=has_more,
                cursor=next_cursor,
            )

        except Exception as e:
            self.logger.error(f"Error getting filter options for {filter_key}: {e}", exc_info=True)
            return FilterOptionsResponse(
                success=False,
                options=[],
                page=page,
                limit=limit,
                has_more=False,
                message=str(e),
            )

    @classmethod
    async def create_connector(
        cls,
        logger: Logger,
        data_store_provider: DataStoreProvider,
        config_service: ConfigurationService,
        connector_id: str,
        data_entities_processor,
        **kwargs,
    ) -> "PostgreSQLConnector":
        """Factory method to create a PostgreSQL connector instance."""
        return cls(
            logger,
            data_entities_processor,
            data_store_provider,
            config_service,
            connector_id,
            kwargs.get("scope", ConnectorScope.TEAM.value),
            kwargs.get("created_by"),
        )
