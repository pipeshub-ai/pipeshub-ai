# ruff: noqa
"""
Guru REST API DataSource - Auto-generated API wrapper

Generated from Guru REST API v1 documentation.
Uses HTTP client for direct REST API interactions.
All methods have explicit parameter signatures.
"""

from __future__ import annotations

from typing import Any

from app.sources.client.guru.guru import GuruClient, GuruResponse
from app.sources.client.http.http_request import HTTPRequest

# HTTP status code constant
HTTP_ERROR_THRESHOLD = 400


class GuruDataSource:
    """Guru REST API DataSource

    Provides async wrapper methods for Guru REST API operations:
    - Cards management
    - Boards management
    - Collections management
    - Groups management
    - Members management
    - Search
    - Team info
    - Analytics

    All methods return GuruResponse objects.
    """

    def __init__(self, client: GuruClient) -> None:
        """Initialize with GuruClient.

        Args:
            client: GuruClient instance with configured authentication
        """
        self._client = client
        self.http = client.get_client()
        try:
            self.base_url = self.http.get_base_url().rstrip('/')
        except AttributeError as exc:
            raise ValueError('HTTP client does not have get_base_url method') from exc

    def get_data_source(self) -> 'GuruDataSource':
        """Return the data source instance."""
        return self

    def get_client(self) -> GuruClient:
        """Return the underlying GuruClient."""
        return self._client

    # -----------------------------------------------------------------------
    # Cards
    # -----------------------------------------------------------------------

    async def list_cards(
        self,
        *,
        page: int | None = None,
        per_page: int | None = None
    ) -> GuruResponse:
        """List all cards

        HTTP GET /cards

        Args:
            page: Page number for pagination
            per_page: Number of items per page

        Returns:
            GuruResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if page is not None:
            query_params['page'] = str(page)
        if per_page is not None:
            query_params['per_page'] = str(per_page)

        url = self.base_url + "/cards"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return GuruResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_cards" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return GuruResponse(success=False, error=str(e), message="Failed to execute list_cards")

    async def get_card(
        self,
        card_id: str
    ) -> GuruResponse:
        """Get a specific card by ID

        HTTP GET /cards/{card_id}

        Args:
            card_id: The card ID

        Returns:
            GuruResponse with operation result
        """
        url = self.base_url + "/cards/{card_id}".format(card_id=card_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return GuruResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_card" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return GuruResponse(success=False, error=str(e), message="Failed to execute get_card")

    async def get_card_extended(
        self,
        card_id: str
    ) -> GuruResponse:
        """Get extended card details by ID

        HTTP GET /cards/{card_id}/extended

        Args:
            card_id: The card ID

        Returns:
            GuruResponse with operation result
        """
        url = self.base_url + "/cards/{card_id}/extended".format(card_id=card_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return GuruResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_card_extended" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return GuruResponse(success=False, error=str(e), message="Failed to execute get_card_extended")

    # -----------------------------------------------------------------------
    # Boards
    # -----------------------------------------------------------------------

    async def list_boards(
        self
    ) -> GuruResponse:
        """List all boards

        HTTP GET /boards

        Returns:
            GuruResponse with operation result
        """
        url = self.base_url + "/boards"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return GuruResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_boards" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return GuruResponse(success=False, error=str(e), message="Failed to execute list_boards")

    async def get_board(
        self,
        board_id: str
    ) -> GuruResponse:
        """Get a specific board by ID

        HTTP GET /boards/{board_id}

        Args:
            board_id: The board ID

        Returns:
            GuruResponse with operation result
        """
        url = self.base_url + "/boards/{board_id}".format(board_id=board_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return GuruResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_board" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return GuruResponse(success=False, error=str(e), message="Failed to execute get_board")

    async def get_board_items(
        self,
        board_id: str
    ) -> GuruResponse:
        """Get items on a specific board

        HTTP GET /boards/{board_id}/items

        Args:
            board_id: The board ID

        Returns:
            GuruResponse with operation result
        """
        url = self.base_url + "/boards/{board_id}/items".format(board_id=board_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return GuruResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_board_items" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return GuruResponse(success=False, error=str(e), message="Failed to execute get_board_items")

    # -----------------------------------------------------------------------
    # Collections
    # -----------------------------------------------------------------------

    async def list_collections(
        self
    ) -> GuruResponse:
        """List all collections

        HTTP GET /collections

        Returns:
            GuruResponse with operation result
        """
        url = self.base_url + "/collections"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return GuruResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_collections" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return GuruResponse(success=False, error=str(e), message="Failed to execute list_collections")

    async def get_collection(
        self,
        collection_id: str
    ) -> GuruResponse:
        """Get a specific collection by ID

        HTTP GET /collections/{collection_id}

        Args:
            collection_id: The collection ID

        Returns:
            GuruResponse with operation result
        """
        url = self.base_url + "/collections/{collection_id}".format(collection_id=collection_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return GuruResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_collection" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return GuruResponse(success=False, error=str(e), message="Failed to execute get_collection")

    # -----------------------------------------------------------------------
    # Groups
    # -----------------------------------------------------------------------

    async def list_groups(
        self
    ) -> GuruResponse:
        """List all groups

        HTTP GET /groups

        Returns:
            GuruResponse with operation result
        """
        url = self.base_url + "/groups"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return GuruResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_groups" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return GuruResponse(success=False, error=str(e), message="Failed to execute list_groups")

    async def get_group(
        self,
        group_id: str
    ) -> GuruResponse:
        """Get a specific group by ID

        HTTP GET /groups/{group_id}

        Args:
            group_id: The group ID

        Returns:
            GuruResponse with operation result
        """
        url = self.base_url + "/groups/{group_id}".format(group_id=group_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return GuruResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_group" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return GuruResponse(success=False, error=str(e), message="Failed to execute get_group")

    # -----------------------------------------------------------------------
    # Members
    # -----------------------------------------------------------------------

    async def list_members(
        self
    ) -> GuruResponse:
        """List all members

        HTTP GET /members

        Returns:
            GuruResponse with operation result
        """
        url = self.base_url + "/members"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return GuruResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_members" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return GuruResponse(success=False, error=str(e), message="Failed to execute list_members")

    # -----------------------------------------------------------------------
    # Search
    # -----------------------------------------------------------------------

    async def search_cards(
        self,
        search_terms: str
    ) -> GuruResponse:
        """Search cards using the card manager search

        HTTP POST /search/cardmgr

        Args:
            search_terms: Search query string

        Returns:
            GuruResponse with operation result
        """
        url = self.base_url + "/search/cardmgr"

        body: dict[str, Any] = {
            "searchTerms": search_terms,
        }

        try:
            request = HTTPRequest(
                method="POST",
                url=url,
                headers={"Content-Type": "application/json"},
                body=body,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return GuruResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed search_cards" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return GuruResponse(success=False, error=str(e), message="Failed to execute search_cards")

    # -----------------------------------------------------------------------
    # Team Info
    # -----------------------------------------------------------------------

    async def get_team_info(
        self
    ) -> GuruResponse:
        """Get team information

        HTTP GET /teams/teaminfo

        Returns:
            GuruResponse with operation result
        """
        url = self.base_url + "/teams/teaminfo"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return GuruResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_team_info" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return GuruResponse(success=False, error=str(e), message="Failed to execute get_team_info")

    # -----------------------------------------------------------------------
    # Analytics
    # -----------------------------------------------------------------------

    async def get_card_analytics(
        self
    ) -> GuruResponse:
        """Get card analytics

        HTTP GET /analytics/card

        Returns:
            GuruResponse with operation result
        """
        url = self.base_url + "/analytics/card"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return GuruResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_card_analytics" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return GuruResponse(success=False, error=str(e), message="Failed to execute get_card_analytics")
