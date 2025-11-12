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
from typing import Any, AsyncGenerator, Dict, List, Optional, Tuple

from fastapi import HTTPException
from fastapi.responses import StreamingResponse

from app.config.configuration_service import ConfigurationService
from app.config.constants.arangodb import Connectors, MimeTypes, OriginTypes
from app.connectors.core.base.connector.connector_service import BaseConnector
from app.connectors.core.base.data_processor.data_source_entities_processor import (
    DataSourceEntitiesProcessor,
)
from app.connectors.core.base.data_store.data_store import (
    DataStoreProvider,
    TransactionStore,
)
from app.connectors.core.base.sync_point.sync_point import SyncDataPointType, SyncPoint
from app.connectors.core.registry.connector_builder import (
    AuthField,
    CommonFields,
    ConnectorBuilder,
    DocumentationLink,
)
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

# Organizational entity configuration
ORGANIZATIONAL_ENTITIES = {
    "company": {
        "table": "core_company",
        "fields": "sys_id,name,parent,sys_created_on,sys_updated_on",
        "prefix": "COMPANY_",
        "sync_point_key": "companies",
    },
    "department": {
        "table": "cmn_department",
        "fields": "sys_id,name,parent,company,sys_created_on,sys_updated_on",
        "prefix": "DEPARTMENT_",
        "sync_point_key": "departments",
    },
    "location": {
        "table": "cmn_location",
        "fields": "sys_id,name,parent,company,sys_created_on,sys_updated_on",
        "prefix": "LOCATION_",
        "sync_point_key": "locations",
    },
    "cost_center": {
        "table": "cmn_cost_center",
        "fields": "sys_id,name,parent,sys_created_on,sys_updated_on",
        "prefix": "COSTCENTER_",
        "sync_point_key": "cost_centers",
    },
}


@ConnectorBuilder("ServiceNow")\
    .in_group("ServiceNow")\
    .with_auth_type("OAUTH")\
    .with_description("Sync knowledge base articles, categories, and permissions from ServiceNow")\
    .with_categories(["Knowledge Management"])\
    .configure(lambda builder: builder
        .with_icon("/assets/icons/connectors/servicenow.svg")
        .with_realtime_support(False)
        .add_documentation_link(
            DocumentationLink(
                "ServiceNow OAuth Setup",
                "https://docs.servicenow.com/bundle/latest/page/administer/security/concept/c_OAuthApplications.html",
                "setup"
            )
        )
        .with_redirect_uri("connectors/oauth/callback/ServiceNow", True)
        .with_oauth_urls(
            "https://example.service-now.com/oauth_auth.do",
            "https://example.service-now.com/oauth_token.do",
            ["useraccount"]
        )
        .add_auth_field(
            AuthField(
                name="instanceUrl",
                display_name="ServiceNow Instance URL",
                placeholder="https://your-instance.service-now.com",
                description="Your ServiceNow instance URL (e.g., https://dev12345.service-now.com)",
                field_type="URL",
                required=True,
                max_length=2000,
            )
        )
        .add_auth_field(
            AuthField(
                name="authorizeUrl",
                display_name="ServiceNow Authorize URL",
                placeholder="https://your-instance.service-now.com/oauth_auth.do",
                description="Your ServiceNow authorize URL (e.g., https://dev12345.service-now.com/oauth_auth.do)",
                field_type="URL",
                required=True,
                max_length=2000,
            )
        )
        .add_auth_field(
            AuthField(
                name="tokenUrl",
                display_name="ServiceNow Token URL",
                placeholder="https://your-instance.service-now.com/oauth_token.do",
                description="Your ServiceNow token URL (e.g., https://dev12345.service-now.com/oauth_token.do)",
                field_type="URL",
                required=True,
                max_length=2000,
            )
        )
        .add_auth_field(CommonFields.client_id("ServiceNow OAuth Application Registry"))
        .add_auth_field(CommonFields.client_secret("ServiceNow OAuth Application Registry"))
        .with_sync_strategies(["SCHEDULED", "MANUAL"])
        .with_scheduled_config(True, 60)
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
            ServicenowApp(),
            logger,
            data_entities_processor,
            data_store_provider,
            config_service,
        )

        # ServiceNow API client instances
        self.servicenow_client: Optional[ServiceNowRESTClientViaOAuthAuthorizationCode] = None
        self.servicenow_datasource: Optional[ServiceNowDataSource] = None

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
                connector_name=self.connector_name,
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

            # Load configuration
            config = await self.config_service.get_config(
                "/services/connectors/servicenow/config"
            )

            if not config:
                self.logger.error("❌ ServiceNow configuration not found")
                return False

            # Extract OAuth configuration
            auth_config = config.get("auth", {})
            self.instance_url = auth_config.get("instanceUrl")
            self.client_id = auth_config.get("clientId")
            self.client_secret = auth_config.get("clientSecret")
            self.redirect_uri = auth_config.get("redirectUri")

            # OAuth tokens (stored after authorization flow completes)
            credentials = config.get("credentials", {})
            self.access_token = credentials.get("access_token")
            self.refresh_token = credentials.get("refresh_token")

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

        # Fetch current token from config (async I/O)
        config = await self.config_service.get_config("/services/connectors/servicenow/config")

        if not config:
            raise Exception("ServiceNow configuration not found")

        credentials = config.get("credentials") or {}
        fresh_token = credentials.get("access_token")

        if not fresh_token:
            raise Exception("No access token available")

        # Update client's token if it changed (mutation)
        if self.servicenow_client.access_token != fresh_token:
            self.logger.debug("🔄 Updating client with refreshed access token")
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
            response = await datasource.get_now_table_tableName(
                tableName="kb_knowledge_base",
                sysparm_limit="1",
                sysparm_fields="sys_id,title"
            )

            if not response.success:
                self.logger.error(f"❌ Connection test failed: {response.error}")
                return False

            self.logger.info("✅ OAuth connection test successful")
            return True

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

                # Use stored mime type or default
                media_type = record.mime_type or 'application/octet-stream'
                filename = record.record_name or f"{record.external_record_id}"

                return StreamingResponse(
                    generate_attachment(),
                    media_type=media_type,
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'}
                )

            else:
                raise HTTPException(
                    status_code=400,
                    detail=f"Unsupported record type for streaming: {record.record_type}"
                )

        except HTTPException:
            raise  # Re-raise HTTP exceptions as-is
        except Exception as e:
            self.logger.error(f"❌ Failed to stream record: {e}", exc_info=True)
            raise HTTPException(
                status_code=500, detail=f"Failed to stream record: {str(e)}"
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
            self.logger.debug(f"Fetching article content for {article_sys_id}")

            # Fetch article using ServiceNow Table API
            datasource = await self._get_fresh_datasource()
            response = await datasource.get_now_table_tableName(
                tableName="kb_knowledge",
                sysparm_query=f"sys_id={article_sys_id}",
                sysparm_fields="sys_id,short_description,text,number",
                sysparm_limit="1",
                sysparm_display_value="false",
                sysparm_no_count="true",
                sysparm_exclude_reference_link="true"
            )

            # Check response using correct attributes
            if not response or not response.success or not response.data:
                raise HTTPException(
                    status_code=404,
                    detail=f"Article not found: {article_sys_id}"
                )

            # Extract article from result array
            articles = response.data.get("result", [])
            if not articles or len(articles) == 0:
                raise HTTPException(
                    status_code=404,
                    detail=f"Article not found: {article_sys_id}"
                )

            article = articles[0]

            # Get raw HTML content from text field
            html_content = article.get("text", "")

            if not html_content:
                # If no content, return empty HTML
                self.logger.warning(f"Article {article_sys_id} has no content")
                html_content = "<p>No content available</p>"

            self.logger.debug(f"✅ Fetched {len(html_content)} bytes of HTML for article {article_sys_id}")
            return html_content

        except HTTPException:
            raise  # Re-raise HTTP exceptions
        except Exception as e:
            self.logger.error(f"Failed to fetch article content: {e}", exc_info=True)
            raise HTTPException(
                status_code=500,
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
            self.logger.debug(f"Downloading attachment {attachment_sys_id}")

            # Download using REST client (returns bytes directly)
            datasource = await self._get_fresh_datasource()
            file_content = await datasource.download_attachment(attachment_sys_id)

            if not file_content:
                raise HTTPException(
                    status_code=404,
                    detail=f"Attachment not found or empty: {attachment_sys_id}"
                )

            self.logger.debug(f"✅ Downloaded {len(file_content)} bytes for attachment {attachment_sys_id}")
            return file_content

        except HTTPException:
            raise  # Re-raise HTTP exceptions
        except Exception as e:
            self.logger.error(f"Failed to download attachment: {e}", exc_info=True)
            raise HTTPException(
                status_code=500,
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

    def cleanup(self) -> None:
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
            response = await datasource.get_now_table_tableName(
                tableName="sys_user_has_role",
                sysparm_query="role.name=admin^user.active=true",
                sysparm_fields="user,user.name,user.sys_id,user.email",
                sysparm_display_value="false",
                sysparm_exclude_reference_link="true",
                sysparm_no_count="true",
            )

            if not response.success or not response.data:
                self.logger.warning("Failed to fetch admin users from ServiceNow")
                return []

            role_assignments = response.data.get("result", [])
            self.logger.info(f"Found {len(role_assignments)} admin role assignments")

            # Extract unique user sys_ids
            admin_sys_ids = set()
            for assignment in role_assignments:
                    user_ref = assignment.get("user")

                    # Handle both reference dict and direct string value
                    user_sys_id = None
                    if isinstance(user_ref, dict):
                        user_sys_id = user_ref.get("value")
                    elif isinstance(user_ref, str) and user_ref:
                        user_sys_id = user_ref

                    if user_sys_id:
                        admin_sys_ids.add(user_sys_id)

            self.logger.info(f"Found {len(admin_sys_ids)} unique admin users")

            # Match with platform users using source_user_id
            async with self.data_store_provider.transaction() as tx_store:
                for sys_id in admin_sys_ids:
                    try:
                        # Get AppUser by source_user_id (ServiceNow sys_id)
                        app_user = await tx_store.get_user_by_source_id(
                            connector_name=self.connector_name,
                            source_user_id=sys_id
                        )

                        if app_user:
                            admin_users.append(app_user)
                            self.logger.debug(f"✓ Matched admin user: {app_user.email}")
                        else:
                            self.logger.debug(f"✗ No platform user for ServiceNow sys_id: {sys_id}")

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
            last_sync_data = await self.user_sync_point.read_sync_point("users")
            last_sync_time = (last_sync_data.get("last_sync_time") if last_sync_data else None)

            if last_sync_time:
                self.logger.info(f"🔄 Delta sync: fetching users updated after {last_sync_time}")
                query = f"sys_updated_on>{last_sync_time}^ORDERBYsys_updated_on"
            else:
                self.logger.info("🆕 Full sync: fetching all users")
                query = "ORDERBYsys_updated_on"

            # Pagination variables
            batch_size = 100
            offset = 0
            total_synced = 0
            latest_update_time = None

            # Paginate through all users
            while True:
                datasource = await self._get_fresh_datasource()
                response = await datasource.get_now_table_tableName(
                    tableName="sys_user",
                    sysparm_query=query,
                    sysparm_fields="sys_id,user_name,email,first_name,last_name,title,department,company,location,cost_center,active,sys_created_on,sys_updated_on",
                    sysparm_limit=str(batch_size),
                    sysparm_offset=str(offset),
                    sysparm_display_value="false",
                    sysparm_no_count="true",
                    sysparm_exclude_reference_link="true",
                )

                # Check for errors
                if not response.success or not response.data:
                    if response.error:
                        self.logger.error(f"❌ API error: {response.error}")
                    break

                # Extract users from response
                users_data = response.data.get("result", [])

                if not users_data:
                    break

                # Track the latest update timestamp for checkpoint
                if users_data:
                    latest_update_time = users_data[-1].get("sys_updated_on")

                # Transform users (skip users without email)
                app_users = []
                user_org_links = []  # Collect organizational links

                for user_data in users_data:
                    email = user_data.get("email", "").strip()
                    if not email:
                        continue

                    app_user = await self._transform_to_app_user(user_data)
                    if app_user:
                        app_users.append(app_user)

                        # Collect organizational links for this user
                        user_sys_id = user_data.get("sys_id")
                        if user_sys_id:
                            org_fields = {
                                "company": user_data.get("company"),
                                "department": user_data.get("department"),
                                "location": user_data.get("location"),
                                "cost_center": user_data.get("cost_center"),
                            }

                            for org_type, org_ref in org_fields.items():
                                if not org_ref:
                                    continue

                                # Extract sys_id from reference field
                                org_sys_id = None
                                if isinstance(org_ref, dict):
                                    org_sys_id = org_ref.get("value")
                                elif isinstance(org_ref, str) and org_ref:
                                    org_sys_id = org_ref

                                if org_sys_id:
                                    user_org_links.append({
                                        "user_sys_id": user_sys_id,
                                        "org_sys_id": org_sys_id,
                                        "org_type": org_type,
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
                                link["user_sys_id"],
                                link["org_sys_id"],
                                Connectors.SERVICENOW,
                            )

                # Move to next page
                offset += batch_size

                # If this page has fewer records than batch_size, we're done
                if len(users_data) < batch_size:
                    break

            # Save checkpoint for next sync
            if latest_update_time:
                await self.user_sync_point.update_sync_point("users", {"last_sync_time": latest_update_time})

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

            self.logger.info(f"Flattened groups with permissions: {group_with_permissions}")

            # STEP 4: Upsert to database
            if group_with_permissions:
                await self.data_entities_processor.on_new_user_groups(group_with_permissions)

            self.logger.info(f"✅ Processed {len(group_with_permissions)} user groups")

        except Exception as e:
            self.logger.error(f"❌ Error syncing user groups: {e}", exc_info=True)
            raise


    async def _fetch_all_groups(self) -> List[dict]:
        """Fetch all groups from ServiceNow (no delta sync)"""
        try:
            all_groups = []
            batch_size = 100
            offset = 0

            while True:
                datasource = await self._get_fresh_datasource()
                response = await datasource.get_now_table_tableName(
                    tableName="sys_user_group",
                    sysparm_query="ORDERBYsys_updated_on",
                    sysparm_fields="sys_id,name,description,parent,manager,sys_created_on,sys_updated_on",
                    sysparm_limit=str(batch_size),
                    sysparm_offset=str(offset),
                    sysparm_display_value="false",
                    sysparm_no_count="true",
                    sysparm_exclude_reference_link="true",
                )

                if not response.success or not response.data:
                    break

                groups = response.data.get("result", [])
                if not groups:
                    break

                all_groups.extend(groups)
                offset += batch_size

                if len(groups) < batch_size:
                    break

            self.logger.info(f"Fetched {len(all_groups)} groups")
            return all_groups

        except Exception as e:
            self.logger.error(f"❌ Error fetching groups: {e}", exc_info=True)
            raise


    async def _fetch_all_memberships(self) -> List[dict]:
        """Fetch all user-group memberships from ServiceNow"""
        try:
            last_sync_data = await self.group_sync_point.read_sync_point("groups")
            last_sync_time = (last_sync_data.get("last_sync_time") if last_sync_data else None)

            if last_sync_time:
                self.logger.info(f"🔄 Delta sync: fetching user memberships updated after {last_sync_time}")
                query = f"sys_updated_on>{last_sync_time}^ORDERBYsys_updated_on"
            else:
                self.logger.info("🆕 Full sync: fetching all user memberships")
                query = "ORDERBYsys_updated_on"

            all_memberships = []
            batch_size = 100
            offset = 0
            latest_update_time = None

            while True:
                datasource = await self._get_fresh_datasource()
                response = await datasource.get_now_table_tableName(
                    tableName="sys_user_grmember",
                    sysparm_query=query,
                    sysparm_fields="sys_id,user,group,sys_updated_on",
                    sysparm_limit=str(batch_size),
                    sysparm_offset=str(offset),
                    sysparm_display_value="false",
                    sysparm_no_count="true",
                    sysparm_exclude_reference_link="true",
                )

                if not response.success or not response.data:
                    break

                memberships = response.data.get("result", [])
                if not memberships:
                    break

                latest_update_time = memberships[-1].get("sys_updated_on")

                all_memberships.extend(memberships)
                offset += batch_size

                if len(memberships) < batch_size:
                    break

            self.logger.info(f"Fetched {len(all_memberships)} memberships")
            if latest_update_time:
                await self.group_sync_point.update_sync_point("groups", {"last_sync_time": latest_update_time})
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
                parent_ref = hierarchy_record.get('contains', {})
                child_ref = hierarchy_record.get('role', {})

                parent_id = parent_ref.get('value') if isinstance(parent_ref, dict) else parent_ref
                child_id = child_ref.get('value') if isinstance(child_ref, dict) else child_ref

                if parent_id and child_id and child_id not in child_to_parent:
                    child_to_parent[child_id] = parent_id

            # Add parent field to roles
            roles_with_hierarchy = []
            for role in roles_data:
                role_with_parent = role.copy()
                role_id = role.get('sys_id')

                if role_id in child_to_parent:
                    role_with_parent['parent'] = {"value": child_to_parent[role_id]}

                roles_with_hierarchy.append(role_with_parent)

            self.logger.info(
                f"Merged hierarchy: {len(roles_with_hierarchy)} roles, "
                f"{len(child_to_parent)} with parents"
            )

            # Step 5: flatten user roles hierarchy
            roles_with_permissions = await self._flatten_and_create_user_groups(
                roles_with_hierarchy,  # Roles with embedded parent
                role_assignments,      # Role assignments
            )

            # Step 6: Add ROLE_ prefix to names
            for role_group, users in roles_with_permissions:
                if not role_group.name.startswith("ROLE_"):
                    role_group.name = f"ROLE_{role_group.name}"

            # Step 7: Upsert roles as user groups
            if roles_with_permissions:
                await self.data_entities_processor.on_new_user_groups(roles_with_permissions)

            self.logger.info(f"✅ Processed {len(roles_with_permissions)} roles")

        except Exception as e:
            self.logger.error(f"❌ Error syncing roles: {e}", exc_info=True)
            raise


    async def _fetch_all_roles(self) -> List[dict]:
        """Fetch all roles from sys_user_role table."""
        try:
            self.logger.info("Fetching all roles")

            all_roles = []
            batch_size = 100
            offset = 0

            while True:
                datasource = await self._get_fresh_datasource()
                response = await datasource.get_now_table_tableName(
                    tableName="sys_user_role",
                    sysparm_query=None,
                    sysparm_fields="sys_id,name,description,sys_created_on,sys_updated_on",
                    sysparm_limit=str(batch_size),
                    sysparm_offset=str(offset),
                    sysparm_display_value="false",
                    sysparm_no_count="true",
                    sysparm_exclude_reference_link="true",
                )

                if not response.success or not response.data:
                    break

                roles = response.data.get("result", [])
                if not roles:
                    break

                all_roles.extend(roles)

                offset += batch_size
                if len(roles) < batch_size:
                    break

            self.logger.info(f"Fetched {len(all_roles)} roles")
            return all_roles

        except Exception as e:
            self.logger.error(f"❌ Error fetching roles: {e}", exc_info=True)
            raise


    async def _fetch_all_role_assignments(self) -> List[dict]:
        """
        Fetch all user-role assignments from sys_user_has_role table.

        Transforms role assignments to look like group memberships by renaming
        'role' field to 'group' so the same flatten function can be used.
        """
        try:
            last_sync_data = await self.role_assignment_sync_point.read_sync_point("role_assignments")
            last_sync_time = last_sync_data.get("last_sync_time") if last_sync_data else None

            if last_sync_time:
                self.logger.info(f"🔄 Delta sync: fetching role assignments updated after {last_sync_time}")
                query = f"state=active^sys_updated_on>{last_sync_time}^ORDERBYsys_updated_on"
            else:
                self.logger.info("🆕 Full sync: fetching all active role assignments")
                query = "state=active^ORDERBYsys_updated_on"

            all_assignments = []
            batch_size = 100
            offset = 0
            latest_update_time = None

            while True:
                datasource = await self._get_fresh_datasource()
                response = await datasource.get_now_table_tableName(
                    tableName="sys_user_has_role",
                    sysparm_query=query,
                    sysparm_fields="sys_id,user,role,sys_updated_on",
                    sysparm_limit=str(batch_size),
                    sysparm_offset=str(offset),
                    sysparm_display_value="false",
                    sysparm_no_count="true",
                    sysparm_exclude_reference_link="true",
                )

                if not response.success or not response.data:
                    break

                assignments = response.data.get("result", [])
                if not assignments:
                    break

                # Transform role assignments to look like group memberships
                for assignment in assignments:
                    # Rename 'role' field to 'group' so flatten function works
                    transformed = {
                        "sys_id": assignment.get("sys_id"),
                        "user": assignment.get("user"),
                        "group": assignment.get("role"),  # ✅ Rename role → group
                        "sys_updated_on": assignment.get("sys_updated_on")
                    }
                    all_assignments.append(transformed)

                latest_update_time = assignments[-1].get("sys_updated_on")

                offset += batch_size
                if len(assignments) < batch_size:
                    break

            if latest_update_time:
                await self.role_assignment_sync_point.update_sync_point(
                    "role_assignments",
                    {"last_sync_time": latest_update_time}
                )

            self.logger.info(f"Fetched {len(all_assignments)} role assignments")
            return all_assignments

        except Exception as e:
            self.logger.error(f"❌ Error fetching role assignments: {e}", exc_info=True)
            raise


    async def _fetch_role_hierarchy(self) -> List[dict]:
        """
        Fetch role hierarchy from sys_user_role_contains table.

        This is a full sync (no checkpoint) since role hierarchy changes are rare.
        """
        try:
            self.logger.info("Fetching role hierarchy")

            all_hierarchy = []
            batch_size = 100
            offset = 0

            while True:
                datasource = await self._get_fresh_datasource()
                response = await datasource.get_now_table_tableName(
                    tableName="sys_user_role_contains",
                    sysparm_query=None,
                    sysparm_fields="sys_id,contains,role",
                    sysparm_limit=str(batch_size),
                    sysparm_offset=str(offset),
                    sysparm_display_value="false",
                    sysparm_no_count="true",
                    sysparm_exclude_reference_link="true",
                )

                if not response.success or not response.data:
                    break

                hierarchy = response.data.get("result", [])
                if not hierarchy:
                    break

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
        groups_data: List[dict],
        memberships_data: List[dict]
    ) -> List[Tuple[AppUserGroup, List[AppUser]]]:
        """
        Flatten group hierarchy and create AppUserGroup objects.

        Returns:
            List of (AppUserGroup, [AppUser]) tuples
        """
        try:
            # Build parent-child relationships
            children_map = defaultdict(set)  # parent_id -> {child_ids}
            group_by_id = {}  # group_id -> group_data

            for group in groups_data:
                group_id = group['sys_id']
                group_by_id[group_id] = group

                # Extract parent sys_id
                parent_ref = group.get('parent')
                if parent_ref:
                    parent_id = parent_ref.get('value') if isinstance(parent_ref, dict) else parent_ref
                    if parent_id:
                        children_map[parent_id].add(group_id)

            # Build direct user memberships
            direct_users = defaultdict(set)  # group_id -> {user_ids}

            for membership in memberships_data:
                user_ref = membership.get('user', {})
                group_ref = membership.get('group', {})

                user_id = user_ref.get('value') if isinstance(user_ref, dict) else user_ref
                group_id = group_ref.get('value') if isinstance(group_ref, dict) else group_ref

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
                    app_name=Connectors.SERVICENOW
                )

                self.logger.info(f"Loaded {len(existing_app_users)} existing users from DB for lookup: {existing_app_users}")

                # Create lookup map: source_user_id -> AppUser
                user_lookup = {user.source_user_id: user for user in existing_app_users}
                self.logger.info(f"Loaded lookup users: {list(user_lookup.keys())}")

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
                    else:
                        self.logger.debug(f"User {user_id} not found in database")

                self.logger.debug(
                    f"Group {group_data.get('name')} ({group_id}): "
                    f"{len(flattened_user_ids)} total users, {len(app_users)} found in DB"
                )

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
        self, entity_type: str, config: Dict[str, Any]
    ) -> None:
        """
        Generic sync method for a single organizational entity type.

        Uses two-pass approach:
        - Pass 1: Create all entity nodes as AppUserGroups
        - Pass 2: Create hierarchy edges (parent-child) - COMMENTED OUT FOR NOW

        Args:
            entity_type: Type of entity (company, department, location, cost_center)
            config: Configuration dict with table name, fields, prefix, etc.
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
                last_sync_data.get("last_sync_time") if last_sync_data else None
            )

            if last_sync_time:
                self.logger.info(f"🔄 Delta sync: fetching {entity_type} updated after {last_sync_time}")
                query = f"sys_updated_on>{last_sync_time}^ORDERBYsys_updated_on"
            else:
                self.logger.info(f"🆕 Full sync: fetching all {entity_type}")
                query = "ORDERBYsys_updated_on"

            # Pagination variables
            batch_size = 100
            offset = 0
            total_synced = 0
            latest_update_time = None

            # Fetch and create all entity nodes
            while True:
                datasource = await self._get_fresh_datasource()
                response = await datasource.get_now_table_tableName(
                    tableName=table_name,
                    sysparm_query=query,
                    sysparm_fields=fields,
                    sysparm_limit=str(batch_size),
                    sysparm_offset=str(offset),
                    sysparm_display_value="false",
                    sysparm_no_count="true",
                    sysparm_exclude_reference_link="true",
                )

                # Check for errors
                if not response.success or not response.data:
                    if response.error:
                        self.logger.error(f"❌ API error: {response.error}")
                    break

                # Extract entities from response
                entities_data = response.data.get("result", [])

                if not entities_data:
                    break

                # Track latest update timestamp
                if entities_data:
                    latest_update_time = entities_data[-1].get("sys_updated_on")

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
                    sync_point_key, {"last_sync_time": latest_update_time}
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

        Creates:
        - RecordGroup nodes (type=SERVICENOW) in recordGroups collection
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
            last_sync_data = await self.kb_sync_point.read_sync_point("knowledge_bases")
            last_sync_time = (last_sync_data.get("last_sync_time") if last_sync_data else None)

            if last_sync_time:
                self.logger.info(f"🔄 Delta sync: Fetching KBs updated after {last_sync_time}")
                query = f"sys_updated_on>{last_sync_time}^ORDERBYsys_updated_on"
            else:
                self.logger.info("🆕 Full sync: Fetching all knowledge bases")
                query = "ORDERBYsys_updated_on"

            # Pagination variables
            batch_size = 100
            offset = 0
            total_synced = 0
            latest_update_time = None

            # Paginate through all KBs
            while True:
                # Fetch KBs from ServiceNow
                datasource = await self._get_fresh_datasource()
                response = await datasource.get_now_table_tableName(
                    tableName="kb_knowledge_base",
                    sysparm_query=query,
                    sysparm_fields="sys_id,title,description,owner,kb_managers,active,sys_created_on,sys_updated_on",
                    sysparm_limit=str(batch_size),
                    sysparm_offset=str(offset),
                    sysparm_display_value="false",
                    sysparm_no_count="true",
                    sysparm_exclude_reference_link="true",
                )

                # Check for errors
                if not response.success or not response.data:
                    if response.error and "Expecting value" not in response.error:
                        self.logger.error(f"❌ API error: {response.error}")
                    break

                # Extract KBs from response
                kbs_data = response.data.get("result", [])

                if not kbs_data:
                    self.logger.info("✅ No more knowledge bases to fetch")
                    break

                # Track the latest update timestamp for checkpoint
                if kbs_data:
                    latest_update_time = kbs_data[-1].get("sys_updated_on")

                # Transform to RecordGroup entities
                kb_record_groups = []
                for kb_data in kbs_data:
                    kb_record_group = self._transform_to_kb_record_group(kb_data)
                    if kb_record_group:
                        kb_record_groups.append((kb_record_group, kb_data))

                # Save KBs and create permission edges in transaction
                if kb_record_groups:
                    async with self.data_store_provider.transaction() as tx_store:
                        for kb_record_group, kb_data in kb_record_groups:
                            kb_sys_id = kb_data['sys_id']

                            existing_kb = await tx_store.get_record_group_by_external_id(
                                connector_name=Connectors.SERVICENOW,
                                external_id=kb_sys_id
                            )

                            # Save KB RecordGroup
                            if not existing_kb:
                                await tx_store.batch_upsert_record_groups([kb_record_group])

                            # Fetch criteria IDs for this KB
                            criteria_map = await self._fetch_kb_permissions_from_criteria(kb_sys_id)

                            # Process READ permissions using shared method
                            read_permissions = await self._process_criteria_permissions(
                                criteria_map["read"],
                                PermissionType.READ,
                                tx_store
                            )

                            # Process WRITE permissions using shared method
                            write_permissions = await self._process_criteria_permissions(
                                criteria_map["write"],
                                PermissionType.WRITE,
                                tx_store
                            )

                            # Combine all permissions
                            permission_objects = read_permissions + write_permissions

                            # Add OWNER permission (fallback from owner field)
                            owner_ref = kb_data.get("owner")
                            if owner_ref:
                                owner_sys_id = None
                                if isinstance(owner_ref, dict):
                                    owner_sys_id = owner_ref.get("value")
                                elif isinstance(owner_ref, str) and owner_ref:
                                    owner_sys_id = owner_ref

                                if owner_sys_id:
                                    owner_perms = await self._convert_permissions_to_objects(
                                        [{
                                            "entity_type": EntityType.USER.value,
                                            "source_sys_id": owner_sys_id,
                                            "role": PermissionType.OWNER.value,
                                        }],
                                        tx_store
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

                            if permission_objects:
                                await tx_store.batch_upsert_record_group_permissions(
                                    kb_record_group.id,
                                    permission_objects,
                                    Connectors.SERVICENOW
                                )

                                self.logger.debug(f"Created KB {kb_sys_id} with {len(permission_objects)} permissions")

                    total_synced += len(kb_record_groups)

                # Move to next page
                offset += batch_size

                # If this page has fewer records than batch_size, we're done
                if len(kbs_data) < batch_size:
                    break

            # Save checkpoint for next sync
            if latest_update_time:
                await self.kb_sync_point.update_sync_point("knowledge_bases", {"last_sync_time": latest_update_time})

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
            last_sync_data = await self.category_sync_point.read_sync_point("categories")
            last_sync_time = (last_sync_data.get("last_sync_time") if last_sync_data else None)

            if last_sync_time:
                self.logger.info(f"🔄 Delta sync: Fetching categories updated after {last_sync_time}")
                query = f"sys_updated_on>{last_sync_time}^ORDERBYsys_updated_on"
            else:
                self.logger.info("🆕 Full sync: Fetching all categories")
                query = "ORDERBYsys_updated_on"

            # Pagination variables
            batch_size = 100
            offset = 0
            total_synced = 0
            latest_update_time = None

            # Paginate through all categories
            while True:
                # Fetch categories from ServiceNow
                datasource = await self._get_fresh_datasource()
                response = await datasource.get_now_table_tableName(
                    tableName="kb_category",
                    sysparm_query=query,
                    sysparm_fields="sys_id,label,value,parent_table,parent_id,kb_knowledge_base,active,sys_created_on,sys_updated_on",
                    sysparm_limit=str(batch_size),
                    sysparm_offset=str(offset),
                    sysparm_display_value="false",
                    sysparm_no_count="true",
                    sysparm_exclude_reference_link="true",
                )

                # Check for errors
                if not response.success or not response.data:
                    if response.error:
                        self.logger.error(f"❌ API error: {response.error}")
                    break

                # Extract categories from response
                categories_data = response.data.get("result", [])

                if not categories_data:
                    self.logger.debug(f"No more categories at offset {offset}")
                    break

                # Track the latest update timestamp for checkpoint
                if categories_data:
                    latest_update_time = categories_data[-1].get("sys_updated_on")

                # Transform categories to RecordGroups with hierarchy information
                categories_with_permissions = []
                for cat_data in categories_data:
                    category_rg = self._transform_to_category_record_group(cat_data)
                    if not category_rg:
                        continue

                    # Set parent information for hierarchy edge creation
                    parent_table = cat_data.get("parent_table")
                    parent_id_ref = cat_data.get("parent_id")

                    # Extract parent sys_id from reference field
                    parent_sys_id = None
                    if isinstance(parent_id_ref, dict):
                        parent_sys_id = parent_id_ref.get("value")
                    elif isinstance(parent_id_ref, str) and parent_id_ref:
                        parent_sys_id = parent_id_ref

                    # Set parent_record_group_id if parent exists
                    if parent_sys_id and parent_table:
                        category_rg.parent_record_group_id = parent_sys_id


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
                await self.category_sync_point.update_sync_point("categories", {"last_sync_time": latest_update_time})

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
            last_sync_data = await self.article_sync_point.read_sync_point("articles")
            last_sync_time = (last_sync_data.get("last_sync_time") if last_sync_data else None)

            if last_sync_time:
                self.logger.info(f"🔄 Delta sync: Fetching articles updated after {last_sync_time}")
                query = f"active=true^workflow_state=published^sys_updated_on>{last_sync_time}^ORDERBYsys_updated_on"
            else:
                self.logger.info("🆕 Full sync: Fetching all articles")
                query = "active=true^workflow_state=published^ORDERBYsys_updated_on"

            # Pagination variables
            batch_size = 100
            offset = 0
            total_articles_synced = 0
            total_attachments_synced = 0
            latest_update_time = None

            # Paginate through all articles
            while True:
                # Fetch batch of 100 articles
                datasource = await self._get_fresh_datasource()
                response = await datasource.get_now_table_tableName(
                    tableName="kb_knowledge",
                    sysparm_query=query,
                    sysparm_fields="sys_id,number,short_description,text,author,kb_knowledge_base,kb_category,workflow_state,active,published,can_read_user_criteria,sys_created_on,sys_updated_on",
                    sysparm_limit=str(batch_size),
                    sysparm_offset=str(offset),
                    sysparm_display_value="false",
                    sysparm_no_count="true",
                    sysparm_exclude_reference_link="true",
                )

                # Check for errors
                if not response.success or not response.data:
                    if response.error:
                        self.logger.error(f"❌ API error: {response.error}")
                    break

                # Extract articles from response
                articles_data = response.data.get("result", [])

                if not articles_data:
                    self.logger.debug(f"No more articles at offset {offset}")
                    break

                # Track the latest update timestamp for checkpoint
                if articles_data:
                    latest_update_time = articles_data[-1].get("sys_updated_on")

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
                        article_id = article_data.get("sys_id", "unknown")
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
                await self.article_sync_point.update_sync_point("articles", {"last_sync_time": latest_update_time})
                self.logger.debug(f"Checkpoint updated: {latest_update_time}")

            self.logger.info(f"✅ Articles synced: {total_articles_synced} articles, {total_attachments_synced} attachments")

        except Exception as e:
            self.logger.error(f"❌ Error syncing articles: {e}", exc_info=True)
            raise

    async def _process_single_article(
        self, article_data: Dict[str, Any]
    ) -> List[RecordUpdate]:
        """
        Process a single article and return RecordUpdate objects for article + attachments.

        Args:
            article_data: ServiceNow kb_knowledge record

        Returns:
            List[RecordUpdate]: RecordUpdate for article + RecordUpdates for attachments
        """
        try:
            article_sys_id = article_data.get("sys_id")
            article_title = article_data.get("short_description", "Unknown")

            self.logger.debug(f"Processing article: {article_title} ({article_sys_id})")

            record_updates = []

            # Transform article to WebpageRecord
            article_record = self._transform_to_article_webpage_record(article_data)
            if not article_record:
                self.logger.warning(f"Failed to transform article {article_sys_id}")
                return []

            # Fetch attachments for this article
            attachments_data = await self._fetch_attachments_for_article(article_sys_id)

            # Extract criteria IDs from article's can_read_user_criteria field
            can_read_criteria = article_data.get("can_read_user_criteria", "")
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
                author_ref = article_data.get("author")
                if author_ref:
                    author_sys_id = None
                    if isinstance(author_ref, dict):
                        author_sys_id = author_ref.get("value")
                    elif isinstance(author_ref, str) and author_ref:
                        author_sys_id = author_ref

                    if author_sys_id:
                        owner_perms = await self._convert_permissions_to_objects(
                            [{
                                "entity_type": EntityType.USER.value,
                                "source_sys_id": author_sys_id,
                                "role": PermissionType.OWNER.value,
                            }],
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
                att_sys_id = att_data.get("sys_id")

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

            self.logger.debug(f"✅ Article {article_sys_id} -> {len(record_updates)} RecordUpdates")
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
                self.logger.debug(f"Processed batch of {len(records_with_permissions)} records")

        except Exception as e:
            self.logger.error(f"Failed to process record updates batch: {e}", exc_info=True)
            raise

    async def _fetch_attachments_for_article(
        self, article_sys_id: str
    ) -> List[Dict[str, Any]]:
        """
        Fetch all attachments for a single article.

        Args:
            article_sys_id: Article sys_id

        Returns:
            List of attachment data dictionaries
        """
        try:
            # Query: table_name=kb_knowledge^table_sys_id={article_sys_id}
            query = f"table_name=kb_knowledge^table_sys_id={article_sys_id}"

            datasource = await self._get_fresh_datasource()
            response = await datasource.get_now_table_tableName(
                tableName="sys_attachment",
                sysparm_query=query,
                sysparm_fields="sys_id,file_name,content_type,size_bytes,table_sys_id,sys_created_on,sys_updated_on",
                sysparm_display_value="false",
                sysparm_no_count="true",
                sysparm_exclude_reference_link="true",
            )

            if not response.success or not response.data:
                return []

            return response.data.get("result", [])

        except Exception as e:
            self.logger.warning(f"Failed to fetch attachments for article {article_sys_id}: {e}")
            return []

    async def _convert_permissions_to_objects(
        self, permissions_dict: List[Dict[str, Any]], tx_store: TransactionStore
    ) -> List[Permission]:
        """
        Convert USER and GROUP permissions from dict format to Permission objects.

        ServiceNow-specific: Uses sourceUserId field to look up users, then gets their email.
        This method handles the connector-specific logic for permission mapping.

        Args:
            permissions_dict: List of permission dicts with entity_type, source_sys_id, role
                Example: [
                    {"entity_type": "USER", "source_sys_id": "abc123", "role": "OWNER"},
                    {"entity_type": "GROUP", "source_sys_id": "group456", "role": "WRITE"}
                ]
            tx_store: Transaction store for database access

        Returns:
            List of Permission objects ready for edge creation
        """
        permission_objects = []

        for perm in permissions_dict:
            try:
                entity_type = perm.get("entity_type")
                source_sys_id = perm.get("source_sys_id")
                role = perm.get("role")

                if not entity_type or not source_sys_id or not role:
                    self.logger.warning(f"Skipping incomplete permission dict: {perm}")
                    continue

                if entity_type == EntityType.USER.value:
                    # Use tx_store method to get user by source_sys_id
                    user = await tx_store.get_user_by_source_id(
                        source_sys_id,
                        Connectors.SERVICENOW,
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
                "read": [],
                "write": []
            }

            # Fetch READ criteria
            datasource = await self._get_fresh_datasource()
            read_response = await datasource.get_now_table_tableName(
                tableName="kb_uc_can_read_mtom",
                sysparm_query=f"kb_knowledge_base={kb_sys_id}",
                sysparm_fields="user_criteria",
                sysparm_display_value="false",
                sysparm_exclude_reference_link="true",
            )

            if read_response.success and read_response.data:
                for record in read_response.data.get("result", []):
                    criteria_ref = record.get("user_criteria")
                    if isinstance(criteria_ref, dict):
                        criteria_id = criteria_ref.get("value")
                    elif isinstance(criteria_ref, str):
                        criteria_id = criteria_ref
                    else:
                        continue

                    if criteria_id:
                        criteria_map["read"].append(criteria_id)

            # Fetch WRITE criteria (contribute)
            datasource = await self._get_fresh_datasource()
            write_response = await datasource.get_now_table_tableName(
                tableName="kb_uc_can_contribute_mtom",
                sysparm_query=f"kb_knowledge_base={kb_sys_id}",
                sysparm_fields="user_criteria",
                sysparm_display_value="false",
                sysparm_exclude_reference_link="true",
            )

            if write_response.success and write_response.data:
                for record in write_response.data.get("result", []):
                    criteria_ref = record.get("user_criteria")
                    if isinstance(criteria_ref, dict):
                        criteria_id = criteria_ref.get("value")
                    elif isinstance(criteria_ref, str):
                        criteria_id = criteria_ref
                    else:
                        continue

                    if criteria_id:
                        criteria_map["write"].append(criteria_id)

            return criteria_map

        except Exception as e:
            self.logger.error(f"Failed to fetch KB permissions: {e}", exc_info=True)
            return {"read": [], "write": []}

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
            criteria_query = f"sys_idIN{','.join(criteria_ids)}"
            datasource = await self._get_fresh_datasource()
            criteria_response = await datasource.get_now_table_tableName(
                tableName="user_criteria",
                sysparm_query=criteria_query,
                sysparm_fields="sys_id,user,group,role,department,location,company,cost_center",
                sysparm_display_value="false",
                sysparm_exclude_reference_link="true",
            )

            if criteria_response.success and criteria_response.data:
                for criteria_record in criteria_response.data.get("result", []):
                    # Extract permissions from this criteria
                    perms = await self._extract_permissions_from_user_criteria_details(
                        criteria_record,
                        permission_type
                    )
                    permission_dicts.extend(perms)

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
        self, criteria_details: Dict[str, Any], permission_type: PermissionType
    ) -> List[Dict[str, Any]]:
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
            criteria_details: user_criteria record from ServiceNow
            permission_type: READER or WRITER

        Returns:
            List of permission dictionaries
        """
        permissions = []

        try:
            # Helper function to parse comma-separated sys_ids
            def parse_sys_ids(field_value) -> List[str]:
                """Parse comma-separated sys_ids from field value."""
                if not field_value:
                    return []

                sys_ids = []
                if isinstance(field_value, dict):
                    # Reference field with value
                    value = field_value.get("value", "")
                    if value:
                        sys_ids = [s.strip() for s in value.split(",") if s.strip()]
                elif isinstance(field_value, str) and field_value:
                    # Direct string value
                    sys_ids = [s.strip() for s in field_value.split(",") if s.strip()]

                return sys_ids

            # 1. Extract USER permissions
            user_sys_ids = parse_sys_ids(criteria_details.get("user"))
            for user_sys_id in user_sys_ids:
                permissions.append({
                    "entity_type": EntityType.USER.value,
                    "source_sys_id": user_sys_id,
                    "role": permission_type.value,
                })

            # 2. Extract GROUP permissions
            group_sys_ids = parse_sys_ids(criteria_details.get("group"))
            for group_sys_id in group_sys_ids:
                permissions.append({
                    "entity_type": EntityType.GROUP.value,
                    "source_sys_id": group_sys_id,
                    "role": permission_type.value,
                })

            # 3. Extract ROLE permissions (role names need lookup)
            role_sys_ids = parse_sys_ids(criteria_details.get("role"))
            for role_sys_id in role_sys_ids:
                permissions.append({
                    "entity_type": EntityType.GROUP.value,  # Roles are stored as groups
                    "source_sys_id": role_sys_id,
                    "role": permission_type.value,
                })

            # 4. Extract DEPARTMENT permissions (organizational entity)
            department_sys_ids = parse_sys_ids(criteria_details.get("department"))
            for dept_sys_id in department_sys_ids:
                permissions.append({
                    "entity_type": EntityType.GROUP.value,  # Org entities stored as groups
                    "source_sys_id": dept_sys_id,
                    "role": permission_type.value,
                })

            # 5. Extract LOCATION permissions (organizational entity)
            location_sys_ids = parse_sys_ids(criteria_details.get("location"))
            for loc_sys_id in location_sys_ids:
                permissions.append({
                    "entity_type": EntityType.GROUP.value,  # Org entities stored as groups
                    "source_sys_id": loc_sys_id,
                    "role": permission_type.value,
                })

            # 6. Extract COMPANY permissions (organizational entity)
            company_sys_ids = parse_sys_ids(criteria_details.get("company"))
            for company_sys_id in company_sys_ids:
                permissions.append({
                    "entity_type": EntityType.GROUP.value,  # Org entities stored as groups
                    "source_sys_id": company_sys_id,
                    "role": permission_type.value,
                })

        except Exception as e:
            self.logger.error(
                f"Error extracting permissions from user_criteria: {e}",
                exc_info=True
            )
        self.logger.debug(f"Extracted {len(permissions)} permissions from user_criteria: {permissions}")
        return permissions

    async def _transform_to_app_user(
        self, user_data: Dict[str, Any]
    ) -> Optional[AppUser]:
        """
        Transform ServiceNow user to AppUser entity.

        Args:
            user_data: ServiceNow sys_user record

        Returns:
            AppUser: Transformed user entity or None if invalid
        """
        try:
            sys_id = user_data.get("sys_id")
            email = user_data.get("email", "").strip()
            user_name = user_data.get("user_name", "")
            first_name = user_data.get("first_name", "")
            last_name = user_data.get("last_name", "")

            if not sys_id or not email:
                return None

            # Build full name
            full_name = f"{first_name} {last_name}".strip()
            if not full_name:
                full_name = user_name or email

            # Parse timestamps
            source_created_at = None
            source_updated_at = None
            if user_data.get("sys_created_on"):
                source_created_at = self._parse_servicenow_datetime(user_data["sys_created_on"])
            if user_data.get("sys_updated_on"):
                source_updated_at = self._parse_servicenow_datetime(user_data["sys_updated_on"])

            app_user = AppUser(
                app_name=Connectors.SERVICENOW,
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
            self.logger.error(f"Error transforming user {user_data.get('sys_id')}: {e}", exc_info=True)
            return None

    def _transform_to_user_group(
        self, group_data: Dict[str, Any]
    ) -> Optional[AppUserGroup]:
        """
        Transform ServiceNow group to AppUserGroup entity.

        Args:
            group_data: ServiceNow sys_user_group record

        Returns:
            AppUserGroup: Transformed user group entity or None if invalid
        """
        try:
            sys_id = group_data.get("sys_id")
            name = group_data.get("name", "")

            if not sys_id or not name:
                return None

            # Parse timestamps
            source_created_at = None
            source_updated_at = None
            if group_data.get("sys_created_on"):
                source_created_at = self._parse_servicenow_datetime(group_data["sys_created_on"])
            if group_data.get("sys_updated_on"):
                source_updated_at = self._parse_servicenow_datetime(group_data["sys_updated_on"])

            # Create AppUserGroup (for user groups, not record groups)
            user_group = AppUserGroup(
                app_name=Connectors.SERVICENOW,
                source_user_group_id=sys_id,
                name=name,
                org_id=self.data_entities_processor.org_id,
                source_created_at=source_created_at,
                source_updated_at=source_updated_at,
            )

            return user_group

        except Exception as e:
            self.logger.error(f"Error transforming group {group_data.get('sys_id')}: {e}", exc_info=True)
            return None

    def _transform_to_organizational_group(
        self, entity_data: Dict[str, Any], prefix: str
    ) -> Optional[AppUserGroup]:
        """
        Transform ServiceNow organizational entity to AppUserGroup.

        This is a generic transform method for companies, departments, locations, and cost centers.

        Args:
            entity_data: ServiceNow entity record (company, department, location, cost_center)
            prefix: Name prefix (COMPANY_, DEPARTMENT_, LOCATION_, COSTCENTER_)

        Returns:
            AppUserGroup: Transformed organizational group or None if invalid
        """
        try:
            sys_id = entity_data.get("sys_id")
            name = entity_data.get("name", "")

            if not sys_id or not name:
                return None

            # Parse timestamps
            source_created_at = None
            source_updated_at = None
            if entity_data.get("sys_created_on"):
                source_created_at = self._parse_servicenow_datetime(
                    entity_data["sys_created_on"]
                )
            if entity_data.get("sys_updated_on"):
                source_updated_at = self._parse_servicenow_datetime(
                    entity_data["sys_updated_on"]
                )

            # Create AppUserGroup with prefix
            org_group = AppUserGroup(
                app_name=Connectors.SERVICENOW,
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
                f"Error transforming organizational entity {entity_data.get('sys_id')}: {e}",
                exc_info=True,
            )
            return None

    def _transform_to_kb_record_group(
        self, kb_data: Dict[str, Any]
    ) -> Optional[RecordGroup]:
        """
        Transform ServiceNow knowledge base to RecordGroup entity.

        Args:
            kb_data: ServiceNow kb_knowledge_base record

        Returns:
            RecordGroup: Transformed KB as RecordGroup with type SERVICENOW or None if invalid
        """
        try:
            sys_id = kb_data.get("sys_id")
            title = kb_data.get("title", "")

            if not sys_id or not title:
                self.logger.warning(f"KB missing sys_id or title: {kb_data}")
                return None

            # Parse timestamps
            source_created_at = None
            source_updated_at = None
            if kb_data.get("sys_created_on"):
                source_created_at = self._parse_servicenow_datetime(kb_data["sys_created_on"])
            if kb_data.get("sys_updated_on"):
                source_updated_at = self._parse_servicenow_datetime(kb_data["sys_updated_on"])

            # Construct web URL: https://<instance>.service-now.com/kb?kb=<sys_id>
            web_url = None
            if self.instance_url:
                web_url = f"{self.instance_url}kb?kb={sys_id}"

            # Create RecordGroup for Knowledge Base
            kb_record_group = RecordGroup(
                org_id=self.data_entities_processor.org_id,
                name=title,
                description=kb_data.get("description", ""),
                external_group_id=sys_id,
                connector_name=Connectors.SERVICENOW,
                group_type=RecordGroupType.SERVICENOWKB,
                web_url=web_url,
                source_created_at=source_created_at,
                source_updated_at=source_updated_at,
            )

            return kb_record_group

        except Exception as e:
            self.logger.error(f"Error transforming KB {kb_data.get('sys_id')}: {e}", exc_info=True)
            return None

    def _transform_to_category_record_group(
        self, category_data: Dict[str, Any]
    ) -> Optional[RecordGroup]:
        """
        Transform ServiceNow kb_category to RecordGroup entity.

        Args:
            category_data: ServiceNow kb_category record

        Returns:
            RecordGroup: Transformed category as RecordGroup with type SERVICENOW_CATEGORY or None if invalid
        """
        try:
            sys_id = category_data.get("sys_id")
            label = category_data.get("label", "")
            parent_sys_id = category_data.get("parent_id")

            if not sys_id or not label:
                self.logger.warning(f"Category missing sys_id or label: {category_data}")
                return None

            # Parse timestamps
            source_created_at = None
            source_updated_at = None
            if category_data.get("sys_created_on"):
                source_created_at = self._parse_servicenow_datetime(category_data["sys_created_on"])
            if category_data.get("sys_updated_on"):
                source_updated_at = self._parse_servicenow_datetime(category_data["sys_updated_on"])

            # Construct web URL: https://<instance>.service-now.com/sp?id=kb_category&kb_category=<sys_id>
            web_url = None
            if self.instance_url:
                web_url = f"{self.instance_url}sp?id=kb_category&kb_category={sys_id}"

            # Create RecordGroup for Category
            category_record_group = RecordGroup(
                org_id=self.data_entities_processor.org_id,
                name=label,
                short_name=category_data.get("value", ""),
                parent_external_group_id=parent_sys_id,
                external_group_id=sys_id,
                connector_name=Connectors.SERVICENOW,
                group_type=RecordGroupType.SERVICENOW_CATEGORY,
                web_url=web_url,
                source_created_at=source_created_at,
                source_updated_at=source_updated_at,
                inherit_permissions=True,
            )

            return category_record_group

        except Exception as e:
            self.logger.error(f"Error transforming category {category_data.get('sys_id')}: {e}", exc_info=True)
            return None

    def _transform_to_article_webpage_record(
        self, article_data: Dict[str, Any]
    ) -> Optional[WebpageRecord]:
        """
        Transform ServiceNow kb_knowledge article to WebpageRecord entity.

        Args:
            article_data: ServiceNow kb_knowledge record

        Returns:
            WebpageRecord: Transformed article or None if invalid
        """
        try:
            sys_id = article_data.get("sys_id")
            short_description = article_data.get("short_description", "")

            if not sys_id or not short_description:
                self.logger.warning(f"Article missing sys_id or short_description: {article_data}")
                return None

            # Parse timestamps
            source_created_at = None
            source_updated_at = None
            if article_data.get("sys_created_on"):
                source_created_at = self._parse_servicenow_datetime(article_data["sys_created_on"])
            if article_data.get("sys_updated_on"):
                source_updated_at = self._parse_servicenow_datetime(article_data["sys_updated_on"])

            # Construct web URL: https://<instance>/sp?id=kb_article&sys_id=<sys_id>
            web_url = None
            if self.instance_url:
                web_url = f"{self.instance_url}/sp?id=kb_article&sys_id={sys_id}"

            # Extract category sys_id for external_record_group_id
            # Fallback to KB if category is empty/missing
            kb_category_ref = article_data.get("kb_category")
            external_record_group_id = None
            record_group_type = None

            # Try category first
            if isinstance(kb_category_ref, dict):
                external_record_group_id = kb_category_ref.get("value")
            elif isinstance(kb_category_ref, str) and kb_category_ref:
                external_record_group_id = kb_category_ref

            if external_record_group_id:
                record_group_type = RecordGroupType.SERVICENOW_CATEGORY
            else:
                # Fallback to KB if no category
                kb_ref = article_data.get("kb_knowledge_base")
                if isinstance(kb_ref, dict):
                    external_record_group_id = kb_ref.get("value")
                elif isinstance(kb_ref, str) and kb_ref:
                    external_record_group_id = kb_ref

                if external_record_group_id:
                    record_group_type = RecordGroupType.SERVICENOWKB
                    self.logger.debug(f"Article {sys_id} has no category, using KB {external_record_group_id} as parent")
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
            self.logger.error(f"Error transforming article {article_data.get('sys_id')}: {e}", exc_info=True)
            return None

    def _transform_to_attachment_file_record(
        self,
        attachment_data: Dict[str, Any],
        parent_record_group_type: Optional[RecordGroupType] = None,
        parent_external_record_group_id: Optional[str] = None,
    ) -> Optional[FileRecord]:
        """
        Transform ServiceNow sys_attachment to FileRecord entity.

        Args:
            attachment_data: ServiceNow sys_attachment record
            parent_record_group_type: The record group type from parent article (CATEGORY or KB)
            parent_external_record_group_id: The external record group ID from parent article

        Returns:
            FileRecord: Transformed attachment or None if invalid
        """
        try:
            sys_id = attachment_data.get("sys_id")
            file_name = attachment_data.get("file_name", "")

            if not sys_id or not file_name:
                self.logger.warning(f"Attachment missing sys_id or file_name: {attachment_data}")
                return None

            # Parse timestamps
            source_created_at = None
            source_updated_at = None
            if attachment_data.get("sys_created_on"):
                source_created_at = self._parse_servicenow_datetime(attachment_data["sys_created_on"])
            if attachment_data.get("sys_updated_on"):
                source_updated_at = self._parse_servicenow_datetime(attachment_data["sys_updated_on"])

            # Construct web URL: https://<instance>/sys_attachment.do?sys_id=<sys_id>
            web_url = None
            if self.instance_url:
                web_url = f"{self.instance_url}/sys_attachment.do?sys_id={sys_id}"

            # Parse content type for mime type
            content_type = attachment_data.get("content_type", "application/octet-stream")
            mime_type = None
            # Map to MimeTypes enum if possible
            for mime in MimeTypes:
                if mime.value == content_type:
                    mime_type = mime
                    break

            # Parse file size
            file_size = None
            size_bytes = attachment_data.get("size_bytes")
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
                mime_type=mime_type,
                parent_external_record_id=attachment_data.get("table_sys_id"),  # Parent article sys_id
                parent_record_type=RecordType.WEBPAGE,  # Parent is article
                record_group_type=parent_record_group_type,  # Same as parent article (CATEGORY or KB)
                external_record_group_id=parent_external_record_group_id,  # Same as parent article
                weburl=web_url,
                is_file=True,
                size_in_bytes=file_size,
                source_created_at=source_created_at,
                source_updated_at=source_updated_at,
                extension=file_name.split(".")[-1] if "." in file_name else None,
            )

            return attachment_record

        except Exception as e:
            self.logger.error(f"Error transforming attachment {attachment_data.get('sys_id')}: {e}", exc_info=True)
            return None

    def _parse_servicenow_datetime(self, datetime_str: str) -> Optional[int]:
        """
        Parse ServiceNow datetime string to epoch timestamp in milliseconds.

        ServiceNow format: "2023-01-15 10:30:45" (UTC)

        Args:
            datetime_str: ServiceNow datetime string

        Returns:
            int: Epoch timestamp in milliseconds or None if parsing fails
        """
        try:
            from datetime import datetime

            # ServiceNow format: "YYYY-MM-DD HH:MM:SS"
            dt = datetime.strptime(datetime_str, "%Y-%m-%d %H:%M:%S")
            return int(dt.timestamp() * 1000)
        except Exception as e:
            self.logger.warning(f"Failed to parse datetime '{datetime_str}': {e}")
            return None


    @classmethod
    async def create_connector(
        cls,
        logger: Logger,
        data_store_provider: DataStoreProvider,
        config_service: ConfigurationService,
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

        return cls(logger, data_entities_processor, data_store_provider, config_service)
