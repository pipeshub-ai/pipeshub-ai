import json
import logging
from typing import List, Optional, Tuple

from app.agents.actions.utils import run_async
from app.agents.tools.decorator import tool
from app.agents.tools.enums import ParameterType
from app.agents.tools.models import ToolParameter
from app.connectors.core.registry.auth_builder import (
    AuthBuilder,
    AuthType,
    OAuthScopeConfig,
)
from app.connectors.core.registry.connector_builder import CommonFields
from app.connectors.core.registry.tool_builder import (
    ToolCategory,
    ToolDefinition,
    ToolsetBuilder,
)
from app.sources.client.microsoft.microsoft import MSGraphClient
from app.sources.external.microsoft.sharepoint.sharepoint import SharePointDataSource

logger = logging.getLogger(__name__)

# Define tools
tools: List[ToolDefinition] = [
    ToolDefinition(
        name="get_sites",
        description="Get SharePoint sites",
        parameters=[
            {"name": "search", "type": "string", "description": "Search query", "required": False}
        ],
        tags=["sites", "list"]
    ),
    ToolDefinition(
        name="get_site",
        description="Get site details",
        parameters=[
            {"name": "site_id", "type": "string", "description": "Site ID", "required": True}
        ],
        tags=["sites", "read"]
    ),
    ToolDefinition(
        name="get_lists",
        description="Get lists in a site",
        parameters=[
            {"name": "site_id", "type": "string", "description": "Site ID", "required": True}
        ],
        tags=["lists", "list"]
    ),
    ToolDefinition(
        name="get_drives",
        description="Get drives in a site",
        parameters=[
            {"name": "site_id", "type": "string", "description": "Site ID", "required": True}
        ],
        tags=["drives", "list"]
    ),
    ToolDefinition(
        name="get_pages",
        description="Get pages in a site",
        parameters=[
            {"name": "site_id", "type": "string", "description": "Site ID", "required": True}
        ],
        tags=["pages", "list"]
    ),
]


# Register SharePoint toolset
@ToolsetBuilder("SharePoint")\
    .in_group("Microsoft 365")\
    .with_description("SharePoint integration for sites, lists, and document management")\
    .with_category(ToolCategory.APP)\
    .with_auth([
        AuthBuilder.type(AuthType.OAUTH).oauth(
            connector_name="SharePoint",
            authorize_url="https://login.microsoftonline.com/common/oauth2/v2.0/authorize",
            token_url="https://login.microsoftonline.com/common/oauth2/v2.0/token",
            redirect_uri="toolsets/oauth/callback/sharepoint",
            scopes=OAuthScopeConfig(
                personal_sync=[],
                team_sync=[],
                agent=[
                    "Sites.ReadWrite.All",
                    "Files.ReadWrite.All"
                ]
            ),
            fields=[
                CommonFields.client_id("Azure App Registration"),
                CommonFields.client_secret("Azure App Registration")
            ],
            icon_path="/assets/icons/connectors/sharepoint.svg",
            app_group="Microsoft 365",
            app_description="SharePoint OAuth application for agent integration"
        )
    ])\
    .with_tools(tools)\
    .configure(lambda builder: builder.with_icon("/assets/icons/connectors/sharepoint.svg"))\
    .build_decorator()
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
            response = run_async(self.client.sites_get_all_sites(
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
            response = run_async(self.client.sites_site_get_by_path(
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
            response = run_async(self.client.sites_list_lists(
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
            response = run_async(self.client.sites_list_drives(
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
            response = run_async(self.client.sites_list_pages(
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
