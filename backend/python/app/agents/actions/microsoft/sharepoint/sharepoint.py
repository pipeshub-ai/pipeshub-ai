import asyncio
import json
import logging
from typing import Optional, Tuple

from app.agents.tools.decorator import tool
from app.agents.tools.enums import ParameterType
from app.agents.tools.models import ToolParameter
from app.sources.client.http.http_response import HTTPResponse
from app.sources.client.microsoft.microsoft import MSGraphClient
from app.sources.external.microsoft.sharepoint.sharepoint import SharePointDataSource

logger = logging.getLogger(__name__)


class SharePoint:
    """SharePoint tool exposed to the agents"""
    def __init__(self, client: MSGraphClient) -> None:
        """Initialize the SharePoint tool"""
        """
        Args:
            client: Microsoft Graph client object
        Returns:
            None
        """
        self.client = SharePointDataSource(client)

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
        app_name="sharepointonline",
        tool_name="get_sites",
        description="Get SharePoint sites",
        parameters=[
            ToolParameter(
                name="search",
                type=ParameterType.STRING,
                description="Search query for sites",
                required=False
            ),
            ToolParameter(
                name="filter",
                type=ParameterType.STRING,
                description="Filter query for sites",
                required=False
            ),
            ToolParameter(
                name="orderby",
                type=ParameterType.STRING,
                description="Order by field",
                required=False
            ),
            ToolParameter(
                name="select",
                type=ParameterType.STRING,
                description="Select specific fields",
                required=False
            ),
            ToolParameter(
                name="expand",
                type=ParameterType.STRING,
                description="Expand related entities",
                required=False
            ),
            ToolParameter(
                name="top",
                type=ParameterType.INTEGER,
                description="Number of results to return",
                required=False
            ),
            ToolParameter(
                name="skip",
                type=ParameterType.INTEGER,
                description="Number of results to skip",
                required=False
            )
        ]
    )
    def get_sites(
        self,
        search: Optional[str] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        select: Optional[str] = None,
        expand: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None
    ) -> Tuple[bool, str]:
        """Get SharePoint sites"""
        """
        Args:
            search: Search query for sites
            filter: Filter query for sites
            orderby: Order by field
            select: Select specific fields
            expand: Expand related entities
            top: Number of results to return
            skip: Number of results to skip
        Returns:
            Tuple[bool, str]: True if successful, False otherwise
        """
        try:
            # Use SharePointDataSource method
            response = self._run_async(self.client.sites_get_all_sites(
                search=search,
                filter=filter,
                orderby=orderby,
                select=select,
                expand=expand,
                top=top,
                skip=skip
            ))

            if response.success:
                return True, response.to_json()
            else:
                return False, response.to_json()
        except Exception as e:
            logger.error(f"Error in get_sites: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="sharepointonline",
        tool_name="get_site",
        description="Get a specific SharePoint site",
        parameters=[
            ToolParameter(
                name="site_id",
                type=ParameterType.STRING,
                description="The ID of the site to get",
                required=True
            ),
            ToolParameter(
                name="select",
                type=ParameterType.STRING,
                description="Select specific fields",
                required=False
            ),
            ToolParameter(
                name="expand",
                type=ParameterType.STRING,
                description="Expand related entities",
                required=False
            )
        ]
    )
    def get_site(
        self,
        site_id: str,
        select: Optional[str] = None,
        expand: Optional[str] = None
    ) -> Tuple[bool, str]:
        """Get a specific SharePoint site"""
        """
        Args:
            site_id: The ID of the site to get
            select: Select specific fields
            expand: Expand related entities
        Returns:
            Tuple[bool, str]: True if successful, False otherwise
        """
        try:
            # Use SharePointDataSource method
            response = self._run_async(self.client.sites_site_get_by_path(
                site_id=site_id,
                select=select,
                expand=expand
            ))

            if response.success:
                return True, response.to_json()
            else:
                return False, response.to_json()
        except Exception as e:
            logger.error(f"Error in get_site: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="sharepointonline",
        tool_name="get_lists",
        description="Get lists from a SharePoint site",
        parameters=[
            ToolParameter(
                name="site_id",
                type=ParameterType.STRING,
                description="The ID of the site",
                required=True
            ),
            ToolParameter(
                name="search",
                type=ParameterType.STRING,
                description="Search query for lists",
                required=False
            ),
            ToolParameter(
                name="filter",
                type=ParameterType.STRING,
                description="Filter query for lists",
                required=False
            ),
            ToolParameter(
                name="orderby",
                type=ParameterType.STRING,
                description="Order by field",
                required=False
            ),
            ToolParameter(
                name="select",
                type=ParameterType.STRING,
                description="Select specific fields",
                required=False
            ),
            ToolParameter(
                name="expand",
                type=ParameterType.STRING,
                description="Expand related entities",
                required=False
            ),
            ToolParameter(
                name="top",
                type=ParameterType.INTEGER,
                description="Number of results to return",
                required=False
            ),
            ToolParameter(
                name="skip",
                type=ParameterType.INTEGER,
                description="Number of results to skip",
                required=False
            )
        ]
    )
    def get_lists(
        self,
        site_id: str,
        search: Optional[str] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        select: Optional[str] = None,
        expand: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None
    ) -> Tuple[bool, str]:
        """Get lists from a SharePoint site"""
        """
        Args:
            site_id: The ID of the site
            search: Search query for lists
            filter: Filter query for lists
            orderby: Order by field
            select: Select specific fields
            expand: Expand related entities
            top: Number of results to return
            skip: Number of results to skip
        Returns:
            Tuple[bool, str]: True if successful, False otherwise
        """
        try:
            # Use SharePointDataSource method
            response = self._run_async(self.client.sites_list_lists(
                site_id=site_id,
                search=search,
                filter=filter,
                orderby=orderby,
                select=select,
                expand=expand,
                top=top,
                skip=skip
            ))

            if response.success:
                return True, response.to_json()
            else:
                return False, response.to_json()
        except Exception as e:
            logger.error(f"Error in get_lists: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="sharepointonline",
        tool_name="get_drives",
        description="Get drives from a SharePoint site",
        parameters=[
            ToolParameter(
                name="site_id",
                type=ParameterType.STRING,
                description="The ID of the site",
                required=True
            ),
            ToolParameter(
                name="search",
                type=ParameterType.STRING,
                description="Search query for drives",
                required=False
            ),
            ToolParameter(
                name="filter",
                type=ParameterType.STRING,
                description="Filter query for drives",
                required=False
            ),
            ToolParameter(
                name="orderby",
                type=ParameterType.STRING,
                description="Order by field",
                required=False
            ),
            ToolParameter(
                name="select",
                type=ParameterType.STRING,
                description="Select specific fields",
                required=False
            ),
            ToolParameter(
                name="expand",
                type=ParameterType.STRING,
                description="Expand related entities",
                required=False
            ),
            ToolParameter(
                name="top",
                type=ParameterType.INTEGER,
                description="Number of results to return",
                required=False
            ),
            ToolParameter(
                name="skip",
                type=ParameterType.INTEGER,
                description="Number of results to skip",
                required=False
            )
        ]
    )
    def get_drives(
        self,
        site_id: str,
        search: Optional[str] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        select: Optional[str] = None,
        expand: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None
    ) -> Tuple[bool, str]:
        """Get drives from a SharePoint site"""
        """
        Args:
            site_id: The ID of the site
            search: Search query for drives
            filter: Filter query for drives
            orderby: Order by field
            select: Select specific fields
            expand: Expand related entities
            top: Number of results to return
            skip: Number of results to skip
        Returns:
            Tuple[bool, str]: True if successful, False otherwise
        """
        try:
            # Use SharePointDataSource method
            response = self._run_async(self.client.sites_list_drives(
                site_id=site_id,
                search=search,
                filter=filter,
                orderby=orderby,
                select=select,
                expand=expand,
                top=top,
                skip=skip
            ))

            if response.success:
                return True, response.to_json()
            else:
                return False, response.to_json()
        except Exception as e:
            logger.error(f"Error in get_drives: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="sharepointonline",
        tool_name="get_pages",
        description="Get pages from a SharePoint site",
        parameters=[
            ToolParameter(
                name="site_id",
                type=ParameterType.STRING,
                description="The ID of the site",
                required=True
            ),
            ToolParameter(
                name="search",
                type=ParameterType.STRING,
                description="Search query for pages",
                required=False
            ),
            ToolParameter(
                name="filter",
                type=ParameterType.STRING,
                description="Filter query for pages",
                required=False
            ),
            ToolParameter(
                name="orderby",
                type=ParameterType.STRING,
                description="Order by field",
                required=False
            ),
            ToolParameter(
                name="select",
                type=ParameterType.STRING,
                description="Select specific fields",
                required=False
            ),
            ToolParameter(
                name="expand",
                type=ParameterType.STRING,
                description="Expand related entities",
                required=False
            ),
            ToolParameter(
                name="top",
                type=ParameterType.INTEGER,
                description="Number of results to return",
                required=False
            ),
            ToolParameter(
                name="skip",
                type=ParameterType.INTEGER,
                description="Number of results to skip",
                required=False
            )
        ]
    )
    def get_pages(
        self,
        site_id: str,
        search: Optional[str] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        select: Optional[str] = None,
        expand: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None
    ) -> Tuple[bool, str]:
        """Get pages from a SharePoint site"""
        """
        Args:
            site_id: The ID of the site
            search: Search query for pages
            filter: Filter query for pages
            orderby: Order by field
            select: Select specific fields
            expand: Expand related entities
            top: Number of results to return
            skip: Number of results to skip
        Returns:
            Tuple[bool, str]: True if successful, False otherwise
        """
        try:
            # Use SharePointDataSource method
            response = self._run_async(self.client.sites_list_pages(
                site_id=site_id,
                search=search,
                filter=filter,
                orderby=orderby,
                select=select,
                expand=expand,
                top=top,
                skip=skip
            ))

            if response.success:
                return True, response.to_json()
            else:
                return False, response.to_json()
        except Exception as e:
            logger.error(f"Error in get_pages: {e}")
            return False, json.dumps({"error": str(e)})
