"""Read access to chat attachments and artifacts for non-uploaders.

`can_read_record` is the single gate for read paths: the existing ACL query OR
the Node PDP for chat content. The uploader/creator is never sent to the PDP
(the OWNER edge already grants them access) and neither is a service account
(no USER OWNER edge, so no `ownerUserId` to assert).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from app.config.constants.arangodb import CollectionNames, Connectors, RecordTypes
from app.modules.authz.node_pdp_client import (
    ChatArtifactKind,
    ChatContentCheck,
    ChatContentPdp,
)
from app.services.graph_db.common.record_visibility import is_live_record

if TYPE_CHECKING:
    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

logger = logging.getLogger(__name__)

RecordRef = str | dict[str, Any] | object

__all__ = ["can_read_chat_content_via_pdp", "can_read_record", "resolve_read_access"]

_PERMISSION = CollectionNames.PERMISSION.value
_RECORDS = CollectionNames.RECORDS.value
_USERS = CollectionNames.USERS.value


class _RecordView:
    __slots__ = ("id", "org_id", "connector_name", "record_type")

    def __init__(
        self, record_id: str | None, org_id: str | None, connector_name: str | None, record_type: str | None,
    ) -> None:
        self.id = record_id
        self.org_id = org_id
        self.connector_name = connector_name
        self.record_type = record_type


def _enum_value(value: object) -> str | None:
    if value is None:
        return None
    return str(getattr(value, "value", value))


async def _view_of(graph: IGraphDBProvider, record: RecordRef) -> _RecordView | None:
    if isinstance(record, str):
        record = await graph.get_document(record, _RECORDS)
        if not record:
            return None
    # A trashed record keeps its edges and chat rows for restore; to a reader it is gone.
    if not is_live_record(record):
        return None
    if isinstance(record, dict):
        return _RecordView(
            record.get("id") or record.get("_key"),
            record.get("orgId"),
            _enum_value(record.get("connectorName")),
            _enum_value(record.get("recordType")),
        )
    return _RecordView(
        getattr(record, "id", None),
        getattr(record, "org_id", None),
        _enum_value(getattr(record, "connector_name", None)),
        _enum_value(getattr(record, "record_type", None)),
    )


async def _owner_user_id(graph: IGraphDBProvider, record_id: str) -> str | None:
    """`userId` of the USER that holds the OWNER edge on the record."""
    edges = await graph.get_edges_to_node(f"{_RECORDS}/{record_id}", _PERMISSION)
    for edge in edges or []:
        if not isinstance(edge, dict) or edge.get("role") != "OWNER" or edge.get("type") != "USER":
            continue
        raw = str(edge.get("from_id") or edge.get("_from") or "")
        collection, _, key = raw.rpartition("/")
        collection = collection or str(edge.get("from_collection") or _USERS)
        if not key or collection != _USERS:
            continue
        user_doc = await graph.get_document(key, _USERS)
        owner = (user_doc or {}).get("userId")
        if owner:
            return str(owner)
    return None


async def can_read_chat_content_via_pdp(
    graph: IGraphDBProvider,
    pdp: ChatContentPdp,
    *,
    user_id: str | None,
    org_id: str | None,
    record: RecordRef,
    conversation_id: str | None = None,
    acl_version: int | None = None,
) -> bool:
    """PDP-only half of `can_read_record`: True iff Node allows a non-uploader.

    Anything that is not a chat attachment or a conversation artifact, or whose
    uploader cannot be resolved, is denied without asking Node. Never raises.
    """
    if not user_id or not org_id:
        return False
    try:
        view = await _view_of(graph, record)
        if view is None or not view.id or view.org_id != org_id:
            return False

        if view.connector_name == Connectors.ATTACHMENTS.value:
            resource_type = "chatAttachment"
            run_id: str | None = None
            kind: ChatArtifactKind | None = None
            scope_conversation = conversation_id
        elif view.record_type == RecordTypes.ARTIFACT.value:
            artifact = await graph.get_document(view.id, CollectionNames.ARTIFACTS.value)
            artifact_conversation = (artifact or {}).get("conversationId")
            if not artifact_conversation:
                return False
            # A caller-asserted conversation that differs from the artifact's
            # own must not borrow another chat's grant.
            if conversation_id and conversation_id != artifact_conversation:
                return False
            resource_type = "chatArtifact"
            scope_conversation = artifact_conversation
            run_id = (artifact or {}).get("runId")
            kind = ChatArtifactKind(
                visibility=str((artifact or {}).get("visibility") or "VISIBLE"),
                is_temporary=bool((artifact or {}).get("isTemporary", False)),
            )
        else:
            return False

        # `acl_version` is the asking chat's; it may key the PDP cache only when that
        # is the chat being decided, or a revocation there would not change the key.
        if scope_conversation is None or scope_conversation != conversation_id:
            acl_version = None

        owner = await _owner_user_id(graph, view.id)
        if not owner or owner == user_id:
            return False

        return await pdp.can_read_chat_content(
            ChatContentCheck(
                user_id=user_id,
                org_id=org_id,
                resource_type=resource_type,  # type: ignore[arg-type]
                record_id=view.id,
                owner_user_id=owner,
                conversation_id=scope_conversation,
                run_id=run_id,
                kind=kind,
                acl_version=acl_version,
            )
        )
    except Exception:
        logger.warning("authz_pdp_error: chat content check failed", exc_info=True)
        return False


async def resolve_read_access(
    graph: IGraphDBProvider,
    pdp: ChatContentPdp | None,
    *,
    user_id: str | None,
    org_id: str | None,
    record: RecordRef,
    conversation_id: str | None = None,
    acl_version: int | None = None,
) -> dict | None:
    """The ACL-details dict when the caller may read the record, else None.

    A PDP allow returns a minimal details dict flagged `viaChatContentPdp`.
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

    if pdp is None:
        from app.modules.authz.node_pdp_client import get_node_pdp_client

        pdp = get_node_pdp_client()
    if await can_read_chat_content_via_pdp(
        graph, pdp, user_id=user_id, org_id=org_id, record=record,
        conversation_id=conversation_id, acl_version=acl_version,
    ):
        doc = await graph.get_document(record_id, _RECORDS)
        return {"record": doc or {"id": record_id}, "permissions": [], "viaChatContentPdp": True}
    return None


async def can_read_record(
    graph: IGraphDBProvider,
    pdp: ChatContentPdp | None,
    *,
    user_id: str | None,
    org_id: str | None,
    record: RecordRef,
    conversation_id: str | None = None,
    acl_version: int | None = None,
) -> bool:
    return (
        await resolve_read_access(
            graph, pdp, user_id=user_id, org_id=org_id, record=record,
            conversation_id=conversation_id, acl_version=acl_version,
        )
        is not None
    )
