"""
Trello API DataSource

Comprehensive Trello API client using py-trello library.
Covers core Trello API endpoints for boards, lists, cards, members, and organizations.

Total API Categories: 5
Total Methods: 26
"""

import asyncio
from typing import List, Optional

# Import from our client module
from app.sources.client.trello.trello import TrelloClient, TrelloResponse
from trello import TrelloClient as PyTrelloClient


class TrelloDataSource:
    """Comprehensive Trello API DataSource wrapper.

    Uses the py-trello Python library through our TrelloClient wrapper.
    Covers 5 API categories with 26 methods.

    All methods are async and return TrelloResponse objects.

    Example:
        >>> from app.sources.client.trello.trello import TrelloClient, TrelloApiKeyConfig
        >>> from app.sources.external.trello.trello import TrelloDataSource
        >>>
        >>> # Create client with API key and token
        >>> client = TrelloClient.build_with_config(
        ...     TrelloApiKeyConfig(
        ...         api_key="your_api_key",
        ...         api_token="your_token"
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

    def _get_trello_client(self) -> PyTrelloClient:
        """Get the underlying py-trello client.

        Returns:
            PyTrelloClient instance from the wrapped client
        """
        return self.client.get_trello_client()

    def get_client(self) -> TrelloClient:
        """Get the wrapped TrelloClient instance.

        Returns:
            TrelloClient instance
        """
        return self.client

    async def _execute(self, func, *args, **kwargs) -> TrelloResponse:
        """Execute a synchronous py-trello function asynchronously.

        Args:
            func: Function to execute
            *args: Positional arguments
            **kwargs: Keyword arguments

        Returns:
            TrelloResponse with success status and data or error
        """
        try:
            loop = asyncio.get_running_loop()

            def _serialize(data):
                """Recursively serialize py-trello objects to dictionaries."""
                if isinstance(data, list):
                    return [_serialize(item) for item in data]
                if hasattr(data, "__dict__"):
                    return {
                        k: v for k, v in data.__dict__.items() if not k.startswith("_")
                    }
                return data

            result = await loop.run_in_executor(None, lambda: func(*args, **kwargs))
            return TrelloResponse(success=True, data=_serialize(result))
        except Exception as e:
            return TrelloResponse(success=False, error=str(e))

    # ========================================================================
    # Members API - 5 methods
    # ========================================================================

    async def get_authenticated_member(self) -> TrelloResponse:
        """
        Get the authenticated member (current user)

        Returns:
            TrelloResponse: Standardized response with member data
        """
        client = self._get_trello_client()
        return await self._execute(client.get_member, "me")

    async def get_member(self, member_id: str) -> TrelloResponse:
        """
        Get a member by ID

        Args:
            member_id: Member ID or username

        Returns:
            TrelloResponse: Standardized response with member data
        """
        client = self._get_trello_client()
        return await self._execute(client.get_member, member_id)

    async def list_member_boards(
        self, member_id: str = "me", board_filter: str = "all"
    ) -> TrelloResponse:
        """
        List boards for a member

        Args:
            member_id: Member ID or 'me' for authenticated user
            board_filter: Filter for boards ('all', 'open', 'closed', 'starred', etc.)

        Returns:
            TrelloResponse: Standardized response with list of boards
        """
        client = self._get_trello_client()

        def get_boards():
            member = client.get_member(member_id)
            board_methods = {
                "all": member.all_boards,
                "open": member.open_boards,
                "closed": member.closed_boards,
            }
            method = board_methods.get(board_filter, member.all_boards)
            return method()

        return await self._execute(get_boards)

    async def list_member_cards(self, member_id: str = "me") -> TrelloResponse:
        """
        List cards for a member

        Args:
            member_id: Member ID or 'me' for authenticated user

        Returns:
            TrelloResponse: Standardized response with list of cards
        """
        client = self._get_trello_client()

        def get_cards():
            member = client.get_member(member_id)
            return member.fetch_cards()

        return await self._execute(get_cards)

    async def list_member_organizations(self, member_id: str = "me") -> TrelloResponse:
        """
        List organizations for a member

        Args:
            member_id: Member ID or 'me' for authenticated user

        Returns:
            TrelloResponse: Standardized response with list of organizations
        """
        client = self._get_trello_client()

        def get_orgs():
            member = client.get_member(member_id)
            return member.fetch_organizations()

        return await self._execute(get_orgs)

    # ========================================================================
    # Boards API - 6 methods
    # ========================================================================

    async def list_boards(self, board_filter: str = "open") -> TrelloResponse:
        """
        List all boards for the authenticated user

        Args:
            board_filter: Filter for boards ('all', 'open', 'closed', etc.)

        Returns:
            TrelloResponse: Standardized response with list of boards
        """
        client = self._get_trello_client()
        return await self._execute(client.list_boards, board_filter=board_filter)

    async def get_board(self, board_id: str) -> TrelloResponse:
        """
        Get a board by ID

        Args:
            board_id: Board ID

        Returns:
            TrelloResponse: Standardized response with board data
        """
        client = self._get_trello_client()
        return await self._execute(client.get_board, board_id)

    async def get_board_lists(
        self, board_id: str, list_filter: str = "open"
    ) -> TrelloResponse:
        """
        Get lists on a board

        Args:
            board_id: Board ID
            list_filter: Filter for lists ('all', 'open', 'closed', etc.)

        Returns:
            TrelloResponse: Standardized response with list of lists
        """
        client = self._get_trello_client()

        def get_lists():
            board = client.get_board(board_id)
            list_methods = {
                "all": board.all_lists,
                "open": board.open_lists,
                "closed": board.closed_lists,
            }
            # Defaults to open lists if filter is not recognized
            method = list_methods.get(list_filter, board.open_lists)
            return method()

        return await self._execute(get_lists)

    async def get_board_cards(self, board_id: str) -> TrelloResponse:
        """
        Get all cards on a board

        Args:
            board_id: Board ID

        Returns:
            TrelloResponse: Standardized response with list of cards
        """
        client = self._get_trello_client()

        def get_cards():
            board = client.get_board(board_id)
            return board.all_cards()

        return await self._execute(get_cards)

    async def get_board_members(self, board_id: str) -> TrelloResponse:
        """
        Get members of a board

        Args:
            board_id: Board ID

        Returns:
            TrelloResponse: Standardized response with list of members
        """
        client = self._get_trello_client()

        def get_members():
            board = client.get_board(board_id)
            return board.get_members()

        return await self._execute(get_members)

    async def create_board(
        self,
        name: str,
        source_board: Optional[str] = None,
        default_lists: bool = True,
        organization_id: Optional[str] = None,
    ) -> TrelloResponse:
        """
        Create a new board

        Args:
            name: Board name
            source_board: Board ID to copy from (optional)
            default_lists: Whether to create default lists
            organization_id: Organization ID (optional)

        Returns:
            TrelloResponse: Standardized response with created board data
        """
        client = self._get_trello_client()
        return await self._execute(
            client.add_board,
            board_name=name,
            source_board=source_board,
            default_lists=default_lists,
            organization_id=organization_id,
        )

    # ========================================================================
    # Lists API - 4 methods
    # ========================================================================

    async def get_list(self, list_id: str) -> TrelloResponse:
        """
        Get a list by ID

        Args:
            list_id: List ID

        Returns:
            TrelloResponse: Standardized response with list data
        """
        client = self._get_trello_client()
        return await self._execute(client.get_list, list_id)

    async def get_list_cards(self, list_id: str) -> TrelloResponse:
        """
        Get cards in a list

        Args:
            list_id: List ID

        Returns:
            TrelloResponse: Standardized response with list of cards
        """
        client = self._get_trello_client()

        def get_cards():
            trello_list = client.get_list(list_id)
            return trello_list.list_cards()

        return await self._execute(get_cards)

    async def create_list(
        self, board_id: str, name: str, pos: Optional[str] = None
    ) -> TrelloResponse:
        """
        Create a new list on a board

        Args:
            board_id: Board ID
            name: List name
            pos: Position ('top', 'bottom', or a positive number)

        Returns:
            TrelloResponse: Standardized response with created list data
        """
        client = self._get_trello_client()

        def create():
            board = client.get_board(board_id)
            return board.add_list(name, pos=pos)

        return await self._execute(create)

    async def archive_list(self, list_id: str) -> TrelloResponse:
        """
        Archive (close) a list

        Args:
            list_id: List ID

        Returns:
            TrelloResponse: Standardized response with result
        """
        client = self._get_trello_client()

        def archive():
            trello_list = client.get_list(list_id)
            trello_list.close()
            return {"archived": True, "list_id": list_id}

        return await self._execute(archive)

    # ========================================================================
    # Cards API - 8 methods
    # ========================================================================

    async def get_card(self, card_id: str) -> TrelloResponse:
        """
        Get a card by ID

        Args:
            card_id: Card ID

        Returns:
            TrelloResponse: Standardized response with card data
        """
        client = self._get_trello_client()
        return await self._execute(client.get_card, card_id)

    async def create_card(
        self,
        list_id: str,
        name: str,
        desc: Optional[str] = None,
        labels: Optional[List[str]] = None,
        due: Optional[str] = None,
        assign: Optional[List[str]] = None,
    ) -> TrelloResponse:
        """
        Create a new card

        Args:
            list_id: List ID where card will be created
            name: Card name
            desc: Card description (optional)
            labels: List of label IDs (optional)
            due: Due date (optional)
            assign: List of member IDs to assign (optional)

        Returns:
            TrelloResponse: Standardized response with created card data
        """
        client = self._get_trello_client()

        def create():
            trello_list = client.get_list(list_id)
            return trello_list.add_card(
                name=name, desc=desc, labels=labels, due=due, assign=assign
            )

        return await self._execute(create)

    async def update_card(
        self,
        card_id: str,
        name: Optional[str] = None,
        desc: Optional[str] = None,
        closed: Optional[bool] = None,
        due: Optional[str] = None,
    ) -> TrelloResponse:
        """
        Update a card

        Args:
            card_id: Card ID
            name: New card name (optional)
            desc: New card description (optional)
            closed: Whether card is archived (optional)
            due: New due date (optional)

        Returns:
            TrelloResponse: Standardized response with updated card data
        """
        client = self._get_trello_client()

        def update():
            card = client.get_card(card_id)
            if name is not None:
                card.set_name(name)
            if desc is not None:
                card.set_description(desc)
            if closed is not None:
                card.set_closed(closed)
            if due is not None:
                card.set_due(due)
            return card

        return await self._execute(update)

    async def delete_card(self, card_id: str) -> TrelloResponse:
        """
        Delete a card permanently

        Args:
            card_id: Card ID

        Returns:
            TrelloResponse: Standardized response with result
        """
        client = self._get_trello_client()

        def delete():
            card = client.get_card(card_id)
            card.delete()
            return {"deleted": True, "card_id": card_id}

        return await self._execute(delete)

    async def get_card_checklists(self, card_id: str) -> TrelloResponse:
        """
        Get checklists on a card

        Args:
            card_id: Card ID

        Returns:
            TrelloResponse: Standardized response with list of checklists
        """
        client = self._get_trello_client()

        def get_checklists():
            card = client.get_card(card_id)
            return card.fetch_checklists()

        return await self._execute(get_checklists)

    async def get_card_attachments(self, card_id: str) -> TrelloResponse:
        """
        Get attachments on a card

        Args:
            card_id: Card ID

        Returns:
            TrelloResponse: Standardized response with list of attachments
        """
        client = self._get_trello_client()

        def get_attachments():
            card = client.get_card(card_id)
            return card.get_attachments()

        return await self._execute(get_attachments)

    async def get_card_comments(self, card_id: str) -> TrelloResponse:
        """
        Get comments (actions) on a card

        Args:
            card_id: Card ID

        Returns:
            TrelloResponse: Standardized response with list of comments
        """
        client = self._get_trello_client()

        def get_comments():
            card = client.get_card(card_id)
            return card.get_comments()

        return await self._execute(get_comments)

    async def add_card_comment(self, card_id: str, text: str) -> TrelloResponse:
        """
        Add a comment to a card

        Args:
            card_id: Card ID
            text: Comment text

        Returns:
            TrelloResponse: Standardized response with comment data
        """
        client = self._get_trello_client()

        def add_comment():
            card = client.get_card(card_id)
            return card.comment(text)

        return await self._execute(add_comment)

    # ========================================================================
    # Organizations API - 3 methods
    # ========================================================================

    async def get_organization(self, org_id: str) -> TrelloResponse:
        """
        Get an organization by ID

        Args:
            org_id: Organization ID or name

        Returns:
            TrelloResponse: Standardized response with organization data
        """
        client = self._get_trello_client()
        return await self._execute(client.get_organization, org_id)

    async def list_organization_boards(self, org_id: str) -> TrelloResponse:
        """
        List boards in an organization

        Args:
            org_id: Organization ID or name

        Returns:
            TrelloResponse: Standardized response with list of boards
        """
        client = self._get_trello_client()

        def get_boards():
            org = client.get_organization(org_id)
            return org.get_boards()

        return await self._execute(get_boards)

    async def list_organization_members(self, org_id: str) -> TrelloResponse:
        """
        List members in an organization

        Args:
            org_id: Organization ID or name

        Returns:
            TrelloResponse: Standardized response with list of members
        """
        client = self._get_trello_client()

        def get_members():
            org = client.get_organization(org_id)
            return org.get_members()

        return await self._execute(get_members)
