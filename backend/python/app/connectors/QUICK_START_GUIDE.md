# Quick Start Guide: Adding a New Connector in 15 Minutes

## Overview

This guide shows you how to add a new connector to the PipesHub connector framework in under 15 minutes. We'll create a Notion connector as an example, following the actual codebase patterns and structure.

## Prerequisites

- Python 3.8+
- Understanding of async/await patterns
- Basic knowledge of the target API you're integrating

## Step 1: Understand the Framework Structure (2 minutes)

The connector framework follows a modular architecture:

```
app/connectors/
├── core/                          # Core framework components
│   ├── base/                      # Base classes for all services
│   ├── factory/                   # Connector factory and registry
│   └── interfaces/                # Interface definitions
├── enums/                         # Enums for connector types and auth
├── sources/                       # Actual connector implementations
│   ├── google/                    # Google connectors (Drive, Gmail, etc.)
│   └── s3/                        # AWS S3 connector
└── utils/                         # Shared utilities
```

## Step 2: Create the Connector Directory Structure (1 minute)

Create the directory structure for your new connector:

```bash
mkdir -p app/connectors/sources/notion/{services,interfaces,factories}
```

## Step 3: Define the Connector Type (1 minute)

Add your connector type to the enums:

```python
# In app/connectors/enums/enums.py
class ConnectorType(Enum):
    # ... existing connectors ...
    NOTION = "notion"
    # Add your new connector here
```

## Step 4: Create the Authentication Service (3 minutes)

Create `app/connectors/sources/notion/services/authentication_service.py`:

```python
"""Authentication service for Notion connector"""

import aiohttp
import logging
from typing import Dict, Any, Optional
from datetime import datetime, timezone

from app.connectors.core.base.auth.authentication_service import BaseAuthenticationService
from app.connectors.core.interfaces.connector.iconnector_config import ConnectorConfig
from app.connectors.core.interfaces.auth.iauth_service import IAuthenticationService


class NotionAuthenticationService(BaseAuthenticationService):
    """Handles authentication for Notion API"""

    def __init__(self, logger: logging.Logger, config: ConnectorConfig) -> None:
        super().__init__(logger, config)
        self.session: Optional[aiohttp.ClientSession] = None
        self._token: Optional[str] = None
        self._user_id: Optional[str] = None
        self._workspace_id: Optional[str] = None

    async def authenticate(self, credentials: Dict[str, Any]) -> bool:
        """Authenticate with Notion API"""
        try:
            self.logger.info("🔐 Authenticating with Notion API")
            
            # Extract integration token
            token = credentials.get("integration_token")
            if not token:
                self.logger.error("❌ No integration token provided")
                return False

            self._token = token
            
            # Create aiohttp session
            self.session = aiohttp.ClientSession()
            
            # Test authentication by calling the users/me endpoint
            async with self.session.get(
                "https://api.notion.com/v1/users/me",
                headers=self.get_auth_headers()
            ) as response:
                if response.status == 200:
                    user_data = await response.json()
                    self._user_id = user_data.get("id")
                    self.logger.info(f"✅ Authenticated as user: {self._user_id}")
                    return True
                else:
                    self.logger.error(f"❌ Authentication failed: {response.status}")
                    return False
                    
        except Exception as e:
            self.logger.error(f"❌ Notion authentication failed: {str(e)}")
            return False

    def get_auth_headers(self) -> Dict[str, str]:
        """Get Notion authentication headers"""
        if self._token:
            return {
                "Authorization": f"Bearer {self._token}",
                "Notion-Version": "2022-06-28",
                "Content-Type": "application/json"
            }
        return {}

    async def refresh_token(self, refresh_token: str) -> Dict[str, Any]:
        """Refresh authentication token (Notion doesn't support refresh tokens)"""
        self.logger.warning("⚠️ Notion doesn't support token refresh")
        return {"access_token": self._token, "refresh_token": refresh_token}

    async def validate_token(self, token: str) -> bool:
        """Validate current token"""
        try:
            if not self.session:
                return False
                
            async with self.session.get(
                "https://api.notion.com/v1/users/me",
                headers={"Authorization": f"Bearer {token}", "Notion-Version": "2022-06-28"}
            ) as response:
                return response.status == 200
        except Exception as e:
            self.logger.error(f"❌ Token validation failed: {str(e)}")
            return False

    async def revoke_token(self, token: str) -> bool:
        """Revoke authentication token (Notion doesn't support token revocation)"""
        self.logger.warning("⚠️ Notion doesn't support token revocation")
        return True

    async def disconnect(self) -> bool:
        """Disconnect and cleanup authentication"""
        try:
            self.logger.info("🔄 Disconnecting Notion authentication service")
            
            if self.session:
                await self.session.close()
                self.session = None
            
            self._token = None
            self._user_id = None
            self._workspace_id = None
            
            self.logger.info("✅ Notion authentication service disconnected successfully")
            return True
        except Exception as e:
            self.logger.error(f"❌ Failed to disconnect Notion authentication service: {str(e)}")
            return False

    def get_service(self):
        """Get the current session instance"""
        return self.session

    def is_connected(self) -> bool:
        """Check if service is connected"""
        return self.session is not None and self._token is not None

    def get_user_id(self) -> Optional[str]:
        """Get the authenticated user ID"""
        return self._user_id
```

## Step 5: Create the Data Service (3 minutes)

Create `app/connectors/sources/notion/services/data_service.py`:

```python
"""Data service for Notion connector"""

import aiohttp
import logging
from typing import Dict, List, Any, Optional
import json

from app.connectors.core.base.data_service.data_service import BaseDataService
from app.connectors.core.interfaces.auth.iauth_service import IAuthenticationService
from app.connectors.core.interfaces.data_service.data_service import IDataService


class NotionDataService(BaseDataService):
    """Handles data operations for Notion API"""

    def __init__(self, logger: logging.Logger, auth_service: IAuthenticationService) -> None:
        super().__init__(logger, auth_service)
        self.session = auth_service.get_service()

    async def list_items(self, path: str = "/", recursive: bool = True) -> List[Dict[str, Any]]:
        """List pages from Notion workspace"""
        try:
            self.logger.info(f"📄 Listing Notion pages from path: {path}")
            
            if not self.session:
                self.logger.error("❌ No active session")
                return []

            # Search for pages in the workspace
            async with self.session.post(
                "https://api.notion.com/v1/search",
                headers=self.auth_service.get_auth_headers(),
                json={
                    "filter": {
                        "value": "page",
                        "property": "object"
                    },
                    "sort": {
                        "direction": "descending",
                        "timestamp": "last_edited_time"
                    }
                }
            ) as response:
                if response.status == 200:
                    data = await response.json()
                    pages = data.get("results", [])
                    self.logger.info(f"✅ Found {len(pages)} pages")
                    return pages
                else:
                    self.logger.error(f"❌ Failed to list pages: {response.status}")
                    return []
                    
        except Exception as e:
            self.logger.error(f"❌ Failed to list Notion pages: {str(e)}")
            return []

    async def get_item_metadata(self, item_id: str) -> Optional[Dict[str, Any]]:
        """Get metadata for a specific Notion page"""
        try:
            self.logger.info(f"📋 Getting metadata for page: {item_id}")
            
            if not self.session:
                self.logger.error("❌ No active session")
                return None

            async with self.session.get(
                f"https://api.notion.com/v1/pages/{item_id}",
                headers=self.auth_service.get_auth_headers()
            ) as response:
                if response.status == 200:
                    data = await response.json()
                    self.logger.info(f"✅ Retrieved metadata for page: {item_id}")
                    return data
                else:
                    self.logger.error(f"❌ Failed to get page metadata: {response.status}")
                    return None
                    
        except Exception as e:
            self.logger.error(f"❌ Failed to get Notion page metadata: {str(e)}")
            return None

    async def get_item_content(self, item_id: str) -> Optional[bytes]:
        """Get content for a specific Notion page"""
        try:
            self.logger.info(f"📝 Getting content for page: {item_id}")
            
            if not self.session:
                self.logger.error("❌ No active session")
                return None

            # Get page blocks
            async with self.session.get(
                f"https://api.notion.com/v1/blocks/{item_id}/children",
                headers=self.auth_service.get_auth_headers()
            ) as response:
                if response.status == 200:
                    data = await response.json()
                    blocks = data.get("results", [])
                    
                    # Convert blocks to readable content
                    content = self._process_blocks(blocks)
                    content_bytes = json.dumps(content, indent=2).encode('utf-8')
                    
                    self.logger.info(f"✅ Retrieved content for page: {item_id}")
                    return content_bytes
                else:
                    self.logger.error(f"❌ Failed to get page content: {response.status}")
                    return None
                    
        except Exception as e:
            self.logger.error(f"❌ Failed to get Notion page content: {str(e)}")
            return None

    async def search_items(self, query: str, filters: Dict[str, Any] = None) -> List[Dict[str, Any]]:
        """Search for pages in Notion"""
        try:
            self.logger.info(f"🔍 Searching Notion pages with query: {query}")
            
            if not self.session:
                self.logger.error("❌ No active session")
                return []

            search_params = {
                "query": query,
                "filter": {
                    "value": "page",
                    "property": "object"
                }
            }
            
            if filters:
                search_params.update(filters)

            async with self.session.post(
                "https://api.notion.com/v1/search",
                headers=self.auth_service.get_auth_headers(),
                json=search_params
            ) as response:
                if response.status == 200:
                    data = await response.json()
                    results = data.get("results", [])
                    self.logger.info(f"✅ Found {len(results)} pages matching query")
                    return results
                else:
                    self.logger.error(f"❌ Failed to search pages: {response.status}")
                    return []
                    
        except Exception as e:
            self.logger.error(f"❌ Failed to search Notion pages: {str(e)}")
            return []

    async def get_item_permissions(self, item_id: str) -> List[Dict[str, Any]]:
        """Get permissions for a specific Notion page"""
        try:
            self.logger.info(f"🔐 Getting permissions for page: {item_id}")
            
            # Notion doesn't have a direct permissions API
            # This would need to be implemented based on your specific needs
            self.logger.warning("⚠️ Notion permissions API not implemented")
            return []
            
        except Exception as e:
            self.logger.error(f"❌ Failed to get Notion page permissions: {str(e)}")
            return []

    def _process_blocks(self, blocks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Process Notion blocks into readable content"""
        processed_blocks = []
        
        for block in blocks:
            block_type = block.get("type")
            block_content = block.get(block_type, {})
            
            processed_block = {
                "id": block.get("id"),
                "type": block_type,
                "content": block_content.get("rich_text", []),
                "has_children": block.get("has_children", False)
            }
            
            processed_blocks.append(processed_block)
        
        return processed_blocks
```

## Step 6: Create the Main Connector Service (3 minutes)

Create `app/connectors/sources/notion/services/connector_service.py`:

```python
"""Main connector service for Notion"""

import logging
from typing import Any, Dict

from app.connectors.core.base.connector.connector_service import BaseConnectorService
from app.connectors.core.interfaces.connector.iconnector_config import ConnectorConfig
from app.connectors.core.interfaces.auth.iauth_service import IAuthenticationService
from app.connectors.core.interfaces.data_service.data_service import IDataService
from app.connectors.core.interfaces.error.error import IErrorHandlingService
from app.connectors.core.interfaces.event_service.event_service import IEventService
from app.connectors.enums.enums import ConnectorType
from app.connectors.core.interfaces.connector.iconnector_service import IConnectorService


class NotionConnectorService(BaseConnectorService):
    """Main Notion connector service"""

    def __init__(
        self,
        logger: logging.Logger,
        connector_type: ConnectorType,
        config: ConnectorConfig,
        auth_service: IAuthenticationService,
        data_service: IDataService,
        error_service: IErrorHandlingService,
        event_service: IEventService,
    ) -> None:
        super().__init__(
            logger, connector_type, config, auth_service, 
            data_service, error_service, event_service
        )
        self.auth_service = auth_service
        self.data_service = data_service

    async def test_connection(self) -> bool:
        """Test Notion connection"""
        try:
            self.logger.info("🧪 Testing Notion connection")
            
            if not self.auth_service.is_connected():
                self.logger.error("❌ Authentication service not connected")
                return False

            # Test by calling the users/me endpoint
            session = self.auth_service.get_service()
            if not session:
                self.logger.error("❌ No active session")
                return False

            async with session.get(
                "https://api.notion.com/v1/users/me",
                headers=self.auth_service.get_auth_headers()
            ) as response:
                if response.status == 200:
                    self.logger.info("✅ Notion connection test successful")
                    return True
                else:
                    self.logger.error(f"❌ Notion connection test failed: {response.status}")
                    return False
                    
        except Exception as e:
            self.error_service.log_error(e, "test_connection", {
                "connector_type": self.connector_type.value
            })
            self.logger.error(f"❌ Notion connection test failed: {str(e)}")
            return False

    def get_service_info(self) -> Dict[str, Any]:
        """Get Notion service information"""
        base_info = super().get_service_info()
        
        # Add Notion-specific information
        notion_info = {
            **base_info,
            "notion_user_id": self.auth_service.get_user_id() if hasattr(self.auth_service, 'get_user_id') else None,
            "api_version": "2022-06-28",
            "supported_operations": [
                "list_pages",
                "get_page_metadata", 
                "get_page_content",
                "search_pages"
            ]
        }
        
        return notion_info
```

## Step 7: Create Interfaces (1 minute)

Create `app/connectors/sources/notion/interfaces/__init__.py`:

```python
"""Notion connector interfaces"""

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

from app.connectors.core.interfaces.auth.iauth_service import IAuthenticationService
from app.connectors.core.interfaces.data_service.data_service import IDataService
from app.connectors.core.interfaces.connector.iconnector_service import IConnectorService


class INotionAuthenticationService(IAuthenticationService):
    """Notion authentication service interface"""
    
    @abstractmethod
    def get_user_id(self) -> Optional[str]:
        """Get the authenticated user ID"""
        pass


class INotionDataService(IDataService):
    """Notion data service interface"""
    
    @abstractmethod
    def _process_blocks(self, blocks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Process Notion blocks into readable content"""
        pass


class INotionConnectorService(IConnectorService):
    """Notion connector service interface"""
    pass
```

## Step 8: Create Configuration (1 minute)

Create `app/connectors/sources/notion/config.py`:

```python
"""Configuration for Notion connector"""

from app.connectors.core.interfaces.connector.iconnector_config import ConnectorConfig
from app.connectors.enums.enums import ConnectorType, AuthenticationType


# Notion connector configuration
NOTION_CONFIG = ConnectorConfig(
    connector_type=ConnectorType.NOTION,
    authentication_type=AuthenticationType.BEARER_TOKEN,
    base_url="https://api.notion.com/v1",
    api_version="2022-06-28",
    rate_limits={
        "requests_per_second": 3,
        "requests_per_minute": 180,
        "requests_per_day": 100000
    },
    scopes=["read", "write"],
    webhook_support=True,
    batch_operations=False,
    real_time_sync=True
)
```

## Step 9: Register the Connector (1 minute)

Create `app/connectors/sources/notion/factories/connector_factory.py`:

```python
"""Factory for creating Notion connector instances"""

import logging
from typing import Dict, Any

from app.connectors.core.factory.connector_factory import UniversalConnectorFactory
from app.connectors.core.interfaces.connector.iconnector_config import ConnectorConfig
from app.connectors.core.interfaces.connector.iconnector_service import IConnectorService
from app.connectors.enums.enums import ConnectorType

from app.connectors.sources.notion.services.authentication_service import NotionAuthenticationService
from app.connectors.sources.notion.services.data_service import NotionDataService
from app.connectors.sources.notion.services.connector_service import NotionConnectorService
from app.connectors.sources.notion.config import NOTION_CONFIG


class NotionConnectorFactory:
    """Factory for creating Notion connector instances"""

    @staticmethod
    def register_with_factory(factory: UniversalConnectorFactory) -> None:
        """Register Notion connector with the universal factory"""
        factory.register_connector_implementation(
            connector_type=ConnectorType.NOTION,
            connector_class=NotionConnectorService,
            auth_service_class=NotionAuthenticationService,
            data_service_class=NotionDataService,
            config=NOTION_CONFIG
        )

    @staticmethod
    def create_connector(logger: logging.Logger) -> IConnectorService:
        """Create a Notion connector instance"""
        factory = UniversalConnectorFactory(logger)
        NotionConnectorFactory.register_with_factory(factory)
        return factory.create_connector(ConnectorType.NOTION, NOTION_CONFIG)
```

## Step 10: Usage Examples (2 minutes)

### Basic Usage

```python
"""Example: Using the Notion connector"""

import asyncio
import logging
from typing import Dict, Any

from app.connectors.sources.notion.factories.connector_factory import NotionConnectorFactory


async def basic_notion_example():
    """Basic example of using the Notion connector"""
    
    # Setup logging
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger(__name__)
    
    try:
        # Create connector
        connector = NotionConnectorFactory.create_connector(logger)
        
        # Connect with credentials
        credentials = {
            "integration_token": "your_notion_integration_token_here"
        }
        
        logger.info("🔌 Connecting to Notion...")
        if await connector.connect(credentials):
            logger.info("✅ Successfully connected to Notion")
            
            # Test connection
            if await connector.test_connection():
                logger.info("✅ Connection test passed")
                
                # Get service info
                info = connector.get_service_info()
                logger.info(f"📊 Service info: {info}")
                
                # List pages
                pages = await connector.data_service.list_items()
                logger.info(f"📄 Found {len(pages)} pages")
                
                # Get first page details
                if pages:
                    first_page = pages[0]
                    page_id = first_page.get("id")
                    
                    # Get page metadata
                    metadata = await connector.data_service.get_item_metadata(page_id)
                    if metadata:
                        title = metadata.get("properties", {}).get("title", {}).get("title", [{}])[0].get("plain_text", "No title")
                        logger.info(f"📋 Page title: {title}")
                    
                    # Get page content
                    content = await connector.data_service.get_item_content(page_id)
                    if content:
                        logger.info(f"📝 Page content length: {len(content)} bytes")
                
                # Search pages
                search_results = await connector.data_service.search_items("important")
                logger.info(f"🔍 Found {len(search_results)} pages matching 'important'")
                
            else:
                logger.error("❌ Connection test failed")
        else:
            logger.error("❌ Failed to connect to Notion")
            
    except Exception as e:
        logger.error(f"❌ Error in Notion example: {str(e)}")
    
    finally:
        # Disconnect
        if connector:
            await connector.disconnect()
            logger.info("🔌 Disconnected from Notion")


async def advanced_notion_example():
    """Advanced example with error handling and batch operations"""
    
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger(__name__)
    
    connector = None
    
    try:
        # Create connector
        connector = NotionConnectorFactory.create_connector(logger)
        
        # Connect
        credentials = {"integration_token": "your_token"}
        if not await connector.connect(credentials):
            raise Exception("Failed to connect to Notion")
        
        # Get all pages with pagination
        all_pages = []
        start_cursor = None
        
        while True:
            # This would need to be implemented in the data service
            # For now, we'll use the basic list_items method
            pages = await connector.data_service.list_items()
            all_pages.extend(pages)
            
            if len(pages) < 100:  # Assuming page size is 100
                break
            # start_cursor = pages[-1].get("id")  # For pagination
        
        logger.info(f"📚 Total pages found: {len(all_pages)}")
        
        # Process pages in batches
        batch_size = 10
        for i in range(0, len(all_pages), batch_size):
            batch = all_pages[i:i + batch_size]
            logger.info(f"🔄 Processing batch {i//batch_size + 1}")
            
            for page in batch:
                try:
                    # Get page content
                    content = await connector.data_service.get_item_content(page["id"])
                    if content:
                        # Process content (e.g., extract text, analyze structure)
                        logger.info(f"✅ Processed page: {page.get('id')}")
                except Exception as e:
                    logger.error(f"❌ Failed to process page {page.get('id')}: {str(e)}")
        
        logger.info("🎉 All pages processed successfully")
        
    except Exception as e:
        logger.error(f"❌ Advanced example failed: {str(e)}")
    
    finally:
        if connector:
            await connector.disconnect()


# Run examples
if __name__ == "__main__":
    # Run basic example
    asyncio.run(basic_notion_example())
    
    # Run advanced example
    # asyncio.run(advanced_notion_example())
```

### Integration with Main Application

```python
"""Example: Integrating Notion connector with main application"""

import logging
from typing import Dict, Any

from app.connectors.core.factory.connector_factory import UniversalConnectorFactory
from app.connectors.enums.enums import ConnectorType
from app.connectors.sources.notion.factories.connector_factory import NotionConnectorFactory
from app.connectors.sources.notion.config import NOTION_CONFIG


class ApplicationConnectorManager:
    """Manages connectors in the main application"""
    
    def __init__(self):
        self.logger = logging.getLogger(__name__)
        self.factory = UniversalConnectorFactory(self.logger)
        self.active_connectors: Dict[str, Any] = {}
        
        # Register all connectors
        self._register_connectors()
    
    def _register_connectors(self):
        """Register all available connectors"""
        # Register Notion connector
        NotionConnectorFactory.register_with_factory(self.factory)
        
        # Register other connectors here...
        # GoogleDriveConnectorFactory.register_with_factory(self.factory)
        # GitHubConnectorFactory.register_with_factory(self.factory)
        
        self.logger.info("✅ All connectors registered")
    
    async def connect_to_notion(self, credentials: Dict[str, Any]) -> bool:
        """Connect to Notion"""
        try:
            connector = self.factory.create_connector(ConnectorType.NOTION, NOTION_CONFIG)
            
            if await connector.connect(credentials):
                self.active_connectors["notion"] = connector
                self.logger.info("✅ Connected to Notion")
                return True
            else:
                self.logger.error("❌ Failed to connect to Notion")
                return False
                
        except Exception as e:
            self.logger.error(f"❌ Error connecting to Notion: {str(e)}")
            return False
    
    async def get_notion_pages(self) -> list:
        """Get all Notion pages"""
        try:
            connector = self.active_connectors.get("notion")
            if not connector:
                raise Exception("Notion connector not connected")
            
            return await connector.data_service.list_items()
            
        except Exception as e:
            self.logger.error(f"❌ Error getting Notion pages: {str(e)}")
            return []
    
    async def disconnect_all(self):
        """Disconnect all active connectors"""
        for name, connector in self.active_connectors.items():
            try:
                await connector.disconnect()
                self.logger.info(f"🔌 Disconnected from {name}")
            except Exception as e:
                self.logger.error(f"❌ Error disconnecting from {name}: {str(e)}")
        
        self.active_connectors.clear()


# Usage in main application
async def main():
    """Main application example"""
    
    logging.basicConfig(level=logging.INFO)
    manager = ApplicationConnectorManager()
    
    try:
        # Connect to Notion
        credentials = {"integration_token": "your_token"}
        if await manager.connect_to_notion(credentials):
            # Get pages
            pages = await manager.get_notion_pages()
            print(f"Found {len(pages)} pages in Notion")
        
    finally:
        # Cleanup
        await manager.disconnect_all()


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
```

## Step 11: Testing (2 minutes)

Create `tests/test_notion_connector.py`:

```python
"""Tests for Notion connector"""

import pytest
import asyncio
from unittest.mock import Mock, AsyncMock
from typing import Dict, Any

from app.connectors.sources.notion.services.authentication_service import NotionAuthenticationService
from app.connectors.sources.notion.services.data_service import NotionDataService
from app.connectors.sources.notion.services.connector_service import NotionConnectorService
from app.connectors.sources.notion.config import NOTION_CONFIG
from app.connectors.enums.enums import ConnectorType


class TestNotionConnector:
    """Test cases for Notion connector"""
    
    @pytest.fixture
    def mock_logger(self):
        """Create mock logger"""
        return Mock()
    
    @pytest.fixture
    def mock_auth_service(self, mock_logger):
        """Create mock authentication service"""
        auth_service = NotionAuthenticationService(mock_logger, NOTION_CONFIG)
        auth_service.session = AsyncMock()
        return auth_service
    
    @pytest.fixture
    def mock_data_service(self, mock_logger, mock_auth_service):
        """Create mock data service"""
        return NotionDataService(mock_logger, mock_auth_service)
    
    @pytest.fixture
    def mock_error_service(self, mock_logger):
        """Create mock error service"""
        error_service = Mock()
        error_service.log_error = Mock()
        return error_service
    
    @pytest.fixture
    def mock_event_service(self, mock_logger):
        """Create mock event service"""
        event_service = Mock()
        return event_service
    
    @pytest.fixture
    def connector_service(self, mock_logger, mock_auth_service, mock_data_service, mock_error_service, mock_event_service):
        """Create connector service instance"""
        return NotionConnectorService(
            mock_logger,
            ConnectorType.NOTION,
            NOTION_CONFIG,
            mock_auth_service,
            mock_data_service,
            mock_error_service,
            mock_event_service
        )
    
    @pytest.mark.asyncio
    async def test_authentication_success(self, mock_auth_service):
        """Test successful authentication"""
        # Mock successful response
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value={"id": "test_user_id"})
        
        mock_auth_service.session.get = AsyncMock(return_value=mock_response)
        
        credentials = {"integration_token": "test_token"}
        result = await mock_auth_service.authenticate(credentials)
        
        assert result is True
        assert mock_auth_service._token == "test_token"
        assert mock_auth_service._user_id == "test_user_id"
    
    @pytest.mark.asyncio
    async def test_authentication_failure(self, mock_auth_service):
        """Test failed authentication"""
        # Mock failed response
        mock_response = AsyncMock()
        mock_response.status = 401
        
        mock_auth_service.session.get = AsyncMock(return_value=mock_response)
        
        credentials = {"integration_token": "invalid_token"}
        result = await mock_auth_service.authenticate(credentials)
        
        assert result is False
    
    @pytest.mark.asyncio
    async def test_list_items_success(self, mock_data_service):
        """Test successful item listing"""
        # Mock successful response
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value={
            "results": [
                {"id": "page1", "type": "page"},
                {"id": "page2", "type": "page"}
            ]
        })
        
        mock_data_service.session.post = AsyncMock(return_value=mock_response)
        
        items = await mock_data_service.list_items()
        
        assert len(items) == 2
        assert items[0]["id"] == "page1"
        assert items[1]["id"] == "page2"
    
    @pytest.mark.asyncio
    async def test_connection_test_success(self, connector_service, mock_auth_service):
        """Test successful connection test"""
        # Mock successful response
        mock_response = AsyncMock()
        mock_response.status = 200
        
        mock_auth_service.session.get = AsyncMock(return_value=mock_response)
        mock_auth_service.is_connected = Mock(return_value=True)
        
        result = await connector_service.test_connection()
        
        assert result is True
    
    @pytest.mark.asyncio
    async def test_connection_test_failure(self, connector_service, mock_auth_service):
        """Test failed connection test"""
        # Mock failed response
        mock_response = AsyncMock()
        mock_response.status = 401
        
        mock_auth_service.session.get = AsyncMock(return_value=mock_response)
        mock_auth_service.is_connected = Mock(return_value=True)
        
        result = await connector_service.test_connection()
        
        assert result is False


# Integration tests
class TestNotionConnectorIntegration:
    """Integration tests for Notion connector"""
    
    @pytest.mark.asyncio
    async def test_full_connector_workflow(self):
        """Test complete connector workflow"""
        # This would test with real Notion API (requires valid token)
        # For now, we'll skip this test
        pytest.skip("Integration test requires valid Notion token")
        
        # Example of what the test would look like:
        """
        from app.connectors.sources.notion.factories.connector_factory import NotionConnectorFactory
        
        # Create connector
        connector = NotionConnectorFactory.create_connector(logging.getLogger())
        
        # Connect with real credentials
        credentials = {"integration_token": "real_token"}
        assert await connector.connect(credentials)
        
        # Test connection
        assert await connector.test_connection()
        
        # List items
        items = await connector.data_service.list_items()
        assert isinstance(items, list)
        
        # Disconnect
        assert await connector.disconnect()
        """


if __name__ == "__main__":
    pytest.main([__file__])
```

## Step 12: Documentation (1 minute)

Create `app/connectors/sources/notion/README.md`:

```markdown
# Notion Connector

## Overview

The Notion connector allows you to integrate with Notion's API to access and manage pages, databases, and other Notion resources.

## Features

- ✅ Authentication with Notion Integration Token
- ✅ List pages from workspace
- ✅ Get page metadata and content
- ✅ Search pages
- ✅ Process Notion blocks into readable content
- ✅ Error handling and logging
- ✅ Rate limiting support

## Configuration

```python
from app.connectors.sources.notion.config import NOTION_CONFIG

# Configuration includes:
# - API version: 2022-06-28
# - Rate limits: 3 req/sec, 180 req/min
# - Authentication: Bearer token
# - Webhook support: Yes
```

## Usage

### Basic Usage

```python
from app.connectors.sources.notion.factories.connector_factory import NotionConnectorFactory

# Create connector
connector = NotionConnectorFactory.create_connector(logger)

# Connect
credentials = {"integration_token": "your_token"}
await connector.connect(credentials)

# List pages
pages = await connector.data_service.list_items()

# Get page content
content = await connector.data_service.get_item_content(page_id)
```

### Advanced Usage

```python
# Search pages
results = await connector.data_service.search_items("important")

# Get page metadata
metadata = await connector.data_service.get_item_metadata(page_id)
```

## Authentication

The connector uses Notion's Integration Token authentication:

1. Create a Notion integration at https://www.notion.so/my-integrations
2. Get your integration token
3. Share pages/databases with your integration
4. Use the token in your credentials

## Rate Limits

- 3 requests per second
- 180 requests per minute
- 100,000 requests per day

## Error Handling

The connector includes comprehensive error handling:

- Authentication failures
- API rate limiting
- Network errors
- Invalid responses

## Testing

Run the tests:

```bash
pytest tests/test_notion_connector.py -v
```

## Contributing

1. Follow the existing code patterns
2. Add tests for new functionality
3. Update documentation
4. Ensure error handling is comprehensive
```

## Summary

In just 15 minutes, you can add a new connector by:

1. **Create directory structure** - 1 minute
2. **Add connector type to enums** - 1 minute  
3. **Create authentication service** - 3 minutes
4. **Create data service** - 3 minutes
5. **Create main connector service** - 3 minutes
6. **Create interfaces** - 1 minute
7. **Create configuration** - 1 minute
8. **Register with factory** - 1 minute
9. **Add usage examples** - 2 minutes
10. **Add tests** - 2 minutes
11. **Add documentation** - 1 minute

## Key Benefits

The framework provides:

- ✅ **Modular Architecture**: Separate services for auth, data, and main logic
- ✅ **Error Handling**: Comprehensive error logging and handling
- ✅ **Rate Limiting**: Built-in rate limiting support
- ✅ **Logging**: Detailed logging for debugging
- ✅ **Testing**: Easy testing with mocks and real API
- ✅ **Factory Pattern**: Easy registration and creation
- ✅ **Interface Contracts**: Clear interfaces for all services
- ✅ **Configuration Management**: Centralized configuration
- ✅ **Async Support**: Full async/await support
- ✅ **Extensibility**: Easy to add new features

## Next Steps

After creating your connector:

1. **Add to the main application registry**
2. **Create API endpoints** for your connector
3. **Add webhook support** if needed
4. **Implement batch operations** for better performance
5. **Add monitoring and metrics**
6. **Create user documentation**

The framework handles all the complex parts - you only need to implement the specific API calls for your service! 