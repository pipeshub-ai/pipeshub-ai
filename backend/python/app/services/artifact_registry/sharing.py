"""Owner-driven sharing of one artifact with users of the same org.

A direct share is a READER ``permission`` edge tagged ``grantedVia:
ARTIFACT_SHARE_GRANT`` (see ``access.py`` for how it coexists with a
conversation grant on the same edge). Every read path (gallery, record stream,
``AccessPolicy.authorize_read``) already accepts a READER edge, so sharing
writes edges and nothing else. Graph access goes through
``IGraphDBProvider`` so this works unchanged on Neo4j and ArangoDB.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

from app.config.constants.arangodb import CollectionNames
from app.services.notification.types import (
    NotificationOrigin,
    NotificationSeverity,
    NotificationType,
)
from app.utils.time_conversion import get_epoch_timestamp_in_ms

from .access import AccessPolicy, ArtifactNotFoundError, is_direct_share, reader_edge
from .gallery import ArtifactDisplayPolicy

if TYPE_CHECKING:
    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider
    from app.services.notification.notification_service import NotificationService

    from .models import Actor

logger = logging.getLogger(__name__)

__all__ = ["MAX_SHARE_TARGETS", "ArtifactSharingService", "InvalidShareTargetError"]

MAX_SHARE_TARGETS = 100


class InvalidShareTargetError(ValueError):
    """A target user does not exist in the actor's org, or there are too many."""


def _user_key_from_edge(edge: dict) -> str | None:
    """Arango returns ``_from: "users/<key>"``; Neo4j returns ``from_id`` plus
    ``from_collection``. Permission edges also come from groups and teams."""
    if edge.get("from_id"):
        return edge["from_id"] if edge.get("from_collection") == CollectionNames.USERS.value else None
    collection, _, key = (edge.get("_from") or "").partition("/")
    return key if collection == CollectionNames.USERS.value and key else None


class ArtifactSharingService:
    def __init__(
        self, graph: IGraphDBProvider, notification_service: NotificationService | None = None
    ) -> None:
        self._graph = graph
        self._access = AccessPolicy(graph)
        self._notifications = notification_service

    async def list_members(self, actor: Actor, artifact_id: str) -> list[dict[str, str]]:
        """The owner and the users the artifact was shared with directly. Users
        who read it through a shared conversation are managed from that chat."""
        await self._authorize(actor, artifact_id)
        edges = await self._graph.get_edges_to_node(
            f"{CollectionNames.RECORDS.value}/{artifact_id}",
            CollectionNames.PERMISSION.value,
            raise_on_error=True,
        )
        members: list[dict[str, str]] = []
        for edge in edges or []:
            if edge.get("role") != "OWNER" and not is_direct_share(edge):
                continue
            user_key = _user_key_from_edge(edge)
            if not user_key:
                continue
            user = await self._graph.get_document(
                user_key, CollectionNames.USERS.value, raise_on_error=True
            )
            if user and user.get("userId"):
                members.append({"userId": user["userId"], "role": edge["role"]})
        return members

    async def share(self, actor: Actor, artifact_id: str, user_ids: list[str]) -> int:
        """Grant READER to each target that cannot already read the artifact
        directly, and notify them. A READER edge from a shared conversation is
        tagged and keeps a note of that grant, so each outlives the other."""
        artifact_name = await self._authorize(actor, artifact_id)
        targets = await self._resolve_targets(actor, user_ids)
        now = get_epoch_timestamp_in_ms()
        edges: list[dict[str, Any]] = []
        recipients: list[str] = []
        for user_id, user_key in targets.items():
            edge = await self._get_permission_edge(user_key, artifact_id)
            if edge and (edge.get("role") != "READER" or is_direct_share(edge)):
                continue
            # Any edge left here is an untagged READER edge: a shared conversation's grant.
            edges.append(
                reader_edge(
                    user_key,
                    artifact_id,
                    direct=True,
                    chat=edge is not None,
                    now=now,
                    created_at=(edge or {}).get("createdAtTimestamp"),
                )
            )
            recipients.append(user_id)
        if edges:
            await self._graph.batch_create_edges(edges, CollectionNames.PERMISSION.value)
            await self._notify(actor, artifact_id, artifact_name, recipients)
        return len(edges)

    async def unshare(self, actor: Actor, artifact_id: str, user_ids: list[str]) -> int:
        """Take back direct shares only. The owner's edge is never touched, and a
        reader a shared conversation also covers keeps that conversation's grant."""
        await self._authorize(actor, artifact_id)
        if len(user_ids) > MAX_SHARE_TARGETS:
            raise InvalidShareTargetError(f"At most {MAX_SHARE_TARGETS} users per request")
        now = get_epoch_timestamp_in_ms()
        to_delete: list[dict[str, str]] = []
        to_keep_for_chat: list[dict[str, Any]] = []
        for user_id in dict.fromkeys(user_ids):
            user = await self._graph.get_user_by_user_id(user_id, raise_on_error=True)
            user_key = user and (user.get("_key") or user.get("id"))
            if not user_key:
                continue
            edge = await self._get_permission_edge(user_key, artifact_id)
            if not is_direct_share(edge):
                continue
            if edge.get("chatGranted"):
                to_keep_for_chat.append(
                    reader_edge(
                        user_key,
                        artifact_id,
                        direct=False,
                        chat=True,
                        now=now,
                        created_at=edge.get("createdAtTimestamp"),
                    )
                )
            else:
                to_delete.append(
                    {
                        "from_id": user_key,
                        "from_collection": CollectionNames.USERS.value,
                        "to_id": artifact_id,
                        "to_collection": CollectionNames.RECORDS.value,
                    }
                )
        if to_keep_for_chat:
            await self._graph.batch_create_edges(to_keep_for_chat, CollectionNames.PERMISSION.value)
        if to_delete:
            await self._graph.batch_delete_edges(to_delete, CollectionNames.PERMISSION.value)
        return len(to_keep_for_chat) + len(to_delete)

    async def _authorize(self, actor: Actor, artifact_id: str) -> str:
        """Only the owner manages sharing, and only of an artifact the gallery
        shows. Returns the artifact's display name."""
        record = await self._access.authorize_write(actor, artifact_id)
        artifact_doc = await self._graph.get_document(
            artifact_id, CollectionNames.ARTIFACTS.value, raise_on_error=True
        )
        if not artifact_doc or not ArtifactDisplayPolicy.is_user_visible_doc(artifact_doc):
            raise ArtifactNotFoundError(f"Artifact not found: {artifact_id}")
        return artifact_doc.get("name") or record.get("recordName") or ""

    async def _resolve_targets(self, actor: Actor, user_ids: list[str]) -> dict[str, str]:
        """Auth ``userId`` -> graph user key for each target other than the actor.
        ``get_user_by_user_id`` is not org-scoped, so the org is checked here."""
        unique = [user_id for user_id in dict.fromkeys(user_ids) if user_id and user_id != actor.user_id]
        if len(unique) > MAX_SHARE_TARGETS:
            raise InvalidShareTargetError(f"At most {MAX_SHARE_TARGETS} users per request")
        targets: dict[str, str] = {}
        for user_id in unique:
            user = await self._graph.get_user_by_user_id(user_id, raise_on_error=True)
            user_key = user and (user.get("_key") or user.get("id"))
            if not user_key or user.get("orgId") != actor.org_id:
                raise InvalidShareTargetError(f"User not found: {user_id}")
            targets[user_id] = user_key
        return targets

    async def _get_permission_edge(self, user_key: str, artifact_id: str) -> dict | None:
        return await self._graph.get_edge(
            from_id=user_key,
            from_collection=CollectionNames.USERS.value,
            to_id=artifact_id,
            to_collection=CollectionNames.RECORDS.value,
            collection=CollectionNames.PERMISSION.value,
            raise_on_error=True,
        )

    async def _notify(
        self, actor: Actor, artifact_id: str, artifact_name: str, recipients: list[str]
    ) -> None:
        """Best-effort: the share is already written, so a failure here is logged
        by the notification service and never undoes it."""
        if not self._notifications:
            return
        sharer = await self._graph.get_user_by_user_id(actor.user_id) or {}
        sharer_name = sharer.get("fullName") or sharer.get("email") or "A teammate"
        await self._notifications.publish_notification(
            org_id=actor.org_id,
            origin=NotificationOrigin.AI,
            type=NotificationType.ARTIFACT_SHARED,
            severity=NotificationSeverity.INFO,
            title=f"{sharer_name} shared an artifact with you",
            message=artifact_name,
            payload={"artifactId": artifact_id},
            redirect_link=f"/artifacts/?tab=shared&artifactId={quote(artifact_id, safe='')}",
            recipient_user_ids=recipients,
        )
