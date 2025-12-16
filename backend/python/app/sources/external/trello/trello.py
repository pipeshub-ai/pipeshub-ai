"""
Trello API DataSource

Comprehensive Trello API client using HTTPClient infrastructure.
Covers core Trello API endpoints for boards, lists, cards, members, and organizations.

Total API Categories: 5
Total Methods: 26
"""

from typing import Any, Dict, List, Optional

from app.config.constants.http_status_code import HttpStatusCode
from app.sources.client.http.http_request import HTTPRequest
from app.sources.client.http.http_response import HTTPResponse
from app.sources.client.trello.trello import TrelloClient, TrelloResponse


class TrelloDataSource:
    """Comprehensive Trello API DataSource wrapper.

    Uses HTTPClient.execute() with HTTPRequest objects to call the Trello REST API.
    Covers 5 API categories with 26 methods.

    All methods are async and return TrelloResponse objects.

    Example:
        >>> from app.sources.client.trello.trello import TrelloClient, TrelloTokenConfig
        >>> from app.sources.external.trello.trello import TrelloDataSource
        >>>
        >>> # Create client with API Key + Token
        >>> client = TrelloClient.build_with_config(
        ...     TrelloTokenConfig(
        ...         api_key="your_api_key",
        ...         token="your_token",
        ...     )
        ... )
        >>>
        >>> # Create datasource
        >>> datasource = TrelloDataSource(client)
        >>>
        >>> # Use the datasource
        >>> response = await datasource.get_authenticated_member()
        >>> if response.success:
        ...     print(response.data)
    """

    def __init__(self, client: TrelloClient) -> None:
        """Initialize TrelloDataSource with a TrelloClient instance.

        Args:
            client: TrelloClient instance (created via build_with_config or build_from_services)
        """
        self.client = client
        self.http_client = client.get_client()
        if self.http_client is None:
            raise ValueError("HTTP client is not initialized")
        try:
            self.base_url = self.http_client.get_base_url()
        except AttributeError as exc:
            raise ValueError("HTTP client does not have get_base_url method") from exc

    def get_client(self) -> TrelloClient:
        """Get the wrapped TrelloClient instance.

        Returns:
            TrelloClient instance
        """
        return self.client

    def _as_str_dict(self, data: Dict[str, Any]) -> Dict[str, str]:
        """Helper to ensure all dict values are strings for HTTPRequest."""
        return {k: str(v) for k, v in data.items() if v is not None}

    async def _execute_request(
        self,
        method: str,
        endpoint: str,
        query_params: Optional[Dict[str, Any]] = None,
        body: Optional[Dict[str, Any]] = None,
    ) -> TrelloResponse:
        """Internal helper to execute HTTP requests and convert to TrelloResponse.

        Args:
            method: HTTP method (GET, POST, PUT, DELETE)
            endpoint: API endpoint (e.g., "/members/me")
            query_params: Optional query parameters
            body: Optional request body

        Returns:
            TrelloResponse with success status and data or error
        """
        # Merge Trello auth params (key and token) with any extra query params
        auth_params = self.http_client._build_auth_params()
        merged_params: Dict[str, Any] = {**auth_params, **(query_params or {})}

        url = f"{self.base_url}{endpoint}"
        request = HTTPRequest(
            method=method,
            url=url,
            headers=self.http_client.headers,
            query_params=self._as_str_dict(merged_params),
            body=body,
        )

        try:
            http_response: HTTPResponse = await self.http_client.execute(request)
            status_code = http_response.status

            if status_code >= HttpStatusCode.BAD_REQUEST.value:
                return TrelloResponse(
                    success=False,
                    error=f"HTTP {status_code}: {http_response.text()}",
                )

            data = http_response.json() if http_response.is_json else http_response.text()
            return TrelloResponse(success=True, data=data)
        except Exception as e:
            return TrelloResponse(
                success=False,
                error=str(e),
            )

    # ========================================================================
    # Members API - 5 methods
    # ========================================================================

    async def get_authenticated_member(self) -> TrelloResponse:
        """Get the authenticated member (current user).

        Returns:
            TrelloResponse: Standardized response with member data
        """
        return await self._execute_request("GET", "/members/me")

    async def get_member(self, member_id: str) -> TrelloResponse:
        """Get a member by ID.

        Args:
            member_id: Member ID or username

        Returns:
            TrelloResponse: Standardized response with member data
        """
        return await self._execute_request("GET", f"/members/{member_id}")

    async def list_member_boards(
        self,
        member_id: str = "me",
        board_filter: str = "all"
    ) -> TrelloResponse:
        """List boards for a member.

        Args:
            member_id: Member ID or 'me' for authenticated user
            board_filter: Filter for boards ('all', 'open', 'closed')

        Returns:
            TrelloResponse: Standardized response with list of boards
        """
        return await self._execute_request(
            "GET",
            f"/members/{member_id}/boards",
            query_params={"filter": board_filter}
        )

    async def list_member_cards(self, member_id: str = "me") -> TrelloResponse:
        """List cards for a member.

        Args:
            member_id: Member ID or 'me' for authenticated user

        Returns:
            TrelloResponse: Standardized response with list of cards
        """
        return await self._execute_request("GET", f"/members/{member_id}/cards")

    async def list_member_organizations(self, member_id: str = "me") -> TrelloResponse:
        """List organizations for a member.

        Args:
            member_id: Member ID or 'me' for authenticated user

        Returns:
            TrelloResponse: Standardized response with list of organizations
        """
        return await self._execute_request("GET", f"/members/{member_id}/organizations")

    # ========================================================================
    # Boards API - 6 methods
    # ========================================================================

    async def list_boards(self, board_filter: str = "open") -> TrelloResponse:
        """List all boards for the authenticated user.

        Args:
            board_filter: Filter for boards ('all', 'open', 'closed', etc.)

        Returns:
            TrelloResponse: Standardized response with list of boards
        """
        return await self._execute_request(
            "GET",
            "/members/me/boards",
            query_params={"filter": board_filter}
        )

    async def get_board(self, board_id: str) -> TrelloResponse:
        """Get a board by ID.

        Args:
            board_id: Board ID

        Returns:
            TrelloResponse: Standardized response with board data
        """
        return await self._execute_request("GET", f"/boards/{board_id}")

    async def get_board_lists(
        self, board_id: str, list_filter: str = "open"
    ) -> TrelloResponse:
        """Get lists on a board.

        Args:
            board_id: Board ID
            list_filter: Filter for lists ('all', 'open', 'closed', etc.)

        Returns:
            TrelloResponse: Standardized response with list of lists
        """
        return await self._execute_request(
            "GET",
            f"/boards/{board_id}/lists",
            query_params={"filter": list_filter}
        )

    async def get_board_cards(self, board_id: str) -> TrelloResponse:
        """Get all cards on a board.

        Args:
            board_id: Board ID

        Returns:
            TrelloResponse: Standardized response with list of cards
        """
        return await self._execute_request("GET", f"/boards/{board_id}/cards")

    async def get_board_members(self, board_id: str) -> TrelloResponse:
        """Get members of a board.

        Args:
            board_id: Board ID

        Returns:
            TrelloResponse: Standardized response with list of members
        """
        return await self._execute_request("GET", f"/boards/{board_id}/members")

    async def create_board(
        self,
        name: str,
        default_lists: bool = True,
        organization_id: Optional[str] = None
    ) -> TrelloResponse:
        """Create a new board.

        Args:
            name: Board name
            default_lists: Whether to create default lists
            organization_id: Organization ID (optional)

        Returns:
            TrelloResponse: Standardized response with created board data
        """
        params: Dict[str, Any] = {
            "name": name,
            "defaultLists": str(default_lists).lower(),
        }
        if organization_id:
            params["idOrganization"] = organization_id

        return await self._execute_request("POST", "/boards", query_params=params)

    # ========================================================================
    # Lists API - 4 methods
    # ========================================================================

    async def get_list(self, list_id: str) -> TrelloResponse:
        """Get a list by ID.

        Args:
            list_id: List ID

        Returns:
            TrelloResponse: Standardized response with list data
        """
        return await self._execute_request("GET", f"/lists/{list_id}")

    async def get_list_cards(self, list_id: str) -> TrelloResponse:
        """Get cards in a list.

        Args:
            list_id: List ID

        Returns:
            TrelloResponse: Standardized response with list of cards
        """
        return await self._execute_request("GET", f"/lists/{list_id}/cards")

    async def create_list(
        self, board_id: str, name: str, pos: Optional[str] = None
    ) -> TrelloResponse:
        """Create a new list on a board.

        Args:
            board_id: Board ID
            name: List name
            pos: Position ('top', 'bottom', or a positive number)

        Returns:
            TrelloResponse: Standardized response with created list data
        """
        params: Dict[str, Any] = {
            "name": name,
            "idBoard": board_id,
        }
        if pos:
            params["pos"] = pos

        return await self._execute_request("POST", "/lists", query_params=params)

    async def archive_list(self, list_id: str) -> TrelloResponse:
        """Archive (close) a list.

        Args:
            list_id: List ID

        Returns:
            TrelloResponse: Standardized response with result
        """
        return await self._execute_request(
            "PUT",
            f"/lists/{list_id}/closed",
            query_params={"value": "true"}
        )

    # ========================================================================
    # Cards API - 8 methods
    # ========================================================================

    async def get_card(self, card_id: str) -> TrelloResponse:
        """Get a card by ID.

        Args:
            card_id: Card ID

        Returns:
            TrelloResponse: Standardized response with card data
        """
        return await self._execute_request("GET", f"/cards/{card_id}")

    async def create_card(
        self,
        list_id: str,
        name: str,
        desc: Optional[str] = None,
        labels: Optional[List[str]] = None,
        due: Optional[str] = None,
        members: Optional[List[str]] = None
    ) -> TrelloResponse:
        """Create a new card.

        Args:
            list_id: List ID where card will be created
            name: Card name
            desc: Card description (optional)
            labels: List of label IDs (optional)
            due: Due date (optional)
            members: List of member IDs to assign (optional)

        Returns:
            TrelloResponse: Standardized response with created card data
        """
        params: Dict[str, Any] = {
            "idList": list_id,
            "name": name,
        }
        if desc:
            params["desc"] = desc
        if labels:
            params["idLabels"] = ",".join(labels)
        if due:
            params["due"] = due
        if members:
            params["idMembers"] = ",".join(members)

        return await self._execute_request("POST", "/cards", query_params=params)

    async def update_card(
        self,
        card_id: str,
        name: Optional[str] = None,
        desc: Optional[str] = None,
        closed: Optional[bool] = None,
        due: Optional[str] = None
    ) -> TrelloResponse:
        """Update a card.

        Args:
            card_id: Card ID
            name: New card name (optional)
            desc: New card description (optional)
            closed: Whether card is archived (optional)
            due: New due date (optional)

        Returns:
            TrelloResponse: Standardized response with updated card data
        """
        params: Dict[str, Any] = {}
        if name is not None:
            params["name"] = name
        if desc is not None:
            params["desc"] = desc
        if closed is not None:
            params["closed"] = str(closed).lower()
        if due is not None:
            params["due"] = due

        return await self._execute_request(
            "PUT",
            f"/cards/{card_id}",
            query_params=params
        )

    async def delete_card(self, card_id: str) -> TrelloResponse:
        """Delete a card permanently.

        Args:
            card_id: Card ID

        Returns:
            TrelloResponse: Standardized response with result
        """
        return await self._execute_request("DELETE", f"/cards/{card_id}")

    async def get_card_checklists(self, card_id: str) -> TrelloResponse:
        """Get checklists on a card.

        Args:
            card_id: Card ID

        Returns:
            TrelloResponse: Standardized response with list of checklists
        """
        return await self._execute_request("GET", f"/cards/{card_id}/checklists")

    async def get_card_attachments(self, card_id: str) -> TrelloResponse:
        """Get attachments on a card.

        Args:
            card_id: Card ID

        Returns:
            TrelloResponse: Standardized response with list of attachments
        """
        return await self._execute_request("GET", f"/cards/{card_id}/attachments")

    async def get_card_comments(self, card_id: str) -> TrelloResponse:
        """Get comments (actions) on a card.

        Args:
            card_id: Card ID

        Returns:
            TrelloResponse: Standardized response with list of comments
        """
        return await self._execute_request(
            "GET",
            f"/cards/{card_id}/actions",
            query_params={"filter": "commentCard"}
        )

    async def add_card_comment(self, card_id: str, text: str) -> TrelloResponse:
        """Add a comment to a card.

        Args:
            card_id: Card ID
            text: Comment text

        Returns:
            TrelloResponse: Standardized response with comment data
        """
        return await self._execute_request(
            "POST",
            f"/cards/{card_id}/actions/comments",
            query_params={"text": text}
        )

    # ========================================================================
    # Organizations API - 3 methods
    # ========================================================================

    async def get_organization(self, org_id: str) -> TrelloResponse:
        """Get an organization by ID.

        Args:
            org_id: Organization ID or name

        Returns:
            TrelloResponse: Standardized response with organization data
        """
        return await self._execute_request("GET", f"/organizations/{org_id}")

    async def list_organization_boards(self, org_id: str) -> TrelloResponse:
        """List boards in an organization.

        Args:
            org_id: Organization ID or name

        Returns:
            TrelloResponse: Standardized response with list of boards
        """
        return await self._execute_request("GET", f"/organizations/{org_id}/boards")

    async def list_organization_members(self, org_id: str) -> TrelloResponse:
        """List members in an organization.

        Args:
            org_id: Organization ID or name

        Returns:
            TrelloResponse: Standardized response with list of members
        """
        return await self._execute_request("GET", f"/organizations/{org_id}/members")
