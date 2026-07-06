"""Confluence Data Center / Server toolset (standalone, REST v1).

Independent of the Cloud ``Confluence`` action — it neither imports nor subclasses
it, so the two toolsets cannot break each other (mirrors the connector split
``confluence_cloud`` vs ``confluence_datacenter``, and the ``jira_data_center``
toolset). It wraps the shared ``ConfluenceDataSource`` and calls only its ``*_v1``
methods (REST v1, ``/rest/api/...``), applying the Data Center deltas:

- there is no Cloud v2 API — pages/spaces/comments go through ``/rest/api/content`` etc.;
- users are identified by ``username``/``userKey``, NOT ``accountId``;
- spaces are referenced by their KEY, NOT a numeric v2 space id;
- content search is v1 CQL with ``start``/``limit`` offset paging;
- browse URLs come from the response ``_links`` (``base`` + ``webui``) against the
  configured instance ``baseUrl`` directly (no Cloud cloud-id proxy).
"""

import html
import json
import logging
import re
from typing import Any, Optional

from pydantic import BaseModel, Field

from app.agents.tools.config import ToolCategory
from app.agents.tools.decorator import tool
from app.agents.tools.models import ToolIntent
from app.connectors.core.constants import IconPaths
from app.connectors.core.registry.auth_builder import AuthBuilder, AuthType
from app.connectors.core.registry.tool_builder import ToolsetBuilder, ToolsetCategory
from app.connectors.core.registry.types import AuthField, DocumentationLink
from app.sources.client.confluence.confluence import ConfluenceClient
from app.sources.client.http.exception.exception import HttpStatusCode
from app.sources.client.http.http_response import HTTPResponse
from app.sources.external.confluence.confluence import ConfluenceDataSource

logger = logging.getLogger(__name__)

_APP = "confluencedatacenter"

# Whitelist for the ``order_by`` CQL clause on `search_content`: a field name
# with an optional asc/desc, comma-separated for multi-key sorts. Confluence
# rejects unknown fields with a 400, so field names themselves aren't validated.
_ORDER_BY_PATTERN = re.compile(
    r"^\s*[A-Za-z_][A-Za-z0-9_]*(\s+(asc|desc))?"
    r"(\s*,\s*[A-Za-z_][A-Za-z0-9_]*(\s+(asc|desc))?)*\s*$",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Pydantic input schemas (self-contained — no import from the Cloud module)
# ---------------------------------------------------------------------------
class CreatePageInput(BaseModel):
    """Schema for creating Confluence Data Center pages"""
    space_key: str = Field(description="Space KEY (e.g. 'DS', 'ENG', '~jdoe'). Data Center uses the key, not a numeric id.")
    page_title: str = Field(description="Page title")
    page_content: str = Field(description="Page content in storage format (XHTML)")
    parent_page_id: Optional[str] = Field(default=None, description="Parent page id to nest this page under (optional)")


class GetPageContentInput(BaseModel):
    """Schema for getting page content"""
    page_id: str = Field(description="Page id")


class UpdatePageInput(BaseModel):
    """Schema for updating a Confluence Data Center page"""
    page_id: str = Field(description="Page id")
    page_title: Optional[str] = Field(default=None, description="New page title (optional)")
    page_content: Optional[str] = Field(default=None, description="New page content in storage format (optional)")
    parent_page_id: Optional[str] = Field(default=None, description="Move the page under this parent page id (optional; re-parents the page)")


class GetChildPagesInput(BaseModel):
    """Schema for getting the direct child pages of a page"""
    page_id: str = Field(description="The parent page id")
    limit: Optional[int] = Field(default=None, description="Max child pages to return")


class GetPageVersionsInput(BaseModel):
    """Schema for getting a page's version history"""
    page_id: str = Field(description="Page id")


class AddCommentInput(BaseModel):
    """Schema for commenting on a Confluence Data Center page"""
    page_id: str = Field(description="Page id")
    comment_text: str = Field(description="Comment text (plain text is HTML-escaped and wrapped; storage XHTML is passed through)")
    parent_comment_id: Optional[str] = Field(default=None, description="Parent comment id when replying (optional)")


class GetCommentsInput(BaseModel):
    """Schema for getting comments on a page"""
    page_id: str = Field(description="Page id")


class GetPagesInSpaceInput(BaseModel):
    """Schema for listing pages in a space"""
    space_key: str = Field(description="Space KEY (e.g. 'DS')")
    limit: Optional[int] = Field(default=None, description="Max pages to return")


class SearchPagesInput(BaseModel):
    """Schema for fuzzy page-title search"""
    title: str = Field(description="Page title fragment to search (fuzzy)")
    space_key: Optional[str] = Field(default=None, description="Space KEY to limit the search (optional)")
    limit: Optional[int] = Field(default=None, description="Max results")


class SearchContentInput(BaseModel):
    """Schema for full-text Confluence Data Center content search"""
    query: Optional[str] = Field(default=None, description="Free-text search across title/body/comments")
    space_key: Optional[str] = Field(default=None, description="Space KEY to restrict the search (optional)")
    content_types: Optional[list[str]] = Field(default=None, description="Content types: 'page', 'blogpost', or both. Defaults to both.")
    labels: Optional[list[str]] = Field(default=None, description="Label names to filter by (optional)")
    order_by: Optional[str] = Field(default=None, description="CQL ORDER BY clause, e.g. 'lastmodified desc' (optional)")
    limit: Optional[int] = Field(default=25, description="Max results (1-50). Default 25.")


class GetSpaceInput(BaseModel):
    """Schema for getting a single space by key"""
    space_key: str = Field(description="Space KEY (e.g. 'DS')")


class SearchUsersInput(BaseModel):
    """Schema for searching Data Center users by name / username / email"""
    query: str = Field(description="Display name or name fragment to match (matches the user's full name)")
    max_results: Optional[int] = Field(default=10, description="Max users to return (1-50). Default 10.")


@ToolsetBuilder("ConfluenceDataCenter")\
    .in_group("Atlassian")\
    .with_description(
        "Confluence Data Center / Server integration for wiki pages, documentation, and knowledge management"
    )\
    .with_category(ToolsetCategory.APP)\
    .with_auth([
        # Personal Access Token — apiToken only (no email) -> Bearer against baseUrl.
        AuthBuilder.type(AuthType.API_TOKEN).fields([
            AuthField(
                name="baseUrl",
                display_name="Base URL",
                placeholder="https://confluence.yourcompany.com",
                description="The base URL of your Confluence Data Center / Server instance",
                field_type="URL",
                required=True,
                usage="CONFIGURE",
                max_length=2000,
                is_secret=False,
            ),
            AuthField(
                name="apiToken",
                display_name="Personal Access Token",
                placeholder="your-personal-access-token",
                description="Personal Access Token from your Confluence profile settings",
                field_type="PASSWORD",
                required=True,
                usage="AUTHENTICATE",
                max_length=2000,
                is_secret=True,
            ),
        ]),
        # Username + password -> HTTP Basic against baseUrl (older DC instances).
        AuthBuilder.type(AuthType.BASIC_AUTH).fields([
            AuthField(
                name="baseUrl",
                display_name="Base URL",
                placeholder="https://confluence.yourcompany.com",
                description="The base URL of your Confluence Data Center / Server instance",
                field_type="URL",
                required=True,
                usage="CONFIGURE",
                max_length=2000,
                is_secret=False,
            ),
            AuthField(
                name="username",
                display_name="Username",
                placeholder="your-username",
                description="Your Confluence Data Center account username",
                field_type="TEXT",
                required=True,
                usage="AUTHENTICATE",
                max_length=500,
                is_secret=False,
            ),
            AuthField(
                name="password",
                display_name="Password",
                placeholder="your-password",
                description="Your Confluence Data Center account password",
                field_type="PASSWORD",
                required=True,
                usage="AUTHENTICATE",
                max_length=2000,
                is_secret=True,
            ),
        ]),
    ])\
    .configure(lambda builder: builder.with_icon(IconPaths.connector_icon("confluence"))
        .add_documentation_link(DocumentationLink(
            "Confluence Data Center REST API",
            "https://developer.atlassian.com/server/confluence/confluence-server-rest-api/",
            "setup",
        ))
        .add_documentation_link(DocumentationLink(
            "Pipeshub Documentation",
            "https://docs.pipeshub.com/toolsets/confluence/confluence",
            "pipeshub",
        )))\
    .build_decorator()
class ConfluenceDataCenter:
    """Confluence Data Center / Server tool exposed to agents using ``ConfluenceDataSource`` (v1)."""

    def __init__(self, client: ConfluenceClient) -> None:
        self.client = ConfluenceDataSource(client)
        self._site_url: Optional[str] = None

    # ------------------------------------------------------------------
    # Response / error helpers
    # ------------------------------------------------------------------
    def _handle_response(
        self,
        response: HTTPResponse,
        success_message: str,
        include_guidance: bool = False,
    ) -> tuple[bool, str]:
        if response.status in [HttpStatusCode.SUCCESS.value, HttpStatusCode.CREATED.value, HttpStatusCode.NO_CONTENT.value]:
            try:
                data = response.json() if response.status != HttpStatusCode.NO_CONTENT.value else {}
                return True, json.dumps({"message": success_message, "data": data})
            except Exception as e:
                logger.error(f"Error parsing response: {e}")
                return True, json.dumps({"message": success_message, "data": {}})

        error_text = ""
        error_message = None
        try:
            if response.is_json:
                error_data = response.json()
                if isinstance(error_data, dict):
                    error_message = error_data.get("message") or error_data.get("error")
                    error_text = str(error_message) if error_message else json.dumps(error_data)
                else:
                    error_text = str(error_data)
            else:
                error_text = response.text() if hasattr(response, "text") else str(response)
        except Exception as e:
            logger.debug(f"Error parsing error response: {e}")
            error_text = response.text() if hasattr(response, "text") else str(response)

        error_response: dict[str, object] = {
            "error": error_message or f"HTTP {response.status}",
            "status_code": response.status,
            "details": error_text,
        }
        if include_guidance:
            guidance = self._get_error_guidance(response.status)
            if guidance:
                error_response["guidance"] = guidance

        logger.error(f"HTTP error {response.status}: {error_text}")
        return False, json.dumps(error_response)

    def _get_error_guidance(self, status_code: int) -> Optional[str]:
        guidance_map = {
            HttpStatusCode.UNAUTHORIZED.value: (
                "Authentication failed. Check that the Personal Access Token (or username/password) "
                "is valid and not expired, and that the base URL points at the Confluence Data Center instance."
            ),
            HttpStatusCode.FORBIDDEN.value: (
                "Access forbidden. The account may lack permission for this space/operation."
            ),
            HttpStatusCode.NOT_FOUND.value: (
                "Resource not found. Check the space key / page id exists and the base URL is correct."
            ),
            HttpStatusCode.BAD_REQUEST.value: (
                "Bad request. Common causes: a page with the same title already exists in the space, "
                "invalid storage-format body, invalid CQL, or a stale page version. See 'details' for the exact reason."
            ),
        }
        return guidance_map.get(status_code)

    # ------------------------------------------------------------------
    # DC-specific helpers
    # ------------------------------------------------------------------
    async def _get_site_url(self) -> Optional[str]:
        """Data Center browse URLs use the configured instance base URL directly.

        ``ConfluenceDataSource.base_url`` is populated (and trailing-slash-stripped) at
        init, so use it directly rather than re-deriving from the private HTTP client.
        """
        if self._site_url:
            return self._site_url
        base_url = getattr(self.client, "base_url", None)
        if base_url:
            self._site_url = base_url
            return self._site_url
        return None

    def _url_from_links(self, links: Any, base: Optional[str]) -> Optional[str]:
        """Build a browse URL from a v1 ``_links`` block.

        DC exposes a relative ``_links.webui``; the browse URL is that path joined to
        the instance root. Single-content responses carry the root in ``_links.base``;
        **list** responses carry it only at the response root (passed in as ``base``),
        so each item's ``_links`` has just ``webui``/``self``. Resolution order mirrors
        the DC connector: item ``base`` → caller-supplied ``base`` → derive from
        ``_links.self`` (split on ``/rest/api/``).
        """
        if not isinstance(links, dict):
            return None
        webui = links.get("webui")
        if not webui:
            return None
        resolved = links.get("base") or base
        if not resolved:
            self_link = links.get("self")
            if isinstance(self_link, str) and "/rest/api/" in self_link:
                resolved = self_link.split("/rest/api/")[0]
        if not resolved:
            return None
        return f"{resolved.rstrip('/')}{webui}"

    def _page_url(self, links: Any, page_id: Optional[str], base: Optional[str]) -> Optional[str]:
        """Best-effort page URL: prefer ``_links``, else the stable ``viewpage.action`` deep-link."""
        url = self._url_from_links(links, base)
        if url:
            return url
        if base and page_id:
            return f"{base.rstrip('/')}/pages/viewpage.action?pageId={page_id}"
        return None

    @staticmethod
    def _response_base(data: Any, site_url: Optional[str]) -> Optional[str]:
        """Instance root for building item URLs off a **list** response.

        v1 list envelopes put the base at the response root (``_links.base``); its
        items don't repeat it. Prefer that over the configured ``site_url`` so URLs
        stay correct under reverse proxies / context paths that differ from the
        admin-supplied base URL.
        """
        links = data.get("_links") if isinstance(data, dict) else None
        base = links.get("base") if isinstance(links, dict) else None
        return base or site_url

    @staticmethod
    def _to_storage_body(text: str) -> str:
        """Wrap plain text as a storage-format paragraph; pass through anything that already looks like XHTML."""
        stripped = text.strip()
        if stripped.startswith("<"):
            return text
        return f"<p>{html.escape(text)}</p>"

    @staticmethod
    def _clean_user(user: dict[str, Any]) -> dict[str, Any]:
        """Reduce a DC user object to its stable identity fields (username/userKey, no accountId)."""
        out: dict[str, Any] = {}
        for k in ("username", "userKey", "displayName"):
            if user.get(k):
                out[k] = user.get(k)
        email = user.get("email") or user.get("emailAddress")
        if email:
            out["email"] = email
        return out

    # ------------------------------------------------------------------
    # Tools — connection / identity
    # ------------------------------------------------------------------
    @tool(
        app_name=_APP,
        tool_name="validate_connection",
        description="Validate the Confluence Data Center connection and provide diagnostics",
        parameters=[],
        returns="Connection validation status with the current user",
        primary_intent=ToolIntent.UTILITY,
        category=ToolCategory.DOCUMENTATION,
    )
    async def validate_connection(self) -> tuple[bool, str]:
        try:
            response = await self.client.get_current_user_v1()
            if response.status == HttpStatusCode.SUCCESS.value:
                user = response.json()
                return True, json.dumps({
                    "message": "Confluence Data Center connection is valid",
                    "data": self._clean_user(user if isinstance(user, dict) else {}),
                })
            return self._handle_response(response, "Connection validated", include_guidance=True)
        except Exception as e:
            logger.error(f"Error validating Confluence DC connection: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name=_APP,
        tool_name="get_current_user",
        description="Get the current authenticated Confluence Data Center user (username, userKey, displayName)",
        parameters=[],
        returns="Current user's account details",
        primary_intent=ToolIntent.SEARCH,
        category=ToolCategory.DOCUMENTATION,
    )
    async def get_current_user(self) -> tuple[bool, str]:
        try:
            response = await self.client.get_current_user_v1()
            if response.status == HttpStatusCode.SUCCESS.value:
                user = response.json()
                return True, json.dumps({
                    "message": "Current user fetched successfully",
                    "data": self._clean_user(user if isinstance(user, dict) else {}),
                })
            return self._handle_response(response, "Current user fetched", include_guidance=True)
        except Exception as e:
            logger.error(f"Error getting current user: {e}")
            return False, json.dumps({"error": str(e)})

    # ------------------------------------------------------------------
    # Tools — spaces
    # ------------------------------------------------------------------
    @tool(
        app_name=_APP,
        tool_name="get_spaces",
        description="List Confluence Data Center spaces",
        parameters=[],
        returns="List of spaces (key, name, type, url)",
        primary_intent=ToolIntent.SEARCH,
        category=ToolCategory.DOCUMENTATION,
    )
    async def get_spaces(self) -> tuple[bool, str]:
        try:
            site_url = await self._get_site_url()
            spaces: list[dict[str, Any]] = []
            base: Optional[str] = None
            start = 0
            page = 100
            max_pages = 50  # bound the scan at ~5000 spaces
            exhausted = True
            for page_num in range(max_pages):
                # No expand: listing spaces doesn't need per-space permissions/history.
                response = await self.client.get_spaces_v1(expand="", limit=page, start=start)
                if response.status != HttpStatusCode.SUCCESS.value:
                    if start == 0:
                        return self._handle_response(response, "Spaces fetched successfully", include_guidance=True)
                    break  # keep spaces gathered from earlier pages
                data = response.json()
                if base is None:
                    base = self._response_base(data, site_url)
                results = data.get("results", []) if isinstance(data, dict) else []
                for s in results:
                    if not isinstance(s, dict):
                        continue
                    entry = {
                        "id": s.get("id"),
                        "key": s.get("key"),
                        "name": s.get("name"),
                        "type": s.get("type"),
                        "status": s.get("status"),
                    }
                    url = self._url_from_links(s.get("_links"), base)
                    if url:
                        entry["url"] = url
                    spaces.append(entry)
                if len(results) < page:
                    break
                start += page
                if page_num == max_pages - 1:
                    exhausted = False
            result: dict[str, Any] = {
                "message": "Spaces fetched successfully",
                "data": {"results": spaces, "total": len(spaces)},
            }
            if not exhausted:
                result["note"] = f"Listed the first {max_pages * page} spaces; more may exist."
            return True, json.dumps(result)
        except Exception as e:
            logger.error(f"Error getting spaces: {e}")
            return False, json.dumps({"error": str(e)})

    async def _resolve_space_key(self, space_key: str) -> Optional[str]:
        """Resolve a case-insensitive space key to its stored casing via the space list.

        DC's ``?spaceKey=`` filter is exact-match, so a correct-but-wrong-case key
        (e.g. ``DS`` when the space is stored ``ds``) returns no results. Scan the
        space list and match case-insensitively; return the real key or ``None``.
        """
        needle = space_key.strip().lower()
        start = 0
        page = 100
        for _ in range(50):
            response = await self.client.get_spaces_v1(expand="", limit=page, start=start)
            if response.status != HttpStatusCode.SUCCESS.value:
                return None
            data = response.json()
            results = data.get("results", []) if isinstance(data, dict) else []
            for s in results:
                if isinstance(s, dict) and str(s.get("key", "")).lower() == needle:
                    return s.get("key")
            if len(results) < page:
                return None
            start += page
        return None

    @tool(
        app_name=_APP,
        tool_name="get_space",
        description="Get a single Confluence Data Center space by key",
        args_schema=GetSpaceInput,
        returns="Space details",
        primary_intent=ToolIntent.SEARCH,
        category=ToolCategory.DOCUMENTATION,
    )
    async def get_space(self, space_key: str) -> tuple[bool, str]:
        try:
            # description.plain is not in the default expand — request it so the
            # space summary is actually populated.
            response = await self.client.get_spaces_v1(keys=[space_key], expand="description.plain,homepage")
            data: Any = None
            space: Optional[dict[str, Any]] = None
            if response.status == HttpStatusCode.SUCCESS.value:
                data = response.json()
                results = data.get("results", []) if isinstance(data, dict) else []
                if results:
                    space = results[0]

            if space is None:
                # ?spaceKey= is exact-match, so a correct-but-wrong-case key
                # (e.g. "DS" vs stored "ds") returns nothing — resolve and retry once.
                resolved = await self._resolve_space_key(space_key)
                if resolved and resolved != space_key:
                    response = await self.client.get_spaces_v1(keys=[resolved], expand="description.plain,homepage")
                    if response.status == HttpStatusCode.SUCCESS.value:
                        data = response.json()
                        results = data.get("results", []) if isinstance(data, dict) else []
                        if results:
                            space = results[0]

            if space is None:
                if response.status != HttpStatusCode.SUCCESS.value:
                    return self._handle_response(response, "Space fetched successfully", include_guidance=True)
                return False, json.dumps({
                    "error": f"Space '{space_key}' not found",
                    "guidance": "Use get_spaces to list available space keys.",
                })

            site_url = await self._get_site_url()
            base = self._response_base(data, site_url)
            description = None
            desc_obj = space.get("description")
            if isinstance(desc_obj, dict):
                description = (desc_obj.get("plain") or {}).get("value")
            entry: dict[str, Any] = {
                "id": space.get("id"),
                "key": space.get("key"),
                "name": space.get("name"),
                "type": space.get("type"),
                "status": space.get("status"),
                "description": description,
            }
            creator = space.get("creator")
            if isinstance(creator, dict):
                cleaned_creator = self._clean_user(creator)
                if cleaned_creator:
                    entry["creator"] = cleaned_creator
            homepage = space.get("homepage")
            if isinstance(homepage, dict):
                # The space landing page — lets the agent read the space's main
                # content directly via get_page_content without another lookup.
                if homepage.get("id"):
                    entry["homepage_id"] = homepage.get("id")
                if homepage.get("title"):
                    entry["homepage_title"] = homepage.get("title")
                hp_url = self._page_url(homepage.get("_links"), homepage.get("id"), base)
                if hp_url:
                    entry["homepage_url"] = hp_url
            url = self._url_from_links(space.get("_links"), base)
            if url:
                entry["url"] = url
            return True, json.dumps({"message": "Space fetched successfully", "data": entry})
        except Exception as e:
            logger.error(f"Error getting space: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name=_APP,
        tool_name="get_pages_in_space",
        description="List pages in a Confluence Data Center space, most recently modified first",
        args_schema=GetPagesInSpaceInput,
        returns="List of pages (id, title, type, status, updated, url), newest first",
        primary_intent=ToolIntent.SEARCH,
        category=ToolCategory.DOCUMENTATION,
    )
    async def get_pages_in_space(self, space_key: str, limit: Optional[int] = None) -> tuple[bool, str]:
        try:
            effective_limit = limit or 25
            # Order by last-modified so the agent sees the freshest pages first, and
            # expand version to surface each page's last-updated timestamp.
            response = await self.client.get_pages_v1(
                space_key=space_key,
                limit=effective_limit,
                order_by="lastModified",
                sort_order="desc",
                expand="version",
            )
            if response.status == HttpStatusCode.SUCCESS.value:
                data = response.json()
                site_url = await self._get_site_url()
                base = self._response_base(data, site_url)
                pages = []
                for p in (data.get("results", []) if isinstance(data, dict) else []):
                    if not isinstance(p, dict):
                        continue
                    entry: dict[str, Any] = {
                        "id": p.get("id"),
                        "title": p.get("title"),
                        "type": p.get("type"),
                        "status": p.get("status"),
                    }
                    version = p.get("version")
                    if isinstance(version, dict) and version.get("when"):
                        entry["updated"] = version.get("when")
                    url = self._page_url(p.get("_links"), p.get("id"), base)
                    if url:
                        entry["url"] = url
                    pages.append(entry)
                result: dict[str, Any] = {
                    "message": "Pages fetched successfully",
                    "data": {"results": pages, "total": len(pages)},
                }
                next_link = (data.get("_links") or {}).get("next") if isinstance(data, dict) else None
                if next_link or len(pages) >= effective_limit:
                    result["note"] = (
                        f"Showing the {len(pages)} most recently modified pages; more may exist. "
                        "Increase limit or use search_pages to find a specific page."
                    )
                return True, json.dumps(result)
            return self._handle_response(response, "Pages fetched successfully", include_guidance=True)
        except Exception as e:
            logger.error(f"Error getting pages in space: {e}")
            return False, json.dumps({"error": str(e)})

    # ------------------------------------------------------------------
    # Tools — pages
    # ------------------------------------------------------------------
    @tool(
        app_name=_APP,
        tool_name="get_page_content",
        description="Get a Confluence Data Center page's content (storage format) by id",
        args_schema=GetPageContentInput,
        returns="Page details with body in storage format",
        primary_intent=ToolIntent.SEARCH,
        category=ToolCategory.DOCUMENTATION,
    )
    async def get_page_content(self, page_id: str) -> tuple[bool, str]:
        try:
            response = await self.client.get_page_content_v1(
                page_id=page_id, expand="body.storage,version,space,history.lastUpdated",
            )
            if response.status == HttpStatusCode.SUCCESS.value:
                data = response.json()
                site_url = await self._get_site_url()
                entry = self._simplify_page(data, site_url)
                return True, json.dumps({"message": "Page fetched successfully", "data": entry})
            return self._handle_response(response, "Page fetched successfully", include_guidance=True)
        except Exception as e:
            logger.error(f"Error getting page content: {e}")
            return False, json.dumps({"error": str(e)})

    def _simplify_page(self, data: dict[str, Any], site_url: Optional[str]) -> dict[str, Any]:
        if not isinstance(data, dict):
            return {}
        space = data.get("space") or {}
        version = data.get("version") or {}
        body = (data.get("body") or {}).get("storage") or {}
        entry = {
            "id": data.get("id"),
            "title": data.get("title"),
            "type": data.get("type"),
            "status": data.get("status"),
            "space_key": space.get("key") if isinstance(space, dict) else None,
            "version": version.get("number") if isinstance(version, dict) else None,
            "body": body.get("value") if isinstance(body, dict) else None,
        }
        url = self._page_url(data.get("_links"), data.get("id"), site_url)
        if url:
            entry["url"] = url
        return entry

    @tool(
        app_name=_APP,
        tool_name="create_page",
        description="Create a new Confluence Data Center page",
        args_schema=CreatePageInput,
        returns="Created page id, title and url",
        primary_intent=ToolIntent.ACTION,
        category=ToolCategory.DOCUMENTATION,
    )
    async def create_page(
        self,
        space_key: str,
        page_title: str,
        page_content: str,
        parent_page_id: Optional[str] = None,
    ) -> tuple[bool, str]:
        try:
            content: dict[str, Any] = {
                "type": "page",
                "title": page_title,
                "space": {"key": space_key},
                "body": {"storage": {"value": self._to_storage_body(page_content), "representation": "storage"}},
            }
            if parent_page_id:
                content["ancestors"] = [{"id": str(parent_page_id)}]
            response = await self.client.create_content_v1(body=content)
            if response.status in (HttpStatusCode.SUCCESS.value, HttpStatusCode.CREATED.value):
                data = response.json()
                site_url = await self._get_site_url()
                entry = {
                    "id": data.get("id"),
                    "title": data.get("title"),
                    "space_key": (data.get("space") or {}).get("key"),
                    "version": (data.get("version") or {}).get("number"),
                }
                url = self._page_url(data.get("_links"), data.get("id"), site_url)
                if url:
                    entry["url"] = url
                return True, json.dumps({"message": "Page created successfully", "data": entry})
            return self._handle_response(response, "Page created successfully", include_guidance=True)
        except Exception as e:
            logger.error(f"Error creating page: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name=_APP,
        tool_name="update_page",
        description="Update a Confluence Data Center page's title/content, or move it under a new parent",
        args_schema=UpdatePageInput,
        returns="Updated page details",
        primary_intent=ToolIntent.ACTION,
        category=ToolCategory.DOCUMENTATION,
    )
    async def update_page(
        self,
        page_id: str,
        page_title: Optional[str] = None,
        page_content: Optional[str] = None,
        parent_page_id: Optional[str] = None,
    ) -> tuple[bool, str]:
        try:
            if not page_title and not page_content and not parent_page_id:
                return False, json.dumps({
                    "error": "No updates provided",
                    "guidance": "Provide page_title, page_content, and/or parent_page_id to update.",
                })

            # v1 requires the current version number+1 on the PUT. If a concurrent edit
            # bumps the version between our read and write, Confluence returns 409; re-read
            # the fresh version and retry once before giving up.
            response = None
            for _ in range(2):
                current = await self.client.get_page_content_v1(page_id=page_id, expand="version,space")
                if current.status != HttpStatusCode.SUCCESS.value:
                    return self._handle_response(current, "Page updated successfully", include_guidance=True)
                current_data = current.json()
                current_version = ((current_data.get("version") or {}).get("number") or 0) if isinstance(current_data, dict) else 0
                current_title = current_data.get("title") if isinstance(current_data, dict) else None

                content: dict[str, Any] = {
                    "id": str(page_id),
                    "type": "page",
                    "title": page_title or current_title,
                    "version": {"number": int(current_version) + 1},
                }
                if page_content:
                    content["body"] = {"storage": {"value": self._to_storage_body(page_content), "representation": "storage"}}
                if parent_page_id:
                    # Re-parent (move) the page by setting a new ancestor on the update PUT.
                    content["ancestors"] = [{"id": str(parent_page_id)}]

                response = await self.client.update_content_v1(content_id=page_id, body=content)
                if response.status == HttpStatusCode.SUCCESS.value:
                    data = response.json()
                    site_url = await self._get_site_url()
                    return True, json.dumps({"message": "Page updated successfully", "data": self._simplify_page(data, site_url)})
                if response.status != HttpStatusCode.CONFLICT.value:
                    break  # non-conflict error — retrying won't help
            return self._handle_response(response, "Page updated successfully", include_guidance=True)
        except Exception as e:
            logger.error(f"Error updating page: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name=_APP,
        tool_name="get_child_pages",
        description="Get the direct child pages of a Confluence Data Center page",
        args_schema=GetChildPagesInput,
        returns="List of child pages (id, title, url)",
        primary_intent=ToolIntent.SEARCH,
        category=ToolCategory.DOCUMENTATION,
    )
    async def get_child_pages(self, page_id: str, limit: Optional[int] = None) -> tuple[bool, str]:
        try:
            page_limit = limit or 25
            response = await self.client.get_child_pages_v1(content_id=page_id, limit=page_limit)
            if response.status == HttpStatusCode.SUCCESS.value:
                data = response.json()
                site_url = await self._get_site_url()
                base = self._response_base(data, site_url)
                pages = []
                for p in (data.get("results", []) if isinstance(data, dict) else []):
                    if not isinstance(p, dict):
                        continue
                    entry = {"id": p.get("id"), "title": p.get("title"), "type": p.get("type"), "status": p.get("status")}
                    url = self._page_url(p.get("_links"), p.get("id"), base)
                    if url:
                        entry["url"] = url
                    pages.append(entry)
                result: dict[str, Any] = {"message": "Child pages fetched successfully", "data": {"results": pages, "total": len(pages)}}
                next_link = (data.get("_links") or {}).get("next") if isinstance(data, dict) else None
                if next_link or len(pages) >= page_limit:
                    result["note"] = f"Showing the first {len(pages)} child pages; more may exist. Increase limit."
                return True, json.dumps(result)
            return self._handle_response(response, "Child pages fetched successfully", include_guidance=True)
        except Exception as e:
            logger.error(f"Error getting child pages: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name=_APP,
        tool_name="get_page_versions",
        description="Get the version history of a Confluence Data Center page",
        args_schema=GetPageVersionsInput,
        returns="List of versions (number, when, author, message)",
        primary_intent=ToolIntent.SEARCH,
        category=ToolCategory.DOCUMENTATION,
    )
    async def get_page_versions(self, page_id: str) -> tuple[bool, str]:
        try:
            page_limit = 25
            response = await self.client.get_content_versions_v1(content_id=page_id, limit=page_limit)
            if response.status == HttpStatusCode.SUCCESS.value:
                data = response.json()
                versions = []
                for v in (data.get("results", []) if isinstance(data, dict) else []):
                    if not isinstance(v, dict):
                        continue
                    entry: dict[str, Any] = {
                        "number": v.get("number"),
                        "when": v.get("when"),
                        "minorEdit": v.get("minorEdit"),
                    }
                    if v.get("message"):
                        entry["message"] = v.get("message")
                    who = self._clean_user(v.get("by")) if isinstance(v.get("by"), dict) else {}
                    if who:
                        entry["author"] = who
                    versions.append(entry)
                result: dict[str, Any] = {
                    "message": "Page versions fetched successfully",
                    "data": {"results": versions, "total": len(versions)},
                    "page_id": page_id,
                }
                next_link = (data.get("_links") or {}).get("next") if isinstance(data, dict) else None
                if next_link or len(versions) >= page_limit:
                    result["note"] = f"Showing the {len(versions)} most recent versions; more may exist."
                return True, json.dumps(result)
            return self._handle_response(response, "Page versions fetched successfully", include_guidance=True)
        except Exception as e:
            logger.error(f"Error getting page versions: {e}")
            return False, json.dumps({"error": str(e)})

    # ------------------------------------------------------------------
    # Tools — comments
    # ------------------------------------------------------------------
    @tool(
        app_name=_APP,
        tool_name="add_comment",
        description="Add a comment to a Confluence Data Center page",
        args_schema=AddCommentInput,
        returns="Created comment details",
        primary_intent=ToolIntent.ACTION,
        category=ToolCategory.DOCUMENTATION,
    )
    async def add_comment(
        self,
        page_id: str,
        comment_text: str,
        parent_comment_id: Optional[str] = None,
    ) -> tuple[bool, str]:
        try:
            if not comment_text or not str(comment_text).strip():
                return False, json.dumps({"error": "Comment text is required and cannot be empty"})
            body_value = self._to_storage_body(str(comment_text))
            response = await self.client.create_comment_v1(
                page_id=page_id, body_value=body_value, parent_comment_id=parent_comment_id,
            )
            if response.status in (HttpStatusCode.SUCCESS.value, HttpStatusCode.CREATED.value):
                data = response.json()
                site_url = await self._get_site_url()
                entry = {"id": data.get("id"), "page_id": page_id}
                url = self._url_from_links(data.get("_links"), site_url)
                if url:
                    entry["url"] = url
                return True, json.dumps({"message": "Comment added successfully", "data": entry})
            return self._handle_response(response, "Comment added successfully", include_guidance=True)
        except Exception as e:
            logger.error(f"Error commenting on page: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name=_APP,
        tool_name="get_comments",
        description="Get comments on a Confluence Data Center page",
        args_schema=GetCommentsInput,
        returns="List of comments",
        primary_intent=ToolIntent.SEARCH,
        category=ToolCategory.DOCUMENTATION,
    )
    async def get_comments(self, page_id: str) -> tuple[bool, str]:
        try:
            page_size = 100
            response = await self.client.get_content_comments_v1(content_id=page_id, limit=page_size)
            if response.status == HttpStatusCode.SUCCESS.value:
                data = response.json()
                site_url = await self._get_site_url()
                base = self._response_base(data, site_url)
                comments = []
                for c in (data.get("results", []) if isinstance(data, dict) else []):
                    if not isinstance(c, dict):
                        continue
                    body = (c.get("body") or {}).get("storage") or {}
                    version = c.get("version") or {}
                    author = version.get("by") or {} if isinstance(version, dict) else {}
                    entry: dict[str, Any] = {
                        "id": c.get("id"),
                        "body": body.get("value") if isinstance(body, dict) else None,
                    }
                    if isinstance(version, dict) and version.get("when"):
                        entry["updated"] = version.get("when")
                    who = self._clean_user(author) if isinstance(author, dict) else {}
                    if who:
                        entry["author"] = who
                    url = self._url_from_links(c.get("_links"), base)
                    if url:
                        entry["url"] = url
                    comments.append(entry)
                result: dict[str, Any] = {
                    "message": "Comments fetched successfully",
                    "data": {"results": comments, "total": len(comments)},
                    "page_id": page_id,
                }
                next_link = (data.get("_links") or {}).get("next") if isinstance(data, dict) else None
                if next_link or len(comments) >= page_size:
                    result["note"] = f"Showing the first {len(comments)} comments; more may exist on this page."
                return True, json.dumps(result)
            return self._handle_response(response, "Comments fetched successfully", include_guidance=True)
        except Exception as e:
            logger.error(f"Error getting comments: {e}")
            return False, json.dumps({"error": str(e)})

    # ------------------------------------------------------------------
    # Tools — search
    # ------------------------------------------------------------------
    @staticmethod
    def _clean_excerpt(excerpt: Any) -> Optional[str]:
        """Strip Confluence's ``@@@hl@@@…@@@endhl@@@`` search-highlight markers; drop if empty."""
        if not isinstance(excerpt, str):
            return None
        cleaned = excerpt.replace("@@@hl@@@", "").replace("@@@endhl@@@", "").strip()
        return cleaned or None

    @staticmethod
    def _extract_labels(content: Any) -> list[str]:
        """Pull label names from an expanded ``content.metadata.labels.results`` block."""
        meta = content.get("metadata") if isinstance(content, dict) else None
        labels_obj = meta.get("labels") if isinstance(meta, dict) else None
        rows = labels_obj.get("results") if isinstance(labels_obj, dict) else None
        if not isinstance(rows, list):
            return []
        names = []
        for lbl in rows:
            if isinstance(lbl, dict):
                name = lbl.get("name") or lbl.get("label")
                if name:
                    names.append(name)
        return names

    @staticmethod
    def _add_search_truncation(payload: dict[str, Any], data: Any, returned: int) -> None:
        """Surface the true match count (``totalSize``) and note when results are capped."""
        total_size = data.get("totalSize") if isinstance(data, dict) else None
        if isinstance(total_size, int):
            payload["data"]["total_available"] = total_size
            if returned < total_size:
                payload["note"] = f"Showing {returned} of {total_size} matches; increase limit to see more."

    def _simplify_search_results(self, data: dict[str, Any], site_url: Optional[str]) -> list[dict[str, Any]]:
        results = []
        for item in (data.get("results", []) if isinstance(data, dict) else []):
            if not isinstance(item, dict):
                continue
            content = item.get("content") or {}
            entry: dict[str, Any] = {
                "id": content.get("id"),
                "title": content.get("title") or item.get("title"),
                "type": content.get("type"),
            }
            excerpt = self._clean_excerpt(item.get("excerpt"))
            if excerpt:
                entry["excerpt"] = excerpt
            if item.get("lastModified"):
                entry["updated"] = item.get("lastModified")
            labels = self._extract_labels(content)
            if labels:
                entry["labels"] = labels
            base = (data.get("_links") or {}).get("base") if isinstance(data.get("_links"), dict) else None
            rel = item.get("url")
            if base and rel:
                entry["url"] = f"{base.rstrip('/')}{rel}"
            else:
                url = self._page_url(content.get("_links"), content.get("id"), site_url)
                if url:
                    entry["url"] = url
            results.append(entry)
        return results

    @tool(
        app_name=_APP,
        tool_name="search_pages",
        description="Fuzzy-search Confluence Data Center pages by title",
        args_schema=SearchPagesInput,
        returns="Matching pages",
        primary_intent=ToolIntent.SEARCH,
        category=ToolCategory.DOCUMENTATION,
    )
    async def search_pages(self, title: str, space_key: Optional[str] = None, limit: Optional[int] = None) -> tuple[bool, str]:
        try:
            response = await self.client.search_pages_cql(search_term=title, space_id=space_key, limit=limit or 25)
            if response.status == HttpStatusCode.SUCCESS.value:
                data = response.json()
                site_url = await self._get_site_url()
                results = self._simplify_search_results(data, site_url)
                payload: dict[str, Any] = {"message": "Pages fetched successfully", "data": {"results": results, "total": len(results)}}
                self._add_search_truncation(payload, data, len(results))
                return True, json.dumps(payload)
            return self._handle_response(response, "Pages fetched successfully", include_guidance=True)
        except Exception as e:
            logger.error(f"Error searching pages: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name=_APP,
        tool_name="search_content",
        description="Full-text search Confluence Data Center content (pages, blog posts)",
        args_schema=SearchContentInput,
        returns="Matching content",
        primary_intent=ToolIntent.SEARCH,
        category=ToolCategory.DOCUMENTATION,
    )
    async def search_content(
        self,
        query: Optional[str] = None,
        space_key: Optional[str] = None,
        content_types: Optional[list[str]] = None,
        labels: Optional[list[str]] = None,
        order_by: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> tuple[bool, str]:
        try:
            if not query and not labels:
                return False, json.dumps({
                    "error": "A query or labels filter is required",
                    "guidance": "Provide a free-text query and/or a list of labels to search.",
                })
            if order_by and not _ORDER_BY_PATTERN.match(order_by):
                return False, json.dumps({
                    "error": f"Invalid order_by clause: {order_by!r}",
                    "guidance": "Use '<field> [asc|desc]', comma-separated, e.g. 'lastmodified desc'.",
                })
            response = await self.client.search_full_text(
                query=query,
                space_id=space_key,
                content_types=content_types,
                labels=labels,
                order_by=order_by,
                limit=limit or 25,
            )
            if response.status == HttpStatusCode.SUCCESS.value:
                data = response.json()
                site_url = await self._get_site_url()
                results = self._simplify_search_results(data, site_url)
                payload: dict[str, Any] = {"message": "Content fetched successfully", "data": {"results": results, "total": len(results)}}
                self._add_search_truncation(payload, data, len(results))
                return True, json.dumps(payload)
            return self._handle_response(response, "Content fetched successfully", include_guidance=True)
        except Exception as e:
            logger.error(f"Error searching content: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name=_APP,
        tool_name="search_users",
        description="Search Confluence Data Center users by display name (name fragment)",
        args_schema=SearchUsersInput,
        returns="List of users (username, userKey, displayName, profile url)",
        primary_intent=ToolIntent.SEARCH,
        category=ToolCategory.DOCUMENTATION,
    )
    async def search_users(self, query: str, max_results: Optional[int] = None) -> tuple[bool, str]:
        try:
            if not query or not query.strip():
                return False, json.dumps({
                    "error": "Query parameter is required and cannot be empty.",
                    "guidance": "Provide a display name or name fragment.",
                })
            # DC has no /search/user endpoint (Cloud-only, 404 on Server/DC); it does
            # support server-side user search via the general CQL search endpoint with
            # a `type=user AND user.fullname ~ "<query>*"` clause. Matches on the full
            # name only — the datasource escapes the term.
            cap = min(max_results or 10, 50)
            response = await self.client.search_users_v1(query=query.strip(), limit=cap)
            if response.status != HttpStatusCode.SUCCESS.value:
                return self._handle_response(response, "Users fetched successfully", include_guidance=True)
            data = response.json()
            results = data.get("results", []) if isinstance(data, dict) else []
            base = self._response_base(data, await self._get_site_url())
            users: list[dict[str, Any]] = []
            for item in results:
                if not isinstance(item, dict):
                    continue
                user_obj = item.get("user")
                if not isinstance(user_obj, dict):
                    continue
                entry = self._clean_user(user_obj)
                if not entry:
                    continue
                rel = item.get("url")
                if base and rel:
                    entry["url"] = f"{base.rstrip('/')}{rel}"
                users.append(entry)
            return True, json.dumps({
                "message": "Users fetched successfully",
                "data": {"results": users, "total": len(users)},
            })
        except Exception as e:
            logger.error(f"Error searching users: {e}")
            return False, json.dumps({"error": str(e)})
