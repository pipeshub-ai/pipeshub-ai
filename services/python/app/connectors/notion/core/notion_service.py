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
    
    def __init__(self, integration_secret: str, org_id: str, logger=None):
        """Initialize Notion service with integration secret."""
        self.integration_secret = integration_secret
        self.org_id = org_id
        self.logger = logger or logging.getLogger(__name__)
        self.headers = {
            "Authorization": f"Bearer {integration_secret}",
            "Notion-Version": "2022-06-28",
            "Content-Type": "application/json"
        }
    
    async def fetch_all_pages(self) -> List[Dict[str, Any]]:
        """Fetch all pages accessible with the integration secret."""
        try:
            self.logger.info("Fetching all Notion pages...")
            url = f"{self.BASE_URL}/search"
            
            params = {
                "filter": {
                    "value": "page",
                    "property": "object"
                },
                "page_size": 100
            }
            
            all_pages = []
            has_more = True
            next_cursor = None
            
            async with aiohttp.ClientSession() as session:
                while has_more:
                    if next_cursor:
                        params["start_cursor"] = next_cursor
                    
                    async with session.post(url, headers=self.headers, json=params) as response:
                        if response.status != 200:
                            error_text = await response.text()
                            self.logger.error(f"Failed to fetch Notion pages: {error_text}")
                            raise Exception(f"Notion API error: {response.status}")
                        
                        data = await response.json()
                        all_pages.extend(data.get("results", []))
                        
                        has_more = data.get("has_more", False)
                        next_cursor = data.get("next_cursor")
                        
                        self.logger.info(f"Fetched {len(all_pages)} Notion pages so far")
            
            self.logger.info(f"Successfully fetched all {len(all_pages)} Notion pages")
            return all_pages
        
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
                        title_text = "".join([part.get("plain_text", "") for part in title_parts])
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
                truncated_db_id = database_id[-4:] if len(database_id) >= 4 else database_id
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
    
    def transform_pages(self, pages: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
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
                title = "Untitled Page"
            url = page.get("url", "")
            
            # Create Notion page record
            notion_page_record = {
                "page_id": page.get("id", ""),
                "title": title,
                "url": url,
                "parent": page.get("parent", {}),
                "isArchived": page.get("archived", False),
                "isDeleted": False,
                "hasChildren": page.get("has_children", False),
                "propertyValues": page.get("properties", {}),
                "rawData": page
            }
            
            # Create general record
            general_record = {
                "_key": str(uuid4()),
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
                "sourceCreatedAtTimestamp": self._iso_to_timestamp(page.get("created_time")),
                "sourceLastModifiedTimestamp": self._iso_to_timestamp(page.get("last_edited_time")),
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
    
    async def process_and_store_pages(self, arango_service) -> Tuple[int, int]:
        """
        Fetch, transform, and store Notion pages in the database.
        
        Returns:
            Tuple of (notion_pages_count, general_records_count)
        """
        try:
            # Fetch all pages
            pages = await self.fetch_all_pages()
            
            # Transform pages into records
            notion_page_records, general_records = self.transform_pages(pages)
            
            # Store Notion page records
            if notion_page_records:
                await arango_service.batch_upsert_nodes(
                    notion_page_records, CollectionNames.NOTION_PAGE_RECORD.value
                )
            print(general_records)
            # Store general records
            if general_records:
                await arango_service.batch_upsert_nodes(
                    general_records, CollectionNames.RECORDS.value
                )
                
                record_page_edges = []
                for i, general_record in enumerate(general_records):
                    # Get the corresponding Notion page record
                    page_record = notion_page_records[i]
                    
                    # Format the page_id to match ArangoDB's key format
                    notion_page_id = page_record["page_id"].replace("-", "")
                    
                    edge_data = {
                        "_from": f"records/{general_record['_key']}",
                        "_to": f"notion_page_records/{notion_page_id}",
                        "createdAtTimestamp": general_record['createdAtTimestamp'],
                    }
                    record_page_edges.append(edge_data)
                
                # Batch create edges between records and notion page records
                await arango_service.batch_create_edges(
                    record_page_edges,
                    CollectionNames.IS_OF_TYPE.value,  # Assuming this collection exists
                )
            
            return len(notion_page_records), len(general_records)
            
        except Exception as e:
            self.logger.error(f"Error processing and storing Notion pages: {str(e)}")
            raise


    async def fetch_all_databases(self) -> List[Dict[str, Any]]:
        """Fetch all databases accessible with the integration secret."""
        try:
            self.logger.info("Fetching all Notion databases...")
            url = f"{self.BASE_URL}/search"
            
            params = {
                "filter": {
                    "value": "database",
                    "property": "object"
                },
                "page_size": 100
            }
            
            all_databases = []
            has_more = True
            next_cursor = None
            
            async with aiohttp.ClientSession() as session:
                while has_more:
                    if next_cursor:
                        params["start_cursor"] = next_cursor
                    
                    async with session.post(url, headers=self.headers, json=params) as response:
                        if response.status != 200:
                            error_text = await response.text()
                            self.logger.error(f"Failed to fetch Notion databases: {error_text}")
                            raise Exception(f"Notion API error: {response.status}")
                        
                        data = await response.json()
                        all_databases.extend(data.get("results", []))
                        
                        has_more = data.get("has_more", False)
                        next_cursor = data.get("next_cursor")
                        
                        self.logger.info(f"Fetched {len(all_databases)} Notion databases so far")
            
            self.logger.info(f"Successfully fetched all {len(all_databases)} Notion databases")
            return all_databases
        
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

    def transform_databases(self, databases: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
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
            url = database.get("url", "")
            
            # Create Notion database record
            notion_database_record = {
                "database_id": database.get("id", ""),
                "title": title,
                "url": url,
                "parent": database.get("parent", {}),
                "isArchived": database.get("archived", False),
                "isDeleted": False,
                "properties": database.get("properties", {}),
                "rawData": database
            }
            
            # Create general record
            general_record = {
                "_key": str(uuid4()),
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
                "sourceCreatedAtTimestamp": self._iso_to_timestamp(database.get("created_time")),
                "sourceLastModifiedTimestamp": self._iso_to_timestamp(database.get("last_edited_time")),
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
    async def process_and_store_databases(self, arango_service) -> Tuple[int, int]:
        """
        Fetch, transform, and store Notion databases in the database.
        
        Returns:
            Tuple of (notion_databases_count, general_records_count)
        """
        try:
            # Fetch all databases
            databases = await self.fetch_all_databases()
            
            # Transform databases into records
            notion_database_records, general_records = self.transform_databases(databases)
            
            # Store Notion database records
            if notion_database_records:
                await arango_service.batch_upsert_nodes(
                    notion_database_records, CollectionNames.NOTION_DATABASE_RECORD.value
                )
            
            # Store general records
            if general_records:
                await arango_service.batch_upsert_nodes(
                    general_records, CollectionNames.RECORDS.value
                )
                
                record_database_edges = []
                for i, general_record in enumerate(general_records):
                    # Get the corresponding Notion database record
                    database_record = notion_database_records[i]
                    
                    # Format the database_id to match ArangoDB's key format
                    notion_database_id = database_record["database_id"].replace("-", "")
                    
                    edge_data = {
                        "_from": f"records/{general_record['_key']}",
                        "_to": f"notion_database_records/{notion_database_id}",
                        "createdAtTimestamp": general_record['createdAtTimestamp'],
                    }
                    record_database_edges.append(edge_data)
                
                # Batch create edges between records and notion database records
                await arango_service.batch_create_edges(
                    record_database_edges,
                    CollectionNames.IS_OF_TYPE.value,
                )
            
            return len(notion_database_records), len(general_records)
            
        except Exception as e:
            self.logger.error(f"Error processing and storing Notion databases: {str(e)}")
            raise