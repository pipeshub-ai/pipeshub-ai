"""Read access to chat attachments and artifacts for non-uploaders.

`can_read_record` is the single gate for read paths: the existing ACL query OR
the installed `ChatContentAccess`. The default grants nothing beyond the ACL,
so only users with a permission edge on the record (the uploader's OWNER
edge, or a READER edge) can read a chat's files.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Protocol

from app.config.constants.arangodb import CollectionNames

if TYPE_CHECKING:
    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

logger = logging.getLogger(__name__)

RecordRef = str | dict[str, Any] | object

__all__ = [
    "ChatContentAccess",
    "NoChatContentAccess",
    "can_read_record",
    "get_chat_content_access",
    "resolve_read_access",
]

_RECORDS = CollectionNames.RECORDS.value


class ChatContentAccess(Protocol):
    async def can_read_chat_content(
        self,
        graph: IGraphDBProvider,
        *,
        user_id: str | None,
        org_id: str | None,
        record: RecordRef,
        conversation_id: str | None = None,
        acl_version: int | None = None,
    ) -> bool:
        """True iff a caller without an ACL path may read this chat content. Never raises."""
        ...


class NoChatContentAccess:
    """Chat content is readable only through the graph ACL."""

    async def can_read_chat_content(
        self,
        graph: IGraphDBProvider,
        *,
        user_id: str | None,
        org_id: str | None,
        record: RecordRef,
        conversation_id: str | None = None,
        acl_version: int | None = None,
    ) -> bool:
        return False


def get_chat_content_access() -> ChatContentAccess:
    from app.modules.agents.chat_extension import get_chat_extension

    return get_chat_extension().content_access()


async def resolve_read_access(
    graph: IGraphDBProvider,
    access: ChatContentAccess | None = None,
    *,
    user_id: str | None,
    org_id: str | None,
    record: RecordRef,
    conversation_id: str | None = None,
    acl_version: int | None = None,
) -> dict | None:
    """The ACL-details dict when the caller may read the record, else None.

    An allow from `access` returns a minimal details dict flagged `viaChatContentPdp`.
    """
    if not user_id or not org_id:
        return None
    record_id = record if isinstance(record, str) else (
        record.get("id") or record.get("_key") if isinstance(record, dict) else getattr(record, "id", None)
    )
    if not record_id:
        return None

    details = await graph.check_record_access_with_details(user_id, org_id, record_id)
    if details:
        return details

    if await (access or get_chat_content_access()).can_read_chat_content(
        graph, user_id=user_id, org_id=org_id, record=record,
        conversation_id=conversation_id, acl_version=acl_version,
    ):
        doc = await graph.get_document(record_id, _RECORDS)
        return {"record": doc or {"id": record_id}, "permissions": [], "viaChatContentPdp": True}
    return None


async def can_read_record(
    graph: IGraphDBProvider,
    access: ChatContentAccess | None = None,
    *,
    user_id: str | None,
    org_id: str | None,
    record: RecordRef,
    conversation_id: str | None = None,
    acl_version: int | None = None,
) -> bool:
    return (
        await resolve_read_access(
            graph, access, user_id=user_id, org_id=org_id, record=record,
            conversation_id=conversation_id, acl_version=acl_version,
        )
        is not None
    )
