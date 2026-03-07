import json
import logging
import re
from typing import Any, Dict, List, Optional

from kiota_abstractions.method import Method  # type: ignore
from kiota_abstractions.request_information import RequestInformation  # type: ignore
from kiota_serialization_json.json_serialization_writer import JsonSerializationWriter  # type: ignore
from msgraph.generated.models.site_page import SitePage  # type: ignore
from msgraph.generated.sites.item.lists.item.items.items_request_builder import ItemsRequestBuilder  # type: ignore
from msgraph.generated.sites.item.pages.pages_request_builder import PagesRequestBuilder  # type: ignore
from pydantic import BaseModel, Field

from app.agents.tools.config import ToolCategory
from app.agents.tools.decorator import tool
from app.agents.tools.models import ToolIntent
from app.connectors.core.registry.auth_builder import (
    AuthBuilder,
    AuthType,
    OAuthScopeConfig,
)
from app.connectors.core.registry.connector_builder import CommonFields
from app.connectors.core.registry.tool_builder import (
    ToolsetBuilder,
    ToolsetCategory,
)
from app.connectors.core.registry.types import AuthField, DocumentationLink
from app.sources.client.microsoft.microsoft import MSGraphClient
from app.sources.external.microsoft.sharepoint.sharepoint import SharePointDataSource

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------

class GetSitesInput(BaseModel):
    """
    Schema for listing SharePoint sites accessible to the user.
    """
    search: Optional[str] = Field(
        default=None,
        description=(
            "KQL search query to filter sites. Examples: "
            "'marketing' to find marketing sites, "
            "'title:HR' to find HR sites, "
            "'createdDateTime>=2024-01-01' to find recently created sites. "
            "If not provided, returns all accessible sites."
        )
    )
    top: Optional[int] = Field(
        default=20,
        description=(
            "How many sites to return. "
            "Use 500 when user says 'all sites', 'every site', or 'show all'. "
            "Use the exact number when user says 'give me N sites'. "
            "Use 20 (default) when no count is specified. "
            "Maximum allowed: 500."
        )
    )
    skip: Optional[int] = Field(
        default=None,
        description=(
            "Number of sites to skip — use for pagination. "
            "When user says 'next page' or 'show more', set skip to the number of sites already returned "
            "(e.g. if previous call had top=20, set skip=20 to get the next 20)."
        )
    )
    orderby: Optional[str] = Field(
        default=None,
        description=(
            "Sort field and direction. Examples: "
            "'createdDateTime desc' for recently created first, "
            "'lastModifiedDateTime desc' for recently modified first, "
            "'name asc' for alphabetical order."
        )
    )


class GetSiteInput(BaseModel):
    """Schema for getting a specific site"""
    site_id: str = Field(description="The SharePoint site ID (e.g. 'contoso.sharepoint.com,site-guid,web-guid')")



class GetPagesInput(BaseModel):
    """Schema for listing pages in a site."""
    site_id: str = Field(description="The SharePoint site ID")
    top: Optional[int] = Field(default=20, description="Maximum number of pages to return (max 50)")
    search: Optional[str] = Field(
        default=None,
        description=(
            "Filter pages by title keyword. "
            "Example: 'KT' to find pages with 'KT' in the title. "
            "Leave empty to return all pages."
        )
    )


class GetPageInput(BaseModel):
    """Schema for getting a single SharePoint page by ID."""
    site_id: str = Field(description="The SharePoint site ID")
    page_id: str = Field(
        description="The page ID (GUID) — obtain this from get_pages or search_pages results"
    )


class SearchPagesInput(BaseModel):
    """Schema for searching SharePoint pages across all sites by keyword.

    Uses Microsoft Graph Search API with EntityType.DriveItem filtered to
    .aspx files — searches across ALL sites the user has access to without
    needing to know the site first.

    IMPORTANT — use this when:
      - User asks for a page by name/keyword but you don't know which site
      - User says 'find page X', 'show me the KT page', 'search for pages about Y'
    """
    query: str = Field(
        description=(
            "Keyword or phrase from the page name/title to search for. "
            "Use the words the user mentioned as part of the page name. "
            "Examples: 'pipeshub kt', 'onboarding', 'deployment guide', 'project overview'. "
            "The search runs across ALL sites the user has access to."
        )
    )
    top: Optional[int] = Field(
        default=10,
        description=(
            "Maximum number of pages to return (default 10, max 50). "
            "Use 50 for 'show all pages about X'."
        )
    )
    skip: Optional[int] = Field(
        default=None,
        description="Number of results to skip for pagination (e.g. skip=10 for next page)"
    )


class CreatePageInput(BaseModel):
    """Schema for creating a new SharePoint modern site page.

    Pages are created as modern SharePoint pages (SitePages) with a single
    full-width text web part containing the provided HTML content.
    After creation the page is published unless publish=False is specified.
    """
    site_id: str = Field(description="The SharePoint site ID where the page will be created")
    title: str = Field(description="Page title (also used to generate the .aspx filename)")
    content_html: str = Field(
        description=(
            "Page body as HTML. Use standard HTML tags: "
            "<h1>, <h2>, <h3>, <p>, <ul>, <li>, <ol>, <strong>, <em>, <br/>, <code>, <pre>. "
            "Example: '<h1>Project Overview</h1><p>This page describes...</p><ul><li>Point 1</li></ul>'. "
            "The content is placed in a full-width text web part on the page."
        )
    )
    publish: Optional[bool] = Field(
        default=True,
        description=(
            "Whether to publish the page immediately after creation (default: True). "
            "Set to False to save as a draft that only site editors can see."
        )
    )


class UpdatePageInput(BaseModel):
    """Schema for updating an existing SharePoint modern site page.

    Provide title and/or content_html — at least one must be given.
    The page is re-published after update unless publish=False is specified.
    Fetch the current page content first with get_page if you need to merge content.
    """
    site_id: str = Field(description="The SharePoint site ID")
    page_id: str = Field(
        description=(
            "The page ID (GUID) — obtain this from get_pages results "
            "(e.g. 'xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx')"
        )
    )
    title: Optional[str] = Field(
        default=None,
        description="New page title (optional — omit to keep the existing title)"
    )
    content_html: Optional[str] = Field(
        default=None,
        description=(
            "New page content as HTML (optional — omit to keep existing content). "
            "Use standard HTML tags: <h1>, <h2>, <p>, <ul>, <li>, <strong>, <em>, <br/>, <code>, <pre>. "
            "This replaces the entire page body — call get_pages first if you need to merge with existing content."
        )
    )
    publish: Optional[bool] = Field(
        default=True,
        description="Whether to publish the page after update (default: True). Set to False to save as draft."
    )


# ---------------------------------------------------------------------------
# Toolset registration
# ---------------------------------------------------------------------------

@ToolsetBuilder("SharePoint")\
    .in_group("Microsoft 365")\
    .with_description("SharePoint integration for sites, lists, and document management")\
    .with_category(ToolsetCategory.APP)\
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
                    "Files.ReadWrite.All",
                    "offline_access",
                    "User.Read",
                ]
            ),
            additional_params={
                "prompt": "select_account",
                "response_mode": "query",
            },
            fields=[
                CommonFields.client_id("Azure App Registration"),
                CommonFields.client_secret("Azure App Registration"),
                AuthField(
                    name="tenantId",
                    display_name="Tenant ID",
                    field_type="TEXT",
                    placeholder="common  (or your Azure AD tenant ID / domain)",
                    description=(
                        "Your Azure Active Directory tenant ID (e.g. "
                        "'xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx') or domain "
                        "(e.g. 'contoso.onmicrosoft.com'). "
                        "Leave blank or enter 'common' to allow both personal Microsoft "
                        "accounts and any Azure AD tenant."
                    ),
                    required=False,
                    default_value="common",
                    min_length=0,
                    max_length=500,
                    is_secret=False,
                ),
            ],
            icon_path="/assets/icons/connectors/sharepoint.svg",
            app_group="Microsoft 365",
            app_description="Microsoft SharePoint OAuth application for agent integration",
            documentation_links=[
                DocumentationLink(
                    title="Create an Azure App Registration",
                    url="https://learn.microsoft.com/en-us/entra/identity-platform/quickstart-register-app",
                    doc_type="setup",
                ),
                DocumentationLink(
                    title="Microsoft Graph SharePoint permissions",
                    url="https://learn.microsoft.com/en-us/graph/permissions-reference",
                    doc_type="reference",
                ),
                DocumentationLink(
                    title="Configure OAuth 2.0 redirect URIs",
                    url="https://learn.microsoft.com/en-us/entra/identity-platform/reply-url",
                    doc_type="setup",
                ),
            ],
        )
    ])\
    .configure(lambda builder: builder.with_icon("/assets/icons/connectors/sharepoint.svg"))\
    .build_decorator()
class SharePoint:
    """Microsoft SharePoint toolset for sites, lists, and document management.

    Initialised with an MSGraphClient built via ``build_from_toolset`` which
    uses delegated OAuth.  The data source (``SharePointDataSource``) is
    constructed identically to the connector path.

    Client chain:
        MSGraphClient (from build_from_toolset)
            → .get_client()  → _DelegatedGraphClient shim
                → .get_ms_graph_service_client() → GraphServiceClient
        SharePointDataSource(ms_graph_client)
            → internally: self.client = client.get_client().get_ms_graph_service_client()
            → all /sites/* Graph API calls go through self.client
    """

    def __init__(self, client: MSGraphClient) -> None:
        """Initialize the SharePoint toolset.

        The data source is created in exactly the same way the connector
        creates it — ``SharePointDataSource(client)`` — so every
        method the connector can call is available here too.

        Args:
            client: Authenticated MSGraphClient instance (from build_from_toolset)
        """
        self.client = SharePointDataSource(client)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _handle_error(self, error: Exception, operation: str = "operation") -> tuple[bool, str]:
        """Return a standardised error tuple."""
        error_msg = str(error).lower()

        if isinstance(error, AttributeError):
            if "client" in error_msg or "sites" in error_msg:
                logger.error(
                    f"SharePoint client not properly initialised – authentication may be required: {error}"
                )
                return False, json.dumps({
                    "error": (
                        "SharePoint toolset is not authenticated. "
                        "Please complete the OAuth flow first. "
                        "Go to Settings > Toolsets to authenticate your SharePoint account."
                    )
                })

        if (
            isinstance(error, ValueError)
            or "not authenticated" in error_msg
            or "oauth" in error_msg
            or "authentication" in error_msg
            or "unauthorized" in error_msg
        ):
            logger.error(f"SharePoint authentication error during {operation}: {error}")
            return False, json.dumps({
                "error": (
                    "SharePoint toolset is not authenticated. "
                    "Please complete the OAuth flow first. "
                    "Go to Settings > Toolsets to authenticate your SharePoint account."
                )
            })

        logger.error(f"Failed to {operation}: {error}")
        return False, json.dumps({"error": str(error)})

    @staticmethod
    def _serialize_response(response_obj: Any) -> Any:
        """Recursively convert a Graph SDK response object to a JSON-serialisable dict.

        Kiota model objects store their properties in an internal backing store.
        We use kiota's JsonSerializationWriter first, then fall back to backing
        store enumeration, then vars().
        """
        if response_obj is None:
            return None
        if isinstance(response_obj, (str, int, float, bool)):
            return response_obj
        if isinstance(response_obj, list):
            return [SharePoint._serialize_response(item) for item in response_obj]
        if isinstance(response_obj, dict):
            return {k: SharePoint._serialize_response(v) for k, v in response_obj.items()}

        # Kiota Parsable objects
        if hasattr(response_obj, "get_field_deserializers"):
            try:
                writer = JsonSerializationWriter()
                writer.write_object_value(None, response_obj)
                content = writer.get_serialized_content()
                if content:
                    raw = content.decode("utf-8") if isinstance(content, bytes) else content
                    parsed = json.loads(raw)
                    if isinstance(parsed, dict) and parsed:
                        return parsed
            except Exception:
                pass

            try:
                backing_store = getattr(response_obj, "backing_store", None)
                if backing_store is not None and hasattr(backing_store, "enumerate_"):
                    result: Dict[str, Any] = {}
                    for key, value in backing_store.enumerate_():
                        if not str(key).startswith("_"):
                            try:
                                result[key] = SharePoint._serialize_response(value)
                            except Exception:
                                result[key] = str(value)
                    additional = getattr(response_obj, "additional_data", None)
                    if isinstance(additional, dict):
                        for k, v in additional.items():
                            if k not in result:
                                try:
                                    result[k] = SharePoint._serialize_response(v)
                                except Exception:
                                    result[k] = str(v)
                    if result:
                        return result
            except Exception:
                pass

        try:
            obj_dict = vars(response_obj)
        except TypeError:
            obj_dict = {}

        result = {}
        for k, v in obj_dict.items():
            if k.startswith("_"):
                continue
            try:
                result[k] = SharePoint._serialize_response(v)
            except Exception:
                result[k] = str(v)

        additional = getattr(response_obj, "additional_data", None)
        if isinstance(additional, dict):
            for k, v in additional.items():
                if k not in result:
                    try:
                        result[k] = SharePoint._serialize_response(v)
                    except Exception:
                        result[k] = str(v)

        return result if result else str(response_obj)

    def _extract_collection(self, data: Any) -> List[Any]:
        """Extract and serialize a collection from a Graph SDK response."""
        items: List[Any] = []
        if isinstance(data, dict):
            raw = data.get("value", [])
            items = [self._serialize_response(item) for item in raw]
        elif isinstance(data, list):
            items = [self._serialize_response(item) for item in data]
        elif hasattr(data, "value") and data.value is not None:
            items = [self._serialize_response(item) for item in data.value]
        else:
            serialized = self._serialize_response(data)
            if isinstance(serialized, dict) and "value" in serialized:
                items = serialized["value"]
                if isinstance(items, list):
                    items = [self._serialize_response(i) if not isinstance(i, (dict, str, int, float, bool)) else i for i in items]
            elif isinstance(serialized, dict):
                items = [serialized]
        return items

    # ------------------------------------------------------------------
    # Sites tools
    # ------------------------------------------------------------------

    @tool(
        app_name="sharepoint",
        tool_name="get_sites",
        description="List SharePoint sites accessible to the user with search and pagination",
        args_schema=GetSitesInput,
        when_to_use=[
            "User wants to list all SharePoint sites → set top=500",
            "User mentions 'SharePoint' + wants sites",
            "User asks what sites are available → set top=500 for 'all', or exact N for 'give me N'",
            "User wants to search for a specific site by name or keyword",
            "User wants a specific number of sites (e.g. 'show me 5 sites') → top=5",
            "User wants to paginate — 'next page' / 'show more' → keep same top, increment skip by top",
        ],
        when_not_to_use=[
            "User wants a specific site by ID (use get_site)",
            "User wants pages (use get_pages or search_pages)",
            "No SharePoint mention",
        ],
        primary_intent=ToolIntent.SEARCH,
        typical_queries=[
            "List all SharePoint sites → top=500",
            "Give me all sites → top=500",
            "Show available sites → top=500",
            "What sites do I have access to? → top=500",
            "Show me 5 SharePoint sites → top=5",
            "Show me the first 20 sites → top=20",
            "Show the next 20 sites (after already seeing 20) → top=20, skip=20",
            "Find SharePoint sites about marketing → search='marketing'",
            "Show recently created sites → orderby='createdDateTime desc'",
        ],
        category=ToolCategory.DOCUMENTATION,
    )
    async def get_sites(
        self,
        search: Optional[str] = None,
        top: Optional[int] = 20,
        skip: Optional[int] = None,
        orderby: Optional[str] = None,
    ) -> tuple[bool, str]:
        """List SharePoint sites accessible to the user.

        Uses Microsoft Graph Search API (POST /search/query) which returns ALL sites
        the user has access to, security-trimmed to their permissions.
        Supports pagination via top/skip, KQL filtering via search, and sorting via orderby.
        """
        try:
            response = await self.client.list_sites_with_search_api(
                search_query=search,
                top=min(top or 20, 500),
                from_index=skip or 0,
                orderby=orderby,
            )
            if response.success:
                data = response.data or {}
                sites = data.get("sites") or data.get("value") or []
                page_size = min(top or 20, 500)
                return True, json.dumps({
                    "sites": sites,
                    "results": sites,
                    "value": sites,
                    "count": len(sites),
                    "has_more": len(sites) == page_size,
                    "next_skip": (skip or 0) + len(sites),
                    "pagination_hint": (
                        f"To get the next page, use skip={((skip or 0) + len(sites))} with top={page_size}"
                        if len(sites) == page_size else "All available results returned"
                    ),
                })
            else:
                return False, json.dumps({"error": response.error or "Failed to list sites"})
        except Exception as e:
            return self._handle_error(e, "get sites")

    @tool(
        app_name="sharepoint",
        tool_name="get_site",
        description="Get details of a specific SharePoint site",
        args_schema=GetSiteInput,
        when_to_use=[
            "User wants details about a specific SharePoint site",
            "User has a site ID",
            "User asks for site information",
        ],
        when_not_to_use=[
            "User wants to list all sites (use get_sites)",
            "User wants pages (use get_pages)",
            "No SharePoint mention",
        ],
        primary_intent=ToolIntent.SEARCH,
        typical_queries=[
            "Get site details",
            "Show site information",
            "What's in this site?",
        ],
        category=ToolCategory.DOCUMENTATION,
    )
    async def get_site(
        self,
        site_id: str,
    ) -> tuple[bool, str]:
        """Get a specific SharePoint site by ID."""
        try:
            response = await self.client.get_site_by_id(site_id=site_id)
            if response.success:
                return True, json.dumps(response.data)
            else:
                return False, json.dumps({"error": response.error or "Site not found"})
        except Exception as e:
            return self._handle_error(e, f"get site {site_id}")

    # ------------------------------------------------------------------
    # Pages tools
    # ------------------------------------------------------------------

    @tool(
        app_name="sharepoint",
        tool_name="get_pages",
        description="List pages in a specific SharePoint site, optionally filtered by title keyword",
        args_schema=GetPagesInput,
        when_to_use=[
            "User already knows the site and wants to browse its pages",
            "User says 'show pages in the Engineering site' or 'list pages in this site'",
            "Use search param to filter by title: 'show KT pages in this site' → search='KT'",
            "After get_sites found a site and user wants to see its pages",
        ],
        when_not_to_use=[
            "User doesn't know which site the page is in — use search_pages instead",
            "User wants to find a page by keyword across all sites — use search_pages",
            "User wants to list sites (use get_sites)",
            "No SharePoint mention",
        ],
        primary_intent=ToolIntent.SEARCH,
        typical_queries=[
            "Show pages in this site",
            "What pages are available in the Engineering site?",
            "List all SharePoint pages in this site",
            "Find pages with 'KT' in the title in this site → search='KT'",
        ],
        category=ToolCategory.DOCUMENTATION,
    )
    async def get_pages(
        self,
        site_id: str,
        top: Optional[int] = 20,
        search: Optional[str] = None,
    ) -> tuple[bool, str]:
        """Get pages from a SharePoint site.

        Tries two approaches:
        1. Modern pages API: GET /sites/{id}/pages (supports $search for title filtering)
        2. Classic pages list: GET /sites/{id}/lists/SitePages/items (fallback)
        """
        logger.info(f"📍 Getting pages for site_id={site_id}" + (f" search={search!r}" if search else ""))

        # Try modern pages API first
        try:
            graph = self.client.client
            query_params = PagesRequestBuilder.PagesRequestBuilderGetQueryParameters()
            query_params.top = min(top or 20, 50)
            if search and search.strip():
                query_params.search = f'"{search.strip()}"'

            config = PagesRequestBuilder.PagesRequestBuilderGetRequestConfiguration(
                query_parameters=query_params,
            )

            logger.debug(f"🌐 Trying modern pages API: /sites/{site_id}/pages")
            response = await graph.sites.by_site_id(site_id).pages.get(request_configuration=config)
            items = self._extract_collection(response)
            logger.info(f"✅ Modern pages API returned {len(items)} pages")
            return True, json.dumps({
                "pages": items,
                "results": items,
                "value": items,
                "count": len(items),
            })
        except Exception as modern_error:
            error_str = str(modern_error).lower()
            if "404" in error_str or "not found" in error_str:
                logger.info(f"⚠️ Modern pages API not available (404), trying SitePages list fallback")
                
                # Fallback: Try getting pages from SitePages list
                try:
                    graph = self.client.client
                    query_params = ItemsRequestBuilder.ItemsRequestBuilderGetQueryParameters()
                    query_params.top = min(top or 20, 50)
                    query_params.expand = ["fields"]
                    query_params.filter = "fields/ContentType eq 'Site Page'"
                    
                    config = ItemsRequestBuilder.ItemsRequestBuilderGetRequestConfiguration(
                        query_parameters=query_params,
                    )
                    
                    logger.debug(f"🌐 Trying SitePages list: /sites/{site_id}/lists/SitePages/items")
                    # Note: "SitePages" is the standard list name for pages
                    response = await graph.sites.by_site_id(site_id).lists.by_list_id("SitePages").items.get(request_configuration=config)
                    items = self._extract_collection(response)
                    logger.info(f"✅ SitePages list returned {len(items)} pages")
                    return True, json.dumps({
                        "pages": items,
                        "results": items,
                        "value": items,
                        "count": len(items),
                    })
                except Exception as list_error:
                    list_error_str = str(list_error).lower()
                    if "404" in list_error_str or "not found" in list_error_str or "itemnotfound" in list_error_str:
                        # Both APIs returned 404 — site has no pages, not an error
                        logger.info(f"ℹ️ Site {site_id} has no pages (both APIs returned 404)")
                        return True, json.dumps({
                            "pages": [],
                            "results": [],
                            "value": [],
                            "count": 0,
                            "note": "No pages found for this site",
                        })
                    logger.error(f"❌ Both modern pages API and SitePages list failed. Modern error: {modern_error}, List error: {list_error}")
                    return self._handle_error(list_error, "get pages")
            else:
                logger.error(f"❌ Modern pages API failed with non-404 error: {modern_error}")
                return self._handle_error(modern_error, "get pages")

    @tool(
        app_name="sharepoint",
        tool_name="get_page",
        description="Get the full content and metadata of a single SharePoint site page by ID",
        args_schema=GetPageInput,
        when_to_use=[
            "User wants to read the content of a specific SharePoint page",
            "User has a page ID and wants its full details",
            "Need to fetch current page content before updating it (call before update_page)",
            "User asks 'show me this page' or 'what does this page say'",
        ],
        when_not_to_use=[
            "User wants to list all pages in a site (use get_pages)",
            "User does not have a page ID — call search_pages or get_pages first to find it",
            "No SharePoint mention",
        ],
        primary_intent=ToolIntent.SEARCH,
        typical_queries=[
            "Show me the content of this SharePoint page",
            "Get the page with ID <id>",
            "What does the 'Project Overview' page say?",
            "Fetch the page so I can update it",
        ],
        category=ToolCategory.DOCUMENTATION,
    )
    async def get_page(
        self,
        site_id: str,
        page_id: str,
    ) -> tuple[bool, str]:
        """Get a single SharePoint modern page by its ID.

        Uses GET /sites/{site_id}/pages/{page_id}/microsoft.graph.sitePage
        which returns the full page including canvasLayout and webparts.
        Falls back to the basic page endpoint if the typed cast is unavailable.
        """
        try:
            graph = self.client.client
            logger.info(f"📍 Getting page {page_id} from site {site_id}")

            ri = RequestInformation()
            ri.http_method = Method.GET
            ri.url_template = (
                f"https://graph.microsoft.com/v1.0/sites/{site_id}"
                f"/pages/{page_id}/microsoft.graph.sitePage"
                f"?$expand=canvasLayout"
            )
            ri.path_parameters = {}

            response = await graph.request_adapter.send_async(ri, SitePage, {})
            page_data = self._serialize_response(response)

            if not page_data or page_data == str(response):
                return False, json.dumps({"error": "Page not found or could not be serialized"})

            logger.info(f"✅ Retrieved page {page_id}")
            return True, json.dumps({
                "page": page_data,
                "id": page_data.get("id") if isinstance(page_data, dict) else page_id,
                "title": page_data.get("title") if isinstance(page_data, dict) else None,
                "web_url": (
                    page_data.get("webUrl") or page_data.get("web_url")
                    if isinstance(page_data, dict) else None
                ),
            })

        except Exception as e:
            return self._handle_error(e, f"get page {page_id}")

    @tool(
        app_name="sharepoint",
        tool_name="search_pages",
        description="Search SharePoint pages by keyword across ALL sites — no site ID needed",
        args_schema=SearchPagesInput,
        when_to_use=[
            "User wants to find a specific page by name/keyword but doesn't know which site it's in",
            "User says 'find the KT page', 'show me the deployment guide', 'search for pages about X'",
            "User gives a page title or topic without mentioning a site",
            "ALWAYS prefer this over get_sites + get_pages chain when the user mentions a page name",
        ],
        when_not_to_use=[
            "User already knows the site and wants to browse all its pages (use get_pages)",
            "User wants site information (use get_sites or get_site)",
            "User already has a page ID and wants full content (use get_page)",
            "No SharePoint mention",
        ],
        primary_intent=ToolIntent.SEARCH,
        typical_queries=[
            "Find the KT page → query='KT'",
            "Show me the pipeshub KT page → query='pipeshub KT'",
            "Give me details of the onboarding page → query='onboarding'",
            "Find the deployment guide page → query='deployment guide'",
            "What pages mention the API? → query='API'",
        ],
        category=ToolCategory.DOCUMENTATION,
    )
    async def search_pages(
        self,
        query: str,
        top: Optional[int] = 10,
        skip: Optional[int] = None,
    ) -> tuple[bool, str]:
        """Search for SharePoint pages by keyword across all accessible sites.

        Uses the Microsoft Graph Search API with EntityType.DriveItem filtered
        to .aspx files — this searches ALL sites the user has access to without
        needing a site ID.

        Returns page name, title, web_url, site_id, and dates.
        If full page content is needed, follow up with get_page(site_id, page_id)
        after retrieving the page_id via get_pages(site_id, search=query).
        """
        try:
            response = await self.client.search_pages_with_search_api(
                query=query,
                top=min(top or 10, 50),
                from_index=skip or 0,
            )
            if response.success:
                data = response.data or {}
                pages = data.get("pages") or []
                page_size = min(top or 10, 50)
                logger.info(f"✅ search_pages found {len(pages)} pages for query={query!r}")
                return True, json.dumps({
                    "pages": pages,
                    "results": pages,
                    "value": pages,
                    "count": len(pages),
                    "has_more": len(pages) == page_size,
                    "next_skip": (skip or 0) + len(pages),
                    "pagination_hint": (
                        f"To get the next page, use skip={((skip or 0) + len(pages))} with top={page_size}"
                        if len(pages) == page_size else "All available results returned"
                    ),
                    "usage_hint": (
                        "Each result includes web_url (direct link), site_id (for get_pages/get_page), "
                        "and name/title. To read full page content: "
                        "1) call get_pages(site_id, search=query) to get page_id, "
                        "2) call get_page(site_id, page_id) for full content."
                    ),
                })
            else:
                return False, json.dumps({"error": response.error or "Failed to search pages"})
        except Exception as e:
            return self._handle_error(e, f"search pages '{query}'")

    # ------------------------------------------------------------------
    # Page write tools (create / update)
    # ------------------------------------------------------------------

    @tool(
        app_name="sharepoint",
        tool_name="create_page",
        description="Create a new modern page in a SharePoint site",
        args_schema=CreatePageInput,
        when_to_use=[
            "User wants to create a new SharePoint page",
            "User says 'create a page in SharePoint' or 'add a page to this site'",
            "User wants to write/author a page with content",
        ],
        when_not_to_use=[
            "User wants to update an existing page (use update_page)",
            "User wants to read/list pages (use get_pages)",
            "User wants to create a list item, not a page",
            "No SharePoint mention",
        ],
        primary_intent=ToolIntent.ACTION,
        typical_queries=[
            "Create a SharePoint page called 'Project Overview'",
            "Add a new page to this site with the meeting notes",
            "Create a page in SharePoint with this content",
            "Write a new site page about the deployment process",
        ],
        category=ToolCategory.DOCUMENTATION,
    )
    async def create_page(
        self,
        site_id: str,
        title: str,
        content_html: str,
        publish: Optional[bool] = True,
    ) -> tuple[bool, str]:
        """Create a new modern page in a SharePoint site.

        Uses the Microsoft Graph API to create a SitePage with a single
        full-width text web part containing the provided HTML content.
        After creation the page is published (unless publish=False).

        Content format — use standard HTML:
          - Headings:  <h1>Title</h1>, <h2>Section</h2>
          - Paragraph: <p>Text here.</p>
          - Bold/italic: <strong>bold</strong>, <em>italic</em>
          - Lists:     <ul><li>Item</li></ul>  /  <ol><li>Step</li></ol>
          - Code:      <pre><code>code here</code></pre>
          - Line break: <br/>
        """
        try:
            graph = self.client.client

            # Build a URL-safe slug for the .aspx filename
            slug = re.sub(r"[^a-z0-9-]", "-", title.lower()).strip("-")
            slug = re.sub(r"-+", "-", slug) or "page"

            # Build a SitePage object. Complex nested fields (canvasLayout)
            # are passed through additional_data so Kiota serialises them as-is.
            body = SitePage()
            body.additional_data = {
                "@odata.type": "#microsoft.graph.sitePage",
                "title": title,
                "name": f"{slug}.aspx",
                "pageLayout": "article",
                "canvasLayout": {
                    "horizontalSections": [
                        {
                            "layout": "oneColumn",
                            "id": "1",
                            "columns": [
                                {
                                    "id": "1",
                                    "width": 12,
                                    "webparts": [
                                        {
                                            "@odata.type": "#microsoft.graph.textWebPart",
                                            "innerHtml": content_html,
                                        }
                                    ],
                                }
                            ],
                        }
                    ]
                },
            }

            logger.info(f"📍 Creating page '{title}' in site {site_id}")
            response = await graph.sites.by_site_id(site_id).pages.post(body)

            page_data = self._serialize_response(response)
            page_id = page_data.get("id") if isinstance(page_data, dict) else None

            published = False
            publish_error = None
            if publish and page_id:
                try:
                    ri = RequestInformation()
                    ri.http_method = Method.POST
                    ri.url_template = (
                        f"https://graph.microsoft.com/v1.0/sites/{site_id}"
                        f"/pages/{page_id}/microsoft.graph.sitePage/publish"
                    )
                    ri.path_parameters = {}
                    await graph.request_adapter.send_no_response_content_async(ri, {})
                    published = True
                    logger.info(f"✅ Page '{title}' published (id={page_id})")
                except Exception as pub_err:
                    publish_error = str(pub_err)
                    logger.warning(f"⚠️ Page created but publish failed: {pub_err}")

            web_url = (
                page_data.get("webUrl") or page_data.get("web_url")
                if isinstance(page_data, dict) else None
            )

            return True, json.dumps({
                "message": f"Page '{title}' created {'and published ' if published else '(draft) '}successfully",
                "page": page_data if isinstance(page_data, dict) else {},
                "id": page_id,
                "title": title,
                "published": published,
                "web_url": web_url,
                **({"publish_error": publish_error} if publish_error else {}),
            })

        except Exception as e:
            return self._handle_error(e, f"create page '{title}'")

    @tool(
        app_name="sharepoint",
        tool_name="update_page",
        description="Update the title and/or content of an existing SharePoint site page",
        args_schema=UpdatePageInput,
        when_to_use=[
            "User wants to edit or update an existing SharePoint page",
            "User says 'update the page', 'edit this page', 'change the content of'",
            "User wants to rename a SharePoint page",
            "User wants to rewrite or append content to an existing page",
        ],
        when_not_to_use=[
            "User wants to create a brand-new page (use create_page)",
            "User wants to read/list pages (use get_pages)",
            "User does not have the page ID — call get_pages first to find it",
            "No SharePoint mention",
        ],
        primary_intent=ToolIntent.ACTION,
        typical_queries=[
            "Update the 'Project Overview' SharePoint page with new content",
            "Edit the page and add a new section about deployment",
            "Rename the SharePoint page to 'Q1 Planning'",
            "Rewrite the content of this page",
        ],
        category=ToolCategory.DOCUMENTATION,
    )
    async def update_page(
        self,
        site_id: str,
        page_id: str,
        title: Optional[str] = None,
        content_html: Optional[str] = None,
        publish: Optional[bool] = True,
    ) -> tuple[bool, str]:
        """Update the title and/or content of an existing SharePoint modern page.

        At least one of title or content_html must be provided.
        After the update the page is re-published (unless publish=False).

        To merge content with the existing page, call get_pages first to
        retrieve the current content, then pass the merged HTML here.

        Content format — use standard HTML:
          - Headings:  <h1>Title</h1>, <h2>Section</h2>
          - Paragraph: <p>Text here.</p>
          - Bold/italic: <strong>bold</strong>, <em>italic</em>
          - Lists:     <ul><li>Item</li></ul>  /  <ol><li>Step</li></ol>
          - Code:      <pre><code>code here</code></pre>
          - Line break: <br/>
        """
        if title is None and content_html is None:
            return False, json.dumps({"error": "At least one of 'title' or 'content_html' must be provided"})

        try:
            graph = self.client.client

            # Build the PATCH body — only include fields being changed
            patch_data: Dict[str, Any] = {
                "@odata.type": "#microsoft.graph.sitePage",
            }
            if title is not None:
                patch_data["title"] = title
            if content_html is not None:
                patch_data["canvasLayout"] = {
                    "horizontalSections": [
                        {
                            "layout": "oneColumn",
                            "id": "1",
                            "columns": [
                                {
                                    "id": "1",
                                    "width": 12,
                                    "webparts": [
                                        {
                                            "@odata.type": "#microsoft.graph.textWebPart",
                                            "innerHtml": content_html,
                                        }
                                    ],
                                }
                            ],
                        }
                    ]
                }

            # PATCH via raw URL — the typed SDK path (.microsoft_graph_site_page)
            # does not exist on BaseSitePageItemRequestBuilder in all SDK versions.
            logger.info(f"📍 Updating page {page_id} in site {site_id}")
            patch_ri = RequestInformation()
            patch_ri.http_method = Method.PATCH
            patch_ri.url_template = (
                f"https://graph.microsoft.com/v1.0/sites/{site_id}"
                f"/pages/{page_id}/microsoft.graph.sitePage"
            )
            patch_ri.path_parameters = {}
            patch_ri.content = json.dumps(patch_data).encode("utf-8")
            patch_ri.headers.try_add("Content-Type", "application/json")
            await graph.request_adapter.send_no_response_content_async(patch_ri, {})

            page_data: Dict[str, Any] = {"id": page_id}

            published = False
            publish_error = None
            if publish:
                try:
                    pub_ri = RequestInformation()
                    pub_ri.http_method = Method.POST
                    pub_ri.url_template = (
                        f"https://graph.microsoft.com/v1.0/sites/{site_id}"
                        f"/pages/{page_id}/microsoft.graph.sitePage/publish"
                    )
                    pub_ri.path_parameters = {}
                    await graph.request_adapter.send_no_response_content_async(pub_ri, {})
                    published = True
                    logger.info(f"✅ Page {page_id} updated and published")
                except Exception as pub_err:
                    publish_error = str(pub_err)
                    logger.warning(f"⚠️ Page updated but publish failed: {pub_err}")

            web_url = (
                page_data.get("webUrl") or page_data.get("web_url")
                if isinstance(page_data, dict) else None
            )

            return True, json.dumps({
                "message": f"Page updated {'and published ' if published else '(draft) '}successfully",
                "page": page_data if isinstance(page_data, dict) else {},
                "id": page_id,
                **({"title": title} if title else {}),
                "published": published,
                "web_url": web_url,
                **({"publish_error": publish_error} if publish_error else {}),
            })

        except Exception as e:
            return self._handle_error(e, f"update page {page_id}")
