"""
ServiceNow Knowledge Base Connector

This connector syncs knowledge base articles, categories, attachments, and permissions
from ServiceNow into the PipesHub AI platform.

Synced Entities:
- Users and Groups (for permissions)
- Knowledge Bases (containers)
- Categories (hierarchy)
- KB Articles (content)
- Attachments (files)
"""

import uuid
from logging import Logger
from typing import Any, AsyncGenerator, Dict, List, Optional

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
from app.connectors.sources.servicenow.common.apps import ServicenowKBApp
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
        "has_parent": True,
        "sync_point_key": "companies",
    },
    "department": {
        "table": "cmn_department",
        "fields": "sys_id,name,parent,company,sys_created_on,sys_updated_on",
        "prefix": "DEPARTMENT_",
        "has_parent": True,
        "sync_point_key": "departments",
    },
    "location": {
        "table": "cmn_location",
        "fields": "sys_id,name,parent,company,sys_created_on,sys_updated_on",
        "prefix": "LOCATION_",
        "has_parent": True,
        "sync_point_key": "locations",
    },
    "cost_center": {
        "table": "cmn_cost_center",
        "fields": "sys_id,name,parent,sys_created_on,sys_updated_on",
        "prefix": "COSTCENTER_",
        "has_parent": True,
        "sync_point_key": "cost_centers",
    },
}


@ConnectorBuilder("ServiceNowKB")\
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
            )
        )
        .with_redirect_uri("connectors/oauth/callback/ServiceNowKB", True)
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
class ServiceNowKBConnector(BaseConnector):
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
            ServicenowKBApp(),
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

        # Role name to sys_id mapping (loaded from DB before article sync)
        self.role_name_to_id_map: Dict[str, str] = {}

        # Batch processing configuration
        self.batch_size = 100
        self.max_concurrent_batches = 3

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
                "/services/connectors/servicenowkb/config"
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

    async def test_connection_and_access(self) -> bool:
        """
        Test OAuth connection and access to ServiceNow API.

        Returns:
            bool: True if connection successful, False otherwise
        """
        try:
            self.logger.info("🔍 Testing ServiceNow OAuth connection...")

            # Make a simple API call to verify OAuth token works
            response = await self.servicenow_datasource.get_now_table_tableName(
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
        Run full synchronization of ServiceNow Knowledge Base data with multi-user support.

        Sync order:
        1. Users and Groups (global, no impersonation)
        2-4. Per-user sync with impersonation:
           - Knowledge Bases (top-level containers)
           - Categories (hierarchy within KBs)
           - KB Articles (content with permissions)
           - Attachments (files linked to articles)
        """
        try:
            org_id = self.data_entities_processor.org_id
            self.logger.info(f"🚀 Starting multi-user ServiceNow KB sync for org: {org_id}")

            # Ensure client is initialized
            if not self.servicenow_client or not self.servicenow_datasource:
                raise Exception("ServiceNow client not initialized. Call init() first.")

            # Step 1: Sync users and groups globally (ONCE, no impersonation)
            self.logger.info("Step 1/5: Syncing users and groups (global)...")
            await self._sync_users_and_groups()

            # Get active platform users for this org
            active_users = await self.data_entities_processor.get_all_active_users()

            if not active_users:
                self.logger.warning("⚠️ No active users found for this organization")
                return

            self.logger.info(f"📋 Found {len(active_users)} active platform users")

            # Match platform users with ServiceNow users
            matched_users = []
            async with self.data_store_provider.transaction() as tx_store:
                for user in active_users:
                    try:
                        app_user = await tx_store.get_app_user_by_email(user.email)

                        if app_user and app_user.source_user_id:
                            matched_users.append((user.email, app_user.source_user_id))
                            self.logger.debug(f"✓ Matched {user.email} -> {app_user.source_user_id}")
                        else:
                            self.logger.debug(f"✗ No ServiceNow user for {user.email}")

                    except Exception as e:
                        self.logger.warning(f"Error matching user {user.email}: {e}")
                        continue

            if not matched_users:
                self.logger.warning("⚠️ No platform users matched with ServiceNow users")
                return

            self.logger.info(f"✅ Matched {len(matched_users)} users with ServiceNow accounts")

            # Steps 2-5: Sync per user with impersonation
            for user_email, user_sys_id in matched_users:
                try:
                    self.logger.info(f"👤 Syncing for user: {user_email} (sys_id: {user_sys_id})")

                    # Step 2: Sync KBs
                    self.logger.info("  Step 2/4: Syncing KBs...")
                    await self._sync_knowledge_bases(user_sys_id, user_email)

                    # Step 3: Sync Categories
                    self.logger.info("  Step 3/4: Syncing Categories...")
                    await self._sync_categories(user_sys_id, user_email)

                    # Step 4: Sync Articles
                    self.logger.info("  Step 4/4: Syncing Articles...")
                    await self._sync_articles(user_sys_id, user_email)

                    self.logger.info(f"✅ User {user_email} sync completed")

                except Exception as e:
                    self.logger.error(f"❌ Failed to sync for user {user_email}: {e}", exc_info=True)
                    # Continue with next user
                    continue

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
            response = await self.servicenow_datasource.get_now_table_tableName(
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

            # Use the ServiceNow REST client directly for file download
            if not self.servicenow_client:
                raise HTTPException(
                    status_code=500,
                    detail="ServiceNow client not initialized"
                )

            # Download using REST client (returns bytes directly)
            file_content = await self.servicenow_client.download_attachment(attachment_sys_id)

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
        Sync users, groups, and roles from ServiceNow.

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
            # Step 1: Sync users
            await self._sync_users()

            # Step 2: Sync groups
            await self._sync_groups()

            # Step 3: Sync group memberships (user-group relationships)
            await self._sync_group_memberships()

            # # Step 4: Sync roles (creates role-based usergroups)
            # await self._sync_roles()

            # # Step 5: Sync role hierarchy (parent-child edges between roles)
            # await self._sync_role_hierarchy()

            # # Step 6: Sync user-role assignments (user-to-role membership edges)
            # await self._sync_user_role_assignments()

            # Step 7: Sync organizational entities (companies, departments, locations, cost centers)
            await self._sync_organizational_entities()

            self.logger.info("✅ Users, groups, and roles synced successfully")

        except Exception as e:
            self.logger.error(f"❌ Error syncing users/groups: {e}", exc_info=True)
            raise

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
                response = await self.servicenow_datasource.get_now_table_tableName(
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
                            await self.data_entities_processor.create_user_group_membership(
                                link["user_sys_id"],
                                link["org_sys_id"],
                                Connectors.SERVICENOWKB,
                                tx_store,
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

    async def _sync_groups(self) -> None:
        """
        Sync groups from ServiceNow using offset-based pagination.
        Uses two-pass approach: first sync all groups, then create hierarchy edges.

        First sync: Fetches all groups
        Subsequent syncs: Only fetches groups modified since last sync
        """
        try:
            # Get last sync checkpoint
            last_sync_data = await self.group_sync_point.read_sync_point("groups")
            last_sync_time = (last_sync_data.get("last_sync_time") if last_sync_data else None)

            if last_sync_time:
                self.logger.info(f"🔄 Delta sync: fetching groups updated after {last_sync_time}")
                query = f"sys_updated_on>{last_sync_time}^ORDERBYsys_updated_on"
            else:
                self.logger.info("🆕 Full sync: fetching all groups")
                query = "ORDERBYsys_updated_on"

            # Pagination variables
            batch_size = 100
            offset = 0
            total_synced = 0
            latest_update_time = None

            # Collect parent-child relationships for second pass
            parent_child_relationships = []

            # FIRST PASS: Sync all group nodes
            while True:
                # Fetch one page of groups
                response = await self.servicenow_datasource.get_now_table_tableName(
                    tableName="sys_user_group",
                    sysparm_query=query,
                    sysparm_fields="sys_id,name,description,parent,manager,sys_created_on,sys_updated_on",
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

                # Extract groups from response
                groups_data = response.data.get("result", [])

                if not groups_data:
                    break

                # Track the latest update timestamp for checkpoint
                if groups_data:
                    latest_update_time = groups_data[-1].get("sys_updated_on")

                # Collect parent-child relationships for later
                for group_data in groups_data:
                    parent_ref = group_data.get("parent")
                    if parent_ref:
                        # Extract parent sys_id from reference field
                        parent_sys_id = None
                        if isinstance(parent_ref, dict):
                            parent_sys_id = parent_ref.get("value")
                        elif isinstance(parent_ref, str) and parent_ref:
                            parent_sys_id = parent_ref

                        if parent_sys_id:
                            child_sys_id = group_data.get("sys_id")
                            parent_child_relationships.append(
                                {
                                    "child_sys_id": child_sys_id,
                                    "parent_sys_id": parent_sys_id,
                                }
                            )

                # Transform groups
                user_groups = []
                for group_data in groups_data:
                    user_group = self._transform_to_user_group(group_data)
                    if user_group:
                        user_groups.append(user_group)

                # Save groups (nodes only, no edges yet)
                if user_groups:
                    async with self.data_store_provider.transaction() as tx_store:
                        await tx_store.batch_upsert_user_groups(user_groups)

                    total_synced += len(user_groups)

                # Move to next page
                offset += batch_size

                # If this page has fewer records than batch_size, we're done
                if len(groups_data) < batch_size:
                    break

            # SECOND PASS: Create all hierarchy edges now that all nodes exist
            if parent_child_relationships:
                async with self.data_store_provider.transaction() as tx_store:
                    success_count = 0
                    for relationship in parent_child_relationships:
                        success = await self.data_entities_processor.create_user_group_hierarchy(
                            relationship["child_sys_id"],
                            relationship["parent_sys_id"],
                            Connectors.SERVICENOWKB,
                            tx_store,
                        )
                        if success:
                            success_count += 1

                    if success_count > 0:
                        self.logger.info(f"Created {success_count} group hierarchy edges")

            # Save checkpoint for next sync
            if latest_update_time:
                await self.group_sync_point.update_sync_point("groups", {"last_sync_time": latest_update_time})

            self.logger.info(f"Groups sync complete, Total synced: {total_synced}")

        except Exception as e:
            self.logger.error(f"❌ Groups sync failed: {e}", exc_info=True)
            raise

    async def _sync_group_memberships(self) -> None:
        """
        Sync group memberships from ServiceNow using offset-based pagination.

        This handles the case where new users are added to groups - when a new user joins a group,
        the sys_user_grmember record gets a sys_updated_on timestamp, allowing us to detect
        membership changes and grant permissions to new users for existing articles.

        First sync: Fetches all memberships
        Subsequent syncs: Only fetches memberships modified since last sync
        """
        try:
            # Get last sync checkpoint (using separate key from groups sync)
            last_sync_data = await self.group_sync_point.read_sync_point("group_memberships")
            last_sync_time = (last_sync_data.get("last_sync_time") if last_sync_data else None)

            if last_sync_time:
                self.logger.info(f"🔄 Delta sync: fetching memberships updated after {last_sync_time}")
                query = f"sys_updated_on>{last_sync_time}^ORDERBYsys_updated_on"
            else:
                self.logger.info("🆕 Full sync: fetching all memberships")
                query = "ORDERBYsys_updated_on"

            # Pagination variables
            batch_size = 100
            offset = 0
            latest_update_time = None

            # Collect all memberships to process
            all_memberships = []

            # Paginate through all memberships
            while True:
                # Fetch one page of memberships
                response = await self.servicenow_datasource.get_now_table_tableName(
                    tableName="sys_user_grmember",
                    sysparm_query=query,
                    sysparm_fields="sys_id,user,group,sys_updated_on",
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

                # Extract memberships from response
                memberships_data = response.data.get("result", [])

                if not memberships_data:
                    break

                # Track the latest update timestamp for checkpoint
                for membership in memberships_data:
                    updated_on = membership.get("sys_updated_on")
                    if updated_on and (not latest_update_time or updated_on > latest_update_time):
                        latest_update_time = updated_on

                    # Extract and validate sys_ids
                    user_sys_id = membership.get("user", {})
                    group_sys_id = membership.get("group", {})

                    # Extract sys_id from reference fields
                    if isinstance(user_sys_id, dict):
                        user_sys_id = user_sys_id.get("value")
                    if isinstance(group_sys_id, dict):
                        group_sys_id = group_sys_id.get("value")

                    if user_sys_id and group_sys_id:
                        all_memberships.append({"user_sys_id": user_sys_id, "group_sys_id": group_sys_id})

                # Move to next page
                offset += batch_size

                # If this page has fewer records than batch_size, we're done
                if len(memberships_data) < batch_size:
                    break

            # Create user-group membership edges in batches
            if all_memberships:
                async with self.data_store_provider.transaction() as tx_store:
                    success_count = 0
                    for membership in all_memberships:
                        success = await self.data_entities_processor.create_user_group_membership(
                            membership["user_sys_id"],
                            membership["group_sys_id"],
                            Connectors.SERVICENOWKB,
                            tx_store,
                        )
                        if success:
                            success_count += 1

                    if success_count > 0:
                        self.logger.info(f"Created {success_count} user-group membership edges")

            # Save checkpoint for next sync
            if latest_update_time:
                await self.group_sync_point.update_sync_point("group_memberships", {"last_sync_time": latest_update_time})

            self.logger.info(f"Memberships sync complete, Total processed: {success_count if all_memberships else 0}")

        except Exception as e:
            self.logger.error(f"❌ Group memberships sync failed: {e}", exc_info=True)
            raise

    async def _sync_roles(self) -> None:
        """
        Sync roles from ServiceNow sys_user_role table.

        Creates AppUserGroup entities for each ServiceNow role with ROLE_ prefix
        to distinguish them from regular user groups.

        Supports incremental sync using sys_updated_on timestamp.
        """
        try:
            self.logger.info("Starting role sync")

            # Get last sync checkpoint for incremental sync
            last_sync_data = await self.role_sync_point.read_sync_point("roles")
            last_sync_time = (
                last_sync_data.get("last_sync_time") if last_sync_data else None
            )

            if last_sync_time:
                self.logger.info(
                    f"🔄 Delta sync: fetching roles updated after {last_sync_time}"
                )
                query = f"sys_updated_on>{last_sync_time}^ORDERBYsys_updated_on"
            else:
                self.logger.info("🆕 Full sync: fetching all roles")
                query = "ORDERBYsys_updated_on"

            # Pagination variables
            batch_size = 100
            offset = 0
            total_synced = 0
            latest_update_time = None

            # Paginate through all roles
            while True:
                response = await self.servicenow_datasource.get_now_table_tableName(
                    tableName="sys_user_role",
                    sysparm_query=query,
                    sysparm_fields="sys_id,name,description,sys_created_on,sys_updated_on",
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

                # Extract roles from response
                roles_data = response.data.get("result", [])

                if not roles_data:
                    break

                # Transform to AppUserGroup entities
                role_groups = []
                for role_data in roles_data:
                    try:
                        role_sys_id = role_data.get("sys_id")
                        role_name = role_data.get("name", "")
                        role_description = role_data.get("description", "")

                        if not role_name:
                            self.logger.warning(
                                f"Role {role_sys_id} has no name, skipping"
                            )
                            continue

                        # Parse timestamps
                        created_on = role_data.get("sys_created_on")
                        updated_on = role_data.get("sys_updated_on")

                        # Track latest update time
                        if updated_on and (
                            not latest_update_time or updated_on > latest_update_time
                        ):
                            latest_update_time = updated_on

                        # Create AppUserGroup with ROLE_ prefix
                        role_group = AppUserGroup(
                            app_name=Connectors.SERVICENOWKB,
                            source_user_group_id=role_sys_id,
                            name=f"ROLE_{role_name}",
                            description=(
                                f"ServiceNow Role: {role_description}"
                                if role_description
                                else f"ServiceNow Role: {role_name}"
                            ),
                            org_id=self.data_entities_processor.org_id,
                            created_at=self._parse_servicenow_datetime(created_on),
                            updated_at=self._parse_servicenow_datetime(updated_on),
                        )

                        role_groups.append(role_group)

                    except Exception as e:
                        self.logger.error(
                            f"Error transforming role {role_data.get('sys_id')}: {e}"
                        )
                        continue

                # Batch upsert roles
                if role_groups:
                    async with self.data_store_provider.transaction() as tx_store:
                        await tx_store.batch_upsert_user_groups(role_groups)
                        total_synced += len(role_groups)
                        self.logger.info(f"Upserted {len(role_groups)} roles")

                # Move to next page
                offset += batch_size

                # If this page has fewer records than batch_size, we're done
                if len(roles_data) < batch_size:
                    break

            # Save checkpoint
            if latest_update_time:
                await self.role_sync_point.update_sync_point(
                    "roles", {"last_sync_time": latest_update_time}
                )

            self.logger.info(f"✅ Role sync complete. Total synced: {total_synced}")

        except Exception as e:
            self.logger.error(f"❌ Role sync failed: {e}", exc_info=True)
            raise

    async def _sync_role_hierarchy(self) -> None:
        """
        Sync role hierarchy from sys_user_role_contains table.

        ServiceNow roles use a many-to-many containment model where:
        - One role can contain multiple child roles
        - One role can be contained by multiple parent roles

        The sys_user_role_contains table stores these relationships:
        - contains field = parent role sys_id
        - role field = child role sys_id

        Note: This is a full sync each time (no incremental) since role hierarchy
        changes are rare and the table is typically small.
        """
        try:
            self.logger.info("Starting role hierarchy sync")

            # Fetch all role containment relationships (full sync)
            batch_size = 100
            offset = 0
            all_containment_records = []

            while True:
                response = await self.servicenow_datasource.get_now_table_tableName(
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
                    if response.error:
                        self.logger.error(f"❌ API error: {response.error}")
                    break

                containment_data = response.data.get("result", [])

                if not containment_data:
                    break

                all_containment_records.extend(containment_data)

                offset += batch_size

                if len(containment_data) < batch_size:
                    break

            if not all_containment_records:
                self.logger.info("No role hierarchy relationships found")
                return

            self.logger.info(
                f"Fetched {len(all_containment_records)} role containment relationships"
            )

            # Create hierarchy edges
            async with self.data_store_provider.transaction() as tx_store:
                hierarchy_count = 0

                for record in all_containment_records:
                    try:
                        # Extract sys_ids from reference fields
                        parent_role_ref = record.get("contains", {})
                        child_role_ref = record.get("role", {})

                        parent_role_sys_id = (
                            parent_role_ref.get("value")
                            if isinstance(parent_role_ref, dict)
                            else parent_role_ref
                        )
                        child_role_sys_id = (
                            child_role_ref.get("value")
                            if isinstance(child_role_ref, dict)
                            else child_role_ref
                        )

                        if not parent_role_sys_id or not child_role_sys_id:
                            self.logger.warning(
                                f"Invalid containment record {record.get('sys_id')}: "
                                f"parent={parent_role_sys_id}, child={child_role_sys_id}"
                            )
                            continue

                        # Create parent-child hierarchy edge
                        success = await self.data_entities_processor.create_user_group_hierarchy(
                            child_source_id=child_role_sys_id,
                            parent_source_id=parent_role_sys_id,
                            connector_name=Connectors.SERVICENOWKB,
                            tx_store=tx_store,
                        )

                        if success:
                            hierarchy_count += 1

                    except Exception as e:
                        self.logger.error(
                            f"Error creating hierarchy for record {record.get('sys_id')}: {e}"
                        )
                        continue

                self.logger.info(
                    f"✅ Role hierarchy sync complete. Created {hierarchy_count} relationships"
                )

        except Exception as e:
            self.logger.error(f"❌ Role hierarchy sync failed: {e}", exc_info=True)
            raise

    async def _sync_user_role_assignments(self) -> None:
        """
        Sync user-role assignments from sys_user_has_role table.

        Creates membership edges between users and role-based usergroups.
        Includes both direct and inherited role assignments.

        Supports incremental sync using sys_updated_on timestamp.
        """
        try:
            self.logger.info("Starting user-role assignment sync")

            # Get last sync checkpoint
            last_sync_data = await self.role_assignment_sync_point.read_sync_point(
                "role_assignments"
            )
            last_sync_time = (
                last_sync_data.get("last_sync_time") if last_sync_data else None
            )

            # Build query for active assignments with incremental sync
            if last_sync_time:
                self.logger.info(
                    f"🔄 Delta sync: fetching assignments updated after {last_sync_time}"
                )
                query = f"state=active^sys_updated_on>{last_sync_time}^ORDERBYsys_updated_on"
            else:
                self.logger.info("🆕 Full sync: fetching all active assignments")
                query = "state=active^ORDERBYsys_updated_on"

            # Pagination variables
            batch_size = 100
            offset = 0
            latest_update_time = None
            all_assignments = []

            # Paginate through all assignments
            while True:
                response = await self.servicenow_datasource.get_now_table_tableName(
                    tableName="sys_user_has_role",
                    sysparm_query=query,
                    sysparm_fields="sys_id,user,role,state,inherited,sys_updated_on",
                    sysparm_limit=str(batch_size),
                    sysparm_offset=str(offset),
                    sysparm_display_value="false",
                    sysparm_no_count="true",
                    sysparm_exclude_reference_link="true",
                )

                if not response.success or not response.data:
                    if response.error:
                        self.logger.error(f"❌ API error: {response.error}")
                    break

                assignments_data = response.data.get("result", [])

                if not assignments_data:
                    break

                # Extract and validate assignments
                for assignment in assignments_data:
                    try:
                        updated_on = assignment.get("sys_updated_on")
                        if updated_on and (
                            not latest_update_time or updated_on > latest_update_time
                        ):
                            latest_update_time = updated_on

                        # Extract sys_ids from reference fields
                        user_ref = assignment.get("user", {})
                        role_ref = assignment.get("role", {})

                        user_sys_id = (
                            user_ref.get("value")
                            if isinstance(user_ref, dict)
                            else user_ref
                        )
                        role_sys_id = (
                            role_ref.get("value")
                            if isinstance(role_ref, dict)
                            else role_ref
                        )

                        if not user_sys_id or not role_sys_id:
                            self.logger.warning(
                                f"Invalid assignment {assignment.get('sys_id')}: "
                                f"user={user_sys_id}, role={role_sys_id}"
                            )
                            continue

                        all_assignments.append(
                            {"user_sys_id": user_sys_id, "role_sys_id": role_sys_id}
                        )

                    except Exception as e:
                        self.logger.error(
                            f"Error processing assignment {assignment.get('sys_id')}: {e}"
                        )
                        continue

                offset += batch_size

                if len(assignments_data) < batch_size:
                    break

            # Create user-role membership edges
            if all_assignments:
                async with self.data_store_provider.transaction() as tx_store:
                    membership_count = 0

                    for assignment in all_assignments:
                        success = await self.data_entities_processor.create_user_group_membership(
                            assignment["user_sys_id"],
                            assignment["role_sys_id"],
                            Connectors.SERVICENOWKB,
                            tx_store,
                        )

                        if success:
                            membership_count += 1

                    self.logger.info(
                        f"Created {membership_count} user-role membership edges"
                    )

            # Save checkpoint
            if latest_update_time:
                await self.role_assignment_sync_point.update_sync_point(
                    "role_assignments", {"last_sync_time": latest_update_time}
                )

            self.logger.info(
                f"✅ User-role assignment sync complete. Total processed: {len(all_assignments)}"
            )

        except Exception as e:
            self.logger.error(
                f"❌ User-role assignment sync failed: {e}", exc_info=True
            )
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
            has_parent = config["has_parent"]

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

            # Collect parent-child relationships for second pass (commented out for now)
            parent_child_relationships = []

            # PASS 1: Fetch and create all entity nodes
            while True:
                response = await self.servicenow_datasource.get_now_table_tableName(
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

                # Collect parent-child relationships (for future use)
                if has_parent:
                    for entity_data in entities_data:
                        parent_ref = entity_data.get("parent")
                        if parent_ref:
                            parent_sys_id = None
                            if isinstance(parent_ref, dict):
                                parent_sys_id = parent_ref.get("value")
                            elif isinstance(parent_ref, str) and parent_ref:
                                parent_sys_id = parent_ref

                            if parent_sys_id:
                                child_sys_id = entity_data.get("sys_id")
                                parent_child_relationships.append({
                                    "child_sys_id": child_sys_id,
                                    "parent_sys_id": parent_sys_id,
                                })

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

            # PASS 2: Create hierarchy edges - COMMENTED OUT FOR NOW
            # TODO: Uncomment when we have proper edge type for organizational hierarchy
            # The current create_user_group_hierarchy creates "belongsTo" edges,
            # but we need parent-child hierarchy edges instead
            """
            if parent_child_relationships:
                async with self.data_store_provider.transaction() as tx_store:
                    success_count = 0
                    for relationship in parent_child_relationships:
                        success = await self.data_entities_processor.create_user_group_hierarchy(
                            relationship["child_sys_id"],
                            relationship["parent_sys_id"],
                            Connectors.SERVICENOWKB,
                            tx_store,
                        )
                        if success:
                            success_count += 1

                    if success_count > 0:
                        self.logger.info(
                            f"Created {success_count} {entity_type} hierarchy edges"
                        )
            """

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

    async def _sync_knowledge_bases(self, user_sys_id: str, user_email: str) -> None:
        """
        Sync knowledge bases from ServiceNow kb_knowledge_base table using offset-based pagination.

        Creates:
        - RecordGroup nodes (type=SERVICENOWKB) in recordGroups collection
        - OWNER edges: owner → KB RecordGroup
        - WRITER edges: kb_managers → KB RecordGroup
        - READ edge: current user → KB RecordGroup (implicit permission)

        First sync: Fetches all KBs
        Subsequent syncs: Only fetches KBs modified since last sync

        Args:
            user_sys_id: ServiceNow user sys_id for impersonation
            user_email: Platform user email for permission tracking
        """
        try:
            # Get per-user sync checkpoint for delta sync
            sync_point_key = f"kb_{user_email}"
            last_sync_data = await self.kb_sync_point.read_sync_point(sync_point_key)
            last_sync_time = (last_sync_data.get("last_sync_time") if last_sync_data else None)

            if last_sync_time:
                self.logger.info(f"🔄 Delta sync: Fetching KBs updated after {last_sync_time}")
                query = f"sys_updated_on>{last_sync_time}^ORDERBYsys_updated_on"
            else:
                self.logger.info("🆕 Full sync: Fetching all knowledge bases (first time)")
                query = "ORDERBYsys_updated_on"

            # Pagination variables
            batch_size = 100
            offset = 0
            total_synced = 0
            latest_update_time = None

            # Paginate through all KBs
            while True:
                # Fetch KBs from ServiceNow WITH IMPERSONATION
                response = await self.servicenow_datasource.get_now_table_tableName(
                    tableName="kb_knowledge_base",
                    sysparm_query=query,
                    sysparm_fields="sys_id,title,description,owner,kb_managers,active,sys_created_on,sys_updated_on",
                    sysparm_limit=str(batch_size),
                    sysparm_offset=str(offset),
                    sysparm_display_value="false",
                    sysparm_no_count="true",
                    sysparm_exclude_reference_link="true",
                    impersonate_user=user_sys_id,
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

                            # Check if KB already exists
                            existing_kb = await tx_store.get_record_group_by_external_id(
                                Connectors.SERVICENOWKB,
                                kb_sys_id
                            )

                            if existing_kb:
                                # KB exists: Only add current user's permission edge
                                current_user_permission = Permission(
                                    email=user_email,
                                    type=PermissionType.READ,
                                    entity_type=EntityType.USER,
                                )

                                await self.data_entities_processor.batch_upsert_record_group_permissions(
                                    existing_kb.id,
                                    [current_user_permission],
                                    Connectors.SERVICENOWKB,
                                    tx_store
                                )

                                self.logger.debug(f"Added permission for {user_email} to existing KB {kb_sys_id}")
                            else:
                                # KB doesn't exist: Create it with all permissions
                                # Save KB RecordGroup
                                await tx_store.batch_upsert_record_groups([kb_record_group])

                                kb_permissions = []

                                # Fetch criteria IDs for this KB
                                criteria_map = await self._fetch_kb_permissions_from_criteria(kb_sys_id)

                                # Collect all unique criteria IDs
                                all_criteria_ids = set()
                                all_criteria_ids.update(criteria_map["read"])
                                all_criteria_ids.update(criteria_map["write"])

                                # Batch fetch all user_criteria details
                                criteria_details_map = {}
                                if all_criteria_ids:
                                    criteria_query = f"sys_idIN{','.join(all_criteria_ids)}"

                                    criteria_response = await self.servicenow_datasource.get_now_table_tableName(
                                        tableName="user_criteria",
                                        sysparm_query=criteria_query,
                                        sysparm_fields="sys_id,user,group,role,department,location,company,cost_center",
                                        sysparm_display_value="false",
                                        sysparm_exclude_reference_link="true",
                                        impersonate_user=user_sys_id,
                                    )

                                    if criteria_response.success and criteria_response.data:
                                        for criteria_record in criteria_response.data.get("result", []):
                                            criteria_sys_id = criteria_record.get("sys_id")
                                            if criteria_sys_id:
                                                criteria_details_map[criteria_sys_id] = criteria_record

                                # Extract READ permissions from criteria
                                for criteria_id in criteria_map["read"]:
                                    criteria_details = criteria_details_map.get(criteria_id)
                                    if criteria_details:
                                        read_perms = await self._extract_permissions_from_user_criteria_details(
                                            criteria_details,
                                            PermissionType.READ
                                        )
                                        kb_permissions.extend(read_perms)

                                # Extract WRITE permissions from criteria
                                for criteria_id in criteria_map["write"]:
                                    criteria_details = criteria_details_map.get(criteria_id)
                                    if criteria_details:
                                        write_perms = await self._extract_permissions_from_user_criteria_details(
                                            criteria_details,
                                            PermissionType.WRITE
                                        )
                                        kb_permissions.extend(write_perms)

                                # Add OWNER permission (fallback from owner field)
                                owner_ref = kb_data.get("owner")
                                if owner_ref:
                                    owner_sys_id = None
                                    if isinstance(owner_ref, dict):
                                        owner_sys_id = owner_ref.get("value")
                                    elif isinstance(owner_ref, str) and owner_ref:
                                        owner_sys_id = owner_ref

                                    if owner_sys_id:
                                        kb_permissions.append({
                                            "entity_type": EntityType.USER.value,
                                            "source_sys_id": owner_sys_id,
                                            "role": PermissionType.OWNER.value,
                                        })

                                # Add current user's implicit READ permission
                                current_user_permission = Permission(
                                    email=user_email,
                                    type=PermissionType.READ,
                                    entity_type=EntityType.USER,
                                )

                                # Convert to Permission objects and save
                                permission_objects = []
                                if kb_permissions:
                                    permission_objects = await self._convert_permissions_to_objects(
                                        kb_permissions,
                                        tx_store
                                    )

                                # Add current user to permission objects
                                permission_objects.append(current_user_permission)

                                if permission_objects:
                                    await self.data_entities_processor.batch_upsert_record_group_permissions(
                                        kb_record_group.id,
                                        permission_objects,
                                        Connectors.SERVICENOWKB,
                                        tx_store
                                    )

                                    self.logger.debug(
                                        f"Created KB {kb_sys_id} with {len(permission_objects)} permissions (including {user_email})"
                                    )

                    total_synced += len(kb_record_groups)

                # Move to next page
                offset += batch_size

                # If this page has fewer records than batch_size, we're done
                if len(kbs_data) < batch_size:
                    break

            # Save checkpoint for next sync (per-user)
            if latest_update_time:
                await self.kb_sync_point.update_sync_point(sync_point_key, {"last_sync_time": latest_update_time})

            self.logger.debug(f"User {user_email}: Knowledge base sync complete, Total synced: {total_synced}")

        except Exception as e:
            self.logger.error(f"❌ Error syncing knowledge bases: {e}", exc_info=True)
            raise

    async def _sync_categories(self, user_sys_id: str, user_email: str) -> None:
        """
        Sync categories from ServiceNow kb_category table using two-pass approach.

        Creates:
        - RecordGroup nodes (type=SERVICENOW_CATEGORY) in recordGroups collection
        - PARENT_CHILD edges in recordRelations collection

        Pass 1: Fetch and save all category RecordGroups
        Pass 2: Create hierarchy edges (category → parent KB/Category)

        First sync: Fetches all categories
        Subsequent syncs: Only fetches categories modified since last sync

        Args:
            user_sys_id: ServiceNow user sys_id for impersonation
            user_email: Platform user email for tracking
        """
        try:
            # Get per-user sync checkpoint for delta sync
            sync_point_key = f"category_{user_email}"
            last_sync_data = await self.category_sync_point.read_sync_point(sync_point_key)
            last_sync_time = (last_sync_data.get("last_sync_time") if last_sync_data else None)

            if last_sync_time:
                self.logger.info(f"🔄 Delta sync: Fetching categories updated after {last_sync_time}")
                query = f"sys_updated_on>{last_sync_time}^ORDERBYsys_updated_on"
            else:
                self.logger.info("🆕 Full sync: Fetching all categories (first time)")
                query = "ORDERBYsys_updated_on"

            # Pagination variables
            batch_size = 100
            offset = 0
            total_synced = 0
            latest_update_time = None

            # Collect all categories for Pass 2
            all_categories_data = []

            # Pass 1: Fetching and saving category nodes
            while True:
                # Fetch categories from ServiceNow
                response = await self.servicenow_datasource.get_now_table_tableName(
                    tableName="kb_category",
                    sysparm_query=query,
                    sysparm_fields="sys_id,label,value,parent_table,parent_id,kb_knowledge_base,active,sys_created_on,sys_updated_on",
                    sysparm_limit=str(batch_size),
                    sysparm_offset=str(offset),
                    sysparm_display_value="false",
                    sysparm_no_count="true",
                    sysparm_exclude_reference_link="true",
                    impersonate_user=user_sys_id,
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

                # Store for Pass 2
                all_categories_data.extend(categories_data)

                # Track the latest update timestamp for checkpoint
                if categories_data:
                    latest_update_time = categories_data[-1].get("sys_updated_on")

                # Transform categories to RecordGroups
                category_record_groups = []
                for cat_data in categories_data:
                    category_rg = self._transform_to_category_record_group(cat_data)
                    if category_rg:
                        category_record_groups.append(category_rg)

                # Save category RecordGroups (nodes only, no edges yet)
                if category_record_groups:
                    async with self.data_store_provider.transaction() as tx_store:
                        await tx_store.batch_upsert_record_groups(category_record_groups)

                    total_synced += len(category_record_groups)

                # Move to next page
                offset += batch_size

                # If this page has fewer records than batch_size, we're done
                if len(categories_data) < batch_size:
                    break

            # PASS 2: Create hierarchy edges using processor method
            if all_categories_data:
                async with self.data_store_provider.transaction() as tx_store:
                    success_count = 0
                    for cat_data in all_categories_data:
                        category_sys_id = cat_data.get("sys_id")
                        parent_table = cat_data.get("parent_table")
                        parent_id_ref = cat_data.get("parent_id")

                        # Extract parent sys_id from reference field
                        parent_sys_id = None
                        if isinstance(parent_id_ref, dict):
                            parent_sys_id = parent_id_ref.get("value")
                        elif isinstance(parent_id_ref, str) and parent_id_ref:
                            parent_sys_id = parent_id_ref

                        # Skip if no parent (root level category)
                        if not parent_sys_id or not parent_table:
                            continue

                        # Determine parent group type based on parent_table
                        if parent_table == "kb_knowledge_base":
                            parent_group_type = RecordGroupType.SERVICENOWKB
                        elif parent_table == "kb_category":
                            parent_group_type = RecordGroupType.SERVICENOW_CATEGORY
                        else:
                            self.logger.warning(
                                f"Unknown parent_table type: {parent_table} for category {category_sys_id}"
                            )
                            continue

                        # Create hierarchy edge using processor method
                        success = await self.data_entities_processor.create_record_group_hierarchy(
                            category_sys_id,
                            parent_sys_id,
                            RecordGroupType.SERVICENOW_CATEGORY,
                            parent_group_type,
                            Connectors.SERVICENOWKB,
                            tx_store
                        )

                        if success:
                            success_count += 1

                    if success_count > 0:
                        self.logger.info(f"Created {success_count} category hierarchy edges")
                    else:
                        self.logger.info("No hierarchy edges to create")

            # Update sync checkpoint (per-user)
            if latest_update_time:
                await self.category_sync_point.create_sync_point(sync_point_key, {"last_sync_time": latest_update_time})

            self.logger.debug(f"User {user_email}: Categories synced: {total_synced} total")

        except Exception as e:
            self.logger.error(f"❌ Error syncing categories: {e}", exc_info=True)
            raise

    async def _load_role_name_mapping_from_db(self) -> None:
        """
        Load role name to sys_id mapping from database.

        Queries existing role-based usergroups (with ROLE_ prefix) and builds
        an in-memory mapping for quick lookups during permission extraction.

        This is called before article sync to enable role-based permission creation.
        """
        self.role_name_to_id_map = {}

        try:
            # Get all usergroups for this connector from database
            async with self.data_store_provider.transaction() as tx_store:
                all_groups = await tx_store.get_user_groups(
                    app_name=Connectors.SERVICENOWKB,
                    org_id=self.data_entities_processor.org_id,
                )

            # Filter to role-based groups and build mapping
            for group in all_groups:
                if group.name and group.name.startswith("ROLE_"):
                    # Extract role name: "ROLE_admin" -> "admin"
                    role_name = group.name.replace("ROLE_", "", 1)
                    role_sys_id = group.source_user_group_id
                    self.role_name_to_id_map[role_name] = role_sys_id

            self.logger.info(
                f"Loaded {len(self.role_name_to_id_map)} role name mappings from database"
            )

        except Exception as e:
            self.logger.error(f"Failed to load role name mappings: {e}")
            # Continue with empty map - role permissions will be skipped

    async def _sync_articles(self, user_sys_id: str, user_email: str) -> None:
        """
        Sync KB articles and attachments from ServiceNow using batch processing.

        Flow:
        1. Fetch 100 articles in a batch
        2. Batch fetch user_criteria for all articles (efficiency)
        3. For each article:
           - Check if article exists
           - If exists: Add current user permission edge
           - If new: Create WebpageRecord + fetch attachments + create all edges + permissions
        4. Update checkpoint after batch

        API Endpoints:
        - /api/now/table/kb_knowledge - Articles
        - /api/now/table/user_criteria - Permissions
        - /api/now/attachment - Attachments

        Args:
            user_sys_id: ServiceNow user sys_id for impersonation
            user_email: Platform user email for permission tracking
        """
        try:
            # Load role name mapping from database (needed for permission extraction)
            await self._load_role_name_mapping_from_db()

            # Get per-user sync checkpoint
            sync_point_key = f"article_{user_email}"
            last_sync_data = await self.article_sync_point.read_sync_point(sync_point_key)
            last_sync_time = (last_sync_data.get("last_sync_time") if last_sync_data else None)

            if last_sync_time:
                self.logger.info(f"🔄 Delta sync: Fetching articles updated after {last_sync_time}")
                query = f"sys_updated_on>{last_sync_time}^ORDERBYsys_updated_on"
            else:
                self.logger.info("🆕 Full sync: Fetching all articles (first time)")
                query = "ORDERBYsys_updated_on"

            # Pagination variables
            batch_size = 100
            offset = 0
            total_articles_synced = 0
            total_attachments_synced = 0
            latest_update_time = None

            # Paginate through all articles
            while True:
                # Fetch batch of 100 articles
                response = await self.servicenow_datasource.get_now_table_tableName(
                    tableName="kb_knowledge",
                    sysparm_query=query,
                    sysparm_fields="sys_id,number,short_description,text,author,kb_knowledge_base,kb_category,workflow_state,active,published,can_read_user_criteria,sys_created_on,sys_updated_on",
                    sysparm_limit=str(batch_size),
                    sysparm_offset=str(offset),
                    sysparm_display_value="false",
                    sysparm_no_count="true",
                    sysparm_exclude_reference_link="true",
                    impersonate_user=user_sys_id,
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

                # Collect all user_criteria sys_ids from this batch
                all_criteria_ids = set()
                for article_data in articles_data:
                    can_read = article_data.get("can_read_user_criteria", "")
                    if can_read:
                        # Split comma-separated sys_ids
                        criteria_ids = [c.strip() for c in can_read.split(",") if c.strip()]
                        all_criteria_ids.update(criteria_ids)

                # Batch fetch user_criteria for entire batch (efficiency!)
                criteria_map = {}
                if all_criteria_ids:
                    criteria_map = await self._fetch_user_criteria_batch(all_criteria_ids, user_sys_id)

                # Collect RecordUpdates for this batch
                record_updates = []

                for article_data in articles_data:
                    try:
                        updates = await self._process_single_article(article_data, criteria_map, user_sys_id, user_email)
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

            # Update sync checkpoint (per-user)
            if latest_update_time:
                await self.article_sync_point.update_sync_point(sync_point_key, {"last_sync_time": latest_update_time})
                self.logger.debug(f"Checkpoint updated: {latest_update_time}")

            self.logger.debug(f"User {user_email}: Articles synced: {total_articles_synced} articles, {total_attachments_synced} attachments")

        except Exception as e:
            self.logger.error(f"❌ Error syncing articles: {e}", exc_info=True)
            raise

    async def _process_single_article(
        self, article_data: Dict[str, Any], criteria_map: Dict[str, Dict], user_sys_id: str, user_email: str
    ) -> List[RecordUpdate]:
        """
        Process a single article and return RecordUpdate objects for article + attachments.

        Args:
            article_data: ServiceNow kb_knowledge record
            criteria_map: Pre-fetched user_criteria mapping
            user_sys_id: ServiceNow user sys_id for impersonation
            user_email: Platform user email for permission tracking

        Returns:
            List[RecordUpdate]: RecordUpdate for article + RecordUpdates for attachments
        """
        try:
            article_sys_id = article_data.get("sys_id")
            article_title = article_data.get("short_description", "Unknown")

            self.logger.debug(f"Processing article: {article_title} ({article_sys_id})")

            record_updates = []

            # Check if article already exists
            existing_article = None
            async with self.data_store_provider.transaction() as tx_store:
                existing_article = await tx_store.get_record_by_external_id(
                    Connectors.SERVICENOWKB,
                    article_sys_id
                )

            # Transform article to WebpageRecord
            article_record = self._transform_to_article_webpage_record(article_data)
            if not article_record:
                self.logger.warning(f"Failed to transform article {article_sys_id}")
                return []

            # Fetch attachments for this article
            attachments_data = await self._fetch_attachments_for_article(article_sys_id, user_sys_id)

            # Extract article permissions from user_criteria
            article_permissions = await self._extract_article_permissions(article_data, criteria_map)

            # Add current user's implicit READ permission
            current_user_permission = Permission(
                email=user_email,
                type=PermissionType.READ,
                entity_type=EntityType.USER,
            )

            # Convert permissions to Permission objects
            async with self.data_store_provider.transaction() as tx_store:
                all_permission_objects = await self._convert_permissions_to_objects(article_permissions, tx_store)

            # Add current user to permissions
            all_permission_objects.append(current_user_permission)

            # Create RecordUpdate for article
            article_update = RecordUpdate(
                record=article_record,
                is_new=(existing_article is None),
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

                # Check if attachment exists
                existing_attachment = None
                async with self.data_store_provider.transaction() as tx_store:
                    existing_attachment = await tx_store.get_record_by_external_id(
                        Connectors.SERVICENOWKB,
                        att_sys_id
                    )

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
                        is_new=(existing_attachment is None),
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

    async def _fetch_user_criteria_batch(
        self, criteria_sys_ids: set, user_sys_id: str
    ) -> Dict[str, Dict]:
        """
        Batch fetch user_criteria records from ServiceNow.

        Args:
            criteria_sys_ids: Set of user_criteria sys_ids
            user_sys_id: ServiceNow user sys_id for impersonation

        Returns:
            Dict mapping criteria_sys_id to {users: [...], groups: [...]}
        """
        try:
            if not criteria_sys_ids:
                return {}

            # Build IN query: sys_idINid1,id2,id3
            criteria_ids_str = ",".join(criteria_sys_ids)
            query = f"sys_idIN{criteria_ids_str}"

            response = await self.servicenow_datasource.get_now_table_tableName(
                tableName="user_criteria",
                sysparm_query=query,
                sysparm_fields="sys_id,users,groups,roles",
                sysparm_display_value="false",
                sysparm_no_count="true",
                sysparm_exclude_reference_link="true",
                impersonate_user=user_sys_id,
            )

            if not response.success or not response.data:
                self.logger.warning("Failed to fetch user_criteria")
                return {}

            # Build map: criteria_sys_id -> {users: [...], groups: [...]}
            criteria_map = {}
            for criteria_data in response.data.get("result", []):
                sys_id = criteria_data.get("sys_id")
                users_str = criteria_data.get("users", "")
                groups_str = criteria_data.get("groups", "")
                roles = criteria_data.get("roles", "")

                # Parse comma-separated lists
                users = [u.strip() for u in users_str.split(",") if u.strip()]
                groups = [g.strip() for g in groups_str.split(",") if g.strip()]
                roles = [r.strip() for r in roles.split(",") if r.strip()]

                criteria_map[sys_id] = {
                    "users": users,
                    "groups": groups,
                    "roles": roles,
                }

            return criteria_map

        except Exception as e:
            self.logger.error(f"Failed to fetch user_criteria: {e}", exc_info=True)
            return {}

    async def _fetch_attachments_for_article(
        self, article_sys_id: str, user_sys_id: str
    ) -> List[Dict[str, Any]]:
        """
        Fetch all attachments for a single article.

        Args:
            article_sys_id: Article sys_id
            user_sys_id: ServiceNow user sys_id for impersonation

        Returns:
            List of attachment data dictionaries
        """
        try:
            # Query: table_name=kb_knowledge^table_sys_id={article_sys_id}
            query = f"table_name=kb_knowledge^table_sys_id={article_sys_id}"

            response = await self.servicenow_datasource.get_now_table_tableName(
                tableName="sys_attachment",
                sysparm_query=query,
                sysparm_fields="sys_id,file_name,content_type,size_bytes,table_sys_id,sys_created_on,sys_updated_on",
                sysparm_display_value="false",
                sysparm_no_count="true",
                sysparm_exclude_reference_link="true",
                impersonate_user=user_sys_id,
            )

            if not response.success or not response.data:
                return []

            return response.data.get("result", [])

        except Exception as e:
            self.logger.warning(f"Failed to fetch attachments for article {article_sys_id}: {e}")
            return []

    def _extract_roles_from_user_criteria(
        self, criteria: Dict[str, Any], permission_type: PermissionType
    ) -> List[Dict[str, Any]]:
        """
        Extract role-based permissions from a user_criteria record.

        Args:
            criteria: User criteria dictionary containing roles list
            permission_type: Type of permission (READER, WRITER, etc.)

        Returns:
            List of permission dictionaries for roles
        """
        permissions = []

        # Get roles from criteria (these are role names, not sys_ids)
        role_names = criteria.get("roles", [])

        if not role_names:
            return permissions

        for role_name in role_names:
            # Look up role sys_id from name using our mapping
            role_sys_id = self.role_name_to_id_map.get(role_name)

            if role_sys_id:
                permissions.append(
                    {
                        "entity_type": EntityType.GROUP.value,
                        "source_sys_id": role_sys_id,
                        "role": permission_type.value,
                    }
                )
            else:
                self.logger.warning(
                    f"Role '{role_name}' referenced in user_criteria but not found in database. "
                    f"Role may need to be synced first."
                )

        return permissions

    async def _extract_article_permissions(
        self, article_data: Dict[str, Any], criteria_map: Dict[str, Dict]
    ) -> List[Dict[str, Any]]:
        """
        Extract all permissions for an article.

        Returns list of permission dictionaries:
        [
            {
                "entity_type": "USER" or "GROUP",
                "source_sys_id": ServiceNow sys_id,
                "role": "OWNER" or "READER"
            },
            ...
        ]
        """
        permissions = []

        try:
            # 1. Author → OWNER permission
            author_ref = article_data.get("author")
            if author_ref:
                author_sys_id = None
                if isinstance(author_ref, dict):
                    author_sys_id = author_ref.get("value")
                elif isinstance(author_ref, str) and author_ref:
                    author_sys_id = author_ref

                if author_sys_id:
                    permissions.append(
                        {
                            "entity_type": "USER",
                            "source_sys_id": author_sys_id,
                            "role": PermissionType.OWNER.value,
                        }
                    )

            # 2. can_read_user_criteria → READER permissions
            can_read = article_data.get("can_read_user_criteria", "")
            if can_read:
                criteria_ids = [c.strip() for c in can_read.split(",") if c.strip()]

                for criteria_id in criteria_ids:
                    criteria_data = criteria_map.get(criteria_id)
                    if not criteria_data:
                        continue

                    # Add user permissions
                    for user_sys_id in criteria_data.get("users", []):
                        permissions.append(
                            {
                                "entity_type": EntityType.USER.value,
                                "source_sys_id": user_sys_id,
                                "role": PermissionType.READ.value,
                            }
                        )

                    # Add group permissions
                    for group_sys_id in criteria_data.get("groups", []):
                        permissions.append(
                            {
                                "entity_type": EntityType.GROUP.value,
                                "source_sys_id": group_sys_id,
                                "role": PermissionType.READ.value,
                            }
                        )

                    # Add role permissions
                    role_permissions = self._extract_roles_from_user_criteria(
                        criteria_data, PermissionType.READ
                    )
                    permissions.extend(role_permissions)

            return permissions

        except Exception as e:
            self.logger.warning(f"Failed to extract permissions for article {article_data.get('sys_id')}: {e}")
            return permissions

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

                if entity_type == "USER":
                    # Use processor helper to get user by source_sys_id
                    user = await self.data_entities_processor.get_user_by_source_id(
                        source_sys_id,
                        Connectors.SERVICENOWKB,
                        tx_store
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

                elif entity_type == "GROUP":
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
            read_response = await self.servicenow_datasource.get_now_table_tableName(
                tableName="kb_uc_can_read_mtom",
                sysparm_query=f"kb_knowledge_base={kb_sys_id}",
                sysparm_fields="user_criteria",
                sysparm_display_value="false",
                sysparm_exclude_reference_link="true",
            )

            if read_response.success and read_response.data:
                for record in read_response.data.get("result", []):
                    criteria_ref = record.get("user_criteria")
                    criteria_id = None

                    if isinstance(criteria_ref, dict):
                        criteria_id = criteria_ref.get("value")
                    elif isinstance(criteria_ref, str) and criteria_ref:
                        criteria_id = criteria_ref

                    if criteria_id:
                        criteria_map["read"].append(criteria_id)

            # Fetch WRITE criteria (contribute)
            write_response = await self.servicenow_datasource.get_now_table_tableName(
                tableName="kb_uc_can_contribute_mtom",
                sysparm_query=f"kb_knowledge_base={kb_sys_id}",
                sysparm_fields="user_criteria",
                sysparm_display_value="false",
                sysparm_exclude_reference_link="true",
            )

            if write_response.success and write_response.data:
                for record in write_response.data.get("result", []):
                    criteria_ref = record.get("user_criteria")
                    criteria_id = None

                    if isinstance(criteria_ref, dict):
                        criteria_id = criteria_ref.get("value")
                    elif isinstance(criteria_ref, str) and criteria_ref:
                        criteria_id = criteria_ref

                    if criteria_id:
                        criteria_map["write"].append(criteria_id)

            self.logger.debug(
                f"KB {kb_sys_id} criteria: {len(criteria_map['read'])} read, "
                f"{len(criteria_map['write'])} write"
            )

            return criteria_map

        except Exception as e:
            self.logger.error(
                f"Failed to fetch KB permissions for {kb_sys_id}: {e}",
                exc_info=True
            )
            return {"read": [], "write": []}

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
            role_names = parse_sys_ids(criteria_details.get("role"))
            for role_name in role_names:
                # Lookup role sys_id from name
                role_sys_id = self.role_name_to_id_map.get(role_name)

                if role_sys_id:
                    permissions.append({
                        "entity_type": EntityType.GROUP.value,  # Roles are stored as groups
                        "source_sys_id": role_sys_id,
                        "role": permission_type.value,
                    })
                else:
                    self.logger.warning(
                        f"Role '{role_name}' in user_criteria not found in role mapping"
                    )

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

            # Check if user exists in platform database (by email)
            is_active = False
            try:
                async with self.data_store_provider.transaction() as tx_store:
                    user_in_db = await tx_store.get_user_by_email(email)
                    is_active = user_in_db is not None
            except Exception as e:
                self.logger.warning(f"Could not check if user {sys_id} exists in platform: {e}")

            # Parse timestamps
            source_created_at = None
            source_updated_at = None
            if user_data.get("sys_created_on"):
                source_created_at = self._parse_servicenow_datetime(user_data["sys_created_on"])
            if user_data.get("sys_updated_on"):
                source_updated_at = self._parse_servicenow_datetime(user_data["sys_updated_on"])

            app_user = AppUser(
                app_name=Connectors.SERVICENOWKB,
                source_user_id=sys_id,
                org_id=self.data_entities_processor.org_id,
                email=email,
                full_name=full_name,
                is_active=is_active,
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
                app_name=Connectors.SERVICENOWKB,
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
                app_name=Connectors.SERVICENOWKB,
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
            RecordGroup: Transformed KB as RecordGroup with type SERVICENOWKB or None if invalid
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
                connector_name=Connectors.SERVICENOWKB,
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
                external_group_id=sys_id,
                connector_name=Connectors.SERVICENOWKB,
                group_type=RecordGroupType.SERVICENOW_CATEGORY,
                web_url=web_url,
                source_created_at=source_created_at,
                source_updated_at=source_updated_at,
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
                connector_name=Connectors.SERVICENOWKB,
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
                connector_name=Connectors.SERVICENOWKB,
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
    ) -> "ServiceNowKBConnector":
        """
        Factory method to create and initialize the connector.

        Args:
            logger: Logger instance
            data_store_provider: Data store provider
            config_service: Configuration service

        Returns:
            ServiceNowKBConnector: Initialized connector instance
        """
        data_entities_processor = DataSourceEntitiesProcessor(
            logger, data_store_provider, config_service
        )
        await data_entities_processor.initialize()

        return cls(logger, data_entities_processor, data_store_provider, config_service)
