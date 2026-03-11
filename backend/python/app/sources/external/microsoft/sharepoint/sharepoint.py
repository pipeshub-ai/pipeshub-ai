import io
import json
import logging
import re
import zipfile
from datetime import datetime
from typing import Any, Dict, List, Mapping, Optional
from urllib.parse import quote

from kiota_abstractions.base_request_configuration import (  # type: ignore
    RequestConfiguration,
)
from kiota_abstractions.method import Method  # type: ignore
from kiota_abstractions.request_information import RequestInformation  # type: ignore
from msgraph.generated.models.drive_item import DriveItem  # type: ignore
from msgraph.generated.models.drive_item_collection_response import (  # type: ignore
    DriveItemCollectionResponse,
)
from msgraph.generated.models.notebook import Notebook  # type: ignore
from msgraph.generated.models.onenote_page import OnenotePage  # type: ignore
from msgraph.generated.models.onenote_section import OnenoteSection  # type: ignore
from msgraph.generated.models.entity_type import EntityType  # type: ignore
from msgraph.generated.models.o_data_errors.o_data_error import ODataError  # type: ignore
from msgraph.generated.models.site_page import SitePage  # type: ignore
from msgraph.generated.models.search_query import SearchQuery  # type: ignore
from msgraph.generated.models.search_request import SearchRequest  # type: ignore
from msgraph.generated.models.sort_property import SortProperty  # type: ignore
from msgraph.generated.search.query.query_post_request_body import QueryPostRequestBody  # type: ignore
from msgraph.generated.sites.item.columns.columns_request_builder import (  # type: ignore
    ColumnsRequestBuilder,
)
from msgraph.generated.sites.item.drives.drives_request_builder import (  # type: ignore
    DrivesRequestBuilder,
)
from msgraph.generated.sites.item.lists.lists_request_builder import (  # type: ignore
    ListsRequestBuilder,
)
from msgraph.generated.sites.item.pages.pages_request_builder import (  # type: ignore
    PagesRequestBuilder,
)
from msgraph.generated.sites.sites_request_builder import (  # type: ignore
    SitesRequestBuilder,
)

from app.agents.actions.util.parse_file import FileContentParser
from app.sources.client.microsoft.microsoft import MSGraphClient


# SharePoint-specific response wrapper
class SharePointResponse:
    """Standardized SharePoint API response wrapper."""
    success: bool
    data: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    message: Optional[str] = None

    def __init__(self, success: bool, data: Optional[Dict[str, Any]] = None, error: Optional[str] = None, message: Optional[str] = None) -> None:
        self.success = success
        self.data = data
        self.error = error
        self.message = message

    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "data": self.data,
            "error": self.error,
            "message": self.message,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict())

# Set up logger
logger = logging.getLogger(__name__)

class SharePointDataSource:
    """
    Comprehensive Microsoft SharePoint API client with complete Sites, Lists, and Libraries coverage.

    Features:
    - Complete SharePoint API coverage with 251 methods organized by operation type
    - Support for Sites, Site Collections, and Subsites
    - Complete List operations: lists, items, content types, columns
    - Complete Document Library operations: drives, folders, files
    - Modern Page operations: pages, canvas layout, web parts
    - Site-specific OneNote operations: notebooks, sections, pages
    - Site Analytics and Activity tracking
    - Site Permissions and Information Protection
    - Term Store and Metadata management
    - Site Search and Discovery capabilities
    - Microsoft Graph SDK integration with SharePoint-specific optimizations
    - Async snake_case method names for all operations
    - Standardized SharePointResponse format for all responses
    - Comprehensive error handling and SharePoint-specific response processing

    EXCLUDED OPERATIONS (modify EXCLUDED_KEYWORDS list to change):
    - Personal OneDrive operations (/me/drive, /users/{user-id}/drive)
    - Outlook operations (messages, events, contacts, calendar, mail folders)
    - Teams operations (chats, teams, channels)
    - Personal OneNote operations (/me/onenote, /users/{user-id}/onenote)
    - Planner operations (plans, tasks, buckets)
    - Directory operations (users, groups, directory objects)
    - Device management operations (devices, device management)
    - Admin operations (admin, compliance, security)
    - Generic drives operations (drives without site context)
    - User activity analytics (keep site analytics)
    - Communications operations (communications, education, identity)

    Operation Types:
    - Sites operations: Site collections, subsites, site information
    - Lists operations: Lists, list items, fields, content types
    - Drives operations: Document libraries, folders, files
    - Pages operations: Modern pages, canvas layout, web parts
    - Content Types operations: Site and list content types
    - Columns operations: Site and list columns, column definitions
    - OneNote operations: Site-specific notebooks, sections, pages
    - Permissions operations: Site and item permissions
    - Analytics operations: Site analytics and activity stats
    - Term Store operations: Managed metadata, term sets, terms
    - Operations operations: Long-running operations, subscriptions
    - Recycle Bin operations: Deleted items and restoration
    - Information Protection operations: Labels and policies
    - General operations: Base SharePoint functionality
    """

    def __init__(self, client: MSGraphClient) -> None:
        """Initialize with Microsoft Graph SDK client optimized for SharePoint."""
        self.client = client.get_client().get_ms_graph_service_client()
        if not hasattr(self.client, "sites"):
            raise ValueError("Client must be a Microsoft Graph SDK client")
        logger.info("SharePoint client initialized with 251 methods")

    def _handle_sharepoint_response(self, response: object) -> SharePointResponse:
        """Handle SharePoint API response with comprehensive error handling."""
        try:
            if response is None:
                return SharePointResponse(success=False, error="Empty response from SharePoint API")

            success = True
            error_msg = None

            # Enhanced error response handling for SharePoint operations
            if hasattr(response, 'error'):
                success = False
                error_msg = str(response.error)
            elif isinstance(response, dict) and 'error' in response:
                success = False
                error_info = response['error']
                if isinstance(error_info, dict):
                    error_code = error_info.get('code', 'Unknown')
                    error_message = error_info.get('message', 'No message')
                    error_msg = f"{error_code}: {error_message}"
                else:
                    error_msg = str(error_info)
            elif hasattr(response, 'code') and hasattr(response, 'message'):
                success = False
                error_msg = f"{response.code}: {response.message}"

            return SharePointResponse(
                success=success,
                data=response,
                error=error_msg,
            )
        except Exception as e:
            logger.error(f"Error handling SharePoint response: {e}")
            return SharePointResponse(success=False, error=str(e))

    def get_data_source(self) -> 'SharePointDataSource':
        """Get the underlying SharePoint client."""
        return self

    # ========== DELEGATED-AUTH OPTIMIZED METHODS ==========
    # for delegated (user-consent) OAuth permissions.

    async def list_sites_with_search_api(
        self,
        search_query: Optional[str] = None,
        top: Optional[int] = None,
        from_index: int = 0,
        orderby: Optional[str] = None,
    ) -> SharePointResponse:
        """List ALL SharePoint sites the user can access using Microsoft Graph Search API.

        Uses POST /search/query with entityTypes: ["site"] which is security-trimmed
        to the authenticated user. This returns ALL sites the user has access to.

        This is the RECOMMENDED approach for delegated permissions per Microsoft docs:
        https://learn.microsoft.com/en-us/graph/api/search-query

        The SDK handles authentication automatically - no manual token extraction needed.

        Args:
            search_query: Optional KQL search query to filter sites.
                         If None, searches for all site collections and webs:
                         "(contentclass:STS_Site OR contentclass:STS_Web)"
            top: Maximum number of sites to return (default: 10, max: 50)
            from_index: Offset for pagination (default: 0). Use multiples of top to
                        paginate (e.g. from_index=10 to get the next page of 10).
            orderby: Sort expression in "<field> <asc|desc>" format.
                     Examples: "createdDateTime desc", "lastModifiedDateTime desc", "name asc".

        Returns:
            SharePointResponse with:
                - success: True if search succeeded
                - data: {"value": List[Dict], "count": int}
                - error: Error message if search failed
        """
        try:
            all_sites_dict = {}  # For deduplication by webUrl
            page_size = min(top or 10, 50)

            # Build KQL query for site search
            if search_query and search_query.strip():
                kql_query = search_query
            else:
                # Default: search for all site collections and webs
                kql_query = "(contentclass:STS_Site OR contentclass:STS_Web)"

            logger.info(f"📍 Using Graph Search API (KQL: {kql_query!r}, size: {page_size}, from: {from_index})")

            # Build search request using SDK
            request_body = QueryPostRequestBody()
            search_request = SearchRequest()
            search_request.entity_types = [EntityType.Site]

            query = SearchQuery()
            query.query_string = kql_query
            search_request.query = query
            search_request.from_ = from_index
            search_request.size = page_size

            # Apply sort if provided — parse "<field> <asc|desc>"
            if orderby and orderby.strip():
                parts = orderby.strip().rsplit(" ", 1)
                sort_field = parts[0].strip()
                is_descending = len(parts) > 1 and parts[1].strip().lower() == "desc"
                sort_prop = SortProperty()
                sort_prop.name = sort_field
                sort_prop.is_descending = is_descending
                search_request.sort_properties = [sort_prop]
            
            request_body.requests = [search_request]

            # Execute search
            response = await self.client.search.query.post(request_body)
            
            # Parse results
            if response and hasattr(response, 'value') and response.value:
                for search_response in response.value:
                    hits_containers = getattr(search_response, 'hits_containers', None)
                    
                    if hits_containers:
                        for container in hits_containers:
                            if hasattr(container, 'hits') and container.hits:
                                for hit in container.hits:
                                    if hasattr(hit, 'resource'):
                                        site_dict = self._serialize_site(hit.resource)
                                        web_url = site_dict.get("webUrl")
                                        if web_url and web_url not in all_sites_dict:
                                            all_sites_dict[web_url] = site_dict

                logger.info(f"✅ Graph Search API returned {len(all_sites_dict)} unique sites")
            else:
                logger.warning("⚠️ No search results returned")

            all_sites = list(all_sites_dict.values())
            logger.info(f"✅ Total sites accessible to user: {len(all_sites)}")

            return SharePointResponse(
                success=True,
                data={"sites": all_sites, "count": len(all_sites)},
                message=f"Found {len(all_sites)} sites using Graph Search API (security-trimmed to user)",
            )

        except Exception as e:
            logger.error(f"❌ Graph Search API failed: {e}")
            error_msg = "Failed to list sites"
            if hasattr(e, 'error') and hasattr(e.error, 'message'):
                error_msg = e.error.message
            elif hasattr(e, 'message'):
                error_msg = str(e.message)
            else:
                error_msg = str(e)
            return SharePointResponse(
                success=False,
                error=error_msg,
            )

    async def search_pages_with_search_api(
        self,
        query: str,
        top: Optional[int] = 10,
        from_index: int = 0,
    ) -> SharePointResponse:
        """Search for SharePoint modern pages across ALL sites using the Graph Search API.

        Uses POST /search/query with entityTypes: ["listItem"] and KQL filter
        contentclass:STS_ListItem_WebPageLibrary — this correctly finds SharePoint
        modern site pages across every site the user has access to without needing
        a site ID.

        Args:
            query: Keyword or phrase to search (e.g. "pipeshub KT", "onboarding")
            top:   Maximum pages to return (default 10, max 50)
            from_index: Offset for pagination

        Returns:
            SharePointResponse with data={"pages": List[Dict], "count": int}
            Each page dict contains: id (page_id for get_page), title, web_url,
            site_id, list_item_id, last_modified, created
        """
        try:
            page_size = min(top or 10, 50)
            # contentclass:STS_ListItem_WebPageLibrary correctly targets SharePoint modern pages.
            # Terms are AND-ed by default in Microsoft Search KQL.
            kql_query = (
                f'{query.strip()} AND contentclass:STS_ListItem_WebPageLibrary'
                if query and query.strip()
                else "contentclass:STS_ListItem_WebPageLibrary"
            )

            logger.info(f"📍 Searching pages via Graph Search API (KQL: {kql_query!r}, size: {page_size}, from: {from_index})")

            request_body = QueryPostRequestBody()
            search_request = SearchRequest()
            search_request.entity_types = [EntityType.ListItem]

            search_query = SearchQuery()
            search_query.query_string = kql_query
            search_request.query = search_query
            search_request.from_ = from_index
            search_request.size = page_size

            request_body.requests = [search_request]

            response = await self.client.search.query.post(request_body)

            pages: List[Dict[str, Any]] = []
            if response and hasattr(response, "value") and response.value:
                for search_response in response.value:
                    hits_containers = getattr(search_response, "hits_containers", None)
                    if hits_containers:
                        for container in hits_containers:
                            if hasattr(container, "hits") and container.hits:
                                for hit in container.hits:
                                    resource = getattr(hit, "resource", None)
                                    if resource:
                                        page_dict = self._serialize_page_from_list_item(resource)
                                        if page_dict.get("web_url"):
                                            pages.append(page_dict)

            logger.info(f"✅ Page search returned {len(pages)} results")
            return SharePointResponse(
                success=True,
                data={"pages": pages, "count": len(pages)},
                message=f"Found {len(pages)} pages matching '{query}'",
            )

        except Exception as e:
            logger.error(f"❌ Page search failed: {e}")
            error_msg = str(e)
            if hasattr(e, "error") and hasattr(e.error, "message"):
                error_msg = e.error.message
            return SharePointResponse(success=False, error=error_msg)

    def _serialize_page_from_list_item(self, resource) -> Dict[str, Any]:
        """Extract page metadata from a listItem search hit resource.

        Expected resource shape (from Postman / Graph API):
        {
            "@odata.type": "#microsoft.graph.listItem",
            "id": "<guid>",                      ← returned as 'page_id' for use in get_page
            "webUrl": "https://.../SitePages/pipeshub-kt.aspx",
            "parentReference": {
                "siteId": "host,collection-guid,web-guid"
            },
            "sharepointIds": {
                "listId": "<guid>",
                "listItemId": "<int>"
            },
            "createdDateTime": "...",
            "lastModifiedDateTime": "..."
        }
        """
        # --- dict path (additional_data from Kiota or raw JSON) ---
        if isinstance(resource, dict):
            web_url = resource.get("webUrl") or resource.get("web_url", "")
            parent_ref = resource.get("parentReference") or {}
            sp_ids = resource.get("sharepointIds") or {}
            filename = web_url.rstrip("/").split("/")[-1] if web_url else ""
            return {
                "page_id": resource.get("id"),
                "title": self._title_from_aspx_name(filename),
                "web_url": web_url,
                "site_id": parent_ref.get("siteId"),
                "list_item_id": sp_ids.get("listItemId"),
                "last_modified": resource.get("lastModifiedDateTime"),
                "created": resource.get("createdDateTime"),
            }

        # --- Kiota Parsable object path ---
        item_id = getattr(resource, "id", None)
        web_url = getattr(resource, "web_url", None) or getattr(resource, "webUrl", None)
        last_modified = getattr(resource, "last_modified_date_time", None)
        created = getattr(resource, "created_date_time", None)

        site_id = None
        list_item_id = None

        parent_ref = getattr(resource, "parent_reference", None)
        if parent_ref:
            site_id = getattr(parent_ref, "site_id", None) or getattr(parent_ref, "siteId", None)

        sp_ids = getattr(resource, "sharepoint_ids", None)
        if sp_ids:
            list_item_id = getattr(sp_ids, "list_item_id", None) or getattr(sp_ids, "listItemId", None)

        # Fallback — look in additional_data for fields Kiota typed model may not expose
        additional = getattr(resource, "additional_data", {}) or {}
        if isinstance(additional, dict):
            if not web_url:
                web_url = additional.get("webUrl") or additional.get("web_url")
            if not site_id:
                pr = additional.get("parentReference") or {}
                if isinstance(pr, dict):
                    site_id = pr.get("siteId")
            if not list_item_id:
                sp = additional.get("sharepointIds") or {}
                if isinstance(sp, dict):
                    list_item_id = sp.get("listItemId")

        filename = web_url.rstrip("/").split("/")[-1] if web_url else ""
        return {
            "page_id": item_id,
            "title": self._title_from_aspx_name(filename),
            "web_url": web_url,
            "site_id": site_id,
            "list_item_id": list_item_id,
            "last_modified": str(last_modified) if last_modified else None,
            "created": str(created) if created else None,
        }

    def _title_from_aspx_name(self, name: str) -> str:
        """Convert an .aspx filename to a human-readable title.

        e.g. 'KT-Session.aspx' → 'KT Session'
             'deployment_guide.aspx' → 'deployment guide'
        """
        title = name.replace(".aspx", "")
        title = title.replace("-", " ").replace("_", " ")
        return title.strip()

    # ========== DOCUMENT / FILE OPERATIONS ==========

    def _serialize_drive(self, drive) -> Dict[str, Any]:
        """Serialize a Graph SDK Drive object to a plain dict."""
        if isinstance(drive, dict):
            return drive

        result: Dict[str, Any] = {}
        # Pull from additional_data first (Kiota backing store)
        additional = getattr(drive, "additional_data", None) or {}
        if isinstance(additional, dict):
            result.update(additional)

        prop_map = {
            "id": "id",
            "name": "name",
            "driveType": "drive_type",
            "webUrl": "web_url",
            "createdDateTime": "created_date_time",
            "lastModifiedDateTime": "last_modified_date_time",
            "description": "description",
        }
        for camel, snake in prop_map.items():
            if camel not in result:
                val = getattr(drive, camel, None) or getattr(drive, snake, None)
                if val is not None:
                    if isinstance(val, datetime):
                        val = val.isoformat()
                    result[camel] = val

        # Quota
        quota = getattr(drive, "quota", None)
        if quota and "quota" not in result:
            try:
                result["quota"] = {
                    "used": getattr(quota, "used", None),
                    "remaining": getattr(quota, "remaining", None),
                    "total": getattr(quota, "total", None),
                }
            except Exception:
                pass

        return result

    def _serialize_drive_item(self, item) -> Dict[str, Any]:
        """Serialize a Graph SDK DriveItem to a plain dict suitable for agents."""
        if isinstance(item, dict):
            return item

        result: Dict[str, Any] = {}
        additional = getattr(item, "additional_data", None) or {}
        if isinstance(additional, dict):
            result.update(additional)

        prop_map = {
            "id": "id",
            "name": "name",
            "webUrl": "web_url",
            "size": "size",
            "createdDateTime": "created_date_time",
            "lastModifiedDateTime": "last_modified_date_time",
            "eTag": "e_tag",
        }
        for camel, snake in prop_map.items():
            if camel not in result:
                val = getattr(item, camel, None) or getattr(item, snake, None)
                if val is not None:
                    if isinstance(val, datetime):
                        val = val.isoformat()
                    result[camel] = val

        # Folder / File facets
        folder = getattr(item, "folder", None)
        if folder and "folder" not in result:
            child_count = getattr(folder, "child_count", None)
            result["folder"] = {"childCount": child_count}

        file_facet = getattr(item, "file", None)
        if file_facet and "file" not in result:
            mime = getattr(file_facet, "mime_type", None) or getattr(file_facet, "mimeType", None)
            result["file"] = {"mimeType": mime}

        # parentReference for site_id / drive_id
        parent_ref = getattr(item, "parent_reference", None)
        if parent_ref and "parentReference" not in result:
            result["parentReference"] = {
                "driveId": getattr(parent_ref, "drive_id", None) or getattr(parent_ref, "driveId", None),
                "driveType": getattr(parent_ref, "drive_type", None) or getattr(parent_ref, "driveType", None),
                "id": getattr(parent_ref, "id", None),
                "siteId": getattr(parent_ref, "site_id", None) or getattr(parent_ref, "siteId", None),
                "path": getattr(parent_ref, "path", None),
            }

        return result

    def _is_text_mime(self, mime_type: Optional[str]) -> bool:
        """Return True for MIME types whose content can be read as plain text."""
        if not mime_type:
            return False
        TEXT_MIMES = {
            "text/plain",
            "text/html",
            "text/csv",
            "text/markdown",
            "text/xml",
            "application/json",
            "application/xml",
        }
        return mime_type in TEXT_MIMES or mime_type.startswith("text/")

    async def list_drives_for_site(
        self,
        site_id: str,
        top: int = 10,
    ) -> SharePointResponse:
        """List document libraries (drives) for a SharePoint site.

        Uses GET /sites/{site-id}/drives via the Graph SDK.

        Args:
            site_id: SharePoint site ID
            top: Maximum number of drives to return (default 10, max 50)

        Returns:
            SharePointResponse with data={"drives": List[Dict], "count": int}
        """
        try:
            config = DrivesRequestBuilder.DrivesRequestBuilderGetRequestConfiguration(
                query_parameters=DrivesRequestBuilder.DrivesRequestBuilderGetQueryParameters(
                    top=min(top, 50),
                )
            )
            response = await self.client.sites.by_site_id(site_id).drives.get(request_configuration=config)

            drives: List[Dict[str, Any]] = []
            if response and hasattr(response, "value") and response.value:
                drives = [self._serialize_drive(d) for d in response.value]
            elif response and isinstance(response, dict):
                raw = response.get("value") or []
                drives = [self._serialize_drive(d) for d in raw]

            logger.info(f"✅ list_drives_for_site: {len(drives)} drives for site {site_id}")
            return SharePointResponse(
                success=True,
                data={"drives": drives, "count": len(drives)},
                message=f"Found {len(drives)} document libraries",
            )
        except Exception as e:
            logger.error(f"❌ list_drives_for_site failed: {e}")
            return SharePointResponse(success=False, error=str(e))

    async def list_drive_children(
        self,
        drive_id: str,
        folder_id: Optional[str] = None,
        top: int = 10,
        depth: int = 1,
    ) -> SharePointResponse:
        """List children (files and folders) inside a drive or folder.

        - folder_id is None: lists the root of the drive via
            GET /sites/{site-id}/drives/{drive-id}/root/children
        - folder_id provided: lists that folder's children via
            GET /sites/{site-id}/drives/{drive-id}/items/{folder-id}/children

        Args:
            drive_id:  Drive (document library) ID
            folder_id: Item ID of the folder to list; None for drive root
            top:       Max items to return (default 10, max 50)
            depth:     Folder traversal depth where 1 lists direct children only

        Returns:
            SharePointResponse with data={"items": List[Dict], "count": int}
        """
        try:
            capped_top = min(top, 50)
            capped_depth = max(depth, 1)
            items: List[Dict[str, Any]] = []
            folder_queue: List[tuple[Optional[str], int]] = [(folder_id, 1)]
            queue_index = 0
            visited_folders = set()

            while queue_index < len(folder_queue):
                current_folder_id, current_depth = folder_queue[queue_index]
                queue_index += 1

                folder_key = current_folder_id or "__root__"
                if folder_key in visited_folders:
                    continue
                visited_folders.add(folder_key)

                if current_folder_id:
                    url = (
                        f"https://graph.microsoft.com/v1.0"
                        f"/drives/{drive_id}/items/{current_folder_id}/children"
                        f"?$top={capped_top}"
                    )
                else:
                    url = (
                        f"https://graph.microsoft.com/v1.0"
                        f"/drives/{drive_id}/root/children"
                        f"?$top={capped_top}"
                    )

                ri = RequestInformation()
                ri.http_method = Method.GET
                ri.url_template = url
                ri.path_parameters = {}

                response = await self.client.request_adapter.send_async(
                    ri, DriveItemCollectionResponse, {}
                )

                current_items: List[Dict[str, Any]] = []
                if response and hasattr(response, "value") and response.value:
                    current_items = [self._serialize_drive_item(i) for i in response.value]

                for item in current_items:
                    if not isinstance(item, dict):
                        continue
                    item["depth"] = current_depth
                    items.append(item)

                    is_folder = bool(item.get("folder")) or item.get("isFolder", False)
                    child_folder_id = item.get("id")
                    if is_folder and child_folder_id and current_depth < capped_depth:
                        folder_queue.append((child_folder_id, current_depth + 1))

            logger.info(
                f"✅ list_drive_children: {len(items)} items "
                f"(drive={drive_id}, folder={folder_id}, depth={capped_depth})"
            )
            return SharePointResponse(
                success=True,
                data={"items": items, "count": len(items), "depth": capped_depth},
                message=f"Found {len(items)} items",
            )
        except Exception as e:
            logger.error(f"❌ list_drive_children failed: {e}")
            return SharePointResponse(success=False, error=str(e))

    async def search_files_with_search_api(
        self,
        query: str,
        site_id: Optional[str] = None,
        top: int = 10,
        from_index: int = 0,
    ) -> SharePointResponse:
        """Search for SharePoint files/documents using the Graph Search API.

        Uses POST /search/query with EntityType.DriveItem — searches across all
        document libraries the user can access (optionally scoped to a site).

        Args:
            query:      Keyword or phrase to search for
            site_id:    Optional site ID to scope the search (KQL: path filter)
            top:        Max results (default 10, max 50)
            from_index: Offset for pagination

        Returns:
            SharePointResponse with data={"files": List[Dict], "count": int}
        """
        try:
            page_size = min(top, 50)

            # Build KQL query — scope to site if provided
            kql = query.strip() if query and query.strip() else "*"

            logger.info(f"📍 search_files_with_search_api: KQL={kql!r}, size={page_size}, from={from_index}")

            request_body = QueryPostRequestBody()
            search_request = SearchRequest()
            search_request.entity_types = [EntityType.DriveItem]

            search_query = SearchQuery()
            search_query.query_string = kql
            search_request.query = search_query
            search_request.from_ = from_index
            search_request.size = page_size

            request_body.requests = [search_request]
            response = await self.client.search.query.post(request_body)

            files: List[Dict[str, Any]] = []
            if response and hasattr(response, "value") and response.value:
                for search_response in response.value:
                    hits_containers = getattr(search_response, "hits_containers", None)
                    if hits_containers:
                        for container in hits_containers:
                            if hasattr(container, "hits") and container.hits:
                                for hit in container.hits:
                                    resource = getattr(hit, "resource", None)
                                    if resource:
                                        file_dict = self._serialize_file_from_search_hit(resource)
                                        # Filter to site if requested
                                        if site_id:
                                            parent_ref = file_dict.get("parentReference") or {}
                                            item_site_id = parent_ref.get("siteId", "")
                                            if site_id not in item_site_id and item_site_id not in site_id:
                                                continue
                                        files.append(file_dict)

            logger.info(f"✅ search_files_with_search_api: {len(files)} files for query={query!r}")
            return SharePointResponse(
                success=True,
                data={"files": files, "count": len(files)},
                message=f"Found {len(files)} files matching '{query}'",
            )
        except Exception as e:
            logger.error(f"❌ search_files_with_search_api failed: {e}")
            error_msg = str(e)
            if hasattr(e, "error") and hasattr(e.error, "message"):
                error_msg = e.error.message
            return SharePointResponse(success=False, error=error_msg)

    def _serialize_file_from_search_hit(self, resource) -> Dict[str, Any]:
        """Extract file metadata from a Graph Search DriveItem hit resource."""
        if isinstance(resource, dict):
            parent_ref = resource.get("parentReference") or {}
            file_facet = resource.get("file") or {}
            return {
                "id": resource.get("id"),
                "name": resource.get("name"),
                "webUrl": resource.get("webUrl") or resource.get("web_url"),
                "size": resource.get("size"),
                "createdDateTime": resource.get("createdDateTime"),
                "lastModifiedDateTime": resource.get("lastModifiedDateTime"),
                "mimeType": file_facet.get("mimeType"),
                "parentReference": {
                    "driveId": parent_ref.get("driveId"),
                    "siteId": parent_ref.get("siteId"),
                    "id": parent_ref.get("id"),
                    "path": parent_ref.get("path"),
                },
                "isFolder": "folder" in resource,
            }

        # Kiota Parsable object
        item_id = getattr(resource, "id", None)
        name = getattr(resource, "name", None)
        web_url = getattr(resource, "web_url", None) or getattr(resource, "webUrl", None)
        size = getattr(resource, "size", None)
        created = getattr(resource, "created_date_time", None)
        modified = getattr(resource, "last_modified_date_time", None)

        mime_type = None
        file_facet = getattr(resource, "file", None)
        if file_facet:
            mime_type = getattr(file_facet, "mime_type", None) or getattr(file_facet, "mimeType", None)

        parent_ref_obj = getattr(resource, "parent_reference", None)
        drive_id = site_id_from_item = parent_id = parent_path = None
        if parent_ref_obj:
            drive_id = getattr(parent_ref_obj, "drive_id", None) or getattr(parent_ref_obj, "driveId", None)
            site_id_from_item = getattr(parent_ref_obj, "site_id", None) or getattr(parent_ref_obj, "siteId", None)
            parent_id = getattr(parent_ref_obj, "id", None)
            parent_path = getattr(parent_ref_obj, "path", None)

        is_folder = getattr(resource, "folder", None) is not None

        # Also check additional_data
        additional = getattr(resource, "additional_data", {}) or {}
        if isinstance(additional, dict):
            if not web_url:
                web_url = additional.get("webUrl") or additional.get("web_url")
            if not mime_type:
                f = additional.get("file") or {}
                if isinstance(f, dict):
                    mime_type = f.get("mimeType")
            if parent_ref_obj is None:
                pr = additional.get("parentReference") or {}
                if isinstance(pr, dict):
                    drive_id = pr.get("driveId")
                    site_id_from_item = pr.get("siteId")
                    parent_id = pr.get("id")
                    parent_path = pr.get("path")

        return {
            "id": item_id,
            "name": name,
            "webUrl": web_url,
            "size": size,
            "createdDateTime": str(created) if created else None,
            "lastModifiedDateTime": str(modified) if modified else None,
            "mimeType": mime_type,
            "parentReference": {
                "driveId": drive_id,
                "siteId": site_id_from_item,
                "id": parent_id,
                "path": parent_path,
            },
            "isFolder": is_folder,
        }

    async def create_folder(
        self,
        drive_id: str,
        name: str,
        parent_folder_id: Optional[str] = None,
    ) -> SharePointResponse:
        """Create a new folder in a SharePoint document library.

        - parent_folder_id is None: creates in drive root via
            POST /drives/{drive-id}/root/children
        - parent_folder_id provided: creates inside that folder via
            POST /drives/{drive-id}/items/{parent-id}/children

        Args:
            drive_id:         Drive (document library) ID
            name:             Folder name
            parent_folder_id: Item ID of the parent folder; None for drive root

        Returns:
            SharePointResponse with folder metadata
        """
        try:
            if parent_folder_id:
                url = (
                    f"https://graph.microsoft.com/v1.0"
                    f"/drives/{drive_id}/items/{parent_folder_id}/children"
                )
            else:
                url = (
                    f"https://graph.microsoft.com/v1.0"
                    f"/drives/{drive_id}/root/children"
                )

            body = {
                "name": name,
                "folder": {},
                "@microsoft.graph.conflictBehavior": "rename",
            }

            ri = RequestInformation()
            ri.http_method = Method.POST
            ri.url_template = url
            ri.path_parameters = {}
            ri.content = json.dumps(body).encode("utf-8")
            ri.headers.try_add("Content-Type", "application/json")

            response = await self.client.request_adapter.send_async(ri, DriveItem, {})
            if response is None:
                return SharePointResponse(success=False, error="Failed to create folder — no response")

            item_dict = self._serialize_drive_item(response)
            logger.info(f"✅ create_folder: '{name}' in drive {drive_id} (parent={parent_folder_id})")
            return SharePointResponse(
                success=True,
                data=item_dict,
                message=f"Folder '{name}' created successfully",
            )
        except Exception as e:
            logger.error(f"❌ create_folder failed: {e}")
            return SharePointResponse(success=False, error=str(e))

    async def create_word_document(
        self,
        drive_id: str,
        name: str,
        parent_folder_id: Optional[str] = None,
        content_text: Optional[str] = None,
    ) -> SharePointResponse:
        """Create a new Word document (.docx) in a SharePoint document library.

        Builds a minimal valid .docx in memory using Python's zipfile + OOXML
        and uploads it via:
          PUT /drives/{drive-id}/root:/{name}.docx:/content  (root)
          PUT /drives/{drive-id}/items/{parent-id}:/{name}.docx:/content  (subfolder)

        Args:
            drive_id:         Drive (document library) ID
            name:             Document name (without .docx extension; extension is appended)
            parent_folder_id: Item ID of the parent folder; None for drive root
            content_text:     Optional plain-text content to insert into the document body

        Returns:
            SharePointResponse with document metadata (id, name, webUrl, etc.)
        """
        try:
            safe_name = name.strip()
            if safe_name.lower().endswith(".docx"):
                safe_name = safe_name[:-5]
            file_name = f"{safe_name}.docx"

            # Build minimal in-memory .docx (OOXML ZIP) ─────────────────────
            raw_text = content_text or ""
            escaped = (
                raw_text
                .replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;")
                .replace('"', "&quot;")
            )
            paragraphs_xml = "".join(
                f'<w:p><w:r><w:t xml:space="preserve">{line}</w:t></w:r></w:p>'
                for line in escaped.split("\n")
            ) or '<w:p><w:r><w:t></w:t></w:r></w:p>'

            content_types_xml = (
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                '<Default Extension="xml" ContentType="application/xml"/>'
                '<Override PartName="/word/document.xml" ContentType="application/'
                'vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
                '</Types>'
            )
            rels_xml = (
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
                '</Relationships>'
            )
            word_rels_xml = (
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"/>'
            )
            document_xml = (
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                '<w:body>'
                f'{paragraphs_xml}'
                '<w:sectPr/>'
                '</w:body>'
                '</w:document>'
            )

            buf = io.BytesIO()
            with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
                zf.writestr("[Content_Types].xml", content_types_xml)
                zf.writestr("_rels/.rels", rels_xml)
                zf.writestr("word/_rels/document.xml.rels", word_rels_xml)
                zf.writestr("word/document.xml", document_xml)
            docx_bytes = buf.getvalue()
            # ─────────────────────────────────────────────────────────────────

            if parent_folder_id:
                url = (
                    f"https://graph.microsoft.com/v1.0"
                    f"/drives/{drive_id}/items/{parent_folder_id}:/{file_name}:/content"
                )
            else:
                url = (
                    f"https://graph.microsoft.com/v1.0"
                    f"/drives/{drive_id}/root:/{file_name}:/content"
                )

            ri = RequestInformation()
            ri.http_method = Method.PUT
            ri.url_template = url
            ri.path_parameters = {}
            ri.content = docx_bytes
            ri.headers.try_add(
                "Content-Type",
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )

            response = await self.client.request_adapter.send_async(ri, DriveItem, {})
            if response is None:
                return SharePointResponse(success=False, error="Failed to create Word document — no response")

            item_dict = self._serialize_drive_item(response)
            logger.info(f"✅ create_word_document: '{file_name}' in drive {drive_id} (parent={parent_folder_id})")
            return SharePointResponse(
                success=True,
                data=item_dict,
                message=f"Word document '{file_name}' created successfully",
            )
        except Exception as e:
            logger.error(f"❌ create_word_document failed: {e}")
            return SharePointResponse(success=False, error=str(e))

    async def move_drive_item(
        self,
        drive_id: str,
        item_id: str,
        destination_folder_id: str,
        new_name: Optional[str] = None,
    ) -> SharePointResponse:
        """Move a SharePoint drive item to another folder in the same drive."""
        try:
            patch_data: Dict[str, Any] = {
                "parentReference": {
                    "id": destination_folder_id,
                }
            }
            if new_name:
                patch_data["name"] = new_name

            ri = RequestInformation()
            ri.http_method = Method.PATCH
            ri.url_template = (
                f"https://graph.microsoft.com/v1.0"
                f"/drives/{drive_id}/items/{item_id}"
            )
            ri.path_parameters = {}
            ri.content = json.dumps(patch_data).encode("utf-8")
            ri.headers.try_add("Content-Type", "application/json")

            response = await self.client.request_adapter.send_async(ri, DriveItem, {})
            if response is None:
                return SharePointResponse(success=False, error="Failed to move item — no response")

            item_dict = self._serialize_drive_item(response)

            logger.info(
                f"✅ move_drive_item: item {item_id} moved in drive {drive_id} "
                f"to parent {destination_folder_id}"
            )
            return SharePointResponse(
                success=True,
                data=item_dict,
                message="Item moved successfully",
            )
        except Exception as e:
            logger.error(f"❌ move_drive_item failed: {e}")
            return SharePointResponse(success=False, error=str(e))

    async def create_onenote_notebook(
        self,
        site_id: str,
        name: str,
        section_name: Optional[str] = None,
        page_title: Optional[str] = None,
        page_content_html: Optional[str] = None,
    ) -> SharePointResponse:
        """Create a new OneNote notebook in a SharePoint site.

        Optionally creates a first section and a first page within that section.

        Steps:
          1. POST /sites/{site-id}/onenote/notebooks
          2. If section_name: POST /sites/{site-id}/onenote/notebooks/{id}/sections
          3. If section created and (page_title or page_content_html):
                 POST /sites/{site-id}/onenote/sections/{id}/pages  (text/html body)

        Args:
            site_id:           SharePoint site ID
            name:              Notebook display name
            section_name:      Optional name for the first section to create
            page_title:        Optional title for the first page (requires section_name)
            page_content_html: Optional HTML body for the first page (requires section_name)

        Returns:
            SharePointResponse with notebook metadata and optionally section/page metadata
        """
        try:
            # ── Step 1: Create notebook ──────────────────────────────────────
            # Use the fluent SDK API so Kiota automatically handles error mapping,
            # serialization and the compound site ID format correctly.
            notebook_body = Notebook()
            notebook_body.display_name = name

            nb_response = await self.client.sites.by_site_id(site_id).onenote.notebooks.post(notebook_body)
            if nb_response is None:
                return SharePointResponse(success=False, error="Failed to create notebook — no response")

            notebook_id = nb_response.id
            nb_links = nb_response.links
            notebook_web_url: Optional[str] = None
            if nb_links:
                onenote_web = getattr(nb_links, "one_note_web_url", None)
                if onenote_web:
                    notebook_web_url = getattr(onenote_web, "href", None)

            result: Dict[str, Any] = {
                "notebook_id": notebook_id,
                "notebook_name": name,
                "notebook_web_url": notebook_web_url,
            }
            logger.info(f"✅ create_onenote_notebook: '{name}' (id={notebook_id}) in site {site_id}")

            # ── Step 2: Create section (optional) ────────────────────────────
            section_id: Optional[str] = None
            if section_name and notebook_id:
                section_body = OnenoteSection()
                section_body.display_name = section_name

                sec_response = await (
                    self.client.sites.by_site_id(site_id)
                    .onenote.notebooks.by_notebook_id(notebook_id)
                    .sections.post(section_body)
                )
                section_id = sec_response.id if sec_response else None
                result["section_id"] = section_id
                result["section_name"] = section_name
                logger.info(f"✅ created section '{section_name}' (id={section_id})")

            # ── Step 3: Create page (optional) ───────────────────────────────
            # The pages endpoint requires a multipart/form-data or text/html body,
            # which the fluent API does not support natively — use raw RequestInformation.
            # Compound site_id must be URL-encoded so path parsing does not produce error 20143.
            if section_id and (page_title or page_content_html):
                title = page_title or name
                body_html = page_content_html or ""
                html_content = (
                    f"<!DOCTYPE html>"
                    f"<html>"
                    f"<head><title>{title}</title></head>"
                    f"<body>{body_html}</body>"
                    f"</html>"
                )
                encoded_site_id = quote(site_id, safe="")
                page_ri = RequestInformation()
                page_ri.http_method = Method.POST
                page_ri.url_template = (
                    f"https://graph.microsoft.com/v1.0"
                    f"/sites/{encoded_site_id}/onenote/sections/{section_id}/pages"
                )
                page_ri.path_parameters = {}
                page_ri.content = html_content.encode("utf-8")
                page_ri.headers.try_add("Content-Type", "text/html")

                page_response = await self.client.request_adapter.send_async(
                    page_ri,
                    OnenotePage,
                    {
                        "4XX": ODataError,
                        "5XX": ODataError,
                    },
                )
                page_id = getattr(page_response, "id", None) if page_response else None
                page_web_url: Optional[str] = None
                if page_response:
                    pg_links = getattr(page_response, "links", None)
                    if pg_links:
                        onenote_web = getattr(pg_links, "one_note_web_url", None)
                        if onenote_web:
                            page_web_url = getattr(onenote_web, "href", None)
                result["page_id"] = page_id
                result["page_title"] = title
                result["page_web_url"] = page_web_url
                logger.info(f"✅ created page '{title}' (id={page_id})")

            return SharePointResponse(
                success=True,
                data=result,
                message=f"OneNote notebook '{name}' created successfully",
            )
        except ODataError as e:
            error_msg = getattr(getattr(e, "error", None), "message", str(e))
            logger.error(f"❌ create_onenote_notebook Graph API error: {error_msg}")
            return SharePointResponse(success=False, error=error_msg)
        except Exception as e:
            logger.error(f"❌ create_onenote_notebook failed: {e}")
            return SharePointResponse(success=False, error=str(e))

    async def get_drive_item_metadata(
        self,
        site_id: str,
        drive_id: str,
        item_id: str,
    ) -> SharePointResponse:
        """Get metadata for a specific drive item (file or folder).

        Uses the Graph SDK typed path:
          GET /drives/{drive-id}/items/{item-id}

        Args:
            site_id:  SharePoint site ID (kept for API consistency; routing goes through drive_id)
            drive_id: Drive (document library) ID
            item_id:  DriveItem ID

        Returns:
            SharePointResponse with the item metadata dict
        """
        try:
            url = (
                f"https://graph.microsoft.com/v1.0"
                f"/drives/{drive_id}/items/{item_id}"
            )
            ri = RequestInformation()
            ri.http_method = Method.GET
            ri.url_template = url
            ri.path_parameters = {}

            response = await self.client.request_adapter.send_async(ri, DriveItem, {})
            if response is None:
                return SharePointResponse(success=False, error="Item not found")

            item_dict = self._serialize_drive_item(response)
            logger.info(f"✅ get_drive_item_metadata: {item_id}")
            return SharePointResponse(success=True, data=item_dict)
        except Exception as e:
            logger.error(f"❌ get_drive_item_metadata failed: {e}")
            return SharePointResponse(success=False, error=str(e))

    async def get_drive_item_content(
        self,
        site_id: str,
        drive_id: str,
        item_id: str,
        max_bytes: int = 512_000,
    ) -> SharePointResponse:
        """Download the content of a drive item as readable text.

        Downloads raw drive item bytes and parses them through the shared
        FileContentParser helper for supported text, PDF, and OOXML formats.

        Args:
            site_id:   SharePoint site ID (unused in URL but kept for API consistency)
            drive_id:  Drive ID
            item_id:   DriveItem ID
            max_bytes: Maximum bytes to read (default 512 KB)

        Returns:
            SharePointResponse with data={"content": str, "encoding": str, ...}
        """
        try:
            url = (
                f"https://graph.microsoft.com/v1.0"
                f"/drives/{drive_id}/items/{item_id}/content"
            )
            ri = RequestInformation()
            ri.http_method = Method.GET
            ri.url_template = url
            ri.path_parameters = {}

            raw: Optional[bytes] = await self.client.request_adapter.send_primitive_async(
                ri, "bytes", {}
            )

            if not raw:
                return SharePointResponse(
                    success=True,
                    data={"content": "", "encoding": "utf-8", "size_bytes": 0},
                    message="Empty file",
                )

            parser = FileContentParser()
            parsed_success, parsed_json = parser.parse(raw, max_bytes=max_bytes)
            if not parsed_success:
                return SharePointResponse(success=False, error=json.loads(parsed_json).get("error", "Failed to parse file content"))

            parsed = json.loads(parsed_json)
            content = parsed.get("content", "")
            truncated = bool(parsed.get("truncated", False))
            format_label = parsed.get("format", "text")
            size_bytes = parsed.get("bytes_read", len(raw))

            logger.info(
                f"✅ get_drive_item_content: {item_id}, {size_bytes} bytes, format={format_label}"
            )
            return SharePointResponse(
                success=True,
                data={
                    "content": content,
                    "encoding": "utf-8",
                    "format": format_label,
                    "size_bytes": size_bytes,
                    "truncated": truncated,
                },
                message=f"Content parsed as {format_label} ({size_bytes} bytes{', truncated' if truncated else ''})",
            )
        except Exception as e:
            logger.error(f"❌ get_drive_item_content failed: {e}")
            return SharePointResponse(success=False, error=str(e))

    def _serialize_site(self, site) -> Dict[str, Any]:
        """Convert a Graph SDK site object to a dictionary."""
        if isinstance(site, dict):
            return site
        
        # Serialize using additional_data (Kiota backing store)
        if hasattr(site, 'additional_data') and isinstance(site.additional_data, dict):
            result = dict(site.additional_data)
            
            # Add common properties (check both camelCase and snake_case)
            prop_map = {
                'id': 'id',
                'name': 'name', 
                'displayName': 'display_name',
                'webUrl': 'web_url',
                'description': 'description',
                'createdDateTime': 'created_date_time',
                'lastModifiedDateTime': 'last_modified_date_time',
            }
            
            for camel_case, snake_case in prop_map.items():
                if camel_case not in result:
                    val = None
                    if hasattr(site, camel_case):
                        val = getattr(site, camel_case)
                    elif hasattr(site, snake_case):
                        val = getattr(site, snake_case)
                    
                    if val is not None:
                        if isinstance(val, datetime):
                            val = val.isoformat()
                        result[camel_case] = val
            
            return result
        
        # Fallback: extract all non-private attributes
        result = {}
        for attr in dir(site):
            if not attr.startswith('_'):
                try:
                    val = getattr(site, attr)
                    if not callable(val):
                        if isinstance(val, datetime):
                            val = val.isoformat()
                        result[attr] = val
                except Exception:
                    pass
        return result

    async def get_site_by_id(
        self,
        site_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
    ) -> SharePointResponse:
        """Get a specific SharePoint site by its ID using delegated permissions.

        Uses the SDK's GET /sites/{site-id} endpoint which works with delegated OAuth.

        Args:
            site_id: The site ID (e.g., 'contoso.sharepoint.com,guid1,guid2')
            select: Select specific properties to return
            expand: Expand related entities

        Returns:
            SharePointResponse with the site data or error
        """
        try:
            response = await self.client.sites.by_site_id(site_id).get()
            
            if response:
                site_dict = self._serialize_site(response)
                logger.info(f"✅ Retrieved site: {site_id}")
                return SharePointResponse(success=True, data=site_dict)
            else:
                logger.warning(f"⚠️ No site returned for ID: {site_id}")
                return SharePointResponse(success=False, error="Site not found")

        except Exception as e:
            logger.error(f"❌ Error getting site by ID: {e}")
            error_msg = "Failed to get site"
            if hasattr(e, 'error') and hasattr(e.error, 'message'):
                error_msg = e.error.message
            elif hasattr(e, 'message'):
                error_msg = str(e.message)
            else:
                error_msg = str(e)
            return SharePointResponse(success=False, error=error_msg)

    async def get_site_page_with_canvas(
        self,
        site_id: str,
        page_id: str,
    ) -> SharePointResponse:
        """Get a SharePoint modern site page by ID with canvas layout (full content).

        Uses GET /sites/{site-id}/pages/{page-id}/microsoft.graph.sitePage?$expand=canvasLayout.
        Compound site_id (host,guid,guid) is URL-encoded so the path is not mis-parsed (404).

        Args:
            site_id: SharePoint site ID (e.g. contoso.sharepoint.com,guid,guid)
            page_id: Page ID (GUID from search_pages or get_pages)

        Returns:
            SharePointResponse with success=True and data=<SitePage Kiota object>,
            or success=False with error message.
        """
        try:
            encoded_site_id = quote(site_id, safe="")
            encoded_page_id = quote(page_id, safe="")
            ri = RequestInformation()
            ri.http_method = Method.GET
            ri.url_template = (
                f"https://graph.microsoft.com/v1.0/sites/{encoded_site_id}"
                f"/pages/{encoded_page_id}/microsoft.graph.sitePage"
                f"?$expand=canvasLayout"
            )
            ri.path_parameters = {}

            response = await self.client.request_adapter.send_async(ri, SitePage, {})
            if response is None:
                return SharePointResponse(success=False, error="Page not found")
            logger.info(f"✅ get_site_page_with_canvas: {page_id}")
            return SharePointResponse(success=True, data=response)
        except Exception as e:
            logger.error(f"❌ get_site_page_with_canvas failed: {e}")
            return SharePointResponse(success=False, error=str(e))

    def _site_page_post_response_to_dict(self, response: Any) -> Dict[str, Any]:
        """Best-effort dict from Kiota SitePage POST response for agents."""
        if response is None:
            return {}
        if isinstance(response, dict):
            return dict(response)
        out: Dict[str, Any] = {}
        for attr in ("id", "title", "name"):
            val = getattr(response, attr, None)
            if val is not None:
                out[attr] = val
        web_url = getattr(response, "web_url", None) or getattr(response, "webUrl", None)
        if web_url is not None:
            out["webUrl"] = web_url
            out["web_url"] = web_url
        additional = getattr(response, "additional_data", None) or {}
        if isinstance(additional, dict):
            for k, v in additional.items():
                if k not in out:
                    out[k] = v
        return out

    async def _publish_site_page(
        self, site_id: str, page_id: str
    ) -> tuple[bool, Optional[str]]:
        """POST .../microsoft.graph.sitePage/publish. Returns (True, None) or (False, error_msg)."""
        try:
            encoded_site_id = quote(site_id, safe="")
            pub_ri = RequestInformation()
            pub_ri.http_method = Method.POST
            pub_ri.url_template = (
                f"https://graph.microsoft.com/v1.0/sites/{encoded_site_id}"
                f"/pages/{page_id}/microsoft.graph.sitePage/publish"
            )
            pub_ri.path_parameters = {}
            pub_ri.content = b"{}"
            pub_ri.headers.try_add("Content-Type", "application/json")
            await self.client.request_adapter.send_no_response_content_async(pub_ri, {})
            return True, None
        except Exception as e:
            return False, str(e)

    async def create_site_page(
        self,
        site_id: str,
        title: str,
        content_html: str,
        publish: bool = False,
    ) -> SharePointResponse:
        """Create a modern SitePage with one text web part; optional publish.

        POST /sites/{site-id}/pages then POST .../microsoft.graph.sitePage/publish
        when publish=True. Uses URL-encoded site_id for publish (compound IDs).
        """
        try:
            slug = re.sub(r"[^a-z0-9-]", "-", title.lower()).strip("-")
            slug = re.sub(r"-+", "-", slug) or "page"

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

            logger.info(f"📍 create_site_page: '{title}' in site {site_id}")
            response = await self.client.sites.by_site_id(site_id).pages.post(body)
            page_data = self._site_page_post_response_to_dict(response)
            page_id = page_data.get("id")

            published = False
            publish_error: Optional[str] = None
            if publish and page_id:
                published, publish_error = await self._publish_site_page(site_id, page_id)
                if published:
                    logger.info(f"✅ create_site_page published (id={page_id})")
                else:
                    logger.warning(f"⚠️ create_site_page publish failed: {publish_error}")

            page_data["published"] = published
            if publish_error:
                page_data["publish_error"] = publish_error
            return SharePointResponse(success=True, data=page_data, message=f"Page '{title}' created")
        except Exception as e:
            logger.error(f"❌ create_site_page failed: {e}")
            return SharePointResponse(success=False, error=str(e))

    async def update_site_page(
        self,
        site_id: str,
        page_id: str,
        title: Optional[str] = None,
        content_html: Optional[str] = None,
        publish: bool = False,
    ) -> SharePointResponse:
        """PATCH page as microsoft.graph.sitePage; optional publish.

        PATCH /sites/{site-id}/pages/{page-id}/microsoft.graph.sitePage
        Typed SDK path missing for cast; uses RequestInformation + encoded site_id.
        """
        if title is None and content_html is None:
            return SharePointResponse(
                success=False,
                error="At least one of title or content_html must be provided",
            )
        try:
            patch_data: Dict[str, Any] = {"@odata.type": "#microsoft.graph.sitePage"}
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

            encoded_site_id = quote(site_id, safe="")
            logger.info(f"📍 update_site_page: {page_id} in site {site_id}")
            patch_ri = RequestInformation()
            patch_ri.http_method = Method.PATCH
            patch_ri.url_template = (
                f"https://graph.microsoft.com/v1.0/sites/{encoded_site_id}"
                f"/pages/{page_id}/microsoft.graph.sitePage"
            )
            patch_ri.path_parameters = {}
            patch_ri.content = json.dumps(patch_data).encode("utf-8")
            patch_ri.headers.try_add("Content-Type", "application/json")
            await self.client.request_adapter.send_no_response_content_async(patch_ri, {})

            published = False
            publish_error: Optional[str] = None
            if publish:
                published, publish_error = await self._publish_site_page(site_id, page_id)
                if published:
                    logger.info(f"✅ update_site_page published (id={page_id})")
                else:
                    logger.warning(f"⚠️ update_site_page publish failed: {publish_error}")

            data: Dict[str, Any] = {
                "page_id": page_id,
                "published": published,
            }
            if title is not None:
                data["title"] = title
            if publish_error:
                data["publish_error"] = publish_error
            return SharePointResponse(success=True, data=data, message="Page updated")
        except Exception as e:
            logger.error(f"❌ update_site_page failed: {e}")
            return SharePointResponse(success=False, error=str(e))

    # ========== SITES OPERATIONS (17 methods) ==========

    async def sites_add(
        self,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Invoke action add.
        SharePoint operation: POST /sites/add
        Operation type: sites
        Args:
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for SharePoint operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.add.post(body=request_body, request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_delta(
        self,
        dollar_select: Optional[List[str]] = None,
        dollar_orderby: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Invoke function delta.
        SharePoint operation: GET /sites/delta()
        Operation type: sites
        Args:
            dollar_select (List[str], optional): Select properties to be returned
            dollar_orderby (List[str], optional): Order items by property values
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = SitesRequestBuilder.SitesRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = SitesRequestBuilder.SitesRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.delta().get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_get_all_sites(
        self,
        dollar_select: Optional[List[str]] = None,
        dollar_orderby: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Invoke function getAllSites.
        SharePoint operation: GET /sites/getAllSites()
        Operation type: sites
        Args:
            dollar_select (List[str], optional): Select properties to be returned
            dollar_orderby (List[str], optional): Order items by property values
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = SitesRequestBuilder.SitesRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = SitesRequestBuilder.SitesRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.get_all_sites.get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_remove(
        self,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Invoke action remove.
        SharePoint operation: POST /sites/remove
        Operation type: sites
        Args:
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for SharePoint operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.remove.post(body=request_body, request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_site_update_site(
        self,
        site_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Update entity in sites.
        SharePoint operation: PATCH /sites/{site-id}
        Operation type: sites
        Args:
            site_id (str, required): SharePoint site id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for SharePoint operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).patch(body=request_body, request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_created_by_user_update_mailbox_settings(
        self,
        site_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Update property mailboxSettings value..
        SharePoint operation: PATCH /sites/{site-id}/createdByUser/mailboxSettings
        Operation type: sites
        Args:
            site_id (str, required): SharePoint site id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for SharePoint operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).created_by_user.mailbox_settings.patch(body=request_body, request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_created_by_user_list_service_provisioning_errors(
        self,
        site_id: str,
        dollar_orderby: Optional[List[str]] = None,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Get serviceProvisioningErrors property value.
        SharePoint operation: GET /sites/{site-id}/createdByUser/serviceProvisioningErrors
        Operation type: sites
        Args:
            site_id (str, required): SharePoint site id identifier
            dollar_orderby (List[str], optional): Order items by property values
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = SitesRequestBuilder.SitesRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = SitesRequestBuilder.SitesRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).created_by_user.service_provisioning_errors.get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_site_get_activities_by_interval_4c35(
        self,
        site_id: str,
        dollar_select: Optional[List[str]] = None,
        dollar_orderby: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Invoke function getActivitiesByInterval.
        SharePoint operation: GET /sites/{site-id}/getActivitiesByInterval()
        Operation type: sites
        Args:
            site_id (str, required): SharePoint site id identifier
            dollar_select (List[str], optional): Select properties to be returned
            dollar_orderby (List[str], optional): Order items by property values
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = SitesRequestBuilder.SitesRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = SitesRequestBuilder.SitesRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).get_activities_by_interval().get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_site_get_activities_by_interval_ad27(
        self,
        site_id: str,
        startDateTime: str,
        endDateTime: str,
        interval: str,
        dollar_select: Optional[List[str]] = None,
        dollar_orderby: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Invoke function getActivitiesByInterval.
        SharePoint operation: GET /sites/{site-id}/getActivitiesByInterval(startDateTime='{startDateTime}',endDateTime='{endDateTime}',interval='{interval}')
        Operation type: sites
        Args:
            site_id (str, required): SharePoint site id identifier
            startDateTime (str, required): SharePoint path parameter: startDateTime
            endDateTime (str, required): SharePoint path parameter: endDateTime
            interval (str, required): SharePoint path parameter: interval
            dollar_select (List[str], optional): Select properties to be returned
            dollar_orderby (List[str], optional): Order items by property values
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = SitesRequestBuilder.SitesRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = SitesRequestBuilder.SitesRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).get_activities_by_interval(start_date_time='{start_date_time}',end_date_time='{end_date_time}',interval='{interval}').get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_site_get_by_path(
        self,
        site_id: str,
        path: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Invoke function getByPath.
        SharePoint operation: GET /sites/{site-id}/getByPath(path='{path}')
        Operation type: sites
        Args:
            site_id (str, required): SharePoint site id identifier
            path (str, required): SharePoint path: path
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = SitesRequestBuilder.SitesRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = SitesRequestBuilder.SitesRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).get_by_path.get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_site_get_by_path_get_activities_by_interval_4c35(
        self,
        site_id: str,
        path: str,
        dollar_select: Optional[List[str]] = None,
        dollar_orderby: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Invoke function getActivitiesByInterval.
        SharePoint operation: GET /sites/{site-id}/getByPath(path='{path}')/getActivitiesByInterval()
        Operation type: sites
        Args:
            site_id (str, required): SharePoint site id identifier
            path (str, required): SharePoint path: path
            dollar_select (List[str], optional): Select properties to be returned
            dollar_orderby (List[str], optional): Order items by property values
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = SitesRequestBuilder.SitesRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = SitesRequestBuilder.SitesRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).get_by_path.get_activities_by_interval().get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_site_get_by_path_get_activities_by_interval_ad27(
        self,
        site_id: str,
        path: str,
        startDateTime: str,
        endDateTime: str,
        interval: str,
        dollar_select: Optional[List[str]] = None,
        dollar_orderby: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Invoke function getActivitiesByInterval.
        SharePoint operation: GET /sites/{site-id}/getByPath(path='{path}')/getActivitiesByInterval(startDateTime='{startDateTime}',endDateTime='{endDateTime}',interval='{interval}')
        Operation type: sites
        Args:
            site_id (str, required): SharePoint site id identifier
            path (str, required): SharePoint path: path
            startDateTime (str, required): SharePoint path parameter: startDateTime
            endDateTime (str, required): SharePoint path parameter: endDateTime
            interval (str, required): SharePoint path parameter: interval
            dollar_select (List[str], optional): Select properties to be returned
            dollar_orderby (List[str], optional): Order items by property values
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = SitesRequestBuilder.SitesRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = SitesRequestBuilder.SitesRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).get_by_path.get_activities_by_interval(start_date_time='{start_date_time}',end_date_time='{end_date_time}',interval='{interval}').get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_get_by_path_list_sites(
        self,
        site_id: str,
        path: str,
        dollar_orderby: Optional[List[str]] = None,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Get sites from sites.
        SharePoint operation: GET /sites/{site-id}/getByPath(path='{path}')/sites
        Operation type: sites
        Args:
            site_id (str, required): SharePoint site id identifier
            path (str, required): SharePoint path: path
            dollar_orderby (List[str], optional): Order items by property values
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = SitesRequestBuilder.SitesRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = SitesRequestBuilder.SitesRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).get_by_path.sites.get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_last_modified_by_user_update_mailbox_settings(
        self,
        site_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Update property mailboxSettings value..
        SharePoint operation: PATCH /sites/{site-id}/lastModifiedByUser/mailboxSettings
        Operation type: sites
        Args:
            site_id (str, required): SharePoint site id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for SharePoint operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).last_modified_by_user.mailbox_settings.patch(body=request_body, request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_last_modified_by_user_list_service_provisioning_errors(
        self,
        site_id: str,
        dollar_orderby: Optional[List[str]] = None,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Get serviceProvisioningErrors property value.
        SharePoint operation: GET /sites/{site-id}/lastModifiedByUser/serviceProvisioningErrors
        Operation type: sites
        Args:
            site_id (str, required): SharePoint site id identifier
            dollar_orderby (List[str], optional): Order items by property values
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = SitesRequestBuilder.SitesRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = SitesRequestBuilder.SitesRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).last_modified_by_user.service_provisioning_errors.get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_list_sites(
        self,
        site_id: str,
        dollar_orderby: Optional[List[str]] = None,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """List subsites for a site.
        SharePoint operation: GET /sites/{site-id}/sites
        Operation type: sites
        Args:
            site_id (str, required): SharePoint site id identifier
            dollar_orderby (List[str], optional): Order items by property values
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = SitesRequestBuilder.SitesRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = SitesRequestBuilder.SitesRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).sites.get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_get_sites(
        self,
        site_id: str,
        site_id1: str,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Get sites from sites.
        SharePoint operation: GET /sites/{site-id}/sites/{site-id1}
        Operation type: sites
        Args:
            site_id (str, required): SharePoint site id identifier
            site_id1 (str, required): SharePoint site id1 identifier
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = SitesRequestBuilder.SitesRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = SitesRequestBuilder.SitesRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).sites.by_site_id(site_id1).get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    # ========== LISTS OPERATIONS (101 methods) ==========

    async def sites_site_get_applicable_content_types_for_list(
        self,
        site_id: str,
        listId: str,
        dollar_select: Optional[List[str]] = None,
        dollar_orderby: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Invoke function getApplicableContentTypesForList.
        SharePoint operation: GET /sites/{site-id}/getApplicableContentTypesForList(listId='{listId}')
        Operation type: lists
        Args:
            site_id (str, required): SharePoint site id identifier
            listId (str, required): SharePoint listId identifier
            dollar_select (List[str], optional): Select properties to be returned
            dollar_orderby (List[str], optional): Order items by property values
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = SitesRequestBuilder.SitesRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = SitesRequestBuilder.SitesRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).get_applicable_content_types_for_list(list_id='{list_id}').get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_site_get_by_path_get_applicable_content_types_for_list(
        self,
        site_id: str,
        path: str,
        listId: str,
        dollar_select: Optional[List[str]] = None,
        dollar_orderby: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Invoke function getApplicableContentTypesForList.
        SharePoint operation: GET /sites/{site-id}/getByPath(path='{path}')/getApplicableContentTypesForList(listId='{listId}')
        Operation type: lists
        Args:
            site_id (str, required): SharePoint site id identifier
            path (str, required): SharePoint path: path
            listId (str, required): SharePoint listId identifier
            dollar_select (List[str], optional): Select properties to be returned
            dollar_orderby (List[str], optional): Order items by property values
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = SitesRequestBuilder.SitesRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = SitesRequestBuilder.SitesRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).get_by_path.get_applicable_content_types_for_list(list_id='{list_id}').get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_get_by_path_list_items(
        self,
        site_id: str,
        path: str,
        dollar_orderby: Optional[List[str]] = None,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Get items from sites.
        SharePoint operation: GET /sites/{site-id}/getByPath(path='{path}')/items
        Operation type: lists
        Args:
            site_id (str, required): SharePoint site id identifier
            path (str, required): SharePoint path: path
            dollar_orderby (List[str], optional): Order items by property values
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = SitesRequestBuilder.SitesRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = SitesRequestBuilder.SitesRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).get_by_path.items.get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_get_by_path_create_lists(
        self,
        site_id: str,
        path: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Create new navigation property to lists for sites.
        SharePoint operation: POST /sites/{site-id}/getByPath(path='{path}')/lists
        Operation type: lists
        Args:
            site_id (str, required): SharePoint site id identifier
            path (str, required): SharePoint path: path
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for SharePoint operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).get_by_path.lists.post(body=request_body, request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_get_by_path_list_lists(
        self,
        site_id: str,
        path: str,
        dollar_orderby: Optional[List[str]] = None,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Get lists from sites.
        SharePoint operation: GET /sites/{site-id}/getByPath(path='{path}')/lists
        Operation type: lists
        Args:
            site_id (str, required): SharePoint site id identifier
            path (str, required): SharePoint path: path
            dollar_orderby (List[str], optional): Order items by property values
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = SitesRequestBuilder.SitesRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = SitesRequestBuilder.SitesRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).get_by_path.lists.get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_list_items(
        self,
        site_id: str,
        dollar_orderby: Optional[List[str]] = None,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Get items from sites.
        SharePoint operation: GET /sites/{site-id}/items
        Operation type: lists
        Args:
            site_id (str, required): SharePoint site id identifier
            dollar_orderby (List[str], optional): Order items by property values
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = SitesRequestBuilder.SitesRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = SitesRequestBuilder.SitesRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).items.get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_get_items(
        self,
        site_id: str,
        baseItem_id: str,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Get items from sites.
        SharePoint operation: GET /sites/{site-id}/items/{baseItem-id}
        Operation type: lists
        Args:
            site_id (str, required): SharePoint site id identifier
            baseItem_id (str, required): SharePoint baseItem id identifier
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = SitesRequestBuilder.SitesRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = SitesRequestBuilder.SitesRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).items.by_list_item_id(baseItem_id).get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_create_lists(
        self,
        site_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Create a new list.
        SharePoint operation: POST /sites/{site-id}/lists
        Operation type: lists
        Args:
            site_id (str, required): SharePoint site id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for SharePoint operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).lists.post(body=request_body, request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_list_lists(
        self,
        site_id: str,
        dollar_orderby: Optional[List[str]] = None,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Get lists in a site.
        SharePoint operation: GET /sites/{site-id}/lists
        Operation type: lists
        Args:
            site_id (str, required): SharePoint site id identifier
            dollar_orderby (List[str], optional): Order items by property values
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = ListsRequestBuilder.ListsRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = ListsRequestBuilder.ListsRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).lists.get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_delete_lists(
        self,
        site_id: str,
        list_id: str,
        If_Match: Optional[str] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Delete navigation property lists for sites.
        SharePoint operation: DELETE /sites/{site-id}/lists/{list-id}
        Operation type: lists
        Args:
            site_id (str, required): SharePoint site id identifier
            list_id (str, required): SharePoint list id identifier
            If_Match (str, optional): ETag
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).lists.by_list_id(list_id).delete(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_get_lists(
        self,
        site_id: str,
        list_id: str,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """List operations on a list.
        SharePoint operation: GET /sites/{site-id}/lists/{list-id}
        Operation type: lists
        Args:
            site_id (str, required): SharePoint site id identifier
            list_id (str, required): SharePoint list id identifier
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = ListsRequestBuilder.ListsRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = ListsRequestBuilder.ListsRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).lists.by_list_id(list_id).get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_update_lists(
        self,
        site_id: str,
        list_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Update the navigation property lists in sites.
        SharePoint operation: PATCH /sites/{site-id}/lists/{list-id}
        Operation type: lists
        Args:
            site_id (str, required): SharePoint site id identifier
            list_id (str, required): SharePoint list id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for SharePoint operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).lists.by_list_id(list_id).patch(body=request_body, request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_lists_create_columns(
        self,
        site_id: str,
        list_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Create a columnDefinition in a list.
        SharePoint operation: POST /sites/{site-id}/lists/{list-id}/columns
        Operation type: lists
        Args:
            site_id (str, required): SharePoint site id identifier
            list_id (str, required): SharePoint list id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for SharePoint operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).lists.by_list_id(list_id).columns.post(body=request_body, request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_lists_list_columns(
        self,
        site_id: str,
        list_id: str,
        dollar_orderby: Optional[List[str]] = None,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """List columnDefinitions in a list.
        SharePoint operation: GET /sites/{site-id}/lists/{list-id}/columns
        Operation type: lists
        Args:
            site_id (str, required): SharePoint site id identifier
            list_id (str, required): SharePoint list id identifier
            dollar_orderby (List[str], optional): Order items by property values
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = ListsRequestBuilder.ListsRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = ListsRequestBuilder.ListsRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).lists.by_list_id(list_id).columns.get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_lists_delete_columns(
        self,
        site_id: str,
        list_id: str,
        columnDefinition_id: str,
        If_Match: Optional[str] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Delete navigation property columns for sites.
        SharePoint operation: DELETE /sites/{site-id}/lists/{list-id}/columns/{columnDefinition-id}
        Operation type: lists
        Args:
            site_id (str, required): SharePoint site id identifier
            list_id (str, required): SharePoint list id identifier
            columnDefinition_id (str, required): SharePoint columnDefinition id identifier
            If_Match (str, optional): ETag
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).lists.by_list_id(list_id).columns.by_column_definition_id(columnDefinition_id).delete(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_lists_get_columns(
        self,
        site_id: str,
        list_id: str,
        columnDefinition_id: str,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Get columns from sites.
        SharePoint operation: GET /sites/{site-id}/lists/{list-id}/columns/{columnDefinition-id}
        Operation type: lists
        Args:
            site_id (str, required): SharePoint site id identifier
            list_id (str, required): SharePoint list id identifier
            columnDefinition_id (str, required): SharePoint columnDefinition id identifier
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = ListsRequestBuilder.ListsRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = ListsRequestBuilder.ListsRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).lists.by_list_id(list_id).columns.by_column_definition_id(columnDefinition_id).get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_lists_update_columns(
        self,
        site_id: str,
        list_id: str,
        columnDefinition_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Update the navigation property columns in sites.
        SharePoint operation: PATCH /sites/{site-id}/lists/{list-id}/columns/{columnDefinition-id}
        Operation type: lists
        Args:
            site_id (str, required): SharePoint site id identifier
            list_id (str, required): SharePoint list id identifier
            columnDefinition_id (str, required): SharePoint columnDefinition id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for SharePoint operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).lists.by_list_id(list_id).columns.by_column_definition_id(columnDefinition_id).patch(body=request_body, request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_lists_columns_get_source_column(
        self,
        site_id: str,
        list_id: str,
        columnDefinition_id: str,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Get sourceColumn from sites.
        SharePoint operation: GET /sites/{site-id}/lists/{list-id}/columns/{columnDefinition-id}/sourceColumn
        Operation type: lists
        Args:
            site_id (str, required): SharePoint site id identifier
            list_id (str, required): SharePoint list id identifier
            columnDefinition_id (str, required): SharePoint columnDefinition id identifier
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = ListsRequestBuilder.ListsRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = ListsRequestBuilder.ListsRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).lists.by_list_id(list_id).columns.by_column_definition_id(columnDefinition_id).source_column.get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_lists_create_content_types(
        self,
        site_id: str,
        list_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Create new navigation property to contentTypes for sites.
        SharePoint operation: POST /sites/{site-id}/lists/{list-id}/contentTypes
        Operation type: lists
        Args:
            site_id (str, required): SharePoint site id identifier
            list_id (str, required): SharePoint list id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for SharePoint operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).lists.by_list_id(list_id).content_types.post(body=request_body, request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_lists_list_content_types(
        self,
        site_id: str,
        list_id: str,
        dollar_orderby: Optional[List[str]] = None,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """List contentTypes in a list.
        SharePoint operation: GET /sites/{site-id}/lists/{list-id}/contentTypes
        Operation type: lists
        Args:
            site_id (str, required): SharePoint site id identifier
            list_id (str, required): SharePoint list id identifier
            dollar_orderby (List[str], optional): Order items by property values
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = ListsRequestBuilder.ListsRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = ListsRequestBuilder.ListsRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).lists.by_list_id(list_id).content_types.get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_site_lists_list_content_types_add_copy(
        self,
        site_id: str,
        list_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Invoke action addCopy.
        SharePoint operation: POST /sites/{site-id}/lists/{list-id}/contentTypes/addCopy
        Operation type: lists
        Args:
            site_id (str, required): SharePoint site id identifier
            list_id (str, required): SharePoint list id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for SharePoint operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).lists.by_list_id(list_id).content_types.add_copy.post(body=request_body, request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_site_lists_list_content_types_add_copy_from_content_type_hub(
        self,
        site_id: str,
        list_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Invoke action addCopyFromContentTypeHub.
        SharePoint operation: POST /sites/{site-id}/lists/{list-id}/contentTypes/addCopyFromContentTypeHub
        Operation type: lists
        Args:
            site_id (str, required): SharePoint site id identifier
            list_id (str, required): SharePoint list id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for SharePoint operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).lists.by_list_id(list_id).content_types.add_copy_from_content_type_hub.post(body=request_body, request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_site_lists_list_content_types_get_compatible_hub_content_types(
        self,
        site_id: str,
        list_id: str,
        dollar_select: Optional[List[str]] = None,
        dollar_orderby: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Invoke function getCompatibleHubContentTypes.
        SharePoint operation: GET /sites/{site-id}/lists/{list-id}/contentTypes/getCompatibleHubContentTypes()
        Operation type: lists
        Args:
            site_id (str, required): SharePoint site id identifier
            list_id (str, required): SharePoint list id identifier
            dollar_select (List[str], optional): Select properties to be returned
            dollar_orderby (List[str], optional): Order items by property values
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = ListsRequestBuilder.ListsRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = ListsRequestBuilder.ListsRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).lists.by_list_id(list_id).content_types.get_compatible_hub_content_types().get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_lists_delete_content_types(
        self,
        site_id: str,
        list_id: str,
        contentType_id: str,
        If_Match: Optional[str] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Delete navigation property contentTypes for sites.
        SharePoint operation: DELETE /sites/{site-id}/lists/{list-id}/contentTypes/{contentType-id}
        Operation type: lists
        Args:
            site_id (str, required): SharePoint site id identifier
            list_id (str, required): SharePoint list id identifier
            contentType_id (str, required): SharePoint contentType id identifier
            If_Match (str, optional): ETag
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).lists.by_list_id(list_id).content_types.by_content_type_id(contentType_id).delete(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_lists_get_content_types(
        self,
        site_id: str,
        list_id: str,
        contentType_id: str,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Get contentTypes from sites.
        SharePoint operation: GET /sites/{site-id}/lists/{list-id}/contentTypes/{contentType-id}
        Operation type: lists
        Args:
            site_id (str, required): SharePoint site id identifier
            list_id (str, required): SharePoint list id identifier
            contentType_id (str, required): SharePoint contentType id identifier
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = ListsRequestBuilder.ListsRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = ListsRequestBuilder.ListsRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).lists.by_list_id(list_id).content_types.by_content_type_id(contentType_id).get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_lists_update_content_types(
        self,
        site_id: str,
        list_id: str,
        contentType_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Update the navigation property contentTypes in sites.
        SharePoint operation: PATCH /sites/{site-id}/lists/{list-id}/contentTypes/{contentType-id}
        Operation type: lists
        Args:
            site_id (str, required): SharePoint site id identifier
            list_id (str, required): SharePoint list id identifier
            contentType_id (str, required): SharePoint contentType id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for SharePoint operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).lists.by_list_id(list_id).content_types.by_content_type_id(contentType_id).patch(body=request_body, request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_site_lists_list_content_types_content_type_associate_with_hub_sites(
        self,
        site_id: str,
        list_id: str,
        contentType_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Invoke action associateWithHubSites.
        SharePoint operation: POST /sites/{site-id}/lists/{list-id}/contentTypes/{contentType-id}/associateWithHubSites
        Operation type: lists
        Args:
            site_id (str, required): SharePoint site id identifier
            list_id (str, required): SharePoint list id identifier
            contentType_id (str, required): SharePoint contentType id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for SharePoint operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).lists.by_list_id(list_id).content_types.by_content_type_id(contentType_id).associate_with_hub_sites.post(body=request_body, request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_lists_content_types_get_base(
        self,
        site_id: str,
        list_id: str,
        contentType_id: str,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Get base from sites.
        SharePoint operation: GET /sites/{site-id}/lists/{list-id}/contentTypes/{contentType-id}/base
        Operation type: lists
        Args:
            site_id (str, required): SharePoint site id identifier
            list_id (str, required): SharePoint list id identifier
            contentType_id (str, required): SharePoint contentType id identifier
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = ListsRequestBuilder.ListsRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = ListsRequestBuilder.ListsRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).lists.by_list_id(list_id).content_types.by_content_type_id(contentType_id).base.get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_lists_content_types_list_base_types(
        self,
        site_id: str,
        list_id: str,
        contentType_id: str,
        dollar_orderby: Optional[List[str]] = None,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Get baseTypes from sites.
        SharePoint operation: GET /sites/{site-id}/lists/{list-id}/contentTypes/{contentType-id}/baseTypes
        Operation type: lists
        Args:
            site_id (str, required): SharePoint site id identifier
            list_id (str, required): SharePoint list id identifier
            contentType_id (str, required): SharePoint contentType id identifier
            dollar_orderby (List[str], optional): Order items by property values
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = ListsRequestBuilder.ListsRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = ListsRequestBuilder.ListsRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).lists.by_list_id(list_id).content_types.by_content_type_id(contentType_id).base_types.get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_lists_content_types_get_base_types(
        self,
        site_id: str,
        list_id: str,
        contentType_id: str,
        contentType_id1: str,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Get baseTypes from sites.
        SharePoint operation: GET /sites/{site-id}/lists/{list-id}/contentTypes/{contentType-id}/baseTypes/{contentType-id1}
        Operation type: lists
        Args:
            site_id (str, required): SharePoint site id identifier
            list_id (str, required): SharePoint list id identifier
            contentType_id (str, required): SharePoint contentType id identifier
            contentType_id1 (str, required): SharePoint contentType id1 identifier
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = ListsRequestBuilder.ListsRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = ListsRequestBuilder.ListsRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).lists.by_list_id(list_id).content_types.by_content_type_id(contentType_id).base_types.by_baseType_id(contentType_id1).get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_lists_content_types_create_column_links(
        self,
        site_id: str,
        list_id: str,
        contentType_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Create new navigation property to columnLinks for sites.
        SharePoint operation: POST /sites/{site-id}/lists/{list-id}/contentTypes/{contentType-id}/columnLinks
        Operation type: lists
        Args:
            site_id (str, required): SharePoint site id identifier
            list_id (str, required): SharePoint list id identifier
            contentType_id (str, required): SharePoint contentType id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for SharePoint operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).lists.by_list_id(list_id).content_types.by_content_type_id(contentType_id).column_links.post(body=request_body, request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_lists_content_types_list_column_links(
        self,
        site_id: str,
        list_id: str,
        contentType_id: str,
        dollar_orderby: Optional[List[str]] = None,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Get columnLinks from sites.
        SharePoint operation: GET /sites/{site-id}/lists/{list-id}/contentTypes/{contentType-id}/columnLinks
        Operation type: lists
        Args:
            site_id (str, required): SharePoint site id identifier
            list_id (str, required): SharePoint list id identifier
            contentType_id (str, required): SharePoint contentType id identifier
            dollar_orderby (List[str], optional): Order items by property values
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = ListsRequestBuilder.ListsRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = ListsRequestBuilder.ListsRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).lists.by_list_id(list_id).content_types.by_content_type_id(contentType_id).column_links.get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_lists_content_types_delete_column_links(
        self,
        site_id: str,
        list_id: str,
        contentType_id: str,
        columnLink_id: str,
        If_Match: Optional[str] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Delete navigation property columnLinks for sites.
        SharePoint operation: DELETE /sites/{site-id}/lists/{list-id}/contentTypes/{contentType-id}/columnLinks/{columnLink-id}
        Operation type: lists
        Args:
            site_id (str, required): SharePoint site id identifier
            list_id (str, required): SharePoint list id identifier
            contentType_id (str, required): SharePoint contentType id identifier
            columnLink_id (str, required): SharePoint columnLink id identifier
            If_Match (str, optional): ETag
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).lists.by_list_id(list_id).content_types.by_content_type_id(contentType_id).column_links.by_column_link_id(columnLink_id).delete(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_lists_content_types_get_column_links(
        self,
        site_id: str,
        list_id: str,
        contentType_id: str,
        columnLink_id: str,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Get columnLinks from sites.
        SharePoint operation: GET /sites/{site-id}/lists/{list-id}/contentTypes/{contentType-id}/columnLinks/{columnLink-id}
        Operation type: lists
        Args:
            site_id (str, required): SharePoint site id identifier
            list_id (str, required): SharePoint list id identifier
            contentType_id (str, required): SharePoint contentType id identifier
            columnLink_id (str, required): SharePoint columnLink id identifier
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = ListsRequestBuilder.ListsRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = ListsRequestBuilder.ListsRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).lists.by_list_id(list_id).content_types.by_content_type_id(contentType_id).column_links.by_column_link_id(columnLink_id).get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_lists_content_types_update_column_links(
        self,
        site_id: str,
        list_id: str,
        contentType_id: str,
        columnLink_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Update the navigation property columnLinks in sites.
        SharePoint operation: PATCH /sites/{site-id}/lists/{list-id}/contentTypes/{contentType-id}/columnLinks/{columnLink-id}
        Operation type: lists
        Args:
            site_id (str, required): SharePoint site id identifier
            list_id (str, required): SharePoint list id identifier
            contentType_id (str, required): SharePoint contentType id identifier
            columnLink_id (str, required): SharePoint columnLink id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for SharePoint operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).lists.by_list_id(list_id).content_types.by_content_type_id(contentType_id).column_links.by_column_link_id(columnLink_id).patch(body=request_body, request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_lists_content_types_list_column_positions(
        self,
        site_id: str,
        list_id: str,
        contentType_id: str,
        dollar_orderby: Optional[List[str]] = None,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Get columnPositions from sites.
        SharePoint operation: GET /sites/{site-id}/lists/{list-id}/contentTypes/{contentType-id}/columnPositions
        Operation type: lists
        Args:
            site_id (str, required): SharePoint site id identifier
            list_id (str, required): SharePoint list id identifier
            contentType_id (str, required): SharePoint contentType id identifier
            dollar_orderby (List[str], optional): Order items by property values
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = ListsRequestBuilder.ListsRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = ListsRequestBuilder.ListsRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).lists.by_list_id(list_id).content_types.by_content_type_id(contentType_id).column_positions.get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_lists_content_types_get_column_positions(
        self,
        site_id: str,
        list_id: str,
        contentType_id: str,
        columnDefinition_id: str,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Get columnPositions from sites.
        SharePoint operation: GET /sites/{site-id}/lists/{list-id}/contentTypes/{contentType-id}/columnPositions/{columnDefinition-id}
        Operation type: lists
        Args:
            site_id (str, required): SharePoint site id identifier
            list_id (str, required): SharePoint list id identifier
            contentType_id (str, required): SharePoint contentType id identifier
            columnDefinition_id (str, required): SharePoint columnDefinition id identifier
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = ListsRequestBuilder.ListsRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = ListsRequestBuilder.ListsRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).lists.by_list_id(list_id).content_types.by_content_type_id(contentType_id).column_positions.by_columnPosition_id(columnDefinition_id).get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_lists_content_types_create_columns(
        self,
        site_id: str,
        list_id: str,
        contentType_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Create new navigation property to columns for sites.
        SharePoint operation: POST /sites/{site-id}/lists/{list-id}/contentTypes/{contentType-id}/columns
        Operation type: lists
        Args:
            site_id (str, required): SharePoint site id identifier
            list_id (str, required): SharePoint list id identifier
            contentType_id (str, required): SharePoint contentType id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for SharePoint operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).lists.by_list_id(list_id).content_types.by_content_type_id(contentType_id).columns.post(body=request_body, request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_lists_content_types_list_columns(
        self,
        site_id: str,
        list_id: str,
        contentType_id: str,
        dollar_orderby: Optional[List[str]] = None,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Get columns from sites.
        SharePoint operation: GET /sites/{site-id}/lists/{list-id}/contentTypes/{contentType-id}/columns
        Operation type: lists
        Args:
            site_id (str, required): SharePoint site id identifier
            list_id (str, required): SharePoint list id identifier
            contentType_id (str, required): SharePoint contentType id identifier
            dollar_orderby (List[str], optional): Order items by property values
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = ListsRequestBuilder.ListsRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = ListsRequestBuilder.ListsRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).lists.by_list_id(list_id).content_types.by_content_type_id(contentType_id).columns.get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_lists_content_types_delete_columns(
        self,
        site_id: str,
        list_id: str,
        contentType_id: str,
        columnDefinition_id: str,
        If_Match: Optional[str] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Delete navigation property columns for sites.
        SharePoint operation: DELETE /sites/{site-id}/lists/{list-id}/contentTypes/{contentType-id}/columns/{columnDefinition-id}
        Operation type: lists
        Args:
            site_id (str, required): SharePoint site id identifier
            list_id (str, required): SharePoint list id identifier
            contentType_id (str, required): SharePoint contentType id identifier
            columnDefinition_id (str, required): SharePoint columnDefinition id identifier
            If_Match (str, optional): ETag
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).lists.by_list_id(list_id).content_types.by_content_type_id(contentType_id).columns.by_column_definition_id(columnDefinition_id).delete(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_lists_content_types_get_columns(
        self,
        site_id: str,
        list_id: str,
        contentType_id: str,
        columnDefinition_id: str,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Get columns from sites.
        SharePoint operation: GET /sites/{site-id}/lists/{list-id}/contentTypes/{contentType-id}/columns/{columnDefinition-id}
        Operation type: lists
        Args:
            site_id (str, required): SharePoint site id identifier
            list_id (str, required): SharePoint list id identifier
            contentType_id (str, required): SharePoint contentType id identifier
            columnDefinition_id (str, required): SharePoint columnDefinition id identifier
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = ListsRequestBuilder.ListsRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = ListsRequestBuilder.ListsRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).lists.by_list_id(list_id).content_types.by_content_type_id(contentType_id).columns.by_column_definition_id(columnDefinition_id).get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_lists_content_types_update_columns(
        self,
        site_id: str,
        list_id: str,
        contentType_id: str,
        columnDefinition_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Update the navigation property columns in sites.
        SharePoint operation: PATCH /sites/{site-id}/lists/{list-id}/contentTypes/{contentType-id}/columns/{columnDefinition-id}
        Operation type: lists
        Args:
            site_id (str, required): SharePoint site id identifier
            list_id (str, required): SharePoint list id identifier
            contentType_id (str, required): SharePoint contentType id identifier
            columnDefinition_id (str, required): SharePoint columnDefinition id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for SharePoint operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).lists.by_list_id(list_id).content_types.by_content_type_id(contentType_id).columns.by_column_definition_id(columnDefinition_id).patch(body=request_body, request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_lists_content_types_columns_get_source_column(
        self,
        site_id: str,
        list_id: str,
        contentType_id: str,
        columnDefinition_id: str,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Get sourceColumn from sites.
        SharePoint operation: GET /sites/{site-id}/lists/{list-id}/contentTypes/{contentType-id}/columns/{columnDefinition-id}/sourceColumn
        Operation type: lists
        Args:
            site_id (str, required): SharePoint site id identifier
            list_id (str, required): SharePoint list id identifier
            contentType_id (str, required): SharePoint contentType id identifier
            columnDefinition_id (str, required): SharePoint columnDefinition id identifier
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = ListsRequestBuilder.ListsRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = ListsRequestBuilder.ListsRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).lists.by_list_id(list_id).content_types.by_content_type_id(contentType_id).columns.by_column_definition_id(columnDefinition_id).source_column.get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_site_lists_list_content_types_content_type_copy_to_default_content_location(
        self,
        site_id: str,
        list_id: str,
        contentType_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Invoke action copyToDefaultContentLocation.
        SharePoint operation: POST /sites/{site-id}/lists/{list-id}/contentTypes/{contentType-id}/copyToDefaultContentLocation
        Operation type: lists
        Args:
            site_id (str, required): SharePoint site id identifier
            list_id (str, required): SharePoint list id identifier
            contentType_id (str, required): SharePoint contentType id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for SharePoint operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).lists.by_list_id(list_id).content_types.by_content_type_id(contentType_id).copy_to_default_content_location.post(body=request_body, request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_site_lists_list_content_types_content_type_is_published(
        self,
        site_id: str,
        list_id: str,
        contentType_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Invoke function isPublished.
        SharePoint operation: GET /sites/{site-id}/lists/{list-id}/contentTypes/{contentType-id}/isPublished()
        Operation type: lists
        Args:
            site_id (str, required): SharePoint site id identifier
            list_id (str, required): SharePoint list id identifier
            contentType_id (str, required): SharePoint contentType id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = ListsRequestBuilder.ListsRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = ListsRequestBuilder.ListsRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).lists.by_list_id(list_id).content_types.by_content_type_id(contentType_id).is_published().get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_site_lists_list_content_types_content_type_publish(
        self,
        site_id: str,
        list_id: str,
        contentType_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Invoke action publish.
        SharePoint operation: POST /sites/{site-id}/lists/{list-id}/contentTypes/{contentType-id}/publish
        Operation type: lists
        Args:
            site_id (str, required): SharePoint site id identifier
            list_id (str, required): SharePoint list id identifier
            contentType_id (str, required): SharePoint contentType id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).lists.by_list_id(list_id).content_types.by_content_type_id(contentType_id).publish.post(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_site_lists_list_content_types_content_type_unpublish(
        self,
        site_id: str,
        list_id: str,
        contentType_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Invoke action unpublish.
        SharePoint operation: POST /sites/{site-id}/lists/{list-id}/contentTypes/{contentType-id}/unpublish
        Operation type: lists
        Args:
            site_id (str, required): SharePoint site id identifier
            list_id (str, required): SharePoint list id identifier
            contentType_id (str, required): SharePoint contentType id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).lists.by_list_id(list_id).content_types.by_content_type_id(contentType_id).unpublish.post(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_lists_created_by_user_update_mailbox_settings(
        self,
        site_id: str,
        list_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Update property mailboxSettings value..
        SharePoint operation: PATCH /sites/{site-id}/lists/{list-id}/createdByUser/mailboxSettings
        Operation type: lists
        Args:
            site_id (str, required): SharePoint site id identifier
            list_id (str, required): SharePoint list id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for SharePoint operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).lists.by_list_id(list_id).created_by_user.mailbox_settings.patch(body=request_body, request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_lists_created_by_user_list_service_provisioning_errors(
        self,
        site_id: str,
        list_id: str,
        dollar_orderby: Optional[List[str]] = None,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Get serviceProvisioningErrors property value.
        SharePoint operation: GET /sites/{site-id}/lists/{list-id}/createdByUser/serviceProvisioningErrors
        Operation type: lists
        Args:
            site_id (str, required): SharePoint site id identifier
            list_id (str, required): SharePoint list id identifier
            dollar_orderby (List[str], optional): Order items by property values
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = ListsRequestBuilder.ListsRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = ListsRequestBuilder.ListsRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).lists.by_list_id(list_id).created_by_user.service_provisioning_errors.get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_lists_get_drive(
        self,
        site_id: str,
        list_id: str,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Get drive from sites.
        SharePoint operation: GET /sites/{site-id}/lists/{list-id}/drive
        Operation type: lists
        Args:
            site_id (str, required): SharePoint site id identifier
            list_id (str, required): SharePoint list id identifier
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = ListsRequestBuilder.ListsRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = ListsRequestBuilder.ListsRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).lists.by_list_id(list_id).drive.get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_lists_create_items(
        self,
        site_id: str,
        list_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Create a new item in a list.
        SharePoint operation: POST /sites/{site-id}/lists/{list-id}/items
        Operation type: lists
        Args:
            site_id (str, required): SharePoint site id identifier
            list_id (str, required): SharePoint list id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for SharePoint operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).lists.by_list_id(list_id).items.post(body=request_body, request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_lists_list_items(
        self,
        site_id: str,
        list_id: str,
        dollar_orderby: Optional[List[str]] = None,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """List items.
        SharePoint operation: GET /sites/{site-id}/lists/{list-id}/items
        Operation type: lists
        Args:
            site_id (str, required): SharePoint site id identifier
            list_id (str, required): SharePoint list id identifier
            dollar_orderby (List[str], optional): Order items by property values
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = ListsRequestBuilder.ListsRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = ListsRequestBuilder.ListsRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).lists.by_list_id(list_id).items.get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_site_lists_list_items_delta_fa14(
        self,
        site_id: str,
        list_id: str,
        dollar_select: Optional[List[str]] = None,
        dollar_orderby: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Invoke function delta.
        SharePoint operation: GET /sites/{site-id}/lists/{list-id}/items/delta()
        Operation type: lists
        Args:
            site_id (str, required): SharePoint site id identifier
            list_id (str, required): SharePoint list id identifier
            dollar_select (List[str], optional): Select properties to be returned
            dollar_orderby (List[str], optional): Order items by property values
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = ListsRequestBuilder.ListsRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = ListsRequestBuilder.ListsRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).lists.by_list_id(list_id).items.delta().get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_site_lists_list_items_delta_9846(
        self,
        site_id: str,
        list_id: str,
        token: str,
        dollar_select: Optional[List[str]] = None,
        dollar_orderby: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Invoke function delta.
        SharePoint operation: GET /sites/{site-id}/lists/{list-id}/items/delta(token='{token}')
        Operation type: lists
        Args:
            site_id (str, required): SharePoint site id identifier
            list_id (str, required): SharePoint list id identifier
            token (str, required): SharePoint path parameter: token
            dollar_select (List[str], optional): Select properties to be returned
            dollar_orderby (List[str], optional): Order items by property values
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = ListsRequestBuilder.ListsRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = ListsRequestBuilder.ListsRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).lists.by_list_id(list_id).items.delta(token='{token}').get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_lists_delete_items(
        self,
        site_id: str,
        list_id: str,
        listItem_id: str,
        If_Match: Optional[str] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Delete an item from a list.
        SharePoint operation: DELETE /sites/{site-id}/lists/{list-id}/items/{listItem-id}
        Operation type: lists
        Args:
            site_id (str, required): SharePoint site id identifier
            list_id (str, required): SharePoint list id identifier
            listItem_id (str, required): SharePoint listItem id identifier
            If_Match (str, optional): ETag
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).lists.by_list_id(list_id).items.by_list_item_id(listItem_id).delete(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_lists_get_items(
        self,
        site_id: str,
        list_id: str,
        listItem_id: str,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Get listItem.
        SharePoint operation: GET /sites/{site-id}/lists/{list-id}/items/{listItem-id}
        Operation type: lists
        Args:
            site_id (str, required): SharePoint site id identifier
            list_id (str, required): SharePoint list id identifier
            listItem_id (str, required): SharePoint listItem id identifier
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = ListsRequestBuilder.ListsRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = ListsRequestBuilder.ListsRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).lists.by_list_id(list_id).items.by_list_item_id(listItem_id).get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_lists_update_items(
        self,
        site_id: str,
        list_id: str,
        listItem_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Update the navigation property items in sites.
        SharePoint operation: PATCH /sites/{site-id}/lists/{list-id}/items/{listItem-id}
        Operation type: lists
        Args:
            site_id (str, required): SharePoint site id identifier
            list_id (str, required): SharePoint list id identifier
            listItem_id (str, required): SharePoint listItem id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for SharePoint operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).lists.by_list_id(list_id).items.by_list_item_id(listItem_id).patch(body=request_body, request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_lists_items_get_analytics(
        self,
        site_id: str,
        list_id: str,
        listItem_id: str,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Get analytics from sites.
        SharePoint operation: GET /sites/{site-id}/lists/{list-id}/items/{listItem-id}/analytics
        Operation type: lists
        Args:
            site_id (str, required): SharePoint site id identifier
            list_id (str, required): SharePoint list id identifier
            listItem_id (str, required): SharePoint listItem id identifier
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = ListsRequestBuilder.ListsRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = ListsRequestBuilder.ListsRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).lists.by_list_id(list_id).items.by_list_item_id(listItem_id).analytics.get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_site_lists_list_items_list_item_create_link(
        self,
        site_id: str,
        list_id: str,
        listItem_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Invoke action createLink.
        SharePoint operation: POST /sites/{site-id}/lists/{list-id}/items/{listItem-id}/createLink
        Operation type: lists
        Args:
            site_id (str, required): SharePoint site id identifier
            list_id (str, required): SharePoint list id identifier
            listItem_id (str, required): SharePoint listItem id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for SharePoint operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).lists.by_list_id(list_id).items.by_list_item_id(listItem_id).create_link.post(body=request_body, request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_lists_items_created_by_user_update_mailbox_settings(
        self,
        site_id: str,
        list_id: str,
        listItem_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Update property mailboxSettings value..
        SharePoint operation: PATCH /sites/{site-id}/lists/{list-id}/items/{listItem-id}/createdByUser/mailboxSettings
        Operation type: lists
        Args:
            site_id (str, required): SharePoint site id identifier
            list_id (str, required): SharePoint list id identifier
            listItem_id (str, required): SharePoint listItem id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for SharePoint operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).lists.by_list_id(list_id).items.by_list_item_id(listItem_id).created_by_user.mailbox_settings.patch(body=request_body, request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_lists_items_created_by_user_list_service_provisioning_errors(
        self,
        site_id: str,
        list_id: str,
        listItem_id: str,
        dollar_orderby: Optional[List[str]] = None,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Get serviceProvisioningErrors property value.
        SharePoint operation: GET /sites/{site-id}/lists/{list-id}/items/{listItem-id}/createdByUser/serviceProvisioningErrors
        Operation type: lists
        Args:
            site_id (str, required): SharePoint site id identifier
            list_id (str, required): SharePoint list id identifier
            listItem_id (str, required): SharePoint listItem id identifier
            dollar_orderby (List[str], optional): Order items by property values
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = ListsRequestBuilder.ListsRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = ListsRequestBuilder.ListsRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).lists.by_list_id(list_id).items.by_list_item_id(listItem_id).created_by_user.service_provisioning_errors.get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_lists_items_create_document_set_versions(
        self,
        site_id: str,
        list_id: str,
        listItem_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Create documentSetVersion.
        SharePoint operation: POST /sites/{site-id}/lists/{list-id}/items/{listItem-id}/documentSetVersions
        Operation type: lists
        Args:
            site_id (str, required): SharePoint site id identifier
            list_id (str, required): SharePoint list id identifier
            listItem_id (str, required): SharePoint listItem id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for SharePoint operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).lists.by_list_id(list_id).items.by_list_item_id(listItem_id).document_set_versions.post(body=request_body, request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_lists_items_list_document_set_versions(
        self,
        site_id: str,
        list_id: str,
        listItem_id: str,
        dollar_orderby: Optional[List[str]] = None,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """List documentSetVersions.
        SharePoint operation: GET /sites/{site-id}/lists/{list-id}/items/{listItem-id}/documentSetVersions
        Operation type: lists
        Args:
            site_id (str, required): SharePoint site id identifier
            list_id (str, required): SharePoint list id identifier
            listItem_id (str, required): SharePoint listItem id identifier
            dollar_orderby (List[str], optional): Order items by property values
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = ListsRequestBuilder.ListsRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = ListsRequestBuilder.ListsRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).lists.by_list_id(list_id).items.by_list_item_id(listItem_id).document_set_versions.get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_lists_items_delete_document_set_versions(
        self,
        site_id: str,
        list_id: str,
        listItem_id: str,
        documentSetVersion_id: str,
        If_Match: Optional[str] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Delete documentSetVersion.
        SharePoint operation: DELETE /sites/{site-id}/lists/{list-id}/items/{listItem-id}/documentSetVersions/{documentSetVersion-id}
        Operation type: lists
        Args:
            site_id (str, required): SharePoint site id identifier
            list_id (str, required): SharePoint list id identifier
            listItem_id (str, required): SharePoint listItem id identifier
            documentSetVersion_id (str, required): SharePoint documentSetVersion id identifier
            If_Match (str, optional): ETag
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).lists.by_list_id(list_id).items.by_list_item_id(listItem_id).document_set_versions.by_documentSetVersion_id(documentSetVersion_id).delete(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_lists_items_get_document_set_versions(
        self,
        site_id: str,
        list_id: str,
        listItem_id: str,
        documentSetVersion_id: str,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Get documentSetVersion.
        SharePoint operation: GET /sites/{site-id}/lists/{list-id}/items/{listItem-id}/documentSetVersions/{documentSetVersion-id}
        Operation type: lists
        Args:
            site_id (str, required): SharePoint site id identifier
            list_id (str, required): SharePoint list id identifier
            listItem_id (str, required): SharePoint listItem id identifier
            documentSetVersion_id (str, required): SharePoint documentSetVersion id identifier
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = ListsRequestBuilder.ListsRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = ListsRequestBuilder.ListsRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).lists.by_list_id(list_id).items.by_list_item_id(listItem_id).document_set_versions.by_documentSetVersion_id(documentSetVersion_id).get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_lists_items_update_document_set_versions(
        self,
        site_id: str,
        list_id: str,
        listItem_id: str,
        documentSetVersion_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Update the navigation property documentSetVersions in sites.
        SharePoint operation: PATCH /sites/{site-id}/lists/{list-id}/items/{listItem-id}/documentSetVersions/{documentSetVersion-id}
        Operation type: lists
        Args:
            site_id (str, required): SharePoint site id identifier
            list_id (str, required): SharePoint list id identifier
            listItem_id (str, required): SharePoint listItem id identifier
            documentSetVersion_id (str, required): SharePoint documentSetVersion id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for SharePoint operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).lists.by_list_id(list_id).items.by_list_item_id(listItem_id).document_set_versions.by_documentSetVersion_id(documentSetVersion_id).patch(body=request_body, request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_lists_items_document_set_versions_delete_fields(
        self,
        site_id: str,
        list_id: str,
        listItem_id: str,
        documentSetVersion_id: str,
        If_Match: Optional[str] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Delete navigation property fields for sites.
        SharePoint operation: DELETE /sites/{site-id}/lists/{list-id}/items/{listItem-id}/documentSetVersions/{documentSetVersion-id}/fields
        Operation type: lists
        Args:
            site_id (str, required): SharePoint site id identifier
            list_id (str, required): SharePoint list id identifier
            listItem_id (str, required): SharePoint listItem id identifier
            documentSetVersion_id (str, required): SharePoint documentSetVersion id identifier
            If_Match (str, optional): ETag
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).lists.by_list_id(list_id).items.by_list_item_id(listItem_id).document_set_versions.by_documentSetVersion_id(documentSetVersion_id).fields.delete(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_lists_items_document_set_versions_get_fields(
        self,
        site_id: str,
        list_id: str,
        listItem_id: str,
        documentSetVersion_id: str,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Get fields from sites.
        SharePoint operation: GET /sites/{site-id}/lists/{list-id}/items/{listItem-id}/documentSetVersions/{documentSetVersion-id}/fields
        Operation type: lists
        Args:
            site_id (str, required): SharePoint site id identifier
            list_id (str, required): SharePoint list id identifier
            listItem_id (str, required): SharePoint listItem id identifier
            documentSetVersion_id (str, required): SharePoint documentSetVersion id identifier
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = ListsRequestBuilder.ListsRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = ListsRequestBuilder.ListsRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).lists.by_list_id(list_id).items.by_list_item_id(listItem_id).document_set_versions.by_documentSetVersion_id(documentSetVersion_id).fields.get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_lists_items_document_set_versions_update_fields(
        self,
        site_id: str,
        list_id: str,
        listItem_id: str,
        documentSetVersion_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Update the navigation property fields in sites.
        SharePoint operation: PATCH /sites/{site-id}/lists/{list-id}/items/{listItem-id}/documentSetVersions/{documentSetVersion-id}/fields
        Operation type: lists
        Args:
            site_id (str, required): SharePoint site id identifier
            list_id (str, required): SharePoint list id identifier
            listItem_id (str, required): SharePoint listItem id identifier
            documentSetVersion_id (str, required): SharePoint documentSetVersion id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for SharePoint operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).lists.by_list_id(list_id).items.by_list_item_id(listItem_id).document_set_versions.by_documentSetVersion_id(documentSetVersion_id).fields.patch(body=request_body, request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_site_lists_list_items_list_item_document_set_versions_document_set_version_restore(
        self,
        site_id: str,
        list_id: str,
        listItem_id: str,
        documentSetVersion_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Invoke action restore.
        SharePoint operation: POST /sites/{site-id}/lists/{list-id}/items/{listItem-id}/documentSetVersions/{documentSetVersion-id}/restore
        Operation type: lists
        Args:
            site_id (str, required): SharePoint site id identifier
            list_id (str, required): SharePoint list id identifier
            listItem_id (str, required): SharePoint listItem id identifier
            documentSetVersion_id (str, required): SharePoint documentSetVersion id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).lists.by_list_id(list_id).items.by_list_item_id(listItem_id).document_set_versions.by_documentSetVersion_id(documentSetVersion_id).restore.post(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_lists_items_get_drive_item(
        self,
        site_id: str,
        list_id: str,
        listItem_id: str,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Get driveItem from sites.
        SharePoint operation: GET /sites/{site-id}/lists/{list-id}/items/{listItem-id}/driveItem
        Operation type: lists
        Args:
            site_id (str, required): SharePoint site id identifier
            list_id (str, required): SharePoint list id identifier
            listItem_id (str, required): SharePoint listItem id identifier
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = ListsRequestBuilder.ListsRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = ListsRequestBuilder.ListsRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).lists.by_list_id(list_id).items.by_list_item_id(listItem_id).drive_item.get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_lists_items_get_drive_item_content(
        self,
        site_id: str,
        list_id: str,
        listItem_id: str,
        dollar_format: Optional[str] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Get content for the navigation property driveItem from sites.
        SharePoint operation: GET /sites/{site-id}/lists/{list-id}/items/{listItem-id}/driveItem/content
        Operation type: lists
        Args:
            site_id (str, required): SharePoint site id identifier
            list_id (str, required): SharePoint list id identifier
            listItem_id (str, required): SharePoint listItem id identifier
            dollar_format (str, optional): Format of the content
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = ListsRequestBuilder.ListsRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = ListsRequestBuilder.ListsRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).lists.by_list_id(list_id).items.by_list_item_id(listItem_id).drive_item.content.get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_lists_items_update_drive_item_content(
        self,
        site_id: str,
        list_id: str,
        listItem_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Update content for the navigation property driveItem in sites.
        SharePoint operation: PUT /sites/{site-id}/lists/{list-id}/items/{listItem-id}/driveItem/content
        Operation type: lists
        Args:
            site_id (str, required): SharePoint site id identifier
            list_id (str, required): SharePoint list id identifier
            listItem_id (str, required): SharePoint listItem id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for SharePoint operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).lists.by_list_id(list_id).items.by_list_item_id(listItem_id).drive_item.content.put(body=request_body, request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_lists_items_delete_fields(
        self,
        site_id: str,
        list_id: str,
        listItem_id: str,
        If_Match: Optional[str] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Delete navigation property fields for sites.
        SharePoint operation: DELETE /sites/{site-id}/lists/{list-id}/items/{listItem-id}/fields
        Operation type: lists
        Args:
            site_id (str, required): SharePoint site id identifier
            list_id (str, required): SharePoint list id identifier
            listItem_id (str, required): SharePoint listItem id identifier
            If_Match (str, optional): ETag
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).lists.by_list_id(list_id).items.by_list_item_id(listItem_id).fields.delete(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_lists_items_get_fields(
        self,
        site_id: str,
        list_id: str,
        listItem_id: str,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Get fields from sites.
        SharePoint operation: GET /sites/{site-id}/lists/{list-id}/items/{listItem-id}/fields
        Operation type: lists
        Args:
            site_id (str, required): SharePoint site id identifier
            list_id (str, required): SharePoint list id identifier
            listItem_id (str, required): SharePoint listItem id identifier
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = ListsRequestBuilder.ListsRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = ListsRequestBuilder.ListsRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).lists.by_list_id(list_id).items.by_list_item_id(listItem_id).fields.get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_lists_items_update_fields(
        self,
        site_id: str,
        list_id: str,
        listItem_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Update listItem.
        SharePoint operation: PATCH /sites/{site-id}/lists/{list-id}/items/{listItem-id}/fields
        Operation type: lists
        Args:
            site_id (str, required): SharePoint site id identifier
            list_id (str, required): SharePoint list id identifier
            listItem_id (str, required): SharePoint listItem id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for SharePoint operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).lists.by_list_id(list_id).items.by_list_item_id(listItem_id).fields.patch(body=request_body, request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_site_lists_list_items_list_item_get_activities_by_interval_4c35(
        self,
        site_id: str,
        list_id: str,
        listItem_id: str,
        dollar_select: Optional[List[str]] = None,
        dollar_orderby: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Invoke function getActivitiesByInterval.
        SharePoint operation: GET /sites/{site-id}/lists/{list-id}/items/{listItem-id}/getActivitiesByInterval()
        Operation type: lists
        Args:
            site_id (str, required): SharePoint site id identifier
            list_id (str, required): SharePoint list id identifier
            listItem_id (str, required): SharePoint listItem id identifier
            dollar_select (List[str], optional): Select properties to be returned
            dollar_orderby (List[str], optional): Order items by property values
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = ListsRequestBuilder.ListsRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = ListsRequestBuilder.ListsRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).lists.by_list_id(list_id).items.by_list_item_id(listItem_id).get_activities_by_interval().get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_site_lists_list_items_list_item_get_activities_by_interval_ad27(
        self,
        site_id: str,
        list_id: str,
        listItem_id: str,
        startDateTime: str,
        endDateTime: str,
        interval: str,
        dollar_select: Optional[List[str]] = None,
        dollar_orderby: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Invoke function getActivitiesByInterval.
        SharePoint operation: GET /sites/{site-id}/lists/{list-id}/items/{listItem-id}/getActivitiesByInterval(startDateTime='{startDateTime}',endDateTime='{endDateTime}',interval='{interval}')
        Operation type: lists
        Args:
            site_id (str, required): SharePoint site id identifier
            list_id (str, required): SharePoint list id identifier
            listItem_id (str, required): SharePoint listItem id identifier
            startDateTime (str, required): SharePoint path parameter: startDateTime
            endDateTime (str, required): SharePoint path parameter: endDateTime
            interval (str, required): SharePoint path parameter: interval
            dollar_select (List[str], optional): Select properties to be returned
            dollar_orderby (List[str], optional): Order items by property values
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = ListsRequestBuilder.ListsRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = ListsRequestBuilder.ListsRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).lists.by_list_id(list_id).items.by_list_item_id(listItem_id).get_activities_by_interval(start_date_time='{start_date_time}',end_date_time='{end_date_time}',interval='{interval}').get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_lists_items_last_modified_by_user_update_mailbox_settings(
        self,
        site_id: str,
        list_id: str,
        listItem_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Update property mailboxSettings value..
        SharePoint operation: PATCH /sites/{site-id}/lists/{list-id}/items/{listItem-id}/lastModifiedByUser/mailboxSettings
        Operation type: lists
        Args:
            site_id (str, required): SharePoint site id identifier
            list_id (str, required): SharePoint list id identifier
            listItem_id (str, required): SharePoint listItem id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for SharePoint operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).lists.by_list_id(list_id).items.by_list_item_id(listItem_id).last_modified_by_user.mailbox_settings.patch(body=request_body, request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_lists_items_last_modified_by_user_list_service_provisioning_errors(
        self,
        site_id: str,
        list_id: str,
        listItem_id: str,
        dollar_orderby: Optional[List[str]] = None,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Get serviceProvisioningErrors property value.
        SharePoint operation: GET /sites/{site-id}/lists/{list-id}/items/{listItem-id}/lastModifiedByUser/serviceProvisioningErrors
        Operation type: lists
        Args:
            site_id (str, required): SharePoint site id identifier
            list_id (str, required): SharePoint list id identifier
            listItem_id (str, required): SharePoint listItem id identifier
            dollar_orderby (List[str], optional): Order items by property values
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = ListsRequestBuilder.ListsRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = ListsRequestBuilder.ListsRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).lists.by_list_id(list_id).items.by_list_item_id(listItem_id).last_modified_by_user.service_provisioning_errors.get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_lists_items_create_versions(
        self,
        site_id: str,
        list_id: str,
        listItem_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Create new navigation property to versions for sites.
        SharePoint operation: POST /sites/{site-id}/lists/{list-id}/items/{listItem-id}/versions
        Operation type: lists
        Args:
            site_id (str, required): SharePoint site id identifier
            list_id (str, required): SharePoint list id identifier
            listItem_id (str, required): SharePoint listItem id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for SharePoint operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).lists.by_list_id(list_id).items.by_list_item_id(listItem_id).versions.post(body=request_body, request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_lists_items_delete_versions(
        self,
        site_id: str,
        list_id: str,
        listItem_id: str,
        listItemVersion_id: str,
        If_Match: Optional[str] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Delete navigation property versions for sites.
        SharePoint operation: DELETE /sites/{site-id}/lists/{list-id}/items/{listItem-id}/versions/{listItemVersion-id}
        Operation type: lists
        Args:
            site_id (str, required): SharePoint site id identifier
            list_id (str, required): SharePoint list id identifier
            listItem_id (str, required): SharePoint listItem id identifier
            listItemVersion_id (str, required): SharePoint listItemVersion id identifier
            If_Match (str, optional): ETag
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).lists.by_list_id(list_id).items.by_list_item_id(listItem_id).versions.by_list_item_version_id(listItemVersion_id).delete(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_lists_items_get_versions(
        self,
        site_id: str,
        list_id: str,
        listItem_id: str,
        listItemVersion_id: str,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Get a ListItemVersion resource.
        SharePoint operation: GET /sites/{site-id}/lists/{list-id}/items/{listItem-id}/versions/{listItemVersion-id}
        Operation type: lists
        Args:
            site_id (str, required): SharePoint site id identifier
            list_id (str, required): SharePoint list id identifier
            listItem_id (str, required): SharePoint listItem id identifier
            listItemVersion_id (str, required): SharePoint listItemVersion id identifier
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = ListsRequestBuilder.ListsRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = ListsRequestBuilder.ListsRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).lists.by_list_id(list_id).items.by_list_item_id(listItem_id).versions.by_list_item_version_id(listItemVersion_id).get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_lists_items_update_versions(
        self,
        site_id: str,
        list_id: str,
        listItem_id: str,
        listItemVersion_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Update the navigation property versions in sites.
        SharePoint operation: PATCH /sites/{site-id}/lists/{list-id}/items/{listItem-id}/versions/{listItemVersion-id}
        Operation type: lists
        Args:
            site_id (str, required): SharePoint site id identifier
            list_id (str, required): SharePoint list id identifier
            listItem_id (str, required): SharePoint listItem id identifier
            listItemVersion_id (str, required): SharePoint listItemVersion id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for SharePoint operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).lists.by_list_id(list_id).items.by_list_item_id(listItem_id).versions.by_list_item_version_id(listItemVersion_id).patch(body=request_body, request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_lists_items_versions_delete_fields(
        self,
        site_id: str,
        list_id: str,
        listItem_id: str,
        listItemVersion_id: str,
        If_Match: Optional[str] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Delete navigation property fields for sites.
        SharePoint operation: DELETE /sites/{site-id}/lists/{list-id}/items/{listItem-id}/versions/{listItemVersion-id}/fields
        Operation type: lists
        Args:
            site_id (str, required): SharePoint site id identifier
            list_id (str, required): SharePoint list id identifier
            listItem_id (str, required): SharePoint listItem id identifier
            listItemVersion_id (str, required): SharePoint listItemVersion id identifier
            If_Match (str, optional): ETag
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).lists.by_list_id(list_id).items.by_list_item_id(listItem_id).versions.by_list_item_version_id(listItemVersion_id).fields.delete(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_lists_items_versions_get_fields(
        self,
        site_id: str,
        list_id: str,
        listItem_id: str,
        listItemVersion_id: str,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Get fields from sites.
        SharePoint operation: GET /sites/{site-id}/lists/{list-id}/items/{listItem-id}/versions/{listItemVersion-id}/fields
        Operation type: lists
        Args:
            site_id (str, required): SharePoint site id identifier
            list_id (str, required): SharePoint list id identifier
            listItem_id (str, required): SharePoint listItem id identifier
            listItemVersion_id (str, required): SharePoint listItemVersion id identifier
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = ListsRequestBuilder.ListsRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = ListsRequestBuilder.ListsRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).lists.by_list_id(list_id).items.by_list_item_id(listItem_id).versions.by_list_item_version_id(listItemVersion_id).fields.get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_lists_items_versions_update_fields(
        self,
        site_id: str,
        list_id: str,
        listItem_id: str,
        listItemVersion_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Update the navigation property fields in sites.
        SharePoint operation: PATCH /sites/{site-id}/lists/{list-id}/items/{listItem-id}/versions/{listItemVersion-id}/fields
        Operation type: lists
        Args:
            site_id (str, required): SharePoint site id identifier
            list_id (str, required): SharePoint list id identifier
            listItem_id (str, required): SharePoint listItem id identifier
            listItemVersion_id (str, required): SharePoint listItemVersion id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for SharePoint operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).lists.by_list_id(list_id).items.by_list_item_id(listItem_id).versions.by_list_item_version_id(listItemVersion_id).fields.patch(body=request_body, request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_site_lists_list_items_list_item_versions_list_item_version_restore_version(
        self,
        site_id: str,
        list_id: str,
        listItem_id: str,
        listItemVersion_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Invoke action restoreVersion.
        SharePoint operation: POST /sites/{site-id}/lists/{list-id}/items/{listItem-id}/versions/{listItemVersion-id}/restoreVersion
        Operation type: lists
        Args:
            site_id (str, required): SharePoint site id identifier
            list_id (str, required): SharePoint list id identifier
            listItem_id (str, required): SharePoint listItem id identifier
            listItemVersion_id (str, required): SharePoint listItemVersion id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).lists.by_list_id(list_id).items.by_list_item_id(listItem_id).versions.by_list_item_version_id(listItemVersion_id).restore_version.post(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_lists_last_modified_by_user_update_mailbox_settings(
        self,
        site_id: str,
        list_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Update property mailboxSettings value..
        SharePoint operation: PATCH /sites/{site-id}/lists/{list-id}/lastModifiedByUser/mailboxSettings
        Operation type: lists
        Args:
            site_id (str, required): SharePoint site id identifier
            list_id (str, required): SharePoint list id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for SharePoint operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).lists.by_list_id(list_id).last_modified_by_user.mailbox_settings.patch(body=request_body, request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_lists_last_modified_by_user_list_service_provisioning_errors(
        self,
        site_id: str,
        list_id: str,
        dollar_orderby: Optional[List[str]] = None,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Get serviceProvisioningErrors property value.
        SharePoint operation: GET /sites/{site-id}/lists/{list-id}/lastModifiedByUser/serviceProvisioningErrors
        Operation type: lists
        Args:
            site_id (str, required): SharePoint site id identifier
            list_id (str, required): SharePoint list id identifier
            dollar_orderby (List[str], optional): Order items by property values
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = ListsRequestBuilder.ListsRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = ListsRequestBuilder.ListsRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).lists.by_list_id(list_id).last_modified_by_user.service_provisioning_errors.get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_lists_create_operations(
        self,
        site_id: str,
        list_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Create new navigation property to operations for sites.
        SharePoint operation: POST /sites/{site-id}/lists/{list-id}/operations
        Operation type: lists
        Args:
            site_id (str, required): SharePoint site id identifier
            list_id (str, required): SharePoint list id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for SharePoint operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).lists.by_list_id(list_id).operations.post(body=request_body, request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_lists_list_operations(
        self,
        site_id: str,
        list_id: str,
        dollar_orderby: Optional[List[str]] = None,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Get operations from sites.
        SharePoint operation: GET /sites/{site-id}/lists/{list-id}/operations
        Operation type: lists
        Args:
            site_id (str, required): SharePoint site id identifier
            list_id (str, required): SharePoint list id identifier
            dollar_orderby (List[str], optional): Order items by property values
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = ListsRequestBuilder.ListsRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = ListsRequestBuilder.ListsRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).lists.by_list_id(list_id).operations.get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_lists_delete_operations(
        self,
        site_id: str,
        list_id: str,
        richLongRunningOperation_id: str,
        If_Match: Optional[str] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Delete navigation property operations for sites.
        SharePoint operation: DELETE /sites/{site-id}/lists/{list-id}/operations/{richLongRunningOperation-id}
        Operation type: lists
        Args:
            site_id (str, required): SharePoint site id identifier
            list_id (str, required): SharePoint list id identifier
            richLongRunningOperation_id (str, required): SharePoint richLongRunningOperation id identifier
            If_Match (str, optional): ETag
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).lists.by_list_id(list_id).operations.by_rich_long_running_operation_id(richLongRunningOperation_id).delete(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_lists_get_operations(
        self,
        site_id: str,
        list_id: str,
        richLongRunningOperation_id: str,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Get operations from sites.
        SharePoint operation: GET /sites/{site-id}/lists/{list-id}/operations/{richLongRunningOperation-id}
        Operation type: lists
        Args:
            site_id (str, required): SharePoint site id identifier
            list_id (str, required): SharePoint list id identifier
            richLongRunningOperation_id (str, required): SharePoint richLongRunningOperation id identifier
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = ListsRequestBuilder.ListsRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = ListsRequestBuilder.ListsRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).lists.by_list_id(list_id).operations.by_rich_long_running_operation_id(richLongRunningOperation_id).get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_lists_update_operations(
        self,
        site_id: str,
        list_id: str,
        richLongRunningOperation_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Update the navigation property operations in sites.
        SharePoint operation: PATCH /sites/{site-id}/lists/{list-id}/operations/{richLongRunningOperation-id}
        Operation type: lists
        Args:
            site_id (str, required): SharePoint site id identifier
            list_id (str, required): SharePoint list id identifier
            richLongRunningOperation_id (str, required): SharePoint richLongRunningOperation id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for SharePoint operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).lists.by_list_id(list_id).operations.by_rich_long_running_operation_id(richLongRunningOperation_id).patch(body=request_body, request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_lists_create_subscriptions(
        self,
        site_id: str,
        list_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Create new navigation property to subscriptions for sites.
        SharePoint operation: POST /sites/{site-id}/lists/{list-id}/subscriptions
        Operation type: lists
        Args:
            site_id (str, required): SharePoint site id identifier
            list_id (str, required): SharePoint list id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for SharePoint operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).lists.by_list_id(list_id).subscriptions.post(body=request_body, request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_lists_list_subscriptions(
        self,
        site_id: str,
        list_id: str,
        dollar_orderby: Optional[List[str]] = None,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Get subscriptions from sites.
        SharePoint operation: GET /sites/{site-id}/lists/{list-id}/subscriptions
        Operation type: lists
        Args:
            site_id (str, required): SharePoint site id identifier
            list_id (str, required): SharePoint list id identifier
            dollar_orderby (List[str], optional): Order items by property values
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = ListsRequestBuilder.ListsRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = ListsRequestBuilder.ListsRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).lists.by_list_id(list_id).subscriptions.get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_lists_delete_subscriptions(
        self,
        site_id: str,
        list_id: str,
        subscription_id: str,
        If_Match: Optional[str] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Delete navigation property subscriptions for sites.
        SharePoint operation: DELETE /sites/{site-id}/lists/{list-id}/subscriptions/{subscription-id}
        Operation type: lists
        Args:
            site_id (str, required): SharePoint site id identifier
            list_id (str, required): SharePoint list id identifier
            subscription_id (str, required): SharePoint subscription id identifier
            If_Match (str, optional): ETag
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).lists.by_list_id(list_id).subscriptions.by_subscription_id(subscription_id).delete(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_lists_get_subscriptions(
        self,
        site_id: str,
        list_id: str,
        subscription_id: str,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Get subscriptions from sites.
        SharePoint operation: GET /sites/{site-id}/lists/{list-id}/subscriptions/{subscription-id}
        Operation type: lists
        Args:
            site_id (str, required): SharePoint site id identifier
            list_id (str, required): SharePoint list id identifier
            subscription_id (str, required): SharePoint subscription id identifier
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = ListsRequestBuilder.ListsRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = ListsRequestBuilder.ListsRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).lists.by_list_id(list_id).subscriptions.by_subscription_id(subscription_id).get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_lists_update_subscriptions(
        self,
        site_id: str,
        list_id: str,
        subscription_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Update the navigation property subscriptions in sites.
        SharePoint operation: PATCH /sites/{site-id}/lists/{list-id}/subscriptions/{subscription-id}
        Operation type: lists
        Args:
            site_id (str, required): SharePoint site id identifier
            list_id (str, required): SharePoint list id identifier
            subscription_id (str, required): SharePoint subscription id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for SharePoint operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).lists.by_list_id(list_id).subscriptions.by_subscription_id(subscription_id).patch(body=request_body, request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_site_lists_list_subscriptions_subscription_reauthorize(
        self,
        site_id: str,
        list_id: str,
        subscription_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Invoke action reauthorize.
        SharePoint operation: POST /sites/{site-id}/lists/{list-id}/subscriptions/{subscription-id}/reauthorize
        Operation type: lists
        Args:
            site_id (str, required): SharePoint site id identifier
            list_id (str, required): SharePoint list id identifier
            subscription_id (str, required): SharePoint subscription id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).lists.by_list_id(list_id).subscriptions.by_subscription_id(subscription_id).reauthorize.post(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    # ========== DRIVES OPERATIONS (7 methods) ==========

    async def sites_analytics_item_activity_stats_activities_get_drive_item(
        self,
        site_id: str,
        itemActivityStat_id: str,
        itemActivity_id: str,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Get driveItem from sites.
        SharePoint operation: GET /sites/{site-id}/analytics/itemActivityStats/{itemActivityStat-id}/activities/{itemActivity-id}/driveItem
        Operation type: drives
        Args:
            site_id (str, required): SharePoint site id identifier
            itemActivityStat_id (str, required): SharePoint itemActivityStat id identifier
            itemActivity_id (str, required): SharePoint itemActivity id identifier
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = SitesRequestBuilder.SitesRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = SitesRequestBuilder.SitesRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).analytics.item_activity_stats.by_itemActivityStat_id(itemActivityStat_id).activities.by_activitie_id(itemActivity_id).drive_item.get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_analytics_item_activity_stats_activities_get_drive_item_content(
        self,
        site_id: str,
        itemActivityStat_id: str,
        itemActivity_id: str,
        dollar_format: Optional[str] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Get content for the navigation property driveItem from sites.
        SharePoint operation: GET /sites/{site-id}/analytics/itemActivityStats/{itemActivityStat-id}/activities/{itemActivity-id}/driveItem/content
        Operation type: drives
        Args:
            site_id (str, required): SharePoint site id identifier
            itemActivityStat_id (str, required): SharePoint itemActivityStat id identifier
            itemActivity_id (str, required): SharePoint itemActivity id identifier
            dollar_format (str, optional): Format of the content
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = SitesRequestBuilder.SitesRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = SitesRequestBuilder.SitesRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).analytics.item_activity_stats.by_itemActivityStat_id(itemActivityStat_id).activities.by_activitie_id(itemActivity_id).drive_item.content.get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_analytics_item_activity_stats_activities_update_drive_item_content(
        self,
        site_id: str,
        itemActivityStat_id: str,
        itemActivity_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Update content for the navigation property driveItem in sites.
        SharePoint operation: PUT /sites/{site-id}/analytics/itemActivityStats/{itemActivityStat-id}/activities/{itemActivity-id}/driveItem/content
        Operation type: drives
        Args:
            site_id (str, required): SharePoint site id identifier
            itemActivityStat_id (str, required): SharePoint itemActivityStat id identifier
            itemActivity_id (str, required): SharePoint itemActivity id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for SharePoint operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).analytics.item_activity_stats.by_itemActivityStat_id(itemActivityStat_id).activities.by_activitie_id(itemActivity_id).drive_item.content.put(body=request_body, request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_get_drive(
        self,
        site_id: str,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Get drive from sites.
        SharePoint operation: GET /sites/{site-id}/drive
        Operation type: drives
        Args:
            site_id (str, required): SharePoint site id identifier
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = SitesRequestBuilder.SitesRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = SitesRequestBuilder.SitesRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).drive.get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_list_drives(
        self,
        site_id: str,
        dollar_orderby: Optional[List[str]] = None,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Get drives from sites.
        SharePoint operation: GET /sites/{site-id}/drives
        Operation type: drives
        Args:
            site_id (str, required): SharePoint site id identifier
            dollar_orderby (List[str], optional): Order items by property values
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = DrivesRequestBuilder.DrivesRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = DrivesRequestBuilder.DrivesRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).drives.get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_get_by_path_get_drive(
        self,
        site_id: str,
        path: str,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Get drive from sites.
        SharePoint operation: GET /sites/{site-id}/getByPath(path='{path}')/drive
        Operation type: drives
        Args:
            site_id (str, required): SharePoint site id identifier
            path (str, required): SharePoint path: path
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = SitesRequestBuilder.SitesRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = SitesRequestBuilder.SitesRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).get_by_path.drive.get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_get_by_path_list_drives(
        self,
        site_id: str,
        path: str,
        dollar_orderby: Optional[List[str]] = None,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Get drives from sites.
        SharePoint operation: GET /sites/{site-id}/getByPath(path='{path}')/drives
        Operation type: drives
        Args:
            site_id (str, required): SharePoint site id identifier
            path (str, required): SharePoint path: path
            dollar_orderby (List[str], optional): Order items by property values
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = SitesRequestBuilder.SitesRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = SitesRequestBuilder.SitesRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).get_by_path.drives.get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    # ========== PAGES OPERATIONS (51 methods) ==========

    async def sites_get_by_path_create_pages(
        self,
        site_id: str,
        path: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Create new navigation property to pages for sites.
        SharePoint operation: POST /sites/{site-id}/getByPath(path='{path}')/pages
        Operation type: pages
        Args:
            site_id (str, required): SharePoint site id identifier
            path (str, required): SharePoint path: path
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for SharePoint operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).get_by_path.pages.post(body=request_body, request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_get_by_path_list_pages(
        self,
        site_id: str,
        path: str,
        dollar_orderby: Optional[List[str]] = None,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Get pages from sites.
        SharePoint operation: GET /sites/{site-id}/getByPath(path='{path}')/pages
        Operation type: pages
        Args:
            site_id (str, required): SharePoint site id identifier
            path (str, required): SharePoint path: path
            dollar_orderby (List[str], optional): Order items by property values
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = SitesRequestBuilder.SitesRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = SitesRequestBuilder.SitesRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).get_by_path.pages.get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_create_pages(
        self,
        site_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Create a page in the site pages list of a site.
        SharePoint operation: POST /sites/{site-id}/pages
        Operation type: pages
        Args:
            site_id (str, required): SharePoint site id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for SharePoint operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).pages.post(body=request_body, request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_list_pages(
        self,
        site_id: str,
        dollar_orderby: Optional[List[str]] = None,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """List baseSitePages.
        SharePoint operation: GET /sites/{site-id}/pages
        Operation type: pages
        Args:
            site_id (str, required): SharePoint site id identifier
            dollar_orderby (List[str], optional): Order items by property values
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = PagesRequestBuilder.PagesRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = PagesRequestBuilder.PagesRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).pages.get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_list_pages_as_site_page(
        self,
        site_id: str,
        dollar_orderby: Optional[List[str]] = None,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Get SitePage.
        SharePoint operation: GET /sites/{site-id}/pages/graph.sitePage
        Operation type: pages
        Args:
            site_id (str, required): SharePoint site id identifier
            dollar_orderby (List[str], optional): Order items by property values
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = PagesRequestBuilder.PagesRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = PagesRequestBuilder.PagesRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).pages.graph_site_page.get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_delete_pages(
        self,
        site_id: str,
        baseSitePage_id: str,
        If_Match: Optional[str] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Delete baseSitePage.
        SharePoint operation: DELETE /sites/{site-id}/pages/{baseSitePage-id}
        Operation type: pages
        Args:
            site_id (str, required): SharePoint site id identifier
            baseSitePage_id (str, required): SharePoint baseSitePage id identifier
            If_Match (str, optional): ETag
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).pages.by_base_site_page_id(baseSitePage_id).delete(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_get_pages(
        self,
        site_id: str,
        baseSitePage_id: str,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Get baseSitePage.
        SharePoint operation: GET /sites/{site-id}/pages/{baseSitePage-id}
        Operation type: pages
        Args:
            site_id (str, required): SharePoint site id identifier
            baseSitePage_id (str, required): SharePoint baseSitePage id identifier
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = PagesRequestBuilder.PagesRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = PagesRequestBuilder.PagesRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).pages.by_base_site_page_id(baseSitePage_id).get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_update_pages(
        self,
        site_id: str,
        baseSitePage_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Update the navigation property pages in sites.
        SharePoint operation: PATCH /sites/{site-id}/pages/{baseSitePage-id}
        Operation type: pages
        Args:
            site_id (str, required): SharePoint site id identifier
            baseSitePage_id (str, required): SharePoint baseSitePage id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for SharePoint operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).pages.by_base_site_page_id(baseSitePage_id).patch(body=request_body, request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_pages_created_by_user_update_mailbox_settings(
        self,
        site_id: str,
        baseSitePage_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Update property mailboxSettings value..
        SharePoint operation: PATCH /sites/{site-id}/pages/{baseSitePage-id}/createdByUser/mailboxSettings
        Operation type: pages
        Args:
            site_id (str, required): SharePoint site id identifier
            baseSitePage_id (str, required): SharePoint baseSitePage id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for SharePoint operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).pages.by_base_site_page_id(baseSitePage_id).created_by_user.mailbox_settings.patch(body=request_body, request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_pages_created_by_user_list_service_provisioning_errors(
        self,
        site_id: str,
        baseSitePage_id: str,
        dollar_orderby: Optional[List[str]] = None,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Get serviceProvisioningErrors property value.
        SharePoint operation: GET /sites/{site-id}/pages/{baseSitePage-id}/createdByUser/serviceProvisioningErrors
        Operation type: pages
        Args:
            site_id (str, required): SharePoint site id identifier
            baseSitePage_id (str, required): SharePoint baseSitePage id identifier
            dollar_orderby (List[str], optional): Order items by property values
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = PagesRequestBuilder.PagesRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = PagesRequestBuilder.PagesRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).pages.by_base_site_page_id(baseSitePage_id).created_by_user.service_provisioning_errors.get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_get_pages_as_site_page(
        self,
        site_id: str,
        baseSitePage_id: str,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Get SitePage.
        SharePoint operation: GET /sites/{site-id}/pages/{baseSitePage-id}/graph.sitePage
        Operation type: pages
        Args:
            site_id (str, required): SharePoint site id identifier
            baseSitePage_id (str, required): SharePoint baseSitePage id identifier
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = PagesRequestBuilder.PagesRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = PagesRequestBuilder.PagesRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).pages.by_base_site_page_id(baseSitePage_id).graph_site_page.get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_pages_as_site_page_delete_canvas_layout(
        self,
        site_id: str,
        baseSitePage_id: str,
        If_Match: Optional[str] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Delete navigation property canvasLayout for sites.
        SharePoint operation: DELETE /sites/{site-id}/pages/{baseSitePage-id}/graph.sitePage/canvasLayout
        Operation type: pages
        Args:
            site_id (str, required): SharePoint site id identifier
            baseSitePage_id (str, required): SharePoint baseSitePage id identifier
            If_Match (str, optional): ETag
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).pages.by_base_site_page_id(baseSitePage_id).graph_site_page.canvas_layout.delete(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_pages_as_site_page_get_canvas_layout(
        self,
        site_id: str,
        baseSitePage_id: str,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Get canvasLayout from sites.
        SharePoint operation: GET /sites/{site-id}/pages/{baseSitePage-id}/graph.sitePage/canvasLayout
        Operation type: pages
        Args:
            site_id (str, required): SharePoint site id identifier
            baseSitePage_id (str, required): SharePoint baseSitePage id identifier
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = PagesRequestBuilder.PagesRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = PagesRequestBuilder.PagesRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).pages.by_base_site_page_id(baseSitePage_id).graph_site_page.canvas_layout.get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_pages_as_site_page_update_canvas_layout(
        self,
        site_id: str,
        baseSitePage_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Update the navigation property canvasLayout in sites.
        SharePoint operation: PATCH /sites/{site-id}/pages/{baseSitePage-id}/graph.sitePage/canvasLayout
        Operation type: pages
        Args:
            site_id (str, required): SharePoint site id identifier
            baseSitePage_id (str, required): SharePoint baseSitePage id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for SharePoint operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).pages.by_base_site_page_id(baseSitePage_id).graph_site_page.canvas_layout.patch(body=request_body, request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_pages_as_site_page_canvas_layout_create_horizontal_sections(
        self,
        site_id: str,
        baseSitePage_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Create new navigation property to horizontalSections for sites.
        SharePoint operation: POST /sites/{site-id}/pages/{baseSitePage-id}/graph.sitePage/canvasLayout/horizontalSections
        Operation type: pages
        Args:
            site_id (str, required): SharePoint site id identifier
            baseSitePage_id (str, required): SharePoint baseSitePage id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for SharePoint operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).pages.by_base_site_page_id(baseSitePage_id).graph_site_page.canvas_layout.horizontal_sections.post(body=request_body, request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_pages_as_site_page_canvas_layout_list_horizontal_sections(
        self,
        site_id: str,
        baseSitePage_id: str,
        dollar_orderby: Optional[List[str]] = None,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Get horizontalSections from sites.
        SharePoint operation: GET /sites/{site-id}/pages/{baseSitePage-id}/graph.sitePage/canvasLayout/horizontalSections
        Operation type: pages
        Args:
            site_id (str, required): SharePoint site id identifier
            baseSitePage_id (str, required): SharePoint baseSitePage id identifier
            dollar_orderby (List[str], optional): Order items by property values
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = PagesRequestBuilder.PagesRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = PagesRequestBuilder.PagesRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).pages.by_base_site_page_id(baseSitePage_id).graph_site_page.canvas_layout.horizontal_sections.get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_pages_as_site_page_canvas_layout_delete_horizontal_sections(
        self,
        site_id: str,
        baseSitePage_id: str,
        horizontalSection_id: str,
        If_Match: Optional[str] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Delete navigation property horizontalSections for sites.
        SharePoint operation: DELETE /sites/{site-id}/pages/{baseSitePage-id}/graph.sitePage/canvasLayout/horizontalSections/{horizontalSection-id}
        Operation type: pages
        Args:
            site_id (str, required): SharePoint site id identifier
            baseSitePage_id (str, required): SharePoint baseSitePage id identifier
            horizontalSection_id (str, required): SharePoint horizontalSection id identifier
            If_Match (str, optional): ETag
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).pages.by_base_site_page_id(baseSitePage_id).graph_site_page.canvas_layout.horizontal_sections.by_horizontal_section_id(horizontalSection_id).delete(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_pages_as_site_page_canvas_layout_get_horizontal_sections(
        self,
        site_id: str,
        baseSitePage_id: str,
        horizontalSection_id: str,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Get horizontalSections from sites.
        SharePoint operation: GET /sites/{site-id}/pages/{baseSitePage-id}/graph.sitePage/canvasLayout/horizontalSections/{horizontalSection-id}
        Operation type: pages
        Args:
            site_id (str, required): SharePoint site id identifier
            baseSitePage_id (str, required): SharePoint baseSitePage id identifier
            horizontalSection_id (str, required): SharePoint horizontalSection id identifier
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = PagesRequestBuilder.PagesRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = PagesRequestBuilder.PagesRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).pages.by_base_site_page_id(baseSitePage_id).graph_site_page.canvas_layout.horizontal_sections.by_horizontal_section_id(horizontalSection_id).get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_pages_as_site_page_canvas_layout_update_horizontal_sections(
        self,
        site_id: str,
        baseSitePage_id: str,
        horizontalSection_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Update the navigation property horizontalSections in sites.
        SharePoint operation: PATCH /sites/{site-id}/pages/{baseSitePage-id}/graph.sitePage/canvasLayout/horizontalSections/{horizontalSection-id}
        Operation type: pages
        Args:
            site_id (str, required): SharePoint site id identifier
            baseSitePage_id (str, required): SharePoint baseSitePage id identifier
            horizontalSection_id (str, required): SharePoint horizontalSection id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for SharePoint operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).pages.by_base_site_page_id(baseSitePage_id).graph_site_page.canvas_layout.horizontal_sections.by_horizontal_section_id(horizontalSection_id).patch(body=request_body, request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_pages_as_site_page_canvas_layout_horizontal_sections_create_columns(
        self,
        site_id: str,
        baseSitePage_id: str,
        horizontalSection_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Create new navigation property to columns for sites.
        SharePoint operation: POST /sites/{site-id}/pages/{baseSitePage-id}/graph.sitePage/canvasLayout/horizontalSections/{horizontalSection-id}/columns
        Operation type: pages
        Args:
            site_id (str, required): SharePoint site id identifier
            baseSitePage_id (str, required): SharePoint baseSitePage id identifier
            horizontalSection_id (str, required): SharePoint horizontalSection id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for SharePoint operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).pages.by_base_site_page_id(baseSitePage_id).graph_site_page.canvas_layout.horizontal_sections.by_horizontal_section_id(horizontalSection_id).columns.post(body=request_body, request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_pages_as_site_page_canvas_layout_horizontal_sections_list_columns(
        self,
        site_id: str,
        baseSitePage_id: str,
        horizontalSection_id: str,
        dollar_orderby: Optional[List[str]] = None,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Get columns from sites.
        SharePoint operation: GET /sites/{site-id}/pages/{baseSitePage-id}/graph.sitePage/canvasLayout/horizontalSections/{horizontalSection-id}/columns
        Operation type: pages
        Args:
            site_id (str, required): SharePoint site id identifier
            baseSitePage_id (str, required): SharePoint baseSitePage id identifier
            horizontalSection_id (str, required): SharePoint horizontalSection id identifier
            dollar_orderby (List[str], optional): Order items by property values
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = PagesRequestBuilder.PagesRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = PagesRequestBuilder.PagesRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).pages.by_base_site_page_id(baseSitePage_id).graph_site_page.canvas_layout.horizontal_sections.by_horizontal_section_id(horizontalSection_id).columns.get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_pages_as_site_page_canvas_layout_horizontal_sections_delete_columns(
        self,
        site_id: str,
        baseSitePage_id: str,
        horizontalSection_id: str,
        horizontalSectionColumn_id: str,
        If_Match: Optional[str] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Delete navigation property columns for sites.
        SharePoint operation: DELETE /sites/{site-id}/pages/{baseSitePage-id}/graph.sitePage/canvasLayout/horizontalSections/{horizontalSection-id}/columns/{horizontalSectionColumn-id}
        Operation type: pages
        Args:
            site_id (str, required): SharePoint site id identifier
            baseSitePage_id (str, required): SharePoint baseSitePage id identifier
            horizontalSection_id (str, required): SharePoint horizontalSection id identifier
            horizontalSectionColumn_id (str, required): SharePoint horizontalSectionColumn id identifier
            If_Match (str, optional): ETag
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).pages.by_base_site_page_id(baseSitePage_id).graph_site_page.canvas_layout.horizontal_sections.by_horizontal_section_id(horizontalSection_id).columns.by_column_definition_id(horizontalSectionColumn_id).delete(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_pages_as_site_page_canvas_layout_horizontal_sections_get_columns(
        self,
        site_id: str,
        baseSitePage_id: str,
        horizontalSection_id: str,
        horizontalSectionColumn_id: str,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Get columns from sites.
        SharePoint operation: GET /sites/{site-id}/pages/{baseSitePage-id}/graph.sitePage/canvasLayout/horizontalSections/{horizontalSection-id}/columns/{horizontalSectionColumn-id}
        Operation type: pages
        Args:
            site_id (str, required): SharePoint site id identifier
            baseSitePage_id (str, required): SharePoint baseSitePage id identifier
            horizontalSection_id (str, required): SharePoint horizontalSection id identifier
            horizontalSectionColumn_id (str, required): SharePoint horizontalSectionColumn id identifier
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = PagesRequestBuilder.PagesRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = PagesRequestBuilder.PagesRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).pages.by_base_site_page_id(baseSitePage_id).graph_site_page.canvas_layout.horizontal_sections.by_horizontal_section_id(horizontalSection_id).columns.by_column_definition_id(horizontalSectionColumn_id).get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_pages_as_site_page_canvas_layout_horizontal_sections_update_columns(
        self,
        site_id: str,
        baseSitePage_id: str,
        horizontalSection_id: str,
        horizontalSectionColumn_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Update the navigation property columns in sites.
        SharePoint operation: PATCH /sites/{site-id}/pages/{baseSitePage-id}/graph.sitePage/canvasLayout/horizontalSections/{horizontalSection-id}/columns/{horizontalSectionColumn-id}
        Operation type: pages
        Args:
            site_id (str, required): SharePoint site id identifier
            baseSitePage_id (str, required): SharePoint baseSitePage id identifier
            horizontalSection_id (str, required): SharePoint horizontalSection id identifier
            horizontalSectionColumn_id (str, required): SharePoint horizontalSectionColumn id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for SharePoint operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).pages.by_base_site_page_id(baseSitePage_id).graph_site_page.canvas_layout.horizontal_sections.by_horizontal_section_id(horizontalSection_id).columns.by_column_definition_id(horizontalSectionColumn_id).patch(body=request_body, request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_pages_as_site_page_canvas_layout_horizontal_sections_columns_create_webparts(
        self,
        site_id: str,
        baseSitePage_id: str,
        horizontalSection_id: str,
        horizontalSectionColumn_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Create new navigation property to webparts for sites.
        SharePoint operation: POST /sites/{site-id}/pages/{baseSitePage-id}/graph.sitePage/canvasLayout/horizontalSections/{horizontalSection-id}/columns/{horizontalSectionColumn-id}/webparts
        Operation type: pages
        Args:
            site_id (str, required): SharePoint site id identifier
            baseSitePage_id (str, required): SharePoint baseSitePage id identifier
            horizontalSection_id (str, required): SharePoint horizontalSection id identifier
            horizontalSectionColumn_id (str, required): SharePoint horizontalSectionColumn id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for SharePoint operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).pages.by_base_site_page_id(baseSitePage_id).graph_site_page.canvas_layout.horizontal_sections.by_horizontal_section_id(horizontalSection_id).columns.by_column_definition_id(horizontalSectionColumn_id).webparts.post(body=request_body, request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_pages_as_site_page_canvas_layout_horizontal_sections_columns_list_webparts(
        self,
        site_id: str,
        baseSitePage_id: str,
        horizontalSection_id: str,
        horizontalSectionColumn_id: str,
        dollar_orderby: Optional[List[str]] = None,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Get webparts from sites.
        SharePoint operation: GET /sites/{site-id}/pages/{baseSitePage-id}/graph.sitePage/canvasLayout/horizontalSections/{horizontalSection-id}/columns/{horizontalSectionColumn-id}/webparts
        Operation type: pages
        Args:
            site_id (str, required): SharePoint site id identifier
            baseSitePage_id (str, required): SharePoint baseSitePage id identifier
            horizontalSection_id (str, required): SharePoint horizontalSection id identifier
            horizontalSectionColumn_id (str, required): SharePoint horizontalSectionColumn id identifier
            dollar_orderby (List[str], optional): Order items by property values
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = PagesRequestBuilder.PagesRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = PagesRequestBuilder.PagesRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).pages.by_base_site_page_id(baseSitePage_id).graph_site_page.canvas_layout.horizontal_sections.by_horizontal_section_id(horizontalSection_id).columns.by_column_definition_id(horizontalSectionColumn_id).webparts.get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_pages_as_site_page_canvas_layout_horizontal_sections_columns_delete_webparts(
        self,
        site_id: str,
        baseSitePage_id: str,
        horizontalSection_id: str,
        horizontalSectionColumn_id: str,
        webPart_id: str,
        If_Match: Optional[str] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Delete navigation property webparts for sites.
        SharePoint operation: DELETE /sites/{site-id}/pages/{baseSitePage-id}/graph.sitePage/canvasLayout/horizontalSections/{horizontalSection-id}/columns/{horizontalSectionColumn-id}/webparts/{webPart-id}
        Operation type: pages
        Args:
            site_id (str, required): SharePoint site id identifier
            baseSitePage_id (str, required): SharePoint baseSitePage id identifier
            horizontalSection_id (str, required): SharePoint horizontalSection id identifier
            horizontalSectionColumn_id (str, required): SharePoint horizontalSectionColumn id identifier
            webPart_id (str, required): SharePoint webPart id identifier
            If_Match (str, optional): ETag
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).pages.by_base_site_page_id(baseSitePage_id).graph_site_page.canvas_layout.horizontal_sections.by_horizontal_section_id(horizontalSection_id).columns.by_column_definition_id(horizontalSectionColumn_id).webparts.by_web_part_id(webPart_id).delete(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_pages_as_site_page_canvas_layout_horizontal_sections_columns_get_webparts(
        self,
        site_id: str,
        baseSitePage_id: str,
        horizontalSection_id: str,
        horizontalSectionColumn_id: str,
        webPart_id: str,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Get webparts from sites.
        SharePoint operation: GET /sites/{site-id}/pages/{baseSitePage-id}/graph.sitePage/canvasLayout/horizontalSections/{horizontalSection-id}/columns/{horizontalSectionColumn-id}/webparts/{webPart-id}
        Operation type: pages
        Args:
            site_id (str, required): SharePoint site id identifier
            baseSitePage_id (str, required): SharePoint baseSitePage id identifier
            horizontalSection_id (str, required): SharePoint horizontalSection id identifier
            horizontalSectionColumn_id (str, required): SharePoint horizontalSectionColumn id identifier
            webPart_id (str, required): SharePoint webPart id identifier
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = PagesRequestBuilder.PagesRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = PagesRequestBuilder.PagesRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).pages.by_base_site_page_id(baseSitePage_id).graph_site_page.canvas_layout.horizontal_sections.by_horizontal_section_id(horizontalSection_id).columns.by_column_definition_id(horizontalSectionColumn_id).webparts.by_web_part_id(webPart_id).get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_pages_as_site_page_canvas_layout_horizontal_sections_columns_update_webparts(
        self,
        site_id: str,
        baseSitePage_id: str,
        horizontalSection_id: str,
        horizontalSectionColumn_id: str,
        webPart_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Update the navigation property webparts in sites.
        SharePoint operation: PATCH /sites/{site-id}/pages/{baseSitePage-id}/graph.sitePage/canvasLayout/horizontalSections/{horizontalSection-id}/columns/{horizontalSectionColumn-id}/webparts/{webPart-id}
        Operation type: pages
        Args:
            site_id (str, required): SharePoint site id identifier
            baseSitePage_id (str, required): SharePoint baseSitePage id identifier
            horizontalSection_id (str, required): SharePoint horizontalSection id identifier
            horizontalSectionColumn_id (str, required): SharePoint horizontalSectionColumn id identifier
            webPart_id (str, required): SharePoint webPart id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for SharePoint operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).pages.by_base_site_page_id(baseSitePage_id).graph_site_page.canvas_layout.horizontal_sections.by_horizontal_section_id(horizontalSection_id).columns.by_column_definition_id(horizontalSectionColumn_id).webparts.by_web_part_id(webPart_id).patch(body=request_body, request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_site_pages_base_site_page_microsoft_graph_site_page_canvas_layout_horizontal_sections_horizontal_section_columns_horizontal_section_column_webparts_web_part_get_position_of_web_part(
        self,
        site_id: str,
        baseSitePage_id: str,
        horizontalSection_id: str,
        horizontalSectionColumn_id: str,
        webPart_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Invoke action getPositionOfWebPart.
        SharePoint operation: POST /sites/{site-id}/pages/{baseSitePage-id}/graph.sitePage/canvasLayout/horizontalSections/{horizontalSection-id}/columns/{horizontalSectionColumn-id}/webparts/{webPart-id}/getPositionOfWebPart
        Operation type: pages
        Args:
            site_id (str, required): SharePoint site id identifier
            baseSitePage_id (str, required): SharePoint baseSitePage id identifier
            horizontalSection_id (str, required): SharePoint horizontalSection id identifier
            horizontalSectionColumn_id (str, required): SharePoint horizontalSectionColumn id identifier
            webPart_id (str, required): SharePoint webPart id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).pages.by_base_site_page_id(baseSitePage_id).graph_site_page.canvas_layout.horizontal_sections.by_horizontal_section_id(horizontalSection_id).columns.by_column_definition_id(horizontalSectionColumn_id).webparts.by_web_part_id(webPart_id).get_position_of_web_part.post(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_pages_as_site_page_canvas_layout_delete_vertical_section(
        self,
        site_id: str,
        baseSitePage_id: str,
        If_Match: Optional[str] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Delete navigation property verticalSection for sites.
        SharePoint operation: DELETE /sites/{site-id}/pages/{baseSitePage-id}/graph.sitePage/canvasLayout/verticalSection
        Operation type: pages
        Args:
            site_id (str, required): SharePoint site id identifier
            baseSitePage_id (str, required): SharePoint baseSitePage id identifier
            If_Match (str, optional): ETag
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).pages.by_base_site_page_id(baseSitePage_id).graph_site_page.canvas_layout.vertical_section.delete(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_pages_as_site_page_canvas_layout_get_vertical_section(
        self,
        site_id: str,
        baseSitePage_id: str,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Get verticalSection from sites.
        SharePoint operation: GET /sites/{site-id}/pages/{baseSitePage-id}/graph.sitePage/canvasLayout/verticalSection
        Operation type: pages
        Args:
            site_id (str, required): SharePoint site id identifier
            baseSitePage_id (str, required): SharePoint baseSitePage id identifier
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = PagesRequestBuilder.PagesRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = PagesRequestBuilder.PagesRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).pages.by_base_site_page_id(baseSitePage_id).graph_site_page.canvas_layout.vertical_section.get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_pages_as_site_page_canvas_layout_update_vertical_section(
        self,
        site_id: str,
        baseSitePage_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Update the navigation property verticalSection in sites.
        SharePoint operation: PATCH /sites/{site-id}/pages/{baseSitePage-id}/graph.sitePage/canvasLayout/verticalSection
        Operation type: pages
        Args:
            site_id (str, required): SharePoint site id identifier
            baseSitePage_id (str, required): SharePoint baseSitePage id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for SharePoint operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).pages.by_base_site_page_id(baseSitePage_id).graph_site_page.canvas_layout.vertical_section.patch(body=request_body, request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_pages_as_site_page_canvas_layout_vertical_section_create_webparts(
        self,
        site_id: str,
        baseSitePage_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Create new navigation property to webparts for sites.
        SharePoint operation: POST /sites/{site-id}/pages/{baseSitePage-id}/graph.sitePage/canvasLayout/verticalSection/webparts
        Operation type: pages
        Args:
            site_id (str, required): SharePoint site id identifier
            baseSitePage_id (str, required): SharePoint baseSitePage id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for SharePoint operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).pages.by_base_site_page_id(baseSitePage_id).graph_site_page.canvas_layout.vertical_section.webparts.post(body=request_body, request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_pages_as_site_page_canvas_layout_vertical_section_list_webparts(
        self,
        site_id: str,
        baseSitePage_id: str,
        dollar_orderby: Optional[List[str]] = None,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Get webparts from sites.
        SharePoint operation: GET /sites/{site-id}/pages/{baseSitePage-id}/graph.sitePage/canvasLayout/verticalSection/webparts
        Operation type: pages
        Args:
            site_id (str, required): SharePoint site id identifier
            baseSitePage_id (str, required): SharePoint baseSitePage id identifier
            dollar_orderby (List[str], optional): Order items by property values
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = PagesRequestBuilder.PagesRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = PagesRequestBuilder.PagesRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).pages.by_base_site_page_id(baseSitePage_id).graph_site_page.canvas_layout.vertical_section.webparts.get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_pages_as_site_page_canvas_layout_vertical_section_delete_webparts(
        self,
        site_id: str,
        baseSitePage_id: str,
        webPart_id: str,
        If_Match: Optional[str] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Delete navigation property webparts for sites.
        SharePoint operation: DELETE /sites/{site-id}/pages/{baseSitePage-id}/graph.sitePage/canvasLayout/verticalSection/webparts/{webPart-id}
        Operation type: pages
        Args:
            site_id (str, required): SharePoint site id identifier
            baseSitePage_id (str, required): SharePoint baseSitePage id identifier
            webPart_id (str, required): SharePoint webPart id identifier
            If_Match (str, optional): ETag
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).pages.by_base_site_page_id(baseSitePage_id).graph_site_page.canvas_layout.vertical_section.webparts.by_web_part_id(webPart_id).delete(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_pages_as_site_page_canvas_layout_vertical_section_get_webparts(
        self,
        site_id: str,
        baseSitePage_id: str,
        webPart_id: str,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Get webparts from sites.
        SharePoint operation: GET /sites/{site-id}/pages/{baseSitePage-id}/graph.sitePage/canvasLayout/verticalSection/webparts/{webPart-id}
        Operation type: pages
        Args:
            site_id (str, required): SharePoint site id identifier
            baseSitePage_id (str, required): SharePoint baseSitePage id identifier
            webPart_id (str, required): SharePoint webPart id identifier
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = PagesRequestBuilder.PagesRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = PagesRequestBuilder.PagesRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).pages.by_base_site_page_id(baseSitePage_id).graph_site_page.canvas_layout.vertical_section.webparts.by_web_part_id(webPart_id).get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_pages_as_site_page_canvas_layout_vertical_section_update_webparts(
        self,
        site_id: str,
        baseSitePage_id: str,
        webPart_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Update the navigation property webparts in sites.
        SharePoint operation: PATCH /sites/{site-id}/pages/{baseSitePage-id}/graph.sitePage/canvasLayout/verticalSection/webparts/{webPart-id}
        Operation type: pages
        Args:
            site_id (str, required): SharePoint site id identifier
            baseSitePage_id (str, required): SharePoint baseSitePage id identifier
            webPart_id (str, required): SharePoint webPart id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for SharePoint operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).pages.by_base_site_page_id(baseSitePage_id).graph_site_page.canvas_layout.vertical_section.webparts.by_web_part_id(webPart_id).patch(body=request_body, request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_site_pages_base_site_page_microsoft_graph_site_page_canvas_layout_vertical_section_webparts_web_part_get_position_of_web_part(
        self,
        site_id: str,
        baseSitePage_id: str,
        webPart_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Invoke action getPositionOfWebPart.
        SharePoint operation: POST /sites/{site-id}/pages/{baseSitePage-id}/graph.sitePage/canvasLayout/verticalSection/webparts/{webPart-id}/getPositionOfWebPart
        Operation type: pages
        Args:
            site_id (str, required): SharePoint site id identifier
            baseSitePage_id (str, required): SharePoint baseSitePage id identifier
            webPart_id (str, required): SharePoint webPart id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).pages.by_base_site_page_id(baseSitePage_id).graph_site_page.canvas_layout.vertical_section.webparts.by_web_part_id(webPart_id).get_position_of_web_part.post(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_pages_as_site_page_created_by_user_update_mailbox_settings(
        self,
        site_id: str,
        baseSitePage_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Update property mailboxSettings value..
        SharePoint operation: PATCH /sites/{site-id}/pages/{baseSitePage-id}/graph.sitePage/createdByUser/mailboxSettings
        Operation type: pages
        Args:
            site_id (str, required): SharePoint site id identifier
            baseSitePage_id (str, required): SharePoint baseSitePage id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for SharePoint operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).pages.by_base_site_page_id(baseSitePage_id).graph_site_page.created_by_user.mailbox_settings.patch(body=request_body, request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_pages_as_site_page_created_by_user_list_service_provisioning_errors(
        self,
        site_id: str,
        baseSitePage_id: str,
        dollar_orderby: Optional[List[str]] = None,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Get serviceProvisioningErrors property value.
        SharePoint operation: GET /sites/{site-id}/pages/{baseSitePage-id}/graph.sitePage/createdByUser/serviceProvisioningErrors
        Operation type: pages
        Args:
            site_id (str, required): SharePoint site id identifier
            baseSitePage_id (str, required): SharePoint baseSitePage id identifier
            dollar_orderby (List[str], optional): Order items by property values
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = PagesRequestBuilder.PagesRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = PagesRequestBuilder.PagesRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).pages.by_base_site_page_id(baseSitePage_id).graph_site_page.created_by_user.service_provisioning_errors.get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_pages_as_site_page_last_modified_by_user_update_mailbox_settings(
        self,
        site_id: str,
        baseSitePage_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Update property mailboxSettings value..
        SharePoint operation: PATCH /sites/{site-id}/pages/{baseSitePage-id}/graph.sitePage/lastModifiedByUser/mailboxSettings
        Operation type: pages
        Args:
            site_id (str, required): SharePoint site id identifier
            baseSitePage_id (str, required): SharePoint baseSitePage id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for SharePoint operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).pages.by_base_site_page_id(baseSitePage_id).graph_site_page.last_modified_by_user.mailbox_settings.patch(body=request_body, request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_pages_as_site_page_last_modified_by_user_list_service_provisioning_errors(
        self,
        site_id: str,
        baseSitePage_id: str,
        dollar_orderby: Optional[List[str]] = None,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Get serviceProvisioningErrors property value.
        SharePoint operation: GET /sites/{site-id}/pages/{baseSitePage-id}/graph.sitePage/lastModifiedByUser/serviceProvisioningErrors
        Operation type: pages
        Args:
            site_id (str, required): SharePoint site id identifier
            baseSitePage_id (str, required): SharePoint baseSitePage id identifier
            dollar_orderby (List[str], optional): Order items by property values
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = PagesRequestBuilder.PagesRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = PagesRequestBuilder.PagesRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).pages.by_base_site_page_id(baseSitePage_id).graph_site_page.last_modified_by_user.service_provisioning_errors.get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_pages_as_site_page_create_web_parts(
        self,
        site_id: str,
        baseSitePage_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Create new navigation property to webParts for sites.
        SharePoint operation: POST /sites/{site-id}/pages/{baseSitePage-id}/graph.sitePage/webParts
        Operation type: pages
        Args:
            site_id (str, required): SharePoint site id identifier
            baseSitePage_id (str, required): SharePoint baseSitePage id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for SharePoint operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).pages.by_base_site_page_id(baseSitePage_id).graph_site_page.web_parts.post(body=request_body, request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_pages_as_site_page_list_web_parts(
        self,
        site_id: str,
        baseSitePage_id: str,
        dollar_orderby: Optional[List[str]] = None,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Get webParts from sites.
        SharePoint operation: GET /sites/{site-id}/pages/{baseSitePage-id}/graph.sitePage/webParts
        Operation type: pages
        Args:
            site_id (str, required): SharePoint site id identifier
            baseSitePage_id (str, required): SharePoint baseSitePage id identifier
            dollar_orderby (List[str], optional): Order items by property values
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = PagesRequestBuilder.PagesRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = PagesRequestBuilder.PagesRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).pages.by_base_site_page_id(baseSitePage_id).graph_site_page.web_parts.get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_pages_as_site_page_delete_web_parts(
        self,
        site_id: str,
        baseSitePage_id: str,
        webPart_id: str,
        If_Match: Optional[str] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Delete webPart.
        SharePoint operation: DELETE /sites/{site-id}/pages/{baseSitePage-id}/graph.sitePage/webParts/{webPart-id}
        Operation type: pages
        Args:
            site_id (str, required): SharePoint site id identifier
            baseSitePage_id (str, required): SharePoint baseSitePage id identifier
            webPart_id (str, required): SharePoint webPart id identifier
            If_Match (str, optional): ETag
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).pages.by_base_site_page_id(baseSitePage_id).graph_site_page.web_parts.by_webPart_id(webPart_id).delete(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_pages_as_site_page_get_web_parts(
        self,
        site_id: str,
        baseSitePage_id: str,
        webPart_id: str,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Get webParts from sites.
        SharePoint operation: GET /sites/{site-id}/pages/{baseSitePage-id}/graph.sitePage/webParts/{webPart-id}
        Operation type: pages
        Args:
            site_id (str, required): SharePoint site id identifier
            baseSitePage_id (str, required): SharePoint baseSitePage id identifier
            webPart_id (str, required): SharePoint webPart id identifier
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = PagesRequestBuilder.PagesRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = PagesRequestBuilder.PagesRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).pages.by_base_site_page_id(baseSitePage_id).graph_site_page.web_parts.by_webPart_id(webPart_id).get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_pages_as_site_page_update_web_parts(
        self,
        site_id: str,
        baseSitePage_id: str,
        webPart_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Update the navigation property webParts in sites.
        SharePoint operation: PATCH /sites/{site-id}/pages/{baseSitePage-id}/graph.sitePage/webParts/{webPart-id}
        Operation type: pages
        Args:
            site_id (str, required): SharePoint site id identifier
            baseSitePage_id (str, required): SharePoint baseSitePage id identifier
            webPart_id (str, required): SharePoint webPart id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for SharePoint operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).pages.by_base_site_page_id(baseSitePage_id).graph_site_page.web_parts.by_webPart_id(webPart_id).patch(body=request_body, request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_site_pages_base_site_page_microsoft_graph_site_page_web_parts_web_part_get_position_of_web_part(
        self,
        site_id: str,
        baseSitePage_id: str,
        webPart_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Invoke action getPositionOfWebPart.
        SharePoint operation: POST /sites/{site-id}/pages/{baseSitePage-id}/graph.sitePage/webParts/{webPart-id}/getPositionOfWebPart
        Operation type: pages
        Args:
            site_id (str, required): SharePoint site id identifier
            baseSitePage_id (str, required): SharePoint baseSitePage id identifier
            webPart_id (str, required): SharePoint webPart id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).pages.by_base_site_page_id(baseSitePage_id).graph_site_page.web_parts.by_webPart_id(webPart_id).get_position_of_web_part.post(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_pages_last_modified_by_user_update_mailbox_settings(
        self,
        site_id: str,
        baseSitePage_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Update property mailboxSettings value..
        SharePoint operation: PATCH /sites/{site-id}/pages/{baseSitePage-id}/lastModifiedByUser/mailboxSettings
        Operation type: pages
        Args:
            site_id (str, required): SharePoint site id identifier
            baseSitePage_id (str, required): SharePoint baseSitePage id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for SharePoint operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).pages.by_base_site_page_id(baseSitePage_id).last_modified_by_user.mailbox_settings.patch(body=request_body, request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_pages_last_modified_by_user_list_service_provisioning_errors(
        self,
        site_id: str,
        baseSitePage_id: str,
        dollar_orderby: Optional[List[str]] = None,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Get serviceProvisioningErrors property value.
        SharePoint operation: GET /sites/{site-id}/pages/{baseSitePage-id}/lastModifiedByUser/serviceProvisioningErrors
        Operation type: pages
        Args:
            site_id (str, required): SharePoint site id identifier
            baseSitePage_id (str, required): SharePoint baseSitePage id identifier
            dollar_orderby (List[str], optional): Order items by property values
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = PagesRequestBuilder.PagesRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = PagesRequestBuilder.PagesRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).pages.by_base_site_page_id(baseSitePage_id).last_modified_by_user.service_provisioning_errors.get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    # ========== CONTENTTYPES OPERATIONS (31 methods) ==========

    async def sites_create_content_types(
        self,
        site_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Create a content type.
        SharePoint operation: POST /sites/{site-id}/contentTypes
        Operation type: contentTypes
        Args:
            site_id (str, required): SharePoint site id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for SharePoint operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).content_types.post(body=request_body, request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_list_content_types(
        self,
        site_id: str,
        dollar_orderby: Optional[List[str]] = None,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """List contentTypes in a site.
        SharePoint operation: GET /sites/{site-id}/contentTypes
        Operation type: contentTypes
        Args:
            site_id (str, required): SharePoint site id identifier
            dollar_orderby (List[str], optional): Order items by property values
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = SitesRequestBuilder.SitesRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = SitesRequestBuilder.SitesRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).content_types.get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_site_content_types_add_copy(
        self,
        site_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Invoke action addCopy.
        SharePoint operation: POST /sites/{site-id}/contentTypes/addCopy
        Operation type: contentTypes
        Args:
            site_id (str, required): SharePoint site id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for SharePoint operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).content_types.add_copy.post(body=request_body, request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_site_content_types_add_copy_from_content_type_hub(
        self,
        site_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Invoke action addCopyFromContentTypeHub.
        SharePoint operation: POST /sites/{site-id}/contentTypes/addCopyFromContentTypeHub
        Operation type: contentTypes
        Args:
            site_id (str, required): SharePoint site id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for SharePoint operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).content_types.add_copy_from_content_type_hub.post(body=request_body, request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_site_content_types_get_compatible_hub_content_types(
        self,
        site_id: str,
        dollar_select: Optional[List[str]] = None,
        dollar_orderby: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Invoke function getCompatibleHubContentTypes.
        SharePoint operation: GET /sites/{site-id}/contentTypes/getCompatibleHubContentTypes()
        Operation type: contentTypes
        Args:
            site_id (str, required): SharePoint site id identifier
            dollar_select (List[str], optional): Select properties to be returned
            dollar_orderby (List[str], optional): Order items by property values
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = SitesRequestBuilder.SitesRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = SitesRequestBuilder.SitesRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).content_types.get_compatible_hub_content_types().get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_delete_content_types(
        self,
        site_id: str,
        contentType_id: str,
        If_Match: Optional[str] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Delete contentType.
        SharePoint operation: DELETE /sites/{site-id}/contentTypes/{contentType-id}
        Operation type: contentTypes
        Args:
            site_id (str, required): SharePoint site id identifier
            contentType_id (str, required): SharePoint contentType id identifier
            If_Match (str, optional): ETag
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).content_types.by_content_type_id(contentType_id).delete(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_get_content_types(
        self,
        site_id: str,
        contentType_id: str,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Get contentType.
        SharePoint operation: GET /sites/{site-id}/contentTypes/{contentType-id}
        Operation type: contentTypes
        Args:
            site_id (str, required): SharePoint site id identifier
            contentType_id (str, required): SharePoint contentType id identifier
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = SitesRequestBuilder.SitesRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = SitesRequestBuilder.SitesRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).content_types.by_content_type_id(contentType_id).get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_update_content_types(
        self,
        site_id: str,
        contentType_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Update contentType.
        SharePoint operation: PATCH /sites/{site-id}/contentTypes/{contentType-id}
        Operation type: contentTypes
        Args:
            site_id (str, required): SharePoint site id identifier
            contentType_id (str, required): SharePoint contentType id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for SharePoint operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).content_types.by_content_type_id(contentType_id).patch(body=request_body, request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_site_content_types_content_type_associate_with_hub_sites(
        self,
        site_id: str,
        contentType_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Invoke action associateWithHubSites.
        SharePoint operation: POST /sites/{site-id}/contentTypes/{contentType-id}/associateWithHubSites
        Operation type: contentTypes
        Args:
            site_id (str, required): SharePoint site id identifier
            contentType_id (str, required): SharePoint contentType id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for SharePoint operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).content_types.by_content_type_id(contentType_id).associate_with_hub_sites.post(body=request_body, request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_content_types_get_base(
        self,
        site_id: str,
        contentType_id: str,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Get base from sites.
        SharePoint operation: GET /sites/{site-id}/contentTypes/{contentType-id}/base
        Operation type: contentTypes
        Args:
            site_id (str, required): SharePoint site id identifier
            contentType_id (str, required): SharePoint contentType id identifier
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = SitesRequestBuilder.SitesRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = SitesRequestBuilder.SitesRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).content_types.by_content_type_id(contentType_id).base.get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_content_types_list_base_types(
        self,
        site_id: str,
        contentType_id: str,
        dollar_orderby: Optional[List[str]] = None,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Get baseTypes from sites.
        SharePoint operation: GET /sites/{site-id}/contentTypes/{contentType-id}/baseTypes
        Operation type: contentTypes
        Args:
            site_id (str, required): SharePoint site id identifier
            contentType_id (str, required): SharePoint contentType id identifier
            dollar_orderby (List[str], optional): Order items by property values
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = SitesRequestBuilder.SitesRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = SitesRequestBuilder.SitesRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).content_types.by_content_type_id(contentType_id).base_types.get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_content_types_get_base_types(
        self,
        site_id: str,
        contentType_id: str,
        contentType_id1: str,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Get baseTypes from sites.
        SharePoint operation: GET /sites/{site-id}/contentTypes/{contentType-id}/baseTypes/{contentType-id1}
        Operation type: contentTypes
        Args:
            site_id (str, required): SharePoint site id identifier
            contentType_id (str, required): SharePoint contentType id identifier
            contentType_id1 (str, required): SharePoint contentType id1 identifier
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = SitesRequestBuilder.SitesRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = SitesRequestBuilder.SitesRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).content_types.by_content_type_id(contentType_id).base_types.by_baseType_id(contentType_id1).get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_content_types_create_column_links(
        self,
        site_id: str,
        contentType_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Create new navigation property to columnLinks for sites.
        SharePoint operation: POST /sites/{site-id}/contentTypes/{contentType-id}/columnLinks
        Operation type: contentTypes
        Args:
            site_id (str, required): SharePoint site id identifier
            contentType_id (str, required): SharePoint contentType id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for SharePoint operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).content_types.by_content_type_id(contentType_id).column_links.post(body=request_body, request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_content_types_list_column_links(
        self,
        site_id: str,
        contentType_id: str,
        dollar_orderby: Optional[List[str]] = None,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Get columnLinks from sites.
        SharePoint operation: GET /sites/{site-id}/contentTypes/{contentType-id}/columnLinks
        Operation type: contentTypes
        Args:
            site_id (str, required): SharePoint site id identifier
            contentType_id (str, required): SharePoint contentType id identifier
            dollar_orderby (List[str], optional): Order items by property values
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = SitesRequestBuilder.SitesRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = SitesRequestBuilder.SitesRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).content_types.by_content_type_id(contentType_id).column_links.get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_content_types_delete_column_links(
        self,
        site_id: str,
        contentType_id: str,
        columnLink_id: str,
        If_Match: Optional[str] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Delete navigation property columnLinks for sites.
        SharePoint operation: DELETE /sites/{site-id}/contentTypes/{contentType-id}/columnLinks/{columnLink-id}
        Operation type: contentTypes
        Args:
            site_id (str, required): SharePoint site id identifier
            contentType_id (str, required): SharePoint contentType id identifier
            columnLink_id (str, required): SharePoint columnLink id identifier
            If_Match (str, optional): ETag
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).content_types.by_content_type_id(contentType_id).column_links.by_column_link_id(columnLink_id).delete(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_content_types_get_column_links(
        self,
        site_id: str,
        contentType_id: str,
        columnLink_id: str,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Get columnLinks from sites.
        SharePoint operation: GET /sites/{site-id}/contentTypes/{contentType-id}/columnLinks/{columnLink-id}
        Operation type: contentTypes
        Args:
            site_id (str, required): SharePoint site id identifier
            contentType_id (str, required): SharePoint contentType id identifier
            columnLink_id (str, required): SharePoint columnLink id identifier
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = SitesRequestBuilder.SitesRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = SitesRequestBuilder.SitesRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).content_types.by_content_type_id(contentType_id).column_links.by_column_link_id(columnLink_id).get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_content_types_update_column_links(
        self,
        site_id: str,
        contentType_id: str,
        columnLink_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Update the navigation property columnLinks in sites.
        SharePoint operation: PATCH /sites/{site-id}/contentTypes/{contentType-id}/columnLinks/{columnLink-id}
        Operation type: contentTypes
        Args:
            site_id (str, required): SharePoint site id identifier
            contentType_id (str, required): SharePoint contentType id identifier
            columnLink_id (str, required): SharePoint columnLink id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for SharePoint operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).content_types.by_content_type_id(contentType_id).column_links.by_column_link_id(columnLink_id).patch(body=request_body, request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_content_types_list_column_positions(
        self,
        site_id: str,
        contentType_id: str,
        dollar_orderby: Optional[List[str]] = None,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Get columnPositions from sites.
        SharePoint operation: GET /sites/{site-id}/contentTypes/{contentType-id}/columnPositions
        Operation type: contentTypes
        Args:
            site_id (str, required): SharePoint site id identifier
            contentType_id (str, required): SharePoint contentType id identifier
            dollar_orderby (List[str], optional): Order items by property values
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = SitesRequestBuilder.SitesRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = SitesRequestBuilder.SitesRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).content_types.by_content_type_id(contentType_id).column_positions.get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_content_types_get_column_positions(
        self,
        site_id: str,
        contentType_id: str,
        columnDefinition_id: str,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Get columnPositions from sites.
        SharePoint operation: GET /sites/{site-id}/contentTypes/{contentType-id}/columnPositions/{columnDefinition-id}
        Operation type: contentTypes
        Args:
            site_id (str, required): SharePoint site id identifier
            contentType_id (str, required): SharePoint contentType id identifier
            columnDefinition_id (str, required): SharePoint columnDefinition id identifier
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = SitesRequestBuilder.SitesRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = SitesRequestBuilder.SitesRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).content_types.by_content_type_id(contentType_id).column_positions.by_columnPosition_id(columnDefinition_id).get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_content_types_create_columns(
        self,
        site_id: str,
        contentType_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Create a columnDefinition in a content type.
        SharePoint operation: POST /sites/{site-id}/contentTypes/{contentType-id}/columns
        Operation type: contentTypes
        Args:
            site_id (str, required): SharePoint site id identifier
            contentType_id (str, required): SharePoint contentType id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for SharePoint operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).content_types.by_content_type_id(contentType_id).columns.post(body=request_body, request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_content_types_list_columns(
        self,
        site_id: str,
        contentType_id: str,
        dollar_orderby: Optional[List[str]] = None,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """List columnDefinitions in a content type.
        SharePoint operation: GET /sites/{site-id}/contentTypes/{contentType-id}/columns
        Operation type: contentTypes
        Args:
            site_id (str, required): SharePoint site id identifier
            contentType_id (str, required): SharePoint contentType id identifier
            dollar_orderby (List[str], optional): Order items by property values
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = SitesRequestBuilder.SitesRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = SitesRequestBuilder.SitesRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).content_types.by_content_type_id(contentType_id).columns.get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_content_types_delete_columns(
        self,
        site_id: str,
        contentType_id: str,
        columnDefinition_id: str,
        If_Match: Optional[str] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Delete columnDefinition.
        SharePoint operation: DELETE /sites/{site-id}/contentTypes/{contentType-id}/columns/{columnDefinition-id}
        Operation type: contentTypes
        Args:
            site_id (str, required): SharePoint site id identifier
            contentType_id (str, required): SharePoint contentType id identifier
            columnDefinition_id (str, required): SharePoint columnDefinition id identifier
            If_Match (str, optional): ETag
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).content_types.by_content_type_id(contentType_id).columns.by_column_definition_id(columnDefinition_id).delete(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_content_types_get_columns(
        self,
        site_id: str,
        contentType_id: str,
        columnDefinition_id: str,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Get columnDefinition.
        SharePoint operation: GET /sites/{site-id}/contentTypes/{contentType-id}/columns/{columnDefinition-id}
        Operation type: contentTypes
        Args:
            site_id (str, required): SharePoint site id identifier
            contentType_id (str, required): SharePoint contentType id identifier
            columnDefinition_id (str, required): SharePoint columnDefinition id identifier
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = SitesRequestBuilder.SitesRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = SitesRequestBuilder.SitesRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).content_types.by_content_type_id(contentType_id).columns.by_column_definition_id(columnDefinition_id).get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_content_types_update_columns(
        self,
        site_id: str,
        contentType_id: str,
        columnDefinition_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Update columnDefinition.
        SharePoint operation: PATCH /sites/{site-id}/contentTypes/{contentType-id}/columns/{columnDefinition-id}
        Operation type: contentTypes
        Args:
            site_id (str, required): SharePoint site id identifier
            contentType_id (str, required): SharePoint contentType id identifier
            columnDefinition_id (str, required): SharePoint columnDefinition id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for SharePoint operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).content_types.by_content_type_id(contentType_id).columns.by_column_definition_id(columnDefinition_id).patch(body=request_body, request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_content_types_columns_get_source_column(
        self,
        site_id: str,
        contentType_id: str,
        columnDefinition_id: str,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Get sourceColumn from sites.
        SharePoint operation: GET /sites/{site-id}/contentTypes/{contentType-id}/columns/{columnDefinition-id}/sourceColumn
        Operation type: contentTypes
        Args:
            site_id (str, required): SharePoint site id identifier
            contentType_id (str, required): SharePoint contentType id identifier
            columnDefinition_id (str, required): SharePoint columnDefinition id identifier
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = SitesRequestBuilder.SitesRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = SitesRequestBuilder.SitesRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).content_types.by_content_type_id(contentType_id).columns.by_column_definition_id(columnDefinition_id).source_column.get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_site_content_types_content_type_copy_to_default_content_location(
        self,
        site_id: str,
        contentType_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Invoke action copyToDefaultContentLocation.
        SharePoint operation: POST /sites/{site-id}/contentTypes/{contentType-id}/copyToDefaultContentLocation
        Operation type: contentTypes
        Args:
            site_id (str, required): SharePoint site id identifier
            contentType_id (str, required): SharePoint contentType id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for SharePoint operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).content_types.by_content_type_id(contentType_id).copy_to_default_content_location.post(body=request_body, request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_site_content_types_content_type_is_published(
        self,
        site_id: str,
        contentType_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Invoke function isPublished.
        SharePoint operation: GET /sites/{site-id}/contentTypes/{contentType-id}/isPublished()
        Operation type: contentTypes
        Args:
            site_id (str, required): SharePoint site id identifier
            contentType_id (str, required): SharePoint contentType id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = SitesRequestBuilder.SitesRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = SitesRequestBuilder.SitesRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).content_types.by_content_type_id(contentType_id).is_published().get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_site_content_types_content_type_publish(
        self,
        site_id: str,
        contentType_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Invoke action publish.
        SharePoint operation: POST /sites/{site-id}/contentTypes/{contentType-id}/publish
        Operation type: contentTypes
        Args:
            site_id (str, required): SharePoint site id identifier
            contentType_id (str, required): SharePoint contentType id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).content_types.by_content_type_id(contentType_id).publish.post(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_site_content_types_content_type_unpublish(
        self,
        site_id: str,
        contentType_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Invoke action unpublish.
        SharePoint operation: POST /sites/{site-id}/contentTypes/{contentType-id}/unpublish
        Operation type: contentTypes
        Args:
            site_id (str, required): SharePoint site id identifier
            contentType_id (str, required): SharePoint contentType id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).content_types.by_content_type_id(contentType_id).unpublish.post(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_get_by_path_create_content_types(
        self,
        site_id: str,
        path: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Create new navigation property to contentTypes for sites.
        SharePoint operation: POST /sites/{site-id}/getByPath(path='{path}')/contentTypes
        Operation type: contentTypes
        Args:
            site_id (str, required): SharePoint site id identifier
            path (str, required): SharePoint path: path
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for SharePoint operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).get_by_path.content_types.post(body=request_body, request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_get_by_path_list_content_types(
        self,
        site_id: str,
        path: str,
        dollar_orderby: Optional[List[str]] = None,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Get contentTypes from sites.
        SharePoint operation: GET /sites/{site-id}/getByPath(path='{path}')/contentTypes
        Operation type: contentTypes
        Args:
            site_id (str, required): SharePoint site id identifier
            path (str, required): SharePoint path: path
            dollar_orderby (List[str], optional): Order items by property values
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = SitesRequestBuilder.SitesRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = SitesRequestBuilder.SitesRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).get_by_path.content_types.get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    # ========== COLUMNS OPERATIONS (11 methods) ==========

    async def sites_create_columns(
        self,
        site_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Create a columnDefinition in a site.
        SharePoint operation: POST /sites/{site-id}/columns
        Operation type: columns
        Args:
            site_id (str, required): SharePoint site id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for SharePoint operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).columns.post(body=request_body, request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_list_columns(
        self,
        site_id: str,
        dollar_orderby: Optional[List[str]] = None,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """List columns in a site.
        SharePoint operation: GET /sites/{site-id}/columns
        Operation type: columns
        Args:
            site_id (str, required): SharePoint site id identifier
            dollar_orderby (List[str], optional): Order items by property values
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = ColumnsRequestBuilder.ColumnsRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = ColumnsRequestBuilder.ColumnsRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).columns.get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_delete_columns(
        self,
        site_id: str,
        columnDefinition_id: str,
        If_Match: Optional[str] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Delete navigation property columns for sites.
        SharePoint operation: DELETE /sites/{site-id}/columns/{columnDefinition-id}
        Operation type: columns
        Args:
            site_id (str, required): SharePoint site id identifier
            columnDefinition_id (str, required): SharePoint columnDefinition id identifier
            If_Match (str, optional): ETag
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).columns.by_column_definition_id(columnDefinition_id).delete(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_get_columns(
        self,
        site_id: str,
        columnDefinition_id: str,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Get columns from sites.
        SharePoint operation: GET /sites/{site-id}/columns/{columnDefinition-id}
        Operation type: columns
        Args:
            site_id (str, required): SharePoint site id identifier
            columnDefinition_id (str, required): SharePoint columnDefinition id identifier
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = ColumnsRequestBuilder.ColumnsRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = ColumnsRequestBuilder.ColumnsRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).columns.by_column_definition_id(columnDefinition_id).get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_update_columns(
        self,
        site_id: str,
        columnDefinition_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Update the navigation property columns in sites.
        SharePoint operation: PATCH /sites/{site-id}/columns/{columnDefinition-id}
        Operation type: columns
        Args:
            site_id (str, required): SharePoint site id identifier
            columnDefinition_id (str, required): SharePoint columnDefinition id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for SharePoint operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).columns.by_column_definition_id(columnDefinition_id).patch(body=request_body, request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_columns_get_source_column(
        self,
        site_id: str,
        columnDefinition_id: str,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Get sourceColumn from sites.
        SharePoint operation: GET /sites/{site-id}/columns/{columnDefinition-id}/sourceColumn
        Operation type: columns
        Args:
            site_id (str, required): SharePoint site id identifier
            columnDefinition_id (str, required): SharePoint columnDefinition id identifier
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = ColumnsRequestBuilder.ColumnsRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = ColumnsRequestBuilder.ColumnsRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).columns.by_column_definition_id(columnDefinition_id).source_column.get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_list_external_columns(
        self,
        site_id: str,
        dollar_orderby: Optional[List[str]] = None,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Get externalColumns from sites.
        SharePoint operation: GET /sites/{site-id}/externalColumns
        Operation type: columns
        Args:
            site_id (str, required): SharePoint site id identifier
            dollar_orderby (List[str], optional): Order items by property values
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = SitesRequestBuilder.SitesRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = SitesRequestBuilder.SitesRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).external_columns.get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_get_external_columns(
        self,
        site_id: str,
        columnDefinition_id: str,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Get externalColumns from sites.
        SharePoint operation: GET /sites/{site-id}/externalColumns/{columnDefinition-id}
        Operation type: columns
        Args:
            site_id (str, required): SharePoint site id identifier
            columnDefinition_id (str, required): SharePoint columnDefinition id identifier
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = SitesRequestBuilder.SitesRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = SitesRequestBuilder.SitesRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).external_columns.by_externalColumn_id(columnDefinition_id).get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_get_by_path_create_columns(
        self,
        site_id: str,
        path: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Create new navigation property to columns for sites.
        SharePoint operation: POST /sites/{site-id}/getByPath(path='{path}')/columns
        Operation type: columns
        Args:
            site_id (str, required): SharePoint site id identifier
            path (str, required): SharePoint path: path
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for SharePoint operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).get_by_path.columns.post(body=request_body, request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_get_by_path_list_columns(
        self,
        site_id: str,
        path: str,
        dollar_orderby: Optional[List[str]] = None,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Get columns from sites.
        SharePoint operation: GET /sites/{site-id}/getByPath(path='{path}')/columns
        Operation type: columns
        Args:
            site_id (str, required): SharePoint site id identifier
            path (str, required): SharePoint path: path
            dollar_orderby (List[str], optional): Order items by property values
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = SitesRequestBuilder.SitesRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = SitesRequestBuilder.SitesRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).get_by_path.columns.get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_get_by_path_list_external_columns(
        self,
        site_id: str,
        path: str,
        dollar_orderby: Optional[List[str]] = None,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Get externalColumns from sites.
        SharePoint operation: GET /sites/{site-id}/getByPath(path='{path}')/externalColumns
        Operation type: columns
        Args:
            site_id (str, required): SharePoint site id identifier
            path (str, required): SharePoint path: path
            dollar_orderby (List[str], optional): Order items by property values
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = SitesRequestBuilder.SitesRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = SitesRequestBuilder.SitesRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).get_by_path.external_columns.get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    # ========== PERMISSIONS OPERATIONS (8 methods) ==========

    async def sites_get_by_path_create_permissions(
        self,
        site_id: str,
        path: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Create new navigation property to permissions for sites.
        SharePoint operation: POST /sites/{site-id}/getByPath(path='{path}')/permissions
        Operation type: permissions
        Args:
            site_id (str, required): SharePoint site id identifier
            path (str, required): SharePoint path: path
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for SharePoint operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).get_by_path.permissions.post(body=request_body, request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_get_by_path_list_permissions(
        self,
        site_id: str,
        path: str,
        dollar_orderby: Optional[List[str]] = None,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Get permissions from sites.
        SharePoint operation: GET /sites/{site-id}/getByPath(path='{path}')/permissions
        Operation type: permissions
        Args:
            site_id (str, required): SharePoint site id identifier
            path (str, required): SharePoint path: path
            dollar_orderby (List[str], optional): Order items by property values
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = SitesRequestBuilder.SitesRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = SitesRequestBuilder.SitesRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).get_by_path.permissions.get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_create_permissions(
        self,
        site_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Create permission.
        SharePoint operation: POST /sites/{site-id}/permissions
        Operation type: permissions
        Args:
            site_id (str, required): SharePoint site id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for SharePoint operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).permissions.post(body=request_body, request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_list_permissions(
        self,
        site_id: str,
        dollar_orderby: Optional[List[str]] = None,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """List permissions.
        SharePoint operation: GET /sites/{site-id}/permissions
        Operation type: permissions
        Args:
            site_id (str, required): SharePoint site id identifier
            dollar_orderby (List[str], optional): Order items by property values
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = SitesRequestBuilder.SitesRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = SitesRequestBuilder.SitesRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).permissions.get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_delete_permissions(
        self,
        site_id: str,
        permission_id: str,
        If_Match: Optional[str] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Delete permission.
        SharePoint operation: DELETE /sites/{site-id}/permissions/{permission-id}
        Operation type: permissions
        Args:
            site_id (str, required): SharePoint site id identifier
            permission_id (str, required): SharePoint permission id identifier
            If_Match (str, optional): ETag
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).permissions.by_permission_id(permission_id).delete(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_get_permissions(
        self,
        site_id: str,
        permission_id: str,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Get permission.
        SharePoint operation: GET /sites/{site-id}/permissions/{permission-id}
        Operation type: permissions
        Args:
            site_id (str, required): SharePoint site id identifier
            permission_id (str, required): SharePoint permission id identifier
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = SitesRequestBuilder.SitesRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = SitesRequestBuilder.SitesRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).permissions.by_permission_id(permission_id).get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_update_permissions(
        self,
        site_id: str,
        permission_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Update permission.
        SharePoint operation: PATCH /sites/{site-id}/permissions/{permission-id}
        Operation type: permissions
        Args:
            site_id (str, required): SharePoint site id identifier
            permission_id (str, required): SharePoint permission id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for SharePoint operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).permissions.by_permission_id(permission_id).patch(body=request_body, request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_site_permissions_permission_grant(
        self,
        site_id: str,
        permission_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Invoke action grant.
        SharePoint operation: POST /sites/{site-id}/permissions/{permission-id}/grant
        Operation type: permissions
        Args:
            site_id (str, required): SharePoint site id identifier
            permission_id (str, required): SharePoint permission id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for SharePoint operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).permissions.by_permission_id(permission_id).grant.post(body=request_body, request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    # ========== ANALYTICS OPERATIONS (18 methods) ==========

    async def sites_delete_analytics(
        self,
        site_id: str,
        If_Match: Optional[str] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Delete navigation property analytics for sites.
        SharePoint operation: DELETE /sites/{site-id}/analytics
        Operation type: analytics
        Args:
            site_id (str, required): SharePoint site id identifier
            If_Match (str, optional): ETag
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).analytics.delete(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_get_analytics(
        self,
        site_id: str,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Get analytics from sites.
        SharePoint operation: GET /sites/{site-id}/analytics
        Operation type: analytics
        Args:
            site_id (str, required): SharePoint site id identifier
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = SitesRequestBuilder.SitesRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = SitesRequestBuilder.SitesRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).analytics.get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_update_analytics(
        self,
        site_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Update the navigation property analytics in sites.
        SharePoint operation: PATCH /sites/{site-id}/analytics
        Operation type: analytics
        Args:
            site_id (str, required): SharePoint site id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for SharePoint operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).analytics.patch(body=request_body, request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_analytics_get_all_time(
        self,
        site_id: str,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Get allTime from sites.
        SharePoint operation: GET /sites/{site-id}/analytics/allTime
        Operation type: analytics
        Args:
            site_id (str, required): SharePoint site id identifier
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = SitesRequestBuilder.SitesRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = SitesRequestBuilder.SitesRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).analytics.all_time.get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_analytics_create_item_activity_stats(
        self,
        site_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Create new navigation property to itemActivityStats for sites.
        SharePoint operation: POST /sites/{site-id}/analytics/itemActivityStats
        Operation type: analytics
        Args:
            site_id (str, required): SharePoint site id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for SharePoint operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).analytics.item_activity_stats.post(body=request_body, request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_analytics_list_item_activity_stats(
        self,
        site_id: str,
        dollar_orderby: Optional[List[str]] = None,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Get itemActivityStats from sites.
        SharePoint operation: GET /sites/{site-id}/analytics/itemActivityStats
        Operation type: analytics
        Args:
            site_id (str, required): SharePoint site id identifier
            dollar_orderby (List[str], optional): Order items by property values
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = SitesRequestBuilder.SitesRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = SitesRequestBuilder.SitesRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).analytics.item_activity_stats.get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_analytics_delete_item_activity_stats(
        self,
        site_id: str,
        itemActivityStat_id: str,
        If_Match: Optional[str] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Delete navigation property itemActivityStats for sites.
        SharePoint operation: DELETE /sites/{site-id}/analytics/itemActivityStats/{itemActivityStat-id}
        Operation type: analytics
        Args:
            site_id (str, required): SharePoint site id identifier
            itemActivityStat_id (str, required): SharePoint itemActivityStat id identifier
            If_Match (str, optional): ETag
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).analytics.item_activity_stats.by_itemActivityStat_id(itemActivityStat_id).delete(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_analytics_get_item_activity_stats(
        self,
        site_id: str,
        itemActivityStat_id: str,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Get itemActivityStats from sites.
        SharePoint operation: GET /sites/{site-id}/analytics/itemActivityStats/{itemActivityStat-id}
        Operation type: analytics
        Args:
            site_id (str, required): SharePoint site id identifier
            itemActivityStat_id (str, required): SharePoint itemActivityStat id identifier
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = SitesRequestBuilder.SitesRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = SitesRequestBuilder.SitesRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).analytics.item_activity_stats.by_itemActivityStat_id(itemActivityStat_id).get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_analytics_update_item_activity_stats(
        self,
        site_id: str,
        itemActivityStat_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Update the navigation property itemActivityStats in sites.
        SharePoint operation: PATCH /sites/{site-id}/analytics/itemActivityStats/{itemActivityStat-id}
        Operation type: analytics
        Args:
            site_id (str, required): SharePoint site id identifier
            itemActivityStat_id (str, required): SharePoint itemActivityStat id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for SharePoint operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).analytics.item_activity_stats.by_itemActivityStat_id(itemActivityStat_id).patch(body=request_body, request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_analytics_item_activity_stats_create_activities(
        self,
        site_id: str,
        itemActivityStat_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Create new navigation property to activities for sites.
        SharePoint operation: POST /sites/{site-id}/analytics/itemActivityStats/{itemActivityStat-id}/activities
        Operation type: analytics
        Args:
            site_id (str, required): SharePoint site id identifier
            itemActivityStat_id (str, required): SharePoint itemActivityStat id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for SharePoint operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).analytics.item_activity_stats.by_itemActivityStat_id(itemActivityStat_id).activities.post(body=request_body, request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_analytics_item_activity_stats_list_activities(
        self,
        site_id: str,
        itemActivityStat_id: str,
        dollar_orderby: Optional[List[str]] = None,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Get activities from sites.
        SharePoint operation: GET /sites/{site-id}/analytics/itemActivityStats/{itemActivityStat-id}/activities
        Operation type: analytics
        Args:
            site_id (str, required): SharePoint site id identifier
            itemActivityStat_id (str, required): SharePoint itemActivityStat id identifier
            dollar_orderby (List[str], optional): Order items by property values
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = SitesRequestBuilder.SitesRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = SitesRequestBuilder.SitesRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).analytics.item_activity_stats.by_itemActivityStat_id(itemActivityStat_id).activities.get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_analytics_item_activity_stats_delete_activities(
        self,
        site_id: str,
        itemActivityStat_id: str,
        itemActivity_id: str,
        If_Match: Optional[str] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Delete navigation property activities for sites.
        SharePoint operation: DELETE /sites/{site-id}/analytics/itemActivityStats/{itemActivityStat-id}/activities/{itemActivity-id}
        Operation type: analytics
        Args:
            site_id (str, required): SharePoint site id identifier
            itemActivityStat_id (str, required): SharePoint itemActivityStat id identifier
            itemActivity_id (str, required): SharePoint itemActivity id identifier
            If_Match (str, optional): ETag
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).analytics.item_activity_stats.by_itemActivityStat_id(itemActivityStat_id).activities.by_activitie_id(itemActivity_id).delete(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_analytics_item_activity_stats_get_activities(
        self,
        site_id: str,
        itemActivityStat_id: str,
        itemActivity_id: str,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Get activities from sites.
        SharePoint operation: GET /sites/{site-id}/analytics/itemActivityStats/{itemActivityStat-id}/activities/{itemActivity-id}
        Operation type: analytics
        Args:
            site_id (str, required): SharePoint site id identifier
            itemActivityStat_id (str, required): SharePoint itemActivityStat id identifier
            itemActivity_id (str, required): SharePoint itemActivity id identifier
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = SitesRequestBuilder.SitesRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = SitesRequestBuilder.SitesRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).analytics.item_activity_stats.by_itemActivityStat_id(itemActivityStat_id).activities.by_activitie_id(itemActivity_id).get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_analytics_item_activity_stats_update_activities(
        self,
        site_id: str,
        itemActivityStat_id: str,
        itemActivity_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Update the navigation property activities in sites.
        SharePoint operation: PATCH /sites/{site-id}/analytics/itemActivityStats/{itemActivityStat-id}/activities/{itemActivity-id}
        Operation type: analytics
        Args:
            site_id (str, required): SharePoint site id identifier
            itemActivityStat_id (str, required): SharePoint itemActivityStat id identifier
            itemActivity_id (str, required): SharePoint itemActivity id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for SharePoint operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).analytics.item_activity_stats.by_itemActivityStat_id(itemActivityStat_id).activities.by_activitie_id(itemActivity_id).patch(body=request_body, request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_analytics_get_last_seven_days(
        self,
        site_id: str,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Get lastSevenDays from sites.
        SharePoint operation: GET /sites/{site-id}/analytics/lastSevenDays
        Operation type: analytics
        Args:
            site_id (str, required): SharePoint site id identifier
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = SitesRequestBuilder.SitesRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = SitesRequestBuilder.SitesRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).analytics.last_seven_days.get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_get_by_path_delete_analytics(
        self,
        site_id: str,
        path: str,
        If_Match: Optional[str] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Delete navigation property analytics for sites.
        SharePoint operation: DELETE /sites/{site-id}/getByPath(path='{path}')/analytics
        Operation type: analytics
        Args:
            site_id (str, required): SharePoint site id identifier
            path (str, required): SharePoint path: path
            If_Match (str, optional): ETag
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).get_by_path.analytics.delete(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_get_by_path_get_analytics(
        self,
        site_id: str,
        path: str,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Get analytics from sites.
        SharePoint operation: GET /sites/{site-id}/getByPath(path='{path}')/analytics
        Operation type: analytics
        Args:
            site_id (str, required): SharePoint site id identifier
            path (str, required): SharePoint path: path
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = SitesRequestBuilder.SitesRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = SitesRequestBuilder.SitesRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).get_by_path.analytics.get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_get_by_path_update_analytics(
        self,
        site_id: str,
        path: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Update the navigation property analytics in sites.
        SharePoint operation: PATCH /sites/{site-id}/getByPath(path='{path}')/analytics
        Operation type: analytics
        Args:
            site_id (str, required): SharePoint site id identifier
            path (str, required): SharePoint path: path
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for SharePoint operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).get_by_path.analytics.patch(body=request_body, request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    # ========== OPERATIONS OPERATIONS (7 methods) ==========

    async def sites_get_by_path_create_operations(
        self,
        site_id: str,
        path: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Create new navigation property to operations for sites.
        SharePoint operation: POST /sites/{site-id}/getByPath(path='{path}')/operations
        Operation type: operations
        Args:
            site_id (str, required): SharePoint site id identifier
            path (str, required): SharePoint path: path
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for SharePoint operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).get_by_path.operations.post(body=request_body, request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_get_by_path_list_operations(
        self,
        site_id: str,
        path: str,
        dollar_orderby: Optional[List[str]] = None,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Get operations from sites.
        SharePoint operation: GET /sites/{site-id}/getByPath(path='{path}')/operations
        Operation type: operations
        Args:
            site_id (str, required): SharePoint site id identifier
            path (str, required): SharePoint path: path
            dollar_orderby (List[str], optional): Order items by property values
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = SitesRequestBuilder.SitesRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = SitesRequestBuilder.SitesRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).get_by_path.operations.get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_create_operations(
        self,
        site_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Create new navigation property to operations for sites.
        SharePoint operation: POST /sites/{site-id}/operations
        Operation type: operations
        Args:
            site_id (str, required): SharePoint site id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for SharePoint operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).operations.post(body=request_body, request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_list_operations(
        self,
        site_id: str,
        dollar_orderby: Optional[List[str]] = None,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """List operations on a site.
        SharePoint operation: GET /sites/{site-id}/operations
        Operation type: operations
        Args:
            site_id (str, required): SharePoint site id identifier
            dollar_orderby (List[str], optional): Order items by property values
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = SitesRequestBuilder.SitesRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = SitesRequestBuilder.SitesRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).operations.get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_delete_operations(
        self,
        site_id: str,
        richLongRunningOperation_id: str,
        If_Match: Optional[str] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Delete navigation property operations for sites.
        SharePoint operation: DELETE /sites/{site-id}/operations/{richLongRunningOperation-id}
        Operation type: operations
        Args:
            site_id (str, required): SharePoint site id identifier
            richLongRunningOperation_id (str, required): SharePoint richLongRunningOperation id identifier
            If_Match (str, optional): ETag
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).operations.by_rich_long_running_operation_id(richLongRunningOperation_id).delete(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_get_operations(
        self,
        site_id: str,
        richLongRunningOperation_id: str,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Get richLongRunningOperation.
        SharePoint operation: GET /sites/{site-id}/operations/{richLongRunningOperation-id}
        Operation type: operations
        Args:
            site_id (str, required): SharePoint site id identifier
            richLongRunningOperation_id (str, required): SharePoint richLongRunningOperation id identifier
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = SitesRequestBuilder.SitesRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = SitesRequestBuilder.SitesRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).operations.by_rich_long_running_operation_id(richLongRunningOperation_id).get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_update_operations(
        self,
        site_id: str,
        richLongRunningOperation_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Update the navigation property operations in sites.
        SharePoint operation: PATCH /sites/{site-id}/operations/{richLongRunningOperation-id}
        Operation type: operations
        Args:
            site_id (str, required): SharePoint site id identifier
            richLongRunningOperation_id (str, required): SharePoint richLongRunningOperation id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for SharePoint operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).operations.by_rich_long_running_operation_id(richLongRunningOperation_id).patch(body=request_body, request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

