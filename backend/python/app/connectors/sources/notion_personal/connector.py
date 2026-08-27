from logging import Logger

from app.config.configuration_service import ConfigurationService
from app.config.constants.arangodb import Connectors
from app.connectors.core.base.connector.connector_service import BaseConnector
from app.connectors.core.base.data_processor.data_source_entities_processor import (
    DataSourceEntitiesProcessor,
)
from app.connectors.core.base.data_store.data_store import DataStoreProvider
from app.connectors.core.constants import IconPaths
from app.connectors.core.registry.auth_builder import (
    AuthBuilder,
    AuthType,
    OAuthScopeConfig,
)
from app.connectors.core.registry.connector_builder import (
    CommonFields,
    ConnectorBuilder,
    ConnectorScope,
    DocumentationLink,
    SyncStrategy,
)
from app.connectors.core.registry.filters import (
    FilterCategory,
    FilterField,
    FilterType,
    load_connector_filters,
)
from app.connectors.sources.notion.connector import NotionConnector
from app.connectors.sources.notion_personal.common.apps import NotionPersonalApp
from app.models.entities import RecordGroup, RecordGroupType
from app.utils.time_conversion import get_epoch_timestamp_in_ms

AUTHORIZE_URL = "https://api.notion.com/v1/oauth/authorize"
TOKEN_URL = "https://api.notion.com/v1/oauth/token"

CONNECTOR_NOTION_PERSONAL_INFO = (
    "Only you can search the content synced by this connector. It indexes exactly the pages "
    "and databases you share with the integration during the Notion authorization step, so "
    "add pages there to widen it and remove them to narrow it."
)


@ConnectorBuilder("Notion Personal")\
    .in_group("Notion")\
    .with_description("Sync pages and databases from your own Notion account")\
    .with_categories(["Knowledge Management", "Collaboration"])\
    .with_resilience_config(
        rate_limit=3,        # Notion allows ~3 requests/second average per integration
        max_retries=3,       # 4 attempts total
        base_delay=1.0,
        max_delay=60.0,
    )\
    .with_scopes([ConnectorScope.PERSONAL.value])\
    .with_auth([
        AuthBuilder.type(AuthType.OAUTH).oauth(
            connector_name="Notion Personal",
            authorize_url=AUTHORIZE_URL,
            token_url=TOKEN_URL,
            redirect_uri="connectors/oauth/callback/Notion%20Personal",
            scopes=OAuthScopeConfig(
                # Placeholder, as on the team connector: Notion uses capabilities
                # rather than URL scopes, and which pages the token can read is
                # chosen in the authorization dialog. The registry's OAuth
                # validator rejects an empty scope list, so this cannot be [].
                personal_sync=["read_content"],
                team_sync=[],
                agent=[]
            ),
            fields=[
                CommonFields.client_id("Notion OAuth App"),
                CommonFields.client_secret("Notion OAuth App")
            ],
            icon_path=IconPaths.connector_icon(Connectors.NOTION.value),
            app_group="Notion",
            app_description="OAuth application for accessing Notion API",
            app_categories=["Knowledge Management", "Collaboration"],
            additional_params={}
        )
    ])\
    .with_info(CONNECTOR_NOTION_PERSONAL_INFO)\
    .configure(lambda builder: builder
        .with_icon(IconPaths.connector_icon(Connectors.NOTION.value))
        .with_realtime_support(False)
        .add_documentation_link(DocumentationLink(
            "Notion OAuth Setup",
            "https://developers.notion.com/docs/authorization",
            "setup"
        ))
        .add_documentation_link(DocumentationLink(
            'Pipeshub Documentation',
            'https://docs.pipeshub.com/connectors/notion/notion',
            'pipeshub'
        ))
        .with_sync_strategies([SyncStrategy.SCHEDULED, SyncStrategy.MANUAL])
        .with_scheduled_config(True, 60)
        .with_sync_support(True)
        .with_agent_support(True)
        .add_filter_field(FilterField(
            name="pages",
            display_name="Index Pages",
            filter_type=FilterType.BOOLEAN,
            category=FilterCategory.INDEXING,
            description="Enable indexing of Notion pages",
            default_value=True
        ))
        .add_filter_field(FilterField(
            name="databases",
            display_name="Index Databases",
            filter_type=FilterType.BOOLEAN,
            category=FilterCategory.INDEXING,
            description="Enable indexing of Notion databases",
            default_value=True
        ))
        .add_filter_field(FilterField(
            name="files",
            display_name="Index Files",
            filter_type=FilterType.BOOLEAN,
            category=FilterCategory.INDEXING,
            description="Enable indexing of files (attachments and comment attachments)",
            default_value=True
        ))
        .add_filter_field(CommonFields.enable_manual_sync_filter())
    )\
    .build_decorator()
class NotionPersonalConnector(NotionConnector):
    """Personal Notion connector.

    Permission model:
      - No Notion user directory is synced.
      - An internal ``AppUserGroup`` (name: ``ConnectorGroup``, scoped by
        ``connector_id``) is materialized once per sync, with the connector's
        creator as its sole member.
      - The workspace ``RecordGroup`` receives a GROUP permission edge pointing
        at that internal group. Records are created with empty ACLs and
        ``inherit_permissions=True``, so all of them resolve through that one
        edge.

    The team connector instead grants direct READ to every person in the Notion
    workspace, because Notion's API exposes no per-page sharing and a team
    instance has to be readable by the team. Here the token is one user's own
    OAuth grant covering only the pages they picked during authorization, so the
    creator is the correct and complete audience.
    """

    def __init__(
        self,
        logger: Logger,
        data_entities_processor: DataSourceEntitiesProcessor,
        data_store_provider: DataStoreProvider,
        config_service: ConfigurationService,
        connector_id: str,
        scope: str,
        created_by: str,
    ) -> None:
        super().__init__(
            logger,
            data_entities_processor,
            data_store_provider,
            config_service,
            connector_id,
            scope,
            created_by,
        )
        self.app = NotionPersonalApp(connector_id)
        # The enum, not its ``.value``: entity models read ``connector_name.value``
        # when serializing, and the parent stamps this onto every record it builds.
        self.connector_name = Connectors.NOTION_PERSONAL

    async def _sync_users(self) -> None:
        """Resolve the workspace from the bot user; never enumerate its members.

        Overrides the parent, which pages through ``list_users`` to build an app-user
        directory and grant every member READ on the workspace record group. A
        personal instance has exactly one audience, so this only needs the workspace
        identity that the parent happens to extract from the same loop.
        """
        try:
            self.logger.info("🔄 Resolving Notion workspace (personal connector)...")

            datasource = await self._get_fresh_datasource()
            response = await datasource.retrieve_bot_user()

            if not response or not response.success:
                error_msg = response.error if response else "No response"
                self.logger.error(f"❌ Failed to retrieve bot user: {error_msg}")
                raise Exception(f"Notion API error while retrieving bot user: {error_msg}")

            bot_data = (response.data.json() if response.data else {}).get("bot", {})
            workspace_id = bot_data.get("workspace_id")

            if not workspace_id:
                self.logger.error("❌ Bot user has no workspace_id; cannot scope records")
                raise Exception("Notion bot user response missing workspace_id")

            self.workspace_id = workspace_id
            self.workspace_name = bot_data.get("workspace_name")

            self.logger.info(
                f"Resolved workspace - ID: {self.workspace_id}, Name: {self.workspace_name}"
            )

            await self._apply_creator_workspace_permission()

        except Exception as e:
            self.logger.error(f"❌ Workspace resolution failed: {e}", exc_info=True)
            raise

    async def _apply_creator_workspace_permission(self) -> None:
        """Upsert the workspace record group carrying only the ConnectorGroup grant."""
        group_permission = self._connector_group_permission
        if group_permission is None:
            self.logger.warning(
                "Notion Personal connector %s: no ConnectorGroup permission — workspace "
                "records will sync without user permissions",
                self.connector_id,
            )

        async with self.data_store_provider.transaction() as tx_store:
            record_group = await tx_store.get_record_group_by_external_id(
                connector_id=self.connector_id,
                external_id=self.workspace_id,
            )

        if not record_group:
            record_group = RecordGroup(
                org_id=self.data_entities_processor.org_id,
                name=self.workspace_name,
                external_group_id=self.workspace_id,
                connector_name=self.connector_name,
                connector_id=self.connector_id,
                group_type=RecordGroupType.NOTION_WORKSPACE,
                created_at=get_epoch_timestamp_in_ms(),
                updated_at=get_epoch_timestamp_in_ms(),
            )

        await self.data_entities_processor.on_new_record_groups(
            [(record_group, [group_permission] if group_permission else [])]
        )

    async def run_sync(self) -> None:
        """Sync the pages this user shared with the integration; access via ConnectorGroup."""
        try:
            org_id = self.data_entities_processor.org_id
            self.logger.info(f"🚀 Starting Notion Personal sync for org: {org_id}")

            self.sync_filters, self.indexing_filters = await load_connector_filters(
                self.config_service, "notionpersonal", self.connector_id, self.logger
            )

            # Force a fresh ConnectorGroup upsert each run so re-runs after the
            # creator email is rotated pick up the new identity instead of
            # reusing a stale cached permission.
            self._connector_group_permission = None

            if not self.creator_email:
                await self._load_creator_email()

            if not self.creator_email:
                self.logger.warning(
                    "Notion Personal connector %s: no creator email — "
                    "records will sync without user permissions",
                    self.connector_id,
                )
            else:
                # Materialize the group + creator membership edge before any
                # record-group write, so the GROUP-permission lookup in
                # on_new_record_groups resolves on the first write instead of
                # silently dropping the permission.
                await self.ensure_connector_group_permission()

            await self._sync_users()
            await self._sync_objects_by_type("data_source")
            await self._sync_objects_by_type("page")

            # Inherited from NotionConnector, but this run_sync does not call super(),
            # so the sweep has to be invoked here too or personal connectors keep
            # accumulating UUID-named parent stubs.
            try:
                await self._sweep_placeholder_records()
            except Exception as e:
                self.logger.error(f"Placeholder sweep failed: {e}", exc_info=True)

            self.logger.info("✅ Notion Personal sync completed successfully")

        except Exception as e:
            self.logger.error(f"❌ Notion Personal sync failed: {e}", exc_info=True)
            raise

    @classmethod
    async def create_connector(
        cls,
        logger: Logger,
        data_store_provider: DataStoreProvider,
        config_service: ConfigurationService,
        connector_id: str,
        scope: str,
        created_by: str,
        data_entities_processor,
        **kwargs,
    ) -> BaseConnector:
        """Factory method to create a Notion Personal connector instance."""
        return cls(
            logger,
            data_entities_processor,
            data_store_provider,
            config_service,
            connector_id,
            scope,
            created_by,
        )

    async def init(self) -> bool:
        """Initialize the client, then cache the creator identity for permissions."""
        initialized = await super().init()
        if initialized:
            await self._load_creator_email()
        return initialized
