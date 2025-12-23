"""
Notion Connector

Authentication: OAuth 2.0
"""

import asyncio
from logging import Logger
from typing import Any, Dict, List, Optional

from fastapi import HTTPException
from fastapi.responses import StreamingResponse

from app.config.configuration_service import ConfigurationService
from app.config.constants.arangodb import Connectors
from app.connectors.core.base.connector.connector_service import BaseConnector
from app.connectors.core.base.data_processor.data_source_entities_processor import (
    DataSourceEntitiesProcessor,
)
from app.connectors.core.base.data_store.data_store import DataStoreProvider
from app.connectors.core.base.sync_point.sync_point import SyncDataPointType, SyncPoint
from app.connectors.core.registry.connector_builder import (
    CommonFields,
    ConnectorBuilder,
    DocumentationLink,
)
from app.connectors.core.registry.filters import (
    FilterCollection,
    load_connector_filters,
)
from app.connectors.sources.notion.common.apps import NotionApp
from app.models.entities import AppUser, Record
from app.sources.client.notion.notion import NotionClient
from app.sources.external.notion.notion import NotionDataSource

# Notion OAuth URLs
# Note: Notion OAuth doesn't use traditional scopes. Permissions are configured
# when creating the integration in Notion's developer portal. The scope parameter
# below is a placeholder to satisfy the OAuth validator.
AUTHORIZE_URL = "https://api.notion.com/v1/oauth/authorize"
TOKEN_URL = "https://api.notion.com/v1/oauth/token"
HTTP_STATUS_200 = 200


@ConnectorBuilder("Notion")\
    .in_group("Notion")\
    .with_auth_type("OAUTH")\
    .with_description("Sync pages, databases, and users from Notion")\
    .with_categories(["Knowledge Management", "Collaboration"])\
    .configure(lambda builder: builder
        .with_icon("/assets/icons/connectors/notion.svg")
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
        .with_redirect_uri("connectors/oauth/callback/Notion", True)
        .with_oauth_urls(AUTHORIZE_URL, TOKEN_URL, ["read"])
        .add_auth_field(CommonFields.client_id("Notion OAuth App"))
        .add_auth_field(CommonFields.client_secret("Notion OAuth App"))
        .with_sync_strategies(["SCHEDULED", "MANUAL"])
        .with_scheduled_config(True, 60)
    )\
    .build_decorator()
class NotionConnector(BaseConnector):
    def __init__(
        self,
        logger: Logger,
        data_entities_processor: DataSourceEntitiesProcessor,
        data_store_provider: DataStoreProvider,
        config_service: ConfigurationService,
    ) -> None:
        """Initialize the Notion connector."""
        super().__init__(
            NotionApp(),
            logger,
            data_entities_processor,
            data_store_provider,
            config_service
        )

        # Client instances
        self.notion_client: Optional[NotionClient] = None
        self.data_source: Optional[NotionDataSource] = None

        # Initialize sync points for incremental sync
        def _create_sync_point(sync_data_point_type: SyncDataPointType) -> SyncPoint:
            return SyncPoint(
                connector_name=self.connector_name,
                org_id=self.data_entities_processor.org_id,
                sync_data_point_type=sync_data_point_type,
                data_store_provider=self.data_store_provider,
            )

        self.pages_sync_point = _create_sync_point(SyncDataPointType.RECORDS)

        self.sync_filters: FilterCollection = FilterCollection()
        self.indexing_filters: FilterCollection = FilterCollection()

    async def init(self) -> bool:
        """Initialize the Notion connector with credentials and client."""
        try:
            self.logger.info("🔧 Initializing Notion Connector...")

            # Build client from services
            self.notion_client = await NotionClient.build_from_services(
                logger=self.logger,
                config_service=self.config_service,
            )

            # Initialize data source
            self.data_source = NotionDataSource(self.notion_client)

            # Load filters
            self.sync_filters, self.indexing_filters = await load_connector_filters(
                self.config_service, "notion", self.logger
            )

            # Test connection
            if not await self.test_connection_and_access():
                self.logger.error("❌ Notion connector connection test failed")
                return False

            self.logger.info("✅ Notion connector initialized successfully")
            return True

        except Exception as e:
            self.logger.error(f"❌ Failed to initialize Notion connector: {e}", exc_info=True)
            return False

    async def test_connection_and_access(self) -> bool:
        """Test connection and access to Notion API."""
        try:
            if not self.notion_client:
                self.logger.error("Notion client not initialized")
                return False

            datasource = await self._get_fresh_datasource()
            response = await datasource.retrieve_bot_user()

            if not response or not response.success:
                self.logger.error(f"Connection test failed: {response.error if response else 'No response'}")
                return False

            self.logger.info("✅ Notion connector connection test passed")
            return True

        except Exception as e:
            self.logger.error(f"Connection test failed: {e}", exc_info=True)
            return False

    async def _get_fresh_datasource(self) -> NotionDataSource:
        """
        Get NotionDataSource with ALWAYS-FRESH access token.

        This method:
        1. Fetches current OAuth token from config
        2. Compares with existing client's token
        3. Updates client ONLY if token changed (mutation)
        4. Returns datasource with current token

        Returns:
            NotionDataSource with current valid token
        """
        if not self.notion_client:
            raise Exception("Notion client not initialized. Call init() first.")

        # Fetch current config from etcd (async I/O)
        config = await self.config_service.get_config("/services/connectors/notion/config")

        if not config:
            raise Exception("Notion configuration not found")

        # Extract fresh OAuth access token from credentials section
        credentials = config.get("credentials", {}) or {}
        fresh_token = credentials.get("access_token", "")

        if not fresh_token:
            raise Exception("No OAuth access token available")

        # Get current token from client
        internal_client = self.notion_client.get_client()
        current_token = internal_client.access_token

        # Update client's token if it changed (mutation)
        if current_token != fresh_token:
            self.logger.debug("🔄 Updating client with refreshed access token")
            internal_client.access_token = fresh_token
            internal_client.headers["Authorization"] = f"Bearer {fresh_token}"

        return NotionDataSource(self.notion_client)

    async def run_sync(self) -> None:
        """
        Run full synchronization of Notion data.

        Sync order:
        1. Users
        """
        try:
            org_id = self.data_entities_processor.org_id
            self.logger.info(f"🚀 Starting Notion sync for org: {org_id}")

            # Step 1: Sync users
            await self._sync_users()

            self.logger.info("✅ Notion sync completed successfully")

        except Exception as e:
            self.logger.error(f"❌ Error during Notion sync: {e}", exc_info=True)
            raise

    async def run_incremental_sync(self) -> None:
        """Run incremental sync (delegates to full sync)."""
        await self.run_sync()

    async def get_signed_url(self, record: Record) -> str:
        """Get a signed URL for a record (not implemented for Notion)."""
        # Notion uses OAuth, signed URLs are not applicable
        return ""

    async def stream_record(self, record: Record) -> StreamingResponse:
        """
        Stream record content from Notion.

        For pages: Fetches page content blocks
        For databases: Fetches database entries
        For files: Downloads file content

        Args:
            record: The record to stream

        Returns:
            StreamingResponse: Streaming response with content
        """
        try:
            self.logger.info(f"📥 Streaming record: {record.record_name} ({record.external_record_id})")

            # TODO: Implement streaming logic based on record type

        except HTTPException:
            raise
        except Exception as e:
            self.logger.error(f"❌ Failed to stream record: {e}", exc_info=True)
            raise HTTPException(
                status_code=500, detail=f"Failed to stream record: {str(e)}"
            )

    async def reindex_records(self, records: List[Record]) -> None:
        """
        Reindex a list of Notion records.

        This method:
        1. For each record, checks if it has been updated at the source
        2. If updated, upserts the record in DB
        3. Publishes reindex events for all records via data_entities_processor

        Args:
            records: List of properly typed Record instances
        """
        try:
            if not records:
                self.logger.info("No records to reindex")
                return

            self.logger.info(f"Starting reindex for {len(records)} Notion records")

            # TODO: Implement reindex logic
            # 1. Check each record at source for updates
            # 2. Update DB only for records that changed at source
            # 3. Publish reindex events for all records

        except Exception as e:
            self.logger.error(f"Error during Notion reindex: {e}", exc_info=True)
            raise

    async def cleanup(self) -> None:
        """
        Cleanup resources when shutting down the connector.

        Notion connector cleanup includes:
        - Clearing client references
        - Clearing datasource reference
        - Logging completion

        Note: Notion uses stateless HTTP requests, so no persistent connections
        or subscriptions to clean up.
        """
        try:
            self.logger.info("🧹 Starting Notion connector cleanup")

            # Clear client references
            if hasattr(self, 'notion_client'):
                self.notion_client = None

            if hasattr(self, 'data_source'):
                self.data_source = None

            self.logger.info("✅ Notion connector cleanup completed")

        except Exception as e:
            self.logger.error(f"❌ Error during Notion connector cleanup: {e}", exc_info=True)

    async def handle_webhook_notification(self, notification: Dict) -> None:
        """Handle webhook notifications (not implemented)."""
        self.logger.warning("Webhook notifications not yet supported for Notion")
        pass

    @classmethod
    async def create_connector(
        cls,
        logger: Logger,
        data_store_provider: DataStoreProvider,
        config_service: ConfigurationService
    ) -> "NotionConnector":
        """Factory method to create a Notion connector instance."""
        data_entities_processor = DataSourceEntitiesProcessor(
            logger,
            data_store_provider,
            config_service
        )

        await data_entities_processor.initialize()

        return cls(
            logger,
            data_entities_processor,
            data_store_provider,
            config_service
        )

    # ==================== Private Helper Methods ====================

    async def _sync_users(self) -> None:
        """
        Sync users from Notion using cursor-based pagination.

        Process:
        1. Call list_users to get all users (paginated)
        2. Filter for "person" type users only (skip "bot" type)
        3. For each person user, call retrieve_user to get their email
        4. Transform and save to database

        Note: list_users doesn't return email, need to fetch each user individually
        """
        try:
            self.logger.info("🔄 Starting user synchronization...")

            # Pagination variables
            page_size = 100  # Max allowed by Notion API
            cursor = None
            total_synced = 0
            total_skipped = 0

            # Paginate through all users
            while True:
                datasource = await self._get_fresh_datasource()

                response = await datasource.list_users(
                    start_cursor=cursor,
                    page_size=page_size
                )

                if not response or not response.success:
                    error_msg = response.error if response else "No response"
                    self.logger.error(f"❌ Failed to fetch users: {error_msg}")
                    break

                response_data = response.data.json() if response.data else {}
                users_data = response_data.get("results", [])

                if not users_data:
                    self.logger.info("No more users to process")
                    break

                # Filter for person users only
                person_user_ids = []
                for user in users_data:
                    if user.get("type") == "person" and user.get("id"):
                        person_user_ids.append(user.get("id"))
                    else:
                        self.logger.debug(
                            f"Skipping user: {user.get('name', 'Unknown')} "
                            f"(type: {user.get('type', 'N/A')}, id: {user.get('id', 'N/A')})"
                        )
                        total_skipped += 1

                if not person_user_ids:
                    continue

                # Fetch full user details in parallel to get emails
                user_detail_tasks = [datasource.retrieve_user(user_id) for user_id in person_user_ids]
                user_detail_responses = await asyncio.gather(*user_detail_tasks, return_exceptions=True)

                # Process fetched user details
                app_users = []
                for i, result in enumerate(user_detail_responses):
                    user_id = person_user_ids[i]

                    if isinstance(result, Exception):
                        self.logger.error(f"❌ Failed to process user {user_id}: {result}", exc_info=False)
                        total_skipped += 1
                        continue

                    if not result or not result.success:
                        self.logger.warning(
                            f"Failed to retrieve user details for {user_id}: "
                            f"{result.error if result else 'No response'}"
                        )
                        total_skipped += 1
                        continue

                    user_detail = result.data.json() if result.data else {}
                    app_user = self._transform_to_app_user(user_detail)
                    if app_user:
                        app_users.append(app_user)
                    else:
                        # _transform_to_app_user logs warnings for invalid data
                        total_skipped += 1

                # Save batch to database
                if app_users:
                    await self.data_entities_processor.on_new_app_users(app_users)
                    total_synced += len(app_users)
                    self.logger.info(f"✅ Synced {len(app_users)} users in this batch")

                has_more = response_data.get("has_more", False)
                cursor = response_data.get("next_cursor")

                if not has_more or not cursor:
                    break

            self.logger.info(f"✅ User sync complete. Synced: {total_synced}, Skipped: {total_skipped}")

        except Exception as e:
            self.logger.error(f"❌ User sync failed: {e}", exc_info=True)
            raise

    def _transform_to_app_user(self, user_data: Dict[str, Any]) -> Optional[AppUser]:
        """
        Transform Notion user data to AppUser entity.

        Args:
            user_data: Full user data from retrieve_user API (includes email)

        Returns:
            AppUser object or None if transformation fails

        Expected user_data format:
        {
            "object": "user",
            "id": "6794760a-1f15-45cd-9c65-0dfe42f5135a",
            "name": "Aman Gupta",
            "avatar_url": null,
            "type": "person",
            "person": {
                "email": "aman@example.com"
            }
        }
        """
        try:
            user_id = user_data.get("id")
            user_type = user_data.get("type")
            name = user_data.get("name")

            # Only process person users
            if user_type != "person":
                self.logger.debug(f"Skipping non-person user type: {user_type}")
                return None

            # Extract email from nested person object
            person_data = user_data.get("person", {}) or {}
            email = person_data.get("email", "").strip()

            # Validate required fields
            if not user_id:
                self.logger.warning("User data missing ID")
                return None

            if not email:
                self.logger.warning(f"User {user_id} ({name}) has no email address")
                return None

            return AppUser(
                app_name=Connectors.NOTION,
                source_user_id=user_id,
                org_id=self.data_entities_processor.org_id,
                email=email,
                full_name=name,
            )

        except Exception as e:
            self.logger.error(f"❌ Failed to transform user: {e}", exc_info=True)
            return None
