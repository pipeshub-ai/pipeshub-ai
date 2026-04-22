"""
MariaDB Connector

Syncs databases, tables and their rows from MariaDB.
"""
import asyncio
import hashlib
import json
import os
import uuid
from dataclasses import dataclass
from logging import Logger
from typing import Any, AsyncGenerator, Dict, List, Optional, Tuple

from aiolimiter import AsyncLimiter
from pydantic import BaseModel

from app.config.configuration_service import ConfigurationService
from app.config.constants.arangodb import (
    Connectors,
    MimeTypes,
    OriginTypes,
    RecordRelations,
)
from app.connectors.core.base.connector.connector_service import BaseConnector
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
from app.connectors.sources.mariadb.apps import MariaDBApp
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
from app.sources.client.mariadb.mariadb import MariaDBConfig
from app.sources.external.mariadb.mariadb_ import (
    MariaDBDataSource,
    ColumnInfo,
    CheckConstraintInfo,
    DDLResult,
    ForeignKeyInfo,
    PrimaryKeyInfo,
    TableDetail,
    TableListEntry,
    TableStatsEntry,
)
from app.utils.streaming import create_stream_record_response
from app.utils.time_conversion import get_epoch_timestamp_in_ms
from fastapi import HTTPException
from fastapi.responses import StreamingResponse

MAX_ROWS_PER_TABLE_LIMIT = 10000


class MariaDBTableState(BaseModel):
    column_hash: str = ""
    n_live_tup: int = 0
    last_updated: Optional[str] = None
    auto_increment: int = 0


@dataclass
class MariaDBTable:
    name: str
    database_name: str
    row_count: Optional[int] = None
    columns: List[ColumnInfo] = None
    foreign_keys: List[ForeignKeyInfo] = None
    primary_keys: List[str] = None
    
    def __post_init__(self):
        if self.columns is None:
            self.columns = []
        if self.foreign_keys is None:
            self.foreign_keys = []
        if self.primary_keys is None:
            self.primary_keys = []
    
    @property
    def fqn(self) -> str:
        return f"{self.database_name}.{self.name}"


@dataclass
class SyncStats:
    tables_new: int = 0
    errors: int = 0
    
    def to_dict(self) -> Dict[str, int]:
        return {
            'tables_new': self.tables_new,
            'errors': self.errors,
        }
    
    def log_summary(self, logger) -> None:
        logger.info(
            f"📊 Sync Stats: "
            f"Tables(new={self.tables_new}) | "
            f"Errors={self.errors}"
        )


@ConnectorBuilder("MariaDB")\
    .in_group("MariaDB")\
    .with_description("Sync databases and tables from MariaDB")\
    .with_categories(["Database"])\
    .with_scopes([ConnectorScope.TEAM.value])\
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
        ]),
    ])\
    .configure(lambda builder: builder
        .with_icon("/assets/icons/connectors/mariadb.svg")
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
class MariaDBConnector(BaseConnector):

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
        self.data_source: Optional[MariaDBDataSource] = None
        self.database_name: Optional[str] = None
        self.batch_size = 100
        self.rate_limiter = AsyncLimiter(25, 1)
        self.connector_scope: Optional[str] = None
        self.created_by: Optional[str] = None
        self.sync_filters: FilterCollection = FilterCollection()
        self.indexing_filters: FilterCollection = FilterCollection()
        self._record_id_cache: Dict[str, str] = {}
        self.sync_stats: SyncStats = SyncStats()
        self._frontend_url: str = os.getenv("FRONTEND_PUBLIC_URL", "").rstrip("/")
        self._table_filter_cache: List[FilterOption] = []
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
                async with self.data_store_provider.transaction() as tx_store:
                    await tx_store.ensure_team_app_edge(
                        self.connector_id,
                        self.data_entities_processor.org_id,
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
            )
            client = mariadb_config.create_client()
            client.connect()

            self.data_source = MariaDBDataSource(client)

            self.logger.info("MariaDB connector initialized successfully")
            return True

        except Exception as e:
            self.logger.error(f"Failed to initialize MariaDB connector: {e}", exc_info=True)
            return False

    async def run_sync(self) -> None:
        try:
            self.logger.info("📦 [Sync] Starting MariaDB sync...")

            if not self.data_source:
                raise ConnectionError("MariaDB connector not initialized")

            self.sync_filters, self.indexing_filters = await load_connector_filters(
                self.config_service, "mariadb", self.connector_id, self.logger
            )

            self.sync_stats = SyncStats()

            sync_point_key = "mariadb_tables_state"
            stored_state = await self.tables_sync_point.read_sync_point(sync_point_key)

            if stored_state and stored_state.get("table_states"):
                self.logger.info("📦 [Sync] Found existing sync state, running incremental sync...")
                await self.run_incremental_sync()
            else:
                self.logger.info("📦 [Sync] No existing sync state, running full sync...")
                await self._run_full_sync_internal()

            self.sync_stats.log_summary(self.logger)

        except Exception as e:
            self.logger.error(f"❌ [Sync] Error: {e}", exc_info=True)
            raise

    def _get_filter_values(self) -> Tuple[Optional[List[str]], str]:
        table_filter = self.sync_filters.get("tables")
        if not table_filter or not table_filter.value:
            return None, MultiselectOperator.IN.value
        return table_filter.value, table_filter.operator_value

    async def _run_full_sync_internal(self) -> None:
        try:
            self.logger.info("📦 [Full Sync] Starting full sync...")
            self._record_id_cache.clear()

            await self._create_app_users()

            selected_tables, filter_op = self._get_filter_values()

            if not self.database_name:
                raise ValueError("Database name must be configured for MariaDB connector")

            await self._ensure_database_record_groups([self.database_name])

            tables = await self._fetch_tables(self.database_name)
            if selected_tables:
                if filter_op == MultiselectOperator.NOT_IN.value:
                    tables = [t for t in tables if t.fqn not in selected_tables]
                else:
                    tables = [t for t in tables if t.fqn in selected_tables]

            await self._sync_tables(self.database_name, tables)
            self.sync_stats.tables_new += len(tables)

            # Save sync state for incremental sync
            await self._save_tables_sync_state("mariadb_tables_state")

            self.logger.info("✅ [Full Sync] MariaDB full sync completed")
        except Exception as e:
            self.sync_stats.errors += 1
            self.logger.error(f"❌ [Full Sync] Error: {e}", exc_info=True)
            raise

    async def _ensure_database_record_groups(self, databases: List[str]) -> None:
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

    async def _fetch_tables(self, database: str) -> List[MariaDBTable]:
        response = await self.data_source.list_tables(database=database)
        if not response.success:
            self.logger.error(f"Failed to fetch tables: {response.error}")
            return []
        
        tables = []
        for item in response.data:
            entry = TableListEntry.model_validate(item)
            
            table_info_response = await self.data_source.get_table_info(entry.name, database)
            columns: List[ColumnInfo] = []
            if table_info_response.success:
                detail = TableDetail.model_validate(table_info_response.data)
                columns = detail.columns
            
            fks_response = await self.data_source.get_foreign_keys(entry.name, database)
            foreign_keys: List[ForeignKeyInfo] = []
            if fks_response.success:
                foreign_keys = [ForeignKeyInfo.model_validate(fk) for fk in fks_response.data]
            
            pks_response = await self.data_source.get_primary_keys(entry.name, database)
            primary_keys: List[str] = []
            if pks_response.success:
                primary_keys = [PrimaryKeyInfo.model_validate(pk).column_name for pk in pks_response.data]
            
            tables.append(MariaDBTable(
                name=entry.name,
                database_name=database,
                columns=columns,
                foreign_keys=foreign_keys,
                primary_keys=primary_keys,
            ))
        return tables

    async def _get_permissions(self) -> List[Permission]:
        return [Permission(
            type=PermissionType.OWNER,
            entity_type=EntityType.ORG,
        )]

    async def _process_tables_generator(
        self,
        database_name: str,
        tables: List[MariaDBTable],
    ) -> AsyncGenerator[Tuple[Record, List[Permission]], None]:
        
        for table in tables:
            try:
                fqn = f"{database_name}.{table.name}"
                record_id = str(uuid.uuid4())
                self._record_id_cache[fqn] = record_id
                
                weburl = f"{self._frontend_url}/record/{record_id}" if self._frontend_url else ""

                current_time = get_epoch_timestamp_in_ms()
                record = SQLTableRecord(
                    id=record_id,
                    record_name=table.name,
                    record_type=RecordType.SQL_TABLE,
                    record_group_type=RecordGroupType.SQL_DATABASE.value,
                    external_record_group_id=database_name,
                    external_record_id=fqn,
                    external_revision_id=str(current_time), 
                    origin=OriginTypes.CONNECTOR.value,
                    connector_name=self.connector_name,
                    connector_id=self.connector_id,
                    mime_type=MimeTypes.SQL_TABLE.value,
                    weburl=weburl,
                    source_created_at=current_time,
                    source_updated_at=current_time,
                    row_count=table.row_count,
                    version=1,
                    inherit_permissions=True,
                )

                if table.foreign_keys:
                    for fk in table.foreign_keys:
                        target_database = fk.foreign_database or database_name
                        if fk.foreign_table_name:
                            target_fqn = f"{target_database}.{fk.foreign_table_name}"
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

                yield (record, [])
                await asyncio.sleep(0)
                
            except Exception as e:
                self.logger.error(f"Error processing table {table.name}: {e}", exc_info=True)
                continue

    async def _sync_tables(self, database_name: str, tables: List[MariaDBTable]) -> None:
        if not tables:
            return
        
        batch: List[Tuple[Record, List[Permission]]] = []
        total_synced = 0

        async for record, perms in self._process_tables_generator(database_name, tables):
            batch.append((record, perms))
            total_synced += 1

            if len(batch) >= self.batch_size:
                self.logger.debug(f"Processing batch of {len(batch)} tables")
                await self.data_entities_processor.on_new_records(batch)
                batch = []

        if batch:
            await self.data_entities_processor.on_new_records(batch)
            
        self.logger.info(f"Synced {total_synced} tables in {database_name}")

    async def _sync_updated_tables(self, database_name: str, tables: List[MariaDBTable]) -> None:
        """Sync tables whose content or schema has changed."""
        if not tables:
            return
        
        self.logger.info(f"Processing {len(tables)} updated tables in {database_name}")
        
        for table in tables:
            try:
                fqn = f"{database_name}.{table.name}"
                
                existing_record = await self.data_entities_processor.get_record_by_external_id(
                    connector_id=self.connector_id,
                    external_record_id=fqn
                )
                
                if not existing_record:
                    self.logger.warning(f"No existing record found for updated table {fqn}, skipping")
                    continue
                
                current_time = get_epoch_timestamp_in_ms()
                
                updated_record = SQLTableRecord(
                    id=existing_record.id,
                    record_name=table.name,
                    record_type=RecordType.SQL_TABLE,
                    record_group_type=RecordGroupType.SQL_DATABASE.value,
                    external_record_group_id=database_name,
                    external_record_id=fqn,
                    external_revision_id=str(current_time),
                    origin=OriginTypes.CONNECTOR.value,
                    connector_name=self.connector_name,
                    connector_id=self.connector_id,
                    mime_type=MimeTypes.SQL_TABLE.value,
                    weburl=existing_record.weburl if hasattr(existing_record, 'weburl') else "",
                    source_created_at=existing_record.source_created_at if hasattr(existing_record, 'source_created_at') else current_time,
                    source_updated_at=current_time,
                    row_count=table.row_count,
                    version=(existing_record.version or 1) + 1,
                    inherit_permissions=True,
                )

                if table.foreign_keys:
                    for fk in table.foreign_keys:
                        target_database = fk.foreign_database or database_name
                        if fk.foreign_table_name:
                            target_fqn = f"{target_database}.{fk.foreign_table_name}"
                            updated_record.related_external_records.append(
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
                    updated_record.indexing_status = ProgressStatus.AUTO_INDEX_OFF.value
                
                await self.data_entities_processor.on_record_content_update(updated_record)
                self.logger.debug(f"Published content update for table: {fqn}")
                
            except Exception as e:
                self.logger.error(f"Error syncing updated table {table.name}: {e}", exc_info=True)
                continue
        
        self.logger.info(f"Completed syncing {len(tables)} updated tables in {database_name}")
 
    async def stream_record(
        self,
        record: Record,
        user_id: Optional[str] = None,
        convertTo: Optional[str] = None
    ) -> StreamingResponse:
        try:
            if not self.data_source:
                raise HTTPException(status_code=500, detail="MariaDB data source not initialized")

            if record.record_type == RecordType.SQL_TABLE:
                parts = record.external_record_id.split(".")
                if len(parts) != 2:
                    raise HTTPException(status_code=500, detail="Invalid table FQN")
                database, table = parts[0], parts[1]

                table_info_response = await self.data_source.get_table_info(table, database)
                columns: List[ColumnInfo] = []
                if table_info_response.success:
                    detail = TableDetail.model_validate(table_info_response.data)
                    columns = detail.columns
                    self.logger.info(f"✅ Retrieved {len(columns)} columns for {database}.{table}")
                else:
                    self.logger.error(f"❌ Failed to get table info for {database}.{table}: {table_info_response.error}")

                fks_response = await self.data_source.get_foreign_keys(table, database)
                foreign_keys: List[ForeignKeyInfo] = []
                if fks_response.success:
                    foreign_keys = [ForeignKeyInfo.model_validate(fk) for fk in fks_response.data]
                
                pks_response = await self.data_source.get_primary_keys(table, database)
                primary_keys: List[str] = []
                if pks_response.success:
                    primary_keys = [PrimaryKeyInfo.model_validate(pk).column_name for pk in pks_response.data]

                sync_filters, _ = await load_connector_filters(
                    self.config_service, "mariadb", self.connector_id, self.logger
                )
                max_rows = min(
                    int(sync_filters.get_value(IndexingFilterKey.MAX_ROWS_PER_TABLE, default=1000)),
                    MAX_ROWS_PER_TABLE_LIMIT,
                )
                rows = await self.data_source.fetch_table_rows(database, table, limit=max_rows)
                
                ddl_response = await self.data_source.get_table_ddl(table, database)
                ddl = ""
                if ddl_response.success:
                    ddl_obj = DDLResult.model_validate(ddl_response.data)
                    ddl = ddl_obj.ddl

                data = {
                    "table_name": table,
                    "database_name": database,
                    "columns": [col.model_dump() for col in columns],
                    "rows": rows,
                    "foreign_keys": [fk.model_dump() for fk in foreign_keys],
                    "primary_keys": primary_keys,
                    "ddl": ddl,
                    "connector_name": self.connector_name.value if hasattr(self.connector_name, "value") else str(self.connector_name),
                }

                json_bytes = json.dumps(data, default=str).encode("utf-8")

                async def json_iterator():
                    yield json_bytes

                return create_stream_record_response(
                    json_iterator(), filename=f"{table}.json", mime_type=MimeTypes.SQL_TABLE.value
                )

            raise HTTPException(status_code=400, detail="Unsupported record type")

        except Exception as e:
            self.logger.error(f"Error streaming record: {e}", exc_info=True)
            raise

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
                    client.close()
                self.data_source = None

            self._record_id_cache.clear()
            self.database_name = None

            self.logger.info("MariaDB connector cleanup completed")

        except Exception as e:
            self.logger.error(f"Error during MariaDB connector cleanup: {e}", exc_info=True)

    def get_signed_url(self, record: Record) -> Optional[str]:
        return None

    def handle_webhook_notification(self, notification: Dict) -> None:
        raise NotImplementedError(
            "MariaDB does not support webhook notifications. "
            "Use scheduled sync for change tracking."
        )

    async def reindex_records(self, records: List[Record]) -> None:
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

    async def run_incremental_sync(self) -> None:
        """
        Run incremental sync for MariaDB.

        Compares current table states (row count, column hash, last_updated)
        with previously stored states to detect changes.
        
        Note: MariaDB does not have cumulative DML counters like PostgreSQL.
        We rely on TABLE_ROWS estimates and UPDATE_TIME from information_schema.TABLES.
        UPDATE_TIME may be NULL for InnoDB tables unless innodb_file_per_table=ON.
        """
        self.logger.info("📦 [Incremental Sync] Starting MariaDB incremental sync...")

        if not self.data_source:
            raise ConnectionError("MariaDB connector not initialized")

        self.sync_filters, self.indexing_filters = await load_connector_filters(
            self.config_service, "mariadb", self.connector_id, self.logger
        )

        try:
            sync_point_key = "mariadb_tables_state"
            stored_state = await self.tables_sync_point.read_sync_point(sync_point_key)
            
            if not stored_state or not stored_state.get("table_states"):
                self.logger.info("No previous sync state found, running full sync")
                await self._run_full_sync_internal()
                await self._save_tables_sync_state(sync_point_key)
                return

            raw_states: Dict[str, Any] = json.loads(
                stored_state.get("table_states", "{}")
            )
            stored_table_states: Dict[str, MariaDBTableState] = {
                fqn: MariaDBTableState.model_validate(state)
                for fqn, state in raw_states.items()
            }
            
            selected_tables, filter_op = self._get_filter_values()
            current_stats = await self._get_current_table_states(selected_tables, filter_op)
            
            new_tables: List[str] = []
            changed_tables: List[str] = []
            deleted_tables: List[str] = []
            
            current_fqns = set(current_stats.keys())
            stored_fqns = set(stored_table_states.keys())
            
            new_tables = list(current_fqns - stored_fqns)
            deleted_tables = list(stored_fqns - current_fqns)
            
            for fqn in current_fqns & stored_fqns:
                current = current_stats[fqn]
                stored = stored_table_states[fqn]
                
                if self._has_table_changed(current, stored):
                    changed_tables.append(fqn)
            
            self.logger.info(
                f"📊 Change detection: new={len(new_tables)}, "
                f"changed={len(changed_tables)}, deleted={len(deleted_tables)}"
            )
            if new_tables:
                await self._sync_new_tables(new_tables)
            if changed_tables:
                await self._sync_changed_tables(changed_tables) 
            if deleted_tables:
                await self._handle_deleted_tables(deleted_tables)
            
            await self._save_tables_sync_state(sync_point_key)
            
            self.logger.info("✅ [Incremental Sync] MariaDB incremental sync completed")

        except Exception as e:
            self.logger.error(f"❌ [Incremental Sync] Error: {e}", exc_info=True)
            raise

    async def _get_current_table_states(
        self,
        selected_tables: Optional[List[str]],
        filter_op: str = MultiselectOperator.IN.value,
    ) -> Dict[str, MariaDBTableState]:
        """Fetch current table states from MariaDB for comparison.

        Uses TABLE_ROWS and UPDATE_TIME from information_schema.TABLES along with
        column hash for change detection.
        """
        table_states: Dict[str, MariaDBTableState] = {}

        if not self.database_name:
            self.logger.warning("Database name is not configured")
            return table_states

        databases_to_check = [self.database_name]

        stats_response = await self.data_source.get_table_stats(databases_to_check)
        if not stats_response.success:
            self.logger.warning(f"Failed to get table stats: {stats_response.error}")
            return table_states

        exclude = filter_op == MultiselectOperator.NOT_IN.value
        stats_by_fqn: Dict[str, TableStatsEntry] = {}
        for stat_dict in stats_response.data:
            stat = TableStatsEntry.model_validate(stat_dict)
            fqn = f"{stat.database_name}.{stat.table_name}"
            if selected_tables:
                is_in = fqn in selected_tables
                if (exclude and is_in) or (not exclude and not is_in):
                    continue
            stats_by_fqn[fqn] = stat
        
        for fqn, stat in stats_by_fqn.items():
            database_name, table_name = fqn.split(".", 1)
            column_hash = await self._compute_column_hash(database_name, table_name)
            
            table_states[fqn] = MariaDBTableState(
                column_hash=column_hash,
                n_live_tup=stat.n_live_tup or 0,
                last_updated=str(stat.last_updated) if stat.last_updated else None,
                auto_increment=stat.auto_increment or 0,
            )
        
        return table_states

    async def _compute_column_hash(self, database: str, table: str) -> str:
        """Compute MD5 hash of column definitions for schema change detection."""
        table_info_response = await self.data_source.get_table_info(table, database)
        if not table_info_response.success:
            return ""
        
        detail = TableDetail.model_validate(table_info_response.data)
        columns_dicts = [col.model_dump() for col in detail.columns]
        column_str = json.dumps(columns_dicts, sort_keys=True, default=str)
        return hashlib.md5(column_str.encode()).hexdigest()

    def _has_table_changed(
        self,
        current: MariaDBTableState,
        stored: MariaDBTableState
    ) -> bool:
        """Check if table has changed by comparing metadata.
        
        - Column hash for schema changes
        - UPDATE_TIME for modification time changes (may be NULL for InnoDB)
        - TABLE_ROWS estimate for row count changes
        - AUTO_INCREMENT for insert detection
        """
        if current.column_hash != stored.column_hash:
            return True

        if current.last_updated and not stored.last_updated:
            return True
        
        if stored.last_updated and not current.last_updated:
            return True

        if current.last_updated and stored.last_updated and current.last_updated != stored.last_updated:
            return True
        
        if current.auto_increment != stored.auto_increment:
            return True

        if current.n_live_tup != stored.n_live_tup:
            return True
        
        return False

    async def _sync_new_tables(self, table_fqns: List[str]) -> None:
        """Sync newly discovered tables.
        
        Also ensures parent database RecordGroups exist for any new databases
        that weren't present during the initial full sync.
        """
        self.logger.info(f"Syncing {len(table_fqns)} new tables")

        # Ensure parent database record groups exist for all new tables
        new_databases = set()
        for fqn in table_fqns:
            database_name = fqn.split(".", 1)[0]
            new_databases.add(database_name)
        
        if new_databases:
            await self._ensure_database_record_groups(list(new_databases))

        for fqn in table_fqns:
            database_name, table_name = fqn.split(".", 1)
            
            table_info_response = await self.data_source.get_table_info(table_name, database_name)
            columns: List[ColumnInfo] = []
            if table_info_response.success:
                detail = TableDetail.model_validate(table_info_response.data)
                columns = detail.columns
            
            fks_response = await self.data_source.get_foreign_keys(table_name, database_name)
            foreign_keys = [ForeignKeyInfo.model_validate(fk) for fk in fks_response.data] if fks_response.success else []
            
            pks_response = await self.data_source.get_primary_keys(table_name, database_name)
            primary_keys = [PrimaryKeyInfo.model_validate(pk).column_name for pk in pks_response.data] if pks_response.success else []
            
            table = MariaDBTable(
                name=table_name,
                database_name=database_name,
                columns=columns,
                foreign_keys=foreign_keys,
                primary_keys=primary_keys,
            )
            
            await self._sync_tables(database_name, [table])
            self.sync_stats.tables_new += 1

    async def _sync_changed_tables(self, table_fqns: List[str]) -> None:
        """Sync changed tables."""
        self.logger.info(f"Syncing {len(table_fqns)} changed tables")
        
        for fqn in table_fqns:
            database_name, table_name = fqn.split(".", 1)
            
            table_info_response = await self.data_source.get_table_info(table_name, database_name)
            columns: List[ColumnInfo] = []
            if table_info_response.success:
                detail = TableDetail.model_validate(table_info_response.data)
                columns = detail.columns
            
            fks_response = await self.data_source.get_foreign_keys(table_name, database_name)
            foreign_keys = [ForeignKeyInfo.model_validate(fk) for fk in fks_response.data] if fks_response.success else []
            
            pks_response = await self.data_source.get_primary_keys(table_name, database_name)
            primary_keys = [PrimaryKeyInfo.model_validate(pk).column_name for pk in pks_response.data] if pks_response.success else []
            
            table = MariaDBTable(
                name=table_name,
                database_name=database_name,
                columns=columns,
                foreign_keys=foreign_keys,
                primary_keys=primary_keys,
            )
            
            await self._sync_updated_tables(database_name, [table])

    async def _handle_deleted_tables(self, table_fqns: List[str]) -> None:
        """Handle tables that no longer exist in the database."""
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
            except Exception as e:
                self.logger.warning(f"Failed to delete record for {fqn}: {e}")

    async def _save_tables_sync_state(self, sync_point_key: str) -> None:
        """Save current table states for next incremental sync comparison."""
        selected_tables, filter_op = self._get_filter_values()
        current_states = await self._get_current_table_states(selected_tables, filter_op)
        count = len(current_states)
        serialized_states = json.dumps(
            {fqn: state.model_dump() for fqn, state in current_states.items()}
        )
        await self.tables_sync_point.update_sync_point(
            sync_point_key,
            {
                "last_sync_time": get_epoch_timestamp_in_ms(),
                "table_states": serialized_states,
            }
        )
        self.logger.debug(f"Saved sync state for {count} tables")

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

            table_cache: List[FilterOption] = []

            tables_resp = await self.data_source.list_tables(database=self.database_name)
            if not tables_resp.success:
                raise RuntimeError(tables_resp.error or "Failed to fetch tables")

            for table_dict in tables_resp.data:
                table_entry = TableListEntry.model_validate(table_dict)
                if not table_entry.name:
                    continue
                fqn = f"{self.database_name}.{table_entry.name}"
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
        search: Optional[str] = None,
        cursor: Optional[str] = None,
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
        **kwargs,
    ) -> "MariaDBConnector":
        """Factory method to create a MariaDB connector instance."""
        data_entities_processor = DataSourceEntitiesProcessor(
            logger, data_store_provider, config_service
        )
        await data_entities_processor.initialize()
        return cls(
            logger,
            data_entities_processor,
            data_store_provider,
            config_service,
            connector_id,
            kwargs.get("scope", ConnectorScope.TEAM.value),
            kwargs.get("created_by"),
        )
