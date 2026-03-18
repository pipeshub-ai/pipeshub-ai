#!/usr/bin/env python3
# ruff: noqa
from __future__ import annotations

"""
Miro (miro_api SDK) -- Code Generator (strict, no `Any` in public signatures)

Emits a `MiroDataSource` with explicit, typed methods mapped to *real* miro_api SDK APIs.
- Wraps the official `MiroApi` class from the `miro_api` package.
- Accepts either a raw `MiroApi` instance or any client exposing `.get_sdk() -> MiroApi`.
- All methods return `MiroResponse` for a uniform interface.

SDK reference: https://miroapp.github.io/api-clients/python/
"""

import argparse
import textwrap
from pathlib import Path
from typing import List, Tuple

# -----------------------------
# Configuration knobs (CLI-set)
# -----------------------------

DEFAULT_RESPONSE_IMPORT = (
    "from app.sources.client.miro.miro import MiroResponse"
)
DEFAULT_CLASS_NAME = "MiroDataSource"
DEFAULT_OUT = "app/sources/external/miro/miro.py"


HEADER = '''\
# ruff: noqa
from __future__ import annotations

from miro_api import MiroApi
from typing import Dict, List, Optional, Union, cast

{response_import}


class {class_name}:
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
'''

FOOTER = """
"""

# Each tuple: (signature, body, short_doc)
METHODS: List[Tuple[str, str, str]] = []

# ---------- Boards ----------
METHODS += [
    (
        "list_boards(self, team_id: Optional[str] = None, query: Optional[str] = None, owner: Optional[str] = None, sort: Optional[str] = None, limit: Optional[str] = None, offset: Optional[str] = None) -> MiroResponse",
        "            params = self._params(team_id=team_id, query=query, owner=owner, sort=sort, limit=limit, offset=offset)\n"
        "            result = self._sdk.get_boards(**params)\n"
        "            return MiroResponse(success=True, data=result)",
        "List boards accessible to the authenticated user.  [boards]",
    ),
    (
        "get_board(self, board_id: str) -> MiroResponse",
        "            result = self._sdk.get_specific_board(board_id)\n"
        "            return MiroResponse(success=True, data=result)",
        "Get a single board by ID.  [boards]",
    ),
    (
        "create_board(self, board_changes: object) -> MiroResponse",
        "            result = self._sdk.create_board(board_changes)\n"
        "            return MiroResponse(success=True, data=result)",
        "Create a new board. Pass a BoardChanges model or compatible dict.  [boards]",
    ),
    (
        "update_board(self, board_id: str, board_changes: object) -> MiroResponse",
        "            result = self._sdk.update_board(board_id, board_changes)\n"
        "            return MiroResponse(success=True, data=result)",
        "Update an existing board.  [boards]",
    ),
    (
        "delete_board(self, board_id: str) -> MiroResponse",
        "            self._sdk.delete_board(board_id)\n"
        "            return MiroResponse(success=True, data=True)",
        "Delete a board by ID.  [boards]",
    ),
]

# ---------- Board Items ----------
METHODS += [
    (
        "list_board_items(self, board_id: str, limit: Optional[str] = None, type: Optional[str] = None, cursor: Optional[str] = None) -> MiroResponse",
        "            params = self._params(limit=limit, type=type, cursor=cursor)\n"
        "            result = self._sdk.get_items(board_id, **params)\n"
        "            return MiroResponse(success=True, data=result)",
        "List all items on a board with optional filters.  [items]",
    ),
    (
        "get_board_item(self, board_id: str, item_id: str) -> MiroResponse",
        "            result = self._sdk.get_specific_item(board_id, item_id)\n"
        "            return MiroResponse(success=True, data=result)",
        "Get a specific item on a board.  [items]",
    ),
]

# ---------- Sticky Notes ----------
METHODS += [
    (
        "create_sticky_note(self, board_id: str, sticky_note_create_request: object) -> MiroResponse",
        "            result = self._sdk.create_sticky_note_item(board_id, sticky_note_create_request)\n"
        "            return MiroResponse(success=True, data=result)",
        "Create a sticky note on a board. Pass a StickyNoteCreateRequest model.  [sticky_notes]",
    ),
]

# ---------- Cards ----------
METHODS += [
    (
        "create_card(self, board_id: str, card_create_request: object) -> MiroResponse",
        "            result = self._sdk.create_card_item(board_id, card_create_request)\n"
        "            return MiroResponse(success=True, data=result)",
        "Create a card on a board. Pass a CardCreateRequest model.  [cards]",
    ),
]

# ---------- Text ----------
METHODS += [
    (
        "create_text(self, board_id: str, text_create_request: object) -> MiroResponse",
        "            result = self._sdk.create_text_item(board_id, text_create_request)\n"
        "            return MiroResponse(success=True, data=result)",
        "Create a text item on a board. Pass a TextCreateRequest model.  [text]",
    ),
]

# ---------- Shapes ----------
METHODS += [
    (
        "create_shape(self, board_id: str, shape_create_request: object) -> MiroResponse",
        "            result = self._sdk.create_shape_item(board_id, shape_create_request)\n"
        "            return MiroResponse(success=True, data=result)",
        "Create a shape on a board. Pass a ShapeCreateRequest model.  [shapes]",
    ),
]

# ---------- Connectors ----------
METHODS += [
    (
        "list_connectors(self, board_id: str, limit: Optional[str] = None, cursor: Optional[str] = None) -> MiroResponse",
        "            params = self._params(limit=limit, cursor=cursor)\n"
        "            result = self._sdk.get_connectors(board_id, **params)\n"
        "            return MiroResponse(success=True, data=result)",
        "List all connectors on a board.  [connectors]",
    ),
    (
        "create_connector(self, board_id: str, connector_creation_data: object) -> MiroResponse",
        "            result = self._sdk.create_connector(board_id, connector_creation_data)\n"
        "            return MiroResponse(success=True, data=result)",
        "Create a connector between two items on a board.  [connectors]",
    ),
]

# ---------- Board Members ----------
METHODS += [
    (
        "list_board_members(self, board_id: str, limit: Optional[str] = None, offset: Optional[str] = None) -> MiroResponse",
        "            params = self._params(limit=limit, offset=offset)\n"
        "            result = self._sdk.get_board_members(board_id, **params)\n"
        "            return MiroResponse(success=True, data=result)",
        "List all members of a board.  [members]",
    ),
    (
        "share_board(self, board_id: str, board_members_invite: object) -> MiroResponse",
        "            result = self._sdk.share_board(board_id, board_members_invite)\n"
        "            return MiroResponse(success=True, data=result)",
        "Share a board by inviting members. Pass a BoardMembersInvite model.  [members]",
    ),
]

# ---------- Tags ----------
METHODS += [
    (
        "list_board_tags(self, board_id: str, limit: Optional[str] = None, offset: Optional[str] = None) -> MiroResponse",
        "            params = self._params(limit=limit, offset=offset)\n"
        "            result = self._sdk.get_tags_from_board(board_id, **params)\n"
        "            return MiroResponse(success=True, data=result)",
        "List all tags on a board.  [tags]",
    ),
    (
        "create_tag(self, board_id: str, tag_create_request: object) -> MiroResponse",
        "            result = self._sdk.create_tag(board_id, tag_create_request)\n"
        "            return MiroResponse(success=True, data=result)",
        "Create a tag on a board. Pass a TagCreateRequest model.  [tags]",
    ),
]

# ---------- Frames ----------
METHODS += [
    (
        "list_frames(self, board_id: str, limit: Optional[str] = None, type: Optional[str] = None, cursor: Optional[str] = None) -> MiroResponse",
        "            params = self._params(limit=limit, type=type, cursor=cursor)\n"
        "            result = self._sdk.get_items(board_id, **params)\n"
        "            return MiroResponse(success=True, data=result)",
        "List items on a board (use type='frame' to filter frames).  [frames]",
    ),
]

# ---------- Organizations ----------
METHODS += [
    (
        "list_organizations(self, org_id: str) -> MiroResponse",
        "            result = self._sdk.enterprise_get_organization(org_id)\n"
        "            return MiroResponse(success=True, data=result)",
        "Get organization details by ID. Requires enterprise plan.  [organizations]",
    ),
    (
        "list_org_members(self, org_id: str, role: Optional[str] = None, limit: Optional[int] = None, cursor: Optional[str] = None) -> MiroResponse",
        "            params = self._params(role=role, limit=limit, cursor=cursor)\n"
        "            result = self._sdk.enterprise_get_organization_members(org_id, **params)\n"
        "            return MiroResponse(success=True, data=result)",
        "List members of an organization. Requires enterprise plan.  [organizations]",
    ),
]

# -------------------------
# Code emission utilities
# -------------------------


def _emit_method(sig: str, body: str, doc: str) -> str:
    normalized_body = textwrap.indent(textwrap.dedent(body), "        ")
    return f'    def {sig}:\n        """{doc}"""\n{normalized_body}\n'


def build_class(
    response_import: str = DEFAULT_RESPONSE_IMPORT,
    class_name: str = DEFAULT_CLASS_NAME,
) -> str:
    parts: List[str] = []
    header = (
        HEADER.replace("{response_import}", response_import)
        .replace("{class_name}", class_name)
    )
    parts.append(header)
    for sig, body, doc in METHODS:
        parts.append(_emit_method(sig, body, doc))
    parts.append(FOOTER)
    return "".join(parts)


def write_output(path: str, code: str) -> None:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        f.write(code)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate MiroDataSource (miro_api SDK)."
    )
    parser.add_argument(
        "--out",
        default=DEFAULT_OUT,
        help="Output path for the generated data source.",
    )
    parser.add_argument(
        "--response-import",
        default=DEFAULT_RESPONSE_IMPORT,
        help="Import line to bring in MiroResponse.",
    )
    parser.add_argument(
        "--class-name",
        default=DEFAULT_CLASS_NAME,
        help="Name of the generated datasource class.",
    )
    parser.add_argument(
        "--print",
        dest="do_print",
        action="store_true",
        help="Also print generated code to stdout.",
    )
    args = parser.parse_args()

    code = build_class(
        response_import=args.response_import, class_name=args.class_name
    )
    write_output(args.out, code)
    print(f"Generated MiroDataSource with {len(METHODS)} methods -> {args.out}")
    if args.do_print:
        print(code)


if __name__ == "__main__":
    main()
