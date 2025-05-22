import asyncio
from uuid import uuid4
import aiohttp
import logging
from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime
from app.config.utils.named_constants.arangodb_constants import (
    CollectionNames,
)


class NotionService:
    """Service class for interacting with Notion API."""

    BASE_URL = "https://api.notion.com/v1"

    def __init__(self, integration_secret: str, org_id: str, logger=None,arango_service=None):
        """Initialize Notion service with integration secret."""
        self.integration_secret = integration_secret
        self.org_id = org_id
        self.logger = logger or logging.getLogger(__name__)
        self.arango_service = arango_service
        self.headers = {
            "Authorization": f"Bearer {integration_secret}",
            "Notion-Version": "2022-06-28",
            "Content-Type": "application/json",
        }

    async def _fetch_current_workspace(self) -> Tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any]]:
        """
        Fetches information about the current user/bot from the Notion API
        and converts it into structured records.
        
        Returns:
            tuple: (notion_user_record, general_record, raw_response)
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
                    notion_workspace_record = await self._process_workspace_data(workspace_data)
                    

                    self.logger.info(f"Successfully fetched user: {workspace_data.get('name', 'Unknown user')}")
                    return notion_workspace_record
        
        except Exception as e:
            self.logger.error(f"Error fetching Notion user data: {str(e)}")
            raise
    
    async def _fetch_and_store_users(self,workspace_record):
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
                await self._process_and_store_users(all_users,workspace_record)
                
                return all_users
        except Exception as e:
            self.logger.error(f"Error fetching Notion users: {str(e)}")
            raise

    async def _process_and_store_users(self, all_users,workspace_record):
        """
        Process and store a batch of Notion users in the database.
        
        Args:
            all_users
            
        Returns:
            List of processed user records
        """
        try:
            notion_user_records = []
            current_timestamp = int(datetime.now().timestamp() * 1000)
            
            for user in all_users:
                user_id = user.get("id")
                key = str(uuid4())
                
                # Determine user type and extract relevant information
                user_type = user.get("type", "")
                user_name = user.get("name", "")
                
                if user_type == "person":
                    person_data = user.get("person", {})
              
                    email = person_data.get("email", "")
                    notion_user_record = {
                        "_key": key,
                        "orgId":self.org_id,
                        "userId":user_id,
                        "fullName": user_name,
                        "email": email,
                    }
                    
                
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
                        "_from":f"{CollectionNames.USERS.value}/{user_record['_key']}",
                        "_to": f"{CollectionNames.GROUPS.value}/{workspace_record['_key']}",
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

    async def get_user_by_id(self, user_id):
        """
        Fetch a specific user by their Notion user ID.
        
        Args:
            user_id: The Notion user ID to fetch
            
        Returns:
            Dict containing the user information or None if not found
        """
        try:
            self.logger.info(f"Fetching Notion user with ID: {user_id}")
            url = f"{self.BASE_URL}/users/{user_id}"
            
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=self.headers) as response:
                    if response.status == 404:
                        self.logger.warning(f"User with ID {user_id} not found")
                        return None
                    
                    if response.status != 200:
                        error_text = await response.text()
                        self.logger.error(f"Failed to fetch Notion user: {error_text}")
                        raise Exception(f"Notion API error: {response.status}")
                    
                    user_data = await response.json()
                    self.logger.info(f"Successfully fetched user: {user_data.get('name', 'Unknown user')}")
                    return user_data
                    
        except Exception as e:
            self.logger.error(f"Error fetching Notion user: {str(e)}")
            raise

    async def get_active_workspace_members(self):
        """
        Get a list of all active members in the workspace.
        This is a convenience method that fetches users and filters to just active members.
        
        Returns:
            List of active workspace members (excluding bots)
        """
        try:
            all_users = []
            async for result in self.fetch_and_store_workspace_users():
                if "users_processed" in result:
                    # We've processed some users
                    pass
                    
            # Once all users are fetched, query ArangoDB for active human members
            query = f"""
            FOR user IN {CollectionNames.NOTION_USER_RECORD.value}
                FILTER user.type == "person"
                RETURN user
            """
            
            active_members = await self.arango_service.execute_query(query)
            self.logger.info(f"Found {len(active_members)} active workspace members")
            
            return active_members
            
        except Exception as e:
            self.logger.error(f"Error fetching active workspace members: {str(e)}")
            raise

    async def get_workspace_bots(self):
        """
        Get a list of all bots in the workspace.
        
        Returns:
            List of workspace bots
        """
        try:
            all_users = []
            async for result in self.fetch_and_store_workspace_users():
                if "users_processed" in result:
                    # We've processed some users
                    pass
                    
            # Once all users are fetched, query ArangoDB for bots
            query = f"""
            FOR user IN {CollectionNames.NOTION_USER_RECORD.value}
                FILTER user.type == "bot"
                RETURN user
            """
            
            bots = await self.arango_service.execute_query(query)
            self.logger.info(f"Found {len(bots)} workspace bots")
            
            return bots
            
        except Exception as e:
            self.logger.error(f"Error fetching workspace bots: {str(e)}")
            raise

        
    async def _process_workspace_data(self, user_data: Dict[str, Any]) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        """
        Process workspace data from Notion API into structured records.
        
        Args:
            workspace_data: User object from Notion API
            
        Returns:
            notion_workspace_record
        """
        try:
            self.logger.info(f"creating workspace records and dumping in arangodb")
            current_timestamp = int(datetime.now().timestamp() * 1000)
            key = str(uuid4())
            
            # Extract workspace information
            workspace_name = None
            
            if user_data.get("type") == "bot" and user_data.get("bot"):
                bot_data = user_data.get("bot", {})
                owner = bot_data.get("owner", {})
                
                if owner.get("type") == "workspace" and owner.get("workspace") is True:
                    workspace_name = bot_data.get("workspace_name", "Unknown Workspace")
                    workspace_info = {
                        "workspace_id": None,  # Not directly provided
                        "workspace_name": workspace_name,
                        "is_default": True
                    }
            
            workspace_records=[]
            # Create Notion user record
            notion_workspace_record = {
                "_key": key,
                "orgId": self.org_id,
                "groupName": user_data.get("name", ""),
                "externalRecordId": user_data.get("id", ""),
                "groupType": "NOTION_WORKSPACE",
                "createdAtTimestamp": current_timestamp,
                "connectorName": "NOTION",
                "updatedAtTimestamp": current_timestamp,
                "lastSyncTimestamp": current_timestamp,
            }
      
            workspace_records.append(notion_workspace_record)
         
            await self.arango_service.batch_upsert_nodes(
                workspace_records, CollectionNames.GROUPS.value
            )
            
            return notion_workspace_record
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

    def _transform_pages(
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
                "page_id": page.get("id", ""),
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
                "externalRecordId": page.get("id", ""),
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

            notion_page_records.append(notion_page_record)
            general_records.append(general_record)

        return notion_page_records, general_records

    async def _process_and_store_pages(self,workspace_record):
        """
        Fetch, transform, and store Notion pages in the database.

        Returns:
            Tuple of (notion_pages_count, general_records_count)
        """
        try:
            # Fetch all pages
            pages = await self._fetch_all_pages()

            # Transform pages into records
            notion_page_records, general_records = self._transform_pages(pages)

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

                    # Format the page_id to match ArangoDB's key format
                    notion_page_id = page_record["page_id"].replace("-", "")

                    edge_data = {
                        "_from": f"{CollectionNames.RECORDS.value}/{general_record['_key']}",
                        "_to": f"{CollectionNames.NOTION_PAGE_RECORD.value}/{page_record['_key']}",
                        "createdAtTimestamp": general_record["createdAtTimestamp"],
                    }
                    record_page_edges.append(edge_data)

                # Batch create edges between records and notion page records
                await self.arango_service.batch_create_edges(
                    record_page_edges,
                    CollectionNames.IS_OF_TYPE.value,  # Assuming this collection exists
                )
            
            await self._create_parent_child_relationships(
                notion_page_records, workspace_record,recordType="page"
            )

            await self._fetch_page_comments(notion_page_records)

            await self._fetch_block_content(notion_page_records)

            return notion_page_records

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
            database_id = database.get("id", "unknown")
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

    def _transform_databases(
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
                "database_id": database.get("id", ""),
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
                "externalRecordId": database.get("id", ""),
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

            notion_database_records.append(notion_database_record)
            general_records.append(general_record)

        return notion_database_records, general_records

    async def _process_and_store_databases(self,workspace_record):
        """
        Fetch, transform, and store Notion databases in the database._process_and_store_databases

        Returns:
            Tuple of (notion_databases_count, general_records_count)
        """
        try:
            # Fetch all databases
            databases = await self._fetch_all_databases()

            # Transform databases into records
            notion_database_records, general_records = self._transform_databases(
                databases
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

                    # Format the database_id to match ArangoDB's key format
                    notion_database_id = database_record["database_id"].replace("-", "")

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

            await self._create_parent_child_relationships(
                notion_database_records, workspace_record,recordType="db"
            )


            return notion_database_records

        except Exception as e:
            self.logger.error(
                f"Error processing and storing Notion databases: {str(e)}"
            )
            raise

    async def _create_parent_child_relationships(self, pages_or_databases,workspace_record,recordType):
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
            if(recordType=="page"):
               
                # Lists to hold edges
                relationship_edges = []
                self.logger.info("creating page parent relationship")
                # Process each page to create parent-child relationships
                for page in pages_or_databases:
                    page_id = page.get("page_id")
                    page_key = page.get("_key", page_id.replace("-", ""))
                    parent = page.get("parent", {})
                    parent_type = parent.get("type")

                    if parent_type == "page_id":
                        parent_id = parent.get("page_id")

                        
                        parent_key = await self._get_or_create_page_key(parent_id)


                        # Create edge from parent to child using _key
                        edge_data = {
                            "_from": f"{CollectionNames.NOTION_PAGE_RECORD.value}/{parent_key}",
                            "_to": f"{CollectionNames.NOTION_PAGE_RECORD.value}/{page_key}",
                            "relationship": "PARENT_CHILD",
                            "createdAtTimestamp": int(datetime.now().timestamp() * 1000),
                        }
                        relationship_edges.append(edge_data)

                    elif parent_type == "database_id":
                        parent_id = parent.get("database_id")

                        parent_key = await self._get_or_create_db_key(parent_id)

                        # Create edge from parent database to child page using _key
                        edge_data = {
                            "_from": f"{CollectionNames.NOTION_DATABASE_RECORD.value}/{parent_key}",
                            "_to": f"{CollectionNames.NOTION_PAGE_RECORD.value}/{page_key}",
                            "relationship": "PARENT_CHILD",
                            "createdAtTimestamp": int(datetime.now().timestamp() * 1000),
                        }
                        relationship_edges.append(edge_data)

                    elif parent_type == "workspace":
                        edge_data = {
                            "_from": f"{CollectionNames.GROUPS.value}/{workspace_record['_key']}",
                            "_to": f"{CollectionNames.NOTION_PAGE_RECORD.value}/{page_key}",
                            "relationship": "PARENT_CHILD",
                            "createdAtTimestamp": int(datetime.now().timestamp() * 1000),
                        }
                        relationship_edges.append(edge_data)
                    
        

                self.logger.info("creating database parent relationship")

            elif recordType =="db":
                for db in pages_or_databases:
                    db_id = db.get("database_id")
                    db_key = db.get("_key", db_id.replace("-", ""))
                    parent = db.get("parent", {})
                    parent_type = parent.get("type")

                    if parent_type == "page_id":
                        parent_id = parent.get("page_id")

                        parent_key = await self._get_or_create_page_key(parent_id)

                        # Create edge from parent page to child database using _key
                        edge_data = {
                            "_from": f"{CollectionNames.NOTION_PAGE_RECORD.value}/{parent_key}",
                            "_to": f"{CollectionNames.NOTION_DATABASE_RECORD.value}/{db_key}",
                            "relationship": "PARENT_CHILD",
                            "createdAtTimestamp": int(datetime.now().timestamp() * 1000),
                        }
                        relationship_edges.append(edge_data)

                    elif parent_type == "database_id":
                        parent_id = parent.get("database_id")

                        parent_key = await self._get_or_create_db_key(parent_id)

                        edge_data = {
                            "_from": f"{CollectionNames.NOTION_DATABASE_RECORD.value}/{parent_key}",
                            "_to": f"{CollectionNames.NOTION_DATABASE_RECORD.value}/{db_key}",
                            "relationship": "PARENT_CHILD",
                            "createdAtTimestamp": int(datetime.now().timestamp() * 1000),
                        }
                        relationship_edges.append(edge_data)

                    elif parent_type == "workspace":
                        edge_data = {
                            "_from": f"{CollectionNames.GROUPS.value}/{workspace_record['_key']}",
                            "_to": f"{CollectionNames.NOTION_DATABASE_RECORD.value}/{db_key}",
                            "relationship": "PARENT_CHILD",
                            "createdAtTimestamp": int(datetime.now().timestamp() * 1000),
                        }
                        relationship_edges.append(edge_data)
                    

            # Batch create edges
            if relationship_edges:
                await self.arango_service.batch_create_edges(
                    relationship_edges, CollectionNames.RECORD_RELATIONS.value
                )

                self.logger.info(
                    f"Created {len(relationship_edges)} parent-child relationships"
                )

                return len(relationship_edges)

            return 0

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
        page_record = await self.arango_service.get_document(
            collection=CollectionNames.NOTION_PAGE_RECORD.value,
            filter_dict={"page_id": page_id}
        )
        
        if page_record:
            return page_record.get("_key", page_id.replace("-", ""))
        else:
            # Create a placeholder if not found
            return await self._create_placeholder_page(page_id)

    async def _get_or_create_database_key(self, database_id):
        """
        Look up a database key in ArangoDB, or create a placeholder if not found.
        
        Args:
            database_id: The Notion database ID to look up
            
        Returns:
            The _key value for the database
        """
        # First try to find the database in ArangoDB
        db_record = await self.arango_service.get_document(
            collection=CollectionNames.NOTION_DATABASE_RECORD.value,
            filter_dict={"database_id": database_id}
        )
        
        if db_record:
            return db_record.get("_key", database_id.replace("-", ""))
        else:
            # Create a placeholder if not found
            return await self._create_placeholder_database(database_id)
        
        
    async def _create_placeholder_page(self, page_id):
        """
        Create a placeholder page node in ArangoDB for a parent page that hasn't been fetched yet.

        This placeholder will be updated when the actual page is fetched
        """

        try:
            key = str(uuid4())
            placeholder_page_records=[]
            placeholder_general_records=[]
            edges_data=[]

            # Create a placeholder record in the notion_page_records collection
            placeholder_page = {
                "_key": key,
                "page_id": page_id,
                "title": f"Placeholder Page ({page_id[-8:]})",
                "createdAtTimestamp": int(datetime.now().timestamp() * 1000),
            }

            # Create a placeholder general record
            placeholder_general_record = {
                "_key": key,
                "orgId": self.org_id,
                "recordName": f"Placeholder Page ({page_id})",
                "externalRecordId": page_id,
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
            record_key = await self.arango_service.batch_upsert_nodes(
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

        except Exception as e:
            self.logger.error(f"Error creating placeholder page: {str(e)}")
            raise

    async def _create_placeholder_database(self,database_id):
        """
        Create a placeholder page node in ArangoDB for a parent page that hasn't been fetched yet.

        This placeholder will be updated when the actual page is fetched.
        """
        try:
            key = str(uuid4())
            # Create a placeholder record in the notion_page_records collection
            placeholder_db_records=[]
            placeholder_general_records=[]
            edges_data=[]

            placeholder_db = {
                "_key": key,
                "database_id": database_id,
                "title": f"Placeholder Page ({database_id})",
                "isArchived": False,
                "isDeleted": False,
                "hasChildren": False,
                "createdAtTimestamp": int(datetime.now().timestamp() * 1000),
            }

            # Create a placeholder general record
            placeholder_general_record = {
                "_key": key,
                "orgId": self.org_id,
                "recordName": f"Placeholder Page ({database_id})",
                "externalRecordId": database_id,
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

        except Exception as e:
            self.logger.error(f"Error creating placeholder page: {str(e)}")
            raise


    async def _fetch_block_content(self,pages):
        """Fetch all child blocks for all pages."""
        self.logger.info(f"fetching blocks ")
        processed_blocks = []
        try:
            for page in pages:
                page_id = page.get("page_id")
                blocks = await self._fetch_block_children(page_id)
                file_records=[]
                general_records=[]
                record_edges=[]
                record_relation_edges=[]

                for block in blocks:
                    block_id = block.get("id")
                    block_type = block.get("type")
                    block_created_time = block.get("created_time")
                    block_last_edited_time = block.get("last_edited_time")
                 
                    if block_type in ["child_page", "child_database","unsupported"]:
                        continue

                    block_content = block.get(block_type, {})
                  
                   
                    
                    if block_type=="file":
                        self.logger.info(f"got a file record")
                        file_type = block_content.get("type")
                        if file_type == "external":
                            file_url = block_content.get("external", {}).get("url", "")
                        elif file_type == "file":
                            file_url = block_content.get("file", {}).get("url", "")

                        name = block_content.get("name", "Untitled")
                        createdAt=self._iso_to_timestamp(block_content.get("created_time"))
                        lastEditedAt=  self._iso_to_timestamp(block_content.get("last_edited_time"))
                        file_record,general_record,record_edge_data,record_relation_edge_data= await self.store_file_block(page,file_url,name,createdAt,lastEditedAt)
                        file_records.append(file_record)
                        general_records.append(general_record)
                        record_edges.append(record_edge_data)
                        record_relation_edges.append(record_relation_edge_data)
                
                    processed_block = await self._process_block( block_id, block_type, block_content, 
                                                   block_created_time, block_last_edited_time)
        
                    if processed_block:
                        processed_blocks.append(processed_block)
        

                await self.arango_service.batch_upsert_nodes(
                    file_records,
                    CollectionNames.FILES.value
                )

                await self.arango_service.batch_upsert_nodes(
                    general_records,
                    CollectionNames.RECORDS.value
                )

                await self.arango_service.batch_create_edges(
                    record_edges,
                    CollectionNames.IS_OF_TYPE.value,  # Assuming this collection exists
                )
                
                # Create edges between blocks and comments
                await self.arango_service.batch_create_edges(
                    record_relation_edges,
                    CollectionNames.RECORD_RELATIONS.value,  # Assuming this collection exists
                )
    
            
            self.logger.info(f"Processed all blocks")
            print(processed_blocks)
            
        except Exception as e:
            self.logger.error(f"Error fetching block children: {str(e)}")
            raise

    async def _fetch_page_comments(self,pages):
        """ Fetch comments for all the pages """
        try:
            self.logger.info(f"Fetching comments and making record for all pages")
            for page in pages:
                page_id = page.get("page_id")

                all_comments = await self._fetch_comments(page_id)

                 # Transform comments into records
                comment_records = []
                general_records =[]
                comment_edges = []
                current_timestamp = int(datetime.now().timestamp() * 1000)
                
                for comment in all_comments:
                    comment_id = comment.get("id", "")
                    
                    # Extract comment text
                    rich_text = []
                    if comment.get("type") == "discussion":
                        rich_text = comment.get("discussion", {}).get("rich_text", [])
                    else :
                        rich_text = comment.get("rich_text", [])
                    comment_text = self._extract_text_from_rich_text(rich_text)
                    
                    if(len(comment_text)==0):
                        pass
                    # Get user information
                    user_id = comment.get("created_by", {}).get("id", "Unknown user ")
                    
                    # Get creation time
                    created_time = comment.get("created_time", "")
                    created_timestamp = self._iso_to_timestamp(created_time)
                    
                    key = str(uuid4())
                    # Create comment record
                    comment_record = {
                        "_key": key,
                        "comment_id": comment_id,
                        "page_id": page_id,
                        "text": comment_text,
                        "createdBy": user_id ,
                        "createdAtTimestamp": created_timestamp,
                    }


                    general_record = {
                        "_key": key,
                        "orgId": self.org_id,
                        "recordName": f"comment with id {comment_id}",
                        "externalRecordId": page_id,
                        "recordType": "COMMENT",
                        "origin": "CONNECTOR",
                        "version": 0,
                        "createdAtTimestamp": current_timestamp,
                        "connectorName": "NOTION",
                        "updatedAtTimestamp": current_timestamp,
                        "lastSyncTimestamp": current_timestamp,
                        "sourceCreatedAtTimestamp": self._iso_to_timestamp(
                            comment.get("created_time")
                        ),
                        "sourceLastModifiedTimestamp": self._iso_to_timestamp(
                            comment.get("last_edited_time")
                        ),
                        "isDeleted": False,
                        "isArchived": comment.get("archived", False),
                        "indexingStatus": "NOT_STARTED",
                        "extractionStatus": "NOT_STARTED",
                        "isLatestVersion": True,
                        "isDirty": False,
                    }
                 

                    edge_data = {
                        "_from": f"{CollectionNames.NOTION_PAGE_RECORD.value}/{page['_key']}",
                        "_to": f"{CollectionNames.NOTION_COMMENT_RECORD.value}/{key}",
                        "relationship": "PARENT_CHILD",
                        "createdAtTimestamp": int(datetime.now().timestamp() * 1000)
                    }

                    comment_edges.append(edge_data)
                    comment_records.append(comment_record)
                    general_records.append(general_record)


                if not comment_records:
                    self.logger.info(f"No comments found for page {page_id}")
                
                # Store comment records
                await self.arango_service.batch_upsert_nodes(
                    comment_records,
                    CollectionNames.NOTION_COMMENT_RECORD.value
                )

                await self.arango_service.batch_upsert_nodes(
                    general_records,
                    CollectionNames.RECORDS.value
                )

                record_page_edges = []

                for i, general_record in enumerate(general_records):
                    # Get the corresponding Notion page record
                    comment_record = comment_records[i]

                    edge_data = {
                        "_from": f"{CollectionNames.RECORDS.value}/{general_record['_key']}",
                        "_to": f"{CollectionNames.NOTION_COMMENT_RECORD.value}/{comment_record['_key']}",
                        "createdAtTimestamp": general_record["createdAtTimestamp"],
                    }
                    record_page_edges.append(edge_data)

                # Batch create edges between records and notion page records
                await self.arango_service.batch_create_edges(
                    record_page_edges,
                    CollectionNames.IS_OF_TYPE.value,  # Assuming this collection exists
                )
                
                # Create edges between blocks and comments
                await self.arango_service.batch_create_edges(
                    comment_edges,
                    CollectionNames.RECORD_RELATIONS.value,  # Assuming this collection exists
                )
                

        except Exception as e:
            self.logger.error(f"Error fetching block children: {str(e)}")
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

    async def _process_block(self, block_id, block_type, block_content, 
                        created_time, last_edited_time):
        """
        Process an individual Notion block and return formatted content.
        
        Args:
            arango_service: Service for interacting with the database
            block_id: ID of the block
            block_type: Type of the block
            block_content: Content of the block
            created_time: Block creation timestamp
            last_edited_time: Block last edit timestamp
            
        Returns:
            Processed block in standardized format
        """
        try:
            # Initialize the standard block structure
            formatted_block = {
                "block_id": block_id,
                "block_type": block_type,
                "block_name": "",
                "block_format": "txt",  # Default format
                "block_comments": [],
                "block_creation_date": created_time,
                "block_update_date": last_edited_time,
                "links": [],
                "data": None,
                "public_data_link": "",
                "public_data_link_expiration_epoch_time_in_ms": None
            }
            
            # Process text-based blocks
            if block_type in ["paragraph", "heading_1", "heading_2", "heading_3", 
                            "bulleted_list_item", "numbered_list_item", "quote", "callout"]:
                text_content = self._extract_text_from_rich_text(block_content.get("rich_text", []))
                formatted_block["data"] = text_content
                formatted_block["block_format"] = "txt"
                
                # Extract links from rich text if any
                links = self._extract_links_from_rich_text(block_content.get("rich_text", []))
                if links:
                    formatted_block["links"] = links
                    
            # Process to-do items
            elif block_type == "to_do":
                text_content = self._extract_text_from_rich_text(block_content.get("rich_text", []))
                checked = block_content.get("checked", False)
                formatted_block["data"] = {
                    "text": text_content,
                    "checked": checked
                }
                formatted_block["block_format"] = "txt"
                
            # Process toggle blocks
            elif block_type == "toggle":
                text_content = self._extract_text_from_rich_text(block_content.get("rich_text", []))
                formatted_block["data"] = text_content
                formatted_block["block_format"] = "txt"
                
            # Process code blocks
            elif block_type == "code":
                text_content = self._extract_text_from_rich_text(block_content.get("rich_text", []))
                language = block_content.get("language", "")
                formatted_block["data"] = {
                    "code": text_content,
                    "language": language
                }
                formatted_block["block_format"] = "markdown"
                formatted_block["block_name"] = f"code_{language}"
                
            # Process divider
            elif block_type == "divider":
                formatted_block["data"] = "---"
                formatted_block["block_format"] = "markdown"
                
            # Process image blocks
            elif block_type == "image":
                image_type = block_content.get("type")
                caption = self._extract_text_from_rich_text(block_content.get("caption", []))
                
                if image_type == "external":
                    image_url = block_content.get("external", {}).get("url", "")
                elif image_type == "file":
                    image_url = block_content.get("file", {}).get("url", "")
                else:
                    image_url = ""
                    
                formatted_block["data"] = {
                    "url": image_url,
                    "caption": caption
                }
                formatted_block["block_format"] = "bin"
                formatted_block["block_name"] = f"image_{self._generate_filename_from_url(image_url)}"
                formatted_block["public_data_link"] = image_url
                
                # Extract expiry time if available
                expiry = block_content.get("file", {}).get("expiry_time", None)
                if expiry:
                    formatted_block["public_data_link_expiration_epoch_time_in_ms"] = self._convert_to_epoch_ms(expiry)
                    
            # Process file blocks
            elif block_type == "file":
                file_type = block_content.get("type")
                caption = self._extract_text_from_rich_text(block_content.get("caption", []))
                
                if file_type == "external":
                    file_url = block_content.get("external", {}).get("url", "")
                elif file_type == "file":
                    file_url = block_content.get("file", {}).get("url", "")
                else:
                    file_url = ""
                    
                formatted_block["data"] = {
                    "url": file_url,
                    "caption": caption
                }
                formatted_block["block_format"] = "bin"
                formatted_block["block_name"] = f"file_{self._generate_filename_from_url(file_url)}"
                formatted_block["public_data_link"] = file_url
                
                # Extract expiry time if available
                expiry = block_content.get("file", {}).get("expiry_time", None)
                if expiry:
                    formatted_block["public_data_link_expiration_epoch_time_in_ms"] = self._convert_to_epoch_ms(expiry)
                    
            # Process video blocks
            elif block_type == "video":
                video_type = block_content.get("type")
                caption = self._extract_text_from_rich_text(block_content.get("caption", []))
                
                if video_type == "external":
                    video_url = block_content.get("external", {}).get("url", "")
                elif video_type == "file":
                    video_url = block_content.get("file", {}).get("url", "")
                else:
                    video_url = ""
                    
                formatted_block["data"] = {
                    "url": video_url,
                    "caption": caption
                }
                formatted_block["block_format"] = "bin"
                formatted_block["block_name"] = f"video_{self._generate_filename_from_url(video_url)}"
                formatted_block["public_data_link"] = video_url
                
                # Extract expiry time if available
                expiry = block_content.get("file", {}).get("expiry_time", None)
                if expiry:
                    formatted_block["public_data_link_expiration_epoch_time_in_ms"] = self._convert_to_epoch_ms(expiry)
                    
            # Process audio blocks
            elif block_type == "audio":
                audio_type = block_content.get("type")
                caption = self._extract_text_from_rich_text(block_content.get("caption", []))
                
                if audio_type == "external":
                    audio_url = block_content.get("external", {}).get("url", "")
                elif audio_type == "file":
                    audio_url = block_content.get("file", {}).get("url", "")
                else:
                    audio_url = ""
                    
                formatted_block["data"] = {
                    "url": audio_url,
                    "caption": caption
                }
                formatted_block["block_format"] = "bin"
                formatted_block["block_name"] = f"audio_{self._generate_filename_from_url(audio_url)}"
                formatted_block["public_data_link"] = audio_url
                
                # Extract expiry time if available
                expiry = block_content.get("file", {}).get("expiry_time", None)
                if expiry:
                    formatted_block["public_data_link_expiration_epoch_time_in_ms"] = self._convert_to_epoch_ms(expiry)
                    
            # Process bookmark blocks
            elif block_type == "bookmark":
                url = block_content.get("url", "")
                caption = self._extract_text_from_rich_text(block_content.get("caption", []))
                
                formatted_block["data"] = {
                    "url": url,
                    "caption": caption
                }
                formatted_block["block_format"] = "txt"
                formatted_block["links"] = [url]
                
            # Process embed blocks
            elif block_type == "embed":
                url = block_content.get("url", "")
                caption = self._extract_text_from_rich_text(block_content.get("caption", []))
                
                formatted_block["data"] = {
                    "url": url,
                    "caption": caption
                }
                formatted_block["block_format"] = "txt"
                formatted_block["links"] = [url]
                
            # Process web_bookmark blocks
            elif block_type == "link_preview":
                url = block_content.get("url", "")
                
                formatted_block["data"] = {
                    "url": url
                }
                formatted_block["block_format"] = "txt"
                formatted_block["links"] = [url]
                
            # Process table blocks
            elif block_type == "table":
                # Table properties
                has_column_header = block_content.get("has_column_header", False)
                has_row_header = block_content.get("has_row_header", False)
                
                # We would need to fetch the table rows in a separate call
                # This is a placeholder - in a real implementation, you would fetch the children
                table_data = {
                    "has_column_header": has_column_header,
                    "has_row_header": has_row_header,
                    "rows": []  # Would be populated by fetching child blocks
                }
                
                formatted_block["data"] = table_data
                formatted_block["block_format"] = "markdown"
                formatted_block["block_name"] = f"table_{block_id[-8:]}"
                
            # Process equation blocks
            elif block_type == "equation":
                expression = block_content.get("expression", "")
                
                formatted_block["data"] = expression
                formatted_block["block_format"] = "markdown"
                formatted_block["block_name"] = "equation"
                
            # Process table of contents
            elif block_type == "table_of_contents":
                formatted_block["data"] = "Table of Contents"
                formatted_block["block_format"] = "markdown"
                
            # Process breadcrumb
            elif block_type == "breadcrumb":
                formatted_block["data"] = "Breadcrumb"
                formatted_block["block_format"] = "txt"
                
            # Process synced blocks
            elif block_type == "synced_block":
                synced_from = block_content.get("synced_from")
                if synced_from:
                    original_block_id = synced_from.get("block_id")
                    formatted_block["data"] = {
                        "synced_from_id": original_block_id
                    }
                else:
                    formatted_block["data"] = {
                        "is_original": True
                    }
                formatted_block["block_format"] = "txt"
                
            # Process template blocks
            elif block_type == "template":
                text_content = self._extract_text_from_rich_text(block_content.get("rich_text", []))
                formatted_block["data"] = text_content
                formatted_block["block_format"] = "txt"
                formatted_block["block_name"] = "template"
                
            else:
                # For any unsupported block types, store basic info
                formatted_block["data"] = f"Unsupported block type: {block_type}"
                
            # Check if we have comments for this block
            comments = await self._fetch_comments(block_id)
            if comments:
                formatted_block["block_comments"] = comments
                
            return formatted_block
        
        except Exception as e:
            print(f"Error processing block {block_id} of type {block_type}: {str(e)}")
            return None

  
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
            import uuid
            filename = str(uuid.uuid4())[:8]
            
        return filename
        
    def _convert_to_epoch_ms(self, iso_datetime):
        """Convert ISO datetime string to epoch milliseconds"""
        from datetime import datetime
        import calendar
        
        dt = datetime.fromisoformat(iso_datetime.replace("Z", "+00:00"))
        return calendar.timegm(dt.utctimetuple()) * 1000 + dt.microsecond // 1000
        

    async def store_file_block(self,page,file_url,name,createdAt,lastEditedAt):
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
                "externalRecordId": page.get("page_id", ""),
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
            print(file_record)
            print(general_record)
            record_edge_data = {
                "_from": f"{CollectionNames.RECORDS.value}/{key}",
                "_to": f"{CollectionNames.FILES.value}/{key}",
                "createdAtTimestamp": general_record["createdAtTimestamp"],
            }

            record_relation_edge_data = {
                "_from": f"{CollectionNames.NOTION_PAGE_RECORD.value}/{page['_key']}",
                "_to": f"{CollectionNames.FILES.value}/{key}",
                "relationship": "PARENT_CHILD",
                "createdAtTimestamp": int(datetime.now().timestamp() * 1000)
            }

            self.logger.info(f"create file records")
            return file_record,general_record,record_edge_data,record_relation_edge_data
    
            
        except Exception as e:
            self.logger.error(f"Error storing file block: {str(e)}")
            raise
    
    

## Entry point for creating notion records
    async def fetch_and_create_notion_records(self):
        try:
            self.logger.info("fetching the pages and creating records")

            workspace_record= await self._fetch_current_workspace()
            users = await self._fetch_and_store_users(workspace_record)

            page_records = await self._process_and_store_pages(workspace_record)
            database_records = await self._process_and_store_databases(workspace_record)
            yield 
          

        except:
            yield