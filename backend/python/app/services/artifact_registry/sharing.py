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
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

from app.config.constants.arangodb import CollectionNames
from app.services.cache.invalidation_hooks import notify_principals_changed
from app.services.notification.types import (
    NotificationOrigin,
    NotificationSeverity,
    NotificationType,
)
from app.utils.time_conversion import get_epoch_timestamp_in_ms

from .access import (
    SHARED_VERSION,
    AccessPolicy,
    ArtifactNotFoundError,
    is_direct_share,
    reader_edge,
)
from .gallery import ArtifactDisplayPolicy

if TYPE_CHECKING:
    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider
    from app.services.notification.notification_service import NotificationService

    from .models import Actor

logger = logging.getLogger(__name__)

__all__ = [
    "MAX_SHARE_TARGETS",
    "ArtifactSharingService",
    "InvalidShareTargetError",
    "ShareGate",
    "ShareNotAllowedError",
    "ShareNotWidenableError",
]

MAX_SHARE_TARGETS = 100


class InvalidShareTargetError(ValueError):
    """A target user does not exist in the actor's org, or there are too many."""


class ShareNotAllowedError(PermissionError):
    """The artifact is never shared on, whoever owns it: a service-account agent's run made it
    (stamped when it was written), and who may use what that agent reads is its admin's choice."""


class ShareNotWidenableError(PermissionError):
    """The edition refused to show the content this file was made from to more people; ``code`` says why."""

    def __init__(self, code: str) -> None:
        super().__init__("This file can't be shared: it was made from something you can't share.")
        self.code = code


# Why the owner may not grant this file (an error code), or None. An exception refuses too.
ShareGate = Callable[["Actor", str, dict[str, Any]], Awaitable[str | None]]


def _user_key_from_edge(edge: dict) -> str | None:
    """Arango returns ``_from: "users/<key>"``; Neo4j returns ``from_id`` plus
    ``from_collection``. Permission edges also come from groups and teams."""
    if edge.get("from_id"):
        return edge["from_id"] if edge.get("from_collection") == CollectionNames.USERS.value else None
    collection, _, key = (edge.get("_from") or "").partition("/")
    return key if collection == CollectionNames.USERS.value and key else None


class ArtifactSharingService:
    def __init__(
        self,
        graph: IGraphDBProvider,
        notification_service: NotificationService | None = None,
        share_gate: ShareGate | None = None,
    ) -> None:
        self._graph = graph
        self._access = AccessPolicy(graph)
        self._notifications = notification_service
        self._share_gate = share_gate

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
        artifact_doc, artifact_name, current = await self._authorize(actor, artifact_id)
        if artifact_doc.get("serviceAgentRun") is True:
            raise ShareNotAllowedError("A file a service account agent made can't be shared.")
        # The gate judges the file as it is now: the version the share is checked on.
        await self._check_gate(actor, artifact_id, {**artifact_doc, "version": current})
        targets = await self._resolve_targets(actor, user_ids)
        now = get_epoch_timestamp_in_ms()
        edges: list[dict[str, Any]] = []
        refreshed: list[dict[str, Any]] = []
        recipients: list[str] = []
        reshared: list[str] = []
        for user_id, user_key in targets.items():
            edge = await self._get_permission_edge(user_key, artifact_id)
            if edge and edge.get("role") != "READER":
                continue
            if is_direct_share(edge):
                # Shared again: the share now covers the version it was just checked on. One made before the version
                # was recorded was checked on the first.
                if (edge.get(SHARED_VERSION) or 1) != current:
                    refreshed.append(
                        reader_edge(
                            user_key,
                            artifact_id,
                            direct=True,
                            chat=bool(edge.get("chatGranted")),
                            now=now,
                            created_at=edge.get("createdAtTimestamp"),
                            shared_version=current,
                        )
                    )
                    reshared.append(user_id)
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
                    shared_version=current,
                )
            )
            recipients.append(user_id)
        if edges or refreshed:
            await self._graph.batch_create_edges(edges + refreshed, CollectionNames.PERMISSION.value)
            await notify_principals_changed(actor.org_id, user_ids=recipients + reshared)  # a new READER grant
        if edges:
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
        if to_keep_for_chat or to_delete:
            await notify_principals_changed(actor.org_id, user_ids=user_ids)  # a narrowing PERMISSION write
        return len(to_keep_for_chat) + len(to_delete)

    async def _check_gate(self, actor: Actor, artifact_id: str, artifact_doc: dict) -> None:
        """Refuses the grant when the edition's gate does, or when the gate itself fails."""
        if self._share_gate is None:
            return
        try:
            refusal = await self._share_gate(actor, artifact_id, artifact_doc)
        except Exception:
            logger.warning("Artifact share gate failed; refusing the share", exc_info=True)
            refusal = "SHARE_NOT_WIDENABLE"
        if refusal is not None:
            raise ShareNotWidenableError(refusal)

    async def _authorize(self, actor: Actor, artifact_id: str) -> tuple[dict, str, int]:
        """Only the owner manages sharing, and only of an artifact the gallery
        shows. Returns the artifact's document, its display name and its current version."""
        record = await self._access.authorize_write(actor, artifact_id)
        artifact_doc = await self._graph.get_document(
            artifact_id, CollectionNames.ARTIFACTS.value, raise_on_error=True
        )
        if not artifact_doc or not ArtifactDisplayPolicy.is_user_visible_doc(artifact_doc):
            raise ArtifactNotFoundError(f"Artifact not found: {artifact_id}")
        version = record.get("version")
        current = version if isinstance(version, int) and not isinstance(version, bool) and version >= 1 else 1
        return artifact_doc, artifact_doc.get("name") or record.get("recordName") or "", current

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
