"""
PostgreSQL Connector

Syncs schemas, tables and their rows from PostgreSQL.
"""
import asyncio
import hashlib
import json
import os
import re
import socket
import ssl
import uuid
from collections import defaultdict
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from logging import Logger
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

import asyncpg
from aiolimiter import AsyncLimiter
from fastapi import HTTPException
from fastapi.responses import StreamingResponse

from app.config.configuration_service import ConfigurationService
from app.config.constants.arangodb import (
    CollectionNames,
    Connectors,
    MimeTypes,
    OriginTypes,
    PermissionModel,
    RecordRelations,
)
from app.connectors.core.base.connector.connector_service import (
    BaseConnector,
    ConnectionCheckResult,
    ConnectorInitError,
)
from app.connectors.core.base.data_processor.data_source_entities_processor import (
    DataSourceEntitiesProcessor,
)
from app.connectors.core.base.data_store.data_store import DataStoreProvider
from app.connectors.core.base.error.sql_stream_errors import (
    to_sql_response_error,
    to_sql_stream_error,
)
from app.connectors.core.base.error.stream_errors import connector_not_ready
from app.connectors.core.base.sync_point.sync_point import (
    SyncDataPointType,
    SyncPoint,
)
from app.connectors.core.constants import IconPaths
from app.connectors.core.registry.auth_builder import AuthBuilder, AuthType
from app.connectors.core.registry.connector_builder import (
    AuthField,
    CommonFields,
    ConnectorBuilder,
    ConnectorScope,
    DocumentationLink,
    SyncStrategy,
)
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
    SyncFilterKey,
    load_connector_filters,
)
from app.connectors.core.sql.column_records import (
    build_column_records,
    load_typed_columns,
)
from app.connectors.core.sql.permissions import database_permissions
from app.connectors.core.sql.qualified_names import (
    join_qualified_name,
    split_qualified_name,
)
from app.connectors.core.sql.record_moves import hold_renames, rename_updates
from app.connectors.core.sql.sync_outcome import SyncOutcome
from app.connectors.sources.postgres.apps import PostgreSQLApp
from app.connectors.sources.postgres.catalog_diff import (
    PostgresTableState,
    TableChange,
    diff_catalog,
)
from app.models.entities import (
    AppUser,
    ProgressStatus,
    Record,
    RecordGroup,
    RecordGroupType,
    RecordType,
    RelatedExternalRecord,
    SQLColumnRecord,
    SQLTableRecord,
    User,
)
from app.models.permission import Permission
from app.sources.client.postgres.postgres import PostgreSQLConfig, PostgreSQLResponse
from app.sources.external.postgres.postgres_ import (
    ColumnInfo,
    ColumnProfile,
    DDLResult,
    ForeignKeyInfo,
    PostgreSQLDataSource,
    PrimaryKeyInfo,
    SchemaInfo,
    TableColumnStatistics,
    TableDetail,
    TableListEntry,
    TableStats,
    countable_column_names,
    looks_personal,
)
from app.utils.streaming import create_stream_record_response
from app.utils.time_conversion import get_epoch_timestamp_in_ms
from app.utils.url_fetcher import HostCheckError

MAX_ROWS_PER_TABLE_LIMIT = 10000
# Cap on the rows' text in one table's document; Postgres stops sending rows
# past it, so a table of wide rows can't exhaust the connector's memory.
MAX_TABLE_DOCUMENT_BYTES = 8 * 1024 * 1024
MAX_COLUMN_VALUES_STREAMED = 1000

SYNC_STATE_KEY = "postgres_tables_state"
# Bump when a saved state can no longer be trusted to describe what is indexed;
# a state saved under another version sends the next run to a full sync.
# 2: states saved before it could list tables that were never synced.
# 3: column records saved before it have no stats or value profile.
# 4: states saved before it carry no table or column ids to follow a rename
#    with, and no constraint or storage digest.
SYNC_STATE_VERSION = 4

# Dropped tables removed per cascade; each batch is one delete and one
# on_sql_records_deleted call.
TABLES_REMOVED_PER_BATCH = 200
# Tries at saving the state mid-run, after records moved.
SYNC_STATE_SAVE_ATTEMPTS = 3

# Exact per-column distinct/null counts sort every column of the table, so they
# are taken only where rows x columns stays under this; larger tables rely on
# pg_stats estimates.
EXACT_COLUMN_STATS_MAX_CELLS = 1_000_000


# The user waits on the setup form while this runs; a firewall that drops
# packets otherwise holds them for the sync's 30 s connect timeout.
CONNECTION_CHECK_TIMEOUT_S = 10
SSL_MODES = ("disable", "allow", "prefer", "require", "verify-ca", "verify-full")
# asyncpg's connect error for a refusal on every address carries only the errno text.
_REFUSED_ERRNO_TEXT = re.compile(r"\[Errno (111|10061)\]")


def connection_kwargs_from_auth(auth_config: dict[str, Any]) -> dict[str, Any]:
    """host, port, database, user, password and sslmode from the auth settings.

    Raises ValueError with a message the user can act on."""
    connection_string = auth_config.get("connectionString")
    sslmode = auth_config.get("sslmode", "prefer")

    if connection_string:
        parsed = urlparse(connection_string)
        if parsed.scheme not in ("postgresql", "postgres"):
            raise ValueError("The connection string must start with postgresql://")
        try:
            port = parsed.port or 5432
        except ValueError as e:
            raise ValueError("The port in the connection string must be a number from 1 to 65535.") from e
        host = parsed.hostname
        database = unquote(parsed.path.lstrip("/")) if parsed.path else ""
        user = unquote(parsed.username) if parsed.username else None
        password = unquote(parsed.password) if parsed.password else ""
        # Dropping this silently would connect without the
        # certificate checks the string asked for.
        sslmode = parse_qs(parsed.query).get("sslmode", [sslmode])[0]
        if not all([host, database, user]):
            raise ValueError(
                "The connection string needs a user, host and database, "
                "like postgresql://user:password@host:5432/database"
            )
    else:
        host = auth_config.get("host")
        database = auth_config.get("database")
        user = auth_config.get("username")
        password = auth_config.get("password", "")
        try:
            port = int(auth_config.get("port", 5432))
        except (TypeError, ValueError) as e:
            raise ValueError("Port must be a number, such as 5432.") from e
        if not all([host, database, user]):
            raise ValueError("Host, database and username are required.")

    if not 1 <= port <= 65535:
        raise ValueError("Port must be a number from 1 to 65535.")
    sslmode = (sslmode or "prefer").strip().lower()
    if sslmode not in SSL_MODES:
        raise ValueError(f"sslmode must be one of {', '.join(SSL_MODES)}.")
    return {
        "host": host,
        "port": port,
        "database": database,
        "user": user,
        "password": password,
        "sslmode": sslmode,
    }


def describe_connection_error(
    error: BaseException, host: str, port: int, database: str, user: str, sslmode: str
) -> str:
    """A message for the setup form saying why a connection attempt failed."""
    firewall_hint = "that the database's firewall allows connections from PipesHub's IP address"
    # Raised before anything is dialled; its message is already written for the user.
    if isinstance(error, HostCheckError):
        return str(error)
    # Subclass of InvalidAuthorizationSpecificationError, so it goes first. The
    # server sends it for an unknown user too, so it can't say which was wrong.
    if isinstance(error, asyncpg.InvalidPasswordError):
        return f'PostgreSQL rejected the login for user "{user}". Check the username and password.'
    if isinstance(error, asyncpg.InvalidAuthorizationSpecificationError):
        # pg_hba.conf refusals land here; their text names the address the server saw.
        return f"PostgreSQL refused the login: {error}"
    if isinstance(error, asyncpg.InvalidCatalogNameError):
        return f'Database "{database}" does not exist on {host}.'
    if isinstance(error, asyncpg.PostgresError):
        return f"PostgreSQL returned an error: {error}"
    if isinstance(error, socket.gaierror):
        return f'Could not find a server named "{host}". Check the host name.'
    if isinstance(error, TimeoutError):
        return f"Timed out connecting to {host}:{port}. Check {firewall_hint}."
    if "rejected SSL upgrade" in str(error):
        # A real server without SSL only refuses the upgrade when SSL is required;
        # under prefer/allow the refusal comes from something that isn't PostgreSQL.
        if sslmode in ("require", "verify-ca", "verify-full"):
            return f"{host}:{port} does not accept SSL connections, but sslmode={sslmode} requires SSL."
        return f"{host}:{port} did not answer like a PostgreSQL server. Check the port."
    if isinstance(error, ssl.SSLError):
        return f"The SSL handshake with {host}:{port} failed: {error}"
    # When every address of a host fails, asyncio raises one OSError without an
    # errno, so a refusal shows up only in its text.
    if isinstance(error, ConnectionRefusedError) or _REFUSED_ERRNO_TEXT.search(str(error)):
        return f"{host}:{port} refused the connection. Check the host and port, and {firewall_hint}."
    if isinstance(error, OSError):
        # No error text: asyncio's names the addresses the host resolved to.
        return f"Could not reach {host}:{port}. Check the host and port, and {firewall_hint}."
    return f"Could not connect to {host}:{port}."


def _is_ssl_only_server_refusal(error: BaseException, sslmode: str) -> bool:
    # Under prefer, asyncpg retries a refused SSL login without SSL. A server that
    # only takes SSL then answers with its no-encryption rule, which hides why the
    # SSL login was refused (usually a wrong password).
    return (
        sslmode == "prefer"
        and isinstance(error, asyncpg.InvalidAuthorizationSpecificationError)
        and not isinstance(error, asyncpg.InvalidPasswordError)
        # "SSL off" is how servers before PostgreSQL 12 word it.
        and ("no encryption" in str(error) or "SSL off" in str(error))
    )


def table_fqn(schema_name: str, table_name: str) -> str:
    """A table's id, ``schema.table``; see ``join_qualified_name`` for quoting."""
    return join_qualified_name((schema_name, table_name))


def split_table_fqn(fqn: str) -> tuple[str, str]:
    """Inverse of ``table_fqn``. Raises ValueError for anything it didn't write."""
    try:
        schema_name, table_name = split_qualified_name(fqn, 2)
    except ValueError:
        raise ValueError(f"Not a table id: {fqn!r}") from None
    return schema_name, table_name


@dataclass
class PostgresSchema:
    name: str
    owner: str | None = None


def _table_revision(state: PostgresTableState) -> str:
    """The table record's revision, written as structure:content.

    The first part alone says whether the table's name, columns or keys
    changed, which is what decides if what was derived from it is out of date.
    """
    return f"{state.structure()}:{state.revision()}"


def _table_ids(batch: list[tuple[Record, list[Permission]]]) -> list[str]:
    """The table ids in a batch, which also holds the tables' column records."""
    return [r.external_record_id for r, _ in batch if r.record_type == RecordType.SQL_TABLE]


def _stored_structure(revision: str | None) -> str | None:
    """The structure part of a saved revision; None for one saved before it had two parts."""
    if not revision or ":" not in revision:
        return None
    return revision.split(":", 1)[0]


@dataclass
class PostgresTable:
    name: str
    schema_name: str
    row_count: int | None = None
    db_row_count: int | None = None
    owner: str | None = None
    columns: list[ColumnInfo] = None
    foreign_keys: list[ForeignKeyInfo] = None
    primary_keys: list[str] = None
    state: PostgresTableState | None = None
    column_stats: tuple[int, list[dict[str, Any]]] | None = None
    column_profiles: dict[str, ColumnProfile] | None = None
    # Profiling raised: the stored column stats and profiles are kept rather
    # than overwritten with empty values.
    profiling_failed: bool = False

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


def _column_hash(columns: list[ColumnInfo]) -> str:
    """MD5 of a table's column definitions, for spotting schema changes."""
    column_str = json.dumps([col.model_dump() for col in columns], sort_keys=True, default=str)
    return hashlib.md5(column_str.encode()).hexdigest()


def _table_shape(columns: list[tuple[int, dict]], constraint_shape: str) -> str:
    """MD5 of a table's columns, by attnum, and keys, with no name in either."""
    key = json.dumps([sorted(columns, key=lambda column: column[0]), constraint_shape], sort_keys=True, default=str)
    return hashlib.md5(key.encode()).hexdigest()


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


def _decode_json_columns(rows: list[dict[str, Any]], columns: list[ColumnInfo]) -> list[dict[str, Any]]:
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
    
    def to_dict(self) -> dict[str, int]:
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
    .with_scopes([ConnectorScope.PERSONAL.value, ConnectorScope.TEAM.value])\
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
            name=SyncFilterKey.SCHEMAS.value,
            display_name="Schemas",
            filter_type=FilterType.MULTISELECT,
            category=FilterCategory.SYNC,
            description="Select specific schemas to sync",
            option_source_type=OptionSourceType.DYNAMIC,
            default_operator=MultiselectOperator.IN.value
        ))
        .add_filter_field(FilterField(
            name=SyncFilterKey.TABLES.value,
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
        created_by: str | None = None,
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
        self.data_source: PostgreSQLDataSource | None = None
        self.database_name: str | None = None
        # host:port and pg_database.oid: with the name, which database a saved
        # sync state describes. Read again after each connect.
        self.server_address: str | None = None
        self.database_oid: int | None = None
        self.batch_size = 100
        self.rate_limiter = AsyncLimiter(25, 1)
        self.connector_scope: str | None = scope
        self.sync_filters: FilterCollection = FilterCollection()
        self.indexing_filters: FilterCollection = FilterCollection()
        self.sync_stats: SyncStats = SyncStats()
        self.sync_outcome: SyncOutcome = SyncOutcome()
        # Set during a full sync: see _build_table_record.
        self._keep_legacy_revisions = False
        # Tables an incremental sync found with other columns or keys, or with
        # only other names; see _is_restructured.
        self._structure_changed: set[str] = set()
        self._names_changed: set[str] = set()
        # The state saved as an incremental sync moves records; see run_incremental_sync.
        self._checkpoint: dict[str, PostgresTableState] | None = None
        self._frontend_url: str = os.getenv("FRONTEND_PUBLIC_URL", "").rstrip("/")

        self._schema_filter_cache: list[FilterOption] = []
        self._table_filter_cache:  list[FilterOption] = []
        self._filter_cache_rebuild_event: asyncio.Event | None = None

        # Initialize sync point for incremental sync
        org_id = self.data_entities_processor.org_id
        self.tables_sync_point = SyncPoint(
            connector_id=self.connector_id,
            org_id=org_id,
            sync_data_point_type=SyncDataPointType.RECORDS,
            data_store_provider=data_store_provider
        )

    def get_app_users(self, users: list[User]) -> list[AppUser]:
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

            try:
                connection_kwargs = connection_kwargs_from_auth(config.get("auth") or {})
            except ValueError as e:
                self.logger.error(f"Invalid PostgreSQL configuration: {e}")
                return False

            self.database_name = connection_kwargs["database"]
            self.server_address = f"{(connection_kwargs.get('host') or '').lower()}:{connection_kwargs.get('port')}"
            self.database_oid = None
            self.scope = config.get("scope", self.scope or ConnectorScope.TEAM.value)
            self.connector_scope = self.scope
            self.created_by = config.get("created_by", self.created_by)

            pg_config_kwargs: dict[str, Any] = {
                **connection_kwargs,
                "timeout": int(config.get("timeout", 30)),
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
            try:
                await client.connect()
            except ConnectionError as e:
                if isinstance(e.__cause__, HostCheckError):
                    raise ConnectorInitError(str(e.__cause__)) from e
                raise

            self.data_source = PostgreSQLDataSource(client)


            self.logger.info("PostgreSQL connector initialized successfully")
            return True

        except ConnectorInitError:
            raise
        except Exception as e:
            self.logger.error(f"Failed to initialize PostgreSQL connector: {e}", exc_info=True)
            return False

    async def run_sync(self) -> None:
        try:
            self.logger.info("📦 [Sync] Starting PostgreSQL sync...")
            # Before anything that can raise: a failed sync must not report the
            # previous one's tables again.
            self.sync_stats = SyncStats()
            self.sync_outcome = SyncOutcome()
            self._structure_changed = set()
            self._names_changed = set()
            self._checkpoint = None

            if not self.data_source:
                raise ConnectionError("PostgreSQL connector not initialized")

            self.sync_filters, self.indexing_filters = await load_connector_filters(
                self.config_service, "postgresql", self.connector_id, self.logger
            )

            stored_state = await self.tables_sync_point.read_sync_point(SYNC_STATE_KEY)
            await self._load_database_oid()

            try:
                if self._has_usable_state(stored_state):
                    self.logger.info("📦 [Sync] Found existing sync state, running incremental sync...")
                    await self.run_incremental_sync()
                else:
                    self.logger.info("📦 [Sync] No usable sync state, running full sync...")
                    await self._run_full_sync_internal()
            finally:
                # Also after a failure: the tables saved before it still changed.
                await self._resolve_restructured()
                await self._announce_renames()

            self.sync_stats.log_summary(self.logger)
            self.logger.info(
                f"📦 [Sync] {len(self.sync_outcome.restructured)} table(s) new or restructured, "
                f"{len(self.sync_outcome.removed)} record(s) removed"
            )

        except Exception as e:
            self.logger.error(f"❌ [Sync] Error: {e}", exc_info=True)
            raise

    async def _resolve_restructured(self) -> None:
        """Turn the restructured tables' ids into the ids of the records saved for them.

        The state is saved by now, so a table left out here would not come up
        as changed again: a failed lookup is tried again before it is given up.
        """
        for fqn in sorted(self.sync_outcome.restructured_tables):
            for attempt in range(1, SYNC_STATE_SAVE_ATTEMPTS + 1):
                try:
                    record = await self.data_entities_processor.get_record_by_external_id(
                        connector_id=self.connector_id, external_record_id=fqn, raise_on_error=True,
                    )
                    break
                except Exception as e:
                    if attempt == SYNC_STATE_SAVE_ATTEMPTS:
                        self.sync_stats.errors += 1
                        self.logger.error(
                            f"Could not look up the record of restructured table {fqn}; "
                            f"it is not reported as restructured until it changes: {e}"
                        )
                        record = None
                    else:
                        await asyncio.sleep(attempt)
            if record and record.id:
                self.sync_outcome.restructured.add(record.id)

    async def _announce_renames(self) -> None:
        """Tell the processor which tables were renamed, or had columns renamed.

        When that fails, they are reported as restructured instead: a rename
        alone would never bring them up again.
        """
        renamed = sorted(self.sync_outcome.renamed - self.sync_outcome.removed)
        if not renamed:
            return
        try:
            await self.data_entities_processor.on_sql_records_renamed(renamed, self.connector_id)
        except Exception as e:
            self.logger.warning(
                f"Could not report {len(renamed)} renamed table(s); "
                f"reporting them as restructured instead: {e}"
            )
            self.sync_outcome.restructured.update(renamed)

    async def _load_database_oid(self) -> None:
        if self.database_oid is None:
            self.database_oid = await self.data_source.get_database_oid()

    def _has_usable_state(self, stored_state: dict[str, Any] | None) -> bool:
        # Saved for another database: the connector was pointed elsewhere, the
        # same name is on another server, or the database was dropped and made
        # again. Nothing in it describes this one, and its table oids would
        # match unrelated tables here.
        return bool(
            stored_state
            and stored_state.get("table_states")
            and stored_state.get("state_version") == SYNC_STATE_VERSION
            and stored_state.get("database") == self.database_name
            and stored_state.get("server") == self.server_address
            and stored_state.get("database_oid") == self.database_oid
        )

    @staticmethod
    def _passes_filter(value: str, selected: list[str] | None, operator: str) -> bool:
        if not selected:
            return True
        if operator == MultiselectOperator.NOT_IN.value:
            return value not in selected
        return value in selected

    def _get_filter_values(
        self,
    ) -> tuple[list[str] | None, str, list[str] | None, str]:
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
        self._keep_legacy_revisions = True
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
            self._structure_changed |= await self._columns_changed_since_older_state(snapshot)

            schemas = await self._fetch_schemas()

            schemas = [
                s for s in schemas if self._passes_filter(s.name, selected_schemas, schemas_op)
            ]

            await self._sync_schemas(schemas)
            self.sync_stats.schemas_synced = len(schemas)
            if any(s.name == self.database_name for s in schemas):
                await self._remove_database_group_self_links()

            listed: set[str] = set()
            synced: set[str] = set()
            for schema in schemas:
                table_names = [
                    n for n in await self._list_table_names(schema.name)
                    if self._passes_filter(table_fqn(schema.name, n), selected_tables, tables_op)
                ]

                listed.update(table_fqn(schema.name, n) for n in table_names)
                tables = await self._load_tables(schema.name, table_names, snapshot)
                synced |= await self._sync_tables(schema.name, tables)
                await self._remove_dropped_columns(schema.name, tables)

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
        finally:
            self._keep_legacy_revisions = False

    async def _columns_changed_since_older_state(
        self, snapshot: dict[str, PostgresTableState],
    ) -> set[str]:
        """Tables whose columns differ from a state saved by an earlier version.

        Earlier versions synced once and saved that state: everything changed in
        the database since then is found by the first sync after the upgrade,
        which is a full one. Their records carry revisions that cannot say
        whether the columns moved, so the old state is asked instead. It kept no
        keys, so a key changed since then is found only when it changes again.
        """
        try:
            stored = await self.tables_sync_point.read_sync_point(SYNC_STATE_KEY)
            if not stored or stored.get("state_version") == SYNC_STATE_VERSION or not stored.get("table_states"):
                return set()
            older = json.loads(stored["table_states"])
        except Exception as e:
            self.logger.warning(f"Could not read the sync state saved by an earlier version: {e}")
            return set()
        return {
            fqn for fqn, state in snapshot.items()
            if isinstance(older.get(fqn), dict)
            and older[fqn].get("column_hash")
            and older[fqn]["column_hash"] != state.column_hash
        }

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

    async def _fetch_schemas(self) -> list[PostgresSchema]:
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

    async def _list_table_names(self, schema: str) -> list[str]:
        response = await self.data_source.list_tables(schema=schema)
        if not response.success:
            raise RuntimeError(f"Failed to list tables in {schema}: {response.error}")
        return [TableListEntry.model_validate(item).name for item in response.data]

    async def _load_table(
        self,
        schema_name: str,
        table_name: str,
        state: PostgresTableState | None = None,
    ) -> PostgresTable:
        """Read a table's columns and keys, and profile its columns.

        Raises if the columns or keys can't be read: syncing the table without
        them would index it as though it had none. Profiling never raises.
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

        table = PostgresTable(
            name=table_name,
            schema_name=schema_name,
            row_count=state.n_live_tup if state else None,
            columns=TableDetail.model_validate(info.data).columns,
            foreign_keys=[ForeignKeyInfo.model_validate(fk) for fk in (fks.data or [])],
            primary_keys=[PrimaryKeyInfo.model_validate(pk).column_name for pk in (pks.data or [])],
            state=state,
        )
        await self._profile_table(table)
        return table

    async def _load_tables(
        self,
        schema_name: str,
        table_names: list[str],
        states: dict[str, PostgresTableState],
    ) -> list[PostgresTable]:
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

    async def _profile_table(self, table: PostgresTable) -> None:
        """Attach column stats and value profiles; the table syncs without them on failure."""
        if not self.data_source:
            return
        try:
            statistics = await self.data_source.fetch_column_statistics(table.schema_name, table.name)
            estimated_rows = self._estimated_rows(table, statistics)
            if (
                estimated_rows is not None
                and estimated_rows * len(table.columns) <= EXACT_COLUMN_STATS_MAX_CELLS
            ):
                await self._attach_column_stats(table)
            # Stored values and extremes are row data; keep them out of the
            # graph when the admin turned table indexing off.
            if statistics is None or (
                self.indexing_filters
                and not self.indexing_filters.is_enabled(IndexingFilterKey.TABLES.value)
            ):
                return
            exact_stats = None
            if table.column_stats:
                exact_stats = {s["name"]: s for s in table.column_stats[1] if s.get("name")}
            table.column_profiles = await self.data_source.profile_columns(
                table.schema_name,
                table.name,
                table.columns,
                statistics,
                row_count=table.db_row_count if table.db_row_count is not None else estimated_rows,
                exact_stats=exact_stats,
            )
        except Exception as e:
            table.profiling_failed = True
            self.logger.warning(f"Could not profile columns of {table.fqn}: {e}")

    @staticmethod
    def _estimated_rows(
        table: PostgresTable,
        statistics: TableColumnStatistics | None,
    ) -> int | None:
        """The larger row estimate, or None when neither is above zero.

        Zero is not taken as empty: n_live_tup reads 0 after a stats reset and
        reltuples -1 before the first ANALYZE, whatever the table holds. A new
        table nobody has analyzed still counts its inserts in n_live_tup, so
        only one whose counters were reset goes unprofiled until analyzed.
        """
        estimates = [table.row_count, statistics.reltuples if statistics else None]
        positive = [int(n) for n in estimates if n is not None and n > 0]
        return max(positive) if positive else None

    async def _attach_column_stats(self, table: PostgresTable) -> None:
        """One heap scan for row count and per-column distinct/null stats."""
        if not self.data_source:
            return
        row_count, column_stats = await self.data_source.fetch_table_row_and_column_stats(
            table.schema_name,
            table.name,
            countable_column_names(table.columns or []),
        )
        if row_count is not None:
            table.db_row_count = row_count
            if row_count > 0:
                table.row_count = row_count
        if column_stats is not None:
            table.column_stats = column_stats

    def _indexed_row_count(self, db_row_count: int | None) -> int | None:
        if db_row_count is None:
            return None
        max_rows = min(
            int(self.indexing_filters.get_value(IndexingFilterKey.MAX_ROWS_PER_TABLE, default=1000)),
            MAX_ROWS_PER_TABLE_LIMIT,
        )
        return min(db_row_count, max_rows)

    async def _get_permissions(self) -> list[Permission]:
        return await database_permissions(self)

    async def _build_table_record(
        self,
        schema_name: str,
        table: PostgresTable,
    ) -> tuple[SQLTableRecord, bool]:
        """Build the record for a table; the flag says whether one is already stored."""
        fqn = table_fqn(schema_name, table.name)
        existing = await self.data_entities_processor.get_record_by_external_id(
            connector_id=self.connector_id,
            external_record_id=fqn,
        )

        current_time = get_epoch_timestamp_in_ms()
        revision = _table_revision(table.state) if table.state else str(current_time)
        if (
            self._keep_legacy_revisions
            and existing is not None
            and table.state is not None
            and existing.external_revision_id == table.state.legacy_revision()
        ):
            # Saved by an earlier version, whose revisions were written another
            # way, and the rows are as they were then. A new revision would
            # re-index every table of every connector on the first sync after
            # an upgrade; the next change to the table writes one.
            revision = existing.external_revision_id
        record_id = existing.id if existing else str(uuid.uuid4())
        changed = existing is None or existing.external_revision_id != revision
        if self._is_restructured(
            existing, table.state,
            structure_changed=fqn in self._structure_changed, names_only=fqn in self._names_changed,
        ):
            self.sync_outcome.restructured_tables.add(fqn)

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
            database_name=self.database_name,
            schema_name=schema_name,
            row_count=table.row_count,
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

    @staticmethod
    def _is_restructured(
        existing: Record | None,
        state: PostgresTableState | None,
        *,
        structure_changed: bool = False,
        names_only: bool = False,
    ) -> bool:
        """Whether what is known about the table no longer describes it.

        ``structure_changed`` is what a comparison with the saved sync state
        says, and it counts whatever the record says: the record may have been
        written by a run that failed before handing the table on. A record saved
        before revisions carried the structure cannot be compared by its
        revision, so an upgrade does not report every table as changed.
        ``names_only`` is that comparison finding only names changed, which the
        record's structure, written with the names, cannot tell apart.
        """
        if existing is None or getattr(existing, "is_placeholder", False) or structure_changed:
            return True
        if names_only:
            return False
        stored = _stored_structure(existing.external_revision_id)
        return state is not None and stored is not None and stored != state.structure()

    def _column_fqn(self, table_fqn: str, column_name: str) -> str:
        return f"{self.database_name}.{table_fqn}.{column_name}"

    def _build_column_records(
        self,
        table_record: SQLTableRecord,
        table: PostgresTable,
        stored: dict[str, Record] | None = None,
    ) -> list[SQLColumnRecord]:
        """Column records for ``table``.

        ``stored`` maps column FQN to the saved record; when this sync's
        profiling failed, the saved stats and profile are kept.
        """
        stats_by_name: dict[str, dict[str, Any]] = {}
        if table.column_stats:
            _total_rows, stats_list = table.column_stats
            stats_by_name = {str(s.get("name")): s for s in stats_list if s.get("name")}
        return build_column_records(
            table_record,
            table.columns,
            column_fqn=self._column_fqn,
            stats_by_name=stats_by_name,
            profiles=table.column_profiles,
            stored=stored,
            keep_stored=table.profiling_failed,
            values_allowed=not self.indexing_filters or self.indexing_filters.is_enabled(IndexingFilterKey.TABLES.value),
        )

    async def _typed_columns(self, stored: dict[str, Record]) -> dict[str, Record]:
        """Stored column records with their stats and profiles, to keep when profiling failed.

        A parent lookup gives base records, which carry neither. Raises when
        they cannot be loaded: the table's sync fails and is tried again,
        rather than writing its columns back without their profiles.
        """
        return await load_typed_columns(self.data_entities_processor, stored)

    async def _stored_columns(self, table_fqn: str) -> dict[str, Record]:
        async with self.data_store_provider.transaction() as tx_store:
            existing = await tx_store.get_records_by_parent(
                self.connector_id, table_fqn, RecordType.SQL_COLUMN.value,
            )
        return {child.external_record_id: child for child in existing}

    async def _remove_records(self, record_ids: list[str], *, table_record_id: str | None = None) -> None:
        """Delete table or column records with everything built from them.

        A table's columns go with it. ``table_record_id`` is the table the
        columns in ``record_ids`` belong to, when only columns are removed.
        """
        await self._note_tables_to_relate_again(record_ids, table_record_id=table_record_id)
        removed = await self.data_entities_processor.on_sql_records_deleted(
            record_ids, self.connector_id, table_record_id=table_record_id,
        )
        self.sync_outcome.removed.update(removed)

    async def _note_tables_to_relate_again(self, record_ids: list[str], *, table_record_id: str | None) -> None:
        """Note the tables whose relations depended on these records, before they go.

        Nothing else about those tables changed, so no other sync would look
        at them again. Best effort.
        """
        try:
            sources = await self.data_entities_processor.sql_tables_to_relate_again(
                [table_record_id] if table_record_id else record_ids,
                column_ids=record_ids if table_record_id else None,
            )
        except Exception as e:
            self.logger.warning(f"Could not find the tables related through {len(record_ids)} removed record(s): {e}")
            return
        self.sync_outcome.relations_only.update(sources)

    async def _remove_dropped_columns(self, schema_name: str, tables: list[PostgresTable]) -> None:
        """Delete the records of columns these tables no longer have.

        For a full sync, which saves every table's current columns without
        looking at the stored ones the way a changed table's sync does.
        """
        for table in tables:
            fqn = table_fqn(schema_name, table.name)
            try:
                current = {
                    self._column_fqn(fqn, (col.name or "").strip()) for col in table.columns or []
                }
                stored = await self._stored_columns(fqn)
                dropped = [
                    record.id for external_id, record in stored.items()
                    if external_id not in current and record.id
                ]
                if not dropped:
                    continue
                table_record = await self.data_entities_processor.get_record_by_external_id(
                    connector_id=self.connector_id, external_record_id=fqn,
                )
                await self._remove_records(
                    dropped, table_record_id=table_record.id if table_record else None,
                )
            except Exception as e:
                self.sync_stats.errors += 1
                self.logger.warning(f"Failed to remove dropped columns of {fqn}: {e}")

    async def _rename_tables(self, renamed: dict[str, str], *, blocked: set[str] = frozenset()) -> set[str]:
        """Move the records of renamed tables to their new names; returns the new names not moved.

        Only their ids change here; the sync of each table that follows rewrites
        the rest. Keeping the records keeps what hangs off them: the notes
        people wrote and anything else linked to the table.

        One write moves them all, so tables that swapped names, or a chain of
        renames, move together. A table waits when the name it moves into is
        not free: a ``blocked`` name (a dropped table there could not be
        removed), or the name of a table that could not move out of it.
        """
        moves: dict[str, tuple[Record, list[Record]]] = {}
        unmoved: set[str] = set()
        for old_fqn, new_fqn in sorted(renamed.items()):
            try:
                async with self.data_store_provider.transaction() as tx_store:
                    table = await tx_store.get_record_by_external_id(
                        self.connector_id, old_fqn, raise_on_error=True,
                    )
                    if table is None:
                        # Never stored, or moved by a run that failed after this step.
                        continue
                    columns = await tx_store.get_records_by_parent(
                        self.connector_id, old_fqn, RecordType.SQL_COLUMN.value,
                    )
                moves[old_fqn] = (table, columns)
            except Exception as e:
                self.sync_stats.errors += 1
                unmoved.add(new_fqn)
                self.logger.error(f"Error renaming table {old_fqn} to {new_fqn}: {e}", exc_info=True)

        hold_renames(renamed, moves, unmoved, blocked)
        for old_fqn in sorted(moves):
            new_fqn = renamed[old_fqn]
            if new_fqn in moves:
                continue  # left by a table that moves too
            try:
                await self._remove_occupant(new_fqn, moves[old_fqn][0])
            except Exception as e:
                self.sync_stats.errors += 1
                unmoved.add(new_fqn)
                moves.pop(old_fqn)
                self.logger.error(f"Error freeing {new_fqn} for table {old_fqn}: {e}", exc_info=True)
        hold_renames(renamed, moves, unmoved, blocked)

        updates = rename_updates(moves, renamed, self._column_fqn)
        if updates:
            try:
                async with self.data_store_provider.transaction() as tx_store:
                    await tx_store.batch_update_nodes(updates, CollectionNames.RECORDS.value)
            except Exception as e:
                self.sync_stats.errors += 1
                self.logger.error(f"Error renaming {len(moves)} table(s): {e}", exc_info=True)
                return unmoved | {renamed[old_fqn] for old_fqn in moves}
        for old_fqn in sorted(moves):
            self.logger.info(f"Table {old_fqn} is now {renamed[old_fqn]}; kept its record")
        return unmoved

    @staticmethod
    def _hold_renames(
        renamed: dict[str, str],
        moves: dict[str, tuple[Record, list[Record]]],
        unmoved: set[str],
        blocked: set[str],
    ) -> None:
        """Hold back every rename into a name that will not be free."""
        hold_renames(renamed, moves, unmoved, blocked)

    async def _remove_occupant(self, fqn: str, table: Record) -> None:
        """Remove what is stored under a name a renamed table moves into.

        A table that references the renamed one and was synced first leaves a
        stand-in there; the tables that reference it keep their foreign keys,
        moved to the real record, since they are not synced again until they
        change. Anything else there belongs to a table that is gone.
        """
        async with self.data_store_provider.transaction() as tx_store:
            occupant = await tx_store.get_record_by_external_id(self.connector_id, fqn, raise_on_error=True)
        if occupant is None or occupant.id == table.id:
            return
        await self.data_entities_processor.move_incoming_relations(occupant.id, table.id)
        await self._remove_records([occupant.id])

    async def _on_table_renamed(self, table_record_id: str) -> None:
        """Tell the processor a table, or one of its columns, was renamed."""
        try:
            await self.data_entities_processor.on_sql_table_renamed(table_record_id)
        except Exception as e:
            self.sync_stats.errors += 1
            self.logger.warning(f"Could not handle the rename of table record {table_record_id}: {e}")

    async def _rename_column_records(
        self,
        table_fqn: str,
        renames: dict[str, str],
        *,
        table_record_id: str | None = None,
    ) -> None:
        """Move stored column records to their columns' new names.

        One write moves them all, so columns that swapped names (``id`` became
        ``legacy_id`` and ``uuid`` became ``id``) keep their records. A record
        under a name a renamed column takes, whose own column was not renamed
        away, belonged to a column that was dropped: it goes first.
        """
        if not renames:
            return
        stored = await self._stored_columns(table_fqn)
        displaced: list[str] = []
        for old_name, new_name in renames.items():
            occupant = stored.get(self._column_fqn(table_fqn, new_name))
            # Moved there already when the old name holds no record: by a run
            # that failed after this step.
            if (
                new_name not in renames
                and self._column_fqn(table_fqn, old_name) in stored
                and occupant is not None
                and occupant.id
            ):
                displaced.append(occupant.id)
        if displaced:
            await self._remove_records(displaced, table_record_id=table_record_id)
        updates = [
            {"id": column.id, "externalRecordId": self._column_fqn(table_fqn, new_name)}
            for old_name, new_name in renames.items()
            if (column := stored.get(self._column_fqn(table_fqn, old_name))) is not None and column.id
        ]
        if updates:
            async with self.data_store_provider.transaction() as tx_store:
                await tx_store.batch_update_nodes(updates, CollectionNames.RECORDS.value)
            await self._checkpoint_column_renames(table_fqn, renames)

    async def _sync_table_columns(
        self,
        table_record: SQLTableRecord,
        table: PostgresTable,
        column_renames: dict[str, str] | None = None,
    ) -> None:
        table_fqn = table_record.external_record_id
        desired_fqns = {
            self._column_fqn(table_fqn, (col.name or "").strip())
            for col in (table.columns or [])
            if (col.name or "").strip()
        }

        await self._rename_column_records(table_fqn, column_renames or {}, table_record_id=table_record.id)

        async with self.data_store_provider.transaction() as tx_store:
            existing = await tx_store.get_records_by_parent(
                self.connector_id,
                table_fqn,
                RecordType.SQL_COLUMN.value,
            )
        dropped = [
            child.id for child in existing
            if child.external_record_id not in desired_fqns and child.id
        ]
        if dropped:
            await self._remove_records(dropped, table_record_id=table_record.id)

        stored = {child.external_record_id: child for child in existing}
        if table.profiling_failed:
            stored = await self._typed_columns(stored)
        col_records = self._build_column_records(table_record, table, stored)
        if col_records:
            await self.data_entities_processor.on_new_records(
                [(record, []) for record in col_records]
            )

    async def _process_tables_generator(
        self,
        schema_name: str,
        tables: list[PostgresTable],
    ) -> AsyncGenerator[tuple[Record, list[Permission]], None]:

        for table in tables:
            try:
                record, _ = await self._build_table_record(schema_name, table)
                yield (record, [])
                stored = (
                    await self._typed_columns(await self._stored_columns(record.external_record_id))
                    if table.profiling_failed else None
                )
                for col_record in self._build_column_records(record, table, stored):
                    yield (col_record, [])
                await asyncio.sleep(0)
            except Exception as e:
                self.sync_stats.errors += 1
                self.logger.error(f"Error processing table {table.name}: {e}", exc_info=True)
                continue

    async def _sync_schemas(self, schemas: list[PostgresSchema]) -> None:
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

    async def _sync_tables(self, schema_name: str, tables: list[PostgresTable]) -> set[str]:
        """Save records for these tables; returns the ones that were saved."""
        synced: set[str] = set()
        if not tables:
            return synced

        batch: list[tuple[Record, list[Permission]]] = []

        async for record, perms in self._process_tables_generator(schema_name, tables):
            batch.append((record, perms))

            if len(batch) >= self.batch_size:
                self.logger.debug(f"Processing batch of {len(batch)} tables")
                await self.data_entities_processor.on_new_records(batch)
                synced.update(_table_ids(batch))
                batch = []

        if batch:
            await self.data_entities_processor.on_new_records(batch)
            synced.update(_table_ids(batch))

        self.logger.info(f"Synced {len(synced)} tables in {schema_name}")
        return synced

    async def _sync_updated_tables(
        self,
        schema_name: str,
        tables: list[PostgresTable],
        changes: dict[str, TableChange] | None = None,
    ) -> set[str]:
        """Re-sync tables whose content or columns changed; returns the ones saved."""
        synced: set[str] = set()
        if not tables:
            return synced

        self.logger.info(f"Processing {len(tables)} updated tables in {schema_name}")

        for table in tables:
            fqn = table_fqn(schema_name, table.name)
            change = (changes or {}).get(fqn)
            try:
                record, existed = await self._build_table_record(schema_name, table)
                if existed:
                    await self.data_entities_processor.on_record_content_update(record)
                else:
                    self.logger.warning(f"No record for changed table {fqn}; creating it")
                    await self.data_entities_processor.on_new_records([(record, [])])
                await self._sync_table_columns(
                    record, table, change.column_renames if change else None,
                )
                if change and (change.previous_fqn or change.column_renames or change.rename_pending):
                    await self._on_table_renamed(record.id)
                    self.sync_outcome.renamed.add(record.id)
                    if change.names_only:
                        # Its pairs are ranked by name, and nothing else changed.
                        self.sync_outcome.relations_only.add(record.id)
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
        user_id: str | None = None,
        convertTo: str | None = None
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
                columns: list[ColumnInfo] = detail.columns
                primary_keys = list(detail.primary_keys or [])
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

            if record.record_type == RecordType.SQL_COLUMN:
                return await self._stream_column(record)

            raise HTTPException(status_code=400, detail="Unsupported record type")

        except Exception as e:
            self.logger.error(f"Error streaming record: {e}", exc_info=True)
            raise

    async def _stream_column(self, record: Record) -> StreamingResponse:
        """A column's definition, its stored profile, and its live value counts."""
        schema, table = self._split_table_fqn(
            record, fqn=getattr(record, "table_fqn", None) or record.parent_external_record_id,
        )
        column = record.record_name
        # The counts group the whole table, past the rows a table stream is
        # capped to, so a column that looks personal gets none, as it gets no
        # top values in its profile.
        withheld = looks_personal(column)
        value_counts: list[dict[str, Any]] = []
        if not withheld:
            try:
                # One past the limit, to tell a cut-off list from one that fits exactly.
                value_counts = await self.data_source.fetch_column_value_counts(
                    schema, table, column, limit=MAX_COLUMN_VALUES_STREAMED + 1,
                )
            except Exception as e:
                self.logger.error(f"❌ Failed to read values of {schema}.{table}.{column}: {e}")
                raise to_sql_stream_error(e, connector=self.display_name) from e

        top_values = getattr(record, "top_values", None) or []
        frequencies = getattr(record, "top_value_frequencies", None) or []
        data = {
            "column_name": column,
            "table_name": table,
            "schema_name": schema,
            "database_name": self.database_name,
            "data_type": getattr(record, "data_type", None),
            "nullable": getattr(record, "nullable", None),
            "is_primary_key": getattr(record, "is_primary_key", None),
            "is_unique": getattr(record, "is_unique", None),
            "default": getattr(record, "default", None),
            "ordinal_position": getattr(record, "ordinal_position", None),
            "profile": {
                "distinct_count": getattr(record, "distinct_count", None),
                "null_count": getattr(record, "null_count", None),
                "cardinality_ratio": getattr(record, "cardinality_ratio", None),
                "min_value": getattr(record, "min_value", None),
                "max_value": getattr(record, "max_value", None),
                "numeric_text": getattr(record, "numeric_text", None),
                "profiled_at": getattr(record, "profiled_at", None),
                "top_values": [
                    {"value": value, "frequency": frequency}
                    for value, frequency in zip(top_values, frequencies)
                ],
            },
            "value_counts": [
                {"value": row["value"], "count": row["count"]}
                for row in value_counts[:MAX_COLUMN_VALUES_STREAMED]
            ],
            "value_counts_truncated": len(value_counts) > MAX_COLUMN_VALUES_STREAMED,
            "value_counts_withheld": withheld,
            "connector_name": self.connector_name.value if hasattr(self.connector_name, "value") else str(self.connector_name),
        }
        json_bytes = json.dumps(data, default=_json_default).encode("utf-8")

        async def json_iterator() -> AsyncGenerator[bytes, None]:
            yield json_bytes

        return create_stream_record_response(
            json_iterator(), filename=f"{table}.{column}.json", mime_type=MimeTypes.SQL_COLUMN.value
        )

    def _split_table_fqn(self, record: Record, fqn: str | None = None) -> tuple[str, str]:
        """Schema and table of a table record, or of the table ``fqn`` names."""
        fqn = (fqn if fqn is not None else record.external_record_id) or ""
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

    @staticmethod
    async def _try_connection(
        kwargs: dict[str, Any],
    ) -> tuple[PostgreSQLResponse | None, BaseException | None]:
        """The test query's response, or the driver error that stopped the connection."""
        client = PostgreSQLConfig(
            **kwargs, timeout=CONNECTION_CHECK_TIMEOUT_S, max_pool_size=1
        ).create_client()
        try:
            await client.connect()
            return await PostgreSQLDataSource(client).test_connection(), None
        except ConnectionError as e:
            return None, e.__cause__ or e
        finally:
            await client.close()

    @classmethod
    async def check_connection(
        cls, auth_config: dict[str, Any], logger: Logger
    ) -> ConnectionCheckResult:
        try:
            kwargs = connection_kwargs_from_auth(auth_config)
        except ValueError as e:
            return ConnectionCheckResult(success=False, message=str(e))

        host, port, database, user = kwargs["host"], kwargs["port"], kwargs["database"], kwargs["user"]
        response, cause = await cls._try_connection(kwargs)
        if cause is not None and _is_ssl_only_server_refusal(cause, kwargs["sslmode"]):
            ssl_response, ssl_cause = await cls._try_connection({**kwargs, "sslmode": "require"})
            # A server without SSL fails the retry before the login, and then the
            # first answer is the real one.
            if ssl_cause is None or isinstance(ssl_cause, asyncpg.PostgresError):
                response, cause = ssl_response, ssl_cause
        if cause is not None:
            logger.info("PostgreSQL connection check to %s:%s failed: %r", host, port, cause)
            return ConnectionCheckResult(
                success=False,
                message=describe_connection_error(cause, host, port, database, user, kwargs["sslmode"]),
            )

        if not response.success:
            return ConnectionCheckResult(
                success=False,
                message=f"Connected, but a test query failed: {response.error}",
            )
        return ConnectionCheckResult(
            success=True, message=f'Connected to database "{database}" as "{user}".'
        )

    async def cleanup(self) -> None:
        try:
            self.logger.info("Starting PostgreSQL connector cleanup...")

            if self.data_source:
                client = self.data_source.get_client()
                if client:
                    await client.close()
                self.data_source = None

            self.database_name = None
            self.server_address = None
            self.database_oid = None

            self.logger.info("PostgreSQL connector cleanup completed")

        except Exception as e:
            self.logger.error(f"Error during PostgreSQL connector cleanup: {e}", exc_info=True)

    def get_signed_url(self, record: Record) -> str | None:
        """
        Get a signed URL for a record.
        
        PostgreSQL doesn't support signed URLs for direct file access.
        
        Returns:
            None - not supported for PostgreSQL
        """
        return None

    def handle_webhook_notification(self, notification: dict) -> None:
        """
        Handle webhook notifications from PostgreSQL.

        PostgreSQL does not support webhooks for data change notifications.
        This method raises NotImplementedError as per the base class contract.
        """
        raise NotImplementedError(
            "PostgreSQL does not support webhook notifications. "
            "Use scheduled sync or PostgreSQL logical replication for change tracking."
        )

    async def reindex_records(self, records: list[Record]) -> None:
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
        Sync what changed in the database since the last run.

        The catalog is read again and compared with the state saved last time
        (see ``catalog_diff``): tables that are new, renamed or moved, changed
        in structure or in rows, only re-analyzed, or gone.

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
            await self._load_database_oid()

            if not self._has_usable_state(stored_state):
                self.logger.info("No usable sync state, running full sync")
                await self._run_full_sync_internal()
                return

            stored: dict[str, PostgresTableState] = {
                fqn: PostgresTableState.model_validate(state)
                for fqn, state in json.loads(stored_state["table_states"]).items()
            }

            filters = self._get_filter_values()
            current, unreadable = await self._get_current_table_states(*filters)

            diff = diff_catalog(stored, current, unreadable)
            deleted_tables = await self._confirm_dropped(diff.missing, stored, filters)

            self.logger.info(
                f"📊 Change detection: new={len(diff.added)}, "
                f"changed={len(diff.changed)}, renamed={len(diff.renamed)}, "
                f"reanalyzed={len(diff.reanalyzed)}, "
                f"deleted={len(deleted_tables)}, replaced={len(diff.replaced)}, "
                f"unreadable={len(unreadable)}"
            )

            # Unreadable, or missing from the stats but still listed: keep what
            # was known and look again next run. Every other table's state is
            # the one just read once it is synced, and the stored one until then,
            # so the next run finds what this one could not finish.
            next_state: dict[str, PostgresTableState] = {
                fqn: stored[fqn]
                for fqn in (unreadable & stored.keys()) | (set(diff.missing) - set(deleted_tables))
            }
            for fqn in diff.unchanged:
                next_state[fqn] = current[fqn]

            # Saved as records move, so a run cut off after a move starts the
            # next one from where the records are: moved again, swapped tables
            # would trade their records back.
            self._checkpoint = dict(stored)

            # A dropped table whose name a renamed one took goes first, so the
            # name is free when that table moves into it.
            replaced = await self._handle_deleted_tables(diff.replaced)
            for fqn in diff.replaced:
                if fqn not in replaced:
                    next_state[fqn] = stored[fqn]

            # Before anything else is synced: a table that references a renamed
            # one would otherwise create a stand-in under the new name.
            unmoved = await self._rename_tables(diff.renamed, blocked=set(diff.replaced) - replaced)
            # Still held by a table that could not move out of it.
            held = {old for old, new in diff.renamed.items() if new in unmoved}
            moved = {old: new for old, new in diff.renamed.items() if new not in unmoved}
            if replaced or moved:
                await self._checkpoint_moves(replaced, moved, current)

            added = await self._sync_new_tables([fqn for fqn in diff.added if fqn not in held], current)
            for fqn in added:
                next_state[fqn] = current[fqn]

            to_sync = {fqn: change for fqn, change in diff.changed.items() if fqn not in unmoved}
            self._structure_changed = {
                fqn for fqn, change in to_sync.items() if change.structure and not change.names_only
            }
            self._names_changed = {fqn for fqn, change in to_sync.items() if change.names_only}
            synced_changed = await self._sync_changed_tables(to_sync, current)
            for fqn, change in diff.changed.items():
                previous = change.previous_fqn or fqn
                if fqn in synced_changed:
                    next_state[fqn] = current[fqn]
                elif previous != fqn and fqn not in unmoved:
                    # Its record moved, so the next run finds it under the new
                    # name and finishes the rename. Under the old name, a table
                    # created there since would be taken for it.
                    next_state[fqn] = self._checkpoint[fqn]
                else:
                    # As before this run, so the next one sees the same change;
                    # with its columns' new names once their records moved.
                    next_state[previous] = self._checkpoint.get(previous, stored[previous])

            synced_reanalyzed = await self._sync_reanalyzed_tables(diff.reanalyzed, current)
            for fqn in diff.reanalyzed:
                next_state[fqn] = current[fqn] if fqn in synced_reanalyzed else stored[fqn]

            await self._relink_referencing_tables(
                added, current, skip=added | synced_changed | synced_reanalyzed,
            )

            removed = await self._handle_deleted_tables(deleted_tables)
            for fqn in deleted_tables:
                if fqn not in removed:
                    next_state[fqn] = stored[fqn]
            if removed or replaced or diff.renamed:
                await self._remove_stale_schema_groups(filters=filters)

            await self._save_tables_sync_state(next_state)

            self.logger.info("✅ [Incremental Sync] PostgreSQL incremental sync completed")

        except Exception as e:
            self.logger.error(f"❌ [Incremental Sync] Error: {e}", exc_info=True)
            raise

    async def _confirm_dropped(
        self,
        candidates: list[str],
        stored: dict[str, PostgresTableState],
        filters: tuple[list[str] | None, str, list[str] | None, str],
    ) -> list[str]:
        """Of the tables missing from the stats, those that are really gone.

        Gone means the sync filters now exclude the table, or the table listing
        a full sync uses no longer has it. The stats come from another catalog,
        so deleting on their word alone would let an empty or partial answer
        remove tables that still exist. Raises if a listing fails.
        """
        selected_schemas, schemas_op, selected_tables, tables_op = filters
        dropped: list[str] = []
        to_check: dict[str, list[tuple[str, str]]] = defaultdict(list)

        for fqn in candidates:
            state = stored[fqn]
            if state.schema_name:
                schema_name, table_name = state.schema_name, state.table_name
            else:
                try:
                    schema_name, table_name = split_table_fqn(fqn)
                except ValueError:
                    # A name no listing can hold, kept by a delete that failed.
                    dropped.append(fqn)
                    continue
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
        selected_schemas: list[str] | None,
        schemas_op: str = MultiselectOperator.IN.value,
        selected_tables: list[str] | None = None,
        tables_op: str = MultiselectOperator.IN.value,
    ) -> tuple[dict[str, PostgresTableState], set[str]]:
        """Fetch current table states from PostgreSQL for comparison.

        Retrieves cumulative DML counters (n_tup_ins, n_tup_upd, n_tup_del) along with
        column hash for reliable change detection that survives ANALYZE runs. On a
        standby, where replayed writes leave the counters still, each table's row
        sample is hashed as well. Each state also carries what a rename leaves
        alone (the table's oid, its columns' attnums) and digests of its
        constraints and storage, which no row counter reflects.

        Returns the states and, separately, the tables that could not be read this
        run: those are unknown, not dropped. Raises if the stats or columns can't
        be read at all, since an empty answer would read as every table dropped.
        """
        table_states: dict[str, PostgresTableState] = {}
        unreadable: set[str] = set()

        # For IN, push schema filter down to SQL; for NOT_IN, fetch all and exclude client-side.
        schemas_exclude = schemas_op == MultiselectOperator.NOT_IN.value
        scope = None if schemas_exclude else selected_schemas
        stats_response = await self.data_source.get_table_stats(scope)
        if not stats_response.success:
            raise RuntimeError(f"Failed to get table stats: {stats_response.error}")

        stats_by_fqn: dict[str, TableStats] = {}
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
        columns_by_table: dict[tuple[str, str], list[ColumnInfo]] = defaultdict(list)
        column_ids: dict[tuple[str, str], dict[str, str]] = defaultdict(dict)
        column_shapes: dict[tuple[str, str], list[tuple[int, dict]]] = defaultdict(list)
        # A column with no attnum cannot be told apart from a renamed one.
        unplaced: set[tuple[str, str]] = set()
        for row in columns_response.data:
            row = dict(row)
            key = (row.pop("schema_name"), row.pop("table_name"))
            attnum = row.pop("attnum", None)
            column = ColumnInfo.model_validate(row)
            columns_by_table[key].append(column)
            if attnum is None:
                unplaced.add(key)
                continue
            column_ids[key][str(attnum)] = row.get("name") or ""
            column_shapes[key].append((int(attnum), column.model_dump(exclude={"name"})))

        constraints_response = await self.data_source.get_constraint_hashes(scope)
        if not constraints_response.success:
            raise RuntimeError(f"Failed to get constraints: {constraints_response.error}")
        constraint_hashes = {
            (row["schema_name"], row["table_name"]): row["constraint_hash"] or ""
            for row in constraints_response.data
        }
        constraint_shapes = {
            (row["schema_name"], row["table_name"]): row.get("constraint_shape") or ""
            for row in constraints_response.data
        }

        primary_keys = await self._primary_keys_if_standby(scope)
        max_rows = self._max_rows(self.sync_filters)

        for fqn, stat in stats_by_fqn.items():
            key = (stat.schema_name, stat.table_name)
            state = PostgresTableState(
                column_hash=_column_hash(columns_by_table.get(key, [])),
                constraint_hash=constraint_hashes.get(key, ""),
                n_tup_ins=stat.n_tup_ins or 0,
                n_tup_upd=stat.n_tup_upd or 0,
                n_tup_del=stat.n_tup_del or 0,
                n_live_tup=stat.n_live_tup or 0,
                schema_name=stat.schema_name,
                table_name=stat.table_name,
                analyze_count=stat.analyze_count or 0,
                oid=stat.oid or 0,
                storage_id=stat.storage_id or "",
                columns=column_ids.get(key, {}),
                row_limit=max_rows,
                shape="" if key in unplaced else _table_shape(
                    column_shapes.get(key, []), constraint_shapes.get(key, ""),
                ),
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
        scope: list[str] | None,
    ) -> dict[tuple[str, str], list[str]] | None:
        """Primary keys per table when the server is a standby, else None."""
        recovery = await self.data_source.is_in_recovery()
        if not recovery.success:
            raise RuntimeError(f"Failed to read recovery state: {recovery.error}")
        if not recovery.data["in_recovery"]:
            return None

        response = await self.data_source.get_primary_keys_by_table(scope)
        if not response.success:
            raise RuntimeError(f"Failed to get primary keys: {response.error}")
        keys: dict[tuple[str, str], list[str]] = defaultdict(list)
        for row in response.data:
            keys[(row["schema_name"], row["table_name"])].append(row["column_name"])
        return keys

    @staticmethod
    def _max_rows(sync_filters: FilterCollection) -> int:
        value = sync_filters.get_value(IndexingFilterKey.MAX_ROWS_PER_TABLE, default=1000)
        return max(1, min(int(value), MAX_ROWS_PER_TABLE_LIMIT))

    async def _sync_new_tables(
        self,
        table_fqns: list[str],
        states: dict[str, PostgresTableState],
    ) -> set[str]:
        """Sync newly discovered tables; returns the ones saved.

        Also ensures parent schema RecordGroups exist for any new schemas
        that weren't present during the initial full sync.
        """
        synced: set[str] = set()
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
        changes: dict[str, TableChange],
        states: dict[str, PostgresTableState],
    ) -> set[str]:
        """Sync changed tables; returns the ones saved."""
        synced: set[str] = set()
        if not changes:
            return synced

        self.logger.info(f"Syncing {len(changes)} changed tables")

        # A table moved into a schema that was not synced before needs its group.
        moved_into = sorted({states[fqn].schema_name for fqn, c in changes.items() if c.previous_fqn})
        if moved_into:
            await self._sync_schemas([PostgresSchema(name=s) for s in moved_into])

        for fqn in sorted(changes):
            state = states[fqn]
            try:
                table = await self._load_table(state.schema_name, state.table_name, state)
            except Exception as e:
                self.sync_stats.errors += 1
                self.logger.error(f"Error reading changed table {fqn}: {e}")
                continue
            synced |= await self._sync_updated_tables(state.schema_name, [table], changes)

        return synced

    async def _sync_reanalyzed_tables(
        self,
        table_fqns: list[str],
        states: dict[str, PostgresTableState],
    ) -> set[str]:
        """Re-profile tables ANALYZE ran on since the last sync; returns the ones saved.

        Only their column records are saved. The rows did not change, and a
        content update on the table record would re-index it.
        """
        synced: set[str] = set()
        for fqn in table_fqns:
            state = states[fqn]
            try:
                table = await self._load_table(state.schema_name, state.table_name, state)
                record, existed = await self._build_table_record(state.schema_name, table)
                if existed:
                    await self._sync_table_columns(record, table)
                    synced.add(fqn)
                else:
                    synced |= await self._sync_updated_tables(state.schema_name, [table])
            except Exception as e:
                self.sync_stats.errors += 1
                self.logger.error(f"Error re-profiling table {fqn}: {e}", exc_info=True)
        return synced

    async def _handle_deleted_tables(self, table_fqns: list[str]) -> set[str]:
        """Delete records of tables that no longer exist; returns the ones handled.

        One whose record could not be looked up is not handled, so its state
        stays and the next run tries again: a failed lookup is not an answer
        that nothing is stored.
        """
        handled: set[str] = set()
        if not table_fqns:
            return handled

        self.logger.info(f"Handling {len(table_fqns)} deleted tables")

        record_ids: dict[str, str] = {}
        for fqn in table_fqns:
            try:
                record = await self.data_entities_processor.get_record_by_external_id(
                    connector_id=self.connector_id, external_record_id=fqn, raise_on_error=True,
                )
            except Exception as e:
                self.sync_stats.errors += 1
                self.logger.warning(f"Failed to look up the record of deleted table {fqn}: {e}")
                continue
            if record and record.id:
                record_ids[fqn] = record.id
            else:
                handled.add(fqn)
        for chunk in self._removal_batches(sorted(record_ids.items())):
            try:
                await self._remove_records([record_id for _, record_id in chunk])
                handled.update(fqn for fqn, _ in chunk)
            except Exception as e:
                self.sync_stats.errors += 1
                self.logger.warning(f"Failed to delete the records of {len(chunk)} deleted table(s): {e}")
        return handled

    @staticmethod
    def _removal_batches(items: list[Any]) -> list[list[Any]]:
        return [items[i:i + TABLES_REMOVED_PER_BATCH] for i in range(0, len(items), TABLES_REMOVED_PER_BATCH)]

    async def _relink_referencing_tables(
        self,
        added: set[str],
        current: dict[str, PostgresTableState],
        *,
        skip: set[str],
    ) -> None:
        """Write again the foreign keys of the tables that point at tables just added.

        A table that comes back (the filter includes it again, the connector
        can read it again) gets a new record; the edges into the one it had went
        with it, and the tables they came from did not change, so they are not
        synced. Best effort: a table missed here gets its edges back the next
        time it changes.
        """
        oids = [current[fqn].oid for fqn in sorted(added) if current[fqn].oid]
        if not oids:
            return
        try:
            response = await self.data_source.get_referencing_tables(oids)
            if not response.success:
                raise RuntimeError(response.error)
        except Exception as e:
            self.sync_stats.errors += 1
            self.logger.warning(f"Could not find the tables that reference new tables: {e}")
            return
        for row in response.data or []:
            fqn = table_fqn(row["schema_name"], row["table_name"])
            if fqn in skip or fqn not in current:
                continue
            state = current[fqn]
            try:
                fks = await self.data_source.get_foreign_keys(state.schema_name, state.table_name)
                if not fks.success:
                    raise RuntimeError(fks.error)
                table = PostgresTable(
                    name=state.table_name,
                    schema_name=state.schema_name,
                    row_count=state.n_live_tup,
                    foreign_keys=[ForeignKeyInfo.model_validate(fk) for fk in (fks.data or [])],
                    state=state,
                )
                record, existed = await self._build_table_record(state.schema_name, table)
                if existed:
                    await self.data_entities_processor.on_record_metadata_update(record)
            except Exception as e:
                self.sync_stats.errors += 1
                self.logger.warning(f"Could not write the foreign keys of {fqn} again: {e}")

    async def _remove_stale_tables(self, listed_fqns: set[str]) -> dict[str, PostgresTableState]:
        """Delete records of tables that were dropped or are now filtered out.

        Returns a placeholder state for each table whose delete failed.
        """
        records = await self.data_entities_processor.get_records_by_record_type(
            self.connector_id, RecordType.SQL_TABLE
        )
        stale = [r for r in records if r.external_record_id not in listed_fqns]
        undeleted: dict[str, PostgresTableState] = {}
        if not stale:
            return undeleted

        self.logger.info(f"Removing {len(stale)} tables that are no longer synced")
        for chunk in self._removal_batches(stale):
            try:
                await self._remove_records([record.id for record in chunk])
            except Exception as e:
                self.sync_stats.errors += 1
                self.logger.warning(f"Failed to delete the records of {len(chunk)} table(s): {e}")
                for record in chunk:
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
        synced_schemas: list[str] | None = None,
        filters: tuple[list[str] | None, str, list[str] | None, str] | None = None,
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
            group_ids = {
                g.get("externalGroupId"): str(g.get("id") or g.get("_key"))
                for g in groups if g.get("id") or g.get("_key")
            }
            deleted: list[str] = []
            try:
                for external_group_id in stale:
                    if not await self.data_entities_processor.on_record_group_deleted(
                        external_group_id, self.connector_id
                    ):
                        # Still there, so its search entries stay too; tried again next run.
                        self.sync_stats.errors += 1
                        continue
                    if external_group_id in group_ids:
                        deleted.append(group_ids[external_group_id])
            finally:
                # A group deleted is not listed again, so its search entries
                # are cleaned up now even if a later group failed.
                if deleted:
                    await self.data_entities_processor.on_sql_record_groups_deleted(deleted, self.connector_id)
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
            for edge_collection in (
                CollectionNames.BELONGS_TO.value,
                CollectionNames.INHERIT_PERMISSIONS.value,
                CollectionNames.NODE_RELATIONS.value,
            ):
                await tx_store.delete_edge(
                    group.id, CollectionNames.RECORD_GROUPS.value,
                    group.id, CollectionNames.RECORD_GROUPS.value,
                    edge_collection,
                )

    async def _checkpoint_moves(
        self, replaced: set[str], moved: dict[str, str], current: dict[str, PostgresTableState],
    ) -> None:
        """Save that these tables went and these records moved, before anything else is synced."""
        checkpoint = self._checkpoint
        moving = {old: checkpoint.pop(old) for old in moved if old in checkpoint}
        for fqn in replaced:
            checkpoint.pop(fqn, None)
        for old, state in moving.items():
            new = moved[old]
            checkpoint[new] = state.model_copy(update={
                "schema_name": current[new].schema_name,
                "table_name": current[new].table_name,
                "renamed_from": old,
            })
        await self._save_checkpoint()

    async def _checkpoint_column_renames(self, table_fqn: str, renames: dict[str, str]) -> None:
        """Save that this table's column records moved to their new names."""
        state = (self._checkpoint or {}).get(table_fqn)
        if state is None:
            return
        self._checkpoint[table_fqn] = state.model_copy(update={
            "columns": {attnum: renames.get(name, name) for attnum, name in state.columns.items()},
            "renamed_from": state.renamed_from or table_fqn,
        })
        await self._save_checkpoint()

    async def _save_checkpoint(self) -> None:
        for attempt in range(1, SYNC_STATE_SAVE_ATTEMPTS + 1):
            try:
                await self._save_tables_sync_state(self._checkpoint, checkpoint=True)
                return
            except Exception as e:
                if attempt == SYNC_STATE_SAVE_ATTEMPTS:
                    self.sync_stats.errors += 1
                    self.logger.error(
                        f"Could not save where this run moved records; cut off before it ends, "
                        f"the next run would move them again: {e}"
                    )
                    return
                await asyncio.sleep(attempt)

    async def _save_tables_sync_state(
        self, states: dict[str, PostgresTableState], *, checkpoint: bool = False,
    ) -> None:
        """Save table states for the next incremental sync to compare against.

        A ``checkpoint`` is saved mid-run, so it leaves the last sync time alone.
        """
        serialized_states = json.dumps(
            {fqn: state.model_dump() for fqn, state in states.items()}
        )
        data: dict[str, Any] = {
            "state_version": SYNC_STATE_VERSION,
            "database": self.database_name,
            "server": self.server_address,
            "database_oid": self.database_oid,
            "table_states": serialized_states,
        }
        if not checkpoint:
            data["last_sync_time"] = get_epoch_timestamp_in_ms()
        await self.tables_sync_point.update_sync_point(SYNC_STATE_KEY, data)
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

            schema_cache: list[FilterOption] = []
            table_cache:  list[FilterOption] = []

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
        search: str | None = None,
        cursor: str | None = None,
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
