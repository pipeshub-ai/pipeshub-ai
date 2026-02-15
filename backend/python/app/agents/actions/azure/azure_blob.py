import asyncio
import json
import logging
import threading
from typing import Coroutine, List, Optional, Tuple

from app.agents.tools.decorator import tool
from app.agents.tools.enums import ParameterType
from app.agents.tools.models import ToolParameter
from app.connectors.core.registry.auth_builder import (
    AuthBuilder,
    AuthType,
)
from app.connectors.core.registry.connector_builder import CommonFields
from app.connectors.core.registry.tool_builder import (
    ToolCategory,
    ToolDefinition,
    ToolsetBuilder,
)
from app.sources.client.azure.azure_blob import AzureBlobClient
from app.sources.external.azure.azure_blob import AzureBlobDataSource

logger = logging.getLogger(__name__)

# Define tools
tools: List[ToolDefinition] = [
    ToolDefinition(
        name="create_container",
        description="Create a new container",
        parameters=[
            {"name": "container_name", "type": "string", "description": "Container name", "required": True}
        ],
        tags=["containers", "create"]
    ),
    ToolDefinition(
        name="get_container",
        description="Get container details",
        parameters=[
            {"name": "container_name", "type": "string", "description": "Container name", "required": True}
        ],
        tags=["containers", "read"]
    ),
    ToolDefinition(
        name="delete_container",
        description="Delete a container",
        parameters=[
            {"name": "container_name", "type": "string", "description": "Container name", "required": True}
        ],
        tags=["containers", "delete"]
    ),
    ToolDefinition(
        name="upload_blob",
        description="Upload a blob",
        parameters=[
            {"name": "container_name", "type": "string", "description": "Container name", "required": True},
            {"name": "blob_name", "type": "string", "description": "Blob name", "required": True},
            {"name": "content", "type": "string", "description": "Blob content", "required": True}
        ],
        tags=["blobs", "upload"]
    ),
    ToolDefinition(
        name="get_blob",
        description="Get a blob",
        parameters=[
            {"name": "container_name", "type": "string", "description": "Container name", "required": True},
            {"name": "blob_name", "type": "string", "description": "Blob name", "required": True}
        ],
        tags=["blobs", "read"]
    ),
    ToolDefinition(
        name="delete_blob",
        description="Delete a blob",
        parameters=[
            {"name": "container_name", "type": "string", "description": "Container name", "required": True},
            {"name": "blob_name", "type": "string", "description": "Blob name", "required": True}
        ],
        tags=["blobs", "delete"]
    ),
    ToolDefinition(
        name="search_blobs_by_tags",
        description="Search blobs by tags",
        parameters=[
            {"name": "container_name", "type": "string", "description": "Container name", "required": True},
            {"name": "tag_filter", "type": "string", "description": "Tag filter", "required": True}
        ],
        tags=["blobs", "search"]
    ),
]


# Register Azure Blob Storage toolset
@ToolsetBuilder("Azure Blob Storage")\
    .in_group("Storage")\
    .with_description("Azure Blob Storage integration for object storage and container management")\
    .with_category(ToolCategory.APP)\
    .with_auth([
        AuthBuilder.type(AuthType.API_TOKEN).fields([
            CommonFields.api_token("Azure Storage Account Name", "your-account-name", field_name="accountName"),
            CommonFields.api_token("Azure Storage Account Key", "your-account-key", field_name="accountKey")
        ])
    ])\
    .with_tools(tools)\
    .configure(lambda builder: builder.with_icon("/assets/icons/connectors/azure.svg"))\
    .build_decorator()
class AzureBlob:
    """Azure Blob Storage tools using AzureBlobDataSource (CRUD + search)."""

    def __init__(self, client: AzureBlobClient) -> None:
        self.client = AzureBlobDataSource(client)
        self._bg_loop = asyncio.new_event_loop()
        self._bg_loop_thread = threading.Thread(target=self._start_background_loop, daemon=True)
        self._bg_loop_thread.start()

    def _start_background_loop(self) -> None:
        asyncio.set_event_loop(self._bg_loop)
        self._bg_loop.run_forever()

    def _run_async(self, coro: Coroutine[None, None, object]) -> object:
        future = asyncio.run_coroutine_threadsafe(coro, self._bg_loop)
        return future.result()

    def shutdown(self) -> None:
        """Gracefully stop the background event loop and thread."""
        try:
            if getattr(self, "_bg_loop", None) is not None and self._bg_loop.is_running():
                self._bg_loop.call_soon_threadsafe(self._bg_loop.stop)
            if getattr(self, "_bg_loop_thread", None) is not None:
                self._bg_loop_thread.join()
            if getattr(self, "_bg_loop", None) is not None:
                self._bg_loop.close()
        except Exception as exc:
            logger.warning(f"AzureBlob shutdown encountered an issue: {exc}")

    def _wrap(self, success: bool, data: object | None, error: Optional[str], message: str) -> Tuple[bool, str]:
        if success:
            return True, json.dumps({"message": message, "data": data}, default=str)
        return False, json.dumps({"error": error or "Unknown error"})

    @tool(
        app_name="azure_blob",
        tool_name="create_container",
        description="Create a new container",
        parameters=[
            ToolParameter(name="container_name", type=ParameterType.STRING, description="Container name"),
        ],
        returns="JSON with operation result"
    )
    def create_container(self, container_name: str) -> Tuple[bool, str]:
        try:
            resp = self._run_async(self.client.create_container(container_name=container_name))
            return self._wrap(getattr(resp, "success", False), getattr(resp, "data", None), getattr(resp, "error", None), "Container created successfully")
        except Exception as e:
            logger.error(f"create_container error: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="azure_blob",
        tool_name="get_container",
        description="Get container properties",
        parameters=[
            ToolParameter(name="container_name", type=ParameterType.STRING, description="Container name"),
        ],
        returns="JSON with container properties"
    )
    def get_container(self, container_name: str) -> Tuple[bool, str]:
        try:
            resp = self._run_async(self.client.get_container_properties(container_name=container_name))
            return self._wrap(getattr(resp, "success", False), getattr(resp, "data", None), getattr(resp, "error", None), "Container fetched successfully")
        except Exception as e:
            logger.error(f"get_container error: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="azure_blob",
        tool_name="delete_container",
        description="Delete a container",
        parameters=[
            ToolParameter(name="container_name", type=ParameterType.STRING, description="Container name"),
        ],
        returns="JSON confirming deletion"
    )
    def delete_container(self, container_name: str) -> Tuple[bool, str]:
        try:
            resp = self._run_async(self.client.delete_container(container_name=container_name))
            return self._wrap(getattr(resp, "success", False), getattr(resp, "data", None), getattr(resp, "error", None), "Container deleted successfully")
        except Exception as e:
            logger.error(f"delete_container error: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="azure_blob",
        tool_name="upload_blob",
        description="Create or overwrite a block blob with text content",
        parameters=[
            ToolParameter(name="container_name", type=ParameterType.STRING, description="Container name"),
            ToolParameter(name="blob_name", type=ParameterType.STRING, description="Blob name"),
            ToolParameter(name="content", type=ParameterType.STRING, description="Blob text content"),
        ],
        returns="JSON with upload result"
    )
    def upload_blob(self, container_name: str, blob_name: str, content: str) -> Tuple[bool, str]:
        try:
            body_bytes = content.encode('utf-8')
            resp = self._run_async(
                self.client.upload_blob(
                    container_name=container_name,
                    blob_name=blob_name,
                    body=body_bytes,
                    Content_Length=len(body_bytes)
                )
            )
            return self._wrap(getattr(resp, "success", False), getattr(resp, "data", None), getattr(resp, "error", None), "Blob uploaded successfully")
        except Exception as e:
            logger.error(f"upload_blob error: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="azure_blob",
        tool_name="get_blob",
        description="Get blob properties",
        parameters=[
            ToolParameter(name="container_name", type=ParameterType.STRING, description="Container name"),
            ToolParameter(name="blob_name", type=ParameterType.STRING, description="Blob name"),
        ],
        returns="JSON with blob properties"
    )
    def get_blob(self, container_name: str, blob_name: str) -> Tuple[bool, str]:
        try:
            resp = self._run_async(self.client.get_blob_properties(container_name=container_name, blob_name=blob_name))
            return self._wrap(getattr(resp, "success", False), getattr(resp, "data", None), getattr(resp, "error", None), "Blob fetched successfully")
        except Exception as e:
            logger.error(f"get_blob error: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="azure_blob",
        tool_name="delete_blob",
        description="Delete a blob",
        parameters=[
            ToolParameter(name="container_name", type=ParameterType.STRING, description="Container name"),
            ToolParameter(name="blob_name", type=ParameterType.STRING, description="Blob name"),
        ],
        returns="JSON confirming deletion"
    )
    def delete_blob(self, container_name: str, blob_name: str) -> Tuple[bool, str]:
        try:
            resp = self._run_async(self.client.delete_blob(container_name=container_name, blob_name=blob_name))
            return self._wrap(getattr(resp, "success", False), getattr(resp, "data", None), getattr(resp, "error", None), "Blob deleted successfully")
        except Exception as e:
            logger.error(f"delete_blob error: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="azure_blob",
        tool_name="search_blobs_by_tags",
        description="Search blobs across account by tags WHERE clause",
        parameters=[
            ToolParameter(name="where", type=ParameterType.STRING, description="Tag query, e.g. '@tag = \"value\"'"),
            ToolParameter(name="maxresults", type=ParameterType.NUMBER, description="Max results", required=False),
        ],
        returns="JSON with search results"
    )
    def search_blobs_by_tags(self, where: str, maxresults: Optional[int] = None) -> Tuple[bool, str]:
        try:
            resp = self._run_async(self.client.find_blobs_by_tags(where=where, maxresults=maxresults))
            return self._wrap(getattr(resp, "success", False), getattr(resp, "data", None), getattr(resp, "error", None), "Search completed successfully")
        except Exception as e:
            logger.error(f"search_blobs_by_tags error: {e}")
            return False, json.dumps({"error": str(e)})



