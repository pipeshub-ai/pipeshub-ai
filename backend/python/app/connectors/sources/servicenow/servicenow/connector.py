"""
ServiceNow Knowledge Base Connector

This connector syncs knowledge base articles, categories, attachments, and permissions
from ServiceNow into the PipesHub AI platform.

Synced Entities:
- Users and Groups (for permissions)
- Roles (for role-based permissions)
- Organizational Entities (companies, departments, locations, cost centers)
- Knowledge Bases (containers)
- Categories (hierarchy)
- KB Articles (content)
- Attachments (files)
"""

import uuid
from collections import defaultdict
from logging import Logger
from typing import Any, AsyncGenerator, Dict, List, NoReturn, Optional, Tuple

from fastapi import HTTPException
from fastapi.responses import StreamingResponse

from app.config.configuration_service import ConfigurationService
from app.config.constants.arangodb import Connectors, MimeTypes, OriginTypes
from app.config.constants.http_status_code import HttpStatusCode
from app.connectors.core.constants import (
    AuthFieldKeys,
    CommonStrings,
    ConnectorRegistryCategories,
    IconPaths,
    OAuthConfigKeys,
    OAuthDefaults,
    OAuthRedirectPaths,
)
from app.connectors.core.base.connector.connector_service import BaseConnector
from app.connectors.core.base.data_processor.data_source_entities_processor import (
    DataSourceEntitiesProcessor,
)
from app.connectors.core.base.data_store.data_store import (
    DataStoreProvider,
    TransactionStore,
)
from app.connectors.core.base.sync_point.sync_point import SyncDataPointType, SyncPoint
from app.connectors.core.registry.auth_builder import (
    AuthBuilder,
    AuthType,
    OAuthScopeConfig,
)
from app.connectors.core.registry.connector_builder import (
    AuthField,
    CommonFields,
    ConnectorBuilder,
    ConnectorScope,
    DocumentationLink,
    SyncStrategy,
)
from app.connectors.core.registry.types import FieldType
from app.connectors.sources.microsoft.common.msgraph_client import RecordUpdate
from app.connectors.sources.servicenow.common.apps import ServicenowApp
from app.models.entities import (
    AppUser,
    AppUserGroup,
    FileRecord,
    Record,
    RecordGroup,
    RecordGroupType,
    RecordType,
    WebpageRecord,
)
from app.models.permission import EntityType, Permission, PermissionType
from app.sources.client.servicenow.servicenow import (
    ServiceNowRESTClientViaOAuthAuthorizationCode,
)
from app.sources.external.servicenow.servicenow import ServiceNowDataSource
from app.sources.external.servicenow.models import (
    AttachmentMetadata,
    KBCategory,
    KBKnowledge,
    KBKnowledgeBase,
    KBPermissionMapping,
    OrganizationalEntity,
    RawPermission,
    ServiceNowAPIError,
    SysUser,
    SysUserGroup,
    SysUserGroupMembership,
    SysUserRole,
    SysUserRoleAssignment,
    SysUserRoleContains,
    TableAPIRecord,
    TableAPIResponse,
    UserCriteria,
)
from app.utils.oauth_config import fetch_oauth_config_by_id
from app.utils.streaming import create_stream_record_response
from app.utils.time_conversion import datetime_to_epoch_ms
from app.connectors.sources.servicenow.servicenow.constants import (
    ORGANIZATIONAL_ENTITIES,
    OrganizationalEntityConfig,
    ServiceNowConfigPaths,
    ServiceNowConnectorMetadata,
    ServiceNowDefaults,
    ServiceNowDictKeys,
    ServiceNowFields,
    ServiceNowPrefixes,
    ServiceNowQueryParams,
    ServiceNowQueryValues,
    ServiceNowRoles,
    ServiceNowSyncPointKeys,
    ServiceNowTables,
    ServiceNowURLPatterns,
)



@ConnectorBuilder(ServiceNowConnectorMetadata.NAME)\
    .in_group(ServiceNowConnectorMetadata.NAME)\
    .with_description("Sync knowledge base articles, categories, and permissions from ServiceNow")\
    .with_categories([ConnectorRegistryCategories.KNOWLEDGE_MANAGEMENT])\
    .with_scopes([ConnectorScope.TEAM.value])\
    .with_auth([
        AuthBuilder.type(AuthType.OAUTH).oauth(
            connector_name=ServiceNowConnectorMetadata.NAME,
            authorize_url="https://example.service-now.com/oauth_auth.do",
            token_url="https://example.service-now.com/oauth_token.do",
            redirect_uri=OAuthRedirectPaths.CONNECTOR_CALLBACK.format(
                connector_name=ServiceNowConnectorMetadata.NAME
            ),
            scopes=OAuthScopeConfig(
                personal_sync=[],
                team_sync=["useraccount"],
                agent=[]
            ),
            fields=[
                AuthField(
                    name=AuthFieldKeys.INSTANCE_URL,
                    display_name="ServiceNow Instance URL",
                    placeholder="https://your-instance.service-now.com",
                    description="Your ServiceNow instance URL (e.g., https://dev12345.service-now.com)",
                    field_type=FieldType.URL.value,
                    required=True,
                    max_length=OAuthDefaults.MAX_URL_LENGTH,
                ),
                AuthField(
                    name=AuthFieldKeys.AUTHORIZE_URL,
                    display_name="ServiceNow Authorize URL",
                    placeholder="https://your-instance.service-now.com/oauth_auth.do",
                    description="Your ServiceNow authorize URL (e.g., https://dev12345.service-now.com/oauth_auth.do)",
                    field_type=FieldType.URL.value,
                    required=True,
                    max_length=OAuthDefaults.MAX_URL_LENGTH,
                ),
                AuthField(
                    name=AuthFieldKeys.TOKEN_URL,
                    display_name="ServiceNow Token URL",
                    placeholder="https://your-instance.service-now.com/oauth_token.do",
                    description="Your ServiceNow token URL (e.g., https://dev12345.service-now.com/oauth_token.do)",
                    field_type=FieldType.URL.value,
                    required=True,
                    max_length=OAuthDefaults.MAX_URL_LENGTH,
                ),
                CommonFields.client_id(f"{ServiceNowConnectorMetadata.NAME} OAuth Application Registry"),
                CommonFields.client_secret(f"{ServiceNowConnectorMetadata.NAME} OAuth Application Registry")
            ],
            icon_path=IconPaths.connector_icon(Connectors.SERVICENOW.value),
            app_group=ServiceNowConnectorMetadata.NAME,
            app_description=f"OAuth application for accessing {ServiceNowConnectorMetadata.NAME} API and knowledge base services",
            app_categories=[ConnectorRegistryCategories.KNOWLEDGE_MANAGEMENT]
        )
    ])\
    .configure(lambda builder: builder
        .with_icon(IconPaths.connector_icon(Connectors.SERVICENOW.value))
        .with_realtime_support(False)
        .add_documentation_link(
            DocumentationLink(
                "ServiceNow OAuth Setup",
                "https://docs.servicenow.com/bundle/latest/page/administer/security/concept/c_OAuthApplications.html",
                "Setup"
            )
        )
        .add_documentation_link(
            DocumentationLink(
                "Pipeshub Documentation",
                "https://docs.pipeshub.com/connectors/servicenow/servicenow",
                "Pipeshub"
            )
        )
        .with_sync_strategies([SyncStrategy.SCHEDULED, SyncStrategy.MANUAL])
        .with_scheduled_config(True, 60)
        .with_sync_support(True)
        .with_agent_support(False)
    )\
    .build_decorator()
class ServiceNowConnector(BaseConnector):
    """
    ServiceNow Knowledge Base Connector

    This connector syncs ServiceNow Knowledge Base data including:
    - Knowledge bases and categories
    - KB articles with metadata
    - Article attachments
    - User and group permissions
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
        """
        Initialize the ServiceNow KB Connector.

        Args:
            logger: Logger instance
            data_entities_processor: Processor for handling entities
            data_store_provider: Data store provider
            config_service: Configuration service
        """
        super().__init__(
            ServicenowApp(connector_id),
            logger,
            data_entities_processor,
            data_store_provider,
            config_service,
            connector_id,
            scope,
            created_by,
        )

        # ServiceNow API client instances
        self.servicenow_client: Optional[ServiceNowRESTClientViaOAuthAuthorizationCode] = None
        self.servicenow_datasource: Optional[ServiceNowDataSource] = None
        self.connector_id = connector_id

        # Configuration
        self.instance_url: Optional[str] = None
        self.client_id: Optional[str] = None
        self.client_secret: Optional[str] = None
        self.redirect_uri: Optional[str] = None

        # OAuth tokens (managed by framework/client)
        self.access_token: Optional[str] = None
        self.refresh_token: Optional[str] = None

        # Initialize sync points for incremental sync
        def _create_sync_point(sync_data_point_type: SyncDataPointType) -> SyncPoint:
            return SyncPoint(
                connector_id=self.connector_id,
                org_id=self.data_entities_processor.org_id,
                sync_data_point_type=sync_data_point_type,
                data_store_provider=self.data_store_provider,
            )

        # Sync points for different entity types
        self.user_sync_point = _create_sync_point(SyncDataPointType.USERS)
        self.group_sync_point = _create_sync_point(SyncDataPointType.GROUPS)
        self.kb_sync_point = _create_sync_point(SyncDataPointType.RECORD_GROUPS)
        self.category_sync_point = _create_sync_point(SyncDataPointType.RECORD_GROUPS)
        self.article_sync_point = _create_sync_point(SyncDataPointType.RECORDS)

        # Role sync points (roles are represented as special user groups)
        self.role_sync_point = _create_sync_point(SyncDataPointType.GROUPS)
        self.role_assignment_sync_point = _create_sync_point(SyncDataPointType.GROUPS)

        # Organizational entity sync points
        self.company_sync_point = _create_sync_point(SyncDataPointType.GROUPS)
        self.department_sync_point = _create_sync_point(SyncDataPointType.GROUPS)
        self.location_sync_point = _create_sync_point(SyncDataPointType.GROUPS)
        self.cost_center_sync_point = _create_sync_point(SyncDataPointType.GROUPS)

        # Map entity types to their sync points for easy lookup
        self.org_entity_sync_points = {
            "company": self.company_sync_point,
            "department": self.department_sync_point,
            "location": self.location_sync_point,
            "cost_center": self.cost_center_sync_point,
        }

    async def init(self) -> bool:
        """
        Initialize the connector with OAuth credentials and API client.

        Returns:
            bool: True if initialization successful, False otherwise
        """
        try:
            self.logger.info("🔧 Initializing ServiceNow KB Connector (OAuth)...")
            connector_id = self.connector_id
            # Load configuration
            config = await self.config_service.get_config(
                ServiceNowConfigPaths.CONNECTOR_CONFIG.format(connector_id=connector_id)
            )

            if not config:
                self.logger.error("❌ ServiceNow configuration not found")
                return False

            # Extract OAuth configuration
            auth_config = config.get(OAuthConfigKeys.AUTH, {})
            oauth_config_id = auth_config.get(OAuthConfigKeys.OAUTH_CONFIG_ID)

            if not oauth_config_id:
                self.logger.error("ServiceNow oauthConfigId not found in auth configuration.")
                return False

            # Fetch OAuth config
            oauth_config = await fetch_oauth_config_by_id(
                oauth_config_id=oauth_config_id,
                connector_type=ServiceNowDefaults.CONNECTOR_TYPE,
                config_service=self.config_service,
                logger=self.logger
            )

            if not oauth_config:
                self.logger.error("OAuth config not found for ServiceNow connector.")
                return False

            oauth_config_data = oauth_config.get(OAuthConfigKeys.CONFIG, {})

            self.instance_url = oauth_config_data.get(AuthFieldKeys.INSTANCE_URL)
            self.client_id = oauth_config_data.get(AuthFieldKeys.CLIENT_ID)
            self.client_secret = oauth_config_data.get(AuthFieldKeys.CLIENT_SECRET)
            self.redirect_uri = oauth_config.get(AuthFieldKeys.REDIRECT_URI)
            self.logger.info("Using shared OAuth config for ServiceNow connector")

            # OAuth tokens (stored after authorization flow completes)
            credentials = config.get(OAuthConfigKeys.CREDENTIALS, {})
            self.access_token = credentials.get(OAuthConfigKeys.ACCESS_TOKEN)
            self.refresh_token = credentials.get(OAuthConfigKeys.REFRESH_TOKEN)

            if not all(
                [
                    self.instance_url,
                    self.client_id,
                    self.client_secret,
                    self.redirect_uri,
                ]
            ):
                self.logger.error(
                    "❌ Incomplete ServiceNow OAuth configuration. "
                    "Ensure instanceUrl, clientId, clientSecret, and redirectUri are configured."
                )
                return False

            # Check if OAuth flow is complete
            if not self.access_token:
                self.logger.warning("⚠️ OAuth authorization not complete. User needs to authorize.")
                return False

            # Initialize ServiceNow OAuth client
            self.logger.info(
                f"🔗 Connecting to ServiceNow instance: {self.instance_url}"
            )
            self.servicenow_client = ServiceNowRESTClientViaOAuthAuthorizationCode(
                instance_url=self.instance_url,
                client_id=self.client_id,
                client_secret=self.client_secret,
                redirect_uri=self.redirect_uri,
                access_token=self.access_token,
            )

            # Store refresh token if available
            if self.refresh_token:
                self.servicenow_client.refresh_token = self.refresh_token

            # Initialize data source wrapper
            self.servicenow_datasource = ServiceNowDataSource(self.servicenow_client)

            # Test connection
            if not await self.test_connection_and_access():
                self.logger.error("❌ Connection test failed")
                return False

            self.logger.info("✅ ServiceNow KB Connector initialized successfully")
            return True

        except Exception as e:
            self.logger.error(f"❌ Failed to initialize connector: {e}", exc_info=True)
            return False

    async def _get_fresh_datasource(self) -> ServiceNowDataSource:
        """
        Get ServiceNowDataSource with ALWAYS-FRESH access token.

        This method:
        1. Fetches current token from config (async I/O)
        2. Updates client if token changed
        3. Returns ready-to-use datasource

        Returns:
            ServiceNowDataSource with current valid token
        """
        if not self.servicenow_client:
            raise Exception("ServiceNow client not initialized. Call init() first.")

        connector_id = self.connector_id

        # Fetch current token from config (async I/O)
        config = await self.config_service.get_config(ServiceNowConfigPaths.CONNECTOR_CONFIG.format(connector_id=connector_id))

        if not config:
            raise Exception("ServiceNow configuration not found")

        credentials = config.get(OAuthConfigKeys.CREDENTIALS) or {}
        fresh_token = credentials.get(OAuthConfigKeys.ACCESS_TOKEN)

        if not fresh_token:
            raise Exception("No access token available")

        # Update client's token if it changed (mutation)
        if self.servicenow_client.access_token != fresh_token:
            self.servicenow_client.access_token = fresh_token

        return ServiceNowDataSource(self.servicenow_client)

    async def test_connection_and_access(self) -> bool:
        """
        Test OAuth connection and access to ServiceNow API.

        Returns:
            bool: True if connection successful, False otherwise
        """
        try:
            self.logger.info("🔍 Testing ServiceNow OAuth connection...")

            # Make a simple API call to verify OAuth token works
            datasource = await self._get_fresh_datasource()
            try:
                response = await datasource.get_now_table_tableName(
                    tableName=ServiceNowTables.KB_KNOWLEDGE_BASE,
                    sysparm_limit=ServiceNowDefaults.DEFAULT_LIMIT,
                    sysparm_fields=CommonStrings.COMMA.join(
                        [
                            ServiceNowFields.SYS_ID,
                            ServiceNowFields.TITLE,
                        ]
                    )
                )
                
                self.logger.info("✅ OAuth connection test successful")
                return True
                
            except ServiceNowAPIError as e:
                self.logger.error(f"❌ Connection test failed: {e.message} (status: {e.status_code})")
                return False

        except Exception as e:
            self.logger.error(f"❌ Connection test failed: {e}", exc_info=True)
            return False

    async def run_sync(self) -> None:
        """
        Run full synchronization of ServiceNow Knowledge Base data.

        Sync order:
        1. Users and Groups (global)
        2. Get admin users from ServiceNow
        3. Knowledge Bases (with admin permissions)
        4. Categories (with admin permissions)
        5. KB Articles (with admin permissions)
        """
        try:
            org_id = self.data_entities_processor.org_id
            self.logger.info(f"🚀 Starting ServiceNow KB sync for org: {org_id}")

            # Ensure client is initialized
            if not self.servicenow_client:
                raise Exception("ServiceNow client not initialized. Call init() first.")

            # Step 1: Sync users and groups globally
            self.logger.info("Step 1/5: Syncing users and groups...")
            await self._sync_users_and_groups()

            # Step 2: Get admin users from ServiceNow
            self.logger.info("Step 2/5: Fetching admin users from ServiceNow...")
            admin_users = await self._get_admin_users()

            if not admin_users:
                self.logger.warning("No admin users found, proceeding without explicit admin permissions")
                admin_users = []

            self.logger.info(f"✅ Found {len(admin_users)} admin users")

            # Step 3: Knowledge Bases
            self.logger.info("Step 3/5: Syncing Knowledge Bases...")
            await self._sync_knowledge_bases(admin_users)

            # Step 4: Categories
            self.logger.info("Step 4/5: Syncing Categories...")
            await self._sync_categories()

            # Step 5: Articles & Attachments
            self.logger.info("Step 5/5: Syncing Articles & Attachments...")
            await self._sync_articles()

            self.logger.info("🎉 ServiceNow KB sync completed successfully")

        except Exception as e:
            self.logger.error(f"❌ Error during sync: {e}", exc_info=True)
            raise

    async def run_incremental_sync(self) -> None:
        """
        Run incremental synchronization using delta links or timestamps.

        For ServiceNow, this uses the sys_updated_on field to fetch only
        records updated since the last sync.
        """
        # delegate to full sync
        await self.run_sync()

    async def stream_record(self, record: Record) -> StreamingResponse:
        """
        Stream record content (article HTML or attachment file) from ServiceNow.

        For articles (WebpageRecord): Fetches HTML content from kb_knowledge table
        For attachments (FileRecord): Downloads file from attachment API

        Args:
            record: The record to stream (article or attachment)

        Returns:
            StreamingResponse: Streaming response with article HTML or file content
        """
        try:
            self.logger.info(f"📥 Streaming record: {record.record_name} ({record.external_record_id})")

            if record.record_type == RecordType.WEBPAGE:
                # Article - fetch HTML content from kb_knowledge table
                html_content = await self._fetch_article_content(record.external_record_id)

                async def generate_article() -> AsyncGenerator[bytes, None]:
                    yield html_content.encode('utf-8')

                return StreamingResponse(
                    generate_article(),
                    media_type='text/html',
                    headers={"Content-Disposition": f'inline; filename="{record.external_record_id}.html"'}
                )

            elif record.record_type == RecordType.FILE:
                # Attachment - download file from ServiceNow
                file_content = await self._fetch_attachment_content(record.external_record_id)

                async def generate_attachment() -> AsyncGenerator[bytes, None]:
                    yield file_content

                filename = record.record_name or f"{record.external_record_id}"
                return create_stream_record_response(
                    generate_attachment(),
                    filename=filename,
                    mime_type=record.mime_type,
                    fallback_filename=f"record_{record.id}"
                )

            else:
                raise HTTPException(
                    status_code=HttpStatusCode.BAD_REQUEST.value,
                    detail=f"Unsupported record type for streaming: {record.record_type}"
                )

        except HTTPException:
            raise  # Re-raise HTTP exceptions as-is
        except Exception as e:
            self.logger.error(f"❌ Failed to stream record: {e}", exc_info=True)
            raise HTTPException(
                status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value, detail=f"Failed to stream record: {str(e)}"
            )

    async def _fetch_article_content(self, article_sys_id: str) -> str:
        """
        Fetch article HTML content from ServiceNow kb_knowledge table.

        Args:
            article_sys_id: The sys_id of the article

        Returns:
            str: HTML content of the article

        Raises:
            HTTPException: If article not found or fetch fails
        """
        try:
            # Fetch article using ServiceNow Table API
            datasource = await self._get_fresh_datasource()
            try:
                response = await datasource.get_now_table_tableName(
                    tableName=ServiceNowTables.KB_KNOWLEDGE,
                    sysparm_query=f"{ServiceNowFields.SYS_ID}={article_sys_id}",
                    sysparm_fields=CommonStrings.COMMA.join(
                        [
                            ServiceNowFields.SYS_ID,
                            ServiceNowFields.SHORT_DESCRIPTION,
                            ServiceNowFields.TEXT,
                            ServiceNowFields.NUMBER,
                        ]
                    ),
                    sysparm_limit=ServiceNowDefaults.DEFAULT_LIMIT,
                    sysparm_display_value=ServiceNowQueryValues.DISPLAY_VALUE_FALSE,
                    sysparm_no_count=ServiceNowQueryValues.NO_COUNT_TRUE,
                    sysparm_exclude_reference_link=ServiceNowQueryValues.EXCLUDE_REFERENCE_LINK_TRUE
                )
            except ServiceNowAPIError as e:
                raise HTTPException(
                    status_code=e.status_code,
                    detail=f"Failed to fetch article: {e.message}"
                )

            # Extract article from result array
            articles = response.result
            if not articles or len(articles) == 0:
                raise HTTPException(
                    status_code=HttpStatusCode.NOT_FOUND.value,
                    detail=f"Article not found: {article_sys_id}"
                )

            article = articles[0]

            # Get raw HTML content from text field
            html_content = article.text or ""

            if not html_content:
                # If no content, return empty HTML
                self.logger.warning(f"Article {article_sys_id} has no content")
                html_content = "<p>No content available</p>"

            return html_content

        except HTTPException:
            raise  # Re-raise HTTP exceptions
        except Exception as e:
            self.logger.error(f"Failed to fetch article content: {e}", exc_info=True)
            raise HTTPException(
                status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value,
                detail=f"Failed to fetch article content: {str(e)}"
            )

    async def _fetch_attachment_content(self, attachment_sys_id: str) -> bytes:
        """
        Fetch attachment file content from ServiceNow.

        Uses the attachment download API: GET /api/now/attachment/{sys_id}/file

        Args:
            attachment_sys_id: The sys_id of the attachment

        Returns:
            bytes: Binary file content

        Raises:
            HTTPException: If attachment not found or download fails
        """
        try:
            # Download using REST client (returns bytes directly)
            datasource = await self._get_fresh_datasource()
            file_content = await datasource.download_attachment(attachment_sys_id)

            if not file_content:
                raise HTTPException(
                    status_code=HttpStatusCode.NOT_FOUND.value,
                    detail=f"Attachment not found or empty: {attachment_sys_id}"
                )

            return file_content

        except HTTPException:
            raise  # Re-raise HTTP exceptions
        except ServiceNowAPIError as e:
            self.logger.error(f"ServiceNow API error downloading attachment: {e.message}")
            raise HTTPException(
                status_code=e.status_code,
                detail=f"Failed to download attachment: {e.message}"
            )
        except Exception as e:
            self.logger.error(f"Failed to download attachment: {e}", exc_info=True)
            raise HTTPException(
                status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value,
                detail=f"Failed to download attachment: {str(e)}"
            )

    def get_signed_url(self, record: Record) -> Optional[str]:
        """
        Get signed URL for record access.

        ServiceNow doesn't support pre-signed URLs in the traditional sense,
        so this returns None. Access is controlled through the stream_record method.

        Args:
            record: The record to get URL for

        Returns:
            Optional[str]: None for ServiceNow
        """
        return None

    async def handle_webhook_notification(
        self, org_id: str, notification: Dict
    ) -> bool:
        """
        Handle webhook notifications from ServiceNow.

        This can be used for real-time sync when ServiceNow sends notifications
        about changes to KB articles.

        Args:
            org_id: Organization ID
            notification: Webhook notification payload

        Returns:
            bool: True if handled successfully
        """
        try:
            # TODO: Implement webhook handling
            # ServiceNow can send notifications via Business Rules or Flow Designer
            self.logger.info(f"📬 Received webhook notification: {notification}")
            return True

        except Exception as e:
            self.logger.error(f"❌ Error handling webhook: {e}", exc_info=True)
            return False

    async def cleanup(self) -> None:
        """
        Clean up resources used by the connector.

        This is called when the connector is being shut down.
        """
        try:
            self.logger.info("🧹 Cleaning up ServiceNow KB Connector...")

            # Clean up clients
            self.servicenow_client = None
            self.servicenow_datasource = None

            self.logger.info("✅ Cleanup completed")

        except Exception as e:
            self.logger.error(f"❌ Error during cleanup: {e}", exc_info=True)

    async def reindex_records(self, record_results: List[Record]) -> None:
        """Reindex records - not implemented for ServiceNow yet."""
        self.logger.warning("Reindex not implemented for ServiceNow connector")
        pass

    async def get_filter_options(
        self,
        filter_key: str,
        page: int = 1,
        limit: int = 20,
        search: Optional[str] = None,
        cursor: Optional[str] = None
    ) -> NoReturn:
        """ServiceNow connector does not support dynamic filter options."""
        raise NotImplementedError("ServiceNow connector does not support dynamic filter options")

    async def _sync_users_and_groups(self) -> None:
        """
        Sync users, groups, roles, and organizational entities from ServiceNow.

        This is the foundation for permission management.

        API Endpoints:
        - /api/now/table/sys_user - Users
        - /api/now/table/sys_user_group - Groups
        - /api/now/table/sys_user_grmember - Group memberships
        - /api/now/table/sys_user_role - Roles
        - /api/now/table/sys_user_role_contains - Role hierarchy
        - /api/now/table/sys_user_has_role - User-role assignments
        """
        try:
            # Step 1: Sync organizational entities
            self.logger.info("Step 1/4: Syncing organizational entities...")
            await self._sync_organizational_entities()

            # Step 4: Sync users
            self.logger.info("Step 2/4: Syncing users...")
            await self._sync_users()

            # Step 2: Sync user groups
            self.logger.info("Step 3/4: Syncing user groups...")
            await self._sync_user_groups()

            # Step 3: Sync roles
            self.logger.info("Step 4/4: Syncing roles...")
            await self._sync_roles()


            self.logger.info("✅ Users, groups, roles, and organizational entities synced successfully")

        except Exception as e:
            self.logger.error(f"❌ Error syncing users/groups: {e}", exc_info=True)
            raise

    async def _get_admin_users(self) -> List[AppUser]:
        """
        Get users with admin role from ServiceNow and match with platform users.

        Fetches users with admin role from sys_user_has_role table and matches them
        with existing platform users in the database.

        Returns:
            List[AppUser]: List of admin users from platform
        """
        try:
            admin_users = []

            # Query sys_user_has_role for admin role assignments
            datasource = await self._get_fresh_datasource()
            try:
                response = await datasource.get_now_table_tableName(
                    tableName=ServiceNowTables.SYS_USER_HAS_ROLE,
                    sysparm_query=f"{ServiceNowFields.ROLE}.{ServiceNowFields.NAME}={ServiceNowRoles.ADMIN}^{ServiceNowFields.USER}.{ServiceNowFields.ACTIVE}=true",
                    sysparm_fields=CommonStrings.COMMA.join(
                        [
                            ServiceNowFields.USER,
                            f"{ServiceNowFields.USER}.{ServiceNowFields.NAME}",
                            f"{ServiceNowFields.USER}.{ServiceNowFields.SYS_ID}",
                            f"{ServiceNowFields.USER}.{ServiceNowFields.EMAIL}",
                        ]
                    ),
                    sysparm_display_value=ServiceNowQueryValues.DISPLAY_VALUE_FALSE,
                    sysparm_exclude_reference_link=ServiceNowQueryValues.EXCLUDE_REFERENCE_LINK_TRUE,
                    sysparm_no_count=ServiceNowQueryValues.NO_COUNT_TRUE,
                )
                
                role_assignments = response.result
            except ServiceNowAPIError as e:
                self.logger.warning(f"Failed to fetch admin users from ServiceNow: {e.message}")
                return []
            self.logger.info(f"Found {len(role_assignments)} admin role assignments")

            # Extract unique user sys_ids
            admin_sys_ids = set()
            for assignment in role_assignments:
                    # user is now a sys_id string (not a reference object)
                    user_sys_id = assignment.user if hasattr(assignment, 'user') else None

                    if user_sys_id:
                        admin_sys_ids.add(user_sys_id)

            self.logger.info(f"Found {len(admin_sys_ids)} unique admin users")

            # Match with platform users using source_user_id
            async with self.data_store_provider.transaction() as tx_store:
                for sys_id in admin_sys_ids:
                    try:
                        # Get AppUser by source_user_id (ServiceNow sys_id)
                        app_user = await tx_store.get_user_by_source_id(
                            source_user_id=sys_id,
                            connector_id=self.connector_id
                        )

                        if app_user:
                            admin_users.append(app_user)

                    except Exception as e:
                        self.logger.warning(f"Error matching admin user {sys_id}: {e}")
                        continue

            self.logger.info(f"✅ Matched {len(admin_users)} admin users with platform accounts")
            return admin_users

        except Exception as e:
            self.logger.error(f"❌ Error fetching admin users: {e}", exc_info=True)
            return []

    async def _sync_users(self) -> None:
        """
        Sync users from ServiceNow using offset-based pagination.

        First sync: Fetches all users
        Subsequent syncs: Only fetches users modified since last sync
        """
        try:
            # Get last sync checkpoint
            last_sync_data = await self.user_sync_point.read_sync_point(ServiceNowSyncPointKeys.USERS)
            last_sync_time = (last_sync_data.get(ServiceNowSyncPointKeys.LAST_SYNC_TIME) if last_sync_data else None)

            if last_sync_time:
                self.logger.info(f"🔄 Delta sync: fetching users updated after {last_sync_time}")
                query = f"{ServiceNowFields.SYS_UPDATED_ON}>{last_sync_time}^{ServiceNowQueryValues.ORDER_BY_UPDATED}"
            else:
                self.logger.info("🆕 Full sync: fetching all users")
                query = ServiceNowQueryValues.ORDER_BY_UPDATED

            # Pagination variables
            batch_size = ServiceNowDefaults.BATCH_SIZE
            offset = ServiceNowDefaults.PAGINATION_OFFSET
            total_synced = 0
            latest_update_time = None

            # Paginate through all users
            while True:
                datasource = await self._get_fresh_datasource()
                try:
                    response = await datasource.get_now_table_tableName(
                        tableName=ServiceNowTables.SYS_USER,
                        sysparm_query=query,
                        sysparm_fields=CommonStrings.COMMA.join(
                            [
                                ServiceNowFields.SYS_ID,
                                ServiceNowFields.USER_NAME,
                                ServiceNowFields.EMAIL,
                                ServiceNowFields.FIRST_NAME,
                                ServiceNowFields.LAST_NAME,
                                ServiceNowFields.TITLE,
                                ServiceNowFields.DEPARTMENT,
                                ServiceNowFields.COMPANY,
                                ServiceNowFields.LOCATION,
                                ServiceNowFields.COST_CENTER,
                                ServiceNowFields.ACTIVE,
                                ServiceNowFields.SYS_CREATED_ON,
                                ServiceNowFields.SYS_UPDATED_ON,
                            ]
                        ),
                        sysparm_limit=str(batch_size),
                        sysparm_offset=str(offset),
                        sysparm_display_value=ServiceNowQueryValues.DISPLAY_VALUE_FALSE,
                        sysparm_no_count=ServiceNowQueryValues.NO_COUNT_TRUE,
                        sysparm_exclude_reference_link=ServiceNowQueryValues.EXCLUDE_REFERENCE_LINK_TRUE,
                    )
                    
                except ServiceNowAPIError as e:
                    self.logger.error(f"❌ API error: {e.message} (status: {e.status_code})")
                    break

                # Extract users from response
                users_data = response.result

                if not users_data:
                    break

                # Track the latest update timestamp for checkpoint
                if users_data:
                    latest_update_time = users_data[-1].sys_updated_on

                # Transform users (skip users without email)
                app_users = []
                user_org_links = []  # Collect organizational links

                for user_data in users_data:
                    email = (user_data.email or "").strip()
                    if not email:
                        continue

                    app_user = await self._transform_to_app_user(user_data)
                    if app_user:
                        app_users.append(app_user)

                        # Collect organizational links for this user
                        user_sys_id = user_data.sys_id
                        if user_sys_id:
                            org_fields = {
                                "company": user_data.company,
                                "department": user_data.department,
                                "location": user_data.location,
                                "cost_center": user_data.cost_center,
                            }

                            for org_type, org_ref in org_fields.items():
                                if not org_ref:
                                    continue

                                # org_ref is now just a sys_id string (not a reference object)
                                org_sys_id = org_ref if isinstance(org_ref, str) else None

                                if org_sys_id:
                                    user_org_links.append({
                                        ServiceNowDictKeys.USER_SYS_ID: user_sys_id,
                                        ServiceNowDictKeys.ORG_SYS_ID: org_sys_id,
                                        ServiceNowDictKeys.ORG_TYPE: org_type,
                                    })

                # Save batch to database
                if app_users:
                    await self.data_entities_processor.on_new_app_users(app_users)
                    total_synced += len(app_users)

                # Create user-to-organizational-entity edges
                if user_org_links:
                    self.logger.info(f"Creating {len(user_org_links)} user-to-organizational-entity link")
                    async with self.data_store_provider.transaction() as tx_store:
                        for link in user_org_links:
                            await tx_store.create_user_group_membership(
                                link[ServiceNowDictKeys.USER_SYS_ID],
                                link[ServiceNowDictKeys.ORG_SYS_ID],
                                self.connector_id
                            )

                # Move to next page
                offset += batch_size

                # If this page has fewer records than batch_size, we're done
                if len(users_data) < batch_size:
                    break

            # Save checkpoint for next sync
            if latest_update_time:
                await self.user_sync_point.update_sync_point(ServiceNowSyncPointKeys.USERS, {ServiceNowSyncPointKeys.LAST_SYNC_TIME: latest_update_time})

            self.logger.info(f"User sync complete, Total synced: {total_synced}")

        except Exception as e:
            self.logger.error(f"❌ User sync failed: {e}", exc_info=True)
            raise

    async def _sync_user_groups(self) -> None:
        """
        Sync user groups and flatten memberships.
        Simple 3-step process: fetch groups → fetch memberships → flatten & upsert
        """
        try:
            self.logger.info("Starting user group synchronization")

            # STEP 1: Fetch all memberships
            memberships_data = await self._fetch_all_memberships()

            if not memberships_data:
                self.logger.info("No memberships found, skipping group sync")
                return

            # STEP 2: Fetch all groups
            groups_data = await self._fetch_all_groups()

            # STEP 3: Flatten and create AppUserGroup objects
            group_with_permissions = await self._flatten_and_create_user_groups(
                groups_data,
                memberships_data
            )

            # STEP 4: Upsert to database
            if group_with_permissions:
                await self.data_entities_processor.on_new_user_groups(group_with_permissions)

            self.logger.info(f"✅ Processed {len(group_with_permissions)} user groups")

        except Exception as e:
            self.logger.error(f"❌ Error syncing user groups: {e}", exc_info=True)
            raise


    async def _fetch_all_groups(self) -> List[SysUserGroup]:
        """Fetch all groups from ServiceNow (no delta sync)
        
        Returns:
            List of SysUserGroup Pydantic models
        """
        try:
            all_groups: List[SysUserGroup] = []
            batch_size = ServiceNowDefaults.BATCH_SIZE
            offset = ServiceNowDefaults.PAGINATION_OFFSET

            while True:
                datasource = await self._get_fresh_datasource()
                try:
                    response = await datasource.get_now_table_tableName(
                        tableName=ServiceNowTables.SYS_USER_GROUP,
                        sysparm_query=ServiceNowQueryValues.ORDER_BY_UPDATED,
                        sysparm_fields=CommonStrings.COMMA.join(
                            [
                                ServiceNowFields.SYS_ID,
                                ServiceNowFields.NAME,
                                ServiceNowFields.DESCRIPTION,
                                ServiceNowFields.PARENT,
                                ServiceNowFields.MANAGER,
                                ServiceNowFields.SYS_CREATED_ON,
                                ServiceNowFields.SYS_UPDATED_ON,
                            ]
                        ),
                        sysparm_limit=str(batch_size),
                        sysparm_offset=str(offset),
                        sysparm_display_value=ServiceNowQueryValues.DISPLAY_VALUE_FALSE,
                        sysparm_no_count=ServiceNowQueryValues.NO_COUNT_TRUE,
                        sysparm_exclude_reference_link=ServiceNowQueryValues.EXCLUDE_REFERENCE_LINK_TRUE,
                    )
                    
                except ServiceNowAPIError:
                    break

                if not response.result:
                    break

                # Parse records into Pydantic models
                groups = [SysUserGroup(**record.model_dump()) for record in response.result]
                all_groups.extend(groups)
                offset += batch_size

                if len(groups) < batch_size:
                    break

            self.logger.info(f"Fetched {len(all_groups)} groups")
            return all_groups

        except Exception as e:
            self.logger.error(f"❌ Error fetching groups: {e}", exc_info=True)
            raise


    async def _fetch_all_memberships(self) -> List[SysUserGroupMembership]:
        """Fetch all user-group memberships from ServiceNow
        
        Returns:
            List of SysUserGroupMembership Pydantic models
        """
        try:
            last_sync_data = await self.group_sync_point.read_sync_point(ServiceNowSyncPointKeys.GROUPS)
            last_sync_time = (last_sync_data.get(ServiceNowSyncPointKeys.LAST_SYNC_TIME) if last_sync_data else None)

            if last_sync_time:
                self.logger.info(f"🔄 Delta sync: fetching user memberships updated after {last_sync_time}")
                query = f"{ServiceNowFields.SYS_UPDATED_ON}>{last_sync_time}^{ServiceNowQueryValues.ORDER_BY_UPDATED}"
            else:
                self.logger.info("🆕 Full sync: fetching all user memberships")
                query = ServiceNowQueryValues.ORDER_BY_UPDATED

            all_memberships: List[SysUserGroupMembership] = []
            batch_size = ServiceNowDefaults.BATCH_SIZE
            offset = ServiceNowDefaults.PAGINATION_OFFSET
            latest_update_time = None

            while True:
                datasource = await self._get_fresh_datasource()
                try:
                    response = await datasource.get_now_table_tableName(
                        tableName=ServiceNowTables.SYS_USER_GRMEMBER,
                        sysparm_query=query,
                        sysparm_fields=CommonStrings.COMMA.join(
                            [
                                ServiceNowFields.SYS_ID,
                                ServiceNowFields.USER,
                                ServiceNowFields.GROUP,
                                ServiceNowFields.SYS_UPDATED_ON,
                            ]
                        ),
                        sysparm_limit=str(batch_size),
                        sysparm_offset=str(offset),
                        sysparm_display_value=ServiceNowQueryValues.DISPLAY_VALUE_FALSE,
                        sysparm_no_count=ServiceNowQueryValues.NO_COUNT_TRUE,
                        sysparm_exclude_reference_link=ServiceNowQueryValues.EXCLUDE_REFERENCE_LINK_TRUE,
                    )
                    
                except ServiceNowAPIError:
                    break

                if not response.result:
                    break

                # Parse records into Pydantic models
                memberships = [SysUserGroupMembership(**record.model_dump()) for record in response.result]
                latest_update_time = memberships[-1].sys_updated_on

                all_memberships.extend(memberships)
                offset += batch_size

                if len(memberships) < batch_size:
                    break

            self.logger.info(f"Fetched {len(all_memberships)} memberships")
            if latest_update_time:
                await self.group_sync_point.update_sync_point(ServiceNowSyncPointKeys.GROUPS, {ServiceNowSyncPointKeys.LAST_SYNC_TIME: latest_update_time})
            return all_memberships

        except Exception as e:
            self.logger.error(f"❌ Error fetching memberships: {e}", exc_info=True)
            raise


    async def _sync_roles(self) -> None:
        """
        Sync roles using the same flattening logic as user groups.

        Roles are synced by:
        1. Fetching roles, role hierarchy, and role assignments
        2. Merging hierarchy into roles (embed parent field)
        3. Transforming role assignments to look like group memberships
        4. Using the same flatten function as groups
        5. Adding ROLE_ prefix to distinguish from regular groups
        """
        try:
            self.logger.info("Starting role synchronization")

            # Step 1: Fetch role assignments
            role_assignments = await self._fetch_all_role_assignments()
            if not role_assignments:
                self.logger.info("No role assignments found")
                return

            # Step 2: Fetch roles
            roles_data = await self._fetch_all_roles()

            # Step 3: Fetch role hierarchy
            hierarchy_data = await self._fetch_role_hierarchy()

            # Step 4: Merge hierarchy into roles (embed parent field)
            child_to_parent = {}
            for hierarchy_record in hierarchy_data:
                # Both contains and role are sys_id strings
                parent_id = hierarchy_record.contains
                child_id = hierarchy_record.role

                if parent_id and child_id and child_id not in child_to_parent:
                    child_to_parent[child_id] = parent_id

            # Add parent field to roles and convert to SysUserGroup models for flatten function
            roles_with_hierarchy = []
            for role in roles_data:
                # Convert role to SysUserGroup format (roles are treated as groups)
                group_data = {
                    "sys_id": role.sys_id,
                    "name": role.name,
                    "description": role.description,
                    "sys_created_on": role.sys_created_on,
                    "sys_updated_on": role.sys_updated_on,
                    "parent": child_to_parent.get(role.sys_id),  # Add parent sys_id if exists
                    "manager": None,
                    "active": "true"
                }
                roles_with_hierarchy.append(SysUserGroup(**group_data))

            self.logger.info(
                f"Merged hierarchy: {len(roles_with_hierarchy)} roles, "
                f"{len(child_to_parent)} with parents"
            )

            # Step 5: Transform role assignments to SysUserGroupMembership models
            role_assignments_as_memberships = []
            for assignment in role_assignments:
                membership_data = {
                    "sys_id": assignment.sys_id,
                    "user": assignment.user,
                    "group": assignment.role,  # Rename role to group for flatten function
                    "sys_created_on": None,
                    "sys_updated_on": assignment.sys_updated_on
                }
                role_assignments_as_memberships.append(SysUserGroupMembership(**membership_data))

            # Step 6: flatten user roles hierarchy
            roles_with_permissions = await self._flatten_and_create_user_groups(
                roles_with_hierarchy,  # Roles as SysUserGroup models with parent
                role_assignments_as_memberships,  # Role assignments as SysUserGroupMembership models
            )

            # Step 7: Add ROLE_ prefix to names
            for role_group, users in roles_with_permissions:
                if not role_group.name.startswith(ServiceNowPrefixes.ROLE):
                    role_group.name = f"{ServiceNowPrefixes.ROLE}{role_group.name}"

            # Step 8: Upsert roles as user groups
            if roles_with_permissions:
                await self.data_entities_processor.on_new_user_groups(roles_with_permissions)

            self.logger.info(f"✅ Processed {len(roles_with_permissions)} roles")

        except Exception as e:
            self.logger.error(f"❌ Error syncing roles: {e}", exc_info=True)
            raise


    async def _fetch_all_roles(self) -> List[SysUserRole]:
        """Fetch all roles from sys_user_role table.
        
        Returns:
            List of SysUserRole Pydantic models
        """
        try:
            self.logger.info("Fetching all roles")

            all_roles: List[SysUserRole] = []
            batch_size = ServiceNowDefaults.BATCH_SIZE
            offset = ServiceNowDefaults.PAGINATION_OFFSET

            while True:
                datasource = await self._get_fresh_datasource()
                try:
                    response = await datasource.get_now_table_tableName(
                        tableName=ServiceNowTables.SYS_USER_ROLE,
                        sysparm_query=None,
                        sysparm_fields=CommonStrings.COMMA.join(
                            [
                                ServiceNowFields.SYS_ID,
                                ServiceNowFields.NAME,
                                ServiceNowFields.DESCRIPTION,
                                ServiceNowFields.SYS_CREATED_ON,
                                ServiceNowFields.SYS_UPDATED_ON,
                            ]
                        ),
                        sysparm_limit=str(batch_size),
                        sysparm_offset=str(offset),
                        sysparm_display_value=ServiceNowQueryValues.DISPLAY_VALUE_FALSE,
                        sysparm_no_count=ServiceNowQueryValues.NO_COUNT_TRUE,
                        sysparm_exclude_reference_link=ServiceNowQueryValues.EXCLUDE_REFERENCE_LINK_TRUE,
                    )
                    
                except ServiceNowAPIError:
                    break

                if not response.result:
                    break

                # Parse records into Pydantic models
                roles = [SysUserRole(**record.model_dump()) for record in response.result]
                all_roles.extend(roles)

                offset += batch_size
                if len(roles) < batch_size:
                    break

            self.logger.info(f"Fetched {len(all_roles)} roles")
            return all_roles

        except Exception as e:
            self.logger.error(f"❌ Error fetching roles: {e}", exc_info=True)
            raise


    async def _fetch_all_role_assignments(self) -> List[SysUserRoleAssignment]:
        """
        Fetch all user-role assignments from sys_user_has_role table.
        
        Returns:
            List of SysUserRoleAssignment Pydantic models
        """
        try:
            last_sync_data = await self.role_assignment_sync_point.read_sync_point(ServiceNowSyncPointKeys.ROLE_ASSIGNMENTS)
            last_sync_time = last_sync_data.get(ServiceNowSyncPointKeys.LAST_SYNC_TIME) if last_sync_data else None

            if last_sync_time:
                self.logger.info(f"🔄 Delta sync: fetching role assignments updated after {last_sync_time}")
                query = f"state={ServiceNowFields.ACTIVE}^{ServiceNowFields.SYS_UPDATED_ON}>{last_sync_time}^{ServiceNowQueryValues.ORDER_BY_UPDATED}"
            else:
                self.logger.info("🆕 Full sync: fetching all active role assignments")
                query = f"state={ServiceNowFields.ACTIVE}^{ServiceNowQueryValues.ORDER_BY_UPDATED}"

            all_assignments: List[SysUserRoleAssignment] = []
            batch_size = ServiceNowDefaults.BATCH_SIZE
            offset = ServiceNowDefaults.PAGINATION_OFFSET
            latest_update_time = None

            while True:
                datasource = await self._get_fresh_datasource()
                try:
                    response = await datasource.get_now_table_tableName(
                        tableName=ServiceNowTables.SYS_USER_HAS_ROLE,
                        sysparm_query=query,
                        sysparm_fields=CommonStrings.COMMA.join(
                            [
                                ServiceNowFields.SYS_ID,
                                ServiceNowFields.USER,
                                ServiceNowFields.ROLE,
                                ServiceNowFields.SYS_UPDATED_ON,
                            ]
                        ),
                        sysparm_limit=str(batch_size),
                        sysparm_offset=str(offset),
                        sysparm_display_value=ServiceNowQueryValues.DISPLAY_VALUE_FALSE,
                        sysparm_no_count=ServiceNowQueryValues.NO_COUNT_TRUE,
                        sysparm_exclude_reference_link=ServiceNowQueryValues.EXCLUDE_REFERENCE_LINK_TRUE,
                    )
                except ServiceNowAPIError:
                    break

                if not response.result:
                    break

                # Parse records into Pydantic models
                assignments = [SysUserRoleAssignment(**record.model_dump()) for record in response.result]
                all_assignments.extend(assignments)
                latest_update_time = assignments[-1].sys_updated_on

                offset += batch_size
                if len(assignments) < batch_size:
                    break

            if latest_update_time:
                await self.role_assignment_sync_point.update_sync_point(
                    ServiceNowSyncPointKeys.ROLE_ASSIGNMENTS,
                    {ServiceNowSyncPointKeys.LAST_SYNC_TIME: latest_update_time}
                )

            self.logger.info(f"Fetched {len(all_assignments)} role assignments")
            return all_assignments

        except Exception as e:
            self.logger.error(f"❌ Error fetching role assignments: {e}", exc_info=True)
            raise


    async def _fetch_role_hierarchy(self) -> List[SysUserRoleContains]:
        """
        Fetch role hierarchy from sys_user_role_contains table.

        This is a full sync (no checkpoint) since role hierarchy changes are rare.
        
        Returns:
            List of SysUserRoleContains Pydantic models
        """
        try:
            self.logger.info("Fetching role hierarchy")

            all_hierarchy: List[SysUserRoleContains] = []
            batch_size = ServiceNowDefaults.BATCH_SIZE
            offset = ServiceNowDefaults.PAGINATION_OFFSET

            while True:
                datasource = await self._get_fresh_datasource()
                try:
                    response = await datasource.get_now_table_tableName(
                        tableName=ServiceNowTables.SYS_USER_ROLE_CONTAINS,
                        sysparm_query=None,
                        sysparm_fields=CommonStrings.COMMA.join(
                            [
                                ServiceNowFields.SYS_ID,
                                ServiceNowFields.CONTAINS,
                                ServiceNowFields.ROLE,
                            ]
                        ),
                        sysparm_limit=str(batch_size),
                        sysparm_offset=str(offset),
                        sysparm_display_value=ServiceNowQueryValues.DISPLAY_VALUE_FALSE,
                        sysparm_no_count=ServiceNowQueryValues.NO_COUNT_TRUE,
                        sysparm_exclude_reference_link=ServiceNowQueryValues.EXCLUDE_REFERENCE_LINK_TRUE,
                    )
                    
                except ServiceNowAPIError:
                    break

                if not response.result:
                    break

                # Parse records into Pydantic models
                hierarchy = [SysUserRoleContains(**record.model_dump()) for record in response.result]
                all_hierarchy.extend(hierarchy)

                offset += batch_size
                if len(hierarchy) < batch_size:
                    break

            self.logger.info(f"Fetched {len(all_hierarchy)} role hierarchy records")
            return all_hierarchy

        except Exception as e:
            self.logger.error(f"❌ Error fetching role hierarchy: {e}", exc_info=True)
            raise


    async def _flatten_and_create_user_groups(
        self,
        groups_data: List[SysUserGroup],
        memberships_data: List[SysUserGroupMembership]
    ) -> List[Tuple[AppUserGroup, List[AppUser]]]:
        """
        Flatten group hierarchy and create AppUserGroup objects.

        Args:
            groups_data: List of SysUserGroup Pydantic models
            memberships_data: List of SysUserGroupMembership Pydantic models

        Returns:
            List of (AppUserGroup, [AppUser]) tuples
        """
        try:
            # Build parent-child relationships
            children_map = defaultdict(set)  # parent_id -> {child_ids}
            group_by_id = {}  # group_id -> group_data

            for group in groups_data:
                group_id = group.sys_id
                group_by_id[group_id] = group

                # Extract parent sys_id
                parent_id = group.parent
                if parent_id:
                    children_map[parent_id].add(group_id)

            # Build direct user memberships
            direct_users = defaultdict(set)  # group_id -> {user_ids}

            for membership in memberships_data:
                user_id = membership.user
                group_id = membership.group

                if user_id and group_id:
                    direct_users[group_id].add(user_id)

            # Recursive function to get all users for a group
            def get_all_users(group_id: str, visited: set = None) -> set:
                """Get all users including inherited from child groups."""
                if visited is None:
                    visited = set()

                # Prevent infinite loops
                if group_id in visited:
                    return set()
                visited.add(group_id)

                # Start with direct users
                all_users = set(direct_users.get(group_id, []))

                # Add users from child groups recursively
                for child_id in children_map.get(group_id, []):
                    all_users.update(get_all_users(child_id, visited))

                return all_users

            # Create AppUserGroup objects with flattened members
            result = []

             # Get all existing users from database for lookup
            async with self.data_store_provider.transaction() as tx_store:
                existing_app_users = await tx_store.get_app_users(
                    org_id=self.data_entities_processor.org_id,
                    connector_id=self.connector_id
                )

                # Create lookup map: source_user_id -> AppUser
                user_lookup = {user.source_user_id: user for user in existing_app_users}

            for group_id, group_data in group_by_id.items():
                # Create AppUserGroup
                user_group = self._transform_to_user_group(group_data)

                if not user_group:
                    continue

                # Get flattened user IDs
                flattened_user_ids = get_all_users(group_id)

                # Create AppUser objects
                app_users = []
                for user_id in flattened_user_ids:
                    app_user = user_lookup.get(user_id)
                    if app_user:
                        app_users.append(app_user)

                result.append((user_group, app_users))

            self.logger.info(f"Flattened {len(result)} groups")
            return result

        except Exception as e:
            self.logger.error(f"❌ Error flattening groups: {e}", exc_info=True)
            raise

    async def _sync_organizational_entities(self) -> None:
        """
        Sync all organizational entities from ServiceNow.

        Syncs in order:
        1. Companies (top-level)
        2. Departments
        3. Locations
        4. Cost Centers

        Each entity type creates:
        - AppUserGroup nodes with prefix (COMPANY_, DEPARTMENT_, etc.)
        - Parent-child hierarchy edges between entities (commented out for now)

        Note: User-to-organizational-entity edges are created during user sync.
        """
        try:
            self.logger.info("🏢 Starting organizational entities sync")

            # Sync each entity type in order
            for entity_type, config in ORGANIZATIONAL_ENTITIES.items():
                await self._sync_single_organizational_entity(entity_type, config)

            self.logger.info("✅ All organizational entities synced successfully")

        except Exception as e:
            self.logger.error(f"❌ Error syncing organizational entities: {e}", exc_info=True)
            raise

    async def _sync_single_organizational_entity(
        self, entity_type: str, config: OrganizationalEntityConfig
    ) -> None:
        """
        Generic sync method for a single organizational entity type.

        Uses two-pass approach:
        - Pass 1: Create all entity nodes as AppUserGroups
        - Pass 2: Create hierarchy edges (parent-child) - COMMENTED OUT FOR NOW

        Args:
            entity_type: Type of entity (company, department, location, cost_center)
            config: OrganizationalEntityConfig with table name, fields, prefix, and sync point key
        """
        try:
            table_name = config["table"]
            fields = config["fields"]
            prefix = config["prefix"]
            sync_point_key = config["sync_point_key"]

            # Get sync point for this entity type
            sync_point = self.org_entity_sync_points.get(entity_type)

            self.logger.info(f"📊 Starting {entity_type} sync from table {table_name}")

            # Get last sync checkpoint
            last_sync_data = await sync_point.read_sync_point(sync_point_key)
            last_sync_time = (
                last_sync_data.get(ServiceNowSyncPointKeys.LAST_SYNC_TIME) if last_sync_data else None
            )

            if last_sync_time:
                self.logger.info(f"🔄 Delta sync: fetching {entity_type} updated after {last_sync_time}")
                query = f"{ServiceNowFields.SYS_UPDATED_ON}>{last_sync_time}^{ServiceNowQueryValues.ORDER_BY_UPDATED}"
            else:
                self.logger.info(f"🆕 Full sync: fetching all {entity_type}")
                query = ServiceNowQueryValues.ORDER_BY_UPDATED

            # Pagination variables
            batch_size = ServiceNowDefaults.BATCH_SIZE
            offset = ServiceNowDefaults.PAGINATION_OFFSET
            total_synced = 0
            latest_update_time = None

            # Fetch and create all entity nodes
            while True:
                datasource = await self._get_fresh_datasource()
                try:
                    response = await datasource.get_now_table_tableName(
                        tableName=table_name,
                        sysparm_query=query,
                        sysparm_fields=fields,
                        sysparm_limit=str(batch_size),
                        sysparm_offset=str(offset),
                        sysparm_display_value=ServiceNowQueryValues.DISPLAY_VALUE_FALSE,
                        sysparm_no_count=ServiceNowQueryValues.NO_COUNT_TRUE,
                        sysparm_exclude_reference_link=ServiceNowQueryValues.EXCLUDE_REFERENCE_LINK_TRUE,
                    )
                    
                except ServiceNowAPIError as e:
                    self.logger.error(f"❌ API error: {e.message} (status: {e.status_code})")
                    break

                # Extract entities from response
                entities_data = response.result

                if not entities_data:
                    break

                # Track latest update timestamp
                if entities_data:
                    latest_update_time = entities_data[-1].sys_updated_on

                # Transform to AppUserGroup entities
                user_groups = []
                for entity_data in entities_data:
                    user_group = self._transform_to_organizational_group(
                        entity_data, prefix
                    )
                    if user_group:
                        user_groups.append(user_group)

                # Batch upsert entity nodes
                if user_groups:
                    async with self.data_store_provider.transaction() as tx_store:
                        await tx_store.batch_upsert_user_groups(user_groups)

                    total_synced += len(user_groups)

                # Move to next page
                offset += batch_size

                # If fewer records than batch_size, we're done
                if len(entities_data) < batch_size:
                    break

            # Save checkpoint
            if latest_update_time:
                await sync_point.update_sync_point(
                    sync_point_key, {ServiceNowSyncPointKeys.LAST_SYNC_TIME: latest_update_time}
                )

            self.logger.info(
                f"✅ {entity_type.capitalize()} sync complete. Total synced: {total_synced}"
            )

        except Exception as e:
            self.logger.error(
                f"❌ {entity_type.capitalize()} sync failed: {e}", exc_info=True
            )
            raise

    async def _sync_knowledge_bases(self, admin_users: List[AppUser]) -> None:
        """
        Sync knowledge bases from ServiceNow kb_knowledge_base table.

        Uses data_entities_processor.on_new_record_groups() so that BELONGS_TO edges
        (RecordGroup → Org, RecordGroup → App) are created; Knowledge Hub tree relies on these.

        Creates:
        - RecordGroup nodes (type=SERVICENOW) in recordGroups collection
        - BELONGS_TO edges: RecordGroup → Org, RecordGroup → App
        - OWNER edges: owner → KB RecordGroup
        - WRITER edges: kb_managers → KB RecordGroup
        - READ edges: admin users → KB RecordGroup

        First sync: Fetches all KBs
        Subsequent syncs: Only fetches KBs modified since last sync

        Args:
            admin_users: List of admin users to grant explicit READ permissions
        """
        try:
            # Get sync checkpoint for delta sync
            last_sync_data = await self.kb_sync_point.read_sync_point(ServiceNowSyncPointKeys.KNOWLEDGE_BASES)
            last_sync_time = (last_sync_data.get(ServiceNowSyncPointKeys.LAST_SYNC_TIME) if last_sync_data else None)

            if last_sync_time:
                self.logger.info(f"🔄 Delta sync: Fetching KBs updated after {last_sync_time}")
                query = f"{ServiceNowFields.SYS_UPDATED_ON}>{last_sync_time}^{ServiceNowQueryValues.ORDER_BY_UPDATED}"
            else:
                self.logger.info("🆕 Full sync: Fetching all knowledge bases")
                query = ServiceNowQueryValues.ORDER_BY_UPDATED

            # Pagination variables
            batch_size = ServiceNowDefaults.BATCH_SIZE
            offset = ServiceNowDefaults.PAGINATION_OFFSET
            total_synced = 0
            latest_update_time = None

            # Paginate through all KBs
            while True:
                # Fetch KBs from ServiceNow
                datasource = await self._get_fresh_datasource()
                try:
                    response = await datasource.get_now_table_tableName(
                        tableName=ServiceNowTables.KB_KNOWLEDGE_BASE,
                        sysparm_query=query,
                        sysparm_fields=CommonStrings.COMMA.join(
                            [
                                ServiceNowFields.SYS_ID,
                                ServiceNowFields.TITLE,
                                ServiceNowFields.DESCRIPTION,
                                ServiceNowFields.OWNER,
                                ServiceNowFields.KB_MANAGERS,
                                ServiceNowFields.ACTIVE,
                                ServiceNowFields.SYS_CREATED_ON,
                                ServiceNowFields.SYS_UPDATED_ON,
                            ]
                        ),
                        sysparm_limit=str(batch_size),
                        sysparm_offset=str(offset),
                        sysparm_display_value=ServiceNowQueryValues.DISPLAY_VALUE_FALSE,
                        sysparm_no_count=ServiceNowQueryValues.NO_COUNT_TRUE,
                        sysparm_exclude_reference_link=ServiceNowQueryValues.EXCLUDE_REFERENCE_LINK_TRUE,
                    )
                    
                except ServiceNowAPIError as e:
                    if "Expecting value" not in str(e):
                        self.logger.error(f"❌ API error: {e.message} (status: {e.status_code})")
                    break

                # Extract KBs from response
                kbs_data = response.result

                if not kbs_data:
                    self.logger.info("✅ No more knowledge bases to fetch")
                    break

                # Track the latest update timestamp for checkpoint
                if kbs_data:
                    latest_update_time = kbs_data[-1].sys_updated_on

                # Transform to RecordGroup entities
                kb_record_groups = []
                for kb_data in kbs_data:
                    kb_record_group = self._transform_to_kb_record_group(kb_data)
                    if kb_record_group:
                        kb_record_groups.append((kb_record_group, kb_data))

                # Build (RecordGroup, permissions) list and route through on_new_record_groups so that
                # BELONGS_TO edges (RecordGroup → Org, RecordGroup → App) are created by the processor.
                if kb_record_groups:
                    kb_list_with_permissions = []
                    async with self.data_store_provider.transaction() as tx_store:
                        for kb_record_group, kb_data in kb_record_groups:
                            kb_sys_id = kb_data.sys_id

                            # Fetch criteria IDs for this KB
                            criteria_map = await self._fetch_kb_permissions_from_criteria(kb_sys_id)

                            # Process READ permissions using shared method
                            read_permissions = await self._process_criteria_permissions(
                                criteria_map[ServiceNowDictKeys.READ],
                                PermissionType.READ,
                                tx_store,
                            )

                            # Process WRITE permissions using shared method
                            write_permissions = await self._process_criteria_permissions(
                                criteria_map[ServiceNowDictKeys.WRITE],
                                PermissionType.WRITE,
                                tx_store,
                            )

                            # Combine all permissions
                            permission_objects = read_permissions + write_permissions

                            # Add OWNER permission (fallback from owner field)
                            owner_sys_id = kb_data.owner
                            if owner_sys_id:
                                owner_perms = await self._convert_permissions_to_objects(
                                    [
                                        RawPermission(
                                            entity_type="USER",
                                            source_sys_id=owner_sys_id,
                                            role="OWNER",
                                        )
                                    ],
                                    tx_store,
                                )
                                permission_objects.extend(owner_perms)

                            # Add admin users as explicit READ permissions
                            for admin_user in admin_users:
                                admin_permission = Permission(
                                    email=admin_user.email,
                                    type=PermissionType.READ,
                                    entity_type=EntityType.USER,
                                )
                                permission_objects.append(admin_permission)

                            kb_list_with_permissions.append((kb_record_group, permission_objects))

                    await self.data_entities_processor.on_new_record_groups(kb_list_with_permissions)
                    total_synced += len(kb_list_with_permissions)

                # Move to next page
                offset += batch_size

                # If this page has fewer records than batch_size, we're done
                if len(kbs_data) < batch_size:
                    break

            # Save checkpoint for next sync
            if latest_update_time:
                await self.kb_sync_point.update_sync_point(ServiceNowSyncPointKeys.KNOWLEDGE_BASES, {ServiceNowSyncPointKeys.LAST_SYNC_TIME: latest_update_time})

            self.logger.info(f"✅ Knowledge base sync complete, Total synced: {total_synced}")

        except Exception as e:
            self.logger.error(f"❌ Error syncing knowledge bases: {e}", exc_info=True)
            raise

    async def _sync_categories(self) -> None:
        """
        Sync categories from ServiceNow kb_category table.

        Creates:
        - RecordGroup nodes (type=SERVICENOW_CATEGORY) in recordGroups collection
        - PARENT_CHILD edges in recordRelations collection

        First sync: Fetches all categories
        Subsequent syncs: Only fetches categories modified since last sync
        """
        try:
            # Get sync checkpoint for delta sync
            last_sync_data = await self.category_sync_point.read_sync_point(ServiceNowSyncPointKeys.CATEGORIES)
            last_sync_time = (last_sync_data.get(ServiceNowSyncPointKeys.LAST_SYNC_TIME) if last_sync_data else None)

            if last_sync_time:
                self.logger.info(f"🔄 Delta sync: Fetching categories updated after {last_sync_time}")
                query = f"{ServiceNowFields.SYS_UPDATED_ON}>{last_sync_time}^{ServiceNowQueryValues.ORDER_BY_UPDATED}"
            else:
                self.logger.info("🆕 Full sync: Fetching all categories")
                query = ServiceNowQueryValues.ORDER_BY_UPDATED

            # Pagination variables
            batch_size = ServiceNowDefaults.BATCH_SIZE
            offset = ServiceNowDefaults.PAGINATION_OFFSET
            total_synced = 0
            latest_update_time = None

            # Paginate through all categories
            while True:
                # Fetch categories from ServiceNow
                datasource = await self._get_fresh_datasource()
                try:
                    response = await datasource.get_now_table_tableName(
                        tableName=ServiceNowTables.KB_CATEGORY,
                        sysparm_query=query,
                        sysparm_fields=CommonStrings.COMMA.join(
                            [
                                ServiceNowFields.SYS_ID,
                                ServiceNowFields.LABEL,
                                ServiceNowFields.VALUE,
                                ServiceNowFields.PARENT_TABLE,
                                ServiceNowFields.PARENT_ID,
                                ServiceNowFields.KB_KNOWLEDGE_BASE,
                                ServiceNowFields.ACTIVE,
                                ServiceNowFields.SYS_CREATED_ON,
                                ServiceNowFields.SYS_UPDATED_ON,
                            ]
                        ),
                        sysparm_limit=str(batch_size),
                        sysparm_offset=str(offset),
                        sysparm_display_value=ServiceNowQueryValues.DISPLAY_VALUE_FALSE,
                        sysparm_no_count=ServiceNowQueryValues.NO_COUNT_TRUE,
                        sysparm_exclude_reference_link=ServiceNowQueryValues.EXCLUDE_REFERENCE_LINK_TRUE,
                    )
                    
                except ServiceNowAPIError as e:
                    self.logger.error(f"❌ API error: {e.message} (status: {e.status_code}")
                    break

                # Extract categories from response
                categories_data = response.result

                if not categories_data:
                    break

                # Track the latest update timestamp for checkpoint
                if categories_data:
                    latest_update_time = categories_data[-1].sys_updated_on

                # Transform categories to RecordGroups with hierarchy information
                categories_with_permissions = []
                for cat_data in categories_data:
                    category_rg = self._transform_to_category_record_group(cat_data)
                    if not category_rg:
                        continue

                    # Categories inherit permissions from parent KB
                    categories_with_permissions.append((category_rg, []))

                # Use on_new_record_groups to create nodes and edges in one transaction
                if categories_with_permissions:
                    await self.data_entities_processor.on_new_record_groups(categories_with_permissions)
                    total_synced += len(categories_with_permissions)

                # Move to next page
                offset += batch_size

                # If this page has fewer records than batch_size, we're done
                if len(categories_data) < batch_size:
                    break

            # Update sync checkpoint
            if latest_update_time:
                await self.category_sync_point.update_sync_point(ServiceNowSyncPointKeys.CATEGORIES, {ServiceNowSyncPointKeys.LAST_SYNC_TIME: latest_update_time})

            self.logger.info(f"✅ Categories synced: {total_synced} total")

        except Exception as e:
            self.logger.error(f"❌ Error syncing categories: {e}", exc_info=True)
            raise

    async def _sync_articles(self) -> None:
        """
        Sync KB articles and attachments from ServiceNow using batch processing.

        Flow:
        1. Fetch 100 articles in a batch
        2. Batch fetch user_criteria for all articles (efficiency)
        3. For each article:
           - Create WebpageRecord + fetch attachments + create all edges + permissions
        4. Update checkpoint after batch

        API Endpoints:
        - /api/now/table/kb_knowledge - Articles
        - /api/now/table/user_criteria - Permissions
        - /api/now/attachment - Attachments
        """
        try:
            # Get sync checkpoint
            last_sync_data = await self.article_sync_point.read_sync_point(ServiceNowSyncPointKeys.ARTICLES)
            last_sync_time = (last_sync_data.get(ServiceNowSyncPointKeys.LAST_SYNC_TIME) if last_sync_data else None)

            if last_sync_time:
                self.logger.info(f"🔄 Delta sync: Fetching articles updated after {last_sync_time}")
                query = f"{ServiceNowFields.ACTIVE}=true^{ServiceNowFields.WORKFLOW_STATE}={ServiceNowFields.PUBLISHED}^{ServiceNowFields.SYS_UPDATED_ON}>{last_sync_time}^{ServiceNowQueryValues.ORDER_BY_UPDATED}"
            else:
                self.logger.info("🆕 Full sync: Fetching all articles")
                query = f"{ServiceNowFields.ACTIVE}=true^{ServiceNowFields.WORKFLOW_STATE}={ServiceNowFields.PUBLISHED}^{ServiceNowQueryValues.ORDER_BY_UPDATED}"

            # Pagination variables
            batch_size = ServiceNowDefaults.BATCH_SIZE
            offset = ServiceNowDefaults.PAGINATION_OFFSET
            total_articles_synced = 0
            total_attachments_synced = 0
            latest_update_time = None

            # Paginate through all articles
            while True:
                # Fetch batch of 100 articles
                datasource = await self._get_fresh_datasource()
                try:
                    response = await datasource.get_now_table_tableName(
                        tableName=ServiceNowTables.KB_KNOWLEDGE,
                        sysparm_query=query,
                        sysparm_fields=CommonStrings.COMMA.join(
                            [
                                ServiceNowFields.SYS_ID,
                                ServiceNowFields.NUMBER,
                                ServiceNowFields.SHORT_DESCRIPTION,
                                ServiceNowFields.TEXT,
                                ServiceNowFields.AUTHOR,
                                ServiceNowFields.KB_KNOWLEDGE_BASE,
                                ServiceNowFields.KB_CATEGORY,
                                ServiceNowFields.WORKFLOW_STATE,
                                ServiceNowFields.ACTIVE,
                                ServiceNowFields.PUBLISHED,
                                ServiceNowFields.CAN_READ_USER_CRITERIA,
                                ServiceNowFields.SYS_CREATED_ON,
                                ServiceNowFields.SYS_UPDATED_ON,
                            ]
                        ),
                        sysparm_limit=str(batch_size),
                        sysparm_offset=str(offset),
                        sysparm_display_value=ServiceNowQueryValues.DISPLAY_VALUE_FALSE,
                        sysparm_no_count=ServiceNowQueryValues.NO_COUNT_TRUE,
                        sysparm_exclude_reference_link=ServiceNowQueryValues.EXCLUDE_REFERENCE_LINK_TRUE,
                    )
                    
                except ServiceNowAPIError as e:
                    self.logger.error(f"❌ API error: {e.message} (status: {e.status_code})")
                    break

                # Extract articles from response
                articles_data = response.result

                if not articles_data:
                    break

                # Track the latest update timestamp for checkpoint
                if articles_data:
                    latest_update_time = articles_data[-1].sys_updated_on

                # Collect RecordUpdates for this batch
                record_updates = []

                for article_data in articles_data:
                    try:
                        updates = await self._process_single_article(article_data)
                        if updates:
                            record_updates.extend(updates)
                            total_articles_synced += 1
                            # Count attachments
                            total_attachments_synced += len([u for u in updates if u.record.record_type == RecordType.FILE])
                    except Exception as e:
                        article_id = getattr(article_data, 'sys_id', 'unknown')
                        self.logger.error(f"❌ Failed to process article {article_id}: {e}", exc_info=True)

                # Process batch of RecordUpdates
                if record_updates:
                    await self._process_record_updates_batch(record_updates)

                # Move to next batch
                offset += batch_size

                # If this batch has fewer records than batch_size, we're done
                if len(articles_data) < batch_size:
                    break

            # Update sync checkpoint
            if latest_update_time:
                await self.article_sync_point.update_sync_point(ServiceNowSyncPointKeys.ARTICLES, {ServiceNowSyncPointKeys.LAST_SYNC_TIME: latest_update_time})

            self.logger.info(f"✅ Articles synced: {total_articles_synced} articles, {total_attachments_synced} attachments")

        except Exception as e:
            self.logger.error(f"❌ Error syncing articles: {e}", exc_info=True)
            raise

    async def _process_single_article(
        self, article_data: TableAPIRecord
    ) -> List[RecordUpdate]:
        """
        Process a single article and return RecordUpdate objects for article + attachments.

        Args:
            article_data: ServiceNow kb_knowledge TableAPIRecord

        Returns:
            List[RecordUpdate]: RecordUpdate for article + RecordUpdates for attachments
        """
        try:
            article_sys_id = article_data.sys_id
            article_title = article_data.short_description or ServiceNowDefaults.UNKNOWN_VALUE

            record_updates = []

            # Transform article to WebpageRecord
            article_record = self._transform_to_article_webpage_record(article_data)
            if not article_record:
                self.logger.warning(f"Failed to transform article {article_sys_id}")
                return []

            # Fetch attachments for this article
            attachments_data = await self._fetch_attachments_for_article(article_sys_id)

            # Extract criteria IDs from article's can_read_user_criteria field
            can_read_criteria = article_data.can_read_user_criteria or ""
            criteria_ids = []
            if can_read_criteria:
                # Split comma-separated sys_ids
                criteria_ids = [c.strip() for c in can_read_criteria.split(",") if c.strip()]

            # Process READ permissions using shared method
            async with self.data_store_provider.transaction() as tx_store:
                all_permission_objects = await self._process_criteria_permissions(
                    criteria_ids,
                    PermissionType.READ,
                    tx_store
                )

                # Add OWNER permission from author field
                author_sys_id = article_data.author
                if author_sys_id:
                    owner_perms = await self._convert_permissions_to_objects(
                        [RawPermission(
                            entity_type="USER",
                            source_sys_id=author_sys_id,
                            role="OWNER",
                        )],
                        tx_store
                    )
                    all_permission_objects.extend(owner_perms)

            # Create RecordUpdate for article
            article_update = RecordUpdate(
                record=article_record,
                is_new=True,
                is_updated=False,
                is_deleted=False,
                metadata_changed=False,
                content_changed=False,
                permissions_changed=True,
                new_permissions=all_permission_objects,
                external_record_id=article_sys_id,
            )
            record_updates.append(article_update)

            # Process attachments
            for att_data in attachments_data:
                att_sys_id = att_data.sys_id

                # Transform attachment to FileRecord
                att_record = self._transform_to_attachment_file_record(
                    att_data,
                    parent_record_group_type=article_record.record_group_type,
                    parent_external_record_group_id=article_record.external_record_group_id,
                )

                if att_record:
                    # Attachments inherit all permissions from article
                    attachment_update = RecordUpdate(
                        record=att_record,
                        is_new=True,
                        is_updated=False,
                        is_deleted=False,
                        metadata_changed=False,
                        content_changed=False,
                        permissions_changed=True,
                        new_permissions=all_permission_objects,  # Same as article
                        external_record_id=att_sys_id,
                    )
                    record_updates.append(attachment_update)

            return record_updates

        except Exception as e:
            self.logger.error(f"Failed to process article {article_data.get('sys_id')}: {e}", exc_info=True)
            return []

    async def _process_record_updates_batch(self, record_updates: List[RecordUpdate]) -> None:
        """
        Process a batch of RecordUpdates using the data entities processor.

        This method converts RecordUpdates to (Record, Permissions) tuples and passes them
        to on_new_records() for batch processing.

        Args:
            record_updates: List of RecordUpdate objects
        """
        try:
            if not record_updates:
                return

            # Convert RecordUpdates to (Record, Permissions) tuples
            records_with_permissions = []
            for update in record_updates:
                if update.record and update.new_permissions:
                    records_with_permissions.append((update.record, update.new_permissions))

            # Use processor's batch method
            if records_with_permissions:
                await self.data_entities_processor.on_new_records(records_with_permissions)

        except Exception as e:
            self.logger.error(f"Failed to process record updates batch: {e}", exc_info=True)
            raise

    async def _fetch_attachments_for_article(
        self, article_sys_id: str
    ) -> List[AttachmentMetadata]:
        """
        Fetch all attachments for a single article.

        Args:
            article_sys_id: Article sys_id

        Returns:
            List of AttachmentMetadata Pydantic models
        """
        try:
            # Query: table_name=kb_knowledge^table_sys_id={article_sys_id}
            query = f"{ServiceNowFields.TABLE_NAME}={ServiceNowTables.KB_KNOWLEDGE}^{ServiceNowFields.TABLE_SYS_ID}={article_sys_id}"

            datasource = await self._get_fresh_datasource()
            response = await datasource.get_now_table_tableName(
                tableName=ServiceNowTables.SYS_ATTACHMENT,
                sysparm_query=query,
                sysparm_fields=CommonStrings.COMMA.join(
                    [
                        ServiceNowFields.SYS_ID,
                        ServiceNowFields.FILE_NAME,
                        ServiceNowFields.CONTENT_TYPE,
                        ServiceNowFields.SIZE_BYTES,
                        ServiceNowFields.TABLE_SYS_ID,
                        ServiceNowFields.SYS_CREATED_ON,
                        ServiceNowFields.SYS_UPDATED_ON,
                    ]
                ),
                sysparm_display_value=ServiceNowQueryValues.DISPLAY_VALUE_FALSE,
                sysparm_no_count=ServiceNowQueryValues.NO_COUNT_TRUE,
                sysparm_exclude_reference_link=ServiceNowQueryValues.EXCLUDE_REFERENCE_LINK_TRUE,
            )

            # Parse records into Pydantic models
            return [AttachmentMetadata(**record.model_dump()) for record in response.result]

        except ServiceNowAPIError as e:
            self.logger.warning(f"Failed to fetch attachments for article {article_sys_id}: {e.message}")
            return []

        except Exception as e:
            self.logger.warning(f"Failed to fetch attachments for article {article_sys_id}: {e}")
            return []

    async def _convert_permissions_to_objects(
        self, permissions_dict: List[RawPermission], tx_store: TransactionStore
    ) -> List[Permission]:
        """
        Convert USER and GROUP permissions from RawPermission format to Permission objects.

        ServiceNow-specific: Uses sourceUserId field to look up users, then gets their email.
        This method handles the connector-specific logic for permission mapping.

        Args:
            permissions_dict: List of RawPermission Pydantic models with entity_type, source_sys_id, role
                Example: [
                    RawPermission(entity_type="USER", source_sys_id="abc123", role="OWNER"),
                    RawPermission(entity_type="GROUP", source_sys_id="group456", role="WRITE")
                ]
            tx_store: Transaction store for database access

        Returns:
            List of Permission objects ready for edge creation
        """
        permission_objects = []

        for perm in permissions_dict:
            try:
                entity_type = perm.entity_type
                source_sys_id = perm.source_sys_id
                role = perm.role

                if entity_type == EntityType.USER.value:
                    # Use tx_store method to get user by source_sys_id
                    user = await tx_store.get_user_by_source_id(
                        source_sys_id,
                        self.connector_id
                    )

                    if user:
                        permission_objects.append(
                            Permission(
                                email=user.email,
                                type=PermissionType(role),
                                entity_type=EntityType.USER,
                            )
                        )
                    else:
                        self.logger.warning(f"User not found for source_sys_id: {source_sys_id}")

                elif entity_type == EntityType.GROUP.value:
                    # Groups use external_id directly (no lookup needed)
                    permission_objects.append(
                        Permission(
                            external_id=source_sys_id,
                            type=PermissionType(role),
                            entity_type=EntityType.GROUP,
                        )
                    )
                else:
                    self.logger.warning(f"Unknown entity_type '{entity_type}' in permission: {perm}")

            except Exception as e:
                self.logger.error(f"Failed to convert permission {perm}: {str(e)}", exc_info=True)

        return permission_objects

    async def _fetch_kb_permissions_from_criteria(
        self, kb_sys_id: str
    ) -> Dict[str, List[str]]:
        """
        Fetch permission criteria IDs for a knowledge base from mtom tables.

        ServiceNow KB permissions use many-to-many tables:
        - kb_uc_can_read_mtom: Read permissions (maps to READER)
        - kb_uc_can_contribute_mtom: Contribute permissions (maps to WRITER)

        Args:
            kb_sys_id: Knowledge base sys_id

        Returns:
            Dict with 'read' and 'write' lists of criteria sys_ids
        """
        try:
            criteria_map = {
                ServiceNowDictKeys.READ: [],
                ServiceNowDictKeys.WRITE: []
            }

            # Fetch READ criteria
            datasource = await self._get_fresh_datasource()
            try:
                read_response = await datasource.get_now_table_tableName(
                    tableName=ServiceNowTables.KB_UC_CAN_READ_MTOM,
                    sysparm_query=f"{ServiceNowFields.KB_KNOWLEDGE_BASE}={kb_sys_id}",
                    sysparm_fields=ServiceNowFields.USER_CRITERIA,
                    sysparm_display_value=ServiceNowQueryValues.DISPLAY_VALUE_FALSE,
                    sysparm_exclude_reference_link=ServiceNowQueryValues.EXCLUDE_REFERENCE_LINK_TRUE,
                )
                
                for record in read_response.result:
                    criteria_id = record.user_criteria
                    if criteria_id:
                        criteria_map[ServiceNowDictKeys.READ].append(criteria_id)
            except ServiceNowAPIError as e:
                self.logger.warning(f"Failed to fetch READ criteria for KB {kb_sys_id}: {e.message}")

            # Fetch WRITE criteria (contribute)
            datasource = await self._get_fresh_datasource()
            try:
                write_response = await datasource.get_now_table_tableName(
                    tableName=ServiceNowTables.KB_UC_CAN_CONTRIBUTE_MTOM,
                    sysparm_query=f"{ServiceNowFields.KB_KNOWLEDGE_BASE}={kb_sys_id}",
                    sysparm_fields=ServiceNowFields.USER_CRITERIA,
                    sysparm_display_value=ServiceNowQueryValues.DISPLAY_VALUE_FALSE,
                    sysparm_exclude_reference_link=ServiceNowQueryValues.EXCLUDE_REFERENCE_LINK_TRUE,
                )
                
                for record in write_response.result:
                    criteria_id = record.user_criteria
                    if criteria_id:
                        criteria_map[ServiceNowDictKeys.WRITE].append(criteria_id)
            except ServiceNowAPIError as e:
                self.logger.warning(f"Failed to fetch WRITE criteria for KB {kb_sys_id}: {e.message}")

            return criteria_map

        except Exception as e:
            self.logger.error(f"Failed to fetch KB permissions: {e}", exc_info=True)
            return {ServiceNowDictKeys.READ: [], ServiceNowDictKeys.WRITE: []}

    async def _process_criteria_permissions(
        self, criteria_ids: List[str], permission_type: PermissionType, tx_store: TransactionStore
    ) -> List[Permission]:
        """
        Shared method to process user_criteria IDs and extract permissions.

        This method:
        1. Batch fetches all user_criteria details
        2. Extracts permissions from each criteria
        3. Converts to Permission objects

        Args:
            criteria_ids: List of user_criteria sys_ids
            permission_type: Type of permission (READ or WRITE)
            tx_store: Transaction store for database access

        Returns:
            List of Permission objects
        """
        try:
            if not criteria_ids:
                return []

            permission_dicts = []

            # Batch fetch all user_criteria details
            criteria_query = f"{ServiceNowFields.SYS_ID}IN{CommonStrings.COMMA.join(criteria_ids)}"
            datasource = await self._get_fresh_datasource()
            try:
                criteria_response = await datasource.get_now_table_tableName(
                    tableName=ServiceNowTables.USER_CRITERIA,
                    sysparm_query=criteria_query,
                    sysparm_fields=CommonStrings.COMMA.join(
                        [
                            ServiceNowFields.SYS_ID,
                            ServiceNowFields.USER,
                            ServiceNowFields.GROUP,
                            ServiceNowFields.ROLE,
                            ServiceNowFields.DEPARTMENT,
                            ServiceNowFields.LOCATION,
                            ServiceNowFields.COMPANY,
                            ServiceNowFields.COST_CENTER,
                        ]
                    ),
                    sysparm_display_value=ServiceNowQueryValues.DISPLAY_VALUE_FALSE,
                    sysparm_exclude_reference_link=ServiceNowQueryValues.EXCLUDE_REFERENCE_LINK_TRUE,
                )
                
                for criteria_record in criteria_response.result:
                    # Extract permissions from this criteria
                    perms = await self._extract_permissions_from_user_criteria_details(
                        criteria_record,
                        permission_type
                    )
                    permission_dicts.extend(perms)
            except ServiceNowAPIError as e:
                self.logger.warning(f"Failed to fetch user criteria details: {e.message}")

            # Convert to Permission objects
            permission_objects = await self._convert_permissions_to_objects(
                permission_dicts,
                tx_store
            )

            return permission_objects

        except Exception as e:
            self.logger.error(f"Failed to process criteria permissions: {e}", exc_info=True)
            return []

    async def _extract_permissions_from_user_criteria_details(
        self, criteria_details: TableAPIRecord, permission_type: PermissionType
    ) -> List[RawPermission]:
        """
        Extract all permissions from a user_criteria record.

        User criteria can contain:
        - user: Individual user sys_id (comma-separated if multiple)
        - group: User group sys_id (comma-separated if multiple)
        - role: Role name (comma-separated if multiple, needs lookup)
        - department: Department sys_id (comma-separated if multiple)
        - location: Location sys_id (comma-separated if multiple)
        - company: Company sys_id (comma-separated if multiple)

        Args:
            criteria_details: user_criteria TableAPIRecord from ServiceNow
            permission_type: READER or WRITER

        Returns:
            List of RawPermission Pydantic models
        """
        permissions = []

        try:
            # Helper function to parse comma-separated sys_ids
            def parse_sys_ids(field_value: Optional[str]) -> List[str]:
                """Parse comma-separated sys_ids from field value."""
                if not field_value:
                    return []
                return [s.strip() for s in field_value.split(",") if s.strip()]

            # 1. Extract USER permissions
            user_sys_ids = parse_sys_ids(getattr(criteria_details, ServiceNowFields.USER, None))
            for user_sys_id in user_sys_ids:
                permissions.append(RawPermission(
                    entity_type="USER",
                    source_sys_id=user_sys_id,
                    role=permission_type.value,
                ))

            # 2. Extract GROUP permissions
            group_sys_ids = parse_sys_ids(getattr(criteria_details, ServiceNowFields.GROUP, None))
            for group_sys_id in group_sys_ids:
                permissions.append(RawPermission(
                    entity_type="GROUP",
                    source_sys_id=group_sys_id,
                    role=permission_type.value,
                ))

            # 3. Extract ROLE permissions (role names need lookup)
            role_sys_ids = parse_sys_ids(getattr(criteria_details, ServiceNowFields.ROLE, None))
            for role_sys_id in role_sys_ids:
                permissions.append(RawPermission(
                    entity_type="GROUP",  # Roles are stored as groups
                    source_sys_id=role_sys_id,
                    role=permission_type.value,
                ))

            # 4. Extract DEPARTMENT permissions (organizational entity)
            department_sys_ids = parse_sys_ids(getattr(criteria_details, ServiceNowFields.DEPARTMENT, None))
            for dept_sys_id in department_sys_ids:
                permissions.append(RawPermission(
                    entity_type="GROUP",  # Org entities stored as groups
                    source_sys_id=dept_sys_id,
                    role=permission_type.value,
                ))

            # 5. Extract LOCATION permissions (organizational entity)
            location_sys_ids = parse_sys_ids(getattr(criteria_details, ServiceNowFields.LOCATION, None))
            for loc_sys_id in location_sys_ids:
                permissions.append(RawPermission(
                    entity_type="GROUP",  # Org entities stored as groups
                    source_sys_id=loc_sys_id,
                    role=permission_type.value,
                ))

            # 6. Extract COMPANY permissions (organizational entity)
            company_sys_ids = parse_sys_ids(getattr(criteria_details, ServiceNowFields.COMPANY, None))
            for company_sys_id in company_sys_ids:
                permissions.append(RawPermission(
                    entity_type="GROUP",  # Org entities stored as groups
                    source_sys_id=company_sys_id,
                    role=permission_type.value,
                ))

        except Exception as e:
            self.logger.error(
                f"Error extracting permissions from user_criteria: {e}",
                exc_info=True
            )
        return permissions

    async def _transform_to_app_user(
        self, user_data: TableAPIRecord
    ) -> Optional[AppUser]:
        """
        Transform ServiceNow user to AppUser entity.

        Args:
            user_data: ServiceNow sys_user TableAPIRecord

        Returns:
            AppUser: Transformed user entity or None if invalid
        """
        try:
            sys_id = user_data.sys_id
            email = (user_data.email or "").strip()
            user_name = user_data.user_name or ""
            first_name = user_data.first_name or ""
            last_name = user_data.last_name or ""

            if not sys_id or not email:
                return None

            # Build full name
            full_name = f"{first_name} {last_name}".strip()
            if not full_name:
                full_name = user_name or email

            # Parse timestamps
            source_created_at = None
            source_updated_at = None
            if user_data.sys_created_on:
                source_created_at = datetime_to_epoch_ms(user_data.sys_created_on, ServiceNowDefaults.DATETIME_FORMAT)
            if user_data.sys_updated_on:
                source_updated_at = datetime_to_epoch_ms(user_data.sys_updated_on, ServiceNowDefaults.DATETIME_FORMAT)

            app_user = AppUser(
                app_name=self.connector_name,
                connector_id=self.connector_id,
                source_user_id=sys_id,
                org_id=self.data_entities_processor.org_id,
                email=email,
                full_name=full_name,
                is_active=False,
                source_created_at=source_created_at,
                source_updated_at=source_updated_at,
            )

            return app_user

        except Exception as e:
            self.logger.error(f"Error transforming user {getattr(user_data, 'sys_id', 'unknown')}: {e}", exc_info=True)
            return None

    def _transform_to_user_group(
        self, group_data: SysUserGroup
    ) -> Optional[AppUserGroup]:
        """
        Transform ServiceNow group to AppUserGroup entity.

        Args:
            group_data: ServiceNow SysUserGroup Pydantic model

        Returns:
            AppUserGroup: Transformed user group entity or None if invalid
        """
        try:
            sys_id = group_data.sys_id
            name = group_data.name or ""

            if not sys_id or not name:
                return None

            # Parse timestamps
            source_created_at = None
            source_updated_at = None
            if group_data.sys_created_on:
                source_created_at = datetime_to_epoch_ms(group_data.sys_created_on, ServiceNowDefaults.DATETIME_FORMAT)
            if group_data.sys_updated_on:
                source_updated_at = datetime_to_epoch_ms(group_data.sys_updated_on, ServiceNowDefaults.DATETIME_FORMAT)

            # Create AppUserGroup (for user groups, not record groups)
            user_group = AppUserGroup(
                app_name=Connectors.SERVICENOW,
                connector_id=self.connector_id,
                source_user_group_id=sys_id,
                name=name,
                org_id=self.data_entities_processor.org_id,
                source_created_at=source_created_at,
                source_updated_at=source_updated_at,
            )

            return user_group

        except Exception as e:
            self.logger.error(f"Error transforming group {getattr(group_data, 'sys_id', 'unknown')}: {e}", exc_info=True)
            return None

    def _transform_to_organizational_group(
        self, entity_data: TableAPIRecord, prefix: str
    ) -> Optional[AppUserGroup]:
        """
        Transform ServiceNow organizational entity to AppUserGroup.

        This is a generic transform method for companies, departments, locations, and cost centers.

        Args:
            entity_data: ServiceNow entity TableAPIRecord (company, department, location, cost_center)
            prefix: Name prefix (COMPANY_, DEPARTMENT_, LOCATION_, COSTCENTER_)

        Returns:
            AppUserGroup: Transformed organizational group or None if invalid
        """
        try:
            sys_id = entity_data.sys_id
            name = entity_data.name or ""

            if not sys_id or not name:
                return None

            # Parse timestamps
            source_created_at = None
            source_updated_at = None
            if entity_data.sys_created_on:
                source_created_at = datetime_to_epoch_ms(
                    entity_data.sys_created_on, ServiceNowDefaults.DATETIME_FORMAT
                )
            if entity_data.sys_updated_on:
                source_updated_at = datetime_to_epoch_ms(
                    entity_data.sys_updated_on, ServiceNowDefaults.DATETIME_FORMAT
                )

            # Create AppUserGroup with prefix
            org_group = AppUserGroup(
                app_name=Connectors.SERVICENOW,
                connector_id=self.connector_id,
                source_user_group_id=sys_id,
                name=f"{prefix}{name}",
                description=f"ServiceNow {prefix.rstrip('_')}: {name}",
                org_id=self.data_entities_processor.org_id,
                source_created_at=source_created_at,
                source_updated_at=source_updated_at,
            )

            return org_group

        except Exception as e:
            self.logger.error(
                f"Error transforming organizational entity {getattr(entity_data, 'sys_id', 'unknown')}: {e}",
                exc_info=True,
            )
            return None

    def _transform_to_kb_record_group(
        self, kb_data: TableAPIRecord
    ) -> Optional[RecordGroup]:
        """
        Transform ServiceNow knowledge base to RecordGroup entity.

        Args:
            kb_data: ServiceNow kb_knowledge_base TableAPIRecord

        Returns:
            RecordGroup: Transformed KB as RecordGroup with type SERVICENOW or None if invalid
        """
        try:
            sys_id = kb_data.sys_id
            title = kb_data.title or ""

            if not sys_id or not title:
                self.logger.warning(f"KB missing sys_id or title: {kb_data}")
                return None

            # Parse timestamps
            source_created_at = None
            source_updated_at = None
            if kb_data.sys_created_on:
                source_created_at = datetime_to_epoch_ms(kb_data.sys_created_on, ServiceNowDefaults.DATETIME_FORMAT)
            if kb_data.sys_updated_on:
                source_updated_at = datetime_to_epoch_ms(kb_data.sys_updated_on, ServiceNowDefaults.DATETIME_FORMAT)

            # Construct web URL
            web_url = None
            if self.instance_url:
                web_url = ServiceNowURLPatterns.KB_BASE.format(instance_url=self.instance_url, sys_id=sys_id)

            # Create RecordGroup for Knowledge Base
            kb_record_group = RecordGroup(
                org_id=self.data_entities_processor.org_id,
                name=title,
                description=kb_data.description or "",
                external_group_id=sys_id,
                connector_name=Connectors.SERVICENOW,
                connector_id=self.connector_id,
                group_type=RecordGroupType.SERVICENOWKB,
                web_url=web_url,
                source_created_at=source_created_at,
                source_updated_at=source_updated_at,
            )

            return kb_record_group

        except Exception as e:
            self.logger.error(f"Error transforming KB {getattr(kb_data, 'sys_id', 'unknown')}: {e}", exc_info=True)
            return None

    def _transform_to_category_record_group(
        self, category_data: TableAPIRecord
    ) -> Optional[RecordGroup]:
        """
        Transform ServiceNow kb_category to RecordGroup entity.

        Args:
            category_data: ServiceNow kb_category TableAPIRecord

        Returns:
            RecordGroup: Transformed category as RecordGroup with type SERVICENOW_CATEGORY or None if invalid
        """
        try:
            sys_id = category_data.sys_id
            label = category_data.label or ""
            parent_sys_id = category_data.parent_id

            if not sys_id or not label:
                self.logger.warning(f"Category missing sys_id or label: {category_data}")
                return None

            # Parse timestamps
            source_created_at = None
            source_updated_at = None
            if category_data.sys_created_on:
                source_created_at = datetime_to_epoch_ms(category_data.sys_created_on, ServiceNowDefaults.DATETIME_FORMAT)
            if category_data.sys_updated_on:
                source_updated_at = datetime_to_epoch_ms(category_data.sys_updated_on, ServiceNowDefaults.DATETIME_FORMAT)

            # Construct web URL
            web_url = None
            if self.instance_url:
                web_url = ServiceNowURLPatterns.KB_CATEGORY.format(instance_url=self.instance_url, sys_id=sys_id)

            # Create RecordGroup for Category
            category_record_group = RecordGroup(
                org_id=self.data_entities_processor.org_id,
                name=label,
                short_name=category_data.value or "",
                parent_external_group_id=parent_sys_id,
                external_group_id=sys_id,
                connector_name=Connectors.SERVICENOW,
                connector_id=self.connector_id,
                group_type=RecordGroupType.SERVICENOW_CATEGORY,
                web_url=web_url,
                source_created_at=source_created_at,
                source_updated_at=source_updated_at,
                inherit_permissions=True,
            )

            return category_record_group

        except Exception as e:
            self.logger.error(f"Error transforming category {getattr(category_data, 'sys_id', 'unknown')}: {e}", exc_info=True)
            return None

    def _transform_to_article_webpage_record(
        self, article_data: TableAPIRecord
    ) -> Optional[WebpageRecord]:
        """
        Transform ServiceNow kb_knowledge article to WebpageRecord entity.

        Args:
            article_data: ServiceNow kb_knowledge TableAPIRecord

        Returns:
            WebpageRecord: Transformed article or None if invalid
        """
        try:
            sys_id = article_data.sys_id
            short_description = article_data.short_description or ""

            if not sys_id or not short_description:
                self.logger.warning(f"Article missing sys_id or short_description: {article_data}")
                return None

            # Parse timestamps
            source_created_at = None
            source_updated_at = None
            if article_data.sys_created_on:
                source_created_at = datetime_to_epoch_ms(article_data.sys_created_on, ServiceNowDefaults.DATETIME_FORMAT)
            if article_data.sys_updated_on:
                source_updated_at = datetime_to_epoch_ms(article_data.sys_updated_on, ServiceNowDefaults.DATETIME_FORMAT)

            # Construct web URL
            web_url = None
            if self.instance_url:
                web_url = ServiceNowURLPatterns.KB_ARTICLE.format(instance_url=self.instance_url, sys_id=sys_id)

            # Extract category sys_id for external_record_group_id
            # Fallback to KB if category is empty/missing
            kb_category_sys_id = article_data.kb_category
            external_record_group_id = None
            record_group_type = None

            # Try category first
            if kb_category_sys_id:
                external_record_group_id = kb_category_sys_id
                record_group_type = RecordGroupType.SERVICENOW_CATEGORY
            else:
                # Fallback to KB if no category
                kb_sys_id = article_data.kb_knowledge_base
                if kb_sys_id:
                    external_record_group_id = kb_sys_id
                    record_group_type = RecordGroupType.SERVICENOWKB
                else:
                    # No category and no KB - skip this article
                    self.logger.warning(f"Article {sys_id} has no category and no KB - skipping")
                    return None

            # Create WebpageRecord for Article
            record_id = str(uuid.uuid4())
            article_record = WebpageRecord(
                id=record_id,
                external_record_id=sys_id,
                version=0,
                record_name=short_description,
                record_type=RecordType.WEBPAGE,
                origin=OriginTypes.CONNECTOR,
                connector_name=Connectors.SERVICENOW,
                connector_id=self.connector_id,
                record_group_type=record_group_type,  # CATEGORY or KB
                external_record_group_id=external_record_group_id,  # Category or KB sys_id
                parent_external_record_id=None,
                weburl=web_url,
                mime_type=MimeTypes.HTML.value,
                source_created_at=source_created_at,
                source_updated_at=source_updated_at,
            )
            return article_record

        except Exception as e:
            self.logger.error(f"Error transforming article {getattr(article_data, 'sys_id', 'unknown')}: {e}", exc_info=True)
            return None

    def _transform_to_attachment_file_record(
        self,
        attachment_data: AttachmentMetadata,
        parent_record_group_type: Optional[RecordGroupType] = None,
        parent_external_record_group_id: Optional[str] = None,
    ) -> Optional[FileRecord]:
        """
        Transform ServiceNow sys_attachment to FileRecord entity.

        Args:
            attachment_data: ServiceNow sys_attachment AttachmentMetadata model
            parent_record_group_type: The record group type from parent article (CATEGORY or KB)
            parent_external_record_group_id: The external record group ID from parent article

        Returns:
            FileRecord: Transformed attachment or None if invalid
        """
        try:
            sys_id = attachment_data.sys_id
            file_name = attachment_data.file_name

            if not sys_id or not file_name:
                self.logger.warning(f"Attachment missing sys_id or file_name: {attachment_data}")
                return None

            # Parse timestamps
            source_created_at = None
            source_updated_at = None
            if attachment_data.sys_created_on:
                source_created_at = datetime_to_epoch_ms(attachment_data.sys_created_on, ServiceNowDefaults.DATETIME_FORMAT)
            if attachment_data.sys_updated_on:
                source_updated_at = datetime_to_epoch_ms(attachment_data.sys_updated_on, ServiceNowDefaults.DATETIME_FORMAT)

            # Construct web URL
            web_url = None
            if self.instance_url:
                web_url = ServiceNowURLPatterns.ATTACHMENT.format(instance_url=self.instance_url, sys_id=sys_id)

            # Parse content type for mime type
            content_type = attachment_data.content_type or ServiceNowDefaults.DEFAULT_MIME_TYPE
            mime_type = None
            # Map to MimeTypes enum if possible
            for mime in MimeTypes:
                if mime.value == content_type:
                    mime_type = mime
                    break

            # Parse file size
            file_size = None
            size_bytes = attachment_data.size_bytes
            if size_bytes:
                try:
                    file_size = int(size_bytes)
                except (ValueError, TypeError):
                    pass

            # Create FileRecord for Attachment
            attachment_record_id = str(uuid.uuid4())
            attachment_record = FileRecord(
                id=attachment_record_id,
                org_id=self.data_entities_processor.org_id,
                record_name=file_name,
                record_type=RecordType.FILE,
                external_record_id=sys_id,
                version=0,
                origin=OriginTypes.CONNECTOR,
                connector_name=Connectors.SERVICENOW,
                connector_id=self.connector_id,
                mime_type=mime_type,
                parent_external_record_id=attachment_data.table_sys_id,
                parent_record_type=RecordType.WEBPAGE,
                record_group_type=parent_record_group_type,
                external_record_group_id=parent_external_record_group_id,
                weburl=web_url,
                is_file=True,
                size_in_bytes=file_size,
                source_created_at=source_created_at,
                source_updated_at=source_updated_at,
                extension=file_name.split(".")[-1] if "." in file_name else None,
            )

            return attachment_record

        except Exception as e:
            self.logger.error(f"Error transforming attachment {getattr(attachment_data, 'sys_id', 'unknown')}: {e}", exc_info=True)
            return None



    @classmethod
    async def create_connector(
        cls,
        logger: Logger,
        data_store_provider: DataStoreProvider,
        config_service: ConfigurationService,
        connector_id: str,
        scope: str,
        created_by: str,
        **kwargs,
    ) -> "ServiceNowConnector":
        """
        Factory method to create and initialize the connector.

        Args:
            logger: Logger instance
            data_store_provider: Data store provider
            config_service: Configuration service

        Returns:
            ServiceNowConnector: Initialized connector instance
        """
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
            scope,
            created_by,
        )
