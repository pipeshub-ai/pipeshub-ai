import logging
from datetime import datetime
from typing import Any, AsyncGenerator, Dict, List, Optional, Tuple
from uuid import uuid4

import aiohttp

from app.config.configuration_service import DefaultEndpoints, config_node_constants
from app.config.utils.named_constants.arangodb_constants import (
    CollectionNames,
    EventTypes,
    OriginTypes,
    RecordTypes,
)


class NotionService:
    """Service class for interacting with Notion API."""

    BASE_URL = "https://api.notion.com/v1"

    def __init__(self, integration_secret: str, org_id: str,workspace_id:str,logger=None, arango_service=None,kafka_service=None,config_service=None):
        """Initialize Notion service with integration secret."""
        self.integration_secret = integration_secret
        self.org_id = org_id
        self.workspace_id= workspace_id
        self.logger = logger or logging.getLogger(__name__)
        self.arango_service = arango_service
        self.kafka_service =kafka_service
        self.config_service=config_service
        self.headers = {
            "Authorization": f"Bearer {integration_secret}",
            "Notion-Version": "2022-06-28",
            "Content-Type": "application/json",
        }

    async def _fetch_current_workspace(self) -> Dict[str, Any]:
        """
        Fetches information about the current user/bot from the Notion API
        and converts it into structured records.
        Returns:
            notion_workspace_record
        """
        try:
            self.logger.info("Fetching current Notion user information...")
            url = f"{self.BASE_URL}/users/me"

            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=self.headers) as response:
                    if response.status != 200:
                        error_text = await response.text()
                        self.logger.error(
                            f"Failed to fetch Notion user data: {error_text}"
                        )
                        raise Exception(f"Notion API error: {response.status}")

                    workspace_data = await response.json()

                    # Process the user data into records
                    notion_workspace_record_key = await self._process_workspace_data(workspace_data)

                    self.logger.info(f"Successfully fetched user: {workspace_data.get('name', 'Unknown user')}")
                    return notion_workspace_record_key

        except Exception as e:
            self.logger.error(f"Error fetching Notion user data: {str(e)}")
            raise

    async def _fetch_and_store_users(self, workspace_record_key):
        """
        Fetch all users from the Notion workspace and store them in the database.
        This function retrieves all users (both individual users and bots) from the Notion
        workspace, transforms them into appropriate records, and stores them in ArangoDB.
        It also creates relationships between users and the workspace.
        Returns:
            List[Dict]: All fetched users from the Notion API.
        """
        try:
            self.logger.info("Fetching users from Notion workspace")

            # Step 2: Fetch all users from the Notion API
            url = f"{self.BASE_URL}/users"

            all_users = []
            has_more = True
            next_cursor = None
            page_size = 100  # Maximum allowed by Notion API

            async with aiohttp.ClientSession() as session:
                while has_more:
                    params = {"page_size": page_size}
                    if next_cursor:
                        params["start_cursor"] = next_cursor

                    self.logger.info(f"Fetching batch of users with params: {params}")
                    async with session.get(url, headers=self.headers, params=params) as response:
                        if response.status != 200:
                            error_text = await response.text()
                            self.logger.error(f"Failed to fetch Notion users: {error_text}")
                            raise Exception(f"Notion API error: {response.status}")

                        data = await response.json()
                        users_batch = data.get("results", [])
                        all_users.extend(users_batch)

                        has_more = data.get("has_more", False)
                        next_cursor = data.get("next_cursor")

                        self.logger.info(f"Fetched {len(users_batch)} users, total now: {len(all_users)}")

                self.logger.info(f"Successfully fetched all {len(all_users)} Notion users")

                # Process all users after they've been completely fetched
                await self._process_and_store_users(all_users, workspace_record_key)

                return all_users
        except Exception as e:
            self.logger.error(f"Error fetching Notion users: {str(e)}")
            raise

    async def _process_and_store_users(self, all_users, workspace_record_key):
        """
        Process and store a batch of Notion users in the database.
        Args:
            all_users: List of users from Notion API
            workspace_record: Workspace record to link users to
        Returns:
            List of processed user records
        """
        try:
            notion_user_records = []
            current_timestamp = int(datetime.now().timestamp() * 1000)

            for user in all_users:
                user_id = user.get("id").replace("-", "")
                key = str(uuid4())

                existing_key = await self.arango_service.get_user_by_user_id(
                    user_id
                )

                if existing_key:
                    continue

                # Determine user type and extract relevant information
                user_type = user.get("type", "")
                user_name = user.get("name", "")

                if user_type == "person":
                    person_data = user.get("person", {})
                    email = person_data.get("email", "")
                    notion_user_record = {
                        "_key": key,
                        "orgId": self.org_id,
                        "userId": user_id,
                        "fullName": user_name,
                        "email": email,
                    }
                    self.logger.info(f"Notion user record: {notion_user_record}")
                    notion_user_records.append(notion_user_record)

            # Store the user records
            if notion_user_records:
                await self.arango_service.batch_upsert_nodes(
                    notion_user_records, CollectionNames.USERS.value
                )

                # Create relationships between users and workspace
                workspace_user_edges = []
                for user_record in notion_user_records:
                    edge_data = {
                        "_from": f"{CollectionNames.USERS.value}/{user_record['_key']}",
                        "_to": f"{CollectionNames.RECORD_GROUPS.value}/{workspace_record_key}",
                        "entityType": "GROUP",
                        "createdAtTimestamp": current_timestamp,
                    }
                    workspace_user_edges.append(edge_data)

                await self.arango_service.batch_create_edges(
                    workspace_user_edges, CollectionNames.BELONGS_TO.value
                )

            return notion_user_records

        except Exception as e:
            self.logger.error(f"Error processing and storing Notion users: {str(e)}")
            raise

    async def _process_workspace_data(self, user_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Process workspace data from Notion API into structured records.
        Args:
            user_data: User object from Notion API
        Returns:
            notion_workspace_record
        """
        try:
            self.logger.info("Creating workspace records and dumping in arangodb")
            workspaceId = user_data.get("id", "").replace("-", "")
            current_timestamp = int(datetime.now().timestamp() * 1000)


            # Extract workspace information
            # workspace_name = None

            # if user_data.get("type") == "bot" and user_data.get("bot"):
            #     bot_data = user_data.get("bot", {})
                # owner = bot_data.get("owner", {})

                # if owner.get("type") == "workspace" and owner.get("workspace") is True:
                #     workspace_name = bot_data.get("workspace_name", "Unknown Workspace")

            workspace_records = []

             # Check if record with this external record ID already exists
            existing_key = await self.arango_service.get_key_by_external_record_id(
                external_record_id=workspaceId,
                collection_name=CollectionNames.RECORD_GROUPS.value
            )

            if existing_key:
                self.logger.info(
                    "📋 Record with external record ID %s already exists with key %s. Updating record.",
                    workspaceId,
                    existing_key
                )
                return existing_key
            else:
                self.logger.info(
                    "🆕 Creating new record for external record ID %s",
                    workspaceId
                )

            key = str(uuid4())
            # Create Notion user record
            notion_workspace_record = {
                "_key": key,
                "orgId": self.org_id,
                "groupName": user_data.get("name", ""),
                "externalGroupId": workspaceId,
                "groupType": "NOTION_WORKSPACE",
                "createdAtTimestamp": current_timestamp,
                "connectorName": "NOTION",
                "updatedAtTimestamp": current_timestamp,
                "lastSyncTimestamp": current_timestamp,
            }
           
            workspace_records.append(notion_workspace_record)
            print(workspace_records)
            await self.arango_service.batch_upsert_nodes(
                workspace_records, CollectionNames.RECORD_GROUPS.value
            )

            return key
        except Exception as e:
            self.logger.error(
                f"Error processing and storing Notion Workspace Record: {str(e)}"
            )
            raise

    async def _fetch_all_pages(self):
        """
        Fetch all pages accessible with the integration secret.
        Yields:
            List[Dict[str, Any]]: Batches of pages from the Notion API as they're fetched.
        """
        try:
            self.logger.info("Fetching all Notion pages...")
            url = f"{self.BASE_URL}/search"

            params = {
                "filter": {"value": "page", "property": "object"},
                "page_size": 100,
            }

            has_more = True
            next_cursor = None
            total_pages = 0

            async with aiohttp.ClientSession() as session:
                while has_more:
                    if next_cursor:
                        params["start_cursor"] = next_cursor

                    async with session.post(
                        url, headers=self.headers, json=params
                    ) as response:
                        if response.status != 200:
                            error_text = await response.text()
                            self.logger.error(
                                f"Failed to fetch Notion pages: {error_text}"
                            )
                            raise Exception(f"Notion API error: {response.status}")

                        data = await response.json()
                        pages_batch = data.get("results", [])

                        # Yield each batch of pages
                        if pages_batch:
                            total_pages += len(pages_batch)
                            yield pages_batch

                        has_more = data.get("has_more", False)
                        next_cursor = data.get("next_cursor")

                        self.logger.info(
                            f"Fetched {total_pages} Notion pages so far"
                        )

            self.logger.info(f"Successfully fetched all {total_pages} Notion pages")
        except Exception as e:
            self.logger.error(f"Error fetching Notion pages: {str(e)}")
            raise

    def _extract_page_title(self, page: Dict[str, Any]) -> str:
        """Extract the title of a Notion page from its properties."""
        try:
            # Get the title property (usually "Name" or "Title")
            properties = page.get("properties", {})

            # Try different known title property names
            for prop_name in ["Name", "Title", "title"]:
                if prop_name in properties:
                    title_obj = properties[prop_name]
                    if title_obj.get("type") == "title":
                        title_parts = title_obj.get("title", [])
                        title_text = "".join(
                            [part.get("plain_text", "") for part in title_parts]
                        )
                        if title_text:
                            return title_text

            # If we reach here, it means either no title property was found,
            # or the title property exists but is empty
            page_id = page.get("id", "unknown")
            truncated_id = page_id[-8:] if len(page_id) >= 8 else page_id

            # Check if this is a database item
            parent = page.get("parent", {})
            if parent.get("type") == "database_id":
                database_id = parent.get("database_id", "")
                truncated_db_id = (
                    database_id[-4:] if len(database_id) >= 4 else database_id
                )
                return f"Untitled Item in DB-{truncated_db_id} (ID: {truncated_id})"

            return f"Untitled Page (ID: {truncated_id})"
        except Exception as e:
            self.logger.debug(f"Error extracting page title: {str(e)}")
            return "Untitled Page"

    def _iso_to_timestamp(self, iso_string: str) -> int:
        """Convert ISO date string to millisecond timestamp."""
        if not iso_string:
            return None
        try:
            dt = datetime.fromisoformat(iso_string.replace("Z", "+00:00"))
            return int(dt.timestamp() * 1000)
        except Exception:
            return None

    async def _transform_pages(
        self, pages: List[Dict[str, Any]]
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """
        Transform Notion API pages into both Notion page records and general records.

        Args:
            pages: List of page objects from Notion API

        Returns:
            Tuple of (notion_page_records, general_records)
        """
        current_timestamp = int(datetime.now().timestamp() * 1000)
        notion_page_records = []
        general_records = []
        message_events= []
        endpoints = await self.config_service.get_config(
                config_node_constants.ENDPOINTS.value
        )
        connector_endpoint = endpoints.get("connectors").get("endpoint", DefaultEndpoints.CONNECTOR_ENDPOINT.value)

        for page in pages:
            page_id = page.get("id", "").replace("-", "")
            title = self._extract_page_title(page)
            if not title:
                title = "Untitled Page for page id :" + page_id
            url = page.get("url", "")
            key = str(uuid4())

            # Create Notion page record
            notion_page_record = {
                "_key": key,
                "page_id": page_id,
                "title": title,
                "url": url,
                "parent": page.get("parent", {}),
                "isArchived": page.get("archived", False),
                "isDeleted": False,
                "hasChildren": page.get("has_children", False),
                "propertyValues": page.get("properties", {}),
                "rawData": page,
            }

            # Create general record
            general_record = {
                "_key": key,
                "orgId": self.org_id,
                "recordName": title,
                "externalRecordId": page_id,
                "recordType": "WEBPAGE",
                "version": 0,
                "origin": "CONNECTOR",
                "createdAtTimestamp": current_timestamp,
                "connectorName": "NOTION",
                "updatedAtTimestamp": current_timestamp,
                "lastSyncTimestamp": current_timestamp,
                "sourceCreatedAtTimestamp": self._iso_to_timestamp(
                    page.get("created_time")
                ),
                "sourceLastModifiedTimestamp": self._iso_to_timestamp(
                    page.get("last_edited_time")
                ),
                "isDeleted": False,
                "isArchived": page.get("archived", False),
                "indexingStatus": "NOT_STARTED",
                "extractionStatus": "NOT_STARTED",
                "isLatestVersion": True,
                "isDirty": False,
            }


            message_event = {
                "orgId": self.org_id,
                "recordId": key,
                "recordName": title,
                "recordType": "WEBPAGE",
                "recordVersion": 0,
                "eventType": EventTypes.NEW_RECORD.value,
                "signedUrlUnsupported" : False,
                "signedUrlRoute": f"{connector_endpoint}/api/v1/{self.org_id}/{self.workspace_id}/notion/record/page/{key}/signedUrl",
                "connectorName": "NOTION",
                "mimeType": "text",
                "origin": OriginTypes.CONNECTOR.value,
                "createdAtSourceTimestamp": self._iso_to_timestamp(
                    page.get("created_time")
                ),
                "modifiedAtSourceTimestamp": self._iso_to_timestamp(
                    page.get("last_edited_time")
                ),
            }
            notion_page_records.append(notion_page_record)
            general_records.append(general_record)
            message_events.append(message_event)

        return notion_page_records, general_records, message_events

    async def _process_and_store_pages(self, workspace_record_key):
        """
        Fetch, transform, and store Notion pages in the database.

        Returns:
            List of processed page records
        """
        try:
            all_page_records = []

            # Process pages in batches as they're fetched
            async for pages_batch in self._fetch_all_pages():
                # Transform pages into records
                notion_page_records, general_records,message_events = await self._transform_pages(pages_batch)

                # Store Notion page records
                if notion_page_records:
                    await self.arango_service.batch_upsert_nodes(
                        notion_page_records, CollectionNames.NOTION_PAGE_RECORD.value
                    )

                # Store general records
                if general_records:
                    await self.arango_service.batch_upsert_nodes(
                        general_records, CollectionNames.RECORDS.value
                    )

                    record_page_edges = []
                    for i, general_record in enumerate(general_records):
                        # Get the corresponding Notion page record
                        page_record = notion_page_records[i]

                        edge_data = {
                            "_from": f"{CollectionNames.RECORDS.value}/{general_record['_key']}",
                            "_to": f"{CollectionNames.NOTION_PAGE_RECORD.value}/{page_record['_key']}",
                            "createdAtTimestamp": general_record["createdAtTimestamp"],
                        }
                        record_page_edges.append(edge_data)

                    # Batch create edges between records and notion page records
                    await self.arango_service.batch_create_edges(
                        record_page_edges,
                        CollectionNames.IS_OF_TYPE.value,
                    )
                for message_event in message_events:
                    await self.kafka_service.send_event_to_kafka(message_event)
                    self.logger.info(
                        "📨 Sent Kafka Indexing event for message %s",
                        message_event['recordId'],
                    )

                # Create parent-child relationships for this batch
                await self._create_parent_child_relationships(
                    notion_page_records, workspace_record_key, recordType="page"
                )



                # Fetch comments and blocks for this batch
                await self._fetch_page_comments(notion_page_records)
                # await self._fetch_block_content(notion_page_records)

                all_page_records.extend(notion_page_records)

            return all_page_records

        except Exception as e:
            self.logger.error(f"Error processing and storing Notion pages: {str(e)}")
            raise

    async def _fetch_all_databases(self):
        """
        Fetch all databases accessible with the integration secret.
        Yields:
            List[Dict[str, Any]]: Batches of databases from the Notion API as they're fetched.
        """
        try:
            self.logger.info("Fetching all Notion databases...")
            url = f"{self.BASE_URL}/search"

            params = {
                "filter": {"value": "database", "property": "object"},
                "page_size": 100,
            }

            has_more = True
            next_cursor = None
            total_databases = 0

            async with aiohttp.ClientSession() as session:
                while has_more:
                    if next_cursor:
                        params["start_cursor"] = next_cursor

                    async with session.post(
                        url, headers=self.headers, json=params
                    ) as response:
                        if response.status != 200:
                            error_text = await response.text()
                            self.logger.error(
                                f"Failed to fetch Notion databases: {error_text}"
                            )
                            raise Exception(f"Notion API error: {response.status}")

                        data = await response.json()
                        databases_batch = data.get("results", [])

                        # Yield each batch of databases
                        if databases_batch:
                            total_databases += len(databases_batch)
                            yield databases_batch

                        has_more = data.get("has_more", False)
                        next_cursor = data.get("next_cursor")

                        self.logger.info(
                            f"Fetched {total_databases} Notion databases so far"
                        )

            self.logger.info(
                f"Successfully fetched all {total_databases} Notion databases"
            )

        except Exception as e:
            self.logger.error(f"Error fetching Notion databases: {str(e)}")
            raise

    def _extract_database_title(self, database: Dict[str, Any]) -> str:
        """Extract the title of a Notion database from its properties."""
        try:
            title_parts = database.get("title", [])
            title_text = "".join([part.get("plain_text", "") for part in title_parts])

            # If we have a title, return it
            if title_text:
                return title_text

            # If we don't have a title, create a descriptive placeholder
            database_id = database.get("id", "").replace("-", "")
            truncated_id = database_id[-8:] if len(database_id) >= 8 else database_id

            # Check if there's a parent to provide additional context
            parent = database.get("parent", {})
            parent_type = parent.get("type")

            if parent_type == "page_id":
                page_id = parent.get("page_id", "")
                truncated_page_id = page_id[-4:] if len(page_id) >= 4 else page_id
                return f"Untitled Database on Page-{truncated_page_id} (ID: {truncated_id})"
            elif parent_type == "workspace":
                return f"Untitled Workspace Database (ID: {truncated_id})"
            else:
                return f"Untitled Database (ID: {truncated_id})"
        except Exception as e:
            self.logger.debug(f"Error extracting database title: {str(e)}")
            return f"Untitled Database ({database.get('id', 'unknown')[-4:]})"

    async def _transform_databases(
        self, databases: List[Dict[str, Any]]
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """
        Transform Notion API databases into both Notion database records and general records.

        Args:
            databases: List of database objects from Notion API

        Returns:
            Tuple of (notion_database_records, general_records)
        """
        current_timestamp = int(datetime.now().timestamp() * 1000)
        notion_database_records = []
        general_records = []
        message_events=[]
        endpoints = await self.config_service.get_config(
                config_node_constants.ENDPOINTS.value
            )
        connector_endpoint = endpoints.get("connectors").get("endpoint", DefaultEndpoints.CONNECTOR_ENDPOINT.value)


        for database in databases:
            database_id = database.get("id", "").replace("-", "")
            title = self._extract_database_title(database)
            if not title:
                title = "Untitled db for db id :" + database_id
            url = database.get("url", "")
            key = str(uuid4())
            # Create Notion database record
            notion_database_record = {
                "_key": key,
                "database_id": database_id,
                "title": title,
                "url": url,
                "parent": database.get("parent", {}),
                "isArchived": database.get("archived", False),
                "isDeleted": False,
                "properties": database.get("properties", {}),
                "rawData": database,
            }

            # Create general record
            general_record = {
                "_key": key,
                "orgId": self.org_id,
                "recordName": title,
                "externalRecordId": database_id,
                "recordType": "DATABASE",
                "origin": "CONNECTOR",
                "version": 0,
                "createdAtTimestamp": current_timestamp,
                "connectorName": "NOTION",
                "updatedAtTimestamp": current_timestamp,
                "lastSyncTimestamp": current_timestamp,
                "sourceCreatedAtTimestamp": self._iso_to_timestamp(
                    database.get("created_time")
                ),
                "sourceLastModifiedTimestamp": self._iso_to_timestamp(
                    database.get("last_edited_time")
                ),
                "isDeleted": False,
                "isArchived": database.get("archived", False),
                "indexingStatus": "NOT_STARTED",
                "extractionStatus": "NOT_STARTED",
                "isLatestVersion": True,
                "isDirty": False,
            }


            message_event = {
                "orgId": self.org_id,
                "recordId": key,
                "recordName": title,
                "recordType": "DATABASE",
                "recordVersion": 0,
                "signedUrlUnsupported" : False,
                "eventType": EventTypes.NEW_RECORD.value,
                "signedUrlRoute": f"{connector_endpoint}/api/v1/{self.org_id}/{self.workspace_id}/notion/record/database/{key}/signedUrl",
                "connectorName": "NOTION",
                "origin": OriginTypes.CONNECTOR.value,

                "createdAtSourceTimestamp": self._iso_to_timestamp(
                    database.get("created_time")
                ),
                "modifiedAtSourceTimestamp": self._iso_to_timestamp(
                    database.get("last_edited_time")
                ),
            }
            message_events.append(message_event)
            notion_database_records.append(notion_database_record)
            general_records.append(general_record)

        return notion_database_records, general_records,message_events

    async def _process_and_store_databases(self, workspace_record_key):
        """
        Fetch, transform, and store Notion databases in the database.

        Returns:
            List of processed database records
        """
        try:
            all_database_records = []

            # Process databases in batches as they're fetched
            async for databases_batch in self._fetch_all_databases():
                # Transform databases into records
                notion_database_records, general_records,message_events = await self._transform_databases(
                    databases_batch
                )
                # Store Notion database records
                if notion_database_records:
                    await self.arango_service.batch_upsert_nodes(
                        notion_database_records,
                        CollectionNames.NOTION_DATABASE_RECORD.value,
                    )

                # Store general records
                if general_records:
                    await self.arango_service.batch_upsert_nodes(
                        general_records, CollectionNames.RECORDS.value
                    )

                    record_database_edges = []
                    for i, general_record in enumerate(general_records):
                        # Get the corresponding Notion database record
                        database_record = notion_database_records[i]

                        edge_data = {
                            "_from": f"{CollectionNames.RECORDS.value}/{general_record['_key']}",
                            "_to": f"{CollectionNames.NOTION_DATABASE_RECORD.value}/{database_record['_key']}",
                            "createdAtTimestamp": general_record["createdAtTimestamp"],
                        }
                        record_database_edges.append(edge_data)

                    # Batch create edges between records and notion database records
                    await self.arango_service.batch_create_edges(
                        record_database_edges,
                        CollectionNames.IS_OF_TYPE.value,
                    )

                for message_event in message_events:
                    await self.kafka_service.send_event_to_kafka(message_event)
                    self.logger.info(
                        "📨 Sent Kafka Indexing event for message %s",
                        message_event['recordId'],
                    )


                await self._create_parent_child_relationships(
                    notion_database_records, workspace_record_key, recordType="db"
                )

                all_database_records.extend(notion_database_records)

            return all_database_records

        except Exception as e:
            self.logger.error(
                f"Error processing and storing Notion databases: {str(e)}"
            )
            raise

    async def _create_parent_child_relationships(self, pages_or_databases,workspace_record_key,recordType):
        """
        Create relationships between pages and their parents/children.

        This function creates edges between:
        1. Pages and their parent pages
        2. Pages and their parent databases
        3. Databases and their parent pages
        4. Databases and their parent databases
        """
        try:

            self.logger.info(
                "Creating parent-child relationships for Notion pages and databases..."
            )

            relationship_edges = []
            group_edges=[]
            if(recordType=="page"):
                # Lists to hold edges
                self.logger.info("creating page parent relationship")
                # Process each page to create parent-child relationships
                for page in pages_or_databases:
                    page_key = page.get("_key")
                    parent = page.get("parent", {})
                    parent_type = parent.get("type")

                    if parent_type == "page_id":
                        parent_id = parent.get("page_id").replace("-", "")


                        parent_key = await self._get_or_create_page_key(parent_id)


                        # Create edge from parent to child using _key
                        edge_data = {
                            "_from": f"{CollectionNames.RECORDS.value}/{parent_key}",
                            "_to": f"{CollectionNames.RECORDS.value}/{page_key}",
                            "relationship": "PARENT_CHILD",
                            "createdAtTimestamp": int(datetime.now().timestamp() * 1000),
                        }
                        relationship_edges.append(edge_data)

                    elif parent_type == "database_id":
                        parent_id = parent.get("database_id").replace("-", "")

                        parent_key = await self._get_or_create_db_key(parent_id)

                        # Create edge from parent database to child page using _key
                        edge_data = {
                            "_from": f"{CollectionNames.RECORDS.value}/{parent_key}",
                            "_to": f"{CollectionNames.RECORDS.value}/{page_key}",
                            "relationship": "PARENT_CHILD",
                            "createdAtTimestamp": int(datetime.now().timestamp() * 1000),
                        }
                        relationship_edges.append(edge_data)

                    elif parent_type == "workspace":
                        edge_data = {
                            "_from": f"{CollectionNames.RECORD_GROUPS.value}/{workspace_record_key}",
                            "_to": f"{CollectionNames.RECORDS.value}/{page_key}",
                            "entityType": "GROUP",
                            "createdAtTimestamp": int(datetime.now().timestamp() * 1000),
                        }
                        group_edges.append(edge_data)



                self.logger.info("creating database parent relationship")

            elif recordType =="db":
                for db in pages_or_databases:
                    db_key = db.get("_key")
                    parent = db.get("parent", {})
                    parent_type = parent.get("type")

                    if parent_type == "page_id":
                        parent_id = parent.get("page_id").replace("-", "")

                        parent_key = await self._get_or_create_page_key(parent_id)

                        # Create edge from parent page to child database using _key
                        edge_data = {
                            "_from": f"{CollectionNames.RECORDS.value}/{parent_key}",
                            "_to": f"{CollectionNames.RECORDS.value}/{db_key}",
                            "relationship": "PARENT_CHILD",
                            "createdAtTimestamp": int(datetime.now().timestamp() * 1000),
                        }
                        relationship_edges.append(edge_data)

                    elif parent_type == "database_id":
                        parent_id = parent.get("database_id").replace("-", "")

                        parent_key = await self._get_or_create_db_key(parent_id)

                        edge_data = {
                            "_from": f"{CollectionNames.RECORDS.value}/{parent_key}",
                            "_to": f"{CollectionNames.RECORDS.value}/{db_key}",
                            "relationship": "PARENT_CHILD",
                            "createdAtTimestamp": int(datetime.now().timestamp() * 1000),
                        }
                        relationship_edges.append(edge_data)

                    elif parent_type == "workspace":
                        edge_data = {
                            "_from": f"{CollectionNames.RECORD_GROUPS.value}/{workspace_record_key}",
                            "_to": f"{CollectionNames.RECORDS.value}/{db_key}",
                            "entityType": "GROUP",
                            "createdAtTimestamp": int(datetime.now().timestamp() * 1000),
                        }
                        group_edges.append(edge_data)

            # Batch create edges
            if(group_edges):
                await self.arango_service.batch_create_edges(
                    group_edges, CollectionNames.BELONGS_TO_RECORD_GROUP.value
                )

                self.logger.info(
                    f"Created {len(relationship_edges)} parent-child relationships"
                )
            if relationship_edges:
                await self.arango_service.batch_create_edges(
                    relationship_edges, CollectionNames.RECORD_RELATIONS.value
                )

                self.logger.info(
                    f"Created {len(relationship_edges)} parent-child relationships"
                )

            return len(relationship_edges)+len(group_edges)

        except Exception as e:
            self.logger.error(f"Error creating parent-child relationships: {str(e)}")
            raise

    async def _get_or_create_page_key(self, page_id):
        """
        Look up a page key in ArangoDB, or create a placeholder if not found.
        Args:
            page_id: The Notion page ID to look up
        Returns:
            The _key value for the page
        """
        # First try to find the page in ArangoDB

        page_record = await self.arango_service.get_key_by_external_file_id(
            page_id,
        )

        if page_record:
            return page_record
        else:
            # Create a placeholder if not found
            return await self._create_placeholder_page(page_id)

    async def _get_or_create_db_key(self, database_id):
        """
        Look up a database key in ArangoDB, or create a placeholder if not found.
        Args:
            database_id: The Notion database ID to look up
        Returns:
            The _key value for the database
        """
        # First try to find the database in ArangoDB
        db_record = await self.arango_service.get_key_by_external_file_id(
            database_id
        )

        if db_record:
            return db_record
        else:
            # Create a placeholder if not found
            return await self._create_placeholder_database(database_id)


    async def _create_placeholder_page(self, page_id):
        """
        Create a placeholder page node in ArangoDB for a parent page that hasn't been fetched yet.

        This placeholder will be updated when the actual page is fetched
        """

        try:
            id=page_id.replace("-", "")
            key = str(uuid4())
            placeholder_page_records=[]
            placeholder_general_records=[]
            edges_data=[]

            # Create a placeholder record in the notion_page_records collection
            placeholder_page = {
                "_key": key,
                "page_id": id,
                "title": f"Placeholder Page ({id})",
                "createdAtTimestamp": int(datetime.now().timestamp() * 1000),
            }

            # Create a placeholder general record
            placeholder_general_record = {
                "_key": key,
                "orgId": self.org_id,
                "recordName": f"Placeholder Page ({id})",
                "externalRecordId": id,
                "recordType": "WEBPAGE",
                "origin": "CONNECTOR",
                "version": 0,
                "createdAtTimestamp": int(datetime.now().timestamp() * 1000),
                "connectorName": "NOTION",
            }
            placeholder_page_records.append(placeholder_page)
            placeholder_general_records.append(placeholder_general_record)

            # Upsert the placeholder page
            await self.arango_service.batch_upsert_nodes(
                placeholder_page_records, CollectionNames.NOTION_PAGE_RECORD.value
            )

            # Upsert the placeholder general record
            await self.arango_service.batch_upsert_nodes(
                placeholder_general_records, CollectionNames.RECORDS.value
            )

            # Create edge between general record and notion page record
            edge_data = {
                "_from": f"{CollectionNames.RECORDS.value}/{placeholder_general_record['_key']}",
                "_to": f"{CollectionNames.NOTION_PAGE_RECORD.value}/{placeholder_page['_key']}",
                "createdAtTimestamp": placeholder_general_record["createdAtTimestamp"],
            }
            edges_data.append(edge_data)
            await self.arango_service.batch_create_edges(
                edges_data, CollectionNames.IS_OF_TYPE.value
            )

            self.logger.info(f"Created placeholder page for ID: {page_id}")
            return key

        except Exception as e:
            self.logger.error(f"Error creating placeholder page: {str(e)}")
            raise

    async def _create_placeholder_database(self,database_id):
        """
        Create a placeholder db node in ArangoDB for a parent page that hasn't been fetched yet.

        This placeholder will be updated when the actual db is fetched.
        """
        try:
            id=database_id.replace("-", "")
            key = str(uuid4())
            # Create a placeholder record in the notion_page_records collection
            placeholder_db_records=[]
            placeholder_general_records=[]
            edges_data=[]

            placeholder_db = {
                "_key": key,
                "database_id": id,
                "title": f"Placeholder Database ({id})",
                "isArchived": False,
                "isDeleted": False,
                "createdAtTimestamp": int(datetime.now().timestamp() * 1000),
            }

            # Create a placeholder general record
            placeholder_general_record = {
                "_key": key,
                "orgId": self.org_id,
                "recordName": f"Placeholder Database ({id})",
                "externalRecordId": id,
                "recordType": "DATABASE",
                "origin": "CONNECTOR",
                "version": 0,
                "createdAtTimestamp": int(datetime.now().timestamp() * 1000),
                "connectorName": "NOTION",
            }
            placeholder_db_records.append(placeholder_db)
            placeholder_general_records.append(placeholder_general_record)
            # Upsert the placeholder page
            await self.arango_service.batch_upsert_nodes(
                placeholder_db_records, CollectionNames.NOTION_DATABASE_RECORD.value
            )

            # Upsert the placeholder general record
            await self.arango_service.batch_upsert_nodes(
                placeholder_general_records, CollectionNames.RECORDS.value
            )

            # Create edge between general record and notion page record
            edge_data = {
                "_from": f"{CollectionNames.RECORDS.value}/{placeholder_general_record['_key']}",
                "_to": f"{CollectionNames.NOTION_DATABASE_RECORD.value}/{placeholder_db['_key']}",
                "createdAtTimestamp": placeholder_general_record["createdAtTimestamp"],
            }
            edges_data.append(edge_data)

            await self.arango_service.batch_create_edges(
                edges_data, CollectionNames.IS_OF_TYPE.value
            )

            self.logger.info(f"Created placeholder page for ID: {database_id}")
            return key

        except Exception as e:
            self.logger.error(f"Error creating placeholder page: {str(e)}")
            raise



    async def _fetch_page_comments(self, pages):
        """ Fetch comments for all the pages and store them as arrays per page """
        try:
            self.logger.info("Fetching comments and making record for all pages")

            page_comment_records = []
            general_records = []
            comment_edges = []
            message_events=[]
            current_timestamp = int(datetime.now().timestamp() * 1000)

            for page in pages:
                page_id = page.get("page_id","").replace("-", "")

                all_comments = await self._fetch_comments(page_id)

                # Transform comments into array format
                comments_array = []

                for comment in all_comments:
                    comment_id = comment.get("id", "").replace("-", "")

                    # Extract comment text
                    rich_text = []
                    if comment.get("type") == "discussion":
                        rich_text = comment.get("discussion", {}).get("rich_text", [])
                    else:
                        rich_text = comment.get("rich_text", [])
                    comment_text = self._extract_text_from_rich_text(rich_text)

                    # Skip empty comments
                    if len(comment_text) == 0:
                        continue

                    # Get user information
                    user_id = comment.get("created_by", {}).get("id", "Unknown user").replace("-","")

                    # Get creation time
                    created_time = comment.get("created_time", "")
                    created_timestamp = self._iso_to_timestamp(created_time)

                    # Get last edited time for deletedAtTimestamp if needed
                    last_edited_time = comment.get("last_edited_time", "")
                    deleted_timestamp = None
                    if comment.get("archived", False):
                        deleted_timestamp = self._iso_to_timestamp(last_edited_time) if last_edited_time else None

                    # Create comment object for the array
                    comment_obj = {
                        "comment_id": comment_id,
                        "text": comment_text,
                        "createdBy": user_id,
                        "createdAtTimestamp": created_timestamp,
                    }

                    # Add deletedAtTimestamp only if comment is deleted/archived
                    if deleted_timestamp is not None:
                        comment_obj["deletedAtTimestamp"] = deleted_timestamp

                    comments_array.append(comment_obj)

                # Create single record per page with all comments
                if comments_array and len(comments_array)>0:  # Create record even if no comments (empty array)
                    key = str(uuid4())

                    # Create page comments record
                    page_comment_record = {
                        "_key": key,
                        "page_id": page_id,
                        "comments": comments_array
                    }

                    # Create corresponding general record
                    general_record = {
                        "_key": key,
                        "orgId": self.org_id,
                        "recordName": f"comments for page {page_id}",
                        "externalRecordId": page_id,
                        "recordType": "PAGE_COMMENTS",
                        "origin": "CONNECTOR",
                        "version": 0,
                        "createdAtTimestamp": current_timestamp,
                        "connectorName": "NOTION",
                        "updatedAtTimestamp": current_timestamp,
                        "lastSyncTimestamp": current_timestamp,
                        "sourceCreatedAtTimestamp": current_timestamp,  # Use current time as page comments creation time
                        "sourceLastModifiedTimestamp": current_timestamp,
                        "isDeleted": False,
                        "isArchived": False,
                        "indexingStatus": "NOT_STARTED",
                        "extractionStatus": "NOT_STARTED",
                        "isLatestVersion": True,
                        "isDirty": False,
                    }

                    # Create edge between page and its comments record
                    edge_data = {
                        "_from": f"{CollectionNames.NOTION_PAGE_RECORD.value}/{page['_key']}",
                        "_to": f"{CollectionNames.NOTION_COMMENT_RECORD.value}/{key}",
                        "relationship": "PARENT_CHILD",
                        "createdAtTimestamp": current_timestamp
                    }
                    endpoints = await self.config_service.get_config(
                        config_node_constants.ENDPOINTS.value
                    )
                    connector_endpoint = endpoints.get("connectors").get("endpoint", DefaultEndpoints.CONNECTOR_ENDPOINT.value)

                    message_event = {
                        "orgId": self.org_id,
                        "recordId": key,
                        "recordName": f"comments for page {page_id}",
                        "recordType": RecordTypes.FILE.value,
                        "mimeType": "text",
                        "recordVersion": 0,
                        "signedUrlUnsupported" : False,
                        "eventType": EventTypes.NEW_RECORD.value,
                        "signedUrlRoute": f"{connector_endpoint}/api/v1/{self.org_id}/{self.workspace_id}/notion/record/comment/{key}/signedUrl",
                        "connectorName": "NOTION",
                        "origin": OriginTypes.CONNECTOR.value,
                        "createdAtSourceTimestamp": current_timestamp,
                        "modifiedAtSourceTimestamp": current_timestamp
                    }

                    message_events.append(message_event)
                    

                    page_comment_records.append(page_comment_record)
                    general_records.append(general_record)
                    comment_edges.append(edge_data)

                    self.logger.info(f"Created comments record for page {page_id} with {len(comments_array)} comments")
                else:
                    self.logger.info(f"No comments found for page {page_id}")

            # Batch operations for all pages
            if page_comment_records:
                # Store page comment records
                await self.arango_service.batch_upsert_nodes(
                    page_comment_records,
                    CollectionNames.NOTION_COMMENT_RECORD.value
                )

                await self.arango_service.batch_upsert_nodes(
                    general_records,
                    CollectionNames.RECORDS.value
                )

                # Create edges between general records and comment records
                record_comment_edges = []
                for i, general_record in enumerate(general_records):
                    comment_record = page_comment_records[i]

                    edge_data = {
                        "_from": f"{CollectionNames.RECORDS.value}/{general_record['_key']}",
                        "_to": f"{CollectionNames.NOTION_COMMENT_RECORD.value}/{comment_record['_key']}",
                        "createdAtTimestamp": general_record["createdAtTimestamp"],
                    }
                    record_comment_edges.append(edge_data)

                # Batch create edges
                await self.arango_service.batch_create_edges(
                    record_comment_edges,
                    CollectionNames.IS_OF_TYPE.value,
                )

                # Create edges between pages and their comment records
                await self.arango_service.batch_create_edges(
                    comment_edges,
                    CollectionNames.RECORD_RELATIONS.value,
                )

                self.logger.info(f"Successfully processed comments for {len(page_comment_records)} pages")


            for message in message_events:
                await self.kafka_service.send_event_to_kafka(message_event)
                self.logger.info(
                    "📨 Sent Kafka Indexing event for message %s",
                    message_event['recordId'],
                )
        except Exception as e:
            self.logger.error(f"Error fetching page comments: {str(e)}")
            raise

    async def _fetch_comments(self, block_id):
        """ Fetch comments for the specific page or block """
        try:
            # self.logger.info(f"Fetching comments and making record for block: {block_id}")
            url = f"{self.BASE_URL}/comments"

            # API params - filter by the block_id to get comments for this specific block
            params = {
                "block_id": block_id,
                "page_size": 100
            }

            all_comments = []
            has_more = True
            next_cursor = None

            async with aiohttp.ClientSession() as session:
                while has_more:
                    if next_cursor:
                        params["start_cursor"] = next_cursor

                    async with session.get(url, headers=self.headers, params=params) as response:
                        if response.status != 200:
                            error_text = await response.text()
                            self.logger.error(f"Failed to fetch comments: {error_text}")
                            raise Exception(f"Notion API error: {response.status}")

                        data = await response.json()
                        all_comments.extend(data.get("results", []))

                        has_more = data.get("has_more", False)
                        next_cursor = data.get("next_cursor")

                # self.logger.info(f"Successfully fetched {len(all_comments)} comments for block: {block_id}")
                return all_comments

        except Exception as e:
            self.logger.error(f"Error fetching comments for block {block_id}: {str(e)}")
            raise

    async def _fetch_block_children(self, block_id):
        """Fetch all child blocks for a given block ID (page or block)."""
        try:
            self.logger.info(f"Fetching child blocks for block: {block_id}")
            url = f"{self.BASE_URL}/blocks/{block_id}/children"

            all_blocks = []
            has_more = True
            next_cursor = None

            async with aiohttp.ClientSession() as session:
                while has_more:
                    params = {"page_size": 100}
                    if next_cursor:
                        params["start_cursor"] = next_cursor

                    async with session.get(
                        url, headers=self.headers, params=params
                    ) as response:
                        if response.status != 200:
                            error_text = await response.text()
                            self.logger.error(
                                f"Failed to fetch block children: {error_text}"
                            )
                            raise Exception(f"Notion API error: {response.status}")

                        data = await response.json()
                        all_blocks.extend(data.get("results", []))

                        has_more = data.get("has_more", False)
                        next_cursor = data.get("next_cursor")

                self.logger.info(f"Successfully fetched {len(all_blocks)} child blocks")
                return all_blocks

        except Exception as e:
            self.logger.error(f"Error fetching block children: {str(e)}")
            raise



# -----------------------------------------------------------------------------------------------------------------------

    async def _process_single_pages_batch(self, pages_batch: List[Dict[str, Any]], workspace_record_key: str):
        """
        Process a single batch of pages without yielding - all processing happens here.
        This replaces the batch processing logic from _process_and_store_pages.
        Args:
            pages_batch: List of page objects from Notion API
            workspace_record: Workspace record for relationship creation
        """
        try:
            # Transform pages into records
            notion_page_records, general_records,message_events = await self._transform_pages(pages_batch)

            # Store Notion page records
            if notion_page_records:
                await self.arango_service.batch_upsert_nodes(
                    notion_page_records, CollectionNames.NOTION_PAGE_RECORD.value
                )

            # Store general records
            if general_records:
                await self.arango_service.batch_upsert_nodes(
                    general_records, CollectionNames.RECORDS.value
                )

                record_page_edges = []
                for i, general_record in enumerate(general_records):
                    # Get the corresponding Notion page record
                    page_record = notion_page_records[i]

                    edge_data = {
                        "_from": f"{CollectionNames.RECORDS.value}/{general_record['_key']}",
                        "_to": f"{CollectionNames.NOTION_PAGE_RECORD.value}/{page_record['_key']}",
                        "createdAtTimestamp": general_record["createdAtTimestamp"],
                    }
                    record_page_edges.append(edge_data)

                # Batch create edges between records and notion page records
                await self.arango_service.batch_create_edges(
                    record_page_edges,
                    CollectionNames.IS_OF_TYPE.value,
                )

            # Create parent-child relationships for this batch
            await self._create_parent_child_relationships(
                notion_page_records, workspace_record_key, recordType="page"
            )

            for message_event in message_events:
                await self.kafka_service.send_event_to_kafka(message_event)
                self.logger.info(
                    "📨 Sent Kafka Indexing event for message %s",
                    message_event['recordId'],
                )

            await self._fetch_page_comments(notion_page_records)
            await self._process_blocks_for_pages_batch(notion_page_records)

        except Exception as e:
            self.logger.error(f"Error processing single pages batch: {str(e)}")
            raise

    async def _process_blocks_for_pages_batch(self, notion_page_records: List[Dict[str, Any]]):
        """
        Process blocks for a batch of pages.
        Args:
            notion_page_records: List of notion page records that have been stored
        """
        try:
            self.logger.info(f"Processing blocks for {len(notion_page_records)} pages")

            # Collections to store all file/media records for batch operations
            all_file_records = []
            all_general_records = []
            all_record_edges = []
            all_record_relation_edges = []
            all_processed_blocks = []
            all_kafka_events = []  # New: Store all Kafka events for batch sending

            for page_record in notion_page_records:
                page_id = page_record.get("page_id").replace("-", "")

                self.logger.info(f"Fetching blocks for page {page_id}")

                try:
                    # Fetch blocks for this page
                    blocks = await self._fetch_block_children(page_id)

                    # Process each block
                    for block in blocks:
                        block_id = block.get("id").replace("-", "")
                        block_type = block.get("type")
                        block_created_time = block.get("created_time")
                        block_last_edited_time = block.get("last_edited_time")

                        if block_type in ["child_page", "child_database", "unsupported"]:
                            continue

                        block_content = block.get(block_type, {})

                        # Handle file/media blocks - create records and return existing info
                        if block_type in ["file", "video", "image", "gif", "audio"]:
                            file_info = await self._process_file_block(
                                block_id, block_type, block_content,
                                block_created_time, block_last_edited_time,
                                page_record
                            )

                            if file_info:
                                all_file_records.append(file_info["file_record"])
                                all_general_records.append(file_info["general_record"])
                                all_record_edges.append(file_info["record_edge"])
                                all_record_relation_edges.append(file_info["record_relation_edge"])

                                # Create Kafka event for this file
                                kafka_event = await self._create_file_kafka_event(
                                    file_info["record_id"],
                                    file_info["name"],
                                    file_info["mimetype"],
                                    self._iso_to_timestamp(block_created_time),
                                    self._iso_to_timestamp(block_last_edited_time)
                                )
                                all_kafka_events.append(kafka_event)

                                # Add processed block info
                                all_processed_blocks.append({
                                    "block_id": block_id,
                                    "block_type": block_type,
                                    "record_id": file_info["record_id"],
                                    "file_url": file_info["file_url"],
                                    "name": file_info["name"],
                                    "mimetype": file_info["mimetype"],
                                    "created_time": block_created_time,
                                    "last_edited_time": block_last_edited_time,
                                    "page_id": page_id
                                })

                except Exception as e:
                    self.logger.error(f"Error processing blocks for page {page_id}: {str(e)}")
                    # Continue with other pages even if one fails
                    continue

            # Batch operations for all file/media records across all pages
            if all_file_records:
                await self.arango_service.batch_upsert_nodes(
                    all_file_records,
                    CollectionNames.FILES.value
                )
                self.logger.info(f"Stored {len(all_file_records)} file records")

            if all_general_records:
                await self.arango_service.batch_upsert_nodes(
                    all_general_records,
                    CollectionNames.RECORDS.value
                )
                self.logger.info(f"Stored {len(all_general_records)} general records")

            if all_record_edges:
                await self.arango_service.batch_create_edges(
                    all_record_edges,
                    CollectionNames.IS_OF_TYPE.value,
                )
                self.logger.info(f"Created {len(all_record_edges)} record type edges")

            if all_record_relation_edges:
                await self.arango_service.batch_create_edges(
                    all_record_relation_edges,
                    CollectionNames.RECORD_RELATIONS.value,
                )
                self.logger.info(f"Created {len(all_record_relation_edges)} record relation edges")

            # Send all Kafka events for file records
            if all_kafka_events:
                await self._send_batch_kafka_events(all_kafka_events)
                self.logger.info(f"Sent {len(all_kafka_events)} Kafka events for file records")

            self.logger.info(f"Successfully processed {len(all_processed_blocks)} blocks across {len(notion_page_records)} pages")

        except Exception as e:
            self.logger.error(f"Error processing blocks for pages batch: {str(e)}")
            raise

    async def _create_file_kafka_event(self, record_id: str, name: str, mime_type: str,
                                    created_at: int, last_edited_at: int) -> Dict[str, Any]:
        """
        Create a Kafka event for a file record.
        Args:
            record_id: The record ID (key)
            name: File name
            mime_type: MIME type of the file
            created_at: Creation timestamp
            last_edited_at: Last edited timestamp
        Returns:
            Kafka event dictionary
        """
        try:
            endpoints = await self.config_service.get_config(
                config_node_constants.ENDPOINTS.value
            )
            connector_endpoint = endpoints.get("connectors").get("endpoint", DefaultEndpoints.CONNECTOR_ENDPOINT.value)

            message_event = {
                "orgId": self.org_id,
                "recordId": record_id,
                "recordName": name,
                "recordType": RecordTypes.FILE.value,
                "mimeType": mime_type,
                "recordVersion": 0,
                "signedUrlUnsupported": False,
                "eventType": EventTypes.NEW_RECORD.value,
                "signedUrlRoute": f"{connector_endpoint}/api/v1/{self.org_id}/{self.workspace_id}/notion/file/{record_id}/signedUrl",
                "connectorName": "NOTION",
                "origin": OriginTypes.CONNECTOR.value,
                "createdAtSourceTimestamp": created_at,
                "modifiedAtSourceTimestamp": last_edited_at
            }

            return message_event

        except Exception as e:
            self.logger.error(f"Error creating Kafka event for record {record_id}: {str(e)}")
            raise

    async def _send_batch_kafka_events(self, kafka_events: List[Dict[str, Any]]):
        """
        Send multiple Kafka events efficiently.
        Args:
            kafka_events: List of Kafka event dictionaries
        """
        try:
            # Send events individually (you can batch this if your Kafka service supports it)
            for event in kafka_events:
                await self.kafka_service.send_event_to_kafka(event)
                self.logger.info(f"📨 Sent Kafka Indexing event for record {event['recordId']}")

        except Exception as e:
            self.logger.error(f"Error sending batch Kafka events: {str(e)}")
            raise

    async def _process_single_databases_batch(self, databases_batch: List[Dict[str, Any]], workspace_record_key):
        """
        Process a single batch of databases without yielding - all processing happens here.
        This replaces the batch processing logic from _process_and_store_databases.
        Args:
            databases_batch: List of database objects from Notion API
            workspace_record: Workspace record for relationship creation
        """
        try:
            # Transform databases into records
            notion_database_records, general_records,message_events = await self._transform_databases(databases_batch)

            # Store Notion database records
            if notion_database_records:
                await self.arango_service.batch_upsert_nodes(
                    notion_database_records,
                    CollectionNames.NOTION_DATABASE_RECORD.value,
                )

            # Store general records
            if general_records:
                await self.arango_service.batch_upsert_nodes(
                    general_records, CollectionNames.RECORDS.value
                )

                record_database_edges = []
                for i, general_record in enumerate(general_records):
                    # Get the corresponding Notion database record
                    database_record = notion_database_records[i]

                    edge_data = {
                        "_from": f"{CollectionNames.RECORDS.value}/{general_record['_key']}",
                        "_to": f"{CollectionNames.NOTION_DATABASE_RECORD.value}/{database_record['_key']}",
                        "createdAtTimestamp": general_record["createdAtTimestamp"],
                    }
                    record_database_edges.append(edge_data)

                # Batch create edges between records and notion database records
                await self.arango_service.batch_create_edges(
                    record_database_edges,
                    CollectionNames.IS_OF_TYPE.value,
                )

            for message_event in message_events:
                await self.kafka_service.send_event_to_kafka(message_event)
                self.logger.info(
                    "📨 Sent Kafka Indexing event for message %s",
                    message_event['recordId'],
                )



            # Create parent-child relationships for this batch
            await self._create_parent_child_relationships(
                notion_database_records, workspace_record_key, recordType="db"
            )

        except Exception as e:
            self.logger.error(f"Error processing single databases batch: {str(e)}")
            raise


    async def fetch_and_create_notion_records(self, batch_size: int = 50) -> AsyncGenerator[Dict[str, Any], None]:
        """
        Fetch and create Notion records with cooperative yielding back to caller.
        After processing each batch, this function yields control back to the caller,
        allowing the caller to decide what to do next (run other services, handle requests, etc.)
        Args:
            batch_size: Number of items to process in each batch before yielding back to caller
        Yields:
            Progress updates - after each yield, control returns to the caller
        """
        try:
            self.logger.info("Starting Notion data sync process")

            # Step 1: Fetch workspace
            yield {"step": "workspace", "status": "started", "message": "Fetching workspace information"}
            workspace_record_key = await self._fetch_current_workspace()
            yield {"step": "workspace", "status": "completed", "message": "Workspace fetched successfully", "data": {"workspace_key": workspace_record_key}}

            # Step 2: Fetch and store users
            yield {"step": "users", "status": "started", "message": "Fetching workspace users"}
            users = await self._fetch_and_store_users(workspace_record_key)
            yield {"step": "users", "status": "completed", "message": f"Processed {len(users)} users", "data": {"user_count": len(users)}}

            # Step 3: Process pages in batches, yielding back to caller after each batch
            yield {"step": "pages", "status": "started", "message": "Starting page processing"}
            total_pages = 0

            pages_buffer = []
            async for pages_batch in self._fetch_all_pages():
                pages_buffer.extend(pages_batch)

                # Process batches and yield back to caller after each batch
                while len(pages_buffer) >= batch_size:
                    processing_batch = pages_buffer[:batch_size]
                    pages_buffer = pages_buffer[batch_size:]

                    # Process this batch
                    await self._process_single_pages_batch(processing_batch, workspace_record_key)
                    total_pages += len(processing_batch)

                    self.logger.info(f"Processed batch of {len(processing_batch)} pages, total: {total_pages}")

                    # YIELD BACK TO CALLER - this is where control returns to orchestrator
                    yield {
                        "step": "pages_batch",
                        "status": "completed",
                        "message": f"Processed {len(processing_batch)} pages",
                        "data": {
                            "batch_pages_processed": len(processing_batch),
                            "total_pages_processed": total_pages
                        }
                    }
                    # After this yield, the caller gets control back and can do whatever they want

            # Process remaining pages
            if pages_buffer:
                await self._process_single_pages_batch(pages_buffer, workspace_record_key)
                total_pages += len(pages_buffer)
                yield {
                    "step": "pages_batch",
                    "status": "completed",
                    "message": f"Processed final {len(pages_buffer)} pages",
                    "data": {
                        "batch_pages_processed": len(pages_buffer),
                        "total_pages_processed": total_pages
                    }
                }

            yield {"step": "pages", "status": "completed", "message": f"All pages processed: {total_pages}", "data": {"page_count": total_pages}}

            # Step 4: Process databases in batches, yielding back to caller after each batch
            yield {"step": "databases", "status": "started", "message": "Starting database processing"}
            total_databases = 0

            databases_buffer = []
            async for databases_batch in self._fetch_all_databases():
                databases_buffer.extend(databases_batch)

                # Process batches and yield back to caller after each batch
                while len(databases_buffer) >= batch_size:
                    processing_batch = databases_buffer[:batch_size]
                    databases_buffer = databases_buffer[batch_size:]

                    # Process this batch
                    await self._process_single_databases_batch(processing_batch, workspace_record_key)
                    total_databases += len(processing_batch)

                    self.logger.info(f"Processed batch of {len(processing_batch)} databases, total: {total_databases}")

                    # YIELD BACK TO CALLER - control returns to orchestrator
                    yield {
                        "step": "databases_batch",
                        "status": "completed",
                        "message": f"Processed {len(processing_batch)} databases",
                        "data": {
                            "batch_databases_processed": len(processing_batch),
                            "total_databases_processed": total_databases
                        }
                    }
                    # After this yield, caller can run other services, handle requests, etc.

            # Process remaining databases
            if databases_buffer:
                await self._process_single_databases_batch(databases_buffer, workspace_record_key)
                total_databases += len(databases_buffer)
                yield {
                    "step": "databases_batch",
                    "status": "completed",
                    "message": f"Processed final {len(databases_buffer)} databases",
                    "data": {
                        "batch_databases_processed": len(databases_buffer),
                        "total_databases_processed": total_databases
                    }
                }

            yield {"step": "databases", "status": "completed", "message": f"All databases processed: {total_databases}", "data": {"database_count": total_databases}}

            # Final completion
            yield {
                "step": "sync",
                "status": "completed",
                "message": "Notion sync completed successfully",
                "data": {
                    "workspace_key": workspace_record_key,
                    "user_count": len(users),
                    "page_count": total_pages,
                    "database_count": total_databases
                }
            }

        except Exception as e:
            self.logger.error(f"Error in fetch_and_create_notion_records: {str(e)}")
            yield {
                "step": "error",
                "status": "failed",
                "message": f"Notion sync failed: {str(e)}",
                "error": str(e)
            }
            raise

    #--------------block fetching --------------------------------

    async def store_file_block(self,page,file_id,file_url,name,mimeType,createdAt,lastEditedAt):
        """Store a single file block in the database"""
        try:

            current_timestamp = int(datetime.now().timestamp() * 1000)
            key = str(uuid4())

            # Create file record
            file_record = {
                "_key": key,
                "orgId": self.org_id,
                "name":name,
                "webUrl": file_url,
            }

            # Create general record
            general_record = {
                "_key": key,
                "orgId": self.org_id,
                "recordName": name,
                "externalRecordId": page.get("page_id", "").replace("-", ""),
                "recordType": "FILE",
                "origin": "CONNECTOR",
                "version": 0,
                "createdAtTimestamp": current_timestamp,
                "connectorName": "NOTION",
                "updatedAtTimestamp": current_timestamp,
                "lastSyncTimestamp": current_timestamp,
                "sourceCreatedAtTimestamp": createdAt,
                "sourceLastModifiedTimestamp": lastEditedAt,
                "isLatestVersion": True,
                "isDirty": False,
            }

            record_edge_data = {
                "_from": f"{CollectionNames.RECORDS.value}/{key}",
                "_to": f"{CollectionNames.FILES.value}/{key}",
                "createdAtTimestamp": general_record["createdAtTimestamp"],
            }

            record_relation_edge_data = {
                "_from": f"{CollectionNames.RECORDS.value}/{page['_key']}",
                "_to": f"{CollectionNames.RECORDS.value}/{key}",
                "relationship": "PARENT_CHILD",
                "createdAtTimestamp": int(datetime.now().timestamp() * 1000)
            }

            endpoints = await self.config_service.get_config(
                config_node_constants.ENDPOINTS.value
            )
            connector_endpoint = endpoints.get("connectors").get("endpoint", DefaultEndpoints.CONNECTOR_ENDPOINT.value)

            message_event = {
                "orgId": self.org_id,
                "recordId": key,
                "recordName": name,
                "recordType": RecordTypes.FILE.value,
                "mimeType": mimeType,
                "recordVersion": 0,
                "signedUrlUnsupported" : False,
                "eventType": EventTypes.NEW_RECORD.value,
                "signedUrlRoute": f"{connector_endpoint}/api/v1/{self.org_id}/{self.workspace_id}/notion/file/{key}/signedUrl",
                "connectorName": "NOTION",
                "origin": OriginTypes.CONNECTOR.value,
                "createdAtSourceTimestamp": createdAt,
                "modifiedAtSourceTimestamp": lastEditedAt
            }

            await self.kafka_service.send_event_to_kafka(message_event)
            self.logger.info(
                "📨 Sent Kafka Indexing event for message %s",
                key,
            )

            self.logger.info("create file records")
            return file_record,general_record,record_edge_data,record_relation_edge_data


        except Exception as e:
            self.logger.error(f"Error storing file block: {str(e)}")
            raise


    def _extract_text_from_rich_text(self, rich_text):
        """Helper method to extract plain text from Notion's rich text objects."""
        if not rich_text:
            return ""

        return "".join([text.get("plain_text", "") for text in rich_text])

    def _extract_links_from_rich_text(self, rich_text):
        """Extract links from Notion's rich text array"""
        if not rich_text:
            return []

        links = []
        for text_item in rich_text:
            if text_item.get("href"):
                links.append(text_item.get("href"))

        return links

    def _generate_filename_from_url(self, url):
        """Generate a simple filename from a URL"""
        if not url:
            return "untitled"

        # Get the last part of the URL
        filename = url.split("/")[-1]

        # Remove query parameters if any
        filename = filename.split("?")[0]

        # If filename is still empty or too long, use a default
        if not filename or len(filename) > 50:
            filename = str(uuid4())[:8]

        return filename

    def _convert_to_epoch_ms(self, iso_datetime):
        """Convert ISO datetime string to epoch milliseconds"""
        import calendar
        from datetime import datetime

        dt = datetime.fromisoformat(iso_datetime.replace("Z", "+00:00"))
        return calendar.timegm(dt.utctimetuple()) * 1000 + dt.microsecond // 1000


    async def _process_file_block(self, block_id: str, block_type: str, block_content: Dict,
                                 block_created_time: str, block_last_edited_time: str,
                                 page_record: Dict) -> Optional[Dict]:
        """
        Process a file/media block and return all necessary records and edges.
        Args:
            block_id: ID of the block
            block_type: Type of the block (file, image, video, audio, gif)
            block_content: Content of the block
            block_created_time: Block creation timestamp
            block_last_edited_time: Block last edit timestamp
        Returns:
            Dictionary containing all records and edges, or None if processing fails
        """
        try:
            # Extract file information based on block type
            if block_type == "file":
                file_type = block_content.get("type")
                if file_type == "external":
                    file_url = block_content.get("external", {}).get("url", "")
                elif file_type == "file":
                    file_url = block_content.get("file", {}).get("url", "")
                else:
                    file_url = ""

                name = block_content.get("name", "Untitled")
                mimetype = self._extract_mimetype_from_content(block_content, block_type, name)

            elif block_type in ["video", "image", "gif"]:
                # Get the nested block content
                media_content = block_content.get(block_type, {})
                file_type = media_content.get("type")

                file_url = ""
                if file_type == "external":
                    file_url = media_content.get("external", {}).get("url", "")
                elif file_type == "file":
                    file_url = media_content.get("file", {}).get("url", "")
                elif file_type == "youtube" and block_type == "video":
                    file_url = media_content.get("youtube", {}).get("url", "")
                elif file_type == "vimeo" and block_type == "video":
                    file_url = media_content.get("vimeo", {}).get("url", "")

                # Extract name
                name = f"Untitled {block_type.title()}"
                if media_content.get("name"):
                    name = media_content.get("name")
                else:
                    caption = media_content.get("caption", [])
                    if caption and len(caption) > 0:
                        caption_text = caption[0].get("plain_text", "")
                        if caption_text:
                            name = caption_text

                mimetype = self._extract_mimetype_from_content(media_content, block_type, name)

            elif block_type == "audio":
                # Get the nested audio content
                audio_content = block_content.get("audio", {})
                audio_type = audio_content.get("type")

                file_url = ""
                if audio_type == "external":
                    file_url = audio_content.get("external", {}).get("url", "")
                elif audio_type == "file":
                    file_url = audio_content.get("file", {}).get("url", "")

                # Extract name
                name = "Untitled Audio"
                if audio_content.get("name"):
                    name = audio_content.get("name")
                else:
                    caption = audio_content.get("caption", [])
                    if caption and len(caption) > 0:
                        caption_text = caption[0].get("plain_text", "")
                        if caption_text:
                            name = caption_text

                mimetype = self._extract_mimetype_from_content(audio_content, block_type, name)

            else:
                return None

            # Create records using existing method
            createdAt = self._iso_to_timestamp(block_created_time)
            lastEditedAt = self._iso_to_timestamp(block_last_edited_time)

            file_record, general_record, record_edge_data, record_relation_edge_data = await self.store_file_block(
                page_record, block_id, file_url, name, mimetype, createdAt, lastEditedAt
            )

            return {
                "record_id": block_id,
                "file_url": file_url,
                "name": name,
                "mimetype": mimetype,
                "file_record": file_record,
                "general_record": general_record,
                "record_edge": record_edge_data,
                "record_relation_edge": record_relation_edge_data
            }

        except Exception as e:
            self.logger.error(f"Error processing file block {block_id}: {str(e)}")
            return None


    def _extract_mimetype_from_content(self, content: Dict, block_type: str, name: str) -> str:
        """Extract or infer mimetype from content and block type"""
        # Try to get mimetype from file content first
        mimetype = None
        if content.get("file"):
            mimetype = content.get("file", {}).get("mimetype") or content.get("file", {}).get("mime_type")

        # If no mimetype found, infer from block type and file extension
        if not mimetype:
            if block_type == "image":
                if name.lower().endswith(('.jpg', '.jpeg')):
                    mimetype = "image/jpeg"
                elif name.lower().endswith('.png'):
                    mimetype = "image/png"
                elif name.lower().endswith('.gif'):
                    mimetype = "image/gif"
                elif name.lower().endswith('.webp'):
                    mimetype = "image/webp"
                else:
                    mimetype = "image/unknown"
            elif block_type == "video":
                mimetype = "video/unknown"
            elif block_type == "gif":
                mimetype = "image/gif"
            elif block_type == "audio":
                if name.lower().endswith(('.mp3', '.mpeg')):
                    mimetype = "audio/mpeg"
                elif name.lower().endswith('.wav'):
                    mimetype = "audio/wav"
                elif name.lower().endswith('.ogg'):
                    mimetype = "audio/ogg"
                elif name.lower().endswith('.m4a'):
                    mimetype = "audio/mp4"
                else:
                    mimetype = "audio/unknown"
            else:
                # For file type, try to infer from extension
                if name.lower().endswith(('.pdf',)):
                    mimetype = "application/pdf"
                elif name.lower().endswith(('.doc', '.docx')):
                    mimetype = "application/msword"
                else:
                    mimetype = "application/octet-stream"

        return mimetype
