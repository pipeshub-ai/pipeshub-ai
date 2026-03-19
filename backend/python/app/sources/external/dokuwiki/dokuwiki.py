# ruff: noqa
"""
DokuWiki JSON API DataSource - Auto-generated API wrapper

Generated from DokuWiki JSON API documentation.
Uses HTTP client for direct REST API interactions.
All methods are POST (DokuWiki JSON-RPC convention).
All methods have explicit parameter signatures.
"""

from __future__ import annotations

from typing import Any

from app.sources.client.dokuwiki.dokuwiki import DokuWikiClient, DokuWikiResponse
from app.sources.client.http.http_request import HTTPRequest

# HTTP status code constant
HTTP_ERROR_THRESHOLD = 400


class DokuWikiDataSource:
    """DokuWiki JSON API DataSource

    Provides async wrapper methods for DokuWiki JSON API operations:
    - Info (API version, wiki time, title, version)
    - Pages (get, HTML, info, history, backlinks, links, recent changes, list, save, append, search, lock, unlock)
    - Media (get, info, history, usage, recent changes, list, save, delete)
    - User (ACL check, login, logoff, whoami)
    - Plugins - ACL (add, delete, list)
    - Plugins - Extensions (list, search, install, uninstall, enable, disable)
    - Plugins - User Manager (create user, delete user)
    - Plugins - Config Manager (get configs)

    The base URL is determined by the DokuWikiClient's configured base URL
    (default: https://<instance>/lib/exe/jsonapi.php).

    All methods return DokuWikiResponse objects.
    All methods are POST requests.
    """

    def __init__(self, client: DokuWikiClient) -> None:
        """Initialize with DokuWikiClient.

        Args:
            client: DokuWikiClient instance with configured authentication
        """
        self._client = client
        self.http = client.get_client()
        try:
            self.base_url = self.http.get_base_url().rstrip('/')
        except AttributeError as exc:
            raise ValueError('HTTP client does not have get_base_url method') from exc

    def get_data_source(self) -> 'DokuWikiDataSource':
        """Return the data source instance."""
        return self

    def get_client(self) -> DokuWikiClient:
        """Return the underlying DokuWikiClient."""
        return self._client

    async def get_api_version(
        self
    ) -> DokuWikiResponse:
        """Return the API version

        Returns:
            DokuWikiResponse with operation result
        """
        url = self.base_url + "/core.getAPIVersion"

        try:
            request = HTTPRequest(
                method="POST",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return DokuWikiResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_api_version" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return DokuWikiResponse(success=False, error=str(e), message="Failed to execute get_api_version")

    async def get_wiki_time(
        self
    ) -> DokuWikiResponse:
        """Return the current server time

        Returns:
            DokuWikiResponse with operation result
        """
        url = self.base_url + "/core.getWikiTime"

        try:
            request = HTTPRequest(
                method="POST",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return DokuWikiResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_wiki_time" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return DokuWikiResponse(success=False, error=str(e), message="Failed to execute get_wiki_time")

    async def get_wiki_title(
        self
    ) -> DokuWikiResponse:
        """Returns the wiki title

        Returns:
            DokuWikiResponse with operation result
        """
        url = self.base_url + "/core.getWikiTitle"

        try:
            request = HTTPRequest(
                method="POST",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return DokuWikiResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_wiki_title" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return DokuWikiResponse(success=False, error=str(e), message="Failed to execute get_wiki_title")

    async def get_wiki_version(
        self
    ) -> DokuWikiResponse:
        """Return DokuWiki's version

        Returns:
            DokuWikiResponse with operation result
        """
        url = self.base_url + "/core.getWikiVersion"

        try:
            request = HTTPRequest(
                method="POST",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return DokuWikiResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_wiki_version" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return DokuWikiResponse(success=False, error=str(e), message="Failed to execute get_wiki_version")

    async def get_page(
        self,
        page: str,
        rev: int | None = None
    ) -> DokuWikiResponse:
        """Get a wiki page's syntax

        Args:
            page: Wiki page id
            rev: Revision timestamp to access an older revision

        Returns:
            DokuWikiResponse with operation result
        """
        url = self.base_url + "/core.getPage"

        body: dict[str, Any] = {}
        body['page'] = page
        if rev is not None:
            body['rev'] = rev

        try:
            request = HTTPRequest(
                method="POST",
                url=url,
                headers={"Content-Type": "application/json"},
                body=body,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return DokuWikiResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_page" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return DokuWikiResponse(success=False, error=str(e), message="Failed to execute get_page")

    async def get_page_html(
        self,
        page: str,
        rev: int | None = None
    ) -> DokuWikiResponse:
        """Return a wiki page rendered to HTML

        Args:
            page: Page id
            rev: Revision timestamp

        Returns:
            DokuWikiResponse with operation result
        """
        url = self.base_url + "/core.getPageHTML"

        body: dict[str, Any] = {}
        body['page'] = page
        if rev is not None:
            body['rev'] = rev

        try:
            request = HTTPRequest(
                method="POST",
                url=url,
                headers={"Content-Type": "application/json"},
                body=body,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return DokuWikiResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_page_html" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return DokuWikiResponse(success=False, error=str(e), message="Failed to execute get_page_html")

    async def get_page_info(
        self,
        page: str,
        *,
        rev: int | None = None,
        author: bool | None = None,
        hash: bool | None = None
    ) -> DokuWikiResponse:
        """Return some basic data about a page

        Args:
            page: Page id
            rev: Revision timestamp
            author: Include author info
            hash: Include content hash

        Returns:
            DokuWikiResponse with operation result
        """
        url = self.base_url + "/core.getPageInfo"

        body: dict[str, Any] = {}
        body['page'] = page
        if rev is not None:
            body['rev'] = rev
        if author is not None:
            body['author'] = author
        if hash is not None:
            body['hash'] = hash

        try:
            request = HTTPRequest(
                method="POST",
                url=url,
                headers={"Content-Type": "application/json"},
                body=body,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return DokuWikiResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_page_info" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return DokuWikiResponse(success=False, error=str(e), message="Failed to execute get_page_info")

    async def get_page_history(
        self,
        page: str,
        first: int | None = None
    ) -> DokuWikiResponse:
        """Returns revisions of a wiki page

        Args:
            page: Page id
            first: Skip the first n results

        Returns:
            DokuWikiResponse with operation result
        """
        url = self.base_url + "/core.getPageHistory"

        body: dict[str, Any] = {}
        body['page'] = page
        if first is not None:
            body['first'] = first

        try:
            request = HTTPRequest(
                method="POST",
                url=url,
                headers={"Content-Type": "application/json"},
                body=body,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return DokuWikiResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_page_history" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return DokuWikiResponse(success=False, error=str(e), message="Failed to execute get_page_history")

    async def get_page_back_links(
        self,
        page: str
    ) -> DokuWikiResponse:
        """Get a page's backlinks

        Args:
            page: Page id

        Returns:
            DokuWikiResponse with operation result
        """
        url = self.base_url + "/core.getPageBackLinks"

        body: dict[str, Any] = {}
        body['page'] = page

        try:
            request = HTTPRequest(
                method="POST",
                url=url,
                headers={"Content-Type": "application/json"},
                body=body,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return DokuWikiResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_page_back_links" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return DokuWikiResponse(success=False, error=str(e), message="Failed to execute get_page_back_links")

    async def get_page_links(
        self,
        page: str
    ) -> DokuWikiResponse:
        """Get a page's links

        Args:
            page: Page id

        Returns:
            DokuWikiResponse with operation result
        """
        url = self.base_url + "/core.getPageLinks"

        body: dict[str, Any] = {}
        body['page'] = page

        try:
            request = HTTPRequest(
                method="POST",
                url=url,
                headers={"Content-Type": "application/json"},
                body=body,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return DokuWikiResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_page_links" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return DokuWikiResponse(success=False, error=str(e), message="Failed to execute get_page_links")

    async def get_recent_page_changes(
        self,
        timestamp: int | None = None
    ) -> DokuWikiResponse:
        """Get recent page changes

        Args:
            timestamp: Unix timestamp to get changes since

        Returns:
            DokuWikiResponse with operation result
        """
        url = self.base_url + "/core.getRecentPageChanges"

        body: dict[str, Any] = {}
        if timestamp is not None:
            body['timestamp'] = timestamp

        try:
            request = HTTPRequest(
                method="POST",
                url=url,
                headers={"Content-Type": "application/json"},
                body=body,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return DokuWikiResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_recent_page_changes" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return DokuWikiResponse(success=False, error=str(e), message="Failed to execute get_recent_page_changes")

    async def list_pages(
        self,
        *,
        namespace: str | None = None,
        depth: int | None = None,
        hash: bool | None = None
    ) -> DokuWikiResponse:
        """List all pages in namespace

        Args:
            namespace: Namespace to list pages from
            depth: Depth of namespace traversal
            hash: Include content hash

        Returns:
            DokuWikiResponse with operation result
        """
        url = self.base_url + "/core.listPages"

        body: dict[str, Any] = {}
        if namespace is not None:
            body['namespace'] = namespace
        if depth is not None:
            body['depth'] = depth
        if hash is not None:
            body['hash'] = hash

        try:
            request = HTTPRequest(
                method="POST",
                url=url,
                headers={"Content-Type": "application/json"},
                body=body,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return DokuWikiResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_pages" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return DokuWikiResponse(success=False, error=str(e), message="Failed to execute list_pages")

    async def save_page(
        self,
        page: str,
        text: str,
        *,
        summary: str | None = None,
        isminor: bool | None = None
    ) -> DokuWikiResponse:
        """Save a wiki page

        Args:
            page: Page id
            text: Page content
            summary: Edit summary
            isminor: Mark as minor edit

        Returns:
            DokuWikiResponse with operation result
        """
        url = self.base_url + "/core.savePage"

        body: dict[str, Any] = {}
        body['page'] = page
        body['text'] = text
        if summary is not None:
            body['summary'] = summary
        if isminor is not None:
            body['isminor'] = isminor

        try:
            request = HTTPRequest(
                method="POST",
                url=url,
                headers={"Content-Type": "application/json"},
                body=body,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return DokuWikiResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed save_page" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return DokuWikiResponse(success=False, error=str(e), message="Failed to execute save_page")

    async def append_page(
        self,
        page: str,
        text: str,
        *,
        summary: str | None = None,
        isminor: bool | None = None
    ) -> DokuWikiResponse:
        """Append text to a wiki page

        Args:
            page: Page id
            text: Text to append
            summary: Edit summary
            isminor: Mark as minor edit

        Returns:
            DokuWikiResponse with operation result
        """
        url = self.base_url + "/core.appendPage"

        body: dict[str, Any] = {}
        body['page'] = page
        body['text'] = text
        if summary is not None:
            body['summary'] = summary
        if isminor is not None:
            body['isminor'] = isminor

        try:
            request = HTTPRequest(
                method="POST",
                url=url,
                headers={"Content-Type": "application/json"},
                body=body,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return DokuWikiResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed append_page" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return DokuWikiResponse(success=False, error=str(e), message="Failed to execute append_page")

    async def search_pages(
        self,
        query: str
    ) -> DokuWikiResponse:
        """Full text search

        Args:
            query: Search query string

        Returns:
            DokuWikiResponse with operation result
        """
        url = self.base_url + "/core.searchPages"

        body: dict[str, Any] = {}
        body['query'] = query

        try:
            request = HTTPRequest(
                method="POST",
                url=url,
                headers={"Content-Type": "application/json"},
                body=body,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return DokuWikiResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed search_pages" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return DokuWikiResponse(success=False, error=str(e), message="Failed to execute search_pages")

    async def lock_pages(
        self,
        pages: list[Any]
    ) -> DokuWikiResponse:
        """Lock pages

        Args:
            pages: List of page ids to lock

        Returns:
            DokuWikiResponse with operation result
        """
        url = self.base_url + "/core.lockPages"

        body: dict[str, Any] = {}
        body['pages'] = pages

        try:
            request = HTTPRequest(
                method="POST",
                url=url,
                headers={"Content-Type": "application/json"},
                body=body,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return DokuWikiResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed lock_pages" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return DokuWikiResponse(success=False, error=str(e), message="Failed to execute lock_pages")

    async def unlock_pages(
        self,
        pages: list[Any]
    ) -> DokuWikiResponse:
        """Unlock pages

        Args:
            pages: List of page ids to unlock

        Returns:
            DokuWikiResponse with operation result
        """
        url = self.base_url + "/core.unlockPages"

        body: dict[str, Any] = {}
        body['pages'] = pages

        try:
            request = HTTPRequest(
                method="POST",
                url=url,
                headers={"Content-Type": "application/json"},
                body=body,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return DokuWikiResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed unlock_pages" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return DokuWikiResponse(success=False, error=str(e), message="Failed to execute unlock_pages")

    async def get_media(
        self,
        media: str,
        rev: int | None = None
    ) -> DokuWikiResponse:
        """Get a media file's content

        Args:
            media: Media file id
            rev: Revision timestamp

        Returns:
            DokuWikiResponse with operation result
        """
        url = self.base_url + "/core.getMedia"

        body: dict[str, Any] = {}
        body['media'] = media
        if rev is not None:
            body['rev'] = rev

        try:
            request = HTTPRequest(
                method="POST",
                url=url,
                headers={"Content-Type": "application/json"},
                body=body,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return DokuWikiResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_media" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return DokuWikiResponse(success=False, error=str(e), message="Failed to execute get_media")

    async def get_media_info(
        self,
        media: str,
        *,
        rev: int | None = None,
        author: bool | None = None,
        hash: bool | None = None
    ) -> DokuWikiResponse:
        """Return info about a media file

        Args:
            media: Media file id
            rev: Revision timestamp
            author: Include author info
            hash: Include content hash

        Returns:
            DokuWikiResponse with operation result
        """
        url = self.base_url + "/core.getMediaInfo"

        body: dict[str, Any] = {}
        body['media'] = media
        if rev is not None:
            body['rev'] = rev
        if author is not None:
            body['author'] = author
        if hash is not None:
            body['hash'] = hash

        try:
            request = HTTPRequest(
                method="POST",
                url=url,
                headers={"Content-Type": "application/json"},
                body=body,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return DokuWikiResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_media_info" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return DokuWikiResponse(success=False, error=str(e), message="Failed to execute get_media_info")

    async def get_media_history(
        self,
        media: str,
        first: int | None = None
    ) -> DokuWikiResponse:
        """Get media revisions

        Args:
            media: Media file id
            first: Skip the first n results

        Returns:
            DokuWikiResponse with operation result
        """
        url = self.base_url + "/core.getMediaHistory"

        body: dict[str, Any] = {}
        body['media'] = media
        if first is not None:
            body['first'] = first

        try:
            request = HTTPRequest(
                method="POST",
                url=url,
                headers={"Content-Type": "application/json"},
                body=body,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return DokuWikiResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_media_history" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return DokuWikiResponse(success=False, error=str(e), message="Failed to execute get_media_history")

    async def get_media_usage(
        self,
        media: str
    ) -> DokuWikiResponse:
        """Get pages using a media file

        Args:
            media: Media file id

        Returns:
            DokuWikiResponse with operation result
        """
        url = self.base_url + "/core.getMediaUsage"

        body: dict[str, Any] = {}
        body['media'] = media

        try:
            request = HTTPRequest(
                method="POST",
                url=url,
                headers={"Content-Type": "application/json"},
                body=body,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return DokuWikiResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_media_usage" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return DokuWikiResponse(success=False, error=str(e), message="Failed to execute get_media_usage")

    async def get_recent_media_changes(
        self,
        timestamp: int | None = None
    ) -> DokuWikiResponse:
        """Get recent media changes

        Args:
            timestamp: Unix timestamp to get changes since

        Returns:
            DokuWikiResponse with operation result
        """
        url = self.base_url + "/core.getRecentMediaChanges"

        body: dict[str, Any] = {}
        if timestamp is not None:
            body['timestamp'] = timestamp

        try:
            request = HTTPRequest(
                method="POST",
                url=url,
                headers={"Content-Type": "application/json"},
                body=body,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return DokuWikiResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_recent_media_changes" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return DokuWikiResponse(success=False, error=str(e), message="Failed to execute get_recent_media_changes")

    async def list_media(
        self,
        *,
        namespace: str | None = None,
        depth: int | None = None,
        hash: bool | None = None,
        pattern: str | None = None
    ) -> DokuWikiResponse:
        """List media files in namespace

        Args:
            namespace: Namespace to list media from
            depth: Depth of namespace traversal
            hash: Include content hash
            pattern: File name pattern filter

        Returns:
            DokuWikiResponse with operation result
        """
        url = self.base_url + "/core.listMedia"

        body: dict[str, Any] = {}
        if namespace is not None:
            body['namespace'] = namespace
        if depth is not None:
            body['depth'] = depth
        if hash is not None:
            body['hash'] = hash
        if pattern is not None:
            body['pattern'] = pattern

        try:
            request = HTTPRequest(
                method="POST",
                url=url,
                headers={"Content-Type": "application/json"},
                body=body,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return DokuWikiResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_media" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return DokuWikiResponse(success=False, error=str(e), message="Failed to execute list_media")

    async def save_media(
        self,
        media: str,
        base64: str,
        *,
        overwrite: bool | None = None
    ) -> DokuWikiResponse:
        """Upload a file

        Args:
            media: Media file id
            base64: Base64 encoded file content
            overwrite: Overwrite existing file

        Returns:
            DokuWikiResponse with operation result
        """
        url = self.base_url + "/core.saveMedia"

        body: dict[str, Any] = {}
        body['media'] = media
        body['base64'] = base64
        if overwrite is not None:
            body['overwrite'] = overwrite

        try:
            request = HTTPRequest(
                method="POST",
                url=url,
                headers={"Content-Type": "application/json"},
                body=body,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return DokuWikiResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed save_media" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return DokuWikiResponse(success=False, error=str(e), message="Failed to execute save_media")

    async def delete_media(
        self,
        media: str
    ) -> DokuWikiResponse:
        """Delete a file

        Args:
            media: Media file id

        Returns:
            DokuWikiResponse with operation result
        """
        url = self.base_url + "/core.deleteMedia"

        body: dict[str, Any] = {}
        body['media'] = media

        try:
            request = HTTPRequest(
                method="POST",
                url=url,
                headers={"Content-Type": "application/json"},
                body=body,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return DokuWikiResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed delete_media" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return DokuWikiResponse(success=False, error=str(e), message="Failed to execute delete_media")

    async def acl_check(
        self,
        page: str,
        user: str | None = None,
        groups: list[Any] | None = None
    ) -> DokuWikiResponse:
        """Check ACL Permissions

        Args:
            page: Page id to check permissions for
            user: Username to check permissions for
            groups: Groups to check permissions for

        Returns:
            DokuWikiResponse with operation result
        """
        url = self.base_url + "/core.aclCheck"

        body: dict[str, Any] = {}
        body['page'] = page
        if user is not None:
            body['user'] = user
        if groups is not None:
            body['groups'] = groups

        try:
            request = HTTPRequest(
                method="POST",
                url=url,
                headers={"Content-Type": "application/json"},
                body=body,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return DokuWikiResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed acl_check" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return DokuWikiResponse(success=False, error=str(e), message="Failed to execute acl_check")

    async def login(
        self,
        user: str,
        password: str
    ) -> DokuWikiResponse:
        """Login

        Args:
            user: Username
            password: Password

        Returns:
            DokuWikiResponse with operation result
        """
        url = self.base_url + "/core.login"

        body: dict[str, Any] = {}
        body['user'] = user
        body['pass'] = password

        try:
            request = HTTPRequest(
                method="POST",
                url=url,
                headers={"Content-Type": "application/json"},
                body=body,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return DokuWikiResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed login" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return DokuWikiResponse(success=False, error=str(e), message="Failed to execute login")

    async def logoff(
        self
    ) -> DokuWikiResponse:
        """Log off

        Returns:
            DokuWikiResponse with operation result
        """
        url = self.base_url + "/core.logoff"

        try:
            request = HTTPRequest(
                method="POST",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return DokuWikiResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed logoff" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return DokuWikiResponse(success=False, error=str(e), message="Failed to execute logoff")

    async def who_am_i(
        self
    ) -> DokuWikiResponse:
        """Info about current user

        Returns:
            DokuWikiResponse with operation result
        """
        url = self.base_url + "/core.whoAmI"

        try:
            request = HTTPRequest(
                method="POST",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return DokuWikiResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed who_am_i" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return DokuWikiResponse(success=False, error=str(e), message="Failed to execute who_am_i")

    async def add_acl(
        self,
        scope: str,
        user: str,
        level: int
    ) -> DokuWikiResponse:
        """Add ACL rule

        Args:
            scope: ACL scope
            user: User or group name
            level: Permission level

        Returns:
            DokuWikiResponse with operation result
        """
        url = self.base_url + "/plugin.acl.addAcl"

        body: dict[str, Any] = {}
        body['scope'] = scope
        body['user'] = user
        body['level'] = level

        try:
            request = HTTPRequest(
                method="POST",
                url=url,
                headers={"Content-Type": "application/json"},
                body=body,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return DokuWikiResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed add_acl" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return DokuWikiResponse(success=False, error=str(e), message="Failed to execute add_acl")

    async def delete_acl(
        self,
        scope: str,
        user: str
    ) -> DokuWikiResponse:
        """Remove ACL entry

        Args:
            scope: ACL scope
            user: User or group name

        Returns:
            DokuWikiResponse with operation result
        """
        url = self.base_url + "/plugin.acl.delAcl"

        body: dict[str, Any] = {}
        body['scope'] = scope
        body['user'] = user

        try:
            request = HTTPRequest(
                method="POST",
                url=url,
                headers={"Content-Type": "application/json"},
                body=body,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return DokuWikiResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed delete_acl" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return DokuWikiResponse(success=False, error=str(e), message="Failed to execute delete_acl")

    async def list_acls(
        self
    ) -> DokuWikiResponse:
        """List ACL entries

        Returns:
            DokuWikiResponse with operation result
        """
        url = self.base_url + "/plugin.acl.listAcls"

        try:
            request = HTTPRequest(
                method="POST",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return DokuWikiResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_acls" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return DokuWikiResponse(success=False, error=str(e), message="Failed to execute list_acls")

    async def list_extensions(
        self
    ) -> DokuWikiResponse:
        """List installed extensions

        Returns:
            DokuWikiResponse with operation result
        """
        url = self.base_url + "/plugin.extension.list"

        try:
            request = HTTPRequest(
                method="POST",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return DokuWikiResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_extensions" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return DokuWikiResponse(success=False, error=str(e), message="Failed to execute list_extensions")

    async def search_extensions(
        self,
        query: str,
        max: int | None = None
    ) -> DokuWikiResponse:
        """Search extensions

        Args:
            query: Search query
            max: Maximum number of results

        Returns:
            DokuWikiResponse with operation result
        """
        url = self.base_url + "/plugin.extension.search"

        body: dict[str, Any] = {}
        body['query'] = query
        if max is not None:
            body['max'] = max

        try:
            request = HTTPRequest(
                method="POST",
                url=url,
                headers={"Content-Type": "application/json"},
                body=body,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return DokuWikiResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed search_extensions" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return DokuWikiResponse(success=False, error=str(e), message="Failed to execute search_extensions")

    async def install_extension(
        self,
        extension: str
    ) -> DokuWikiResponse:
        """Install extension

        Args:
            extension: Extension identifier

        Returns:
            DokuWikiResponse with operation result
        """
        url = self.base_url + "/plugin.extension.install"

        body: dict[str, Any] = {}
        body['extension'] = extension

        try:
            request = HTTPRequest(
                method="POST",
                url=url,
                headers={"Content-Type": "application/json"},
                body=body,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return DokuWikiResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed install_extension" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return DokuWikiResponse(success=False, error=str(e), message="Failed to execute install_extension")

    async def uninstall_extension(
        self,
        extension: str
    ) -> DokuWikiResponse:
        """Uninstall extension

        Args:
            extension: Extension identifier

        Returns:
            DokuWikiResponse with operation result
        """
        url = self.base_url + "/plugin.extension.uninstall"

        body: dict[str, Any] = {}
        body['extension'] = extension

        try:
            request = HTTPRequest(
                method="POST",
                url=url,
                headers={"Content-Type": "application/json"},
                body=body,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return DokuWikiResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed uninstall_extension" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return DokuWikiResponse(success=False, error=str(e), message="Failed to execute uninstall_extension")

    async def enable_extension(
        self,
        extension: str
    ) -> DokuWikiResponse:
        """Enable extension

        Args:
            extension: Extension identifier

        Returns:
            DokuWikiResponse with operation result
        """
        url = self.base_url + "/plugin.extension.enable"

        body: dict[str, Any] = {}
        body['extension'] = extension

        try:
            request = HTTPRequest(
                method="POST",
                url=url,
                headers={"Content-Type": "application/json"},
                body=body,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return DokuWikiResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed enable_extension" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return DokuWikiResponse(success=False, error=str(e), message="Failed to execute enable_extension")

    async def disable_extension(
        self,
        extension: str
    ) -> DokuWikiResponse:
        """Disable extension

        Args:
            extension: Extension identifier

        Returns:
            DokuWikiResponse with operation result
        """
        url = self.base_url + "/plugin.extension.disable"

        body: dict[str, Any] = {}
        body['extension'] = extension

        try:
            request = HTTPRequest(
                method="POST",
                url=url,
                headers={"Content-Type": "application/json"},
                body=body,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return DokuWikiResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed disable_extension" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return DokuWikiResponse(success=False, error=str(e), message="Failed to execute disable_extension")

    async def create_user(
        self,
        user: str,
        name: str,
        mail: str,
        groups: list[Any],
        *,
        password: str | None = None,
        notify: bool | None = None
    ) -> DokuWikiResponse:
        """Create a new user

        Args:
            user: Login name
            name: Full name
            mail: Email address
            groups: List of group names
            password: Password (auto-generated if omitted)
            notify: Send notification to user

        Returns:
            DokuWikiResponse with operation result
        """
        url = self.base_url + "/plugin.usermanager.createUser"

        body: dict[str, Any] = {}
        body['user'] = user
        body['name'] = name
        body['mail'] = mail
        body['groups'] = groups
        if password is not None:
            body['password'] = password
        if notify is not None:
            body['notify'] = notify

        try:
            request = HTTPRequest(
                method="POST",
                url=url,
                headers={"Content-Type": "application/json"},
                body=body,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return DokuWikiResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed create_user" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return DokuWikiResponse(success=False, error=str(e), message="Failed to execute create_user")

    async def delete_user(
        self,
        user: list[Any]
    ) -> DokuWikiResponse:
        """Remove a user

        Args:
            user: List of login names to delete

        Returns:
            DokuWikiResponse with operation result
        """
        url = self.base_url + "/plugin.usermanager.deleteUser"

        body: dict[str, Any] = {}
        body['user'] = user

        try:
            request = HTTPRequest(
                method="POST",
                url=url,
                headers={"Content-Type": "application/json"},
                body=body,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return DokuWikiResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed delete_user" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return DokuWikiResponse(success=False, error=str(e), message="Failed to execute delete_user")

    async def get_configs(
        self
    ) -> DokuWikiResponse:
        """Get configuration

        Returns:
            DokuWikiResponse with operation result
        """
        url = self.base_url + "/plugin.confmanager.getConfigs"

        try:
            request = HTTPRequest(
                method="POST",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return DokuWikiResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_configs" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return DokuWikiResponse(success=False, error=str(e), message="Failed to execute get_configs")
