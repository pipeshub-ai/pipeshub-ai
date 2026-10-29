"""
Snowflake Connector

Syncs databases, namespaces, tables, views, stages and files from Snowflake.
"""
import asyncio
import json
import mimetypes
import os
import re
import uuid
from collections.abc import AsyncGenerator, Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from logging import Logger
from typing import Any

from aiolimiter import AsyncLimiter
from fastapi import HTTPException
from fastapi.responses import StreamingResponse

from app.config.configuration_service import ConfigurationService
from app.config.constants.arangodb import (
    Connectors,
    MimeTypes,
    OriginTypes,
    PermissionModel,
    RecordRelations,
)
from app.config.constants.http_status_code import HttpStatusCode
from app.connectors.core.base.connector.connector_service import (
    BaseConnector,
    ConnectionCheckResult,
)
from app.connectors.core.base.data_processor.data_source_entities_processor import (
    DataSourceEntitiesProcessor,
)
from app.connectors.core.base.data_store.data_store import DataStoreProvider
from app.connectors.core.base.error.sql_stream_errors import (
    to_sql_response_error,
    to_sql_stream_error,
)
from app.connectors.core.base.error.stream_errors import (
    connector_not_ready,
    map_source_status,
    not_found_at_source,
    to_stream_error,
)
from app.connectors.core.base.sync_point.sync_point import SyncDataPointType, SyncPoint
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
    load_connector_filters,
)
from app.connectors.core.sql.catalog_diff import CatalogDiff, diff_catalog
from app.connectors.core.sql.column_records import (
    build_column_records,
    load_typed_columns,
)
from app.connectors.core.sql.permissions import database_permissions
from app.connectors.core.sql.profiling import looks_personal
from app.connectors.core.sql.qualified_names import (
    join_qualified_name,
    split_qualified_name,
)
from app.connectors.core.sql.record_moves import Moves
from app.connectors.core.sql.sync_outcome import SyncOutcome
from app.connectors.core.sync.full_sync_coverage import FullSyncCoverage
from app.connectors.sources.snowflake.apps import SnowflakeApp
from app.connectors.sources.snowflake.catalog_state import SnowflakeTableState
from app.connectors.sources.snowflake.catalog_sync import (
    Filters,
    Scope,
    Snapshot,
    database_of,
    gone_since,
    in_stage,
    next_state,
    stored_tables,
    take_snapshot,
    unreadable_tables,
)
from app.connectors.sources.snowflake.connection_check import check_connection
from app.connectors.sources.snowflake.data_fetcher import (
    SnowflakeDatabase,
    SnowflakeDataFetcher,
    SnowflakeFetchError,
    SnowflakeFile,
    SnowflakeHierarchy,
    SnowflakeSchema,
    SnowflakeStage,
    SnowflakeTable,
    SnowflakeView,
    apply_keys,
    schema_key,
)
from app.connectors.sources.snowflake.sync_lifecycle import (
    LOOKUP_ATTEMPTS,
    SyncLifecycleMixin,
    _column_fqn,
    checkpoint_after_moves,
    restructured_fqns,
)
from app.connectors.sources.snowflake.sync_state import (
    DatabaseState,
    SnowflakeStateStore,
)
from app.models.entities import (
    AppUser,
    FileRecord,
    ProgressStatus,
    Record,
    RecordGroup,
    RecordGroupType,
    RecordType,
    RelatedExternalRecord,
    SQLColumnRecord,
    SQLTableRecord,
    SQLViewRecord,
    User,
)
from app.models.permission import Permission
from app.sources.client.snowflake.snowflake import parse_connection_settings
from app.sources.external.snowflake.profiling import (
    ProfilingBudget,
    ProfilingDecision,
    SnowflakeProfiler,
    TableProfile,
    decide_profiling,
    empty_profile,
    is_countable,
    profile_is_current,
)
from app.sources.external.snowflake.snowflake_ import SnowflakeDataSource
from app.sources.external.snowflake.statements import SnowflakeStatementRunner
from app.utils.streaming import create_stream_record_response, stream_content
from app.utils.time_conversion import get_epoch_timestamp_in_ms


def get_file_extension(path: str) -> str | None:
    if "." in path:
        parts = path.split(".")
        if len(parts) > 1:
            return parts[-1].lower()
    return None


def get_mimetype_from_path(path: str, is_folder: bool = False) -> str:
    if is_folder:
        return MimeTypes.FOLDER.value
    mime_type, _ = mimetypes.guess_type(path)
    if mime_type:
        try:
            return MimeTypes(mime_type).value
        except ValueError:
            return MimeTypes.BIN.value
    return MimeTypes.BIN.value



MAX_ROWS_PER_TABLE_LIMIT = 10000
# Cap on the rows' text in one table's document, so a table of wide rows can't
# exhaust the connector's memory.
MAX_TABLE_DOCUMENT_BYTES = 8 * 1024 * 1024
MAX_COLUMN_VALUES_STREAMED = 1000
# Up to this many rows a row sample is ordered, so unchanged data gives the same
# sample; past it ORDER BY would sort the whole table. Column value
# counts switch from GROUP BY to APPROX_TOP_K at the same size.
ORDERED_READ_MAX_ROWS = 1_000_000

# SQLSTATE classes that mean the statement never got a fair chance: connection
# exception, insufficient resources, operator intervention (a suspended
# warehouse, a tripped resource monitor, a statement killed mid-flight).
_UNAVAILABLE_SQLSTATE_CLASSES = frozenset({"08", "53", "57"})


def to_snowflake_stream_error(exc: BaseException, *, connector: str) -> HTTPException:
    """Map a Snowflake streaming failure, preferring SQLSTATE over HTTP status.

    The SQL API returns HTTP 422 for every statement-level failure alike, so
    ``map_source_status`` can only be trusted where no SQLSTATE came back —
    that is, where the request failed before Snowflake ran anything.
    """
    sqlstate = getattr(exc, "sqlstate", None)
    if not isinstance(sqlstate, str) or not sqlstate:
        return to_stream_error(exc, connector=connector)
    if sqlstate[:2] in _UNAVAILABLE_SQLSTATE_CLASSES:
        return map_source_status(
            HttpStatusCode.SERVICE_UNAVAILABLE.value, connector=connector
        )
    mapped = to_sql_stream_error(exc, connector=connector)
    if mapped.status_code == HttpStatusCode.INTERNAL_SERVER_ERROR.value:
        # No SQLSTATE table hit; Snowflake's own message is the last signal left.
        return to_sql_response_error(str(exc), connector=connector)
    return mapped


_NAME_PART = r'(?:"(?:[^"]|"")+"|[A-Za-z_][A-Za-z0-9_$]*)'
# FROM / JOIN followed by a 1- to 3-part name that is not a function call.
_SOURCE_REFERENCE = re.compile(
    rf'\b(?:FROM|JOIN)\s+({_NAME_PART}(?:\s*\.\s*{_NAME_PART}){{0,2}})(?![\w$])(?!\s*[.(])',
    re.IGNORECASE,
)
_CTE_NAME = re.compile(rf'(?:\bWITH|,)\s*(?:RECURSIVE\s+)?({_NAME_PART})\s+AS\s*\(', re.IGNORECASE)
_NOT_A_TABLE = frozenset({"SELECT", "LATERAL", "TABLE", "VALUES", "UNNEST"})
# Text that is not code: string literals ('...' with '' or \' escapes, and
# $$...$$), line comments and block comments. Quoted identifiers stay.
_NOT_CODE = re.compile(r"'(?:[^'\\]|''|\\.)*'|\$\$.*?\$\$|--[^\n]*|/\*.*?\*/", re.DOTALL)
# Functions whose arguments use FROM as a keyword: EXTRACT(YEAR FROM d).
_FROM_TAKING_FUNCTIONS = frozenset({"EXTRACT", "TRIM", "SUBSTRING", "SUBSTR", "POSITION", "OVERLAY", "DATE_PART"})
_DISTINCT_BEFORE = re.compile(r"\bDISTINCT\s*$", re.IGNORECASE)
_WORD_BEFORE = re.compile(r"([A-Za-z_][A-Za-z0-9_$]*)\s*$")


def _code_only(sql: str) -> str:
    """``sql`` with literals and comments blanked, so their words match nothing."""
    return _NOT_CODE.sub(lambda m: " " * len(m.group(0)), sql)


def _enclosing_function(code: str, position: int) -> str | None:
    """The upper-cased name of the function whose parentheses hold ``position``."""
    depth = 0
    for index in range(position - 1, -1, -1):
        char = code[index]
        if char == ")":
            depth += 1
        elif char == "(":
            if depth == 0:
                word = _WORD_BEFORE.search(code[:index])
                return word.group(1).upper() if word else None
            depth -= 1
    return None


def _name_value(part: str) -> str:
    """A name part as Snowflake stores it: quoted kept exact, unquoted upper-cased."""
    if part.startswith('"'):
        return part[1:-1].replace('""', '"')
    return part.upper()


def parse_view_sources(definition: str | None) -> list[tuple[str, ...]]:
    """The 1- to 3-part names of the tables a view's SQL reads from.

    A heuristic, not a parser: names after FROM and JOIN, outside literals and
    comments, without function calls, FROM used as a keyword inside EXTRACT
    and the like or in IS DISTINCT FROM, and the view's own CTE names.
    """
    if not definition:
        return []
    code = _code_only(definition)
    ctes = {_name_value(m.group(1)) for m in _CTE_NAME.finditer(code)}
    sources: list[tuple[str, ...]] = []
    for match in _SOURCE_REFERENCE.finditer(code):
        if _DISTINCT_BEFORE.search(code[:match.start()]):
            continue
        if _enclosing_function(code, match.start()) in _FROM_TAKING_FUNCTIONS:
            continue
        parts = tuple(_name_value(p) for p in re.findall(_NAME_PART, match.group(1)))
        if len(parts) == 1 and (parts[0] in ctes or parts[0] in _NOT_A_TABLE):
            continue
        if parts not in sources:
            sources.append(parts)
    return sources


def _epoch_ms(timestamp: str | None) -> int | None:
    """Milliseconds since the epoch for an ISO timestamp from the catalog; None if absent or unreadable."""
    if not timestamp:
        return None
    try:
        moment = datetime.fromisoformat(timestamp)
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return int(moment.timestamp() * 1000)


def resolve_source_table(parts: Sequence[str], database: str, schema: str) -> tuple[str, str, str]:
    """A view source's database, schema and table, filling in the view's own."""
    if len(parts) == 1:
        return database, schema, parts[0]
    if len(parts) == 2:
        return database, parts[0], parts[1]
    return parts[0], parts[1], parts[2]


@dataclass
class SyncStats:
    """Statistics for sync operations."""
    databases_synced: int = 0
    schemas_synced: int = 0
    stages_synced: int = 0
    tables_new: int = 0
    views_new: int = 0
    files_new: int = 0
    errors: int = 0

    def to_dict(self) -> dict[str, int]:
        return {
            'databases_synced': self.databases_synced,
            'schemas_synced': self.schemas_synced,
            'stages_synced': self.stages_synced,
            'tables_new': self.tables_new,
            'views_new': self.views_new,
            'files_new': self.files_new,
            'errors': self.errors,
        }

    def log_summary(self, logger: Logger) -> None:
        logger.info(
            f"📊 Sync Stats: DBs={self.databases_synced}, Schemas={self.schemas_synced}, "
            f"Stages={self.stages_synced} | Tables={self.tables_new} | Views={self.views_new} | "
            f"Files={self.files_new} | Errors={self.errors}"
        )


def _paginate_options(
    options: list[FilterOption],
    page: int,
    limit: int,
    search: str | None,
) -> FilterOptionsResponse:
    """One page of filter options, matching ``search`` anywhere in the label."""
    if search:
        needle = search.lower()
        options = [o for o in options if needle in o.label.lower()]
    start = (page - 1) * limit
    page_items = options[start:start + limit]
    has_more = start + limit < len(options)
    return FilterOptionsResponse(
        success=True,
        options=page_items,
        page=page,
        limit=limit,
        has_more=has_more,
        cursor=str(page + 1) if has_more else None,
    )


def _options_failure(page: int, limit: int, message: str) -> FilterOptionsResponse:
    return FilterOptionsResponse(
        success=False, options=[], page=page, limit=limit, has_more=False, message=message,
    )


_ACCOUNT_FIELD = AuthField(
    name="accountIdentifier",
    display_name="Account Identifier",
    placeholder="myorg-myaccount",
    description="orgname-accountname, or the account locator with its region (e.g., abc12345.us-east-1)",
    field_type="TEXT",
    max_length=500,
)
_WAREHOUSE_FIELD = AuthField(
    name="warehouse",
    display_name="Warehouse",
    placeholder="COMPUTE_WH",
    description="Warehouse the connector's queries run on",
    field_type="TEXT",
    max_length=200,
)
_ROLE_FIELD = AuthField(
    name="role",
    display_name="Role",
    placeholder="PIPESHUB_READER",
    description="Role the connector's queries run as; empty for the user's default role",
    field_type="TEXT",
    required=False,
    min_length=0,
    max_length=255,
)


@ConnectorBuilder("Snowflake")\
    .in_group("Snowflake")\
    .with_description("Sync databases, tables, views, stages and files from Snowflake")\
    .with_categories(["Database", "Data Warehouse"])\
    .with_scopes([ConnectorScope.PERSONAL.value, ConnectorScope.TEAM.value])\
    .with_permission_model(PermissionModel.APP_LEVEL)\
    .with_auth([
        # OAuth comes back with token refresh and a per-account authorize URL.
        # PAT first: it stays the default, and saved connectors carry ACCESS_KEY.
        AuthBuilder.type(AuthType.ACCESS_KEY).fields([
            _ACCOUNT_FIELD,
            AuthField(
                name="patToken",
                display_name="Personal Access Token",
                placeholder="Enter your PAT token",
                description="Personal Access Token from Snowflake",
                field_type="PASSWORD",
                max_length=2000,
                is_secret=True
            ),
            _WAREHOUSE_FIELD,
            _ROLE_FIELD,
        ]),
        AuthBuilder.type(AuthType.KEY_PAIR).fields([
            _ACCOUNT_FIELD,
            AuthField(
                name="user",
                display_name="User",
                placeholder="SVC_PIPESHUB",
                description="The user's login name; its RSA_PUBLIC_KEY must match this private key",
                field_type="TEXT",
                max_length=255,
            ),
            AuthField(
                name="privateKey",
                display_name="Private Key",
                placeholder="-----BEGIN PRIVATE KEY-----",
                description="RSA private key in PEM format (PKCS#8 or PKCS#1), at least 2048 bits",
                field_type="TEXTAREA",
                max_length=10000,
                is_secret=True,
            ),
            AuthField(
                name="privateKeyPassphrase",
                display_name="Private Key Passphrase",
                placeholder="Only for an encrypted key",
                description="Passphrase of an encrypted private key; leave empty otherwise",
                field_type="PASSWORD",
                required=False,
                min_length=0,
                max_length=1000,
                is_secret=True,
            ),
            _WAREHOUSE_FIELD,
            _ROLE_FIELD,
        ]),
    ])\
    .configure(lambda builder: builder
        .with_icon(IconPaths.connector_icon(Connectors.SNOWFLAKE.value))
        .add_documentation_link(DocumentationLink(
            "Snowflake PAT Setup",
            "https://docs.snowflake.com/en/developer-guide/sql-api/authenticating",
            "setup"
        ))
        .add_filter_field(FilterField(
            name="databases",
            display_name="Database Names",
            filter_type=FilterType.MULTISELECT,
            category=FilterCategory.SYNC,
            description="Select specific databases to sync",
            option_source_type=OptionSourceType.DYNAMIC,
            default_value=[],
            default_operator=MultiselectOperator.IN.value
        ))
        .add_filter_field(FilterField(
            name="schemas",
            display_name="Schemas",
            filter_type=FilterType.MULTISELECT,
            category=FilterCategory.SYNC,
            description="Select specific schemas to sync (Format: Database.Schema)",
            option_source_type=OptionSourceType.DYNAMIC,
            default_value=[],
            default_operator=MultiselectOperator.IN.value
        ))
        .add_filter_field(FilterField(
            name="tables",
            display_name="Tables",
            filter_type=FilterType.MULTISELECT,
            category=FilterCategory.SYNC,
            description="Select specific tables to sync",
            option_source_type=OptionSourceType.DYNAMIC,
            default_value=[],
            default_operator=MultiselectOperator.IN.value
        ))
        .add_filter_field(FilterField(
            name="views",
            display_name="Views",
            filter_type=FilterType.MULTISELECT,
            category=FilterCategory.SYNC,
            description="Select specific views to sync",
            option_source_type=OptionSourceType.DYNAMIC,
            default_value=[],
            default_operator=MultiselectOperator.IN.value
        ))
        .add_filter_field(FilterField(
            name="stages",
            display_name="Stages",
            filter_type=FilterType.MULTISELECT,
            category=FilterCategory.SYNC,
            description="Select specific stages to sync",
            option_source_type=OptionSourceType.DYNAMIC,
            default_value=[],
            default_operator=MultiselectOperator.IN.value
        ))
        .add_filter_field(FilterField(
            name="files",
            display_name="Files",
            filter_type=FilterType.MULTISELECT,
            category=FilterCategory.SYNC,
            description="Select specific files to sync (Format: Database.Schema.Stage/path/to/file)",
            option_source_type=OptionSourceType.DYNAMIC,
            default_value=[],
            default_operator=MultiselectOperator.IN.value
        ))
        # Indexing Filters
        .add_filter_field(FilterField(
            name=IndexingFilterKey.TABLES.value,
            display_name="Index Tables",
            filter_type=FilterType.BOOLEAN,
            category=FilterCategory.INDEXING,
            description="Enable indexing of tables",
            default_value=True
        ))
        .add_filter_field(FilterField(
            name=IndexingFilterKey.VIEWS.value,
            display_name="Index Views",
            filter_type=FilterType.BOOLEAN,
            category=FilterCategory.INDEXING,
            description="Enable indexing of views",
            default_value=True
        ))
        .add_filter_field(FilterField(
            name=IndexingFilterKey.STAGE_FILES.value,
            display_name="Index Stage Files",
            filter_type=FilterType.BOOLEAN,
            category=FilterCategory.INDEXING,
            description="Enable indexing of files in stages",
            default_value=True
        ))
        .add_filter_field(FilterField(
            name=IndexingFilterKey.MAX_ROWS_PER_TABLE.value,
            display_name="Max Rows Per Table",
            filter_type=FilterType.NUMBER,
            category=FilterCategory.SYNC,
            description=f"Maximum number of rows to index per table (max {MAX_ROWS_PER_TABLE_LIMIT})",
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
class SnowflakeConnector(SyncLifecycleMixin, BaseConnector):

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
            SnowflakeApp(connector_id),
            logger,
            data_entities_processor,
            data_store_provider,
            config_service,
            connector_id,
            scope,
            created_by,
        )
        self.connector_id = connector_id
        self.connector_name = Connectors.SNOWFLAKE
        self.connector_scope: str | None = scope
        self.data_source: SnowflakeDataSource | None = None
        self.runner: SnowflakeStatementRunner | None = None
        self.data_fetcher: SnowflakeDataFetcher | None = None
        self.warehouse: str | None = None
        self.account_identifier: str | None = None
        self.batch_size = 100
        self.rate_limiter = AsyncLimiter(25, 1)
        self.sync_filters: FilterCollection = FilterCollection()
        self.indexing_filters: FilterCollection = FilterCollection()
        self.sync_stats: SyncStats = SyncStats()
        self.profiler: SnowflakeProfiler | None = None
        self._profiling_budget = ProfilingBudget()
        # (table record id, ids of its dropped columns' records), removed once
        # the tables are saved.
        self._dropped_columns: list[tuple[str, list[str]]] = []
        self.sync_outcome = SyncOutcome()
        # Tables written this sync, and renamed ones whose new name was not free.
        self._written_tables: set[str] = set()
        self._held_tables: set[str] = set()
        # Set when this sync left part of the account unwritten; see full_sync_coverage.
        self._skipped_part = False
        self.tables_sync_point = SyncPoint(
            connector_id=self.connector_id,
            org_id=self.data_entities_processor.org_id,
            sync_data_point_type=SyncDataPointType.RECORDS,
            data_store_provider=data_store_provider,
        )
        self._state_store = SnowflakeStateStore(self.tables_sync_point)
        self._frontend_url: str = os.getenv("FRONTEND_PUBLIC_URL", "").rstrip("/")

    def get_app_users(self, users: list[User]) -> list[AppUser]:
        """Convert User objects to AppUser objects for Snowflake connector."""
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

    async def init(self) -> bool:
        """Read the stored config and build the SQL API client."""
        try:
            config = await self.config_service.get_config(
                f"/services/connectors/{self.connector_id}/config"
            )
            if not config:
                self.logger.error("Snowflake configuration not found")
                return False

            try:
                settings = parse_connection_settings(config)
            except ValueError as e:
                self.logger.error(f"Invalid Snowflake configuration: {e}")
                return False

            self.scope = config.get("scope", self.scope or ConnectorScope.TEAM.value)
            self.connector_scope = self.scope
            self.created_by = config.get("created_by", self.created_by)
            self.account_identifier = settings.account_identifier
            self.warehouse = settings.warehouse
            self.logger.info(f"Using {settings.auth_type.value} authentication for Snowflake")

            self.data_source = SnowflakeDataSource(settings.create_client())
            self.runner = SnowflakeStatementRunner(
                self.data_source, warehouse=self.warehouse, role=settings.role, rate_limiter=self.rate_limiter,
            )
            self.data_fetcher = SnowflakeDataFetcher(self.runner)
            self.profiler = SnowflakeProfiler(self.runner)

            self.sync_filters, self.indexing_filters = await load_connector_filters(
                self.config_service, "snowflake", self.connector_id, self.logger
            )

            self.logger.info("Snowflake connector initialized successfully")
            return True

        except Exception as e:
            self.logger.error(f"Failed to initialize Snowflake connector: {e}", exc_info=True)
            return False

    async def run_sync(self) -> None:
        """Bring the graph in step with the account."""
        try:
            self.logger.info("📦 [Sync] Starting Snowflake sync...")
            # Before anything that can raise: a failed sync must not report the
            # previous one's tables again.
            self.sync_stats = SyncStats()
            self.sync_outcome = SyncOutcome()
            self._profiling_budget = ProfilingBudget()
            self._dropped_columns = []
            self._written_tables = set()
            self._skipped_part = False

            if not self.data_fetcher:
                raise ConnectionError("Snowflake connector not initialized")

            self.sync_filters, self.indexing_filters = await load_connector_filters(
                self.config_service, "snowflake", self.connector_id, self.logger
            )
            try:
                await self._sync_catalog()
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

    async def run_incremental_sync(self) -> None:
        """Every sync compares the catalog with the saved state; there is one path."""
        await self.run_sync()

    def full_sync_coverage(self) -> FullSyncCoverage:
        """Incomplete when the sync wrote less than the account has.

        A full sync unlinks what it did not write. A schema or stage that
        could not be listed, a table that could not be read or must wait for
        a rename, and an object whose write failed are still there.
        """
        return FullSyncCoverage(complete=not self._skipped_part)

    def _filter_test(self, key: str) -> Callable[[str], bool] | None:
        """Whether a name passes the ``key`` sync filter; None when the filter is not set."""
        filter_ = self.sync_filters.get(key)
        if not filter_ or not filter_.value:
            return None
        selected = set(filter_.value)
        if filter_.operator_value == MultiselectOperator.NOT_IN.value:
            return lambda name: name not in selected
        return lambda name: name in selected

    @staticmethod
    def _passes(test: Callable[[str], bool] | None, name: str) -> bool:
        return test is None or test(name)

    async def _sync_catalog(self) -> None:
        """Read the catalog, follow renames, write what is listed, remove what is gone, save the state."""
        await self._create_app_user()
        stored = await self._state_store.load(self.account_identifier)
        hierarchy = await self.data_fetcher.fetch_all(
            include_database=self._filter_test("databases"),
            include_schema=self._filter_test("schemas"),
            include_files=True,
            include_relationships=True,
        )
        self.sync_stats.errors += hierarchy.errors
        self.logger.info(f"Fetched hierarchy: {hierarchy.summary()}")
        scope = Scope.of(hierarchy)
        snapshot = take_snapshot(hierarchy, self._filters(), row_limit=self._max_rows(self.sync_filters))
        if scope.databases or scope.schemas or scope.stages or snapshot.unread_tables:
            self._skipped_part = True

        # Groups first: a table renamed into a new schema moves into its group.
        await self._sync_databases(hierarchy.databases)
        self.sync_stats.databases_synced = len(hierarchy.databases)
        for db in hierarchy.databases:
            schemas = hierarchy.schemas.get(db.name, [])
            await self._sync_namespaces(db.name, schemas)
            self.sync_stats.schemas_synced += len(schemas)

        old_tables = stored_tables(stored or {})
        await self._finish_interrupted_moves(old_tables)
        unreadable = unreadable_tables(old_tables, snapshot, scope)
        diff = diff_catalog(old_tables, snapshot.tables, unreadable)
        self.logger.info(
            f"📊 Changes: new={len(diff.added)}, changed={len(diff.changed)}, renamed={len(diff.renamed)}, "
            f"gone={len(diff.missing)}, replaced={len(diff.replaced)}, unreadable={len(unreadable)}"
        )

        replaced = await self._remove_by_external_ids(diff.replaced)

        async def checkpoint(moving: dict[str, str], record_ids: dict[str, str]) -> bool:
            states = checkpoint_after_moves(stored or {}, moving, snapshot.tables, record_ids)
            return await self._save_state(states, stored=stored)

        moved, moves = await self._move_renamed_tables(
            diff.renamed, blocked=set(diff.replaced) - replaced, checkpoint=checkpoint,
        )
        held = {new for old, new in diff.renamed.items() if old not in moved}
        if held:
            self._skipped_part = True
        await self._follow_renames(diff, moved, moves)
        self.sync_outcome.restructured_tables |= restructured_fqns(diff, snapshot.tables) - held
        self._held_tables = held

        known = dict.fromkeys(snapshot.tables.keys() | snapshot.unread_tables, RecordType.SQL_TABLE)
        known.update(dict.fromkeys(snapshot.views, RecordType.SQL_VIEW))
        for db in hierarchy.databases:
            for schema in hierarchy.schemas.get(db.name, []):
                await self._sync_schema_objects(hierarchy, db.name, schema.name, known)

        kept = await self._remove_gone(diff, stored, snapshot, scope)
        # A replaced table that could not be removed is kept, to be removed next sync.
        kept_tables = kept.pop("tables") | (set(diff.replaced) - replaced)
        tables = self._tables_to_save(diff, old_tables, snapshot, unreadable, moves, kept_tables=kept_tables)
        if not await self._save_state(
            next_state(snapshot, stored or {}, tables=tables, **kept),
            stored=stored,
            sync_time=get_epoch_timestamp_in_ms(),
        ):
            # The graph is consistent with the last saved state, so the next sync can
            # compare with it; fail this one so the error is seen.
            raise RuntimeError("Could not save the Snowflake sync state")
        self.logger.info("✅ [Sync] Snowflake sync completed")

    def _filters(self) -> Filters:
        return Filters(
            tables=self._filter_test("tables"),
            views=self._filter_test("views"),
            stages=self._filter_test("stages"),
            files=self._filter_test("files"),
        )

    async def _remove_gone(
        self,
        diff: CatalogDiff,
        stored: dict[str, DatabaseState] | None,
        snapshot: Snapshot,
        scope: Scope,
    ) -> dict[str, Any]:
        """Remove what nothing lists any more; returns what must stay in the saved state.

        Files go before their stages' groups, tables and views before their
        schemas' and databases' groups, and a group goes only when nothing
        under it failed to go. Without a saved state the graph is swept.
        """
        if stored is None:
            # A table that could not be removed has no state to keep; it is left to the next full sync.
            await self._sweep_records(
                RecordType.SQL_TABLE, set(snapshot.tables) | snapshot.unread_tables, scope.object_unreadable,
            )
            views_left = await self._sweep_records(RecordType.SQL_VIEW, set(snapshot.views), scope.object_unreadable)
            files_left = await self._sweep_records(RecordType.FILE, set(snapshot.files), scope.file_unreadable)
            for group_type, listed in (
                (RecordGroupType.STAGE, snapshot.stages),
                (RecordGroupType.SQL_NAMESPACE, snapshot.schemas),
                (RecordGroupType.SQL_DATABASE, snapshot.databases),
            ):
                try:
                    stale = sorted(set(await self._stored_groups(group_type)) - listed)
                except Exception as e:
                    self.sync_stats.errors += 1
                    self.logger.warning(f"Could not list {group_type.value} groups: {e}")
                    continue
                stale = [g for g in stale if not self._group_unreadable(group_type, g, scope)]
                await self._remove_groups(group_type, stale)
            return {"tables": set(), "kept_views": views_left, "kept_files": files_left, "kept_stages": set(),
                    "kept_schemas": set(), "kept_databases": set()}

        gone = gone_since(stored, snapshot, scope)
        tables_left = set(diff.missing) - await self._remove_by_external_ids(diff.missing)
        views_left = set(gone.views) - await self._remove_by_external_ids(gone.views)
        files_left = set(gone.files) - await self._remove_by_external_ids(gone.files)

        blocked_stages = {s for s in gone.stages if any(in_stage(f, s) for f in files_left)}
        stages_gone = [s for s in gone.stages if s not in blocked_stages]
        stages_left = set(gone.stages) - await self._remove_groups(RecordGroupType.STAGE, stages_gone)

        # A renamed table not written under its new name still belongs to its
        # old schema's group, and removing that group would remove its record.
        unwritten_moves = {old for old, new in diff.renamed.items() if new not in self._written_tables}
        under_failures = tables_left | views_left | stages_left | unwritten_moves
        schemas_gone = [k for k in gone.schemas if not any(f.startswith(f"{k}.") for f in under_failures)]
        schemas_left = set(gone.schemas) - await self._remove_groups(RecordGroupType.SQL_NAMESPACE, schemas_gone)
        databases_gone = [
            d for d in gone.databases
            if not any(database_of(x) == d for x in under_failures | schemas_left)
        ]
        databases_left = set(gone.databases) - await self._remove_groups(RecordGroupType.SQL_DATABASE, databases_gone)

        # Under a scope that could not be listed: kept as stored.
        stored_views = {v for state in stored.values() for v in state.views}
        stored_files = {f for state in stored.values() for f in state.files}
        stored_stages = {s for state in stored.values() for s in state.stages}
        stored_schemas = {k for state in stored.values() for k in state.schemas}
        return {
            "tables": tables_left,
            "kept_views": views_left | {v for v in stored_views - snapshot.views.keys() if scope.object_unreadable(v)},
            "kept_files": files_left | {f for f in stored_files - snapshot.files.keys() if scope.file_unreadable(f)},
            "kept_stages": stages_left | {s for s in stored_stages - snapshot.stages if scope.stage_unreadable(s)},
            "kept_schemas": schemas_left | {
                k for k in stored_schemas - snapshot.schemas if database_of(k) in scope.databases
            },
            "kept_databases": databases_left,
        }

    @staticmethod
    def _group_unreadable(group_type: RecordGroupType, external_id: str, scope: Scope) -> bool:
        if group_type is RecordGroupType.STAGE:
            return scope.stage_unreadable(external_id)
        if group_type is RecordGroupType.SQL_NAMESPACE:
            return database_of(external_id) in scope.databases or external_id in scope.schemas
        return external_id in scope.databases

    def _tables_to_save(
        self,
        diff: CatalogDiff,
        old_tables: dict[str, SnowflakeTableState],
        snapshot: Snapshot,
        unreadable: set[str],
        moves: Moves,
        *,
        kept_tables: set[str],
    ) -> dict[str, SnowflakeTableState]:
        """The table states the next sync compares against.

        A table written this sync is saved as read; one that was not keeps
        what was stored, so the next sync sees the same change again; a new
        one that was not written is left out, so it is new again. A moved
        table not yet written keeps its stored state under the new name.
        """
        tables = {fqn: old_tables[fqn] for fqn in unreadable | kept_tables if fqn in old_tables}
        moved = {old: diff.renamed[old] for old in moves}
        renamed_to = {new: old for old, new in moved.items()}
        for fqn, state in snapshot.tables.items():
            if fqn in self._written_tables:
                tables[fqn] = state
                continue
            change = diff.changed.get(fqn)
            previous = (change.previous_fqn if change else None) or fqn
            if fqn in renamed_to:
                tables[fqn] = old_tables[renamed_to[fqn]].model_copy(update={
                    "database": state.database, "schema_name": state.schema_name,
                    "table_name": state.table_name, "renamed_from": renamed_to[fqn],
                    "moved_record_id": moves[renamed_to[fqn]][0].id or "",
                })
            elif previous in old_tables and previous not in moved:
                tables.setdefault(previous, old_tables[previous])
        return tables

    async def _save_state(
        self,
        states: dict[str, DatabaseState],
        *,
        stored: dict[str, DatabaseState] | None,
        sync_time: int | None = None,
    ) -> bool:
        for attempt in range(1, LOOKUP_ATTEMPTS + 1):
            try:
                await self._state_store.save(self.account_identifier, states, previous=stored, sync_time=sync_time)
                return True
            except Exception as e:
                if attempt == LOOKUP_ATTEMPTS:
                    self.sync_stats.errors += 1
                    self.logger.error(f"Could not save the sync state: {e}")
                    return False
                await asyncio.sleep(attempt)
        return False

    async def _sync_schema_objects(
        self,
        hierarchy: SnowflakeHierarchy,
        database: str,
        schema: str,
        known: dict[str, RecordType] | None = None,
    ) -> None:
        key = schema_key(database, schema)
        table_test, view_test = self._filter_test("tables"), self._filter_test("views")
        stage_test, file_test = self._filter_test("stages"), self._filter_test("files")
        tables = [t for t in hierarchy.tables.get(key, []) if self._passes(table_test, t.fqn)]
        views = [v for v in hierarchy.views.get(key, []) if self._passes(view_test, v.fqn)]
        stages = [s for s in hierarchy.stages.get(key, []) if self._passes(stage_test, s.fqn)]

        await self._sync_tables(database, schema, tables)
        self.sync_stats.tables_new += len(tables)
        await self._sync_views(database, schema, views, known)
        self.sync_stats.views_new += len(views)
        await self._sync_stages(database, schema, stages)
        self.sync_stats.stages_synced += len(stages)

        for stage in stages:
            files = [
                f for f in hierarchy.files.get(stage.fqn, [])
                if self._passes(file_test, self._file_id(stage.fqn, f.relative_path))
            ]
            await self._sync_stage_files(database, schema, stage.name, files)
            self.sync_stats.files_new += len([f for f in files if not f.is_folder])

    @staticmethod
    def _file_id(stage_fqn: str, relative_path: str) -> str:
        return f"{stage_fqn}/{relative_path}"

    async def _ensure_scope_app_edges(self) -> None:
        """Ensure connector app edges are created according to connector scope."""
        try:
            if self.scope == ConnectorScope.TEAM.value:
                await self.data_entities_processor.ensure_team_app_edge(
                    self.connector_id
                )
                self.logger.info("Ensured team-app edge for Snowflake connector")
                return

            if not self.created_by:
                self.logger.warning(
                    "Personal Snowflake connector has no created_by; skipping user-app edges."
                )
                return

            creator_user = await self.data_entities_processor.get_user_by_user_id(
                self.created_by
            )
            if not creator_user or not getattr(creator_user, "email", None):
                self.logger.warning(
                    "Creator user not found or has no email for created_by %s; skipping user-app edges.",
                    self.created_by,
                )
                return

            app_users = self.get_app_users([creator_user])
            await self.data_entities_processor.on_new_app_users(app_users)
            self.logger.info(
                "Created user-app edge for Snowflake connector creator %s",
                self.created_by,
            )
        except Exception as e:
            self.logger.error(f"Error creating app users: {e}", exc_info=True)
            raise

    async def _create_app_user(self) -> None:
        """Backward-compatible wrapper for scope-aware app-edge creation."""
        await self._ensure_scope_app_edges()

    async def _get_permissions(self) -> list[Permission]:
        return await database_permissions(self)

    async def _record_identity(self, external_id: str) -> tuple[str, Record | None]:
        """The id to save a record under: the stored record's, or a new one.

        The upsert would keep the stored id anyway, but the weburl is built
        from the id before saving and must point at the record that exists.
        """
        existing = await self.data_entities_processor.get_record_by_external_id(
            connector_id=self.connector_id, external_record_id=external_id,
        )
        return (existing.id if existing and existing.id else str(uuid.uuid4())), existing

    def _weburl(self, record_id: str) -> str:
        return f"{self._frontend_url}/record/{record_id}" if self._frontend_url else ""

    def _table_revision(self, table: SnowflakeTable) -> str | None:
        """``LAST_ALTERED`` (moved by every DML and DDL) and the row limit, which
        decides how many rows the indexed text holds."""
        if not table.last_altered:
            return None
        return f"{table.last_altered}:{self._max_rows(self.sync_filters)}"

    async def _process_tables_generator(
        self,
        database_name: str,
        schema_name: str,
        tables: list[SnowflakeTable],
    ) -> AsyncGenerator[tuple[Record, list[Permission]], None]:
        """Each table's record, then its column records; they inherit permissions from the namespace."""
        parent_fqn = schema_key(database_name, schema_name)
        index_tables = self.indexing_filters.is_enabled(IndexingFilterKey.TABLES.value)

        for table in tables:
            if not table.fully_read or table.fqn in self._held_tables:
                # Unread, it would be written without its columns or foreign
                # keys; held, its name still belongs to another table's record.
                self._skipped_part = True
                continue
            try:
                fqn = table.fqn
                record_id, existing = await self._record_identity(fqn)
                now = get_epoch_timestamp_in_ms()
                record = SQLTableRecord(
                    id=record_id,
                    record_name=table.name,
                    record_type=RecordType.SQL_TABLE,
                    record_group_type=RecordGroupType.SQL_NAMESPACE.value,
                    external_record_group_id=parent_fqn,
                    external_record_id=fqn,
                    origin=OriginTypes.CONNECTOR.value,
                    connector_name=self.connector_name,
                    connector_id=self.connector_id,
                    mime_type=MimeTypes.SQL_TABLE.value,
                    weburl=self._weburl(record_id),
                    external_revision_id=self._table_revision(table),
                    source_created_at=existing.source_created_at if existing else (_epoch_ms(table.created_at) or now),
                    source_updated_at=_epoch_ms(table.last_altered) or now,
                    size_in_bytes=table.bytes or 0,
                    size_bytes=table.bytes,
                    row_count=table.row_count,
                    database_name=database_name,
                    schema_name=schema_name,
                    # Read before the name parts are joined, which would leave a
                    # dotted name unquoted.
                    fqn=fqn,
                    version=0 if existing else 1,
                    inherit_permissions=True,
                )

                for fk in table.foreign_keys:
                    record.related_external_records.append(
                        RelatedExternalRecord(
                            external_record_id=fk.target_fqn,
                            record_type=RecordType.SQL_TABLE,
                            record_name=fk.target_table,
                            relation_type=RecordRelations.FOREIGN_KEY,
                            source_column=fk.source_column,
                            target_column=fk.target_column,
                            child_table_name=fqn,
                            parent_table_name=fk.target_fqn,
                            constraint_name=fk.constraint_name,
                        )
                    )

                if not index_tables:
                    record.indexing_status = ProgressStatus.AUTO_INDEX_OFF.value

                yield (record, [])
                for column_record in await self._column_records(record, table, existing, with_values=index_tables):
                    yield (column_record, [])
                self._written_tables.add(fqn)
                await asyncio.sleep(0)

            except Exception as e:
                self.sync_stats.errors += 1
                self._skipped_part = True
                self.logger.error(f"Error processing table {table.name}: {e}", exc_info=True)
                continue

    async def _column_records(
        self,
        table_record: SQLTableRecord,
        table: SnowflakeTable,
        existing: Record | None,
        *,
        with_values: bool,
    ) -> list[SQLColumnRecord]:
        """The table's column records, profiled when it is worth a scan.

        A table whose columns or keys could not be read keeps its stored
        column records untouched; so does one whose stored columns
        can't be looked up. Records of columns the table no longer has are
        queued for removal.
        """
        fqn = table_record.external_record_id
        if not table.fully_read:
            self.logger.warning(f"Columns or keys of {fqn} unread; keeping its column records")
            return []
        try:
            stored = await self._stored_columns(fqn)
        except Exception as e:
            self.sync_stats.errors += 1
            self._skipped_part = True
            self.logger.warning(f"Could not look up the column records of {fqn}; keeping them: {e}")
            return []

        profile, keep_stored = await self._profile(table, stored, with_values=with_values)
        records = build_column_records(
            table_record,
            table.columns,
            column_fqn=_column_fqn,
            stats_by_name=profile.stats,
            profiles=profile.profiles,
            stored=stored,
            keep_stored=keep_stored,
            values_allowed=with_values,
        )
        current = {r.external_record_id for r in records}
        dropped = [r.id for column_fqn, r in stored.items() if column_fqn not in current and r.id]
        if dropped:
            self._dropped_columns.append((table_record.id, dropped))
        return records

    async def _profile(
        self,
        table: SnowflakeTable,
        stored: dict[str, Record],
        *,
        with_values: bool,
    ) -> tuple[TableProfile, bool]:
        """The table's profile, and whether its stored one is kept instead."""
        decision = decide_profiling(
            row_count=table.row_count,
            table_type=table.table_type,
            size_bytes=table.bytes,
            unchanged=self._profile_still_current(table, stored, with_values=with_values),
            budget_exhausted=self._profiling_budget.exhausted,
        )
        if decision is ProfilingDecision.KEEP_STORED:
            return TableProfile(), True
        if decision is ProfilingDecision.EMPTY:
            return empty_profile(table.columns), False
        self._profiling_budget.start()
        try:
            profile = await self.profiler.profile_table(
                table.database_name, table.schema_name, table.name, table.columns,
                row_count=table.row_count, with_values=with_values,
            )
        except Exception as e:
            self._profiling_budget.record_failure(e)
            self.sync_stats.errors += 1
            self.logger.warning(f"Could not profile {table.fqn}; keeping its stored profile: {e}")
            return TableProfile(), True
        self._profiling_budget.record_success()
        return profile, False

    @staticmethod
    def _profile_still_current(table: SnowflakeTable, stored: dict[str, Record], *, with_values: bool) -> bool:
        """Whether the stored column records already describe the table as it is.

        Every column has a stored record whose profile was taken after the
        table last changed, and the stored records hold values only
        when values are allowed. A scan that failed or was skipped leaves the
        older profile, so the table is scanned again on the next sync.
        """
        last_altered = _epoch_ms(table.last_altered)
        columns = [stored.get(_column_fqn(table.fqn, c.name)) for c in table.columns if c.name]
        if not columns or any(record is None for record in columns):
            return False
        if not all(profile_is_current(getattr(record, "profiled_at", None), last_altered) for record in columns):
            return False
        if with_values:
            return True
        return not any(
            getattr(record, field_name, None) is not None
            for record in columns
            for field_name in ("top_values", "min_value", "max_value")
        )

    async def _stored_columns(self, table_fqn: str) -> dict[str, Record]:
        """The table's stored column records, typed so their stats and profiles are there."""
        async with self.data_store_provider.transaction() as tx_store:
            existing = await tx_store.get_records_by_parent(
                self.connector_id, table_fqn, RecordType.SQL_COLUMN.value,
            )
        return await load_typed_columns(
            self.data_entities_processor,
            {child.external_record_id: child for child in existing},
        )

    async def _remove_dropped_columns(self) -> None:
        """Remove the records of columns tables no longer have, with what was built from them."""
        dropped, self._dropped_columns = self._dropped_columns, []
        for table_record_id, column_ids in dropped:
            try:
                await self._remove_records(column_ids, table_record_id=table_record_id)
            except Exception as e:
                self.sync_stats.errors += 1
                self.logger.warning(f"Could not remove {len(column_ids)} dropped column(s): {e}")

    @staticmethod
    def _max_rows(sync_filters: FilterCollection) -> int:
        value = sync_filters.get_value(IndexingFilterKey.MAX_ROWS_PER_TABLE, default=1000)
        return max(1, min(int(value), MAX_ROWS_PER_TABLE_LIMIT))

    async def _process_views_generator(
        self,
        database_name: str,
        schema_name: str,
        views: list[SnowflakeView],
        known: dict[str, RecordType] | None = None,
    ) -> AsyncGenerator[tuple[Record, list[Permission]], None]:
        """View records with the tables and views they read from, one at a time.

        ``known`` maps the FQN of every table and view this sync read to its
        type. A view links only to those: a name the source parser misread, or
        an object outside the sync filters, would otherwise be saved as a
        placeholder table that never goes away.
        """
        parent_fqn = schema_key(database_name, schema_name)
        index_views = self.indexing_filters.is_enabled(IndexingFilterKey.VIEWS.value)

        for view in views:
            try:
                fqn = view.fqn
                record_id, existing = await self._record_identity(fqn)
                sources = [
                    resolve_source_table(parts, database_name, schema_name)
                    for parts in parse_view_sources(view.definition)
                ]
                view.source_tables = [join_qualified_name(source) for source in sources]
                now = get_epoch_timestamp_in_ms()
                record = SQLViewRecord(
                    id=record_id,
                    record_name=view.name,
                    record_type=RecordType.SQL_VIEW,
                    record_group_type=RecordGroupType.SQL_NAMESPACE.value,
                    external_record_group_id=parent_fqn,
                    external_record_id=fqn,
                    origin=OriginTypes.CONNECTOR.value,
                    connector_name=self.connector_name,
                    connector_id=self.connector_id,
                    weburl=self._weburl(record_id),
                    mime_type=MimeTypes.SQL_VIEW.value,
                    external_revision_id=view.last_altered,
                    source_created_at=existing.source_created_at if existing else (_epoch_ms(view.created_at) or now),
                    source_updated_at=_epoch_ms(view.last_altered) or now,
                    definition=view.definition,
                    source_tables=view.source_tables,
                    database_name=database_name,
                    schema_name=schema_name,
                    fqn=fqn,
                    version=0 if existing else 1,
                    inherit_permissions=True,
                )

                for source, source_fqn in zip(sources, view.source_tables, strict=True):
                    source_type = (known or {}).get(source_fqn)
                    if source_type is None:
                        continue
                    record.related_external_records.append(
                        RelatedExternalRecord(
                            external_record_id=source_fqn,
                            record_type=source_type,
                            record_name=source[2],
                            relation_type=RecordRelations.DEPENDS_ON,
                        )
                    )

                if not index_views:
                    record.indexing_status = ProgressStatus.AUTO_INDEX_OFF.value

                yield (record, [])
                await asyncio.sleep(0)

            except Exception as e:
                self.sync_stats.errors += 1
                self._skipped_part = True
                self.logger.error(f"Error processing view {view.name}: {e}", exc_info=True)
                continue

    async def _process_stage_files_generator(
        self,
        stage_fqn: str,
        files: list[SnowflakeFile],
    ) -> AsyncGenerator[tuple[FileRecord, list[Permission]], None]:
        """File records for a stage, one at a time; they inherit permissions from the stage."""
        index_files = self.indexing_filters.is_enabled(IndexingFilterKey.STAGE_FILES.value)

        for file in files:
            try:
                if file.is_folder:
                    continue

                file_id = self._file_id(stage_fqn, file.relative_path)
                record_id, _ = await self._record_identity(file_id)
                record = FileRecord(
                    id=record_id,
                    record_name=file.file_name,
                    record_type=RecordType.FILE,
                    record_group_type=RecordGroupType.STAGE.value,
                    external_record_group_id=stage_fqn,
                    external_record_id=file_id,
                    origin=OriginTypes.CONNECTOR.value,
                    connector_name=self.connector_name,
                    connector_id=self.connector_id,
                    mime_type=get_mimetype_from_path(file.relative_path),
                    is_file=True,
                    extension=get_file_extension(file.relative_path),
                    path=file.relative_path,
                    weburl=self._weburl(record_id),
                    size_in_bytes=file.size,
                    parent_external_record_id=stage_fqn,
                    parent_record_type=None,
                    etag=file.md5,
                    external_revision_id=file.md5 or file.last_modified,
                    version=0,
                    inherit_permissions=True,
                )

                if not index_files:
                    record.indexing_status = ProgressStatus.AUTO_INDEX_OFF.value

                yield (record, [])
                await asyncio.sleep(0)

            except Exception as e:
                self.sync_stats.errors += 1
                self._skipped_part = True
                self.logger.error(f"Error processing file {file.relative_path}: {e}", exc_info=True)
                continue

    async def _sync_databases(self, databases: list[SnowflakeDatabase]) -> None:
        if not databases:
            return
        permissions = await self._get_permissions()
        groups = []
        for db in databases:
            rg = RecordGroup(
                name=db.name,
                external_group_id=db.name,
                group_type=RecordGroupType.SQL_DATABASE,
                connector_name=self.connector_name,
                connector_id=self.connector_id,
                description=db.comment or f"Snowflake Database: {db.name}",
            )
            groups.append((rg, permissions))
        await self.data_entities_processor.on_new_record_groups(groups)
        self.logger.info(f"Synced {len(groups)} databases")

    async def _sync_namespaces(
        self, database_name: str, schemas: list[SnowflakeSchema]
    ) -> None:
        if not schemas:
            return
        groups = []
        for schema in schemas:
            fqn = schema_key(database_name, schema.name)
            rg = RecordGroup(
                name=schema.name,
                external_group_id=fqn,
                group_type=RecordGroupType.SQL_NAMESPACE,
                connector_name=self.connector_name,
                connector_id=self.connector_id,
                description=schema.comment or f"Snowflake Namespace: {fqn}",
                parent_external_group_id=database_name,
                inherit_permissions=True,
            )
            groups.append((rg, []))
        await self.data_entities_processor.on_new_record_groups(groups)
        self.logger.info(f"Synced {len(groups)} namespaces in {database_name}")

    async def _sync_stages(
        self, database_name: str, schema_name: str, stages: list[SnowflakeStage]
    ) -> None:
        if not stages:
            return
        groups = []
        parent_fqn = schema_key(database_name, schema_name)
        for stage in stages:
            rg = RecordGroup(
                name=stage.name,
                external_group_id=stage.fqn,
                group_type=RecordGroupType.STAGE,
                connector_name=self.connector_name,
                connector_id=self.connector_id,
                description=stage.comment or f"Snowflake Stage: {stage.fqn}",
                parent_external_group_id=parent_fqn,
                inherit_permissions=True,
            )
            groups.append((rg, []))
        await self.data_entities_processor.on_new_record_groups(groups)
        self.logger.info(f"Synced {len(groups)} stages in {parent_fqn}")

    async def _save_in_batches(
        self, records: AsyncGenerator[tuple[Record, list[Permission]], None],
    ) -> int:
        batch: list[tuple[Record, list[Permission]]] = []
        saved = 0
        async for item in records:
            batch.append(item)
            if len(batch) >= self.batch_size:
                await self.data_entities_processor.on_new_records(batch)
                saved += len(batch)
                batch = []
        if batch:
            await self.data_entities_processor.on_new_records(batch)
            saved += len(batch)
        return saved

    async def _sync_tables(
        self, database_name: str, schema_name: str, tables: list[SnowflakeTable]
    ) -> None:
        if not tables:
            return
        try:
            saved = await self._save_in_batches(
                self._process_tables_generator(database_name, schema_name, tables)
            )
            await self._remove_dropped_columns()
        finally:
            # Never carried to another batch of tables, whose save is not this one's.
            self._dropped_columns = []
        self.logger.info(f"Synced {saved} tables in {schema_key(database_name, schema_name)}")

    async def _sync_views(
        self,
        database_name: str,
        schema_name: str,
        views: list[SnowflakeView],
        known: dict[str, RecordType] | None = None,
    ) -> None:
        if not views:
            return
        saved = await self._save_in_batches(
            self._process_views_generator(database_name, schema_name, views, known)
        )
        self.logger.info(f"Synced {saved} views in {schema_key(database_name, schema_name)}")

    async def _sync_stage_files(
        self,
        database_name: str,
        schema_name: str,
        stage_name: str,
        files: list[SnowflakeFile],
    ) -> None:
        stage_fqn = join_qualified_name((database_name, schema_name, stage_name))
        saved = await self._save_in_batches(self._process_stage_files_generator(stage_fqn, files))
        self.logger.info(f"Synced {saved} files in stage {stage_fqn}")

    @staticmethod
    def _split_fqn(fqn: str | None, kind: str) -> tuple[str, str, str]:
        try:
            database, schema, name = split_qualified_name(fqn or "", 3)
        except ValueError:
            raise HTTPException(status_code=500, detail=f"Invalid {kind} FQN") from None
        return database, schema, name

    async def stream_record(
        self,
        record: Record,
        user_id: str | None = None,
        convertTo: str | None = None
    ) -> StreamingResponse:
        """Stream a stage file's bytes, or a table's or view's definition and data as JSON."""
        try:
            if not self.data_fetcher:
                raise connector_not_ready(self.display_name)
            if record.record_type == RecordType.FILE:
                return await self._stream_file(record)
            if record.record_type == RecordType.SQL_TABLE:
                return await self._stream_table(record)
            if record.record_type == RecordType.SQL_COLUMN:
                return await self._stream_column(record)
            if record.record_type == RecordType.SQL_VIEW:
                return await self._stream_view(record)
            raise HTTPException(status_code=400, detail=f"Unsupported record type: {record.record_type}")

        except HTTPException:
            raise
        except Exception as e:
            # Never `detail=str(e)`: a Snowflake driver message carries the
            # account identifier, warehouse and role names, the full statement
            # and *.snowflakecomputing.com hostnames.
            self.logger.error(f"Error streaming record: {e}", exc_info=True)
            raise to_snowflake_stream_error(e, connector=self.display_name) from e

    async def _stream_file(self, record: Record) -> StreamingResponse:
        stage_fqn = record.external_record_group_id
        database, schema, stage = self._split_fqn(stage_fqn, "stage")
        # `record.path` is the stage-relative path as synced; deriving it
        # with str.replace would strip the stage prefix wherever it recurs
        # deeper in the path, not just at the front.
        file_path = getattr(record, "path", None) or record.external_record_id[len(stage_fqn) + 1:]

        signed_url = await self.data_fetcher.presigned_url(database, schema, stage, file_path)
        if not signed_url:
            raise not_found_at_source(self.display_name)

        return create_stream_record_response(
            stream_content(signed_url, record.id, record.record_name, connector=self.display_name),
            filename=record.record_name,
            mime_type=record.mime_type,
        )

    async def _stream_table(self, record: Record) -> StreamingResponse:
        database, schema, table = self._split_fqn(record.external_record_id, "table")

        columns = (await self.data_fetcher.columns_by_table(database, schema, table)).get(table, [])
        keys = (await self.data_fetcher.key_definitions(database, schema, table)).get(table, [])
        snapshot = SnowflakeTable(table, database, schema, columns=columns)
        apply_keys(snapshot, keys)
        foreign_keys = await self.data_fetcher.foreign_keys(database, schema, table)

        sync_filters, _ = await load_connector_filters(
            self.config_service, "snowflake", self.connector_id, self.logger
        )
        row_count = getattr(record, "row_count", None)
        ordered = row_count is not None and row_count <= ORDERED_READ_MAX_ROWS
        names, rows = await self.data_fetcher.sample_rows(
            database, schema, table,
            limit=self._max_rows(sync_filters),
            order_by=(snapshot.primary_keys if ordered else None),
            max_bytes=MAX_TABLE_DOCUMENT_BYTES,
        )
        # GET_DDL is decorative once the columns and rows are in hand; only
        # when there is nothing else to stream does a refusal here decide.
        try:
            ddl = await self.data_fetcher.get_table_ddl(database, schema, table)
        except SnowflakeFetchError:
            if not (columns or rows):
                raise
            self.logger.warning(f"GET_DDL failed for {record.external_record_id}; streaming without it")
            ddl = None

        data = {
            "table_name": table,
            "database_name": database,
            "schema_name": schema,
            "columns": [column.model_dump() for column in snapshot.columns],
            "rows": [dict(zip(names, row, strict=False)) for row in rows],
            # The field names sql_table_parser reads.
            "foreign_keys": [
                {
                    "constraint_name": fk.constraint_name,
                    "column_name": fk.source_column,
                    "foreign_table_database": fk.target_database,
                    "foreign_table_schema": fk.target_schema,
                    "foreign_table_name": fk.target_table,
                    "foreign_column_name": fk.target_column,
                }
                for fk in foreign_keys if fk.source_table == table
            ],
            "primary_keys": snapshot.primary_keys,
            "ddl": ddl,
            "connector_name": self.connector_name.value,
        }
        return self._json_response(data, f"{table}.json", MimeTypes.SQL_TABLE.value)

    async def _stream_column(self, record: Record) -> StreamingResponse:
        """A column's definition, its stored profile, and its live value counts."""
        table_fqn = getattr(record, "table_fqn", None) or record.parent_external_record_id
        database, schema, table = self._split_fqn(table_fqn, "table")
        column = record.record_name
        if not is_countable(getattr(record, "data_type", None)):
            raise HTTPException(status_code=400, detail="Values of this column type cannot be counted")

        table_record = await self.data_entities_processor.get_record_by_external_id(
            connector_id=self.connector_id, external_record_id=table_fqn,
        )
        row_count = getattr(table_record, "row_count", None)
        approximate = row_count is None or row_count > ORDERED_READ_MAX_ROWS
        # The counts group the whole table, past the rows a table stream is
        # capped to, so a column that looks personal gets none, as it gets no
        # top values in its profile.
        withheld = looks_personal(column)
        value_counts: list[dict[str, Any]] = []
        if not withheld:
            # One past the limit, to tell a cut-off list from one that fits exactly.
            value_counts = await self.data_fetcher.column_value_counts(
                database, schema, table, column, limit=MAX_COLUMN_VALUES_STREAMED + 1, approximate=approximate,
            )

        top_values = getattr(record, "top_values", None) or []
        frequencies = getattr(record, "top_value_frequencies", None) or []
        data = {
            "column_name": column,
            "table_name": table,
            "schema_name": schema,
            "database_name": database,
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
                    for value, frequency in zip(top_values, frequencies, strict=False)
                ],
            },
            "value_counts": value_counts[:MAX_COLUMN_VALUES_STREAMED],
            "value_counts_truncated": len(value_counts) > MAX_COLUMN_VALUES_STREAMED,
            "value_counts_withheld": withheld,
            "value_counts_approximate": approximate,
            "connector_name": self.connector_name.value,
        }
        return self._json_response(data, f"{table}.{column}.json", MimeTypes.SQL_COLUMN.value)

    async def _stream_view(self, record: Record) -> StreamingResponse:
        database, schema, view_name = self._split_fqn(record.external_record_id, "view")

        view = await self.data_fetcher.get_view(database, schema, view_name)
        if view is None:
            raise not_found_at_source(self.display_name)
        # INFORMATION_SCHEMA leaves out the body of a secure view the role does
        # not own; GET_DDL is the second chance at it.
        definition = view.definition or await self.data_fetcher.get_ddl("VIEW", database, schema, view_name)
        if not definition:
            # Streaming `"definition": null` would index the view as if it
            # genuinely had no body.
            self.logger.warning(f"View {view_name} has no definition. is_secure={view.is_secure}.")
            if view.is_secure:
                raise map_source_status(HttpStatusCode.FORBIDDEN.value, connector=self.display_name)
            raise RuntimeError(f"GET_DDL returned no definition for view {view_name}")

        sources = [
            resolve_source_table(parts, database, schema)
            for parts in parse_view_sources(definition)
        ]
        source_table_ddls: dict[str, str] = {}
        for source in sources:
            source_fqn = join_qualified_name(source)
            try:
                ddl = await self.data_fetcher.get_table_ddl(*source)
            except SnowflakeFetchError as e:
                self.logger.debug(f"Could not fetch DDL for source table {source_fqn}: {e}")
                continue
            if ddl:
                source_table_ddls[source_fqn] = ddl

        data = {
            "view_name": view_name,
            "database_name": database,
            "schema_name": schema,
            "definition": definition,
            "source_tables": [join_qualified_name(source) for source in sources],
            "source_table_ddls": source_table_ddls,
            "source_tables_summary": "\n".join(
                f"Table {fqn}: {ddl[:500]}..." if len(ddl) > 500 else f"Table {fqn}: {ddl}"
                for fqn, ddl in source_table_ddls.items()
            ),
            "is_secure": view.is_secure,
            "comment": view.comment or "",
        }
        return self._json_response(data, f"{view_name}.json", MimeTypes.SQL_VIEW.value)

    @staticmethod
    def _json_response(data: dict[str, Any], filename: str, mime_type: str) -> StreamingResponse:
        json_bytes = json.dumps(data, default=str).encode("utf-8")

        async def json_iterator() -> AsyncGenerator[bytes, None]:
            yield json_bytes

        return create_stream_record_response(json_iterator(), filename=filename, mime_type=mime_type)

    def get_signed_url(self, record: Record) -> str | None:
        return None

    @classmethod
    async def check_connection(cls, auth_config: dict[str, Any], logger: Logger) -> ConnectionCheckResult:
        return await check_connection(auth_config, logger)

    async def test_connection_and_access(self) -> bool:
        if not self.runner:
            return False
        try:
            await self.runner.run("SELECT CURRENT_ACCOUNT()")
            self.logger.info("Snowflake connection test successful")
            return True
        except Exception as e:
            self.logger.error(f"Connection test failed: {e}", exc_info=True)
            return False

    def handle_webhook_notification(self, notification: dict) -> None:
        raise NotImplementedError("Snowflake does not send webhook notifications; use scheduled sync.")

    async def cleanup(self) -> None:
        """Drop client references so a stale client is never reused."""
        try:
            self.logger.info("Cleaning up Snowflake connector resources")
            if self.data_source:
                await self.data_source.http.close()
            self.data_source = None
            self.runner = None
            self.data_fetcher = None
            self.warehouse = None
            self.account_identifier = None
            self.logger.info("Snowflake connector cleanup completed")
        except Exception as e:
            self.logger.error(f"Error during Snowflake connector cleanup: {e}", exc_info=True)

    async def reindex_records(self, records: list[Record]) -> None:
        """Publish reindex events for these records; their content is read again when streamed."""
        try:
            if not records:
                self.logger.info("No records to reindex")
                return
            if not self.data_fetcher:
                raise Exception("Snowflake data source not initialized")
            await self.data_entities_processor.reindex_existing_records(records)
            self.logger.info(f"Published reindex events for {len(records)} records")
        except Exception as e:
            self.logger.error(f"Error during Snowflake reindex: {e}", exc_info=True)
            raise

    async def get_filter_options(
        self,
        filter_key: str,
        page: int = 1,
        limit: int = 20,
        search: str | None = None,
        cursor: str | None = None
    ) -> FilterOptionsResponse:
        if cursor and cursor.isdigit():
            page = int(cursor)
        page = max(1, page)
        limit = max(1, min(limit, 100))

        builders = {
            "databases": self._database_options,
            "schemas": self._schema_options,
            "tables": self._table_options,
            "views": self._view_options,
            "stages": self._stage_options,
            "files": self._file_options,
        }
        builder = builders.get(filter_key)
        if builder is None:
            return _options_failure(page, limit, f"Unknown filter key: {filter_key}")
        if not self.data_fetcher:
            return _options_failure(page, limit, "Snowflake connector not initialized")
        try:
            return _paginate_options(await builder(), page, limit, search)
        except Exception as e:
            self.logger.error(f"Error getting filter options for {filter_key}: {e}", exc_info=True)
            return _options_failure(page, limit, str(e))

    async def _database_options(self) -> list[FilterOption]:
        return [FilterOption(id=db.name, label=db.name) for db in await self.data_fetcher.list_databases()]

    async def _schema_options(self) -> list[FilterOption]:
        hierarchy = await self.data_fetcher.fetch_all(
            include_database=self._filter_test("databases"), include_objects=False,
        )
        keys = [
            schema_key(db.name, schema.name)
            for db in hierarchy.databases for schema in hierarchy.schemas.get(db.name, [])
        ]
        return [FilterOption(id=key, label=key) for key in keys]

    async def _object_options(self, kind: str) -> list[FilterOption]:
        database_test, schema_test = self._filter_test("databases"), self._filter_test("schemas")
        options = []
        for db in await self.data_fetcher.list_databases():
            if not self._passes(database_test, db.name):
                continue
            try:
                names = await self.data_fetcher.object_names(db.name, kind)
            except SnowflakeFetchError as e:
                # One database the role can't read leaves the others' options.
                self.logger.warning(f"Could not list the {kind} of {db.name}: {e}")
                continue
            for fqn in names:
                _, schema, _ = split_qualified_name(fqn, 3)
                if self._passes(schema_test, schema_key(db.name, schema)):
                    options.append(FilterOption(id=fqn, label=fqn))
        return options

    async def _table_options(self) -> list[FilterOption]:
        return await self._object_options("tables")

    async def _view_options(self) -> list[FilterOption]:
        return await self._object_options("views")

    async def _stage_options(self) -> list[FilterOption]:
        return await self._object_options("stages")

    async def _file_options(self) -> list[FilterOption]:
        hierarchy = await self.data_fetcher.fetch_all(
            include_database=self._filter_test("databases"),
            include_schema=self._filter_test("schemas"),
            include_files=True,
            include_relationships=False,
        )
        stage_test = self._filter_test("stages")
        options = []
        for stage_fqn, files in hierarchy.files.items():
            if not self._passes(stage_test, stage_fqn):
                continue
            for file in files:
                if not file.is_folder:
                    file_id = self._file_id(stage_fqn, file.relative_path)
                    options.append(FilterOption(id=file_id, label=file_id))
        return options

    @classmethod
    async def create_connector(
        cls,
        logger: Logger,
        data_store_provider: DataStoreProvider,
        config_service: ConfigurationService,
        connector_id: str,
        data_entities_processor,
        **kwargs,
    ) -> "SnowflakeConnector":
        return cls(
            logger,
            data_entities_processor,
            data_store_provider,
            config_service,
            connector_id,
            kwargs.get("scope", ConnectorScope.TEAM.value),
            kwargs.get("created_by"),
        )
