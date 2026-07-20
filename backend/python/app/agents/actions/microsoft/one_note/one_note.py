
import json
import logging
from typing import Optional, Tuple

from app.agents.actions.utils import run_async
from app.agent_loop_lib.tools.base import ParameterType, Tag, ToolParameter
from app.agent_loop_lib.tools.decorators import tool
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

    @tool(
        path="/tools/onenote/get_notebooks",
        short_description="Get OneNote notebooks",
        description="Retrieve a list of OneNote notebooks accessible by the current user, optionally limiting the number of results returned.",
        parameters=[
            ToolParameter(
                name="top",
                type=ParameterType.INTEGER,
                description="Number of notebooks to retrieve",
                required=False
            )
        ],
        tags=[Tag(key="category", value="documentation"), Tag(key="type", value="read")],
    )
    async def get_notebooks(self, top: Optional[int] = None) -> Tuple[bool, str]:
        """Get OneNote notebooks"""
        """
        Args:
            top: Number of notebooks to retrieve
        Returns:
            Tuple[bool, str]: True if successful, False otherwise
        """
        try:
            # Use the existing me_onenote_get_notebooks method with proper parameters
            response = run_async(self.client.me_onenote_get_notebooks(
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
        path="/tools/onenote/get_notebook",
        short_description="Get a specific OneNote notebook",
        description="Retrieve details of a specific OneNote notebook by its ID, including metadata such as display name and timestamps.",
        parameters=[
            ToolParameter(
                name="notebook_id",
                type=ParameterType.STRING,
                description="ID of the notebook",
                required=True
            )
        ],
        tags=[Tag(key="category", value="documentation"), Tag(key="type", value="read")],
    )
    async def get_notebook(self, notebook_id: str) -> Tuple[bool, str]:
        """Get a specific OneNote notebook"""
        """
        Args:
            notebook_id: ID of the notebook
        Returns:
            Tuple[bool, str]: True if successful, False otherwise
        """
        try:
            # Map to data source method: me_onenote_get_notebooks requires notebook_id
            response = run_async(self.client.me_onenote_get_notebooks(
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
        path="/tools/onenote/get_sections",
        short_description="Get sections from a OneNote notebook",
        description="Retrieve all sections within a specific OneNote notebook, including section metadata and contained pages.",
        parameters=[
            ToolParameter(
                name="notebook_id",
                type=ParameterType.STRING,
                description="ID of the notebook",
                required=True
            )
        ],
        tags=[Tag(key="category", value="documentation"), Tag(key="type", value="read")],
    )
    async def get_sections(self, notebook_id: str) -> Tuple[bool, str]:
        """Get sections from a OneNote notebook"""
        """
        Args:
            notebook_id: ID of the notebook
        Returns:
            Tuple[bool, str]: True if successful, False otherwise
        """
        try:
            # Use the existing me_onenote_notebooks_get_sections method
            response = run_async(self.client.me_onenote_notebooks_get_sections(
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
        path="/tools/onenote/get_pages",
        short_description="Get pages from a OneNote section",
        description="Retrieve all pages within a specific section of a OneNote notebook, including page titles and timestamps.",
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
        ],
        tags=[Tag(key="category", value="documentation"), Tag(key="type", value="read")],
    )
    async def get_pages(self, notebook_id: str, section_id: str) -> Tuple[bool, str]:
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
            response = run_async(self.client.me_onenote_notebooks_sections_get_pages(
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
        path="/tools/onenote/get_page",
        short_description="Get a specific OneNote page",
        description="Retrieve a specific OneNote page by its ID, including its content and metadata.",
        parameters=[
            ToolParameter(
                name="page_id",
                type=ParameterType.STRING,
                description="ID of the page",
                required=True
            )
        ],
        tags=[Tag(key="category", value="documentation"), Tag(key="type", value="read")],
    )
    async def get_page(self, page_id: str) -> Tuple[bool, str]:
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
        path="/tools/onenote/create_page",
        short_description="Create a new OneNote page",
        description="Create a new page in a specific section of a OneNote notebook with a title and HTML content.",
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
        ],
        tags=[Tag(key="category", value="documentation"), Tag(key="type", value="write")],
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

            response = run_async(self.client.me_onenote_notebooks_sections_create_pages(
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
        path="/tools/onenote/update_page",
        short_description="Update a OneNote page",
        description="Update the content of an existing OneNote page by its ID with new HTML content.",
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
        ],
        tags=[Tag(key="category", value="documentation"), Tag(key="type", value="write")],
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
        path="/tools/onenote/delete_page",
        short_description="Delete a OneNote page",
        description="Delete a specific page from a OneNote notebook section using notebook, section, and page IDs.",
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
        ],
        tags=[Tag(key="category", value="documentation"), Tag(key="type", value="write")],
    )
    async def delete_page(self, notebook_id: str, section_id: str, page_id: str) -> Tuple[bool, str]:
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
            response = run_async(self.client.me_onenote_notebooks_sections_delete_pages(
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
