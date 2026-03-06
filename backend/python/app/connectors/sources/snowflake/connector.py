"""
Snowflake Connector

Syncs databases, namespaces, tables, views, stages and files from Snowflake.
"""
import asyncio
import hashlib
import json
import mimetypes
import os
import re
import uuid
from dataclasses import dataclass
from logging import Logger
from typing import Any, AsyncGenerator, Dict, List, Optional, Set, Tuple

from aiolimiter import AsyncLimiter

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
    RecordRelation,
)
from app.connectors.core.base.data_store.data_store import DataStoreProvider
from app.connectors.core.base.sync_point.sync_point import (
    SyncDataPointType,
    SyncPoint,
)
from app.connectors.core.registry.auth_builder import AuthBuilder, AuthType, OAuthScopeConfig
from app.connectors.core.registry.connector_builder import (
    AuthField,
    CommonFields,
    ConnectorBuilder,
    ConnectorScope,
    DocumentationLink,
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
    OptionSourceType,
    load_connector_filters,
)
from app.connectors.sources.snowflake.apps import SnowflakeApp
from app.connectors.sources.snowflake.data_fetcher import (
    ForeignKey,
    SnowflakeDataFetcher,
    SnowflakeDatabase,
    SnowflakeFile,
    SnowflakeSchema,
    SnowflakeStage,
    SnowflakeTable,
    SnowflakeView,
)
from app.models.entities import (
    FileRecord,
    IndexingStatus,
    Record,
    RecordGroup,
    RecordGroupType,
    RecordType,
    SQLTableRecord,
    SQLViewRecord,
)
from app.models.permission import EntityType, Permission, PermissionType
from app.sources.client.snowflake.snowflake import (
    SnowflakeClient,
    SnowflakeOAuthConfig,
    SnowflakePATConfig,
)
from app.sources.external.snowflake.snowflake_ import SnowflakeDataSource
from app.utils.streaming import create_stream_record_response
from app.utils.time_conversion import get_epoch_timestamp_in_ms
from fastapi import HTTPException
from fastapi.responses import StreamingResponse
from app.models.entities import AppUser, User


def get_file_extension(path: str) -> Optional[str]:
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


# Get Snowflake account identifier from environment variable for OAuth URLs
SNOWFLAKE_ACCOUNT_IDENTIFIER = os.getenv("SNOWFLAKE_ACCOUNT_IDENTIFIER", "")
SNOWFLAKE_OAUTH_BASE_URL = f"https://{SNOWFLAKE_ACCOUNT_IDENTIFIER}.snowflakecomputing.com" if SNOWFLAKE_ACCOUNT_IDENTIFIER else "https://{account}.snowflakecomputing.com"

# Row limit for streaming table data (prevents memory issues with large tables)
SNOWFLAKE_TABLE_ROW_LIMIT = int(os.getenv("SNOWFLAKE_TABLE_ROW_LIMIT", "50"))


# ============================================================================
# Sync Statistics for Tracking Progress
# ============================================================================

@dataclass
class SyncStats:
    """Statistics for sync operations."""
    databases_synced: int = 0
    schemas_synced: int = 0
    stages_synced: int = 0
    tables_new: int = 0
    tables_updated: int = 0
    tables_unchanged: int = 0
    tables_deleted: int = 0
    tables_schema_changed: int = 0  # Schema version changes detected
    tables_stream_changes: int = 0  # Tables with Snowflake Stream changes
    views_new: int = 0
    views_updated: int = 0
    views_unchanged: int = 0
    views_deleted: int = 0
    files_new: int = 0
    files_updated: int = 0
    files_unchanged: int = 0
    files_deleted: int = 0
    records_reindexed: int = 0  # Selective re-indexing count
    checkpoint_resumed: bool = False  # Whether sync resumed from checkpoint
    errors: int = 0

    def to_dict(self) -> Dict[str, int]:
        return {
            'databases_synced': self.databases_synced,
            'schemas_synced': self.schemas_synced,
            'stages_synced': self.stages_synced,
            'tables_new': self.tables_new,
            'tables_updated': self.tables_updated,
            'tables_unchanged': self.tables_unchanged,
            'tables_deleted': self.tables_deleted,
            'tables_schema_changed': self.tables_schema_changed,
            'tables_stream_changes': self.tables_stream_changes,
            'views_new': self.views_new,
            'views_updated': self.views_updated,
            'views_unchanged': self.views_unchanged,
            'views_deleted': self.views_deleted,
            'files_new': self.files_new,
            'files_updated': self.files_updated,
            'files_unchanged': self.files_unchanged,
            'files_deleted': self.files_deleted,
            'records_reindexed': self.records_reindexed,
            'checkpoint_resumed': 1 if self.checkpoint_resumed else 0,
            'errors': self.errors,
        }

    def log_summary(self, logger) -> None:
        """Log a summary of sync statistics."""
        resume_note = " (resumed from checkpoint)" if self.checkpoint_resumed else ""
        logger.info(
            f"📊 Sync Stats{resume_note}: "
            f"DBs={self.databases_synced}, Schemas={self.schemas_synced}, Stages={self.stages_synced} | "
            f"Tables(new={self.tables_new}, updated={self.tables_updated}, schema_changed={self.tables_schema_changed}, "
            f"stream_changes={self.tables_stream_changes}, unchanged={self.tables_unchanged}, deleted={self.tables_deleted}) | "
            f"Views(new={self.views_new}, updated={self.views_updated}, deleted={self.views_deleted}) | "
            f"Files(new={self.files_new}, updated={self.files_updated}, deleted={self.files_deleted}) | "
            f"Reindexed={self.records_reindexed} | Errors={self.errors}"
        )


@ConnectorBuilder("Snowflake")\
    .in_group("Snowflake")\
    .with_description("Sync databases, tables, views, stages and files from Snowflake")\
    .with_categories(["Database", "Data Warehouse"])\
    .with_scopes([ConnectorScope.PERSONAL.value, ConnectorScope.TEAM.value])\
    .with_auth([
        # Option 1: Personal Access Token (PAT) authentication
        AuthBuilder.type(AuthType.ACCESS_KEY).fields([
            AuthField(
                name="accountIdentifier",
                display_name="Account Identifier",
                placeholder="abc12345.us-east-1",
                description="Snowflake account identifier (e.g., abc12345.us-east-1)",
                field_type="TEXT",
                max_length=500,
                is_secret=False
            ),
            AuthField(
                name="patToken",
                display_name="Personal Access Token",
                placeholder="Enter your PAT token",
                description="Personal Access Token from Snowflake",
                field_type="PASSWORD",
                max_length=2000,
                is_secret=True
            ),
            AuthField(
                name="warehouse",
                display_name="Warehouse",
                placeholder="COMPUTE_WH",
                description="Default warehouse for query execution",
                field_type="TEXT",
                max_length=200,
                is_secret=False
            ),
        ]),
        # Option 2: OAuth authentication
        AuthBuilder.type(AuthType.OAUTH).oauth(
            connector_name="Snowflake",
            authorize_url=f"{SNOWFLAKE_OAUTH_BASE_URL}/oauth/authorize",
            token_url=f"{SNOWFLAKE_OAUTH_BASE_URL}/oauth/token-request",
            redirect_uri="connectors/oauth/callback/Snowflake",
            scopes=OAuthScopeConfig(
                personal_sync=["session:role:PUBLIC"],
                team_sync=["session:role:PUBLIC"],
                agent=[]
            ),
            fields=[
                CommonFields.client_id("Snowflake Security Integration"),
                CommonFields.client_secret("Snowflake Security Integration"),
                AuthField(
                    name="accountIdentifier",
                    display_name="Account Identifier",
                    placeholder="abc12345.us-east-1",
                    description="Snowflake account identifier (e.g., abc12345.us-east-1)",
                    field_type="TEXT",
                    max_length=500,
                    is_secret=False
                ),
                AuthField(
                    name="warehouse",
                    display_name="Warehouse",
                    placeholder="COMPUTE_WH",
                    description="Default warehouse for query execution",
                    field_type="TEXT",
                    max_length=200,
                    is_secret=False
                ),
            ],
            icon_path="/assets/icons/connectors/snowflake.svg",
            app_group="Snowflake",
            app_description="OAuth application for accessing Snowflake SQL API",
            app_categories=["Database", "Data Warehouse"],
            additional_params={
                "response_type": "code",
            }
        )
    ])\
    .configure(lambda builder: builder
        .with_icon("/assets/icons/connectors/snowflake.svg")
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
            name="index_tables",
            display_name="Index Tables",
            filter_type=FilterType.BOOLEAN,
            category=FilterCategory.INDEXING,
            description="Enable indexing of tables",
            default_value=True
        ))
        .add_filter_field(FilterField(
            name="index_views",
            display_name="Index Views",
            filter_type=FilterType.BOOLEAN,
            category=FilterCategory.INDEXING,
            description="Enable indexing of views",
            default_value=True
        ))
        .add_filter_field(FilterField(
            name="index_stage_files",
            display_name="Index Stage Files",
            filter_type=FilterType.BOOLEAN,
            category=FilterCategory.INDEXING,
            description="Enable indexing of files in stages",
            default_value=True
        ))
        .add_filter_field(CommonFields.enable_manual_sync_filter())
        .with_sync_strategies(["SCHEDULED", "MANUAL"])
        .with_scheduled_config(True, 120)
        .with_sync_support(True)
        .with_agent_support(False)
    )\
    .build_decorator()
class SnowflakeConnector(BaseConnector):

    def __init__(
        self,
        logger: Logger,
        data_entities_processor: DataSourceEntitiesProcessor,
        data_store_provider: DataStoreProvider,
        config_service: ConfigurationService,
        connector_id: str,
    ) -> None:
        super().__init__(
            SnowflakeApp(connector_id),
            logger,
            data_entities_processor,
            data_store_provider,
            config_service,
            connector_id,
        )
        self.connector_id = connector_id
        self.connector_name = Connectors.SNOWFLAKE

        def _create_sync_point(sync_data_point_type: SyncDataPointType) -> SyncPoint:
            return SyncPoint(
                connector_id=self.connector_id,
                org_id=self.data_entities_processor.org_id,
                sync_data_point_type=sync_data_point_type,
                data_store_provider=self.data_store_provider,
            )

        self.record_sync_point = _create_sync_point(SyncDataPointType.RECORDS)
        self.data_source: Optional[SnowflakeDataSource] = None
        self.data_fetcher: Optional[SnowflakeDataFetcher] = None
        self.warehouse: Optional[str] = None
        self.account_identifier: Optional[str] = None
        self.batch_size = 100
        self.rate_limiter = AsyncLimiter(25, 1)
        self.connector_scope: Optional[str] = None
        self.created_by: Optional[str] = None
        self.sync_filters: FilterCollection = FilterCollection()
        self.indexing_filters: FilterCollection = FilterCollection()
        self._record_id_cache: Dict[str, str] = {}
        
        # Sync statistics for tracking progress
        self.sync_stats: SyncStats = SyncStats()
        
        # Sync state keys for incremental sync and checkpoint/resume
        self._sync_state_key = "snowflake_sync_state"
        self._checkpoint_key = "snowflake_sync_checkpoint"
        
        # Snowflake Streams configuration
        self._enable_streams = True  # Enable Snowflake Streams for CDC
        self._stream_prefix = "PIPESHUB_CDC_"  # Prefix for managed streams

    def get_app_users(self, users: List[User]) -> List[AppUser]:
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
        """
        Initialize the Snowflake connector with credentials and services.

        Supports two authentication methods:
        1. Personal Access Token (PAT) - via auth.patToken
        2. OAuth - via credentials.access_token

        Returns:
            True if initialization was successful, False otherwise
        """
        try:
            config = await self.config_service.get_config(
                f"/services/connectors/{self.connector_id}/config"
            )
            if not config:
                self.logger.error("Snowflake configuration not found")
                return False

            auth_config = config.get("auth") or {}
            credentials_config = config.get("credentials") or {}

            self.account_identifier = auth_config.get("accountIdentifier")
            self.warehouse = auth_config.get("warehouse")

            if not self.account_identifier:
                self.logger.error("Missing accountIdentifier in configuration")
                return False

            self.connector_scope = config.get("scope", ConnectorScope.PERSONAL.value)
            self.created_by = config.get("created_by")

            # Determine authentication method
            pat_token = auth_config.get("patToken")
            oauth_token = credentials_config.get("access_token")

            client: Optional[SnowflakeClient] = None

            if oauth_token:
                # OAuth authentication
                self.logger.info("Using OAuth authentication for Snowflake")
                oauth_config = SnowflakeOAuthConfig(
                    account_identifier=self.account_identifier,
                    oauth_token=oauth_token,
                )
                client = SnowflakeClient(oauth_config.create_client())

            elif pat_token:
                # Personal Access Token authentication
                self.logger.info("Using PAT authentication for Snowflake")
                pat_config = SnowflakePATConfig(
                    account_identifier=self.account_identifier,
                    pat_token=pat_token,
                )
                client = SnowflakeClient(pat_config.create_client())

            else:
                self.logger.error(
                    "No valid authentication method found. "
                    "Provide either patToken (for PAT auth) or access_token (for OAuth)."
                )
                return False

            self.data_source = SnowflakeDataSource(client)
            self.data_fetcher = SnowflakeDataFetcher(self.data_source, self.warehouse)

            self.sync_filters, self.indexing_filters = await load_connector_filters(
                self.config_service, "snowflake", self.connector_id, self.logger
            )

            self.logger.info("Snowflake connector initialized successfully")
            return True

        except Exception as e:
            self.logger.error(f"Failed to initialize Snowflake connector: {e}", exc_info=True)
            return False

    async def run_sync(self) -> None:
        """
        Run full synchronization for Snowflake.
        
        Always performs a full sync (like S3 pattern) because Snowflake lacks a
        reliable change/activity API. The upsert behavior ensures:
        - New records are created
        - Existing records are updated if changed
        - No false deletions from comparing local sync state
        """
        try:
            self.logger.info("📦 [Sync] Starting Snowflake sync...")

            if not self.data_fetcher:
                raise ConnectionError("Snowflake connector not initialized")

            # Load filters
            self.sync_filters, self.indexing_filters = await load_connector_filters(
                self.config_service, "snowflake", self.connector_id, self.logger
            )

            # Reset sync stats
            self.sync_stats = SyncStats()

            # Always run full sync - Snowflake has no change API like Drive or Box
            # The on_new_records() uses upsert behavior, so this is safe and reliable
            await self._run_full_sync_internal()

            # Log final stats
            self.sync_stats.log_summary(self.logger)

        except Exception as e:
            self.logger.error(f"❌ [Sync] Error: {e}", exc_info=True)
            raise

    def _get_filter_values(self) -> Tuple[
        Optional[List[str]], Optional[List[str]], Optional[List[str]],
        Optional[List[str]], Optional[List[str]], Optional[List[str]]
    ]:
        """Extract filter values from sync_filters."""
        db_filter = self.sync_filters.get("databases")
        selected_dbs = db_filter.value if db_filter and db_filter.value else None

        schema_filter = self.sync_filters.get("schemas")
        selected_schemas = schema_filter.value if schema_filter and schema_filter.value else None

        table_filter = self.sync_filters.get("tables")
        selected_tables = table_filter.value if table_filter and table_filter.value else None

        view_filter = self.sync_filters.get("views")
        selected_views = view_filter.value if view_filter and view_filter.value else None

        stage_filter = self.sync_filters.get("stages")
        selected_stages = stage_filter.value if stage_filter and stage_filter.value else None

        file_filter = self.sync_filters.get("files")
        selected_files = file_filter.value if file_filter and file_filter.value else None

        return selected_dbs, selected_schemas, selected_tables, selected_views, selected_stages, selected_files

    async def _run_full_sync_internal(self) -> None:
        """
        Internal method for full synchronization.
        
        Syncs all objects from Snowflake using upsert behavior:
        - Creates new records if they don't exist
        - Updates existing records if they've changed
        - Does NOT delete records (Snowflake lacks reliable change API for deletion detection)
        """
        try:
            self.logger.info("📦 [Full Sync] Starting full sync...")
            self._record_id_cache.clear()

            # Create AppUser for this connector
            await self._create_app_user()

            # Get filter values
            selected_dbs, selected_schemas, selected_tables, selected_views, selected_stages, selected_files = self._get_filter_values()

            # Debug: Log filter values
            self.logger.debug(f"Filter values - databases: {selected_dbs}")
            self.logger.debug(f"Filter values - schemas: {selected_schemas}")
            self.logger.debug(f"Filter values - tables: {selected_tables}")
            self.logger.debug(f"Filter values - views: {selected_views}")
            self.logger.debug(f"Filter values - stages: {selected_stages}")
            self.logger.debug(f"Filter values - files: {selected_files}")

            hierarchy = await self.data_fetcher.fetch_all(
                database_filter=selected_dbs,
                include_files=True,
                include_relationships=True,
            )
            self.logger.info(f"Fetched hierarchy: {hierarchy.summary()}")
            # Debug: Log hierarchy details for stages and files
            self.logger.debug(f"Hierarchy stages keys: {list(hierarchy.stages.keys())}")
            self.logger.debug(f"Hierarchy files keys: {list(hierarchy.files.keys())}")
            for stage_key, stage_files in hierarchy.files.items():
                self.logger.debug(f"Files in {stage_key}: {len(stage_files)} files")

            # Initialize sync state for tracking (used for future incremental syncs)
            new_sync_state = {
                "last_sync_time": get_epoch_timestamp_in_ms(),
                "databases": [],
                "schemas": {},
                "stages": {},
                "tables": {},
                "views": {},
                "files": {},
                "streams": {},  # Track Snowflake Streams for CDC
            }

            await self._sync_databases(hierarchy.databases)
            self.sync_stats.databases_synced = len(hierarchy.databases)
            new_sync_state["databases"] = [db.name for db in hierarchy.databases]

            for db in hierarchy.databases:
                schemas = hierarchy.schemas.get(db.name, [])
                
                # Debug: Log all schemas in hierarchy before filtering
                all_schema_names = [f"{db.name}.{s.name}" for s in schemas]
                self.logger.info(f"All schemas in {db.name}: {all_schema_names}")
                
                # Apply schema filter if specified
                if selected_schemas:
                    self.logger.info(f"Schema filter values: {selected_schemas}")
                    schemas = [s for s in schemas if f"{db.name}.{s.name}" in selected_schemas]
                    self.logger.info(f"Filtered to {len(schemas)} schemas in {db.name}: {[s.name for s in schemas]}")
                
                await self._sync_namespaces(db.name, schemas)
                self.sync_stats.schemas_synced += len(schemas)

                for schema in schemas:
                    schema_key = f"{db.name}.{schema.name}"
                    tables = hierarchy.tables.get(schema_key, [])
                    views = hierarchy.views.get(schema_key, [])
                    stages = hierarchy.stages.get(schema_key, [])

                    # Apply table filter if specified
                    if selected_tables:
                        tables = [t for t in tables if t.fqn in selected_tables]
                        self.logger.info(f"Filtered to {len(tables)} tables in {schema_key} based on table filter")

                    # Apply view filter if specified
                    if selected_views:
                        views = [v for v in views if v.fqn in selected_views]
                        self.logger.info(f"Filtered to {len(views)} views in {schema_key} based on view filter")

                    # Apply stage filter if specified
                    if selected_stages:
                        self.logger.debug(f"Stages before filter in {schema_key}: {[s.fqn for s in stages]}")
                        stages = [s for s in stages if s.fqn in selected_stages]
                        self.logger.info(f"Filtered to {len(stages)} stages in {schema_key} based on stage filter")
                    else:
                        self.logger.debug(f"No stage filter, processing {len(stages)} stages in {schema_key}")

                    await self._sync_tables(db.name, schema.name, tables)
                    self.sync_stats.tables_new += len(tables)

                    await self._sync_views(db.name, schema.name, views)
                    self.sync_stats.views_new += len(views)

                    await self._sync_stages(db.name, schema.name, stages)
                    self.sync_stats.stages_synced += len(stages)

                    self.logger.debug(f"Processing files for {len(stages)} stages in {schema_key}")
                    for stage in stages:
                        stage_key = f"{db.name}.{schema.name}.{stage.name}"
                        files = hierarchy.files.get(stage_key, [])
                        
                        self.logger.debug(f"Stage {stage_key}: found {len(files)} files in hierarchy")
                        
                        # Apply file filter if specified
                        if selected_files:
                            # Log the file paths for debugging
                            file_paths = [f"{db.name}.{schema.name}.{stage.name}/{f.relative_path}" for f in files]
                            self.logger.debug(f"File paths before filter: {file_paths}")
                            self.logger.debug(f"Selected files filter: {selected_files}")
                            files = [f for f in files if f"{db.name}.{schema.name}.{stage.name}/{f.relative_path}" in selected_files]
                            self.logger.info(f"Filtered to {len(files)} files in {stage_key} based on file filter")
                        
                        await self._sync_stage_files(
                            db.name, schema.name, stage.name, files
                        )
                        self.sync_stats.files_new += len([f for f in files if not f.is_folder])

            await self._create_foreign_key_relations(hierarchy.foreign_keys)
            await self._create_view_dependency_relations(hierarchy)

            self.logger.info("✅ [Full Sync] Snowflake full sync completed")
        except Exception as e:
            self.sync_stats.errors += 1
            self.logger.error(f"❌ [Full Sync] Error: {e}", exc_info=True)
            raise

    def _compute_definition_hash(self, definition: Optional[str]) -> str:
        """Compute MD5 hash of a view definition for change detection."""
        if not definition:
            return ""
        return hashlib.md5(definition.encode('utf-8')).hexdigest()

    def _compute_column_signature(self, columns: List[Dict[str, Any]]) -> str:
        """
        Compute a hash signature of column names and types for schema change detection.
        
        This allows detecting structural changes to tables (added/removed/modified columns)
        even when row_count and bytes haven't changed.
        """
        if not columns:
            return ""
        sig = "|".join(
            f"{c.get('name', '')}:{c.get('data_type', '')}" 
            for c in sorted(columns, key=lambda x: x.get('name', ''))
        )
        return hashlib.md5(sig.encode()).hexdigest()


    async def _ensure_stream_exists(self, table_fqn: str) -> Optional[str]:
        """
        Create or verify a Snowflake Stream exists for a table.
        
        Streams provide true CDC (Change Data Capture) tracking for row-level changes.
        Returns the stream name if successful, None if stream creation failed.
        """
        if not self.data_source or not self.warehouse:
            return None
        
        # Build stream name from table FQN (e.g., DB.SCHEMA.TABLE -> PIPESHUB_CDC_DB_SCHEMA_TABLE)
        stream_name = f"{self._stream_prefix}{table_fqn.replace('.', '_').upper()}"
        parts = table_fqn.split(".")
        if len(parts) != 3:
            return None
        
        database, schema, table = parts
        full_stream_name = f"{database}.{schema}.{stream_name}"
        
        try:
            # Check if stream already exists
            check_sql = f"SHOW STREAMS LIKE '{stream_name}' IN SCHEMA {database}.{schema}"
            async with self.rate_limiter:
                response = await self.data_source.execute_sql(
                    statement=check_sql,
                    database=database,
                    warehouse=self.warehouse,
                )
            
            if response.success and response.data:
                rows = response.data.get("data", [])
                if rows:
                    self.logger.debug(f"Stream {full_stream_name} already exists")
                    return full_stream_name
            
            # Create the stream if it doesn't exist
            create_sql = f"""
                CREATE STREAM IF NOT EXISTS {full_stream_name}
                ON TABLE {table_fqn}
                SHOW_INITIAL_ROWS = FALSE
                APPEND_ONLY = FALSE
            """
            async with self.rate_limiter:
                response = await self.data_source.execute_sql(
                    statement=create_sql,
                    database=database,
                    warehouse=self.warehouse,
                )
            
            if response.success:
                self.logger.info(f"✅ Created Snowflake Stream: {full_stream_name}")
                return full_stream_name
            else:
                self.logger.warning(f"Failed to create stream {full_stream_name}: {response.error}")
                return None
                
        except Exception as e:
            self.logger.warning(f"Error managing stream for {table_fqn}: {e}")
            return None

    async def _check_stream_has_changes(self, stream_name: str) -> Tuple[bool, int]:
        """
        Check if a Snowflake Stream has any pending changes.
        
        Returns:
            Tuple of (has_changes: bool, change_count: int)
        """
        if not self.data_source or not self.warehouse:
            return False, 0
        
        parts = stream_name.split(".")
        if len(parts) != 3:
            return False, 0
        
        database = parts[0]
        
        try:
            # Use SYSTEM$STREAM_HAS_DATA for efficient check
            check_sql = f"SELECT SYSTEM$STREAM_HAS_DATA('{stream_name}') as HAS_DATA"
            async with self.rate_limiter:
                response = await self.data_source.execute_sql(
                    statement=check_sql,
                    database=database,
                    warehouse=self.warehouse,
                )
            
            if response.success and response.data:
                rows = response.data.get("data", [])
                if rows and len(rows) > 0:
                    has_data = rows[0][0] if isinstance(rows[0], list) else rows[0].get("HAS_DATA", False)
                    if has_data in (True, "TRUE", "true", 1, "1"):
                        # Get count of changes
                        count_sql = f"SELECT COUNT(*) as CNT FROM {stream_name}"
                        async with self.rate_limiter:
                            count_response = await self.data_source.execute_sql(
                                statement=count_sql,
                                database=database,
                                warehouse=self.warehouse,
                            )
                        count = 0
                        if count_response.success and count_response.data:
                            count_rows = count_response.data.get("data", [])
                            if count_rows:
                                count = int(count_rows[0][0]) if isinstance(count_rows[0], list) else int(count_rows[0].get("CNT", 0))
                        return True, count
            
            return False, 0
            
        except Exception as e:
            self.logger.warning(f"Error checking stream {stream_name}: {e}")
            return False, 0

    async def _consume_stream_changes(self, stream_name: str) -> List[Dict[str, Any]]:
        """
        Consume changes from a Snowflake Stream.
        
        Returns list of changed rows with metadata (METADATA$ACTION, METADATA$ISUPDATE, METADATA$ROW_ID).
        Note: Reading from a stream advances the stream offset, so changes are only returned once.
        """
        if not self.data_source or not self.warehouse:
            return []
        
        parts = stream_name.split(".")
        if len(parts) != 3:
            return []
        
        database = parts[0]
        
        try:
            sql = f"""
                SELECT *, METADATA$ACTION, METADATA$ISUPDATE, METADATA$ROW_ID
                FROM {stream_name}
                LIMIT 10000
            """
            async with self.rate_limiter:
                response = await self.data_source.execute_sql(
                    statement=sql,
                    database=database,
                    warehouse=self.warehouse,
                )
            
            if response.success and response.data:
                # Parse result into list of dicts
                meta = response.data.get("resultSetMetaData", {})
                row_type = meta.get("rowType", [])
                columns = [col.get("name", f"col_{i}") for i, col in enumerate(row_type)]
                
                changes = []
                for row in response.data.get("data", []):
                    if isinstance(row, list):
                        changes.append(dict(zip(columns, row)))
                    elif isinstance(row, dict):
                        changes.append(row)
                return changes
            
            return []
            
        except Exception as e:
            self.logger.warning(f"Error consuming stream {stream_name}: {e}")
            return []


    async def _save_checkpoint(self, checkpoint_data: Dict[str, Any]) -> None:
        """Save checkpoint for resumable sync."""
        try:
            await self.record_sync_point.update_sync_point(
                self._checkpoint_key,
                checkpoint_data
            )
            self.logger.debug(f"📍 Checkpoint saved: {checkpoint_data.get('current_database', 'unknown')}.{checkpoint_data.get('current_schema', 'unknown')}")
        except Exception as e:
            self.logger.warning(f"Failed to save checkpoint: {e}")

    async def _load_checkpoint(self) -> Optional[Dict[str, Any]]:
        """Load checkpoint for resuming sync."""
        try:
            checkpoint = await self.record_sync_point.read_sync_point(self._checkpoint_key)
            return checkpoint
        except Exception:
            return None

    async def _clear_checkpoint(self) -> None:
        """Clear checkpoint after successful sync completion."""
        try:
            await self.record_sync_point.update_sync_point(self._checkpoint_key, {})
            self.logger.debug("📍 Checkpoint cleared")
        except Exception as e:
            self.logger.warning(f"Failed to clear checkpoint: {e}")

    def _should_skip_to_checkpoint(
        self, 
        checkpoint: Optional[Dict[str, Any]], 
        current_db: str, 
        current_schema: Optional[str] = None
    ) -> bool:
        """Check if we should skip this database/schema based on checkpoint."""
        if not checkpoint:
            return False
        
        checkpoint_db = checkpoint.get("current_database")
        checkpoint_schema = checkpoint.get("current_schema")
        
        if not checkpoint_db:
            return False
        
        # Skip databases that come before checkpoint
        if current_db < checkpoint_db:
            return True
        
        # If same database, check schema
        if current_db == checkpoint_db and current_schema and checkpoint_schema:
            if current_schema < checkpoint_schema:
                return True
        
        return False


    async def _batch_get_records_by_external_ids(
        self, 
        external_ids: List[str]
    ) -> Dict[str, Record]:
        """
        Batch fetch records by external IDs for optimized deletion detection.
        
        Returns a dict mapping external_id -> Record for found records.
        """
        if not external_ids:
            return {}
        
        result: Dict[str, Record] = {}
        
        # Process in batches to avoid query size limits
        batch_size = 100
        async with self.data_store_provider.transaction() as tx_store:
            for i in range(0, len(external_ids), batch_size):
                batch = external_ids[i:i + batch_size]
                for ext_id in batch:
                    try:
                        record = await tx_store.get_record_by_external_id(
                            connector_id=self.connector_id,
                            external_id=ext_id
                        )
                        if record:
                            result[ext_id] = record
                    except Exception as e:
                        self.logger.warning(f"Error fetching record {ext_id}: {e}")
        
        return result


    async def _mark_records_for_reindex(self, external_ids: List[str], reason: str = "content_changed") -> None:
        """
        Mark records for selective re-indexing based on detected changes.
        
        This triggers re-indexing only for affected records rather than full re-sync.
        """
        if not external_ids:
            return
        
        self.logger.info(f"🔄 Marking {len(external_ids)} records for re-indexing (reason: {reason})")
        
        records_to_reindex: List[Record] = []
        records_map = await self._batch_get_records_by_external_ids(external_ids)
        
        for ext_id, record in records_map.items():
            records_to_reindex.append(record)
        
        if records_to_reindex:
            await self.data_entities_processor.reindex_existing_records(records_to_reindex)
            self.sync_stats.records_reindexed += len(records_to_reindex)
            self.logger.info(f"✅ Queued {len(records_to_reindex)} records for re-indexing")

    async def _run_incremental_sync_internal(self, prev_state: Dict[str, Any]) -> None:
        """
        Internal method for incremental synchronization with enhanced features:
        
        - Checkpoint/Resume: Saves progress and resumes from last checkpoint on failure
        - Snowflake Streams: Uses CDC streams for accurate row-level change detection
        - Enhanced change detection: Uses last_altered, column_signature, and last_modified
        - Batch deletion: Optimized deletion detection with batch queries
        - Selective re-indexing: Only re-indexes changed content
        
        Compares current state with previous state to detect:
        - New objects (in current but not in previous)
        - Updated objects (metadata/schema/content changed)
        - Deleted objects (in previous but not in current)
        """
        try:
            self.logger.info("🔄 [Incremental Sync] Starting incremental sync...")
            self._record_id_cache.clear()

            # Check for existing checkpoint (resume support)
            checkpoint = await self._load_checkpoint()
            if checkpoint and checkpoint.get("current_database"):
                self.logger.info(f"📍 [Checkpoint] Resuming from: {checkpoint.get('current_database')}.{checkpoint.get('current_schema', '*')}")
                self.sync_stats.checkpoint_resumed = True

            # Get filter values
            selected_dbs, selected_schemas, selected_tables, selected_views, selected_stages, selected_files = self._get_filter_values()

            # Fetch current hierarchy
            hierarchy = await self.data_fetcher.fetch_all(
                database_filter=selected_dbs,
                include_files=True,
                include_relationships=True,
            )
            self.logger.info(f"Fetched hierarchy: {hierarchy.summary()}")

            # Build new sync state (dict structure for next incremental sync)
            new_sync_state = {
                "last_sync_time": get_epoch_timestamp_in_ms(),
                "databases": [],
                "schemas": {},
                "stages": {},
                "tables": {},
                "views": {},
                "files": {},
                "streams": {},  # Track Snowflake Streams for CDC
            }

            # Previous state data
            prev_databases = set(prev_state.get("databases", []))
            prev_schemas = set(prev_state.get("schemas", {}).keys())
            prev_stages = set(prev_state.get("stages", {}).keys())
            prev_tables = prev_state.get("tables", {})
            prev_views = prev_state.get("views", {})
            prev_files = prev_state.get("files", {})
            prev_streams = prev_state.get("streams", {})

            # Log previous state for debugging
            self.logger.info(
                f"📋 [Incremental Sync] Previous state: databases={len(prev_databases)}, "
                f"tables={len(prev_tables)}, views={len(prev_views)}, files={len(prev_files)}"
            )

            # NOTE: We don't track current state for deletion detection anymore.
            # Snowflake doesn't have a reliable change API, so comparing previous vs current
            # state is risky - if fetch fails or state is corrupted, we'd falsely delete records.
            # Instead, incremental sync only handles additions and updates.

            # Process databases (always sync all - they're small)
            await self._sync_databases(hierarchy.databases)
            self.sync_stats.databases_synced = len(hierarchy.databases)
            new_sync_state["databases"] = [db.name for db in hierarchy.databases]

            # Lists to collect records for batch processing
            tables_to_sync: List[Tuple[str, str, SnowflakeTable]] = []
            tables_to_reindex: List[str] = []  # For selective re-indexing (stream changes)
            views_to_sync: List[Tuple[str, str, SnowflakeView]] = []
            files_to_sync: List[Tuple[str, str, str, SnowflakeFile]] = []

            for db in hierarchy.databases:
                # Checkpoint/Resume: Skip databases before checkpoint
                if self._should_skip_to_checkpoint(checkpoint, db.name):
                    self.logger.debug(f"⏭️ Skipping database {db.name} (before checkpoint)")
                    continue
                
                schemas = hierarchy.schemas.get(db.name, [])
                
                # Apply schema filter if specified
                if selected_schemas:
                    schemas = [s for s in schemas if f"{db.name}.{s.name}" in selected_schemas]
                
                # Always sync namespaces (they're small)
                await self._sync_namespaces(db.name, schemas)

                for schema in schemas:
                    # Checkpoint/Resume: Skip schemas before checkpoint
                    if self._should_skip_to_checkpoint(checkpoint, db.name, schema.name):
                        self.logger.debug(f"⏭️ Skipping schema {db.name}.{schema.name} (before checkpoint)")
                        continue
                    
                    # Save checkpoint for resume
                    await self._save_checkpoint({
                        "current_database": db.name,
                        "current_schema": schema.name,
                    })
                    
                    schema_fqn = f"{db.name}.{schema.name}"
                    new_sync_state["schemas"][schema_fqn] = {}
                    self.sync_stats.schemas_synced += 1

                    schema_key = f"{db.name}.{schema.name}"
                    tables = hierarchy.tables.get(schema_key, [])
                    views = hierarchy.views.get(schema_key, [])
                    stages = hierarchy.stages.get(schema_key, [])

                    # Apply filters
                    if selected_tables:
                        tables = [t for t in tables if t.fqn in selected_tables]
                    if selected_views:
                        views = [v for v in views if v.fqn in selected_views]
                    if selected_stages:
                        stages = [s for s in stages if s.fqn in selected_stages]

                    # Process tables - enhanced change detection with streams
                    for table in tables:
                        # Compute column signature for schema change detection
                        column_sig = self._compute_column_signature(table.columns) if table.columns else ""
                        
                        # Store enhanced sync state
                        new_sync_state["tables"][table.fqn] = {
                            "row_count": table.row_count,
                            "bytes": table.bytes,
                            "last_altered": table.last_altered,
                            "column_signature": column_sig,
                        }

                        prev_table = prev_tables.get(table.fqn)
                        if prev_table is None:
                            # New table - create stream for future CDC
                            tables_to_sync.append((db.name, schema.name, table))
                            self.sync_stats.tables_new += 1
                            
                            # Create Snowflake Stream for new table
                            if self._enable_streams:
                                stream_name = await self._ensure_stream_exists(table.fqn)
                                if stream_name:
                                    new_sync_state["streams"][table.fqn] = stream_name
                        else:
                            # Check for schema changes (column structure changed)
                            prev_col_sig = prev_table.get("column_signature", "")
                            if column_sig and prev_col_sig and column_sig != prev_col_sig:
                                self.logger.info(f"📐 Schema change detected for {table.fqn}")
                                tables_to_sync.append((db.name, schema.name, table))
                                self.sync_stats.tables_schema_changed += 1
                                continue
                            
                            # Check Snowflake Stream for row-level changes (if stream exists)
                            stream_name = prev_streams.get(table.fqn)
                            if stream_name and self._enable_streams:
                                has_changes, change_count = await self._check_stream_has_changes(stream_name)
                                if has_changes:
                                    self.logger.info(f"🌊 Stream detected {change_count} changes for {table.fqn}")
                                    # Mark for re-indexing (content changed, not structure)
                                    tables_to_reindex.append(table.fqn)
                                    self.sync_stats.tables_stream_changes += 1
                                    # Consume stream to advance offset
                                    await self._consume_stream_changes(stream_name)
                                new_sync_state["streams"][table.fqn] = stream_name
                            
                            # Fallback: Check metadata changes (last_altered, row_count, bytes)
                            elif self._is_table_changed(table, prev_table):
                                tables_to_sync.append((db.name, schema.name, table))
                                self.sync_stats.tables_updated += 1
                            else:
                                self.sync_stats.tables_unchanged += 1

                    # Process views - check for changes
                    for view in views:
                        definition_hash = self._compute_definition_hash(view.definition)
                        new_sync_state["views"][view.fqn] = {
                            "definition_hash": definition_hash,
                        }

                        prev_view = prev_views.get(view.fqn)
                        if prev_view is None:
                            # New view
                            views_to_sync.append((db.name, schema.name, view))
                            self.sync_stats.views_new += 1
                        elif prev_view.get("definition_hash") != definition_hash:
                            # Updated view
                            views_to_sync.append((db.name, schema.name, view))
                            self.sync_stats.views_updated += 1
                        else:
                            self.sync_stats.views_unchanged += 1

                    # Always sync stages (they're small)
                    await self._sync_stages(db.name, schema.name, stages)
                    for stage in stages:
                        new_sync_state["stages"][stage.fqn] = {}
                        self.sync_stats.stages_synced += 1

                    # Process files - check for changes (using last_modified with MD5 fallback)
                    for stage in stages:
                        stage_key = f"{db.name}.{schema.name}.{stage.name}"
                        files = hierarchy.files.get(stage_key, [])

                        # Apply file filter if specified
                        if selected_files:
                            files = [f for f in files if f"{stage_key}/{f.relative_path}" in selected_files]

                        for file in files:
                            if file.is_folder:
                                continue

                            file_id = f"{stage_key}/{file.relative_path}"
                            
                            # Store enhanced sync state with last_modified for faster change detection
                            new_sync_state["files"][file_id] = {
                                "md5": file.md5,
                                "size": file.size,
                                "last_modified": file.last_modified,  # Primary change indicator
                            }

                            prev_file = prev_files.get(file_id)
                            if prev_file is None:
                                # New file
                                files_to_sync.append((db.name, schema.name, stage.name, file))
                                self.sync_stats.files_new += 1
                            elif self._is_file_changed(file, prev_file):
                                # Updated file
                                files_to_sync.append((db.name, schema.name, stage.name, file))
                                self.sync_stats.files_updated += 1
                            else:
                                self.sync_stats.files_unchanged += 1

            # Sync changed/new tables
            if tables_to_sync:
                self.logger.info(f"📝 [Incremental Sync] Syncing {len(tables_to_sync)} new/updated tables")
                for db_name, schema_name, table in tables_to_sync:
                    await self._sync_tables(db_name, schema_name, [table])

            # Sync changed/new views
            if views_to_sync:
                self.logger.info(f"📝 [Incremental Sync] Syncing {len(views_to_sync)} new/updated views")
                for db_name, schema_name, view in views_to_sync:
                    await self._sync_views(db_name, schema_name, [view])

            # Sync changed/new files
            if files_to_sync:
                self.logger.info(f"📝 [Incremental Sync] Syncing {len(files_to_sync)} new/updated files")
                # Group by stage
                files_by_stage: Dict[str, List[SnowflakeFile]] = {}
                
                for db_name, schema_name, stage_name, file in files_to_sync:
                    key = f"{db_name}.{schema_name}.{stage_name}"
                    if key not in files_by_stage:
                        files_by_stage[key] = []
                    files_by_stage[key].append(file)
                
                for stage_key, files in files_by_stage.items():
                    parts = stage_key.split(".")
                    if len(parts) == 3:
                        await self._sync_stage_files(
                            parts[0], parts[1], parts[2], files
                        )

            # NOTE: Deletion detection is disabled for Snowflake incremental sync.
            # Unlike Google Drive or Nextcloud which have change/activity APIs that explicitly
            # report deletions, Snowflake doesn't have such an API. Comparing "previous state"
            # vs "current state" is risky because:
            # 1. If previous sync state is corrupted/empty, all records appear as "deleted"
            # 2. If current fetch fails partially, unfetched items appear as "deleted"
            # 
            # To handle deletions safely, users should:
            # - Run a manual full re-sync periodically, OR
            # - Use Snowflake Streams (CDC) for true change tracking when available
            self.logger.info("📋 [Incremental Sync] Deletion detection skipped (Snowflake lacks change API)")

            # Selective re-indexing for tables with stream changes (content changed, not structure)
            if tables_to_reindex:
                await self._mark_records_for_reindex(tables_to_reindex, reason="stream_cdc_changes")

            # Create relations for new objects
            await self._create_foreign_key_relations(hierarchy.foreign_keys)
            await self._create_view_dependency_relations(hierarchy)

            # Save new sync state
            await self.record_sync_point.update_sync_point(
                self._sync_state_key,
                new_sync_state
            )
            self.logger.info(f"⚓ [Incremental Sync] Updated sync state with {len(new_sync_state['streams'])} streams tracked")

            # Clear checkpoint on successful completion
            await self._clear_checkpoint()

            self.logger.info("✅ [Incremental Sync] Completed")

        except Exception as e:
            self.sync_stats.errors += 1
            self.logger.error(f"❌ [Incremental Sync] Error: {e}", exc_info=True)
            self.logger.info("📍 Checkpoint preserved for resume on next sync")
            raise

    def _is_table_changed(self, table: SnowflakeTable, prev_state: Dict[str, Any]) -> bool:
        """
        Check if a table has changed based on multiple indicators.
        
        Priority order:
        1. last_altered timestamp (most reliable)
        2. row_count changes
        3. bytes changes (size)
        """
        # Check last_altered first (most reliable for structural changes)
        prev_last_altered = prev_state.get("last_altered")
        if table.last_altered and prev_last_altered:
            if table.last_altered != prev_last_altered:
                return True
        
        # Fallback to row_count and bytes comparison
        return (
            table.row_count != prev_state.get("row_count") or
            table.bytes != prev_state.get("bytes")
        )

    def _is_file_changed(self, file: SnowflakeFile, prev_state: Dict[str, Any]) -> bool:
        """
        Check if a file has changed using last_modified with MD5 as fallback.
        
        Priority order:
        1. last_modified timestamp (fast comparison)
        2. md5 hash (fallback for content verification)
        """
        # Check last_modified first (faster)
        prev_last_modified = prev_state.get("last_modified")
        if file.last_modified and prev_last_modified:
            if file.last_modified != prev_last_modified:
                return True
            # If timestamps match, no change
            return False
        
        # Fallback to MD5 hash comparison
        return file.md5 != prev_state.get("md5")

        #not used
    async def _process_deletions_batch(
        self,
        deleted_databases: Set[str],
        deleted_schemas: Set[str],
        deleted_stages: Set[str],
        deleted_tables: Set[str],
        deleted_views: Set[str],
        deleted_files: Set[str],
    ) -> None:
        """
        Process deleted objects using batch queries for optimized deletion detection.
        
        Uses batch fetching to reduce N+1 query problems.
        Deletion is cascaded: if a database is deleted, all its children are also deleted.
        """
        total_deletions = (
            len(deleted_databases) + len(deleted_schemas) + len(deleted_stages) +
            len(deleted_tables) + len(deleted_views) + len(deleted_files)
        )
        
        if total_deletions == 0:
            self.logger.info("🗑️  [Deletions] No deletions detected")
            return

        self.logger.info(
            f"🗑️  [Deletions] Processing {total_deletions} deletions: "
            f"DBs={len(deleted_databases)}, Schemas={len(deleted_schemas)}, "
            f"Stages={len(deleted_stages)}, Tables={len(deleted_tables)}, "
            f"Views={len(deleted_views)}, Files={len(deleted_files)}"
        )

        # Batch fetch all records to delete (optimized - single query per type)
        all_external_ids = list(deleted_tables) + list(deleted_views) + list(deleted_files)
        records_map = await self._batch_get_records_by_external_ids(all_external_ids)
        
        self.logger.debug(f"🗑️  Found {len(records_map)} records to delete out of {len(all_external_ids)} requested")

        # Process deletions by type
        for table_fqn in deleted_tables:
            record = records_map.get(table_fqn)
            if record:
                try:
                    await self.data_entities_processor.on_record_deleted(record_id=record.id)
                    self.sync_stats.tables_deleted += 1
                except Exception as e:
                    self.logger.error(f"Error deleting table {table_fqn}: {e}")
                    self.sync_stats.errors += 1

        for view_fqn in deleted_views:
            record = records_map.get(view_fqn)
            if record:
                try:
                    await self.data_entities_processor.on_record_deleted(record_id=record.id)
                    self.sync_stats.views_deleted += 1
                except Exception as e:
                    self.logger.error(f"Error deleting view {view_fqn}: {e}")
                    self.sync_stats.errors += 1

        for file_id in deleted_files:
            record = records_map.get(file_id)
            if record:
                try:
                    await self.data_entities_processor.on_record_deleted(record_id=record.id)
                    self.sync_stats.files_deleted += 1
                except Exception as e:
                    self.logger.error(f"Error deleting file {file_id}: {e}")
                    self.sync_stats.errors += 1

        self.logger.info(
            f"✅ [Deletions] Completed: Tables={self.sync_stats.tables_deleted}, "
            f"Views={self.sync_stats.views_deleted}, Files={self.sync_stats.files_deleted}"
        )

    # Keep legacy method for backwards compatibility
    async def _process_deletions(
        self,
        deleted_databases: Set[str],
        deleted_schemas: Set[str],
        deleted_stages: Set[str],
        deleted_tables: Set[str],
        deleted_views: Set[str],
        deleted_files: Set[str],
    ) -> None:
        """Legacy deletion processing - redirects to batch version."""
        await self._process_deletions_batch(
            deleted_databases, deleted_schemas, deleted_stages,
            deleted_tables, deleted_views, deleted_files
        )

    async def _create_app_user(self) -> None:
        """Create AppUser entries for all active users in the organization.
        
        This establishes the userapp relation between users and this Snowflake app,
        enabling proper access control and data visibility.
        """
        try:
            all_active_users = await self.data_entities_processor.get_all_active_users()
            app_users = self.get_app_users(all_active_users)
            await self.data_entities_processor.on_new_app_users(app_users)
            self.logger.info(f"Created {len(app_users)} app users for Snowflake connector")
        except Exception as e:
            self.logger.error(f"Error creating app users: {e}", exc_info=True)
            raise

    async def _get_permissions(self) -> List[Permission]:
        """
        Get permissions for Snowflake records.

        For Snowflake connectors, permissions are granted at the organization level
        since database objects are not owned by individual users. This allows
        anyone in the organization to access the Snowflake data catalog.

        Returns:
            List of Permission objects for the organization
        """
        # Use org-level permissions for Snowflake
        # Database connectors grant access to the entire organization
        return [Permission(
            type=PermissionType.OWNER,
            entity_type=EntityType.ORG,
        )]

    async def _process_tables_generator(
        self,
        database_name: str,
        schema_name: str,
        tables: List[SnowflakeTable],
    ) -> AsyncGenerator[Tuple[Record, List[Permission]], None]:
        """
        Async generator for processing tables in a memory-efficient manner.
        
        Yields records one at a time, allowing the event loop to process other tasks
        and preventing memory buildup for large datasets.
        Tables inherit permissions from parent namespace via inheritPermissions edge.
        
        Args:
            database_name: The database name
            schema_name: The schema name
            tables: List of SnowflakeTable objects to process
            
        Yields:
            Tuple of (Record, List[Permission]) - permissions list is empty for inheritance
        """
        parent_fqn = f"{database_name}.{schema_name}"
        
        for table in tables:
            try:
                fqn = f"{database_name}.{schema_name}.{table.name}"
                record_id = str(uuid.uuid4())
                self._record_id_cache[fqn] = record_id

                frontend_url = os.getenv("FRONTEND_PUBLIC_URL", "").rstrip("/")
                weburl = f"{frontend_url}/record/{record_id}" if frontend_url else ""
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
                    weburl=weburl,
                    source_created_at=get_epoch_timestamp_in_ms(),
                    source_updated_at=get_epoch_timestamp_in_ms(),
                    size_in_bytes=table.bytes or 0,
                    size_bytes=table.bytes,
                    row_count=table.row_count,
                    version=1,
                    inherit_permissions=True,  # Inherit from parent namespace
                )
                
                # Use the correct filter key as defined in connector config
                if not self.indexing_filters.is_enabled(IndexingFilterKey.TABLES.value):
                    record.indexing_status = IndexingStatus.AUTO_INDEX_OFF.value

                yield (record, [])  # Empty permissions - will inherit from parent
                # Allow event loop to process other tasks
                await asyncio.sleep(0)
                
            except Exception as e:
                self.logger.error(f"Error processing table {table.name}: {e}", exc_info=True)
                continue

    async def _process_views_generator(
        self,
        database_name: str,
        schema_name: str,
        views: List[SnowflakeView],
    ) -> AsyncGenerator[Tuple[Record, List[Permission], SnowflakeView], None]:
        """
        Async generator for processing views in a memory-efficient manner.
        
        Yields records one at a time along with the enriched view object containing
        definition and source tables.
        Views inherit permissions from parent namespace via inheritPermissions edge.
        
        Args:
            database_name: The database name
            schema_name: The schema name
            views: List of SnowflakeView objects to process
            
        Yields:
            Tuple of (Record, List[Permission], SnowflakeView with definition)
        """
        parent_fqn = f"{database_name}.{schema_name}"
        
        for view in views:
            try:
                fqn = f"{database_name}.{schema_name}.{view.name}"
                record_id = str(uuid.uuid4())
                self._record_id_cache[fqn] = record_id

                # Fetch view definition
                definition = await self._fetch_view_definition(database_name, schema_name, view.name)
                source_tables = self._parse_source_tables(definition)
                frontend_url = os.getenv("FRONTEND_PUBLIC_URL", "").rstrip("/")
                weburl = f"{frontend_url}/record/{record_id}" if frontend_url else ""
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
                    weburl=weburl,
                    mime_type=MimeTypes.SQL_VIEW.value,
                    source_created_at=get_epoch_timestamp_in_ms(),
                    source_updated_at=get_epoch_timestamp_in_ms(),
                    definition=definition,
                    source_tables=source_tables,
                    version=1,
                    inherit_permissions=True,  
                )
                # Use the correct filter key as defined in connector config
                if not self.indexing_filters.is_enabled(IndexingFilterKey.VIEWS.value):
                    record.indexing_status = IndexingStatus.AUTO_INDEX_OFF.value
                # Enrich view with definition and source tables (for streaming later)
                view.definition = definition
                view.source_tables = source_tables
                
                yield (record, [], view)  # Empty permissions - will inherit from parent
                # Allow event loop to process other tasks
                await asyncio.sleep(0)
                
            except Exception as e:
                self.logger.error(f"Error processing view {view.name}: {e}", exc_info=True)
                continue

    async def _process_stage_files_generator(
        self,
        stage_fqn: str,
        files: List[SnowflakeFile],
    ) -> AsyncGenerator[Tuple[FileRecord, List[Permission]], None]:
        """
        Async generator for processing stage files in a memory-efficient manner.
        Files inherit permissions from parent stage via inheritPermissions edge.
        
        Args:
            stage_fqn: Fully qualified stage name (database.schema.stage)
            files: List of SnowflakeFile objects to process
            
        Yields:
            Tuple of (FileRecord, List[Permission]) - permissions list is empty for inheritance
        """
        for file in files:
            try:
                if file.is_folder:
                    continue
                
                self.logger.info(f"Processing file: {file.relative_path} in stage {stage_fqn}")
                    
                file_id = f"{stage_fqn}/{file.relative_path}"
                ext = get_file_extension(file.relative_path)
                mime_type = get_mimetype_from_path(file.relative_path)

                frontend_url = os.getenv("FRONTEND_PUBLIC_URL", "").rstrip("/")
                weburl = f"{frontend_url}/record/{file_id}" if frontend_url else ""

                record = FileRecord(
                    id=str(uuid.uuid4()),
                    record_name=file.file_name,
                    record_type=RecordType.FILE,
                    record_group_type=RecordGroupType.STAGE.value,
                    external_record_group_id=stage_fqn,
                    external_record_id=file_id,
                    origin=OriginTypes.CONNECTOR.value,
                    connector_name=self.connector_name,
                    connector_id=self.connector_id,
                    mime_type=mime_type,
                    is_file=True,
                    extension=ext,
                    path=file.relative_path,
                    weburl=weburl,
                    size_in_bytes=file.size,
                    parent_external_record_id=stage_fqn,
                    parent_record_type=None,
                    etag=file.md5,
                    version=1,
                    inherit_permissions=True,  # Inherit from parent stage
                )
                
                # Use the correct filter key as defined in connector config
                if not self.indexing_filters.is_enabled(IndexingFilterKey.STAGE_FILES.value):
                    record.indexing_status = IndexingStatus.AUTO_INDEX_OFF.value

                yield (record, [])  
                await asyncio.sleep(0)
                
            except Exception as e:
                self.logger.error(f"Error processing file {file.relative_path}: {e}", exc_info=True)
                continue

    async def _sync_databases(self, databases: List[SnowflakeDatabase]) -> None:
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
        self, database_name: str, schemas: List[SnowflakeSchema]
    ) -> None:
        if not schemas:
            return
        # Namespaces inherit permissions from parent database - no direct permissions needed
        groups = []
        for schema in schemas:
            fqn = f"{database_name}.{schema.name}"
            rg = RecordGroup(
                name=schema.name,
                external_group_id=fqn,
                group_type=RecordGroupType.SQL_NAMESPACE,
                connector_name=self.connector_name,
                connector_id=self.connector_id,
                description=schema.comment or f"Snowflake Namespace: {fqn}",
                parent_external_group_id=database_name,
                inherit_permissions=True,  # Inherit from parent database
            )
            groups.append((rg, []))  # Empty permissions - will inherit from parent
        await self.data_entities_processor.on_new_record_groups(groups)
        self.logger.info(f"Synced {len(groups)} namespaces in {database_name}")

    async def _sync_stages(
        self, database_name: str, schema_name: str, stages: List[SnowflakeStage]
    ) -> None:
        if not stages:
            return
        # Stages inherit permissions from parent namespace - no direct permissions needed
        groups = []
        parent_fqn = f"{database_name}.{schema_name}"
        for stage in stages:
            fqn = f"{database_name}.{schema_name}.{stage.name}"
            rg = RecordGroup(
                name=stage.name,
                external_group_id=fqn,
                group_type=RecordGroupType.STAGE,
                connector_name=self.connector_name,
                connector_id=self.connector_id,
                description=stage.comment or f"Snowflake Stage: {fqn}",
                parent_external_group_id=parent_fqn,
                inherit_permissions=True,  # Inherit from parent namespace
            )
            groups.append((rg, []))  # Empty permissions - will inherit from parent
        await self.data_entities_processor.on_new_record_groups(groups)
        self.logger.info(f"Synced {len(groups)} stages in {parent_fqn}")

    async def _sync_tables(
        self, database_name: str, schema_name: str, tables: List[SnowflakeTable]
    ) -> None:
        """
        Sync tables using async generator for memory-efficient processing.
        
        Processes tables in batches, yielding to the event loop between items
        to prevent blocking on large datasets.
        Tables inherit permissions from parent namespace via inheritPermissions edge.
        """
        if not tables:
            return
        # Tables inherit permissions from parent namespace - no direct permissions needed
        batch: List[Tuple[Record, List[Permission]]] = []
        parent_fqn = f"{database_name}.{schema_name}"
        total_synced = 0

        async for record, perms in self._process_tables_generator(
            database_name, schema_name, tables
        ):
            batch.append((record, perms))
            total_synced += 1

            if len(batch) >= self.batch_size:
                self.logger.debug(f"Processing batch of {len(batch)} tables")
                await self.data_entities_processor.on_new_records(batch)
                batch = []

        if batch:
            await self.data_entities_processor.on_new_records(batch)
            
        self.logger.info(f"Synced {total_synced} tables in {parent_fqn}")

    async def _fetch_table_rows(
        self, database_name: str, schema_name: str, table_name: str,
        limit: Optional[int] = None
    ) -> List[List[Any]]:
        """
        Fetch rows from a table with optional limit.
        
        Args:
            database_name: Database name
            schema_name: Schema name
            table_name: Table name
            limit: Maximum number of rows to fetch (defaults to SNOWFLAKE_TABLE_ROW_LIMIT env var)
        
        Returns:
            List of rows as lists
        """
        if not self.data_source or not self.warehouse:
            return []
        
        row_limit = limit if limit is not None else SNOWFLAKE_TABLE_ROW_LIMIT
        sql = f"SELECT * FROM {database_name}.{schema_name}.{table_name} LIMIT {row_limit}"
        
        try:
            async with self.rate_limiter:
                response = await self.data_source.execute_sql(
                    statement=sql,
                    database=database_name,
                    warehouse=self.warehouse,
                )
            if response.success and response.data:
                return response.data.get("data", [])
        except Exception as e:
            self.logger.warning(f"Failed to fetch rows for {table_name}: {e}")
        return []

    async def _fetch_table_metadata(
        self, database_name: str, schema_name: str, table_name: str
    ) -> Optional[SnowflakeTable]:
        """Fetch metadata for a single table (columns, pks, fks) for streaming."""
        # Simple implementation: Re-use data fetcher logic or cache?
        # Since streaming happens later, we might not have the cache.
        # We should ideally refactor DataFetcher to allow fetching single table meta.
        # For now, we might need to rely on list_tables/files or similar pattern.
        # Efficient way: Use DESCRIBE or SHOW columns.
        
        # NOTE: For this refactor, we assume we can fetch what we need. 
        # But 'stream_record' needs to be efficient.
        # We will assume we can use the existing `fetch_all` logic on a small scope or add specific methods.
        # For simplicity in this step, we will use data_fetcher methods if available, or quick queries.
        
        # Returning None to signal we need to implement this helper if strictly needed.
        # Actually stream_record implementation below will handle the logic.
        return None

    async def _sync_views(
        self, database_name: str, schema_name: str, views: List[SnowflakeView]
    ) -> None:
        """
        Sync views using async generator for memory-efficient processing.
        
        Processes views in batches, fetching definitions one at a time and
        yielding to the event loop to prevent blocking on large datasets.
        Views inherit permissions from parent namespace via inheritPermissions edge.
        """
        if not views:
            return
        # Views inherit permissions from parent namespace - no direct permissions needed
        batch: List[Tuple[Record, List[Permission]]] = []
        parent_fqn = f"{database_name}.{schema_name}"
        total_synced = 0
        views_with_sources = 0

        async for record, perms, enriched_view in self._process_views_generator(
            database_name, schema_name, views
        ):
            batch.append((record, perms))
            total_synced += 1
            
            # Log view enrichment for debugging
            if enriched_view.source_tables:
                views_with_sources += 1
                self.logger.debug(
                    f"View {enriched_view.fqn} enriched with {len(enriched_view.source_tables)} source tables: "
                    f"{enriched_view.source_tables}"
                )

            if len(batch) >= self.batch_size:
                self.logger.debug(f"Processing batch of {len(batch)} views")
                await self.data_entities_processor.on_new_records(batch)
                batch = []

        if batch:
            await self.data_entities_processor.on_new_records(batch)
            
        self.logger.info(f"Synced {total_synced} views in {parent_fqn} ({views_with_sources} have source tables)")

    async def _fetch_view_definition(
        self, database_name: str, schema_name: str, view_name: str
    ) -> Optional[str]:
        """
        Fetch view definition using GET_DDL() SQL function.
        
        The REST API get_view() endpoint doesn't reliably return the view definition,
        especially for secure views. Using GET_DDL() ensures we get the full definition.
        """
        if not self.data_source or not self.warehouse:
            return None
        
        try:
            # Use GET_DDL to get the full view definition
            sql = f"SELECT GET_DDL('VIEW', '{database_name}.{schema_name}.{view_name}') as DDL"
            
            response = await self.data_source.execute_sql(
                statement=sql,
                database=database_name,
                warehouse=self.warehouse,
            )
            
            if response.success and response.data:
                # Parse SQL result to extract DDL
                meta = response.data.get("resultSetMetaData", {})
                row_type = meta.get("rowType", [])
                columns = [col.get("name", f"col_{i}") for i, col in enumerate(row_type)]
                
                rows = []
                for row in response.data.get("data", []):
                    if isinstance(row, list):
                        rows.append(dict(zip(columns, row)))
                    elif isinstance(row, dict):
                        rows.append(row)
                
                if rows:
                    ddl = rows[0].get("DDL")
                    if ddl:
                        self.logger.debug(f"Successfully fetched DDL for view {view_name}, length: {len(ddl)}")
                        return ddl
                    
            self.logger.warning(f"GET_DDL returned no data for view {view_name}")
            return None
            
        except Exception as e:
            self.logger.warning(f"Failed to get view definition for {view_name} using GET_DDL: {e}")
            return None

    def _parse_source_tables(self, definition: Optional[str]) -> List[str]:
        """
        Parse source tables from a view definition SQL.
        
        Handles patterns like:
        - FROM table_name
        - FROM schema.table
        - FROM db.schema.table
        - FROM "quoted_table"
        - JOIN table_name
        """
        if not definition:
            self.logger.debug("_parse_source_tables: No definition provided")
            return []
        import re
        
        # Pattern for unquoted identifiers: table, schema.table, or db.schema.table
        unquoted_pattern = r'(?:FROM|JOIN)\s+([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*){0,2})'
        
        # Pattern for quoted identifiers: "table", "schema"."table", etc.
        quoted_pattern = r'(?:FROM|JOIN)\s+"([^"]+)"(?:\."([^"]+)")?(?:\."([^"]+)")?'
        
        matches = []
        
        # Find unquoted table references
        unquoted_matches = re.findall(unquoted_pattern, definition, re.IGNORECASE)
        for match in unquoted_matches:
            if match and not match.upper() in ('SELECT', 'WHERE', 'GROUP', 'ORDER', 'HAVING', 'LIMIT', 'UNION', 'INTERSECT', 'EXCEPT'):
                matches.append(match)
        
        # Find quoted table references
        quoted_matches = re.findall(quoted_pattern, definition, re.IGNORECASE)
        for match in quoted_matches:
            parts = [p for p in match if p]
            if parts:
                matches.append('.'.join(parts))
        
        result = list(set(matches))
        
        if not result:
            self.logger.debug(
                f"_parse_source_tables: No tables found in definition. "
                f"Unquoted matches: {unquoted_matches}, Quoted matches: {quoted_matches}. "
                f"Definition preview: {definition[:300] if definition else 'None'}..."
            )
        else:
            self.logger.debug(f"_parse_source_tables: Found {len(result)} source tables: {result}")
        
        return result

    async def _sync_stage_files(
        self,
        database_name: str,
        schema_name: str,
        stage_name: str,
        files: List[SnowflakeFile],
    ) -> None:
        """
        Sync stage files using async generators for memory-efficient processing.
        
        Uses generators to yield to the event loop between items.
        Files inherit permissions from parent stage via inheritPermissions edge.
        """
        # Files inherit permissions from parent stage - no direct permissions needed
        stage_fqn = f"{database_name}.{schema_name}.{stage_name}"
        
        total_files = 0
        batch: List[Tuple[FileRecord, List[Permission]]] = []

        # Sync files using generator
        async for record, perms in self._process_stage_files_generator(stage_fqn, files):
            batch.append((record, perms))
            total_files += 1

            if len(batch) >= self.batch_size:
                self.logger.debug(f"Processing batch of {len(batch)} files")
                await self.data_entities_processor.on_new_records(batch)
                batch = []

        if batch:
            await self.data_entities_processor.on_new_records(batch)

        self.logger.info(f"Synced {total_files} files in stage {stage_fqn}")

    async def stream_record(
        self,
        record: Record,
        user_id: Optional[str] = None,
        convertTo: Optional[str] = None
    ) -> StreamingResponse:
        """
        Stream a record's content from Snowflake.

        Handles different record types:
        - FILE: Stream file content from a stage
        - SQL_TABLE: Stream table schema and data as JSON
        - SQL_VIEW: Stream view definition and metadata as JSON

        Args:
            record: Record object containing Snowflake object information
            user_id: Optional user ID (not used for Snowflake)
            convertTo: Optional format conversion (not supported for Snowflake)

        Returns:
            StreamingResponse with record content
        """
        try:
            if not self.data_source:
                raise HTTPException(status_code=500, detail="Snowflake data source not initialized")

            # Handle Snowflake Files (Unstructured)
            if record.record_type == RecordType.FILE:
                stage_fqn = record.external_record_group_id  # database.schema.stage
                file_path = record.external_record_id.replace(f"{stage_fqn}/", "")
                parts = stage_fqn.split(".")
                if len(parts) != 3:
                    raise HTTPException(status_code=500, detail="Invalid stage FQN")
                database, schema, stage = parts[0], parts[1], parts[2]

                response = await self.data_source.get_stage_file_stream(
                    database=database, schema=schema, stage=stage, relative_path=file_path
                )

                async def file_iterator():
                    async with response as resp:
                        if resp.status >= 400:
                            raise HTTPException(status_code=resp.status, detail="Failed to fetch file")
                        async for chunk in resp.content.iter_any():
                            yield chunk

                return create_stream_record_response(
                    file_iterator(), filename=record.record_name, mime_type=record.mime_type
                )

            # Handle Snowflake Tables (Structured)
            elif record.record_type == RecordType.SQL_TABLE:
                parts = record.external_record_id.split(".")
                if len(parts) != 3:
                    raise HTTPException(status_code=500, detail="Invalid table FQN")
                database, schema, table = parts[0], parts[1], parts[2]

                if not self.data_fetcher:
                    raise HTTPException(status_code=500, detail="Data fetcher not ready")

                # Fetch Columns
                all_cols = await self.data_fetcher._fetch_all_columns_in_schema(database, schema)
                columns = all_cols.get(table, [])

                # Fetch Keys
                pks = await self.data_fetcher._fetch_primary_keys_in_schema(database, schema)
                primary_keys = [pk["column"] for pk in pks if pk["table"] == table]

                fks_list = await self.data_fetcher._fetch_foreign_keys_in_schema(database, schema)
                foreign_keys = []
                for fk in fks_list:
                    if fk.source_table == table:
                        foreign_keys.append({
                            "constraint_name": fk.constraint_name,
                            "column": fk.source_column,
                            "references_schema": fk.target_schema,
                            "references_table": fk.target_table,
                            "references_column": fk.target_column,
                        })

                # Fetch Rows
                rows = await self._fetch_table_rows(database, schema, table)
                
                # Fetch Real DDL
                ddl = await self.data_fetcher.get_table_ddl(database, schema, table)

                # Construct JSON Data
                data = {
                    "table_name": table,
                    "database_name": database,
                    "schema_name": schema,
                    "columns": columns,
                    "rows": rows,
                    "foreign_keys": foreign_keys,
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

            # Handle Snowflake Views (Structured Logic)
            elif record.record_type == RecordType.SQL_VIEW:
                parts = record.external_record_id.split(".")
                if len(parts) != 3:
                    raise HTTPException(status_code=500, detail="Invalid view FQN")
                database, schema, view_name = parts[0], parts[1], parts[2]

                # Fetch view definition using GET_DDL (reliable method)
                definition = await self._fetch_view_definition(database, schema, view_name)
                
                # Fetch metadata (is_secure, comment) from API
                view_response = await self.data_source.get_view(
                    database=database, schema=schema, name=view_name
                )
                view_data = view_response.data if view_response.success else {}
                
                # Parse source tables from definition
                source_tables = self._parse_source_tables(definition)
                is_secure = view_data.get("is_secure", False) or view_data.get("isSecure", False)
                comment = view_data.get("comment") or ""
                
                # Log if definition is empty (could be a secure view or API issue)
                if not definition:
                    self.logger.warning(
                        f"View {view_name} has no definition. is_secure={is_secure}. "
                        f"This may be a secure view or GET_DDL may have failed."
                    )

                # Fetch DDLs for source tables (for blob storage context)
                source_table_ddls: Dict[str, str] = {}
                source_table_summaries: List[str] = []
                for src_table_fqn in source_tables:
                    src_parts = src_table_fqn.split(".")
                    if len(src_parts) == 3:
                        src_db, src_schema, src_table = src_parts
                    elif len(src_parts) == 2:
                        # schema.table format - use current database
                        src_db, src_schema, src_table = database, src_parts[0], src_parts[1]
                    elif len(src_parts) == 1:
                        # just table name - use current database.schema
                        src_db, src_schema, src_table = database, schema, src_parts[0]
                    else:
                        continue
                    
                    try:
                        if self.data_fetcher:
                            ddl = await self.data_fetcher.get_table_ddl(src_db, src_schema, src_table)
                            if ddl:
                                source_table_ddls[src_table_fqn] = ddl
                                source_table_summaries.append(f"Table {src_table_fqn}: {ddl[:500]}..." if len(ddl) > 500 else f"Table {src_table_fqn}: {ddl}")
                    except Exception as e:
                        self.logger.debug(f"Could not fetch DDL for source table {src_table_fqn}: {e}")

                data = {
                    "view_name": view_name,
                    "database_name": database,
                    "schema_name": schema,
                    "definition": definition,
                    "source_tables": source_tables,
                    "source_table_ddls": source_table_ddls,
                    "source_tables_summary": "\n".join(source_table_summaries) if source_table_summaries else "",
                    "is_secure": is_secure,
                    "comment": comment,
                }

                json_bytes = json.dumps(data, default=str).encode("utf-8")

                async def json_iterator():
                    yield json_bytes

                return create_stream_record_response(
                    json_iterator(), filename=f"{view_name}.json", mime_type=MimeTypes.SQL_VIEW.value
                )

            else:
                raise HTTPException(status_code=400, detail=f"Unsupported record type: {record.record_type}")

        except HTTPException:
            raise
        except Exception as e:
            self.logger.error(f"Error streaming record: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail=str(e))

    def get_signed_url(self, record: Record) -> Optional[str]:
        return None

    async def _create_foreign_key_relations(self, foreign_keys: List[ForeignKey]) -> None:
        """
        Create foreign key relations between tables using the entities processor.
        
        This creates FOREIGN_KEY edges in the graph database to represent
        database table relationships for lineage and dependency tracking.
        """
        if not foreign_keys:
            self.logger.info("No foreign keys to create relations for")
            return

        self.logger.info(f"Processing {len(foreign_keys)} foreign key relations")
        
        # Build RecordRelation objects for the entities processor
        relations = [
            RecordRelation(
                from_external_id=fk.source_fqn,  # database.schema.table
                to_external_id=fk.target_fqn,    # database.schema.table
                relation_type=RecordRelations.FOREIGN_KEY.value,
                connector_id=self.connector_id,
                metadata={
                    "constraintName": fk.constraint_name,
                    "sourceColumn": fk.source_column,
                    "targetColumn": fk.target_column,
                }
            )
            for fk in foreign_keys
        ]
        
        await self.data_entities_processor.on_new_record_relations(relations)

    async def _create_view_dependency_relations(self, hierarchy) -> None:
        """
        Create view dependency relations using the entities processor.
        
        This creates DEPENDS_ON edges from views to their source tables
        for lineage and dependency tracking.
        """
        relations = []
        views_processed = 0
        views_with_sources = 0
        views_without_definition = 0
        
        self.logger.info("Starting view dependency relation creation")
        
        for db in hierarchy.databases:
            for schema in hierarchy.schemas.get(db.name, []):
                schema_key = f"{db.name}.{schema.name}"
                views_in_schema = hierarchy.views.get(schema_key, [])
                self.logger.debug(f"Processing {len(views_in_schema)} views in {schema_key}")
                
                for view in views_in_schema:
                    views_processed += 1
                    view_fqn = f"{db.name}.{schema.name}.{view.name}"

                    if not view.definition:
                        views_without_definition += 1
                        self.logger.debug(f"View {view_fqn} has no definition")
                        continue
                        
                    if not view.source_tables:
                        self.logger.debug(
                            f"View {view_fqn} has definition but no parsed source_tables. "
                            f"Definition preview: {view.definition[:200] if view.definition else 'None'}..."
                        )
                        continue
                    
                    views_with_sources += 1
                    self.logger.info(f"View {view_fqn} depends on {len(view.source_tables)} tables: {view.source_tables}")

                    for source_table in view.source_tables:
                        # Build the full FQN for the source table
                        if "." not in source_table:
                            # Just table name, assume same database and schema
                            table_fqn = f"{db.name}.{schema.name}.{source_table}"
                        elif source_table.count(".") == 1:
                            # schema.table format, add database
                            table_fqn = f"{db.name}.{source_table}"
                        else:
                            # Already fully qualified
                            table_fqn = source_table

                        relations.append(RecordRelation(
                            from_external_id=view_fqn,
                            to_external_id=table_fqn,
                            relation_type=RecordRelations.DEPENDS_ON.value,
                            connector_id=self.connector_id,
                        ))
        
        self.logger.info(
            f"View dependency analysis: views_processed={views_processed}, "
            f"with_sources={views_with_sources}, no_definition={views_without_definition}, "
            f"relations_to_create={len(relations)}"
        )
        
        if relations:
            await self.data_entities_processor.on_new_record_relations(relations)

    async def test_connection_and_access(self) -> bool:
        if not self.data_source:
            return False
        try:
            response = await self.data_source.list_databases(show_limit=1)
            if response.success:
                self.logger.info("Snowflake connection test successful")
                return True
            self.logger.error(f"Connection test failed: {response.error}")
            return False
        except Exception as e:
            self.logger.error(f"Connection test failed: {e}", exc_info=True)
            return False

    async def run_incremental_sync(self) -> None:
        """
        Run incremental sync for Snowflake.

        Uses hybrid change detection:
        - Tables: Compare row_count and bytes metadata
        - Views: Compare MD5 hash of view definition
        - Staged files: Compare md5 hash from Snowflake

        Only changed objects are re-indexed. Deleted objects are detected
        by comparing current objects with previously synced objects.
        """
        self.logger.info("Starting Snowflake incremental sync")
        
        # Check if we have previous sync state
        previous_state = await self.get_sync_point(self._sync_state_key)
        
        if not previous_state or not previous_state.get("cursor"):
            self.logger.info("No previous sync state found, performing full sync")
            await self._run_full_sync_internal()
        else:
            await self._run_incremental_sync_internal(previous_state["cursor"])
        
        self.logger.info(
            f"Snowflake incremental sync completed - "
            f"Tables: {self.sync_stats.tables_synced} synced, {self.sync_stats.tables_skipped} skipped | "
            f"Views: {self.sync_stats.views_synced} synced, {self.sync_stats.views_skipped} skipped | "
            f"Files: {self.sync_stats.files_synced} synced, {self.sync_stats.files_skipped} skipped | "
            f"Deletions: {self.sync_stats.deletions_processed}"
        )

    def handle_webhook_notification(self, notification: Dict) -> None:
        """
        Handle webhook notifications from Snowflake.

        Snowflake does not support webhooks for data change notifications.
        This method raises NotImplementedError as per the base class contract.
        """
        raise NotImplementedError(
            "Snowflake does not support webhook notifications. "
            "Use scheduled sync or Snowflake STREAMS for change tracking."
        )

    async def cleanup(self) -> None:
        """
        Cleanup resources when shutting down the connector.

        Clears client references and caches to free memory.
        """
        try:
            self.logger.info("Cleaning up Snowflake connector resources")

            # Clear data source and fetcher references
            if hasattr(self, 'data_source') and self.data_source:
                self.data_source = None

            if hasattr(self, 'data_fetcher') and self.data_fetcher:
                self.data_fetcher = None

            # Clear caches
            self._record_id_cache.clear()

            # Clear config references
            self.warehouse = None
            self.account_identifier = None

            self.logger.info("Snowflake connector cleanup completed")

        except Exception as e:
            self.logger.error(f"Error during Snowflake connector cleanup: {e}", exc_info=True)

    async def reindex_records(self, records: List[Record]) -> None:
        """
        Reindex records for Snowflake.

        Checks if records still exist and have updated content at the source,
        then triggers reindexing for changed records.

        Args:
            records: List of Record objects to reindex
        """
        try:
            if not records:
                self.logger.info("No records to reindex")
                return

            self.logger.info(f"Starting reindex for {len(records)} Snowflake records")

            if not self.data_source or not self.data_fetcher:
                self.logger.error("Data source not initialized. Call init() first.")
                raise Exception("Snowflake data source not initialized")

            updated_records = []
            non_updated_records = []
            permissions = await self._get_permissions()

            for record in records:
                try:
                    # Check if record still exists at source and get latest metadata
                    is_updated = await self._check_record_at_source(record)

                    if is_updated:
                        # Record has changed, add to updated list with permissions
                        updated_records.append((record, permissions))
                    else:
                        # Record unchanged, add to non-updated list
                        non_updated_records.append(record)

                except Exception as e:
                    self.logger.error(f"Error checking record {record.id} at source: {e}")
                    # On error, try to reindex anyway
                    non_updated_records.append(record)

            # Update DB for records that changed at source
            if updated_records:
                await self.data_entities_processor.on_new_records(updated_records)
                self.logger.info(f"Updated {len(updated_records)} records that changed at source")

            # Publish reindex events for unchanged records
            if non_updated_records:
                await self.data_entities_processor.reindex_existing_records(non_updated_records)
                self.logger.info(f"Published reindex events for {len(non_updated_records)} unchanged records")

        except Exception as e:
            self.logger.error(f"Error during Snowflake reindex: {e}", exc_info=True)
            raise

    async def _check_record_at_source(self, record: Record) -> bool:
        """
        Check if a record still exists and has changed at the source.

        Args:
            record: Record to check

        Returns:
            True if record has changed, False otherwise
        """
        try:
            # For tables and views, check if they still exist
            if record.record_type in [RecordType.SQL_TABLE, RecordType.SQL_VIEW]:
                parts = record.external_record_id.split(".")
                if len(parts) != 3:
                    return False
                database, schema, name = parts[0], parts[1], parts[2]

                if record.record_type == RecordType.SQL_TABLE:
                    # Check table exists by querying information schema
                    response = await self.data_source.list_tables(
                        database=database, schema=schema
                    )
                    if response.success and response.data:
                        tables = response.data
                        exists = any(t.get("name") == name for t in tables)
                        return exists
                else:
                    # Check view exists
                    response = await self.data_source.list_views(
                        database=database, schema=schema
                    )
                    if response.success and response.data:
                        views = response.data
                        exists = any(v.get("name") == name for v in views)
                        return exists

            # For files, check if file still exists in stage
            elif record.record_type == RecordType.FILE:
                stage_fqn = record.external_record_group_id
                file_path = record.external_record_id.replace(f"{stage_fqn}/", "")
                parts = stage_fqn.split(".")
                if len(parts) != 3:
                    return False
                database, schema, stage = parts[0], parts[1], parts[2]

                response = await self.data_source.list_stage_files(
                    database=database, schema=schema, stage=stage
                )
                if response.success and response.data:
                    files = response.data
                    exists = any(f.get("name") == file_path for f in files)
                    return exists

            return False

        except Exception as e:
            self.logger.warning(f"Error checking record at source: {e}")
            return False

    async def get_filter_options(
        self,
        filter_key: str,
        page: int = 1,
        limit: int = 20,
        search: Optional[str] = None,
        cursor: Optional[str] = None
    ) -> FilterOptionsResponse:
        

        if cursor and cursor.isdigit():
            page = int(cursor)

        try:
            if filter_key == "databases":
                return await self._get_database_options(page, limit, search)
            elif filter_key == "schemas":
                return await self._get_schema_options(page, limit, search)
            elif filter_key == "tables":
                return await self._get_table_options(page, limit, search)
            elif filter_key == "views":
                return await self._get_view_options(page, limit, search)
            elif filter_key == "stages":
                return await self._get_stage_options(page, limit, search)
            elif filter_key == "files":
                return await self._get_file_options(page, limit, search)
            else:
                return FilterOptionsResponse(
                    success=False,
                    options=[],
                    page=page,
                    limit=limit,
                    has_more=False,
                    message=f"Unknown filter key: {filter_key}"
                )
        except Exception as e:
            self.logger.error(f"Error getting filter options for {filter_key}: {e}", exc_info=True)
            return FilterOptionsResponse(
                success=False,
                options=[],
                page=page,
                limit=limit,
                has_more=False,
                message=str(e)
            )

    async def _get_database_options(
        self,
        page: int,
        limit: int,
        search: Optional[str] = None
    ) -> FilterOptionsResponse:

        if not self.data_source:
            return FilterOptionsResponse(
                success=False,
                options=[],
                page=page,
                limit=limit,
                has_more=False,
                message="Snowflake data source not initialized"
            )

        try:
            # Fetch all databases
            response = await self.data_source.list_databases()

            if not response.success:
                return FilterOptionsResponse(
                    success=False,
                    options=[],
                    page=page,
                    limit=limit,
                    has_more=False,
                    message=response.error or "Failed to fetch databases"
                )

            databases = response.data or []

            # Filter by search query if provided
            if search:
                search_lower = search.lower()
                databases = [db for db in databases if search_lower in db.get("name", "").lower()]

            # Apply pagination
            start_idx = (page - 1) * limit
            end_idx = start_idx + limit
            paginated_dbs = databases[start_idx:end_idx]
            has_more = end_idx < len(databases)

            # Convert to FilterOption objects
            options = [
                FilterOption(id=db.get("name", ""), label=db.get("name", ""))
                for db in paginated_dbs
                if db.get("name")
            ]

            return FilterOptionsResponse(
                success=True,
                options=options,
                page=page,
                limit=limit,
                has_more=has_more
            )

        except Exception as e:
            self.logger.error(f"Error fetching database options: {e}", exc_info=True)
            return FilterOptionsResponse(
                success=False,
                options=[],
                page=page,
                limit=limit,
                has_more=False,
                message=str(e)
            )

    async def _get_schema_options(
        self,
        page: int,
        limit: int,
        search: Optional[str] = None
    ) -> FilterOptionsResponse:

        if not self.data_fetcher:
            return FilterOptionsResponse(
                success=False,
                options=[],
                page=page,
                limit=limit,
                has_more=False,
                message="Snowflake data fetcher not initialized"
            )

        try:
            # Get database filter to know which databases to fetch schemas from
            db_filter = self.sync_filters.get("databases")
            selected_dbs = db_filter.value if db_filter and db_filter.value else None

            # Fetch hierarchy to get schemas
            hierarchy = await self.data_fetcher.fetch_all(
                database_filter=selected_dbs,
                include_files=False,
                include_relationships=False,
            )

            # Build list of schemas with format: Database.Schema
            schemas = []
            for db in hierarchy.databases:
                db_schemas = hierarchy.schemas.get(db.name, [])
                for schema in db_schemas:
                    schema_fqn = f"{db.name}.{schema.name}"
                    schemas.append({
                        "id": schema_fqn,
                        "label": schema_fqn,
                        "name": schema.name,
                        "database": db.name
                    })

            # Filter by search query if provided
            if search:
                search_lower = search.lower()
                schemas = [s for s in schemas if search_lower in s["id"].lower()]

            # Apply pagination
            start_idx = (page - 1) * limit
            end_idx = start_idx + limit
            paginated_schemas = schemas[start_idx:end_idx]
            has_more = end_idx < len(schemas)

            options = [
                FilterOption(id=s["id"], label=s["label"])
                for s in paginated_schemas
            ]

            cursor = str(page + 1) if has_more else None

            return FilterOptionsResponse(
                success=True,
                options=options,
                page=page,
                limit=limit,
                has_more=has_more,
                cursor=cursor
            )

        except Exception as e:
            self.logger.error(f"Error fetching schema options: {e}", exc_info=True)
            return FilterOptionsResponse(
                success=False,
                options=[],
                page=page,
                limit=limit,
                has_more=False,
                message=str(e)
            )

    async def _get_table_options(
        self,
        page: int,
        limit: int,
        search: Optional[str] = None
    ) -> FilterOptionsResponse:

        if not self.data_fetcher:
            return FilterOptionsResponse(
                success=False,
                options=[],
                page=page,
                limit=limit,
                has_more=False,
                message="Snowflake data fetcher not initialized"
            )

        try:
            # Get database and schema filters
            db_filter = self.sync_filters.get("databases")
            selected_dbs = db_filter.value if db_filter and db_filter.value else None

            # Fetch hierarchy to get tables
            hierarchy = await self.data_fetcher.fetch_all(
                database_filter=selected_dbs,
                include_files=False,
                include_relationships=False,
            )

            # Build list of tables with format: Database.Schema.Table
            tables = []
            for db in hierarchy.databases:
                db_schemas = hierarchy.schemas.get(db.name, [])
                for schema in db_schemas:
                    schema_key = f"{db.name}.{schema.name}"
                    schema_tables = hierarchy.tables.get(schema_key, [])
                    for table in schema_tables:
                        table_fqn = f"{db.name}.{schema.name}.{table.name}"
                        tables.append({
                            "id": table_fqn,
                            "label": table_fqn,
                            "name": table.name
                        })

            # Filter by search query if provided
            if search:
                search_lower = search.lower()
                tables = [t for t in tables if search_lower in t["id"].lower()]

            # Apply pagination
            start_idx = (page - 1) * limit
            end_idx = start_idx + limit
            paginated_tables = tables[start_idx:end_idx]
            has_more = end_idx < len(tables)

            # Convert to FilterOption objects
            options = [
                FilterOption(id=t["id"], label=t["label"])
                for t in paginated_tables
            ]

            # Generate cursor for next page (if there are more results)
            cursor = str(page + 1) if has_more else None

            return FilterOptionsResponse(
                success=True,
                options=options,
                page=page,
                limit=limit,
                has_more=has_more,
                cursor=cursor
            )

        except Exception as e:
            self.logger.error(f"Error fetching table options: {e}", exc_info=True)
            return FilterOptionsResponse(
                success=False,
                options=[],
                page=page,
                limit=limit,
                has_more=False,
                message=str(e)
            )

    async def _get_view_options(
        self,
        page: int,
        limit: int,
        search: Optional[str] = None
    ) -> FilterOptionsResponse:

        if not self.data_fetcher:
            return FilterOptionsResponse(
                success=False,
                options=[],
                page=page,
                limit=limit,
                has_more=False,
                message="Snowflake data fetcher not initialized"
            )

        try:
            # Get database and schema filters
            db_filter = self.sync_filters.get("databases")
            # selected_dbs = db_filter.value if db_filter and db_filter.value else None
            selected_dbs = ["TEST_DB"]
            
            self.logger.info(f"Fetching view options with database filter: {selected_dbs}")

            # Fetch hierarchy to get views
            hierarchy = await self.data_fetcher.fetch_all(
                database_filter=selected_dbs,
                include_files=False,
                include_relationships=False,
            )
            
            self.logger.info(f"Fetched hierarchy: {len(hierarchy.databases)} databases, {sum(len(v) for v in hierarchy.views.values())} total views")

            # Build list of views with format: Database.Schema.View
            views = []
            for db in hierarchy.databases:
                db_schemas = hierarchy.schemas.get(db.name, [])
                for schema in db_schemas:
                    schema_key = f"{db.name}.{schema.name}"
                    schema_views = hierarchy.views.get(schema_key, [])
                    for view in schema_views:
                        view_fqn = f"{db.name}.{schema.name}.{view.name}"
                        views.append({
                            "id": view_fqn,
                            "label": view_fqn,
                            "name": view.name
                        })

            # Filter by search query if provided
            if search:
                search_lower = search.lower()
                views = [v for v in views if search_lower in v["id"].lower()]

            # Apply pagination
            start_idx = (page - 1) * limit
            end_idx = start_idx + limit
            paginated_views = views[start_idx:end_idx]
            has_more = end_idx < len(views)

            # Convert to FilterOption objects
            options = [
                FilterOption(id=v["id"], label=v["label"])
                for v in paginated_views
            ]
            
            # Generate cursor for next page (if there are more results)
            cursor = str(page + 1) if has_more else None

            return FilterOptionsResponse(
                success=True,
                options=options,
                page=page,
                limit=limit,
                has_more=has_more,
                cursor=cursor
            )

        except Exception as e:
            self.logger.error(f"Error fetching view options: {e}", exc_info=True)
            return FilterOptionsResponse(
                success=False,
                options=[],
                page=page,
                limit=limit,
                has_more=False,
                message=str(e)
            )

    async def _get_stage_options(
        self,
        page: int,
        limit: int,
        search: Optional[str] = None
    ) -> FilterOptionsResponse:

        if not self.data_fetcher:
            return FilterOptionsResponse(
                success=False,
                options=[],
                page=page,
                limit=limit,
                has_more=False,
                message="Snowflake data fetcher not initialized"
            )

        try:
            # Get database and schema filters
            db_filter = self.sync_filters.get("databases")
            selected_dbs = db_filter.value if db_filter and db_filter.value else None

            # Fetch hierarchy to get stages
            hierarchy = await self.data_fetcher.fetch_all(
                database_filter=selected_dbs,
                include_files=False,
                include_relationships=False,
            )

            # Build list of stages with format: Database.Schema.Stage
            stages = []
            for db in hierarchy.databases:
                db_schemas = hierarchy.schemas.get(db.name, [])
                for schema in db_schemas:
                    schema_key = f"{db.name}.{schema.name}"
                    schema_stages = hierarchy.stages.get(schema_key, [])
                    for stage in schema_stages:
                        stage_fqn = f"{db.name}.{schema.name}.{stage.name}"
                        stages.append({
                            "id": stage_fqn,
                            "label": stage_fqn,
                            "name": stage.name
                        })

            # Filter by search query if provided
            if search:
                search_lower = search.lower()
                stages = [s for s in stages if search_lower in s["id"].lower()]

            # Apply pagination
            start_idx = (page - 1) * limit
            end_idx = start_idx + limit
            paginated_stages = stages[start_idx:end_idx]
            has_more = end_idx < len(stages)

            # Convert to FilterOption objects
            options = [
                FilterOption(id=s["id"], label=s["label"])
                for s in paginated_stages
            ]

            # Generate cursor for next page (if there are more results)
            cursor = str(page + 1) if has_more else None

            return FilterOptionsResponse(
                success=True,
                options=options,
                page=page,
                limit=limit,
                has_more=has_more,
                cursor=cursor
            )

        except Exception as e:
            self.logger.error(f"Error fetching stage options: {e}", exc_info=True)
            return FilterOptionsResponse(
                success=False,
                options=[],
                page=page,
                limit=limit,
                has_more=False,
                message=str(e)
            )


    async def _get_file_options(
        self,
        page: int,
        limit: int,
        search: Optional[str] = None
    ) -> FilterOptionsResponse:
        """Get file options for the files sync filter."""
        if not self.data_fetcher:
            return FilterOptionsResponse(
                success=False,
                options=[],
                page=page,
                limit=limit,
                has_more=False,
                message="Snowflake data fetcher not initialized"
            )

        try:
            # Get database, schema, and stage filters
            db_filter = self.sync_filters.get("databases")
            selected_dbs = db_filter.value if db_filter and db_filter.value else None

            stage_filter = self.sync_filters.get("stages")
            selected_stages = stage_filter.value if stage_filter and stage_filter.value else None

            # Fetch hierarchy to get files
            hierarchy = await self.data_fetcher.fetch_all(
                database_filter=selected_dbs,
                include_files=True,
                include_relationships=False,
            )

            # Build list of files with format: Database.Schema.Stage/path/to/file
            files = []
            for db in hierarchy.databases:
                db_schemas = hierarchy.schemas.get(db.name, [])
                for schema in db_schemas:
                    schema_key = f"{db.name}.{schema.name}"
                    schema_stages = hierarchy.stages.get(schema_key, [])
                    
                    for stage in schema_stages:
                        stage_fqn = f"{db.name}.{schema.name}.{stage.name}"
                        
                        # Filter by selected stages if provided
                        if selected_stages and stage_fqn not in selected_stages:
                            continue
                        
                        stage_files = hierarchy.files.get(stage_fqn, [])
                        for file in stage_files:
                            if not file.is_folder:
                                file_id = f"{stage_fqn}/{file.relative_path}"
                                files.append({
                                    "id": file_id,
                                    "label": f"{stage_fqn}/{file.relative_path}",
                                    "name": file.file_name,
                                    "path": file.relative_path
                                })

            # Filter by search query if provided
            if search:
                search_lower = search.lower()
                files = [f for f in files if search_lower in f["label"].lower()]

            # Apply pagination
            start_idx = (page - 1) * limit
            end_idx = start_idx + limit
            paginated_files = files[start_idx:end_idx]
            has_more = end_idx < len(files)

            # Convert to FilterOption objects
            options = [
                FilterOption(id=f["id"], label=f["label"])
                for f in paginated_files
            ]

            # Generate cursor for next page (if there are more results)
            cursor = str(page + 1) if has_more else None

            return FilterOptionsResponse(
                success=True,
                options=options,
                page=page,
                limit=limit,
                has_more=has_more,
                cursor=cursor
            )

        except Exception as e:
            self.logger.error(f"Error fetching file options: {e}", exc_info=True)
            return FilterOptionsResponse(
                success=False,
                options=[],
                page=page,
                limit=limit,
                has_more=False,
                message=str(e)
            )


    @classmethod
    async def create_connector(
        cls,
        logger: Logger,
        data_store_provider: DataStoreProvider,
        config_service: ConfigurationService,
        connector_id: str,
        **kwargs,
    ) -> "SnowflakeConnector":
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
        )
