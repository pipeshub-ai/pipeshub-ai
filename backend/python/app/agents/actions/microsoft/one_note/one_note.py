import asyncio
import json
import logging
from typing import Optional, Tuple

from app.agents.tools.decorator import tool
from app.agents.tools.enums import ParameterType
from app.agents.tools.models import ToolParameter
from app.sources.client.http.http_response import HTTPResponse
from app.sources.client.microsoft.microsoft import MSGraphClient
from app.sources.external.microsoft.one_note.one_note import OneNoteDataSource

logger = logging.getLogger(__name__)


class OneNote:
    """Microsoft OneNote tool exposed to the agents"""
    def __init__(self, client: MSGraphClient) -> None:
        """Initialize the OneNote tool"""
        """
        Args:
            client: Microsoft Graph client object
        Returns:
            None
        """
        self.client = OneNoteDataSource(client)

    def _run_async(self, coro) -> HTTPResponse: # type: ignore [valid method]
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
        app_name="one_note",
        tool_name="get_notebooks",
        description="Get OneNote notebooks",
        parameters=[
            ToolParameter(
                name="top",
                type=ParameterType.INTEGER,
                description="Number of notebooks to retrieve",
                required=False
            )
        ]
    )
    def get_notebooks(self, top: Optional[int] = None) -> Tuple[bool, str]:
        """Get OneNote notebooks"""
        """
        Args:
            top: Number of notebooks to retrieve
        Returns:
            Tuple[bool, str]: True if successful, False otherwise
        """
        try:
            # Use the existing me_onenote_get_notebooks method with proper parameters
            response = self._run_async(self.client.me_onenote_get_notebooks(
                notebook_id="",  # Empty string for listing all notebooks
                top=top,
                select=["id", "displayName", "createdDateTime", "lastModifiedDateTime"],
                expand=["sections"]
            ))

            if response.success:
                return True, response.to_json()
            else:
                return False, response.to_json()
        except Exception as e:
            logger.error(f"Error in get_notebooks: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="one_note",
        tool_name="get_notebook",
        description="Get a specific OneNote notebook",
        parameters=[
            ToolParameter(
                name="notebook_id",
                type=ParameterType.STRING,
                description="ID of the notebook",
                required=True
            )
        ]
    )
    def get_notebook(self, notebook_id: str) -> Tuple[bool, str]:
        """Get a specific OneNote notebook"""
        """
        Args:
            notebook_id: ID of the notebook
        Returns:
            Tuple[bool, str]: True if successful, False otherwise
        """
        try:
            # Map to data source method: me_onenote_get_notebooks requires notebook_id
            response = self._run_async(self.client.me_onenote_get_notebooks(
                notebook_id=notebook_id
            ))
            if response.success:
                return True, response.to_json()
            else:
                return False, response.to_json()
        except Exception as e:
            logger.error(f"Error in get_notebook: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="one_note",
        tool_name="get_sections",
        description="Get sections from a OneNote notebook",
        parameters=[
            ToolParameter(
                name="notebook_id",
                type=ParameterType.STRING,
                description="ID of the notebook",
                required=True
            )
        ]
    )
    def get_sections(self, notebook_id: str) -> Tuple[bool, str]:
        """Get sections from a OneNote notebook"""
        """
        Args:
            notebook_id: ID of the notebook
        Returns:
            Tuple[bool, str]: True if successful, False otherwise
        """
        try:
            # Use the existing me_onenote_notebooks_get_sections method
            response = self._run_async(self.client.me_onenote_notebooks_get_sections(
                notebook_id=notebook_id,
                onenoteSection_id="",  # Empty string for listing all sections
                top=100,
                select=["id", "displayName", "createdDateTime", "lastModifiedDateTime"],
                expand=["pages"]
            ))

            if response.success:
                return True, response.to_json()
            else:
                return False, response.to_json()
        except Exception as e:
            logger.error(f"Error in get_sections: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="one_note",
        tool_name="get_pages",
        description="Get pages from a OneNote section",
        parameters=[
            ToolParameter(
                name="notebook_id",
                type=ParameterType.STRING,
                description="ID of the notebook",
                required=True
            ),
            ToolParameter(
                name="section_id",
                type=ParameterType.STRING,
                description="ID of the section",
                required=True
            )
        ]
    )
    def get_pages(self, notebook_id: str, section_id: str) -> Tuple[bool, str]:
        """Get pages from a OneNote section"""
        """
        Args:
            notebook_id: ID of the notebook
            section_id: ID of the section
        Returns:
            Tuple[bool, str]: True if successful, False otherwise
        """
        try:
            # Use the existing me_onenote_notebooks_sections_get_pages method
            response = self._run_async(self.client.me_onenote_notebooks_sections_get_pages(
                notebook_id=notebook_id,
                onenoteSection_id=section_id,
                onenotePage_id="",  # Empty string for listing all pages
                top=100,
                select=["id", "title", "createdDateTime", "lastModifiedDateTime"],
                expand=["content"]
            ))

            if response.success:
                return True, response.to_json()
            else:
                return False, response.to_json()
        except Exception as e:
            logger.error(f"Error in get_pages: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="one_note",
        tool_name="get_page",
        description="Get a specific OneNote page",
        parameters=[
            ToolParameter(
                name="page_id",
                type=ParameterType.STRING,
                description="ID of the page",
                required=True
            )
        ]
    )
    def get_page(self, page_id: str) -> Tuple[bool, str]:
        """Get a specific OneNote page"""
        """
        Args:
            page_id: ID of the page
        Returns:
            Tuple[bool, str]: True if successful, False otherwise
        """
        try:
            # Direct page GET exists: me_onenote_pages_get_parent_notebook etc.,
            # but fetching page by ID content/metadata typically via me_onenote_pages_onenote_page_onenote_patch_content (for patch) or pages.by_onenote_page_id.get
            logger.error("Get page not implemented - add a pages.by_onenote_page_id GET wrapper in data source")
            return False, json.dumps({
                "error": "Get page not implemented",
                "details": "Add me_onenote_pages_get(page_id) to OneNoteDataSource and call it here",
            })
        except Exception as e:
            logger.error(f"Error in get_page: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="one_note",
        tool_name="create_page",
        description="Create a new OneNote page",
        parameters=[
            ToolParameter(
                name="notebook_id",
                type=ParameterType.STRING,
                description="ID of the notebook",
                required=True
            ),
            ToolParameter(
                name="section_id",
                type=ParameterType.STRING,
                description="ID of the section",
                required=True
            ),
            ToolParameter(
                name="title",
                type=ParameterType.STRING,
                description="Title of the page",
                required=True
            ),
            ToolParameter(
                name="content",
                type=ParameterType.STRING,
                description="Content of the page (HTML format)",
                required=True
            )
        ]
    )
    def create_page(
        self,
        notebook_id: str,
        section_id: str,
        title: str,
        content: str
    ) -> Tuple[bool, str]:
        """Create a new OneNote page"""
        """
        Args:
            notebook_id: ID of the notebook
            section_id: ID of the section
            title: Title of the page
            content: Content of the page (HTML format)
        Returns:
            Tuple[bool, str]: True if successful, False otherwise
        """
        try:
            # Use the existing me_onenote_notebooks_sections_create_pages method
            request_body = {
                "title": title,
                "content": content
            }

            response = self._run_async(self.client.me_onenote_notebooks_sections_create_pages(
                notebook_id=notebook_id,
                onenoteSection_id=section_id,
                request_body=request_body
            ))

            if response.success:
                return True, response.to_json()
            else:
                return False, response.to_json()
        except Exception as e:
            logger.error(f"Error in create_page: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="one_note",
        tool_name="update_page",
        description="Update a OneNote page",
        parameters=[
            ToolParameter(
                name="page_id",
                type=ParameterType.STRING,
                description="ID of the page",
                required=True
            ),
            ToolParameter(
                name="content",
                type=ParameterType.STRING,
                description="New content for the page (HTML format)",
                required=True
            )
        ]
    )
    def update_page(
        self,
        page_id: str,
        content: str
    ) -> Tuple[bool, str]:
        """Update a OneNote page"""
        """
        Args:
            page_id: ID of the page
            content: New content for the page (HTML format)
        Returns:
            Tuple[bool, str]: True if successful, False otherwise
        """
        try:
            # Update page content requires onenote_patch_content with section and notebook context.
            logger.error("Update page not implemented - requires onenote_patch_content path; add wrapper in data source")
            return False, json.dumps({
                "error": "Update page not implemented",
                "details": "Use me_onenote_pages_onenote_page_onenote_patch_content with proper IDs",
            })
        except Exception as e:
            logger.error(f"Error in update_page: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="one_note",
        tool_name="delete_page",
        description="Delete a OneNote page",
        parameters=[
            ToolParameter(
                name="notebook_id",
                type=ParameterType.STRING,
                description="ID of the notebook",
                required=True
            ),
            ToolParameter(
                name="section_id",
                type=ParameterType.STRING,
                description="ID of the section",
                required=True
            ),
            ToolParameter(
                name="page_id",
                type=ParameterType.STRING,
                description="ID of the page",
                required=True
            )
        ]
    )
    def delete_page(self, notebook_id: str, section_id: str, page_id: str) -> Tuple[bool, str]:
        """Delete a OneNote page"""
        """
        Args:
            notebook_id: ID of the notebook
            section_id: ID of the section
            page_id: ID of the page
        Returns:
            Tuple[bool, str]: True if successful, False otherwise
        """
        try:
            # Use the existing me_onenote_notebooks_sections_delete_pages method
            response = self._run_async(self.client.me_onenote_notebooks_sections_delete_pages(
                notebook_id=notebook_id,
                onenoteSection_id=section_id,
                onenotePage_id=page_id
            ))

            if response.success:
                return True, response.to_json()
            else:
                return False, response.to_json()
        except Exception as e:
            logger.error(f"Error in delete_page: {e}")
            return False, json.dumps({"error": str(e)})
