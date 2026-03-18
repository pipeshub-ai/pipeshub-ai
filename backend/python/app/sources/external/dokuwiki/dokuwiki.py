"""
DokuWiki XML-RPC DataSource - Auto-generated API wrapper

Generated from DokuWiki XML-RPC API documentation.
Uses xmlrpc.client.ServerProxy for XML-RPC interactions.
All methods have explicit parameter signatures.
"""

from __future__ import annotations

from typing import Any

from app.sources.client.dokuwiki.dokuwiki import DokuWikiClient, DokuWikiResponse

# Type alias for XML-RPC results which return _Marshallable
_XmlRpcResult = Any


class DokuWikiDataSource:
    """DokuWiki XML-RPC DataSource

    Provides wrapper methods for DokuWiki XML-RPC API operations:
    - System info (version, time)
    - Page CRUD and listing
    - Page info and versions
    - Search
    - Attachments
    - Backlinks
    - Recent changes
    - ACL checks

    Uses xmlrpc.client.ServerProxy under the hood.
    All methods return DokuWikiResponse objects.

    Note: XML-RPC calls are synchronous (blocking). The methods here
    are not async but follow the same response pattern for consistency.
    """

    def __init__(self, client: DokuWikiClient) -> None:
        """Initialize with DokuWikiClient.

        Args:
            client: DokuWikiClient instance with configured authentication
        """
        self._client = client
        self.server = client.get_sdk()

    def get_data_source(self) -> "DokuWikiDataSource":
        """Return the data source instance."""
        return self

    def get_client(self) -> DokuWikiClient:
        """Return the underlying DokuWikiClient."""
        return self._client

    # -----------------------------------------------------------------------
    # System
    # -----------------------------------------------------------------------

    def get_version(self) -> DokuWikiResponse:
        """Get the DokuWiki version.

        Returns:
            DokuWikiResponse with version string
        """
        try:
            result: _XmlRpcResult = self.server.dokuwiki.getVersion()
            return DokuWikiResponse(
                success=True,
                data=result,
                message="Successfully executed get_version",
            )
        except Exception as e:
            return DokuWikiResponse(
                success=False,
                error=str(e),
                message="Failed to execute get_version",
            )

    def get_time(self) -> DokuWikiResponse:
        """Get the server time.

        Returns:
            DokuWikiResponse with server time (Unix timestamp)
        """
        try:
            result: _XmlRpcResult = self.server.dokuwiki.getTime()
            return DokuWikiResponse(
                success=True,
                data=result,
                message="Successfully executed get_time",
            )
        except Exception as e:
            return DokuWikiResponse(
                success=False,
                error=str(e),
                message="Failed to execute get_time",
            )

    # -----------------------------------------------------------------------
    # Pages
    # -----------------------------------------------------------------------

    def get_page(self, pagename: str) -> DokuWikiResponse:
        """Get the content of a wiki page.

        Args:
            pagename: Full page name (e.g. "namespace:pagename")

        Returns:
            DokuWikiResponse with page content (string)
        """
        try:
            result: _XmlRpcResult = self.server.wiki.getPage(pagename)
            return DokuWikiResponse(
                success=True,
                data=result,
                message="Successfully executed get_page",
            )
        except Exception as e:
            return DokuWikiResponse(
                success=False,
                error=str(e),
                message="Failed to execute get_page",
            )

    def put_page(
        self,
        pagename: str,
        content: str,
        *,
        attrs: dict[str, Any] | None = None,
    ) -> DokuWikiResponse:
        """Create or update a wiki page.

        Args:
            pagename: Full page name (e.g. "namespace:pagename")
            content: Page content in wiki markup
            attrs: Optional attributes dict (e.g. {"sum": "edit summary", "minor": True})

        Returns:
            DokuWikiResponse with operation result
        """
        try:
            result: _XmlRpcResult = self.server.wiki.putPage(
                pagename, content, attrs or {}
            )
            return DokuWikiResponse(
                success=True,
                data=result,
                message="Successfully executed put_page",
            )
        except Exception as e:
            return DokuWikiResponse(
                success=False,
                error=str(e),
                message="Failed to execute put_page",
            )

    def list_pages(self, namespace: str) -> DokuWikiResponse:
        """List pages in a namespace.

        Args:
            namespace: Namespace to list pages from (e.g. "wiki")

        Returns:
            DokuWikiResponse with list of page info dicts
        """
        try:
            result: _XmlRpcResult = self.server.wiki.listPages(namespace)
            return DokuWikiResponse(
                success=True,
                data=result,
                message="Successfully executed list_pages",
            )
        except Exception as e:
            return DokuWikiResponse(
                success=False,
                error=str(e),
                message="Failed to execute list_pages",
            )

    def get_all_pages(self) -> DokuWikiResponse:
        """Get a list of all pages.

        Returns:
            DokuWikiResponse with list of all page info dicts
        """
        try:
            result: _XmlRpcResult = self.server.wiki.getAllPages()
            return DokuWikiResponse(
                success=True,
                data=result,
                message="Successfully executed get_all_pages",
            )
        except Exception as e:
            return DokuWikiResponse(
                success=False,
                error=str(e),
                message="Failed to execute get_all_pages",
            )

    def get_page_info(self, pagename: str) -> DokuWikiResponse:
        """Get metadata about a page.

        Args:
            pagename: Full page name

        Returns:
            DokuWikiResponse with page info dict (name, lastModified, author, version)
        """
        try:
            result: _XmlRpcResult = self.server.wiki.getPageInfo(pagename)
            return DokuWikiResponse(
                success=True,
                data=result,
                message="Successfully executed get_page_info",
            )
        except Exception as e:
            return DokuWikiResponse(
                success=False,
                error=str(e),
                message="Failed to execute get_page_info",
            )

    def get_page_versions(
        self,
        pagename: str,
        offset: int = 0,
    ) -> DokuWikiResponse:
        """Get version history of a page.

        Args:
            pagename: Full page name
            offset: Offset for pagination (default: 0)

        Returns:
            DokuWikiResponse with list of version info dicts
        """
        try:
            result: _XmlRpcResult = self.server.wiki.getPageVersions(pagename, offset)
            return DokuWikiResponse(
                success=True,
                data=result,
                message="Successfully executed get_page_versions",
            )
        except Exception as e:
            return DokuWikiResponse(
                success=False,
                error=str(e),
                message="Failed to execute get_page_versions",
            )

    # -----------------------------------------------------------------------
    # Search
    # -----------------------------------------------------------------------

    def search(self, query: str) -> DokuWikiResponse:
        """Search wiki pages.

        Args:
            query: Search query string

        Returns:
            DokuWikiResponse with list of search result dicts
        """
        try:
            result: _XmlRpcResult = self.server.wiki.search(query)
            return DokuWikiResponse(
                success=True,
                data=result,
                message="Successfully executed search",
            )
        except Exception as e:
            return DokuWikiResponse(
                success=False,
                error=str(e),
                message="Failed to execute search",
            )

    # -----------------------------------------------------------------------
    # Attachments
    # -----------------------------------------------------------------------

    def get_attachments(self, namespace: str) -> DokuWikiResponse:
        """Get attachments in a namespace.

        Args:
            namespace: Namespace to list attachments from

        Returns:
            DokuWikiResponse with list of attachment info dicts
        """
        try:
            result: _XmlRpcResult = self.server.wiki.getAttachments(namespace)
            return DokuWikiResponse(
                success=True,
                data=result,
                message="Successfully executed get_attachments",
            )
        except Exception as e:
            return DokuWikiResponse(
                success=False,
                error=str(e),
                message="Failed to execute get_attachments",
            )

    # -----------------------------------------------------------------------
    # Backlinks
    # -----------------------------------------------------------------------

    def get_backlinks(self, pagename: str) -> DokuWikiResponse:
        """Get pages that link to the specified page.

        Args:
            pagename: Full page name to find backlinks for

        Returns:
            DokuWikiResponse with list of page names
        """
        try:
            result: _XmlRpcResult = self.server.wiki.getBackLinks(pagename)
            return DokuWikiResponse(
                success=True,
                data=result,
                message="Successfully executed get_backlinks",
            )
        except Exception as e:
            return DokuWikiResponse(
                success=False,
                error=str(e),
                message="Failed to execute get_backlinks",
            )

    # -----------------------------------------------------------------------
    # Recent Changes
    # -----------------------------------------------------------------------

    def get_recent_changes(self, timestamp: int) -> DokuWikiResponse:
        """Get recent changes since a given timestamp.

        Args:
            timestamp: Unix timestamp to get changes since

        Returns:
            DokuWikiResponse with list of change info dicts
        """
        try:
            result: _XmlRpcResult = self.server.wiki.getRecentChanges(timestamp)
            return DokuWikiResponse(
                success=True,
                data=result,
                message="Successfully executed get_recent_changes",
            )
        except Exception as e:
            return DokuWikiResponse(
                success=False,
                error=str(e),
                message="Failed to execute get_recent_changes",
            )

    # -----------------------------------------------------------------------
    # ACL
    # -----------------------------------------------------------------------

    def acl_check(self, pagename: str) -> DokuWikiResponse:
        """Check ACL permissions for a page.

        Args:
            pagename: Full page name to check

        Returns:
            DokuWikiResponse with permission level (int)
        """
        try:
            result: _XmlRpcResult = self.server.wiki.aclCheck(pagename)
            return DokuWikiResponse(
                success=True,
                data=result,
                message="Successfully executed acl_check",
            )
        except Exception as e:
            return DokuWikiResponse(
                success=False,
                error=str(e),
                message="Failed to execute acl_check",
            )
