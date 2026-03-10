import json
import logging
import re
from typing import Any, Dict, List, Optional
from urllib.parse import quote

from kiota_abstractions.method import Method  # type: ignore
from kiota_abstractions.request_information import RequestInformation  # type: ignore
from kiota_serialization_json.json_serialization_writer import JsonSerializationWriter  # type: ignore
from msgraph.generated.models.site_page import SitePage  # type: ignore
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
from app.connectors.core.constants import IconPaths
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
        default=10,
        description=(
            "How many sites to return (default 10, max 50). "
            "Use 50 when user says 'all sites', 'every site', or 'show all'. "
            "Use the exact number when user says 'give me N sites'. "
            "Use 10 (default) when no count is specified. "
            "Maximum allowed: 50."
        )
    )
    skip: Optional[int] = Field(
        default=None,
        description=(
            "Number of sites to skip — use for pagination. "
            "When user says 'next page' or 'show more', set skip to the number of sites already returned "
            "(e.g. if previous call had top=10, set skip=10 to get the next 10)."
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
    top: Optional[int] = Field(default=10, description="Maximum number of pages to return (default 10, max 50)")


class GetPageInput(BaseModel):
    """Schema for getting a single SharePoint page by ID."""
    site_id: str = Field(description="The SharePoint site ID (from search_pages or get_pages results)")
    page_id: str = Field(
        description="The page ID (GUID) — use the 'page_id' field from search_pages or get_pages results"
    )


class SearchPagesInput(BaseModel):
    """Schema for searching SharePoint pages across all sites by keyword.

    Uses Microsoft Graph Search API with EntityType.ListItem filtered to
    modern site pages — searches across ALL sites the user has access to without
    needing to know the site first.

    IMPORTANT — use this when:
      - User asks for a page by name/keyword but you don't know which site or page_id
      - User says 'find page X', 'show me the KT page', 'search for pages about Y'
      - User wants to read/summarize a page by name: 'summarize the KT page', 'read the deployment guide'
      - This is the FIRST step to get page_id before calling get_page for full content
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


# ---------------------------------------------------------------------------
# Document / File schemas
# ---------------------------------------------------------------------------

class ListDrivesInput(BaseModel):
    """Schema for listing document libraries (drives) in a SharePoint site."""
    site_id: str = Field(
        description=(
            "The SharePoint site ID (from get_sites results). "
            "Format: 'contoso.sharepoint.com,<collection-guid>,<web-guid>'"
        )
    )
    top: Optional[int] = Field(
        default=10,
        description="Maximum number of document libraries to return (default 10, max 50)"
    )


class ListFilesInput(BaseModel):
    """Schema for listing files and folders inside SharePoint document libraries.

    - Omit drive_id to list files from ALL document libraries in the site (recommended
      when user says 'all documents', 'all files', 'list documents in this site').
    - Provide drive_id to list only a specific document library.
    - Provide folder_id to list the contents of a specific sub-folder (requires drive_id).
    """
    site_id: str = Field(description="The SharePoint site ID")
    drive_id: Optional[str] = Field(
        default=None,
        description=(
            "The document library (drive) ID — obtain from list_drives results. "
            "OMIT this when user wants ALL documents in a site (will iterate all drives automatically). "
            "Provide only when user specifies a particular document library. "
            "Example: 'b!abc123...'"
        )
    )
    folder_id: Optional[str] = Field(
        default=None,
        description=(
            "Item ID of a sub-folder to list (requires drive_id). "
            "If omitted, lists the root of the document library. "
            "Use the 'id' field from a previous list_files result to navigate into a folder."
        )
    )
    top: Optional[int] = Field(
        default=10,
        description=(
            "Maximum number of items per drive to return (default 10, max 50). "
            "When drive_id is omitted and all drives are iterated, this limit applies per drive."
        )
    )

class SearchFilesInput(BaseModel):
    """Schema for finding a specific SharePoint file or document by name or keyword.

    Uses the Microsoft Graph Search API (EntityType.DriveItem) to search
    across ALL document libraries the user has access to — no drive ID needed.

    Use this whenever the user refers to a specific file or document by name,
    regardless of the verb they use:
      - 'give me the assignment file'  → query='assignment'
      - 'show the budget document'     → query='budget'
    """
    query: str = Field(
        description=(
            "The file name or keyword the user mentioned. "
            "Extract the key noun/name from the user's request. "
            "Searches across ALL document libraries the user has access to."
        )
    )
    site_id: Optional[str] = Field(
        default=None,
        description=(
            "Optional: restrict results to files in a specific site. "
            "If not provided, searches ALL sites. "
            "Provide the site_id from get_sites when user specifies a particular site."
        )
    )
    top: Optional[int] = Field(
        default=10,
        description="Maximum number of results to return (default 10, max 50)"
    )
    skip: Optional[int] = Field(
        default=None,
        description="Number of results to skip for pagination (e.g. skip=10 for the next page)"
    )

class GetFileMetadataInput(BaseModel):
    """Schema for getting metadata of a specific SharePoint file or folder."""
    site_id: str = Field(description="The SharePoint site ID")
    drive_id: str = Field(
        description=(
            "The document library (drive) ID. "
            "Obtain from list_drives or from the 'parentReference.driveId' field in search_files results."
        )
    )
    item_id: str = Field(
        description=(
            "The DriveItem ID (GUID) of the file or folder. "
            "Use the 'id' field from list_files or search_files results."
        )
    )


class GetFileContentInput(BaseModel):
    """Schema for downloading and reading the text content of a SharePoint file.

    Works best for plain-text files: .txt, .csv, .md, .html, .json, .xml.
    For binary Office/PDF files the content is returned as base64.
    Always call get_file_metadata first to check the file's mimeType before downloading.
    """
    site_id: str = Field(description="The SharePoint site ID")
    drive_id: str = Field(
        description=(
            "The document library (drive) ID. "
            "Use 'parentReference.driveId' from search_files or list_files results."
        )
    )
    item_id: str = Field(
        description=(
            "The DriveItem ID (GUID) of the file to download. "
            "Use the 'id' field from list_files or search_files results."
        )
    )


class CreatePageInput(BaseModel):
    """Schema for creating a new SharePoint modern site page.

    Pages are created as modern SharePoint pages (SitePages) with a single
    full-width text web part containing the provided HTML content.
    Default is draft (publish=False). Only set publish=True when the user
    explicitly says to publish, make it live, or go public; if they said
    nothing about publishing, keep draft.
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
        default=False,
        description=(
            "Publishing: default False (draft). If the user did NOT say anything about publishing, "
            "keep False. Set True ONLY when the user explicitly asks to 'publish', 'make it live', or 'go public'."
        )
    )


class UpdatePageInput(BaseModel):
    """Schema for updating an existing SharePoint modern site page.

    Provide title and/or content_html — at least one must be given.
    Default is draft (publish=False). Only set publish=True when the user
    explicitly says to publish or make it live; if they said nothing about
    publishing, keep draft. Fetch the current page content first with get_page
    if you need to merge content.
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
        default=False,
        description=(
            "Publishing: default False (draft). If the user did NOT say anything about publishing, "
            "keep False. Set True ONLY when the user explicitly asks to 'publish', 'make it live', or 'go public'."
        )
    )


class CreateFolderInput(BaseModel):
    """Schema for creating a new folder in a SharePoint document library.

    Use list_drives to get the drive_id, and optionally list_files to get
    a parent_folder_id when creating inside a subfolder.
    """
    site_id: str = Field(
        description=(
            "The SharePoint site ID — used for context. "
            "Format: 'contoso.sharepoint.com,<collection-guid>,<web-guid>'"
        )
    )
    drive_id: str = Field(
        description=(
            "The document library (drive) ID where the folder will be created. "
            "Obtain from list_drives results."
        )
    )
    folder_name: str = Field(
        description=(
            "Name of the new folder to create. "
            "If a folder with this name already exists, a unique name is generated automatically."
        )
    )
    parent_folder_id: Optional[str] = Field(
        default=None,
        description=(
            "Item ID of the parent folder to create inside (optional). "
            "Omit to create in the root of the document library. "
            "Use the 'id' field from list_files results to target a subfolder."
        )
    )


class CreateWordDocumentInput(BaseModel):
    """Schema for creating a new Word document (.docx) in a SharePoint document library.

    The document is built in memory as a valid .docx and uploaded to SharePoint.
    Optional plain-text content is inserted as paragraphs in the document body.
    """
    site_id: str = Field(
        description="The SharePoint site ID."
    )
    drive_id: str = Field(
        description=(
            "The document library (drive) ID where the file will be created. "
            "Obtain from list_drives results."
        )
    )
    file_name: str = Field(
        description=(
            "Name of the new Word document (without the .docx extension — it is appended automatically). "
            "Example: 'Meeting Notes' → creates 'Meeting Notes.docx'."
        )
    )
    parent_folder_id: Optional[str] = Field(
        default=None,
        description=(
            "Item ID of the folder to create the file inside (optional). "
            "Omit to create in the root of the document library. "
            "Use the 'id' field from list_files results to target a subfolder."
        )
    )
    content_text: Optional[str] = Field(
        default=None,
        description=(
            "Optional plain-text content to write into the document body. "
            "Newlines are preserved as separate paragraphs. "
            "Leave empty to create a blank document."
        )
    )


class CreateOneNoteNotebookInput(BaseModel):
    """Schema for creating a new OneNote notebook in a SharePoint site.

    Optionally creates a first section and a first page with HTML content inside
    that section in a single operation.
    """
    site_id: str = Field(
        description="The SharePoint site ID where the notebook will be created."
    )
    notebook_name: str = Field(
        description="Display name of the new OneNote notebook."
    )
    section_name: Optional[str] = Field(
        default=None,
        description=(
            "Optional name for the first section to create inside the notebook. "
            "A page can only be created if a section is also provided."
        )
    )
    page_title: Optional[str] = Field(
        default=None,
        description=(
            "Optional title for the first page to create inside the section. "
            "Requires section_name to be provided. "
            "Defaults to the notebook name if omitted."
        )
    )
    page_content_html: Optional[str] = Field(
        default=None,
        description=(
            "Optional HTML content for the body of the first page. "
            "Requires section_name to be provided. "
            "Use standard HTML tags: <h1>, <h2>, <p>, <ul>, <li>, <strong>, <em>, <br/>. "
            "Example: '<h1>Introduction</h1><p>This notebook covers...</p>'"
        )
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
                    "Notes.ReadWrite.All",
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
            icon_path=IconPaths.connector_icon("sharepoint"),
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
    .configure(lambda builder: builder.with_icon(IconPaths.connector_icon("sharepoint")))\
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

    def _extract_page_html_content(self, page_data: Dict[str, Any]) -> str:
        """Extract HTML content from page canvasLayout webparts."""
        html_parts = []
        canvas_layout = page_data.get("canvasLayout") or {}
        horizontal_sections = canvas_layout.get("horizontalSections") or []
        
        for section in horizontal_sections:
            if not isinstance(section, dict):
                continue
            columns = section.get("columns") or []
            for column in columns:
                if not isinstance(column, dict):
                    continue
                webparts = column.get("webparts") or []
                for webpart in webparts:
                    if not isinstance(webpart, dict):
                        continue
                    # Extract HTML from text webparts
                    inner_html = webpart.get("innerHtml")
                    if inner_html:
                        html_parts.append(inner_html)
        
        return "\n\n".join(html_parts) if html_parts else ""

    # ------------------------------------------------------------------
    # Sites tools
    # ------------------------------------------------------------------

    @tool(
        app_name="sharepoint",
        tool_name="get_sites",
        description="List SharePoint sites accessible to the user with search and pagination",
        args_schema=GetSitesInput,
        when_to_use=[
            "User wants to list all SharePoint sites → set top=50",
            "User mentions 'SharePoint' + wants sites",
            "User wants to search for a specific site by name or keyword",
            "User says 'show pages in [site name]' — use this first to get site_id, then get_pages",
            "User wants a specific number of sites (e.g. 'show me 5 sites') → top=5",
            "User wants to paginate — 'next page' / 'show more' → keep same top (max 50), increment skip by top",
        ],
        when_not_to_use=[
            "User wants a specific site by ID (use get_site)",
            "User mentions a specific page by name (use search_pages directly, not this)",
            "No SharePoint mention",
        ],
        primary_intent=ToolIntent.SEARCH,
        typical_queries=[
            "List all SharePoint sites → top=50",
            "Give me all sites → top=50",
            "Show me 5 SharePoint sites → top=5",
            "Show pages in Engineering site → STEP 1: get_sites(search='Engineering'), STEP 2: get_pages(site_id=result.sites[0].id)",
            "Find SharePoint sites about marketing → search='marketing'",
            "Show recently created sites → orderby='createdDateTime desc'",
        ],
        category=ToolCategory.DOCUMENTATION,
    )
    async def get_sites(
        self,
        search: Optional[str] = None,
        top: Optional[int] = 10,
        skip: Optional[int] = None,
        orderby: Optional[str] = None,
    ) -> tuple[bool, str]:
        """List SharePoint sites accessible to the user.

        Uses Microsoft Graph Search API (POST /search/query) which returns ALL sites
        the user has access to, security-trimmed to their permissions.
        Supports pagination via top/skip (default 10, max 50), KQL filtering via search, and sorting via orderby.
        """
        try:
            response = await self.client.list_sites_with_search_api(
                search_query=search,
                top=min(top or 10, 50),
                from_index=skip or 0,
                orderby=orderby,
            )
            if response.success:
                data = response.data or {}
                sites = data.get("sites") or data.get("value") or []
                page_size = min(top or 10, 50)
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
                    "usage_hint": (
                        "Each site includes 'id' field (site_id) which can be used with get_pages(site_id=...) "
                        "or get_page(site_id=..., page_id=...) to access pages in that site."
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
        description="List ALL pages in a specific SharePoint site (returns page_id, title, webUrl)",
        args_schema=GetPagesInput,
        when_to_use=[
            "User wants to browse/list ALL pages in a site they mention by name",
            "User says 'show all pages in the Engineering site' or 'list pages in [site name]'",
            "Workflow: get_sites(search='site name') → get_pages(site_id=result.sites[0].id)",
            "ONLY when user wants a complete page list, not to find a specific page",
        ],
        when_not_to_use=[
            "User mentions a specific page by name — ALWAYS use search_pages instead",
            "User wants to summarize/read a specific page — use search_pages, NOT this tool",
            "User wants to find a particular page — use search_pages, NOT this tool",
            "After search_pages — NEVER call this, search_pages already has page_id",
            "User wants to list sites (use get_sites)",
            "No SharePoint mention",
        ],
        primary_intent=ToolIntent.SEARCH,
        typical_queries=[
            "Show all pages in the Engineering site → get_sites(search='Engineering') then get_pages(site_id=result.sites[0].id)",
            "What pages are available in this site?",
            "List every SharePoint page in the Marketing site",
        ],
        category=ToolCategory.DOCUMENTATION,
    )
    async def get_pages(
        self,
        site_id: str,
        top: Optional[int] = 10,
    ) -> tuple[bool, str]:
        """Get ALL pages from a SharePoint site (no filtering).

        Uses GET /sites/{id}/pages which returns all modern pages in the site (default 10, max 50).
        If you need to find a specific page by keyword, use search_pages instead.
        """
        logger.info(f"📍 Getting all pages for site_id={site_id}, top={top}")

        try:
            graph = self.client.client
            query_params = PagesRequestBuilder.PagesRequestBuilderGetQueryParameters()
            query_params.top = min(top or 10, 50)

            config = PagesRequestBuilder.PagesRequestBuilderGetRequestConfiguration(
                query_parameters=query_params,
            )

            response = await graph.sites.by_site_id(site_id).pages.get(request_configuration=config)
            items = self._extract_collection(response)
            
            # Normalize: rename 'id' to 'page_id' for consistency
            for item in items:
                if isinstance(item, dict) and "id" in item and "page_id" not in item:
                    item["page_id"] = item["id"]
            
            logger.info(f"✅ Retrieved {len(items)} pages from site")
            return True, json.dumps({
                "pages": items,
                "results": items,
                "value": items,
                "count": len(items),
            })
            
        except Exception as e:
            error_str = str(e).lower()
            # 404 means site has no pages or pages API not available - return empty list
            if "404" in error_str or "not found" in error_str:
                logger.info(f"ℹ️ Site {site_id} has no pages or pages API not available")
                return True, json.dumps({
                    "pages": [],
                    "results": [],
                    "value": [],
                    "count": 0,
                    "note": "No pages found for this site",
                })
            return self._handle_error(e, "get pages")

    @tool(
        app_name="sharepoint",
        tool_name="get_page",
        description="Get full HTML content and metadata of a SharePoint page for reading/summarization",
        args_schema=GetPageInput,
        when_to_use=[
            "After search_pages returns results with page_id, use this to get full HTML content",
            "User wants to read/summarize a page AND you have page_id from search_pages",
            "Need full page HTML content before updating (call before update_page)",
            "ALWAYS use page_id directly from search_pages results - skip get_pages",
        ],
        when_not_to_use=[
            "User mentions a page by name but no page_id yet — call search_pages first",
            "User wants to list pages in a site (use get_pages)",
            "User wants to find pages by keyword (use search_pages)",
            "No SharePoint mention",
        ],
        primary_intent=ToolIntent.SEARCH,
        typical_queries=[
            "Summarize page workflow: search_pages(query='KT') → get_page(site_id=results[0].site_id, page_id=results[0].page_id)",
            "Read page workflow: search_pages → get_page (use page_id from search results)",
        ],
        category=ToolCategory.DOCUMENTATION,
    )
    async def get_page(
        self,
        site_id: str,
        page_id: str,
    ) -> tuple[bool, str]:
        """Get a single SharePoint modern page by its ID with full HTML content.

        Returns the page with content_html extracted from canvasLayout webparts.
        The content_html field contains the actual page content ready for reading/summarization.
        """
        try:
            logger.info(f"📍 Getting page {page_id} from site {site_id}")
            response = await self.client.get_site_page_with_canvas(site_id=site_id, page_id=page_id)
            if not response.success:
                return False, json.dumps({"error": response.error or "Failed to get page"})
            page_data = self._serialize_response(response.data)

            if not page_data or page_data == str(response):
                return False, json.dumps({"error": "Page not found or could not be serialized"})

            # Extract HTML content from webparts for easy consumption
            html_content = ""
            if isinstance(page_data, dict):
                html_content = self._extract_page_html_content(page_data)

            logger.info(f"✅ Retrieved page {page_id} with {len(html_content)} chars of content")
            return True, json.dumps({
                "page_id": page_data.get("id") if isinstance(page_data, dict) else page_id,
                "title": page_data.get("title") if isinstance(page_data, dict) else None,
                "content_html": html_content,
                "web_url": (
                    page_data.get("webUrl") or page_data.get("web_url")
                    if isinstance(page_data, dict) else None
                ),
                "created": page_data.get("createdDateTime") if isinstance(page_data, dict) else None,
                "last_modified": page_data.get("lastModifiedDateTime") if isinstance(page_data, dict) else None,
                "full_page_data": page_data,
            })

        except Exception as e:
            return self._handle_error(e, f"get page {page_id}")

    @tool(
        app_name="sharepoint",
        tool_name="search_pages",
        description="Search SharePoint pages by keyword across ALL sites — no site ID needed",
        args_schema=SearchPagesInput,
        when_to_use=[
            "User mentions ANY page by name/keyword without providing page_id",
            "User says 'find the KT page', 'show me the deployment guide', 'search for pages about X'",
            "User asks 'summarize the X page', 'read the Y page', 'show me the Z page'",
            "CRITICAL: This is the ONLY tool needed to find pages - returns page_id directly",
            "After this returns results, go DIRECTLY to get_page - skip get_pages entirely",
        ],
        when_not_to_use=[
            "User already has page_id (use get_page directly)",
            "User wants to browse ALL pages in a known site (use get_pages)",
            "User wants site information (use get_sites or get_site)",
            "No SharePoint mention",
        ],
        primary_intent=ToolIntent.SEARCH,
        typical_queries=[
            "Find the KT page → query='KT'",
            "Show me the pipeshub KT page → query='pipeshub KT'",
            "Summarize the KT page → STEP 1: search_pages(query='KT'), STEP 2: get_page(site_id=result.pages[0].site_id, page_id=result.pages[0].page_id)",
            "Read the deployment guide → STEP 1: search_pages(query='deployment guide'), STEP 2: get_page with page_id from results",
            "What does the onboarding page say? → STEP 1: search_pages(query='onboarding'), STEP 2: get_page",
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

        Uses the Microsoft Graph Search API with EntityType.ListItem filtered
        to modern site pages — this searches ALL sites the user has access to without
        needing a site ID.

        Returns page_id, site_id, title, web_url, and basic metadata for each matching page.
        The page_id can be used DIRECTLY with get_page(site_id, page_id) to fetch full HTML content.
        DO NOT call get_pages after this - you already have the page_id.
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
                        "Each result includes page_id, site_id, title, web_url, and basic metadata. "
                        "To get the FULL page HTML content for summarization or detailed reading, "
                        "call get_page(site_id, page_id) using the page_id and site_id from these results. "
                        "DO NOT call get_pages after search_pages - the page_id is already here."
                    ),
                })
            else:
                return False, json.dumps({"error": response.error or "Failed to search pages"})
        except Exception as e:
            return self._handle_error(e, f"search pages '{query}'")

    # ------------------------------------------------------------------
    # Document / File tools
    # ------------------------------------------------------------------

    @tool(
        app_name="sharepoint",
        tool_name="list_drives",
        description="List document libraries (drives) in a SharePoint site",
        args_schema=ListDrivesInput,
        when_to_use=[
            "User explicitly asks what document libraries exist in a site",
            "User asks 'what document libraries are in this site?', 'show me the drives', 'what libraries does this site have?'",
            "User wants to pick a specific library to browse by name",
        ],
        when_not_to_use=[
            "User wants ALL documents/files in a site → use list_files(site_id) WITHOUT drive_id directly",
            "User wants to find a specific file by keyword → use search_files",
            "User wants pages not documents → use get_pages",
            "No SharePoint mention",
            "NEVER call list_drives just to then pass drives[0].id to list_files — list_files handles all drives internally when drive_id is omitted",
        ],
        primary_intent=ToolIntent.SEARCH,
        typical_queries=[
            "What document libraries are in this site? → list_drives(site_id=...)",
            "Show me the drives in this site → list_drives(site_id=...)",
            "NOTE: 'all documents in site X' → use list_files(site_id=...) directly, NOT list_drives first",
        ],
        category=ToolCategory.DOCUMENTATION,
    )
    async def list_drives(
        self,
        site_id: str,
        top: Optional[int] = 10,
    ) -> tuple[bool, str]:
        """List document libraries (drives) in a SharePoint site.

        Returns each drive's id, name, driveType, webUrl and quota (default 10, max 50).
        The drive 'id' is required for list_files, get_file_metadata, and get_file_content.
        """
        try:
            response = await self.client.list_drives_for_site(
                site_id=site_id,
                top=min(top or 10, 50),
            )
            if response.success:
                data = response.data or {}
                drives = data.get("drives") or []
                # Normalise field names for agent consumption
                normalized = []
                for d in drives:
                    if not isinstance(d, dict):
                        continue
                    normalized.append({
                        "id": d.get("id"),
                        "name": d.get("name"),
                        "drive_type": d.get("driveType") or d.get("drive_type"),
                        "web_url": d.get("webUrl") or d.get("web_url"),
                        "description": d.get("description"),
                        "created": d.get("createdDateTime") or d.get("created_date_time"),
                        "last_modified": d.get("lastModifiedDateTime") or d.get("last_modified_date_time"),
                        "quota": d.get("quota"),
                    })
                logger.info(f"✅ list_drives: {len(normalized)} drives for site {site_id}")
                return True, json.dumps({
                    "drives": normalized,
                    "results": normalized,
                    "count": len(normalized),
                    "usage_hint": (
                        "Use the 'id' field of a drive as drive_id in list_files(site_id, drive_id) "
                        "to browse files inside that document library."
                    ),
                })
            else:
                error = response.error or "Failed to list drives"
                if any(k in error for k in ("404", "itemNotFound", "not found", "could not be found")):
                    logger.info(f"ℹ️ list_drives: site {site_id} not accessible via drives API (404) — returning empty")
                    return True, json.dumps({
                        "drives": [],
                        "results": [],
                        "count": 0,
                        "note": "This site is not accessible via the drives API (it may be a hub site, archived, or a subsite with a different URL structure).",
                    })
                return False, json.dumps({"error": error})
        except Exception as e:
            error_msg = str(e)
            if any(k in error_msg for k in ("404", "itemNotFound", "not found", "could not be found")):
                logger.info(f"ℹ️ list_drives: site {site_id} not accessible (404 exception) — returning empty")
                return True, json.dumps({
                    "drives": [],
                    "results": [],
                    "count": 0,
                    "note": "This site is not accessible via the drives API (it may be a hub site, archived, or a subsite).",
                })
            return self._handle_error(e, f"list drives for site {site_id}")

    @tool(
        app_name="sharepoint",
        tool_name="list_files",
        description="List files from ALL document libraries in a site, or from a specific library/sub-folder",
        args_schema=ListFilesInput,
        when_to_use=[
            "User says 'all documents in [site]', 'list all files in this site', 'show me all documents' → omit drive_id",
            "User wants to browse ALL files across every document library in a site → omit drive_id",
            "User wants to browse a specific known document library → provide drive_id",
            "User wants to navigate into a sub-folder → provide both drive_id and folder_id",
            "ONLY use when user wants to browse/list files — use search_files to find a file by keyword",
        ],
        when_not_to_use=[
            "User wants to find a file by name/keyword — use search_files instead",
            "User wants pages (use get_pages or search_pages)",
            "No SharePoint mention",
        ],
        primary_intent=ToolIntent.SEARCH,
        typical_queries=[
            "All documents in testing site → list_files(site_id=...) with NO drive_id",
            "List all files in this site → list_files(site_id=...) with NO drive_id",
            "Show me every document → list_files(site_id=...) with NO drive_id",
            "List files in the Documents library → list_files(site_id, drive_id=...)",
            "Show contents of this folder → list_files(site_id, drive_id=..., folder_id=...)",
        ],
        category=ToolCategory.DOCUMENTATION,
    )
    async def list_files(
        self,
        site_id: str,
        drive_id: Optional[str] = None,
        folder_id: Optional[str] = None,
        top: Optional[int] = 10,
    ) -> tuple[bool, str]:
        """List files and folders from SharePoint document libraries.

        - drive_id omitted: iterates ALL document libraries in the site and merges results (default 10, max 50).
          Use this when user says 'all documents', 'all files', 'list documents in site X'.
        - drive_id provided + folder_id omitted: lists root of that specific library.
        - drive_id + folder_id provided: lists contents of that specific sub-folder.

        Each item includes drive_name so you can tell which library it came from.
        Use item['id'] + item['drive_id'] with get_file_content to read a file.
        """
        try:
            capped_top = min(top or 10, 50)

            # ── All drives mode: no drive_id provided ──────────────────────
            if not drive_id:
                response = await self.client.list_all_drives_children(
                    site_id=site_id,
                    top_per_drive=capped_top,
                )
                if response.success:
                    data = response.data or {}
                    raw_items = data.get("items") or []
                    drives_searched = data.get("drives_searched", 0)
                    drive_errors = data.get("drive_errors") or []

                    normalized = []
                    for item in raw_items:
                        if not isinstance(item, dict):
                            continue
                        parent_ref = item.get("parentReference") or {}
                        file_facet = item.get("file") or {}
                        folder_facet = item.get("folder") or {}
                        is_folder = bool(folder_facet) or item.get("isFolder", False)
                        normalized.append({
                            "id": item.get("id"),
                            "name": item.get("name"),
                            "is_folder": is_folder,
                            "size_bytes": item.get("size"),
                            "mime_type": file_facet.get("mimeType") if isinstance(file_facet, dict) else None,
                            "web_url": item.get("webUrl") or item.get("web_url"),
                            "created": item.get("createdDateTime") or item.get("created_date_time"),
                            "last_modified": item.get("lastModifiedDateTime") or item.get("last_modified_date_time"),
                            "drive_id": parent_ref.get("driveId") or item.get("drive_id"),
                            "drive_name": item.get("drive_name"),
                            "parent_id": parent_ref.get("id"),
                            "site_id": parent_ref.get("siteId") or site_id,
                            "child_count": folder_facet.get("childCount") if isinstance(folder_facet, dict) else None,
                        })

                    logger.info(
                        f"✅ list_files (all drives): {len(normalized)} items across "
                        f"{drives_searched} drives for site {site_id}"
                    )
                    result: Dict[str, Any] = {
                        "items": normalized,
                        "files": [i for i in normalized if not i["is_folder"]],
                        "folders": [i for i in normalized if i["is_folder"]],
                        "count": len(normalized),
                        "drives_searched": drives_searched,
                        "usage_hint": (
                            "Results are from ALL document libraries. "
                            "Each item has 'drive_name' to show which library it came from. "
                            "Call get_file_content(site_id, drive_id=item['drive_id'], item_id=item['id']) to read a file. "
                            "To navigate into a folder call list_files(site_id, drive_id=item['drive_id'], folder_id=item['id'])."
                        ),
                    }
                    if drive_errors:
                        result["drive_errors"] = drive_errors
                    return True, json.dumps(result)
                else:
                    return False, json.dumps({"error": response.error or "Failed to list files"})

            # ── Single drive mode: drive_id provided ───────────────────────
            response = await self.client.list_drive_children(
                site_id=site_id,
                drive_id=drive_id,
                folder_id=folder_id,
                top=capped_top,
            )
            if response.success:
                data = response.data or {}
                items = data.get("items") or []
                normalized = []
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    parent_ref = item.get("parentReference") or {}
                    file_facet = item.get("file") or {}
                    folder_facet = item.get("folder") or {}
                    is_folder = bool(folder_facet) or item.get("isFolder", False)
                    normalized.append({
                        "id": item.get("id"),
                        "name": item.get("name"),
                        "is_folder": is_folder,
                        "size_bytes": item.get("size"),
                        "mime_type": file_facet.get("mimeType") if isinstance(file_facet, dict) else None,
                        "web_url": item.get("webUrl") or item.get("web_url"),
                        "created": item.get("createdDateTime") or item.get("created_date_time"),
                        "last_modified": item.get("lastModifiedDateTime") or item.get("last_modified_date_time"),
                        "drive_id": parent_ref.get("driveId") or drive_id,
                        "parent_id": parent_ref.get("id"),
                        "site_id": parent_ref.get("siteId") or site_id,
                        "child_count": folder_facet.get("childCount") if isinstance(folder_facet, dict) else None,
                    })
                logger.info(f"✅ list_files: {len(normalized)} items (drive={drive_id}, folder={folder_id})")
                return True, json.dumps({
                    "items": normalized,
                    "files": [i for i in normalized if not i["is_folder"]],
                    "folders": [i for i in normalized if i["is_folder"]],
                    "count": len(normalized),
                    "drive_id": drive_id,
                    "folder_id": folder_id,
                    "usage_hint": (
                        "To read a file's content call get_file_content(site_id, drive_id, item_id=item['id']). "
                        "To navigate into a folder call list_files(site_id, drive_id, folder_id=item['id'])."
                    ),
                })
            else:
                return False, json.dumps({"error": response.error or "Failed to list files"})
        except Exception as e:
            return self._handle_error(e, f"list files (site={site_id}, drive={drive_id})")

    @tool(
        app_name="sharepoint",
        tool_name="search_files",
        description="Find or retrieve a specific SharePoint file by name or keyword — use for ANY named file or document request regardless of verb",
        args_schema=SearchFilesInput,
        when_to_use=[
            "CRITICAL: Use whenever user mentions a specific file or document by name — regardless of verb",
            "User says 'give me X file', 'give X document', 'give assignment file', 'give budget doc'",
            "User says 'show me X file', 'show the report', 'show project plan'",
            "User says 'get X file', 'get the KT document', 'get onboarding file'",
            "User says 'open X', 'read X file', 'what is in X file', 'summarize X document'",
            "User says 'find X', 'search for X', 'look for X file'",
            "ANY time user refers to a specific named document without knowing where it is",
            "User mentions a file name or document keyword — assignment, budget, report, KT, onboarding, etc.",
            "FIRST tool to call when user wants a specific file — locate it, then use get_file_content to read it",
        ],
        when_not_to_use=[
            "User wants ALL files/documents in a site or library with no specific name → use list_files",
            "User wants to browse a library without a specific file in mind → use list_files",
            "User wants SharePoint pages (not files/documents) → use search_pages",
            "No SharePoint mention",
        ],
        primary_intent=ToolIntent.SEARCH,
        typical_queries=[
            "Give me the assignment file → search_files(query='assignment')",
            "Give the budget document → search_files(query='budget')",
            "Show me the KT file → search_files(query='KT')",
            "Get the onboarding document → search_files(query='onboarding')",
            "Open the project plan → search_files(query='project plan')",
            "What is in the deployment guide → search_files(query='deployment guide')",
            "Find the Q1 report → search_files(query='Q1 report')",
            "Search for budget spreadsheet → search_files(query='budget spreadsheet')",
            "Read the project plan → STEP 1: search_files(query='project plan'), STEP 2: get_file_content",
            "Summarize the onboarding doc → STEP 1: search_files(query='onboarding'), STEP 2: get_file_content",
        ],
        category=ToolCategory.DOCUMENTATION,
    )
    async def search_files(
        self,
        query: str,
        site_id: Optional[str] = None,
        top: Optional[int] = 10,
        skip: Optional[int] = None,
    ) -> tuple[bool, str]:
        """Search for SharePoint files by keyword across all accessible document libraries.

        Uses the Microsoft Graph Search API (EntityType.DriveItem) to search ALL
        document libraries the user has access to — no site or drive ID needed (default 10, max 50).

        Returns each file's id, name, mimeType, size, webUrl, drive_id (parentReference.driveId),
        and site_id (parentReference.siteId).
        Use id + drive_id + site_id directly with get_file_content to read the file.
        """
        try:
            response = await self.client.search_files_with_search_api(
                query=query,
                site_id=site_id,
                top=min(top or 10, 50),
                from_index=skip or 0,
            )
            if response.success:
                data = response.data or {}
                files = data.get("files") or []
                page_size = min(top or 10, 50)

                # Normalise field names
                normalized = []
                for f in files:
                    if not isinstance(f, dict):
                        continue
                    parent_ref = f.get("parentReference") or {}
                    drive_id_val = parent_ref.get("driveId")
                    site_id_val = parent_ref.get("siteId")
                    item_id_val = f.get("id")
                    normalized.append({
                        # Primary fields (snake_case)
                        "id": item_id_val,
                        "name": f.get("name"),
                        "is_folder": f.get("isFolder", False),
                        "mime_type": f.get("mimeType"),
                        "size_bytes": f.get("size"),
                        "web_url": f.get("webUrl") or f.get("web_url"),
                        "created": f.get("createdDateTime"),
                        "last_modified": f.get("lastModifiedDateTime"),
                        "drive_id": drive_id_val,
                        "site_id": site_id_val,
                        "parent_item_id": parent_ref.get("id"),
                        "parent_path": parent_ref.get("path"),
                        # camelCase aliases — planner sometimes generates these
                        "driveId": drive_id_val,
                        "siteId": site_id_val,
                        "itemId": item_id_val,
                        # Nested parentReference alias — planner sometimes uses
                        # results[0].parentReference.driveId path
                        "parentReference": {
                            "driveId": drive_id_val,
                            "siteId": site_id_val,
                            "id": parent_ref.get("id"),
                            "path": parent_ref.get("path"),
                        },
                    })

                logger.info(f"✅ search_files: {len(normalized)} files for query={query!r}")
                return True, json.dumps({
                    "files": normalized,
                    "results": normalized,
                    "count": len(normalized),
                    "has_more": len(normalized) == page_size,
                    "next_skip": (skip or 0) + len(normalized),
                    "pagination_hint": (
                        f"To get the next page use skip={((skip or 0) + len(normalized))} with top={page_size}"
                        if len(normalized) == page_size else "All available results returned"
                    ),
                    "usage_hint": (
                        "FIELD NAMES in each result (use these exact paths for next tool calls): "
                        "  site_id  = results[0].site_id  (also aliased as results[0].siteId) "
                        "  drive_id = results[0].drive_id  (also aliased as results[0].driveId and results[0].parentReference.driveId) "
                        "  item_id  = results[0].id "
                        "To read file content: get_file_content(site_id=results[0].site_id, drive_id=results[0].drive_id, item_id=results[0].id). "
                        "To get file details: get_file_metadata(site_id=results[0].site_id, drive_id=results[0].drive_id, item_id=results[0].id)."
                    ),
                })
            else:
                return False, json.dumps({"error": response.error or "Failed to search files"})
        except Exception as e:
            return self._handle_error(e, f"search files '{query}'")

    @tool(
        app_name="sharepoint",
        tool_name="get_file_metadata",
        description="Get detailed metadata for a specific SharePoint file or folder",
        args_schema=GetFileMetadataInput,
        when_to_use=[
            "User wants metadata of a specific file (size, type, dates, URL)",
            "Before downloading a file: check mimeType to decide if content is readable as text",
            "After search_files or list_files: user wants more details about a specific item",
        ],
        when_not_to_use=[
            "User wants to read the actual file content (use get_file_content)",
            "User wants to find a file by name (use search_files first)",
            "User does not have item_id yet (use search_files or list_files first)",
            "No SharePoint mention",
        ],
        primary_intent=ToolIntent.SEARCH,
        typical_queries=[
            "What are the details of this file? → get_file_metadata(site_id, drive_id, item_id)",
            "How big is this file?",
            "When was this file last modified?",
        ],
        category=ToolCategory.DOCUMENTATION,
    )
    async def get_file_metadata(
        self,
        site_id: str,
        drive_id: str,
        item_id: str,
    ) -> tuple[bool, str]:
        """Get detailed metadata for a specific SharePoint file or folder.

        Returns id, name, size, mimeType, webUrl, createdDateTime, lastModifiedDateTime,
        and parentReference (driveId, siteId, path).
        """
        try:
            response = await self.client.get_drive_item_metadata(
                site_id=site_id,
                drive_id=drive_id,
                item_id=item_id,
            )
            if response.success:
                raw = response.data or {}
                # Normalise
                parent_ref = raw.get("parentReference") or {}
                file_facet = raw.get("file") or {}
                folder_facet = raw.get("folder") or {}
                is_folder = bool(folder_facet)

                result = {
                    "id": raw.get("id"),
                    "name": raw.get("name"),
                    "is_folder": is_folder,
                    "size_bytes": raw.get("size"),
                    "mime_type": file_facet.get("mimeType") if isinstance(file_facet, dict) else None,
                    "web_url": raw.get("webUrl") or raw.get("web_url"),
                    "created": raw.get("createdDateTime") or raw.get("created_date_time"),
                    "last_modified": raw.get("lastModifiedDateTime") or raw.get("last_modified_date_time"),
                    "drive_id": parent_ref.get("driveId") or drive_id,
                    "site_id": parent_ref.get("siteId") or site_id,
                    "parent_item_id": parent_ref.get("id"),
                    "parent_path": parent_ref.get("path"),
                    "child_count": (
                        folder_facet.get("childCount") if isinstance(folder_facet, dict) else None
                    ),
                    "etag": raw.get("eTag") or raw.get("e_tag"),
                }
                is_text_readable = result["mime_type"] and (
                    result["mime_type"].startswith("text/")
                    or result["mime_type"] in (
                        "application/json", "application/xml"
                    )
                )
                result["content_readable_as_text"] = bool(is_text_readable)
                return True, json.dumps(result)
            else:
                return False, json.dumps({"error": response.error or "File not found"})
        except Exception as e:
            return self._handle_error(e, f"get file metadata {item_id}")

    @tool(
        app_name="sharepoint",
        tool_name="get_file_content",
        description="Download and read the text content of a SharePoint file. Supports Office documents (.docx, .xlsx, .pptx) by converting them to readable HTML automatically.",
        args_schema=GetFileContentInput,
        when_to_use=[
            "User wants to read, summarize, or analyze the content of a SharePoint file",
            "After search_files returns a file, use this to read its content",
            "User says 'read the file', 'summarize this document', 'show me the content'",
            "Works with text files (.txt, .csv, .md, .html, .json, .xml) AND Office documents (.docx, .xlsx, .pptx)",
        ],
        when_not_to_use=[
            "User does not have item_id yet — call search_files or list_files first",
            "User wants file metadata only (use get_file_metadata)",
            "User wants SharePoint pages (use get_page)",
            "No SharePoint mention",
        ],
        primary_intent=ToolIntent.SEARCH,
        typical_queries=[
            "Read the project plan file → STEP 1: search_files(query='project plan'), STEP 2: get_file_content",
            "Summarize the onboarding checklist → search_files → get_file_content",
            "Show me the content of this Word document",
            "What's in the jira_logs.docx file?",
        ],
        category=ToolCategory.DOCUMENTATION,
    )
    async def get_file_content(
        self,
        site_id: str,
        drive_id: str,
        item_id: str,
    ) -> tuple[bool, str]:
        """Download and read the content of a SharePoint file.

        Office documents (.docx, .xlsx, .pptx) are automatically converted to
        HTML via Microsoft Graph's ?format=html so the LLM can read and
        summarise them.  Plain-text files are returned as-is.
        """
        try:
            # Fetch metadata first to get the mime_type and file name so we can
            # decide whether to request the HTML-converted version.
            mime_type: Optional[str] = None
            file_name: Optional[str] = None
            try:
                meta_response = await self.client.get_drive_item_metadata(
                    site_id=site_id,
                    drive_id=drive_id,
                    item_id=item_id,
                )
                if meta_response.success and meta_response.data:
                    raw_meta = meta_response.data
                    file_facet = raw_meta.get("file") or {}
                    if isinstance(file_facet, dict):
                        mime_type = file_facet.get("mimeType")
                    file_name = raw_meta.get("name")
            except Exception:
                # Metadata fetch is best-effort; proceed without it
                pass

            response = await self.client.get_drive_item_content(
                site_id=site_id,
                drive_id=drive_id,
                item_id=item_id,
                mime_type=mime_type,
                file_name=file_name,
            )
            if response.success:
                data = response.data or {}
                if "content" in data:
                    content = data["content"]
                    size = data.get("size_bytes", 0)
                    truncated = data.get("truncated", False)
                    fmt = data.get("format", "text")
                    return True, json.dumps({
                        "content": content,
                        "format": fmt,
                        "encoding": data.get("encoding", "utf-8"),
                        "file_name": file_name,
                        "mime_type": mime_type,
                        "size_bytes": size,
                        "truncated": truncated,
                        "truncation_note": (
                            "Content was truncated to 512 KB. The file may have more content."
                            if truncated else None
                        ),
                    })
                elif "content_base64" in data:
                    return True, json.dumps({
                        "content_base64": data["content_base64"],
                        "encoding": "base64",
                        "file_name": file_name,
                        "mime_type": mime_type,
                        "size_bytes": data.get("size_bytes", 0),
                        "truncated": data.get("truncated", False),
                        "note": (
                            "This is a binary file that could not be converted to text. "
                            "Advise the user to open the file via its web_url in SharePoint."
                        ),
                    })
                else:
                    return True, json.dumps({"content": "", "size_bytes": 0, "note": "Empty file"})
            else:
                return False, json.dumps({"error": response.error or "Failed to read file content"})
        except Exception as e:
            return self._handle_error(e, f"get file content {item_id}")

    # ------------------------------------------------------------------
    # Page write tools (create / update)
    # ------------------------------------------------------------------

    @tool(
        app_name="sharepoint",
        tool_name="create_page",
        description="Create a new modern page in a SharePoint site. Default: draft (publish=False). Only pass publish=True when the user explicitly asks to publish.",
        args_schema=CreatePageInput,
        when_to_use=[
            "User wants to create a new SharePoint page",
            "User says 'create a page in SharePoint' or 'add a page to this site'",
            "User wants to write/author a page with content",
            "If user did NOT say anything about publishing → use publish=False (draft)",
            "If user explicitly says 'publish', 'make it live', 'go public' → use publish=True",
        ],
        when_not_to_use=[
            "User wants to update an existing page (use update_page)",
            "User wants to read/list pages (use get_pages)",
            "User wants to create a list item, not a page",
            "No SharePoint mention",
        ],
        primary_intent=ToolIntent.ACTION,
        typical_queries=[
            "Create a SharePoint page called 'Project Overview' (draft by default)",
            "Add a new page to this site with the meeting notes",
            "Create a page in SharePoint with this content and publish it (use publish=True)",
            "Write a new site page about the deployment process",
        ],
        category=ToolCategory.DOCUMENTATION,
    )
    async def create_page(
        self,
        site_id: str,
        title: str,
        content_html: str,
        publish: Optional[bool] = False,
    ) -> tuple[bool, str]:
        """Create a new modern page in a SharePoint site.

        Uses the Microsoft Graph API to create a SitePage with a single
        full-width text web part containing the provided HTML content.
        Default is draft (publish=False). If the user has not said anything
        about publishing, do not pass publish=True — keep draft.

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
                        "https://graph.microsoft.com/v1.0/sites/{site_id}"
                        "/pages/{page_id}/microsoft.graph.sitePage/publish"
                    )
                    ri.path_parameters = {
                        "site_id": site_id,
                        "page_id": page_id,
                    }
                    ri.content = b"{}"
                    ri.headers.try_add("Content-Type", "application/json")
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
                "page_id": page_id,
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
        description="Update the title and/or content of an existing SharePoint site page. Default: draft (publish=False). Only pass publish=True when the user explicitly asks to publish.",
        args_schema=UpdatePageInput,
        when_to_use=[
            "User wants to edit or update an existing SharePoint page",
            "User says 'update the page', 'edit this page', 'change the content of'",
            "User wants to rename a SharePoint page",
            "User wants to rewrite or append content to an existing page",
            "If user did NOT say anything about publishing → use publish=False (draft)",
            "If user explicitly says 'publish', 'make it live', 'go public' → use publish=True",
        ],
        when_not_to_use=[
            "User wants to create a brand-new page (use create_page)",
            "User wants to read/list pages (use get_pages)",
            "User does not have the page ID — call get_pages first to find it",
            "No SharePoint mention",
        ],
        primary_intent=ToolIntent.ACTION,
        typical_queries=[
            "Update the 'Project Overview' SharePoint page with new content (draft by default)",
            "Edit the page and add a new section about deployment",
            "Rename the SharePoint page to 'Q1 Planning' and publish (use publish=True)",
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
        publish: Optional[bool] = False,
    ) -> tuple[bool, str]:
        """Update the title and/or content of an existing SharePoint modern page.

        At least one of title or content_html must be provided.
        Default is draft (publish=False). If the user has not said anything
        about publishing, do not pass publish=True — keep draft.

        To merge content with the existing page, call get_page first to
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
            # Compound site_id must be URL-encoded so the path is not mis-parsed (404).
            encoded_site_id = quote(site_id, safe="")
            logger.info(f"📍 Updating page {page_id} in site {site_id}")
            patch_ri = RequestInformation()
            patch_ri.http_method = Method.PATCH
            patch_ri.url_template = (
                f"https://graph.microsoft.com/v1.0/sites/{encoded_site_id}"
                f"/pages/{page_id}/microsoft.graph.sitePage"
            )
            patch_ri.path_parameters = {}
            patch_ri.content = json.dumps(patch_data).encode("utf-8")
            patch_ri.headers.try_add("Content-Type", "application/json")
            await graph.request_adapter.send_no_response_content_async(patch_ri, {})

            page_data: Dict[str, Any] = {"page_id": page_id}

            published = False
            publish_error = None
            if publish:
                try:
                    pub_ri = RequestInformation()
                    pub_ri.http_method = Method.POST
                    pub_ri.url_template = (
                        f"https://graph.microsoft.com/v1.0/sites/{encoded_site_id}"
                        f"/pages/{page_id}/microsoft.graph.sitePage/publish"
                    )
                    pub_ri.path_parameters = {}
                    pub_ri.content = b"{}"
                    pub_ri.headers.try_add("Content-Type", "application/json")
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
                "page_id": page_id,
                **({"title": title} if title else {}),
                "published": published,
                "web_url": web_url,
                **({"publish_error": publish_error} if publish_error else {}),
            })

        except Exception as e:
            return self._handle_error(e, f"update page {page_id}")

    # ------------------------------------------------------------------
    # Drive item write tools (create folder / file / notebook)
    # ------------------------------------------------------------------

    @tool(
        app_name="sharepoint",
        tool_name="create_folder",
        description="Create a new folder in a SharePoint document library",
        args_schema=CreateFolderInput,
        when_to_use=[
            "User wants to create a new folder in SharePoint",
            "User says 'create a folder', 'make a new folder', 'add a folder in SharePoint'",
            "User wants to organise files into a new directory within a document library",
            "User specifies a drive (document library) and optionally a parent folder to create inside",
        ],
        when_not_to_use=[
            "User wants to create a file, not a folder — use create_word_document instead",
            "User wants to list existing folders — use list_files instead",
            "No SharePoint mention",
        ],
        primary_intent=ToolIntent.ACTION,
        typical_queries=[
            "Create a folder called 'Project Docs' in the Documents library → create_folder(site_id, drive_id, folder_name='Project Docs')",
            "Add a subfolder 'Q1 Reports' inside the 'Finance' folder → create_folder(site_id, drive_id, folder_name='Q1 Reports', parent_folder_id=...)",
            "Make a new folder in SharePoint → create_folder(site_id, drive_id, folder_name=...)",
        ],
        category=ToolCategory.DOCUMENTATION,
    )
    async def create_folder(
        self,
        site_id: str,
        drive_id: str,
        folder_name: str,
        parent_folder_id: Optional[str] = None,
    ) -> tuple[bool, str]:
        """Create a new folder in a SharePoint document library.

        - parent_folder_id omitted: creates in the root of the document library.
        - parent_folder_id provided: creates inside that specific subfolder.

        If a folder with the same name already exists a unique name is generated
        automatically (@microsoft.graph.conflictBehavior = 'rename').

        Workflow:
          1. Call list_drives(site_id) to get drive_id.
          2. Optionally call list_files(site_id, drive_id) to find parent_folder_id.
          3. Call create_folder(site_id, drive_id, folder_name, parent_folder_id).
        """
        try:
            response = await self.client.create_folder(
                drive_id=drive_id,
                name=folder_name,
                parent_folder_id=parent_folder_id,
            )
            if response.success:
                data = response.data or {}
                logger.info(f"✅ create_folder tool: '{folder_name}' created in drive {drive_id}")
                return True, json.dumps({
                    "message": f"Folder '{folder_name}' created successfully",
                    "folder_id": data.get("id"),
                    "name": data.get("name"),
                    "web_url": data.get("webUrl") or data.get("web_url"),
                    "drive_id": drive_id,
                    "site_id": site_id,
                    "parent_folder_id": parent_folder_id,
                    "created": data.get("createdDateTime") or data.get("created_date_time"),
                    "usage_hint": (
                        "Use 'folder_id' as 'folder_id' in list_files to browse the new folder, "
                        "or as 'parent_folder_id' in create_folder / create_word_document to create inside it."
                    ),
                })
            else:
                return False, json.dumps({"error": response.error or "Failed to create folder"})
        except Exception as e:
            return self._handle_error(e, f"create folder '{folder_name}'")

    @tool(
        app_name="sharepoint",
        tool_name="create_word_document",
        description="Create a new Word document (.docx) in a SharePoint document library with optional content",
        args_schema=CreateWordDocumentInput,
        when_to_use=[
            "User wants to create a Word document in SharePoint",
            "User says 'create a Word file', 'make a .docx', 'add a document to SharePoint'",
            "User wants to write content into a new Word document in SharePoint",
            "User specifies a drive and optionally a subfolder location for the file",
        ],
        when_not_to_use=[
            "User wants to create a folder — use create_folder instead",
            "User wants to create a OneNote notebook — use create_onenote_notebook instead",
            "User wants to create a SharePoint page (not a file) — use create_page instead",
            "No SharePoint mention",
        ],
        primary_intent=ToolIntent.ACTION,
        typical_queries=[
            "Create a Word document called 'Project Plan' in the Documents library → create_word_document(site_id, drive_id, file_name='Project Plan')",
            "Create a .docx with the meeting notes → create_word_document(site_id, drive_id, file_name='Meeting Notes', content_text='...')",
            "Add a Word file inside the 'Finance' folder → create_word_document(site_id, drive_id, file_name=..., parent_folder_id=...)",
        ],
        category=ToolCategory.DOCUMENTATION,
    )
    async def create_word_document(
        self,
        site_id: str,
        drive_id: str,
        file_name: str,
        parent_folder_id: Optional[str] = None,
        content_text: Optional[str] = None,
    ) -> tuple[bool, str]:
        """Create a new Word document (.docx) in a SharePoint document library.

        Builds a minimal valid .docx in memory and uploads it.
        Optional plain-text content is inserted as paragraphs in the document body.
        The .docx extension is appended automatically — do not include it in file_name.

        Workflow:
          1. Call list_drives(site_id) to get drive_id.
          2. Optionally call list_files(site_id, drive_id) to find a parent_folder_id.
          3. Call create_word_document(site_id, drive_id, file_name, ...).
        """
        try:
            response = await self.client.create_word_document(
                drive_id=drive_id,
                name=file_name,
                parent_folder_id=parent_folder_id,
                content_text=content_text,
            )
            if response.success:
                data = response.data or {}
                actual_name = data.get("name") or (
                    file_name if file_name.lower().endswith(".docx") else f"{file_name}.docx"
                )
                logger.info(f"✅ create_word_document tool: '{actual_name}' created in drive {drive_id}")
                return True, json.dumps({
                    "message": f"Word document '{actual_name}' created successfully",
                    "item_id": data.get("id"),
                    "name": actual_name,
                    "web_url": data.get("webUrl") or data.get("web_url"),
                    "drive_id": drive_id,
                    "site_id": site_id,
                    "parent_folder_id": parent_folder_id,
                    "size_bytes": data.get("size"),
                    "created": data.get("createdDateTime") or data.get("created_date_time"),
                    "usage_hint": (
                        "Use 'item_id' with get_file_content(site_id, drive_id, item_id) to read the document. "
                        "Open 'web_url' in a browser to view/edit the document in Word Online."
                    ),
                })
            else:
                return False, json.dumps({"error": response.error or "Failed to create Word document"})
        except Exception as e:
            return self._handle_error(e, f"create Word document '{file_name}'")

    @tool(
        app_name="sharepoint",
        tool_name="create_onenote_notebook",
        description="Create a new OneNote notebook in a SharePoint site with optional first section and page",
        args_schema=CreateOneNoteNotebookInput,
        when_to_use=[
            "User wants to create a OneNote notebook in SharePoint",
            "User says 'create a OneNote notebook', 'make a new notebook', 'add a notebook to SharePoint'",
            "User wants to create a notebook with an initial section and/or page of content",
        ],
        when_not_to_use=[
            "User wants to create a Word document — use create_word_document instead",
            "User wants to create a SharePoint page — use create_page instead",
            "User wants to create a folder — use create_folder instead",
            "No SharePoint mention",
        ],
        primary_intent=ToolIntent.ACTION,
        typical_queries=[
            "Create a OneNote notebook called 'Team Notes' → create_onenote_notebook(site_id, notebook_name='Team Notes')",
            "Create a notebook with a 'Project' section → create_onenote_notebook(site_id, notebook_name=..., section_name='Project')",
            "Create a OneNote notebook with a first page containing meeting notes → create_onenote_notebook(site_id, notebook_name=..., section_name=..., page_title=..., page_content_html='...')",
        ],
        category=ToolCategory.DOCUMENTATION,
    )
    async def create_onenote_notebook(
        self,
        site_id: str,
        notebook_name: str,
        section_name: Optional[str] = None,
        page_title: Optional[str] = None,
        page_content_html: Optional[str] = None,
    ) -> tuple[bool, str]:
        """Create a new OneNote notebook in a SharePoint site.

        Optionally creates a first section and a first page in a single call.
        - Provide section_name to create an initial section inside the notebook.
        - Provide page_title and/or page_content_html (requires section_name) to
          create a first page with HTML content inside that section.

        Workflow:
          1. Call get_sites or get_site to obtain site_id.
          2. Call create_onenote_notebook(site_id, notebook_name, ...).
        """
        try:
            response = await self.client.create_onenote_notebook(
                site_id=site_id,
                name=notebook_name,
                section_name=section_name,
                page_title=page_title,
                page_content_html=page_content_html,
            )
            if response.success:
                data = response.data or {}
                logger.info(f"✅ create_onenote_notebook tool: '{notebook_name}' created in site {site_id}")
                result: Dict[str, Any] = {
                    "message": f"OneNote notebook '{notebook_name}' created successfully",
                    "notebook_id": data.get("notebook_id"),
                    "notebook_name": data.get("notebook_name"),
                    "notebook_web_url": data.get("notebook_web_url"),
                    "site_id": site_id,
                }
                if "section_id" in data:
                    result["section_id"] = data.get("section_id")
                    result["section_name"] = data.get("section_name")
                if "page_id" in data:
                    result["page_id"] = data.get("page_id")
                    result["page_title"] = data.get("page_title")
                    result["page_web_url"] = data.get("page_web_url")
                result["usage_hint"] = (
                    "Open 'notebook_web_url' in a browser to view the notebook in OneNote Online. "
                    "To add more sections or pages, provide section_name / page_content_html in subsequent calls."
                )
                return True, json.dumps(result)
            else:
                return False, json.dumps({"error": response.error or "Failed to create OneNote notebook"})
        except Exception as e:
            return self._handle_error(e, f"create OneNote notebook '{notebook_name}'")
