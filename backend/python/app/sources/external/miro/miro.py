# ruff: noqa
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false
from __future__ import annotations

from miro_api import MiroApi  # type: ignore[reportMissingImports]
from typing import Dict, Optional, Union, cast

from app.sources.client.miro.miro import MiroResponse


class MiroDataSource:
    """
    Strict, typed wrapper over the official miro_api SDK for common Miro
    business operations.

    Accepts either a `MiroApi` instance *or* any object with
    `.get_sdk() -> MiroApi`.

    All methods return `MiroResponse` for a uniform success/error envelope.
    """

    def __init__(self, client_or_sdk: Union[MiroApi, object]) -> None:
        # Support a raw SDK or a wrapper that exposes `.get_sdk()`
        if hasattr(client_or_sdk, "get_sdk"):
            sdk_obj = getattr(client_or_sdk, "get_sdk")()
            self._sdk: MiroApi = cast(MiroApi, sdk_obj)
        else:
            self._sdk = cast(MiroApi, client_or_sdk)

    @staticmethod
    def _params(**kwargs: object) -> Dict[str, object]:
        """Filter out None values to avoid overriding SDK defaults."""
        out: Dict[str, object] = {}
        for k, v in kwargs.items():
            if v is None:
                continue
            if isinstance(v, (list, dict)) and len(v) == 0:
                continue
            out[k] = v
        return out
    def list_boards(self, team_id: Optional[str] = None, query: Optional[str] = None, owner: Optional[str] = None, sort: Optional[str] = None, limit: Optional[str] = None, offset: Optional[str] = None) -> MiroResponse:
        """List boards accessible to the authenticated user.  [boards]"""
        params = self._params(team_id=team_id, query=query, owner=owner, sort=sort, limit=limit, offset=offset)
        result = self._sdk.get_boards(**params)
        return MiroResponse(success=True, data=result)
    def get_board(self, board_id: str) -> MiroResponse:
        """Get a single board by ID.  [boards]"""
        result = self._sdk.get_specific_board(board_id)
        return MiroResponse(success=True, data=result)
    def create_board(self, board_changes: object) -> MiroResponse:
        """Create a new board. Pass a BoardChanges model or compatible dict.  [boards]"""
        result = self._sdk.create_board(board_changes)
        return MiroResponse(success=True, data=result)
    def update_board(self, board_id: str, board_changes: object) -> MiroResponse:
        """Update an existing board.  [boards]"""
        result = self._sdk.update_board(board_id, board_changes)
        return MiroResponse(success=True, data=result)
    def delete_board(self, board_id: str) -> MiroResponse:
        """Delete a board by ID.  [boards]"""
        self._sdk.delete_board(board_id)
        return MiroResponse(success=True, data=True)
    def list_board_items(self, board_id: str, limit: Optional[str] = None, type: Optional[str] = None, cursor: Optional[str] = None) -> MiroResponse:
        """List all items on a board with optional filters.  [items]"""
        params = self._params(limit=limit, type=type, cursor=cursor)
        result = self._sdk.get_items(board_id, **params)
        return MiroResponse(success=True, data=result)
    def get_board_item(self, board_id: str, item_id: str) -> MiroResponse:
        """Get a specific item on a board.  [items]"""
        result = self._sdk.get_specific_item(board_id, item_id)
        return MiroResponse(success=True, data=result)
    def create_sticky_note(self, board_id: str, sticky_note_create_request: object) -> MiroResponse:
        """Create a sticky note on a board. Pass a StickyNoteCreateRequest model.  [sticky_notes]"""
        result = self._sdk.create_sticky_note_item(board_id, sticky_note_create_request)
        return MiroResponse(success=True, data=result)
    def create_card(self, board_id: str, card_create_request: object) -> MiroResponse:
        """Create a card on a board. Pass a CardCreateRequest model.  [cards]"""
        result = self._sdk.create_card_item(board_id, card_create_request)
        return MiroResponse(success=True, data=result)
    def create_text(self, board_id: str, text_create_request: object) -> MiroResponse:
        """Create a text item on a board. Pass a TextCreateRequest model.  [text]"""
        result = self._sdk.create_text_item(board_id, text_create_request)
        return MiroResponse(success=True, data=result)
    def create_shape(self, board_id: str, shape_create_request: object) -> MiroResponse:
        """Create a shape on a board. Pass a ShapeCreateRequest model.  [shapes]"""
        result = self._sdk.create_shape_item(board_id, shape_create_request)
        return MiroResponse(success=True, data=result)
    def list_connectors(self, board_id: str, limit: Optional[str] = None, cursor: Optional[str] = None) -> MiroResponse:
        """List all connectors on a board.  [connectors]"""
        params = self._params(limit=limit, cursor=cursor)
        result = self._sdk.get_connectors(board_id, **params)
        return MiroResponse(success=True, data=result)
    def create_connector(self, board_id: str, connector_creation_data: object) -> MiroResponse:
        """Create a connector between two items on a board.  [connectors]"""
        result = self._sdk.create_connector(board_id, connector_creation_data)
        return MiroResponse(success=True, data=result)
    def list_board_members(self, board_id: str, limit: Optional[str] = None, offset: Optional[str] = None) -> MiroResponse:
        """List all members of a board.  [members]"""
        params = self._params(limit=limit, offset=offset)
        result = self._sdk.get_board_members(board_id, **params)
        return MiroResponse(success=True, data=result)
    def share_board(self, board_id: str, board_members_invite: object) -> MiroResponse:
        """Share a board by inviting members. Pass a BoardMembersInvite model.  [members]"""
        result = self._sdk.share_board(board_id, board_members_invite)
        return MiroResponse(success=True, data=result)
    def list_board_tags(self, board_id: str, limit: Optional[str] = None, offset: Optional[str] = None) -> MiroResponse:
        """List all tags on a board.  [tags]"""
        params = self._params(limit=limit, offset=offset)
        result = self._sdk.get_tags_from_board(board_id, **params)
        return MiroResponse(success=True, data=result)
    def create_tag(self, board_id: str, tag_create_request: object) -> MiroResponse:
        """Create a tag on a board. Pass a TagCreateRequest model.  [tags]"""
        result = self._sdk.create_tag(board_id, tag_create_request)
        return MiroResponse(success=True, data=result)
    def list_frames(self, board_id: str, limit: Optional[str] = None, type: Optional[str] = None, cursor: Optional[str] = None) -> MiroResponse:
        """List items on a board (use type='frame' to filter frames).  [frames]"""
        params = self._params(limit=limit, type=type, cursor=cursor)
        result = self._sdk.get_items(board_id, **params)
        return MiroResponse(success=True, data=result)
    def list_organizations(self, org_id: str) -> MiroResponse:
        """Get organization details by ID. Requires enterprise plan.  [organizations]"""
        result = self._sdk.enterprise_get_organization(org_id)
        return MiroResponse(success=True, data=result)
    def list_org_members(self, org_id: str, role: Optional[str] = None, limit: Optional[int] = None, cursor: Optional[str] = None) -> MiroResponse:
        """List members of an organization. Requires enterprise plan.  [organizations]"""
        params = self._params(role=role, limit=limit, cursor=cursor)
        result = self._sdk.enterprise_get_organization_members(org_id, **params)
        return MiroResponse(success=True, data=result)

