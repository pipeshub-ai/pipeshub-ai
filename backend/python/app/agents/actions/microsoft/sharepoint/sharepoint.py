import json
import logging
import re
from typing import Any, Optional

# JSON-serializable value produced by _serialize_response (avoids Any)
_JsonSerValue = dict[str, object] | list[object] | str | int | float | bool | None

from kiota_serialization_json.json_serialization_writer import (
    JsonSerializationWriter,  # type: ignore
)
from msgraph.generated.sites.item.pages.pages_request_builder import (
    PagesRequestBuilder,  # type: ignore
)
from pydantic import BaseModel, Field

from app.agents.actions.util.parse_file import FileContentParser
from app.agents.tools.config import ToolCategory
from app.agents.tools.decorator import tool
from app.agents.tools.models import ToolIntent
from app.config.constants.arangodb import Connectors, OriginTypes
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
from app.models.entities import FileRecord, RecordType
from app.modules.agents.qna.chat_state import ChatState
from app.sources.client.microsoft.microsoft import MSGraphClient
from app.sources.external.microsoft.sharepoint.sharepoint import SharePointDataSource

logger = logging.getLogger(__name__)

_MAX_FILE_CONTENT_BYTES = 50 * 1024 * 1024  # 50 MB — matches OneDrive


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------

class GetSitesInput(BaseModel):
    """List SharePoint sites accessible to the user."""
    search: Optional[str] = Field(default=None, description="KQL search query to filter sites (e.g. 'marketing', 'title:HR'). Omit for all sites.")
    top: Optional[int] = Field(default=10, description="Max sites to return (default 10, max 50).")
    skip: Optional[int] = Field(default=None, description="Sites to skip for pagination.")
    orderby: Optional[str] = Field(default=None, description="Sort: 'createdDateTime desc', 'lastModifiedDateTime desc', 'name asc'.")

class GetSiteInput(BaseModel):
    """Get a specific SharePoint site."""
    site_id: str = Field(description="SharePoint site ID (e.g. contoso.sharepoint.com,site-guid,web-guid)")

class GetPagesInput(BaseModel):
    """List pages in a SharePoint site."""
    site_id: str = Field(description="SharePoint site ID")
    top: Optional[int] = Field(default=10, description="Max pages to return (default 10, max 50)")

class GetPageInput(BaseModel):
    """Get a single SharePoint page by ID."""
    site_id: str = Field(description="SharePoint site ID (from search_pages or get_pages)")
    page_id: str = Field(description="Page ID (GUID from search_pages or get_pages)")

class SearchPagesInput(BaseModel):
    """Search SharePoint pages by keyword across all sites (no site ID needed)."""
    query: str = Field(description="Keyword or phrase from the page name/title (e.g. 'onboarding', 'deployment guide')")
    top: Optional[int] = Field(default=10, description="Max pages to return (default 10, max 50).")
    skip: Optional[int] = Field(default=None, description="Results to skip for pagination.")


# ---------------------------------------------------------------------------
# Document / File schemas
# ---------------------------------------------------------------------------

class ListDrivesInput(BaseModel):
    """List document libraries (drives) in a SharePoint site."""
    site_id: str = Field(description="SharePoint site ID (from get_sites)")
    top: Optional[int] = Field(default=10, description="Max drives to return (default 10, max 50)")

class ListFilesInput(BaseModel):
    """List files and folders in a specific SharePoint document library/folder."""
    site_id: str = Field(description="SharePoint site ID")
    drive_id: str = Field(description="Drive ID from list_drives.")
    folder_id: Optional[str] = Field(default=None, description="Folder item ID to list inside. Omit for drive root.")
    depth: Optional[int] = Field(default=1, description="Folder traversal depth. 1 lists direct children only; 2 includes one nested level.")
    top: Optional[int] = Field(default=10, description="Max items per drive (default 10, max 50)")

class SearchFilesInput(BaseModel):
    """Find a SharePoint file by name or keyword across all libraries."""
    query: str = Field(description="File name or keyword (e.g. 'assignment', 'budget')")
    site_id: Optional[str] = Field(default=None, description="Restrict to a specific site. Omit to search all sites.")
    top: Optional[int] = Field(default=10, description="Max results (default 10, max 50)")
    skip: Optional[int] = Field(default=None, description="Results to skip for pagination.")

class GetFileMetadataInput(BaseModel):
    """Get metadata for a SharePoint file or folder."""
    site_id: str = Field(description="SharePoint site ID")
    drive_id: str = Field(description="Drive ID (from list_drives or search_files parentReference.driveId)")
    item_id: str = Field(description="DriveItem ID (id from list_files or search_files)")

class GetFileContentInput(BaseModel):
    """Download and read text content of a SharePoint file (plain text and Office docs as HTML)."""
    site_id: str = Field(description="SharePoint site ID")
    drive_id: str = Field(description="Drive ID (from list_files or search_files)")
    item_id: str = Field(description="DriveItem ID (id from list_files or search_files)")

class CreatePageInput(BaseModel):
    """Create a new SharePoint modern site page (draft by default)."""
    site_id: str = Field(description="SharePoint site ID")
    title: str = Field(description="Page title (used for .aspx filename)")
    content_html: str = Field(description="Page body as HTML (e.g. <h1>, <p>, <ul>, <li>, <strong>)")
    publish: Optional[bool] = Field(default=False, description="True to publish; default False (draft).")

class UpdatePageInput(BaseModel):
    """Update an existing SharePoint site page. Provide at least title or content_html."""
    site_id: str = Field(description="SharePoint site ID")
    page_id: str = Field(description="Page ID (GUID from get_pages or search_pages)")
    title: Optional[str] = Field(default=None, description="New title (omit to keep current)")
    content_html: Optional[str] = Field(default=None, description="New HTML body (omit to keep current; replaces entire body)")
    publish: Optional[bool] = Field(default=False, description="True to publish; default False (draft).")

class CreateFolderInput(BaseModel):
    """Create a new folder in a SharePoint document library."""
    site_id: str = Field(description="SharePoint site ID")
    drive_id: str = Field(description="Drive ID (from list_drives)")
    folder_name: str = Field(description="Name of the new folder (duplicate names get auto-renamed)")
    parent_folder_id: Optional[str] = Field(default=None, description="Parent folder ID (from list_files). Omit for drive root.")

class CreateWordDocumentInput(BaseModel):
    """Create a new Word document (.docx) in a SharePoint document library."""
    site_id: str = Field(description="SharePoint site ID")
    drive_id: str = Field(description="Drive ID (from list_drives)")
    file_name: str = Field(description="Document name without .docx (e.g. 'Meeting Notes' → Meeting Notes.docx)")
    parent_folder_id: Optional[str] = Field(default=None, description="Folder ID (from list_files). Omit for drive root.")
    content_text: Optional[str] = Field(default=None, description="Optional plain-text body (newlines = paragraphs). Omit for blank doc.")

class MoveItemInput(BaseModel):
    """Move a SharePoint file or folder within the same document library."""
    site_id: str = Field(description="SharePoint site ID")
    drive_id: str = Field(description="Drive ID (from list_drives or search_files)")
    item_id: str = Field(description="DriveItem ID of the file or folder to move")
    destination_folder_id: str = Field(description="Destination folder ID (from list_files or search_files). Use 'root' for drive root.")
    new_name: Optional[str] = Field(default=None, description="Optional new name after moving. Omit to keep the current name.")

class CreateOneNoteNotebookInput(BaseModel):
    """Create a OneNote notebook in a SharePoint site; optionally add first section and page."""
    site_id: str = Field(description="SharePoint site ID")
    notebook_name: str = Field(description="Display name of the notebook")
    section_name: Optional[str] = Field(default=None, description="First section name (required if adding a page)")
    page_title: Optional[str] = Field(default=None, description="First page title (requires section_name)")
    page_content_html: Optional[str] = Field(default=None, description="First page HTML body (requires section_name)")


class FindNotebookInput(BaseModel):
    """Resolve a OneNote notebook by name in a given site. Call sharepoint.search_files first to get site_id."""
    site_id: str = Field(description="SharePoint site ID (from sharepoint.search_files results)")
    notebook_query: str = Field(description="Notebook name or keyword (e.g. 'mp_plan')")


class ListNotebookPagesInput(BaseModel):
    """List sections and pages of a OneNote notebook (no content). Use after find_notebook."""
    site_id: str = Field(description="SharePoint site ID (from find_notebook)")
    notebook_id: str = Field(description="Notebook ID (from find_notebook)")


class GetNotebookPageContentInput(BaseModel):
    """Get HTML/text content for selected OneNote pages. Use page_ids from list_notebook_pages."""
    site_id: str = Field(description="SharePoint site ID")
    page_ids: list[str] = Field(description="List of page IDs (from list_notebook_pages)", min_length=1)


# ---------------------------------------------------------------------------
# Toolset registration
# ---------------------------------------------------------------------------

@ToolsetBuilder("SharePoint")\
    .in_group("Microsoft 365")\
    .with_description("SharePoint sites, files, and pages")\
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
            app_description="SharePoint OAuth for agents",
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
    """SharePoint toolset for sites, pages, and document management. Uses MSGraphClient and SharePointDataSource."""

    def __init__(
        self,
        client: MSGraphClient,
        state: Optional[ChatState] = None,
        **kwargs,
    ) -> None:
        """Initialize with an authenticated MSGraphClient (from build_from_toolset).

        `state` carries `model_name`, `model_key`, and `config_service` — used by
        `get_file_content` for token-aware parsing via `FileContentParser`.
        """
        self.client = SharePointDataSource(client)
        self.state: Optional[ChatState] = state or kwargs.get("state")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _handle_error(self, error: Exception, operation: str = "operation") -> tuple[bool, str]:
        """Return a standardised error tuple."""
        error_msg = str(error).lower()

        if isinstance(error, AttributeError) and (
            "client" in error_msg or "sites" in error_msg
        ):
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
    def _serialize_response(response_obj: object) -> _JsonSerValue:
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
                    result: dict[str, object] = {}
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

    def _extract_collection(self, data: object) -> list[_JsonSerValue]:
        """Extract and serialize a collection from a Graph SDK response."""
        items: list[_JsonSerValue] = []
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

    def _extract_page_html_content(self, page_data: dict[str, Any]) -> str:
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

    @staticmethod
    def _normalize_notebook_name(name: Optional[str]) -> str:
        """Normalize notebook name for matching: lowercase, strip, remove .one/.onetoc2, collapse spaces."""
        if not name:
            return ""
        s = name.lower().strip().replace(".onetoc2", "").replace(".one", "")
        s = re.sub(r"[^a-z0-9]+", " ", s)
        return " ".join(s.split())

    # ------------------------------------------------------------------
    # Sites tools
    # ------------------------------------------------------------------

    @tool(
        app_name="sharepoint",
        tool_name="get_sites",
        description="List SharePoint sites",
        llm_description="List sites with optional KQL search, top/skip pagination; use get_site for one ID, search_pages for page by name.",
        args_schema=GetSitesInput,
        when_to_use=[
            "User wants to list or search SharePoint sites",
            "User needs site_id before get_pages (e.g. pages in [site name])",
        ],
        when_not_to_use=[
            "User has site_id already (use get_site)",
            "User wants a page by name (use search_pages)",
            "No SharePoint mention",
        ],
        primary_intent=ToolIntent.SEARCH,
        typical_queries=[
            "List all SharePoint sites",
            "Find sites about marketing",
            "Show pages in Engineering site → get_sites then get_pages",
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
        """
        List SharePoint sites accessible to the user.
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
        description="Get one site by ID",
        llm_description="Get one site by site_id; use get_sites to list, get_pages/search_pages for pages.",
        args_schema=GetSiteInput,
        when_to_use=[
            "User has site_id and wants that site's details",
        ],
        when_not_to_use=[
            "User wants to list sites (use get_sites)",
            "No SharePoint mention",
        ],
        primary_intent=ToolIntent.SEARCH,
        typical_queries=[
            "Get site details",
            "Show site information",
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
        description="List all pages in a site",
        llm_description="List all pages in a site (site_id); for one page by name use search_pages then get_page.",
        args_schema=GetPagesInput,
        when_to_use=[
            "User wants a full page list for one site (has or can get site_id)",
        ],
        when_not_to_use=[
            "User names one page only (use search_pages then get_page)",
            "No SharePoint mention",
        ],
        primary_intent=ToolIntent.SEARCH,
        typical_queries=[
            "List all pages in this site",
            "What pages exist in the Marketing site?",
        ],
        category=ToolCategory.DOCUMENTATION,
    )
    async def get_pages(
        self,
        site_id: str,
        top: Optional[int] = 10,
    ) -> tuple[bool, str]:
        """
        Get ALL pages from a SharePoint site (no filtering).
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
        description="Read one page (HTML)",
        llm_description="Get page HTML by site_id and page_id; use search_pages first if only name known; use before update_page to merge.",
        args_schema=GetPageInput,
        when_to_use=[
            "User wants to read/summarize a page and you have site_id + page_id",
        ],
        when_not_to_use=[
            "No page_id yet (use search_pages first)",
            "No SharePoint mention",
        ],
        primary_intent=ToolIntent.SEARCH,
        typical_queries=[
            "Summarize this page",
            "Read the deployment guide page",
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

            if not isinstance(page_data, dict) or not page_data.get("id"):
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
        description="Search pages by keyword",
        llm_description="Search pages by keyword across sites; returns page_id and site_id then call get_page for full content.",
        args_schema=SearchPagesInput,
        when_to_use=[
            "User names or describes a page but has no page_id",
        ],
        when_not_to_use=[
            "User already has page_id (use get_page)",
            "User wants every page in a site listed (use get_pages)",
            "No SharePoint mention",
        ],
        primary_intent=ToolIntent.SEARCH,
        typical_queries=[
            "Find the KT page",
            "Search pages for onboarding",
        ],
        category=ToolCategory.DOCUMENTATION,
    )
    async def search_pages(
        self,
        query: str,
        top: Optional[int] = 10,
        skip: Optional[int] = None,
    ) -> tuple[bool, str]:
        """
        Search for SharePoint pages by keyword across all accessible sites.
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
        description="List document libraries in a site",
        llm_description="List document libraries for a site; use this first to resolve drive_id before calling list_files.",
        args_schema=ListDrivesInput,
        when_to_use=[
            "User asks which document libraries/drives exist in a site",
            "User wants to browse files in a site but no drive_id is known yet",
        ],
        when_not_to_use=[
            "No SharePoint mention",
        ],
        primary_intent=ToolIntent.SEARCH,
        typical_queries=[
            "What libraries are in this site?",
            "List drives for this site",
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
        description="List files in site or folder",
        llm_description="List files in a specific document library or folder; requires drive_id from list_drives; use search_files to find by name.",
        args_schema=ListFilesInput,
        when_to_use=[
            "User wants to browse/list files in a known document library or folder",
        ],
        when_not_to_use=[
            "User wants a file by name (use search_files)",
            "No drive_id is known yet (use list_drives first)",
            "No SharePoint mention",
        ],
        primary_intent=ToolIntent.SEARCH,
        typical_queries=[
            "List files in the Documents library",
            "Show folder contents",
        ],
        category=ToolCategory.DOCUMENTATION,
    )
    async def list_files(
        self,
        site_id: str,
        drive_id: str,
        folder_id: Optional[str] = None,
        depth: Optional[int] = 1,
        top: Optional[int] = 10,
    ) -> tuple[bool, str]:
        """
        List files and folders from SharePoint document libraries.
        """
        try:
            capped_top = min(top or 10, 50)
            traversal_depth = depth or 1

            if traversal_depth < 1:
                return False, json.dumps({"error": "depth must be >= 1"})

            if not drive_id:
                return False, json.dumps({"error": "drive_id is required. Call list_drives(site_id) first."})

            response = await self.client.list_drive_children(
                drive_id=drive_id,
                folder_id=folder_id,
                top=capped_top,
                depth=traversal_depth,
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
                        "depth": item.get("depth"),
                        "parent_path": parent_ref.get("path"),
                    })
                logger.info(f"✅ list_files: {len(normalized)} items (drive={drive_id}, folder={folder_id})")
                return True, json.dumps({
                    "items": normalized,
                    "files": [i for i in normalized if not i["is_folder"]],
                    "folders": [i for i in normalized if i["is_folder"]],
                    "count": len(normalized),
                    "drive_id": drive_id,
                    "folder_id": folder_id,
                    "depth": traversal_depth,
                    "usage_hint": (
                        "To read a file's content call get_file_content(site_id, drive_id, item_id=item['id']). "
                        "To navigate into a folder call list_files(site_id, drive_id, folder_id=item['id']). "
                        "Use the 'depth' parameter to include nested folders."
                    ),
                })
            else:
                return False, json.dumps({"error": response.error or "Failed to list files"})
        except Exception as e:
            return self._handle_error(e, f"list files (site={site_id}, drive={drive_id})")

    @tool(
        app_name="sharepoint",
        tool_name="search_files",
        description="Find a file by name or keyword",
        llm_description="Find files by keyword; then get_file_content or get_file_metadata with id, drive_id, site_id from results.",
        args_schema=SearchFilesInput,
        when_to_use=[
            "User names or describes a file/document to find or read",
        ],
        when_not_to_use=[
            "User wants to browse whole library with no name (use list_files)",
            "User wants a page not a file (use search_pages)",
            "No SharePoint mention",
        ],
        primary_intent=ToolIntent.SEARCH,
        typical_queries=[
            "Find the budget document",
            "Get the onboarding file then read it",
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
        """
        Search for SharePoint files by keyword across all accessible document libraries.
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
                        "driveId": drive_id_val,
                        "siteId": site_id_val,
                        "itemId": item_id_val,
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
        description="File or folder metadata",
        llm_description="Get file/folder metadata (size, mimeType, dates); needs site_id, drive_id, item_id from search_files or list_files.",
        args_schema=GetFileMetadataInput,
        when_to_use=[
            "User wants file size, type, or dates; has site_id, drive_id, item_id",
        ],
        when_not_to_use=[
            "User wants file body (use get_file_content)",
            "No item_id yet (use search_files or list_files)",
            "No SharePoint mention",
        ],
        primary_intent=ToolIntent.SEARCH,
        typical_queries=[
            "How big is this file?",
            "When was it last modified?",
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
        description="Read file content",
        llm_description="Read file content as parsed text using the shared file parser; needs ids from search_files or list_files.",
        args_schema=GetFileContentInput,
        when_to_use=[
            "User wants to read or summarize file content; has ids from search_files or list_files",
        ],
        when_not_to_use=[
            "No item_id yet (use search_files or list_files first)",
            "User wants a page (use get_page)",
            "No SharePoint mention",
        ],
        primary_intent=ToolIntent.SEARCH,
        typical_queries=[
            "Read this document",
            "Summarize the project plan file",
        ],
        category=ToolCategory.DOCUMENTATION,
    )
    async def get_file_content(
        self,
        site_id: str,
        drive_id: str,
        item_id: str,
    ) -> tuple[bool, str]:
        """Download and parse a SharePoint file via the shared FileContentParser.

        Pre-fetches metadata to enforce a 50 MB cap before downloading bytes,
        rejects OneNote notebook files (`.one`, `.onetoc2`) with a redirect to
        the OneNote pipeline, then runs the same async parser used by OneDrive.
        """
        try:
            # 1. Metadata pre-fetch — for size cap, mime, name, extension.
            meta = await self.client.get_drive_item_metadata(
                site_id=site_id, drive_id=drive_id, item_id=item_id,
            )
            if not meta.success:
                return False, json.dumps({"error": meta.error or "File not found"})
            raw_meta = meta.data or {}
            file_facet = raw_meta.get("file") or {}
            mime_type = file_facet.get("mimeType") if isinstance(file_facet, dict) else None
            file_name = raw_meta.get("name") or "document"
            file_size = raw_meta.get("size")

            # 2. OneNote redirect — these need the OneNote API, not file content.
            if file_name.lower().endswith((".one", ".onetoc2")):
                return False, json.dumps({
                    "error": (
                        "OneNote notebook files (.one, .onetoc2) cannot be read with get_file_content. "
                        "Use sharepoint_find_notebook, then sharepoint_list_notebook_pages, then sharepoint_get_notebook_page_content."
                    )
                })

            # 3. Pre-download size cap.
            if file_size is not None and file_size > _MAX_FILE_CONTENT_BYTES:
                return False, json.dumps({
                    "error": f"File is too large to be processed (>{_MAX_FILE_CONTENT_BYTES // (1024 * 1024)} MB)",
                    "size_bytes": file_size,
                    "name": file_name,
                })

            # 4. Download raw bytes.
            resp = await self.client.get_drive_item_content(
                site_id=site_id, drive_id=drive_id, item_id=item_id,
            )
            if not resp.success:
                return False, json.dumps({"error": resp.error or "Failed to read file content"})

            raw = resp.data
            if not isinstance(raw, (bytes, bytearray)) or not raw:
                return True, json.dumps({"content": "", "size_bytes": 0, "note": "Empty file"})

            # 5. Resolve state-bound parser dependencies.
            state = self.state or {}
            model_name = state.get("model_name")
            model_key = state.get("model_key")
            config_service = state.get("config_service")

            # 6. Build FileRecord. Extension may come from the Graph `file` facet
            #    or — more reliably for SharePoint — from the file_name itself.
            ext = ""
            if isinstance(file_facet, dict):
                ext = (file_facet.get("fileExtension") or "").strip().lower().lstrip(".")
            if not ext and "." in file_name:
                ext = file_name.rsplit(".", 1)[1].lower()

            file_record = FileRecord(
                org_id="",
                record_name=file_name,
                record_type=RecordType.FILE,
                external_record_id=item_id,
                version=1,
                origin=OriginTypes.CONNECTOR,
                connector_name=Connectors.SHAREPOINT_ONLINE,
                connector_id=drive_id or "sharepoint",
                mime_type=mime_type or "application/octet-stream",
                extension=ext,
                is_file=True,
            )

            # 7. Parse via shared async parser.
            parser = FileContentParser(logger=logger, config_service=config_service)
            ok, payload = await parser.parse(
                file_record, bytes(raw), model_name, model_key, config_service,
            )
            serialized = [item.model_dump() for item in payload]
            if ok:
                return True, json.dumps({
                    "content": serialized,
                    "size_bytes": len(raw),
                    "mime_type": mime_type,
                    "file_name": file_name,
                })
            return False, json.dumps({"error": "Failed to parse file", "details": serialized})
        except Exception as e:
            return self._handle_error(e, f"get file content {item_id}")

    # ------------------------------------------------------------------
    # Page write tools (create / update)
    # ------------------------------------------------------------------

    @tool(
        app_name="sharepoint",
        tool_name="create_page",
        description="Create a SharePoint page",
        llm_description="Create page from content_html; publish=False unless user asks to publish.",
        args_schema=CreatePageInput,
        when_to_use=[
            "User wants a new site page; publish=False unless they ask to publish",
        ],
        when_not_to_use=[
            "User wants to edit existing page (use update_page)",
            "No SharePoint mention",
        ],
        primary_intent=ToolIntent.ACTION,
        typical_queries=[
            "Create a page called Project Overview",
            "Add a page and publish it",
        ],
        category=ToolCategory.DOCUMENTATION,
    )
    async def create_page(
        self,
        site_id: str,
        title: str,
        content_html: str,
        *,
        publish: Optional[bool] = False,
    ) -> tuple[bool, str]:
        """
        Create a new modern page in a SharePoint site.
        """
        try:
            response = await self.client.create_site_page(
                site_id=site_id,
                title=title,
                content_html=content_html,
                publish=bool(publish),
            )
            if not response.success:
                return False, json.dumps({"error": response.error or "Failed to create page"})

            page_data = response.data or {}
            page_id = page_data.get("id")
            published = page_data.get("published", False)
            publish_error = page_data.get("publish_error")
            # Avoid duplicating published/publish_error inside page payload
            page_payload = {k: v for k, v in page_data.items() if k not in ("published", "publish_error")}
            web_url = page_payload.get("webUrl") or page_payload.get("web_url")

            return True, json.dumps({
                "message": f"Page '{title}' created {'and published ' if published else '(draft) '}successfully",
                "page": page_payload,
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
        description="Update a SharePoint page",
        llm_description="Update page by page_id with title and/or content_html; get_page first to merge; publish=False unless asked.",
        args_schema=UpdatePageInput,
        when_to_use=[
            "User wants to edit/rename a page; needs page_id; publish=False unless asked",
        ],
        when_not_to_use=[
            "New page (use create_page)",
            "No page_id (use search_pages or get_pages)",
            "No SharePoint mention",
        ],
        primary_intent=ToolIntent.ACTION,
        typical_queries=[
            "Update this page with new content",
            "Rename the page and publish",
        ],
        category=ToolCategory.DOCUMENTATION,
    )
    async def update_page(
        self,
        site_id: str,
        page_id: str,
        title: Optional[str] = None,
        content_html: Optional[str] = None,
        *,
        publish: Optional[bool] = False,
    ) -> tuple[bool, str]:
        """
        Update the title and/or content of an existing SharePoint modern page.
        """
        if title is None and content_html is None:
            return False, json.dumps({"error": "At least one of 'title' or 'content_html' must be provided"})

        try:
            response = await self.client.update_site_page(
                site_id=site_id,
                page_id=page_id,
                title=title,
                content_html=content_html,
                publish=bool(publish),
            )
            if not response.success:
                return False, json.dumps({"error": response.error or "Failed to update page"})

            data = response.data or {}
            published = data.get("published", False)
            publish_error = data.get("publish_error")
            page_data = {"page_id": page_id}
            if title is not None:
                page_data["title"] = title

            # PATCH returns no body — fetch page to obtain webUrl for the success payload.
            web_url: Optional[str] = None
            try:
                get_resp = await self.client.get_site_page_with_canvas(
                    site_id=site_id, page_id=page_id
                )
                if get_resp.success and get_resp.data:
                    serialized = self._serialize_response(get_resp.data)
                    if isinstance(serialized, dict):
                        web_url = serialized.get("webUrl") or serialized.get("web_url")
            except Exception:
                pass

            payload: dict[str, Any] = {
                "message": f"Page updated {'and published ' if published else '(draft) '}successfully",
                "page": page_data,
                "page_id": page_id,
                "published": published,
            }
            if title is not None:
                payload["title"] = title
            if web_url:
                payload["web_url"] = web_url
            if publish_error:
                payload["publish_error"] = publish_error

            return True, json.dumps(payload)
        except Exception as e:
            return self._handle_error(e, f"update page {page_id}")

    # ------------------------------------------------------------------
    # Drive item write tools (create folder / file / notebook)
    # ------------------------------------------------------------------

    @tool(
        app_name="sharepoint",
        tool_name="create_folder",
        description="Create a folder in a library",
        llm_description="Create folder in drive root or under parent_folder_id; drive_id from list_drives.",
        args_schema=CreateFolderInput,
        when_to_use=[
            "User wants a new folder in a library (drive_id from list_drives)",
        ],
        when_not_to_use=[
            "User wants a file (use create_word_document)",
            "No SharePoint mention",
        ],
        primary_intent=ToolIntent.ACTION,
        typical_queries=[
            "Create folder Project Docs",
            "Add subfolder in this library",
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
        """
        Create a new folder in a SharePoint document library.
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
        description="Create a Word file (.docx)",
        llm_description="Create .docx in drive; file_name without extension; optional content_text and parent_folder_id.",
        args_schema=CreateWordDocumentInput,
        when_to_use=[
            "User wants a new .docx in a library",
        ],
        when_not_to_use=[
            "Folder only (use create_folder); page (use create_page); notebook (use create_onenote_notebook)",
            "No SharePoint mention",
        ],
        primary_intent=ToolIntent.ACTION,
        typical_queries=[
            "Create Word doc Meeting Notes",
            "Add a .docx with this text",
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
        """
        Create a new Word document (.docx) in a SharePoint document library.
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
        tool_name="move_item",
        description="Move a file or folder",
        llm_description="Move a SharePoint file or folder within the same drive; resolve drive_id via list_drives and item IDs via list_files or search_files first.",
        args_schema=MoveItemInput,
        when_to_use=[
            "User wants to move, relocate, or transfer a SharePoint file or folder",
            "User wants to move a document into another SharePoint folder",
        ],
        when_not_to_use=[
            "User wants to copy instead of move",
            "User wants rename only without moving",
            "drive_id or item IDs are unknown — resolve them with list_drives, list_files, or search_files first",
            "No SharePoint mention",
        ],
        primary_intent=ToolIntent.ACTION,
        typical_queries=[
            "Move budget.docx to Archive",
            "Move this folder into 2025",
        ],
        category=ToolCategory.DOCUMENTATION,
    )
    async def move_item(
        self,
        site_id: str,
        drive_id: str,
        item_id: str,
        destination_folder_id: str,
        new_name: Optional[str] = None,
    ) -> tuple[bool, str]:
        """Move a SharePoint file or folder within the same document library."""
        try:
            if not drive_id.strip():
                return False, json.dumps({"error": "drive_id is required"})
            if not item_id.strip():
                return False, json.dumps({"error": "item_id is required"})
            if not destination_folder_id.strip():
                return False, json.dumps({"error": "destination_folder_id is required"})
            if item_id == destination_folder_id:
                return False, json.dumps({"error": "item_id and destination_folder_id cannot be the same"})

            response = await self.client.move_drive_item(
                drive_id=drive_id,
                item_id=item_id,
                destination_folder_id=destination_folder_id,
                new_name=new_name,
            )
            if response.success:
                data = response.data or {}
                result: dict[str, Any] = {
                    "message": response.message or "Item moved successfully",
                    "item_id": data.get("id") or item_id,
                    "name": data.get("name"),
                    "web_url": data.get("webUrl") or data.get("web_url"),
                    "site_id": site_id,
                    "drive_id": (data.get("parentReference") or {}).get("driveId") or drive_id,
                    "destination_folder_id": (data.get("parentReference") or {}).get("id") or destination_folder_id,
                }
                if "parentReference" in data:
                    result["parent_reference"] = data.get("parentReference")
                if "warning" in data:
                    result["warning"] = data.get("warning")
                result["usage_hint"] = (
                    "Use list_files(site_id, drive_id, folder_id=destination_folder_id) to verify the moved item, "
                    "or get_file_metadata(site_id, drive_id, item_id) for updated details."
                )
                logger.info(
                    f"✅ move_item tool: item {item_id} moved in drive {drive_id} "
                    f"to {result['destination_folder_id']}"
                )
                return True, json.dumps(result)
            return False, json.dumps({"error": response.error or "Failed to move item"})
        except Exception as e:
            return self._handle_error(e, f"move item '{item_id}'")

    @tool(
        app_name="sharepoint",
        tool_name="find_notebook",
        description="Resolve a OneNote notebook by name in a given site. Use after sharepoint.search_files to get site_id.",
        llm_description="Use after sharepoint.search_files to get site_id. Call with site_id (from search_files results) and notebook name. If result is ambiguous, ask user to choose; do not call list_notebook_pages or get_notebook_page_content until resolved.",
        args_schema=FindNotebookInput,
        when_to_use=[
            "User asks for a notebook, OneNote notebook, or notebook pages by name (after search_files gave site_id)",
        ],
        when_not_to_use=[
            "No site_id yet (call sharepoint.search_files first)",
            "Already have site_id and notebook_id (use list_notebook_pages)",
            "User wants to create a notebook (use create_onenote_notebook)",
            "No OneNote/notebook mention",
        ],
        primary_intent=ToolIntent.SEARCH,
        typical_queries=[
            "Show mp_plan notebook",
            "List pages in notebook X",
        ],
        category=ToolCategory.DOCUMENTATION,
    )
    async def find_notebook(
        self,
        site_id: str,
        notebook_query: str,
    ) -> tuple[bool, str]:
        """Resolve a OneNote notebook by name in the given site. Lists notebooks for that site and matches by name."""
        try:
            list_resp = await self.client.list_onenote_notebooks(site_id=site_id, top=50, skip=0)
            if not list_resp.success:
                return False, json.dumps({
                    "resolved": False,
                    "error": list_resp.error or "Failed to list notebooks for this site.",
                })
            notebooks = (list_resp.data or {}).get("results") or (list_resp.data or {}).get("notebooks") or []
            query_norm = self._normalize_notebook_name(notebook_query)
            matches: list[dict[str, Any]] = []
            for nb in notebooks:
                if not isinstance(nb, dict):
                    continue
                disp = nb.get("display_name") or nb.get("displayName") or ""
                nb_norm = self._normalize_notebook_name(disp)
                if not query_norm:
                    matches.append({**nb, "site_id": site_id})
                    continue
                if nb_norm == query_norm or (query_norm in nb_norm) or (nb_norm in query_norm):
                    matches.append({**nb, "site_id": site_id})
            if len(matches) == 1:
                m = matches[0]
                return True, json.dumps({
                    "resolved": True,
                    "site_id": m.get("site_id"),
                    "notebook_id": m.get("notebook_id"),
                    "notebook_name": m.get("display_name") or m.get("displayName"),
                    "usage_hint": "Use sharepoint_list_notebook_pages(site_id, notebook_id) to list sections and pages.",
                })
            if len(matches) == 0:
                return False, json.dumps({
                    "resolved": False,
                    "error": f"No OneNote notebook matched '{notebook_query}' in this site.",
                })
            candidates = [
                {
                    "site_id": m.get("site_id"),
                    "notebook_id": m.get("notebook_id"),
                    "notebook_name": m.get("display_name") or m.get("displayName"),
                    "web_url": m.get("web_url"),
                }
                for m in matches
            ]
            return True, json.dumps({
                "resolved": False,
                "ambiguous": True,
                "candidates": candidates,
                "message": "Multiple notebooks with this name in this site. Please specify which one.",
            })
        except Exception as e:
            return self._handle_error(e, f"find notebook '{notebook_query}'")

    @tool(
        app_name="sharepoint",
        tool_name="list_notebook_pages",
        description="List sections and pages of a OneNote notebook",
        llm_description="List sections and pages (no content). Use site_id and notebook_id from find_notebook. Then use get_notebook_page_content for selected page_ids.",
        args_schema=ListNotebookPagesInput,
        when_to_use=[
            "User wants to see notebook structure or choose which pages to read",
        ],
        when_not_to_use=[
            "Notebook not yet resolved (use find_notebook first)",
            "User wants page content (use get_notebook_page_content)",
            "No OneNote notebook mention",
        ],
        primary_intent=ToolIntent.SEARCH,
        typical_queries=[
            "List pages in this notebook",
            "What sections are in the notebook?",
        ],
        category=ToolCategory.DOCUMENTATION,
    )
    async def list_notebook_pages(
        self,
        site_id: str,
        notebook_id: str,
    ) -> tuple[bool, str]:
        """List sections and pages of a OneNote notebook (metadata only, no content)."""
        try:
            sec_resp = await self.client.list_onenote_sections(
                site_id=site_id,
                notebook_id=notebook_id,
                top=50,
                skip=0,
            )
            if not sec_resp.success:
                return False, json.dumps({"error": sec_resp.error or "Failed to list sections"})
            sections_data = (sec_resp.data or {}).get("results") or (sec_resp.data or {}).get("sections") or []
            sections_with_pages: list[dict[str, Any]] = []
            flat_pages: list[dict[str, Any]] = []
            for sec in sections_data:
                if not isinstance(sec, dict):
                    continue
                sec_id = sec.get("section_id")
                sec_name = sec.get("display_name") or sec.get("displayName")
                if not sec_id:
                    continue
                page_resp = await self.client.list_onenote_pages(
                    site_id=site_id,
                    section_id=sec_id,
                    top=50,
                    skip=0,
                )
                raw_pages = (page_resp.data.get("results") or page_resp.data.get("pages") or []) if (page_resp.success and page_resp.data) else []
                section_pages: list[dict[str, Any]] = []
                for p in raw_pages:
                    if not isinstance(p, dict):
                        continue
                    flat_pages.append({
                        "page_id": p.get("page_id"),
                        "title": p.get("title"),
                        "section_id": sec_id,
                        "section_name": sec_name,
                        "order": p.get("order"),
                        "web_url": p.get("web_url"),
                    })
                    section_pages.append({**p, "section_name": sec_name})
                sections_with_pages.append({
                    "section_id": sec_id,
                    "section_name": sec_name,
                    "pages": section_pages,
                })
            return True, json.dumps({
                "notebook_id": notebook_id,
                "site_id": site_id,
                "sections": sections_with_pages,
                "pages": flat_pages,
                "usage_hint": "Use sharepoint_get_notebook_page_content(site_id, page_ids=[...]) for selected page_ids.",
            })
        except Exception as e:
            return self._handle_error(e, f"list notebook pages {notebook_id}")

    @tool(
        app_name="sharepoint",
        tool_name="get_notebook_page_content",
        description="Get content of OneNote pages",
        llm_description="Get HTML/text content for selected page_ids from list_notebook_pages. Do not use get_file_content for notebook pages.",
        args_schema=GetNotebookPageContentInput,
        when_to_use=[
            "User wants to read or summarize notebook page content",
        ],
        when_not_to_use=[
            "Page list not yet fetched (use list_notebook_pages first)",
            "Regular file (use get_file_content)",
            "No OneNote mention",
        ],
        primary_intent=ToolIntent.SEARCH,
        typical_queries=[
            "Read the overview page",
            "Summarize all pages in this notebook",
        ],
        category=ToolCategory.DOCUMENTATION,
    )
    async def get_notebook_page_content(
        self,
        site_id: str,
        page_ids: list[str],
    ) -> tuple[bool, str]:
        """Get content for selected OneNote pages."""
        try:
            cap = min(len(page_ids), 20)
            page_ids = page_ids[:cap]
            results: list[dict[str, Any]] = []
            failed_page_ids: list[str] = []
            for pid in page_ids:
                content_resp = await self.client.get_onenote_page_content(
                    site_id=site_id,
                    page_id=pid,
                    max_chars=12000,
                )
                if content_resp.success and content_resp.data:
                    results.append(content_resp.data)
                else:
                    failed_page_ids.append(pid)
            out: dict[str, Any] = {
                "pages": results,
                "count": len(results),
                "usage_hint": "Use these page contents to answer the user; do not call get_file_content for .one or .onetoc2.",
            }
            if failed_page_ids:
                out["failed_page_ids"] = failed_page_ids
            return True, json.dumps(out)
        except Exception as e:
            return self._handle_error(e, "get notebook page content")

    @tool(
        app_name="sharepoint",
        tool_name="create_onenote_notebook",
        description="Create a OneNote notebook",
        llm_description="Create OneNote notebook on site; optional section then page; page needs section_name.",
        args_schema=CreateOneNoteNotebookInput,
        when_to_use=[
            "User wants a new OneNote notebook on a site; optional section/page",
        ],
        when_not_to_use=[
            "Word doc (create_word_document); page (create_page); folder (create_folder)",
            "No SharePoint mention",
        ],
        primary_intent=ToolIntent.ACTION,
        typical_queries=[
            "Create notebook Team Notes",
            "Create notebook with Project section",
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
        """
        Create a new OneNote notebook in a SharePoint site.
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
                result: dict[str, Any] = {
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
