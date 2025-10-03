import asyncio
import json
import logging
from typing import Optional, Tuple

from app.agents.tools.decorator import tool
from app.agents.tools.enums import ParameterType
from app.agents.tools.models import ToolParameter
from app.sources.external.azure.azure_blob import AzureBlobDataSource

logger = logging.getLogger(__name__)


class Azure:
    """Azure tool exposed to the agents"""
    def __init__(self, client: object) -> None:
        """Initialize the Azure tool"""
        """
        Args:
            client: Azure client object
        Returns:
            None
        """
        self.client = AzureBlobDataSource(client)

    def _run_async(self, coro):
        """Helper method to run async operations in sync context"""
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # If we're already in an async context, we need to use a thread pool
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor() as executor:
                    future = executor.submit(asyncio.run, coro)
                    return future.result()
            else:
                return loop.run_until_complete(coro)
        except Exception as e:
            logger.error(f"Error running async operation: {e}")
            raise

    @tool(
        app_name="azure",
        tool_name="list_containers",
        description="List Azure Blob Storage containers",
        parameters=[
            ToolParameter(
                name="prefix",
                type=ParameterType.STRING,
                description="Prefix to filter containers",
                required=False
            ),
            ToolParameter(
                name="max_results",
                type=ParameterType.INTEGER,
                description="Maximum number of results to return",
                required=False
            )
        ]
    )
    def list_containers(
        self,
        prefix: Optional[str] = None,
        max_results: Optional[int] = None
    ) -> Tuple[bool, str]:
        """List Azure Blob Storage containers"""
        """
        Args:
            prefix: Prefix to filter containers
            max_results: Maximum number of results to return
        Returns:
            Tuple[bool, str]: True if successful, False otherwise
        """
        try:
            # Use AzureBlobDataSource method
            response = self._run_async(self.client.list_containers(
                prefix=prefix,
                max_results=max_results
            ))

            if response.success:
                return True, response.to_json()
            else:
                return False, response.to_json()
        except Exception as e:
            logger.error(f"Error listing containers: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="azure",
        tool_name="create_container",
        description="Create an Azure Blob Storage container",
        parameters=[
            ToolParameter(
                name="container_name",
                type=ParameterType.STRING,
                description="Name of the container to create",
                required=True
            ),
            ToolParameter(
                name="public_access",
                type=ParameterType.STRING,
                description="Public access level for the container",
                required=False
            )
        ]
    )
    def create_container(
        self,
        container_name: str,
        public_access: Optional[str] = None
    ) -> Tuple[bool, str]:
        """Create an Azure Blob Storage container"""
        """
        Args:
            container_name: Name of the container to create
            public_access: Public access level for the container
        Returns:
            Tuple[bool, str]: True if successful, False otherwise
        """
        try:
            # Use AzureBlobDataSource method
            response = self._run_async(self.client.create_container(
                container_name=container_name,
                public_access=public_access
            ))

            if response.success:
                return True, response.to_json()
            else:
                return False, response.to_json()
        except Exception as e:
            logger.error(f"Error creating container: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="azure",
        tool_name="delete_container",
        description="Delete an Azure Blob Storage container",
        parameters=[
            ToolParameter(
                name="container_name",
                type=ParameterType.STRING,
                description="Name of the container to delete",
                required=True
            )
        ]
    )
    def delete_container(self, container_name: str) -> Tuple[bool, str]:
        """Delete an Azure Blob Storage container"""
        """
        Args:
            container_name: Name of the container to delete
        Returns:
            Tuple[bool, str]: True if successful, False otherwise
        """
        try:
            # Use AzureBlobDataSource method
            response = self._run_async(self.client.delete_container(
                container_name=container_name
            ))

            if response.success:
                return True, response.to_json()
            else:
                return False, response.to_json()
        except Exception as e:
            logger.error(f"Error deleting container: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="azure",
        tool_name="list_blobs",
        description="List blobs in a container",
        parameters=[
            ToolParameter(
                name="container_name",
                type=ParameterType.STRING,
                description="Name of the container",
                required=True
            ),
            ToolParameter(
                name="prefix",
                type=ParameterType.STRING,
                description="Prefix to filter blobs",
                required=False
            ),
            ToolParameter(
                name="max_results",
                type=ParameterType.INTEGER,
                description="Maximum number of results to return",
                required=False
            )
        ]
    )
    def list_blobs(
        self,
        container_name: str,
        prefix: Optional[str] = None,
        max_results: Optional[int] = None
    ) -> Tuple[bool, str]:
        """List blobs in a container"""
        """
        Args:
            container_name: Name of the container
            prefix: Prefix to filter blobs
            max_results: Maximum number of results to return
        Returns:
            Tuple[bool, str]: True if successful, False otherwise
        """
        try:
            # Use AzureBlobDataSource method
            response = self._run_async(self.client.list_blobs(
                container_name=container_name,
                prefix=prefix,
                max_results=max_results
            ))

            if response.success:
                return True, response.to_json()
            else:
                return False, response.to_json()
        except Exception as e:
            logger.error(f"Error listing blobs: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="azure",
        tool_name="get_blob",
        description="Get a blob from Azure Blob Storage",
        parameters=[
            ToolParameter(
                name="container_name",
                type=ParameterType.STRING,
                description="Name of the container",
                required=True
            ),
            ToolParameter(
                name="blob_name",
                type=ParameterType.STRING,
                description="Name of the blob",
                required=True
            )
        ]
    )
    def get_blob(
        self,
        container_name: str,
        blob_name: str
    ) -> Tuple[bool, str]:
        """Get a blob from Azure Blob Storage"""
        """
        Args:
            container_name: Name of the container
            blob_name: Name of the blob
        Returns:
            Tuple[bool, str]: True if successful, False otherwise
        """
        try:
            # Use AzureBlobDataSource method
            response = self._run_async(self.client.get_blob(
                container_name=container_name,
                blob_name=blob_name
            ))

            if response.success:
                return True, response.to_json()
            else:
                return False, response.to_json()
        except Exception as e:
            logger.error(f"Error getting blob: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="azure",
        tool_name="upload_blob",
        description="Upload a blob to Azure Blob Storage",
        parameters=[
            ToolParameter(
                name="container_name",
                type=ParameterType.STRING,
                description="Name of the container",
                required=True
            ),
            ToolParameter(
                name="blob_name",
                type=ParameterType.STRING,
                description="Name of the blob",
                required=True
            ),
            ToolParameter(
                name="data",
                type=ParameterType.STRING,
                description="Data to upload",
                required=True
            ),
            ToolParameter(
                name="content_type",
                type=ParameterType.STRING,
                description="Content type of the blob",
                required=False
            )
        ]
    )
    def upload_blob(
        self,
        container_name: str,
        blob_name: str,
        data: str,
        content_type: Optional[str] = None
    ) -> Tuple[bool, str]:
        """Upload a blob to Azure Blob Storage"""
        """
        Args:
            container_name: Name of the container
            blob_name: Name of the blob
            data: Data to upload
            content_type: Content type of the blob
        Returns:
            Tuple[bool, str]: True if successful, False otherwise
        """
        try:
            # Use AzureBlobDataSource method
            response = self._run_async(self.client.upload_blob(
                container_name=container_name,
                blob_name=blob_name,
                data=data,
                content_type=content_type
            ))

            if response.success:
                return True, response.to_json()
            else:
                return False, response.to_json()
        except Exception as e:
            logger.error(f"Error uploading blob: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="azure",
        tool_name="delete_blob",
        description="Delete a blob from Azure Blob Storage",
        parameters=[
            ToolParameter(
                name="container_name",
                type=ParameterType.STRING,
                description="Name of the container",
                required=True
            ),
            ToolParameter(
                name="blob_name",
                type=ParameterType.STRING,
                description="Name of the blob",
                required=True
            )
        ]
    )
    def delete_blob(
        self,
        container_name: str,
        blob_name: str
    ) -> Tuple[bool, str]:
        """Delete a blob from Azure Blob Storage"""
        """
        Args:
            container_name: Name of the container
            blob_name: Name of the blob
        Returns:
            Tuple[bool, str]: True if successful, False otherwise
        """
        try:
            # Use AzureBlobDataSource method
            response = self._run_async(self.client.delete_blob(
                container_name=container_name,
                blob_name=blob_name
            ))

            if response.success:
                return True, response.to_json()
            else:
                return False, response.to_json()
        except Exception as e:
            logger.error(f"Error deleting blob: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="azure",
        tool_name="copy_blob",
        description="Copy a blob in Azure Blob Storage",
        parameters=[
            ToolParameter(
                name="container_name",
                type=ParameterType.STRING,
                description="Name of the container",
                required=True
            ),
            ToolParameter(
                name="blob_name",
                type=ParameterType.STRING,
                description="Name of the blob",
                required=True
            ),
            ToolParameter(
                name="source_url",
                type=ParameterType.STRING,
                description="URL of the source blob",
                required=True
            )
        ]
    )
    def copy_blob(
        self,
        container_name: str,
        blob_name: str,
        source_url: str
    ) -> Tuple[bool, str]:
        """Copy a blob in Azure Blob Storage"""
        """
        Args:
            container_name: Name of the container
            blob_name: Name of the blob
            source_url: URL of the source blob
        Returns:
            Tuple[bool, str]: True if successful, False otherwise
        """
        try:
            # Use AzureBlobDataSource method
            response = self._run_async(self.client.copy_blob(
                container_name=container_name,
                blob_name=blob_name,
                source_url=source_url
            ))

            if response.success:
                return True, response.to_json()
            else:
                return False, response.to_json()
        except Exception as e:
            logger.error(f"Error copying blob: {e}")
            return False, json.dumps({"error": str(e)})
