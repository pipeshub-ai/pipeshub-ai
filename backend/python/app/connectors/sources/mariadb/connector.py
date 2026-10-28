"""
MariaDB Connector

Syncs databases, tables and their rows from MariaDB.
"""
import asyncio
import hashlib
import json
import os
import re
import uuid
from collections.abc import AsyncGenerator
from dataclasses import dataclass
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
    load_connector_filters,
)
from app.connectors.core.sql.catalog_diff import CatalogDiff, diff_catalog
from app.connectors.core.sql.column_records import (
    build_column_records,
    load_typed_columns,
)
from app.connectors.core.sql.permissions import database_permissions
from app.connectors.core.sql.profiling import looks_personal
from app.connectors.core.sql.record_moves import Moves
from app.connectors.core.sql.sync_lifecycle import (
    LOOKUP_ATTEMPTS,
    SqlSyncLifecycleMixin,
    restructured_fqns,
)
from app.connectors.core.sql.sync_outcome import SyncOutcome
from app.connectors.sources.mariadb.apps import MariaDBApp
from app.connectors.sources.mariadb.catalog_state import (
    MariaDBTableState,
    checkpoint_moves,
    rename_keys,
)
from app.connectors.sources.mariadb.columns import (
    ColumnFacts,
    column_fqn,
    decode_rows,
    json_default,
    sample_order,
)
from app.connectors.sources.mariadb.connection_check import (
    check_connection as check_mariadb_connection,
)
from app.connectors.sources.mariadb.tables import (
    split_table_fqn,
    structure_hash,
    table_fqn,
    table_revision,
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
from app.sources.client.mariadb.mariadb import MariaDBConfig
from app.sources.external.mariadb.mariadb_ import (
    ColumnInfo,
    DDLResult,
    ForeignKeyInfo,
    MariaDBDataSource,
    PrimaryKeyInfo,
    TableDetail,
    TableListEntry,
    TableStatsEntry,
)
from app.sources.external.mariadb.profiling import (
    MariaDBProfiler,
    ProfilingBudget,
    TableProfile,
)
from app.utils.streaming import create_stream_record_response
from app.utils.time_conversion import get_epoch_timestamp_in_ms

MAX_ROWS_PER_TABLE_LIMIT = 10000

_AUTO_INCREMENT_OPTION = re.compile(r"\s+AUTO_INCREMENT=\d+")


# Bumped when what is saved changes meaning: a state of another version is no
# state, and the next sync is a full one.
# 3: tables saved before it have no column records, and their revisions no
#    max-rows part.
SYNC_STATE_VERSION = 3
SYNC_STATE_KEY = "mariadb_tables_state"
MAX_PROFILE_ATTEMPTS = 3
MAX_TABLE_DOCUMENT_BYTES = 8 * 1024 * 1024
MAX_COLUMN_VALUES_STREAMED = 1000


def _bit_value(value: object, record: Record) -> object:
    """A BIT column's values arrive as bytes; they are numbers."""
    if isinstance(value, (bytes, bytearray)) and str(getattr(record, "data_type", "") or "").lower().startswith("bit"):
        return int.from_bytes(value, "big")
    return value


@dataclass
class MariaDBTable:
    name: str
    database_name: str
    row_count: int | None = None
    columns: list[ColumnInfo] = None
    foreign_keys: list[ForeignKeyInfo] = None
    primary_keys: list[str] = None
    stats: TableStatsEntry | None = None
    # Distinct estimates of indexed columns; kept apart from columns so a stats
    # refresh never moves the structure hash.
    cardinality: dict[str, int] = None
    
    def __post_init__(self):
        if self.columns is None:
            self.columns = []
        if self.foreign_keys is None:
            self.foreign_keys = []
        if self.primary_keys is None:
            self.primary_keys = []
        if self.cardinality is None:
            self.cardinality = {}
    
    @property
    def fqn(self) -> str:
        return table_fqn(self.database_name, self.name)

    def revision(self, max_rows: int) -> str:
        """``structure:content:max_rows``; the row limit decides what the indexed text holds."""
        return table_revision(structure_hash(self.columns, self.primary_keys, self.foreign_keys), self.stats, max_rows)


@dataclass
class SyncStats:
    tables_new: int = 0
    tables_renamed: int = 0
    tables_removed: int = 0
    errors: int = 0

    def to_dict(self) -> dict[str, int]:
        return {
            'tables_new': self.tables_new,
            'tables_renamed': self.tables_renamed,
            'tables_removed': self.tables_removed,
            'errors': self.errors,
        }

    def log_summary(self, logger) -> None:
        logger.info(
            f"📊 Sync Stats: "
            f"Tables(new={self.tables_new}, renamed={self.tables_renamed}, removed={self.tables_removed}) | "
            f"Errors={self.errors}"
        )


@ConnectorBuilder("MariaDB")\
    .in_group("MariaDB")\
    .with_description("Sync databases and tables from MariaDB")\
    .with_categories(["Database"])\
    .with_scopes([ConnectorScope.PERSONAL.value, ConnectorScope.TEAM.value])\
    .with_permission_model(PermissionModel.APP_LEVEL)\
    .with_auth([
        AuthBuilder.type(AuthType.BASIC_AUTH).fields([
            AuthField(
                name="host",
                display_name="Host",
                placeholder="localhost",
                description="MariaDB server host",
                field_type="TEXT",
                max_length=500,
                is_secret=False,
                required=True
            ),
            AuthField(
                name="port",
                display_name="Port",
                placeholder="3306",
                description="MariaDB server port",
                field_type="TEXT",
                max_length=10,
                is_secret=False,
                required=True
            ),
            AuthField(
                name="database",
                display_name="Database",
                placeholder="mydb",
                description="MariaDB database name",
                field_type="TEXT",
                max_length=200,
                is_secret=False,
                required=True
            ),
            AuthField(
                name="username",
                display_name="Username",
                placeholder="root",
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
            AuthField(
                name="sslCa",
                display_name="CA Certificate",
                placeholder="-----BEGIN CERTIFICATE-----",
                description="PEM CA bundle that signed the server's certificate. Turns on TLS "
                            "with certificate and hostname checks; leave empty for no TLS.",
                field_type="TEXTAREA",
                max_length=20000,
                min_length=0,
                required=False,
            ),
        ]),
    ])\
    .configure(lambda builder: builder
        .with_icon(IconPaths.connector_icon(Connectors.MARIADB.value))
        .add_documentation_link(DocumentationLink(
            "MariaDB Setup",
            "https://mariadb.com/docs/",
            "setup"
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
            description="Maximum number of rows to index per table (Capped at 10000)",
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
class MariaDBConnector(SqlSyncLifecycleMixin, BaseConnector):

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
            MariaDBApp(connector_id),
            logger,
            data_entities_processor,
            data_store_provider,
            config_service,
            connector_id,
            scope,
            created_by,
        )
        self.connector_id = connector_id
        self.connector_name = Connectors.MARIADB
        self.data_source: MariaDBDataSource | None = None
        self.database_name: str | None = None
        self.batch_size = 100
        self.rate_limiter = AsyncLimiter(25, 1)
        # The base class keeps scope and created_by; they must survive this init.
        self.connector_scope: str | None = scope
        self.sync_filters: FilterCollection = FilterCollection()
        self.indexing_filters: FilterCollection = FilterCollection()
        self.sync_stats: SyncStats = SyncStats()
        # What the last sync changed, for whatever runs after it.
        self.sync_outcome: SyncOutcome = SyncOutcome()
        self._written_tables: set[str] = set()
        self.profiler: MariaDBProfiler | None = None
        self._profiling_budget = ProfilingBudget()
        # Tables whose column records were written with a profile this sync.
        self._profiled: set[str] = set()
        # Whether the stored column records may hold values, and when they were
        # saved (from the sync point).
        self._stored_values_allowed: bool | None = None
        self._stored_sync_time: int | None = None
        self._frontend_url: str = os.getenv("FRONTEND_PUBLIC_URL", "").rstrip("/")
        self._table_filter_cache: list[FilterOption] = []
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
        """Convert User objects to AppUser objects for MariaDB connector."""
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
        """Establish user/team relationships with this MariaDB connector app.

        For TEAM scope: creates a single team-app edge so all org members get access.
        For PERSONAL scope: creates a user-app edge only for the creator.
        """
        try:
            if self.scope == ConnectorScope.TEAM.value:
                await self.data_entities_processor.ensure_team_app_edge(
                    self.connector_id
                )
                self.logger.info("Ensured team-app edge for MariaDB connector")
            else:
                if self.created_by:
                    creator_user = await self.data_entities_processor.get_user_by_user_id(self.created_by)
                    if creator_user and getattr(creator_user, "email", None):
                        app_users = self.get_app_users([creator_user])
                        await self.data_entities_processor.on_new_app_users(app_users)
                        self.logger.info(
                            "Created user-app edge for MariaDB connector creator %s",
                            self.created_by,
                        )
                    else:
                        self.logger.warning(
                            "Creator user not found or has no email for created_by %s; skipping user-app edges.",
                            self.created_by,
                        )
                else:
                    self.logger.warning(
                        "Personal MariaDB connector has no created_by; skipping user-app edges."
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
                self.logger.error("MariaDB configuration not found")
                return False

            auth_config = config.get("auth") or {}

            host = auth_config.get("host")
            port = int(auth_config.get("port", 3306))
            database = auth_config.get("database")
            user = auth_config.get("username") 
            password = auth_config.get("password", "")

            if not all([host, database, user]):
                self.logger.error("Missing required MariaDB configuration (host, database, username)")
                return False

            self.database_name = database
            self.scope = config.get("scope", self.scope or ConnectorScope.TEAM.value)
            self.connector_scope = self.scope
            self.created_by = config.get("created_by", self.created_by)

            mariadb_config = MariaDBConfig(
                host=host,
                port=port,
                database=database,
                user=user,
                password=password,
                ssl_ca_pem=auth_config.get("sslCa") or None,
            )
            client = mariadb_config.create_client()
            await client.connect()

            self.data_source = MariaDBDataSource(client)
            self.profiler = MariaDBProfiler(client)

            self.logger.info("MariaDB connector initialized successfully")
            return True

        except Exception as e:
            self.logger.error(f"Failed to initialize MariaDB connector: {e}", exc_info=True)
            return False

    async def run_sync(self) -> None:
        """Bring the graph in step with the database."""
        try:
            self.logger.info("📦 [Sync] Starting MariaDB sync...")
            # Before anything that can raise: a failed sync must not report the
            # previous one's changes.
            self.sync_stats = SyncStats()
            self.sync_outcome = SyncOutcome()
            self._profiling_budget = ProfilingBudget()
            self._profiled = set()
            self._written_tables = set()

            if not self.data_source:
                raise ConnectionError("MariaDB connector not initialized")

            self.sync_filters, self.indexing_filters = await load_connector_filters(
                self.config_service, "mariadb", self.connector_id, self.logger
            )
            try:
                await self._sync_catalog()
            finally:
                # Also after a failure: the tables written before it still changed.
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

    async def _sync_catalog(self) -> None:
        """List and read the tables, follow renames, write what changed, remove what is gone, save the state."""
        await self._create_app_users()
        if not self.database_name:
            raise ValueError("Database name must be configured for MariaDB connector")
        await self._ensure_database_record_groups([self.database_name])

        stored = await self._load_state()
        old = stored or {}
        listed = await self._listed_tables()
        tables, unread = await self._load_tables(listed)
        by_fqn = {t.fqn: t for t in tables}
        current = self._current_states(tables, await self._table_ids())

        await self._finish_interrupted_moves(old)
        keyed_old, keyed_current = rename_keys(old, current)
        diff = diff_catalog(keyed_old, keyed_current, unreadable=unread & old.keys())
        # Paired by shape only: maybe a lookalike, so described again even when
        # its write waits for a later sync (the stored shape is dropped).
        shape_paired = {
            old_fqn for old_fqn in diff.renamed
            if keyed_old[old_fqn].key is not None and keyed_old[old_fqn].key[0] == "fingerprint"
        }
        self.logger.info(
            f"📊 Changes: new={len(diff.added)}, changed={len(diff.changed)}, renamed={len(diff.renamed)}, "
            f"gone={len(diff.missing)}, replaced={len(diff.replaced)}, unread={len(unread)}"
        )

        replaced = await self._remove_by_external_ids(diff.replaced)

        async def checkpoint(moving: dict[str, str], record_ids: dict[str, str]) -> bool:
            # Only the moves are new: the values setting and sync time stay the
            # last sync's, so a sync cut off after this still sees a switch.
            return await self._save_state(
                checkpoint_moves(old, moving, record_ids, shape_paired=shape_paired), checkpoint=True,
            )

        moved, moves = await self._move_renamed_tables(
            diff.renamed, blocked=set(diff.replaced) - replaced, checkpoint=checkpoint,
        )
        self.sync_stats.tables_renamed += len(moved)
        # A rename that waits keeps both names: the new one is not written, and
        # a new table under the old one would take over the record that did not move.
        held = {
            name for old_fqn, new in diff.renamed.items() if old_fqn not in moved for name in (old_fqn, new)
        }
        await self._follow_renames(diff, moved, moves)
        self.sync_outcome.restructured_tables |= restructured_fqns(diff, current) - held

        changed = (set(diff.added) | set(diff.changed)) - held
        relinked = {
            t.fqn for t in tables
            if t.fqn not in changed and t.fqn not in held
            and any(target in diff.added for target in self._fk_targets(t))
        }
        written = await self._write_tables([by_fqn[fqn] for fqn in sorted(changed | relinked)])
        self._written_tables = written
        self.sync_stats.tables_new += len(written & set(diff.added))

        # Unchanged tables whose column records are behind: not profiled, or
        # holding values that are no longer allowed (or not those now allowed).
        values_switched = self._stored_values_allowed is not None and (
            self._stored_values_allowed != self._values_allowed()
        )
        behind = [
            by_fqn[fqn] for fqn in diff.unchanged
            if fqn not in relinked and (values_switched or not old[fqn].profiled)
        ]
        vanished: set[str] = set()
        if behind:
            self.logger.info(f"Rewriting the column records of {len(behind)} unchanged tables")
            vanished = await self._rewrite_columns(behind)

        retry: set[str] = set()
        if stored is None:
            # One the sweep could not remove gets an empty state: missing next sync, and removed then.
            retry = await self._sweep_records(RecordType.SQL_TABLE, set(listed))
            kept: set[str] = set()
        elif unread - old.keys():
            # A table that could not be read under a new name may be one of the
            # missing ones, renamed: nothing is removed until it reads.
            self.logger.info(f"{len(unread - old.keys())} new table(s) unread; removing no missing table this sync")
            kept = set(diff.missing)
        else:
            kept = set(diff.missing) - await self._remove_by_external_ids(diff.missing)
        self.sync_stats.tables_removed += len(diff.missing) - len(kept)
        kept |= set(diff.replaced) - replaced

        states = self._next_state(
            diff, old, current, by_fqn, moves,
            rewritten={t.fqn for t in behind} - vanished, vanished=vanished, kept=kept | unread,
            values_switched=values_switched, shape_paired=shape_paired,
        )
        states.update({fqn: MariaDBTableState() for fqn in retry if fqn not in states})
        if not await self._save_state(states):
            # The graph matches the last saved state, so the next sync compares
            # with it; failing this one shows the error.
            raise RuntimeError("Could not save the MariaDB sync state")
        self.logger.info("✅ [Sync] MariaDB sync completed")

    async def _table_ids(self) -> dict[str, int]:
        """InnoDB table ids by name; none when they cannot be read."""
        try:
            return await self.data_source.read_table_ids(self.database_name)
        except Exception as e:
            self.logger.info(
                f"InnoDB table ids not readable ({e}); renames are followed by table shape only. "
                "Grant PROCESS to follow renames that also change the table."
            )
            return {}

    def _current_states(self, tables: list["MariaDBTable"], ids: dict[str, int]) -> dict[str, MariaDBTableState]:
        def id_of(fqn: str) -> int | None:
            try:
                database, name = split_table_fqn(fqn)
            except ValueError:
                return None
            return ids.get(name) if database == self.database_name else None

        return {
            t.fqn: MariaDBTableState.of(t, max_rows=self._max_rows(), table_id=ids.get(t.name), id_of=id_of)
            for t in tables
        }

    @staticmethod
    def _fk_targets(table: "MariaDBTable") -> set[str]:
        return {
            table_fqn(fk.foreign_database or table.database_name, fk.foreign_table_name)
            for fk in table.foreign_keys if fk.foreign_table_name
        }

    def _next_state(
        self,
        diff: CatalogDiff,
        old: dict[str, MariaDBTableState],
        current: dict[str, MariaDBTableState],
        by_fqn: dict[str, "MariaDBTable"],
        moves: Moves,
        *,
        rewritten: set[str],
        vanished: set[str],
        kept: set[str],
        values_switched: bool,
        shape_paired: set[str] = frozenset(),
    ) -> dict[str, MariaDBTableState]:
        """The table states the next sync compares against.

        A table written this sync (or its columns) is saved as read; one that
        was not keeps what was stored, so the next sync sees the same change;
        a new one that was not written is left out, so it is new again. A
        moved table not yet written keeps its stored state under the new name.
        """
        def carried(state: MariaDBTableState) -> MariaDBTableState:
            # Not rewritten this sync: if values were switched, its columns are behind.
            return state.model_copy(update={"profiled": False}) if values_switched else state

        states = {fqn: carried(old[fqn]) for fqn in kept if fqn in old}
        renamed_to = {diff.renamed[old_fqn]: old_fqn for old_fqn in moves}
        unchanged = set(diff.unchanged)
        for fqn, state in current.items():
            change = diff.changed.get(fqn)
            previous = (change.previous_fqn if change else None) or fqn
            before = old.get(previous)
            if fqn in vanished:
                continue
            if fqn in self._written_tables or fqn in rewritten:
                states[fqn] = self._with_profile(state, by_fqn[fqn], before)
            elif fqn in renamed_to:
                origin = renamed_to[fqn]
                states[fqn] = carried(old[origin]).model_copy(update={
                    "renamed_from": origin, "moved_record_id": moves[origin][0].id or "",
                    **({"shape": ""} if origin in shape_paired else {}),
                })
            elif fqn in unchanged and before is not None:
                # Its identity as read now; its revision is the stored one.
                states[fqn] = carried(state.model_copy(update={
                    "profiled": before.profiled, "profile_failures": before.profile_failures,
                }))
            elif before is not None and previous not in renamed_to.values():
                states.setdefault(previous, carried(before))
        return states

    def _get_filter_values(self) -> tuple[list[str] | None, str]:
        table_filter = self.sync_filters.get("tables")
        if not table_filter or not table_filter.value:
            return None, MultiselectOperator.IN.value
        return table_filter.value, table_filter.operator_value

    async def _ensure_database_record_groups(self, databases: list[str]) -> None:
        """Create a record group for each database being synced."""
        permissions = await self._get_permissions()
        groups = []
        for db_name in databases:
            rg = RecordGroup(
                name=db_name,
                external_group_id=db_name,
                group_type=RecordGroupType.SQL_DATABASE,
                connector_name=self.connector_name,
                connector_id=self.connector_id,
                description=f"MariaDB Database: {db_name}",
            )
            groups.append((rg, permissions))
        if groups:
            await self.data_entities_processor.on_new_record_groups(groups)
            self.logger.info(f"Ensured {len(groups)} database record groups exist")

    async def _listed_tables(self) -> dict[str, TableStatsEntry]:
        """The configured database's base tables the sync filters keep, with their stats.

        Raises when the catalog cannot be read: an empty answer would read as
        every table dropped.
        """
        if not self.database_name:
            raise ValueError("Database name must be configured for MariaDB connector")
        response = await self.data_source.get_table_stats([self.database_name])
        if not response.success:
            raise ConnectionError(f"Failed to list MariaDB tables: {response.error}")
        selected, filter_op = self._get_filter_values()
        excluding = filter_op == MultiselectOperator.NOT_IN.value
        listed: dict[str, TableStatsEntry] = {}
        for raw in response.data or []:
            stat = TableStatsEntry.model_validate(raw)
            if not stat.table_name:
                continue
            database = stat.database_name or self.database_name
            fqn = table_fqn(database, stat.table_name)
            # A filter saved before ids were quote-aware names "a.b" plainly.
            chosen = fqn in selected or f"{database}.{stat.table_name}" in selected if selected else False
            if selected and chosen == excluding:
                continue
            listed[fqn] = stat
        return listed

    async def _load_tables(self, listed: dict[str, TableStatsEntry]) -> tuple[list[MariaDBTable], set[str]]:
        """The listed tables with their columns, keys and FKs, from one catalog read; and those unread.

        A failed read fails the sync: with renames followed, an
        unread catalog could make a renamed table's old name look gone. A
        listed table the catalog read does not show (renamed or dropped in
        between) is unread: neither written nor removed, its state kept.
        """
        if not listed:
            return [], set()
        try:
            catalog = await self.data_source.read_catalog(self.database_name)
        except Exception as e:
            raise ConnectionError(f"Could not read the catalog of {self.database_name}: {e}") from e
        tables = []
        unread: set[str] = set()
        for fqn, stat in listed.items():
            entry = catalog.get(stat.table_name)
            if entry is None or not entry.columns:
                unread.add(fqn)
                continue
            tables.append(MariaDBTable(
                name=stat.table_name,
                database_name=stat.database_name or self.database_name,
                row_count=stat.n_live_tup,
                columns=entry.columns,
                foreign_keys=entry.foreign_keys,
                primary_keys=entry.primary_keys,
                stats=stat,
                cardinality=entry.cardinality,
            ))
        return tables, unread

    def _max_rows(self, sync_filters: FilterCollection | None = None) -> int:
        filters = sync_filters or self.sync_filters
        value = filters.get_value(IndexingFilterKey.MAX_ROWS_PER_TABLE, default=1000) if filters else 1000
        return max(1, min(int(value or 1000), MAX_ROWS_PER_TABLE_LIMIT))

    def _values_allowed(self) -> bool:
        """Top values and extremes are row data: stored only while table indexing is on."""
        return not self.indexing_filters or self.indexing_filters.is_enabled(IndexingFilterKey.TABLES.value)

    async def _get_permissions(self) -> list[Permission]:
        return await database_permissions(self)

    async def _build_record(self, table: MariaDBTable) -> tuple[SQLTableRecord, Record | None]:
        """The table's record, keeping the stored record's id and created time; and the stored record, if any.

        The revision is ``structure:content:max_rows``. The content part is a
        hash of what indexing receives, so a table whose catalog markers moved
        with no change to its rows (InnoDB empties ``UPDATE_TIME`` on a restart)
        keeps its revision and is not indexed again. When the rows cannot be
        read, the catalog marker stands in.
        """
        fqn = table.fqn
        existing = await self.data_entities_processor.get_record_by_external_id(
            connector_id=self.connector_id, external_record_id=fqn,
        )
        record_id = existing.id if existing and existing.id else str(uuid.uuid4())
        max_rows = self._max_rows()
        revision = table.revision(max_rows)
        fingerprint = await self._table_fingerprint(table, max_rows)
        if fingerprint:
            revision = f"{revision.partition(':')[0]}:{fingerprint}:{max_rows}"
        changed = existing is None or existing.external_revision_id != revision
        now = get_epoch_timestamp_in_ms()
        record = SQLTableRecord(
            id=record_id,
            record_name=table.name,
            record_type=RecordType.SQL_TABLE,
            record_group_type=RecordGroupType.SQL_DATABASE.value,
            external_record_group_id=table.database_name,
            external_record_id=fqn,
            external_revision_id=revision,
            origin=OriginTypes.CONNECTOR.value,
            connector_name=self.connector_name,
            connector_id=self.connector_id,
            mime_type=MimeTypes.SQL_TABLE.value,
            weburl=f"{self._frontend_url}/record/{record_id}" if self._frontend_url else "",
            source_created_at=(existing.source_created_at if existing else None) or now,
            source_updated_at=now if changed else (existing.source_updated_at or now),
            database_name=table.database_name,
            fqn=fqn,
            row_count=table.row_count,
            # The processor bumps it when the revision changes.
            version=0 if existing else 1,
            inherit_permissions=True,
        )
        for fk in table.foreign_keys:
            if not fk.foreign_table_name:
                continue
            target_fqn = table_fqn(fk.foreign_database or table.database_name, fk.foreign_table_name)
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
        return record, existing

    async def _table_fingerprint(self, table: MariaDBTable, max_rows: int) -> str | None:
        """Hash of the content indexing would receive for this table, or None if unreadable."""
        try:
            database, name = split_table_fqn(table.fqn)
            content = await self._read_table_content(database, name, max_rows=max_rows, row_estimate=table.row_count)
        except Exception as e:
            self.logger.warning(f"Could not read {table.fqn} to check it for changes, so it will be indexed again: {e}")
            return None
        # information_schema returns foreign keys in no fixed order.
        content["foreign_keys"] = sorted(
            content["foreign_keys"], key=lambda fk: json.dumps(fk, sort_keys=True, default=str)
        )
        # SHOW CREATE TABLE carries the next AUTO_INCREMENT value, which moves on
        # every insert, even one past the row limit that never reaches the index.
        content["ddl"] = _AUTO_INCREMENT_OPTION.sub("", content["ddl"] or "")
        serialized = json.dumps(content, sort_keys=True, default=json_default)
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()

    async def _stored_columns(self, table_fqn: str, *, typed: bool) -> dict[str, Record]:
        """The table's stored column records; ``typed`` loads their stats and profiles too.

        Raises when they cannot all be loaded so: the table is not written this
        sync and is tried again, rather than writing its columns back without
        their stats. A table whose profile is taken next time needs no load.
        """
        async with self.data_store_provider.transaction() as tx_store:
            existing = await tx_store.get_records_by_parent(
                self.connector_id, table_fqn, RecordType.SQL_COLUMN.value,
            )
        by_fqn = {child.external_record_id: child for child in existing}
        if not typed or not by_fqn:
            return by_fqn
        return await load_typed_columns(self.data_entities_processor, by_fqn)

    async def _column_records(
        self, table_record: Record, table: MariaDBTable, *, existed: bool,
    ) -> tuple[list[SQLColumnRecord], list[str]]:
        """The table's column records with their profile, and the
        ids of stored ones for columns it no longer has.

        A profile not taken (budget spent, scan failed) keeps the stored stats
        and profiles, and the table is not counted as profiled: its column
        records are rewritten next sync.
        """
        profile, keep_stored = await self._profile(table)
        stored = await self._stored_columns(table.fqn, typed=keep_stored) if existed else {}
        records = build_column_records(
            table_record,
            [ColumnFacts.of(column) for column in table.columns],
            column_fqn=column_fqn,
            stats_by_name=profile.stats,
            profiles=profile.profiles,
            stored=stored,
            keep_stored=keep_stored,
            values_allowed=self._values_allowed(),
        )
        if not keep_stored:
            self._profiled.add(table.fqn)
        current = {r.external_record_id for r in records}
        dropped = [r.id for fqn, r in stored.items() if fqn not in current and r.id]
        return records, dropped

    async def _profile(self, table: MariaDBTable) -> tuple[TableProfile, bool]:
        """The table's profile, and whether its stored one is kept instead."""
        if not table.columns:
            self._profiled.add(table.fqn)
            return TableProfile(), False
        if not self.profiler or self._profiling_budget.exhausted:
            return TableProfile(), True
        self._profiling_budget.start()
        try:
            profile = await self.profiler.profile_table(
                table.database_name, table.name, table.columns,
                row_estimate=table.row_count, cardinality=table.cardinality,
                with_values=self._values_allowed(),
            )
        except Exception as e:
            self._profiling_budget.record_failure(e)
            self.sync_stats.errors += 1
            self.logger.warning(f"Could not profile {table.fqn}; keeping its stored profile: {e}")
            return TableProfile(), True
        self._profiling_budget.record_success()
        return profile, False

    async def _rewrite_columns(self, tables: list[MariaDBTable]) -> set[str]:
        """Write the column records of tables whose record stays as it is.

        For a table whose profile was not taken, or every table when the
        values allowed in column records changed: the table is not re-indexed.
        Returns the tables with no record any more: they are written as new
        next sync.
        """
        missing: set[str] = set()
        for table in tables:
            try:
                record = await self.data_entities_processor.get_record_by_external_id(
                    connector_id=self.connector_id, external_record_id=table.fqn,
                )
                if record is None:
                    missing.add(table.fqn)
                    continue
                columns, dropped = await self._column_records(record, table, existed=True)
                if columns:
                    await self.data_entities_processor.on_new_records([(c, []) for c in columns])
                if dropped:
                    await self._remove_records(dropped, table_record_id=record.id)
            except Exception as e:
                self._profiled.discard(table.fqn)
                self.sync_stats.errors += 1
                self.logger.error(f"Error writing the columns of {table.fqn}: {e}", exc_info=True)
        return missing

    def _with_profile(
        self, state: MariaDBTableState, table: MariaDBTable, previous: MariaDBTableState | None = None,
    ) -> MariaDBTableState:
        """The table's state after this sync wrote it (or its columns).

        A failed profile counts against the same revision; at the cap the
        table counts as profiled, so it is not rescanned until it changes.
        """
        if table.fqn in self._profiled:
            return state.model_copy(update={"profiled": True, "profile_failures": 0})
        failures = 1 + (previous.profile_failures if previous and previous.revision == state.revision else 0)
        return state.model_copy(update={
            "profiled": failures >= MAX_PROFILE_ATTEMPTS, "profile_failures": failures,
        })

    async def _write_batch(self, batch: list[tuple[Record, list[Permission]]]) -> set[str]:
        """Write new records; a failed batch is counted, and its tables are new again next sync."""
        try:
            await self.data_entities_processor.on_new_records(batch)
        except Exception as e:
            self.sync_stats.errors += 1
            self.logger.error(f"Could not write {len(batch)} new records: {e}", exc_info=True)
            return set()
        return {record.external_record_id for record, _ in batch if record.record_type == RecordType.SQL_TABLE}

    async def _write_tables(self, tables: list[MariaDBTable]) -> set[str]:
        """Write the tables' records and their column records; returns the tables written.

        A new table goes in a batch with its columns right after it; a changed
        one is updated, then its columns written and its dropped columns
        removed. A table whose columns could not be written is not written:
        its saved state stays, so the next sync tries again.
        """
        written: set[str] = set()
        batch: list[tuple[Record, list[Permission]]] = []
        for table in tables:
            try:
                record, existing = await self._build_record(table)
                existed = existing is not None
                columns, dropped = await self._column_records(record, table, existed=existed)
                if existed:
                    # Columns first: the update publishes the table for indexing,
                    # and a column failure after it would publish it again next sync.
                    if columns:
                        await self.data_entities_processor.on_new_records([(c, []) for c in columns])
                    if dropped:
                        await self._remove_records(dropped, table_record_id=record.id)
                    # The catalog flags a change on a restart alone; the content says
                    # whether there is anything to embed.
                    if not (
                        existing.indexing_status == ProgressStatus.COMPLETED.value
                        and existing.external_revision_id == record.external_revision_id
                    ):
                        await self.data_entities_processor.on_record_content_update(record)
                    else:
                        # Written again as it is, which queues nothing: a full sync
                        # counts the records it writes, and unlinks the rest after.
                        await self.data_entities_processor.on_new_records([(record, [])])
                    written.add(table.fqn)
                else:
                    batch.append((record, []))
                    batch.extend((c, []) for c in columns)
            except Exception as e:
                self._profiled.discard(table.fqn)
                self.sync_stats.errors += 1
                self.logger.error(f"Error writing table {table.fqn}: {e}", exc_info=True)
                continue
            if len(batch) >= self.batch_size:
                written |= await self._write_batch(batch)
                batch = []
            await asyncio.sleep(0)
        if batch:
            written |= await self._write_batch(batch)
        self.logger.info(f"Wrote {len(written)} tables")
        return written

    async def _read_table_content(
        self, database: str, table: str, *, max_rows: int | None = None, row_estimate: int | None,
    ) -> dict[str, Any]:
        """The table as indexing receives it: columns, rows, keys and DDL. Raises a
        stream error when any part cannot be read.

        Without ``max_rows`` the connector's filters are loaded, once the table
        is known to exist.
        """
        table_info_response = await self.data_source.get_table_info(table, database)
        if not table_info_response.success:
            self.logger.error(f"❌ Failed to get table info for {database}.{table}: {table_info_response.error}")
            raise to_sql_response_error(
                table_info_response.error, connector=self.display_name
            )
        detail = TableDetail.model_validate(table_info_response.data)
        columns: list[ColumnInfo] = detail.columns
        self.logger.debug(f"✅ Retrieved {len(columns)} columns for {database}.{table}")

        # These queries only fail on a driver error — a table with no
        # constraints succeeds with an empty list — so degrading here
        # would stream a table whose keys were refused, not absent.
        fks_response = await self.data_source.get_foreign_keys(table, database)
        if not fks_response.success:
            self.logger.error(f"❌ Failed to get foreign keys for {database}.{table}: {fks_response.error}")
            raise to_sql_response_error(
                fks_response.error, connector=self.display_name
            )
        foreign_keys = [
            ForeignKeyInfo.model_validate(fk) for fk in (fks_response.data or [])
        ]

        pks_response = await self.data_source.get_primary_keys(table, database)
        if not pks_response.success:
            self.logger.error(f"❌ Failed to get primary keys for {database}.{table}: {pks_response.error}")
            raise to_sql_response_error(
                pks_response.error, connector=self.display_name
            )
        primary_keys: list[str] = [
            PrimaryKeyInfo.model_validate(pk).column_name
            for pk in (pks_response.data or [])
        ]

        if max_rows is None:
            sync_filters, _ = await load_connector_filters(
                self.config_service, "mariadb", self.connector_id, self.logger
            )
            max_rows = self._max_rows(sync_filters)
        try:
            rows = await self.data_source.fetch_table_rows(
                database, table, limit=max_rows,
                order_by=sample_order(columns, primary_keys, row_estimate),
                byte_columns=[c.name for c in columns if c.name],
                max_bytes=MAX_TABLE_DOCUMENT_BYTES,
            )
            rows = decode_rows(rows, columns)
        except Exception as e:
            self.logger.error(f"❌ Failed to read rows for {database}.{table}: {e}")
            raise to_sql_stream_error(e, connector=self.display_name) from e

        ddl_response = await self.data_source.get_table_ddl(table, database)
        if not ddl_response.success:
            self.logger.error(f"❌ Failed to get DDL for {database}.{table}: {ddl_response.error}")
            raise to_sql_response_error(
                ddl_response.error, connector=self.display_name
            )
        ddl = DDLResult.model_validate(ddl_response.data).ddl

        return {
            "table_name": table,
            "database_name": database,
            "columns": [col.model_dump() for col in columns],
            "rows": rows,
            "foreign_keys": [fk.model_dump() for fk in foreign_keys],
            "primary_keys": primary_keys,
            "ddl": ddl,
            "connector_name": self.connector_name.value if hasattr(self.connector_name, "value") else str(self.connector_name),
        }

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
                try:
                    database, table = split_table_fqn(record.external_record_id)
                except ValueError:
                    # An id written before ids were quote-aware: database.table, split at the first dot.
                    database, _, table = record.external_record_id.partition(".")
                    if not table:
                        raise HTTPException(status_code=500, detail="Invalid table FQN") from None

                row_count = getattr(record, "row_count", None)
                data = await self._read_table_content(
                    database, table, row_estimate=row_count if isinstance(row_count, int) else None,
                )

                json_bytes = json.dumps(data, default=json_default).encode("utf-8")

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
        table_id = getattr(record, "table_fqn", None) or record.parent_external_record_id or ""
        try:
            database, table = split_table_fqn(table_id)
        except ValueError:
            raise HTTPException(status_code=500, detail="Invalid table FQN") from None
        column = record.record_name
        # The counts group the whole table, past the rows a table stream is
        # capped to, so a column that looks personal gets none, as it gets no
        # top values in its profile.
        withheld = looks_personal(column)
        value_counts: list[dict[str, Any]] = []
        if not withheld:
            try:
                # One past the limit, to tell a cut-off list from one that fits exactly.
                value_counts = await self.data_source.column_value_counts(
                    database, table, column, MAX_COLUMN_VALUES_STREAMED + 1,
                )
            except Exception as e:
                self.logger.error(f"❌ Failed to read values of {table_id}.{column}: {e}")
                raise to_sql_stream_error(e, connector=self.display_name) from e
        top_values = getattr(record, "top_values", None) or []
        frequencies = getattr(record, "top_value_frequencies", None) or []
        data = {
            "column_name": column,
            "table_name": table,
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
                    {"value": value, "frequency": frequency} for value, frequency in zip(top_values, frequencies)
                ],
            },
            "value_counts": [
                {"value": _bit_value(row.get("value"), record), "count": row.get("count")}
                for row in value_counts[:MAX_COLUMN_VALUES_STREAMED]
            ],
            "value_counts_truncated": len(value_counts) > MAX_COLUMN_VALUES_STREAMED,
            "value_counts_withheld": withheld,
            "connector_name": self.connector_name.value if hasattr(self.connector_name, "value") else str(self.connector_name),
        }
        json_bytes = json.dumps(data, default=json_default).encode("utf-8")

        async def json_iterator() -> AsyncGenerator[bytes, None]:
            yield json_bytes

        return create_stream_record_response(
            json_iterator(), filename=f"{column}.json", mime_type=MimeTypes.SQL_COLUMN.value
        )

    @classmethod
    async def check_connection(cls, auth_config: dict[str, Any], logger: Logger) -> ConnectionCheckResult:
        return await check_mariadb_connection(auth_config, logger)

    async def test_connection_and_access(self) -> bool:
        if not self.data_source:
            return False
        try:
            response = await self.data_source.test_connection()
            if response.success:
                self.logger.info("MariaDB connection test successful")
                return True
            self.logger.error(f"Connection test failed: {response.error}")
            return False
        except Exception as e:
            self.logger.error(f"Connection test failed: {e}", exc_info=True)
            return False

    async def cleanup(self) -> None:
        try:
            self.logger.info("Starting MariaDB connector cleanup...")

            if self.data_source:
                client = self.data_source.get_client()
                if client:
                    await client.close()
                self.data_source = None

            self.database_name = None

            self.logger.info("MariaDB connector cleanup completed")

        except Exception as e:
            self.logger.error(f"Error during MariaDB connector cleanup: {e}", exc_info=True)

    def get_signed_url(self, record: Record) -> str | None:
        return None

    def handle_webhook_notification(self, notification: dict) -> None:
        raise NotImplementedError(
            "MariaDB does not support webhook notifications. "
            "Use scheduled sync for change tracking."
        )

    async def reindex_records(self, records: list[Record]) -> None:
        try:
            if not records:
                self.logger.info("No records to reindex")
                return

            self.logger.info(f"Starting reindex for {len(records)} MariaDB records")

            if not self.data_source:
                self.logger.error("Data source not initialized. Call init() first.")
                raise Exception("MariaDB data source not initialized")

            await self.data_entities_processor.reindex_existing_records(records)
            self.logger.info(f"Published reindex events for {len(records)} records")

        except Exception as e:
            self.logger.error(f"Error during MariaDB reindex: {e}", exc_info=True)
            raise

    async def _load_state(self) -> dict[str, MariaDBTableState] | None:
        """The saved table states, or None when there is none of this version (a full sync follows).

        Keys are never parsed: an id saved by an earlier version that does not
        parse is still compared, found unlisted, and its record removed.
        """
        self._stored_values_allowed = self._stored_sync_time = None
        stored = await self.tables_sync_point.read_sync_point(SYNC_STATE_KEY)
        if not stored or stored.get("state_version") != SYNC_STATE_VERSION:
            return None
        self._stored_values_allowed = stored.get("values_allowed")
        self._stored_sync_time = stored.get("last_sync_time")
        raw = json.loads(stored.get("table_states") or "{}")
        return {fqn: MariaDBTableState.model_validate(state) for fqn, state in raw.items()}

    async def _save_state(self, states: dict[str, MariaDBTableState], *, checkpoint: bool = False) -> bool:
        """Save the table states; returns whether they were saved (tried a few times).

        A checkpoint keeps the last sync's values setting and time: what it
        says about the column records has not changed yet.
        """
        value = {
            "state_version": SYNC_STATE_VERSION,
            "values_allowed": self._stored_values_allowed if checkpoint else self._values_allowed(),
            "last_sync_time": self._stored_sync_time if checkpoint else get_epoch_timestamp_in_ms(),
            "table_states": json.dumps({fqn: state.model_dump() for fqn, state in states.items()}),
        }
        for attempt in range(1, LOOKUP_ATTEMPTS + 1):
            try:
                await self.tables_sync_point.update_sync_point(SYNC_STATE_KEY, value)
                self.logger.debug(f"Saved sync state for {len(states)} tables")
                return True
            except Exception as e:
                if attempt == LOOKUP_ATTEMPTS:
                    self.sync_stats.errors += 1
                    self.logger.error(f"Could not save the sync state: {e}")
                    return False
                await asyncio.sleep(attempt)
        return False

    async def _populate_filter_cache(self) -> None:
        """
        Fetch all tables from MariaDB and rebuild the in-memory filter cache.
        Race-condition safe: concurrent callers wait on the rebuild event
        instead of each issuing their own DB round-trip.
        """
        if self._filter_cache_rebuild_event is not None:
            await self._filter_cache_rebuild_event.wait()
            return

        self._filter_cache_rebuild_event = asyncio.Event()
        try:
            if not self.data_source:
                raise RuntimeError("MariaDB data source not initialized")
            if not self.database_name:
                raise RuntimeError("MariaDB database is not configured")

            table_cache: list[FilterOption] = []

            tables_resp = await self.data_source.list_tables(database=self.database_name)
            if not tables_resp.success:
                raise RuntimeError(tables_resp.error or "Failed to fetch tables")

            for table_dict in tables_resp.data:
                table_entry = TableListEntry.model_validate(table_dict)
                if not table_entry.name:
                    continue
                fqn = table_fqn(self.database_name, table_entry.name)
                table_cache.append(FilterOption(id=fqn, label=fqn))

            # Atomic swap so readers never see a partially-built cache
            self._table_filter_cache = table_cache
            self.logger.info("Filter cache rebuilt: %d tables", len(table_cache))
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
            if filter_key != "tables":
                return FilterOptionsResponse(
                    success=False,
                    options=[],
                    page=page,
                    limit=limit,
                    has_more=False,
                    message=f"Unknown filter key: {filter_key}",
                )

            # Warm cache on first call or when no search term is provided
            if not self._table_filter_cache or not search or not search.strip():
                await self._populate_filter_cache()

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
    ) -> "MariaDBConnector":
        """Factory method to create a MariaDB connector instance."""
        return cls(
            logger,
            data_entities_processor,
            data_store_provider,
            config_service,
            connector_id,
            kwargs.get("scope", ConnectorScope.TEAM.value),
            kwargs.get("created_by"),
        )
