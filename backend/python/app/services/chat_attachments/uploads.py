"""A chat attachment's graph documents, with who uploaded it recorded on the record (QA-15G).

A member's upload is granted to them (an ``OWNER`` edge); a service account's
to the org, because the person it acts for (a Slack user without an account,
hydrated as a stable id per email) has no user node. Erasure must still know
whose upload it is, so the record itself names the uploader: the person, and
for a service account the agent it came through.

These fields are new to the record schema, which an older release holds closed: rolled back to, it refuses any
update of a record that carries one. So they are written only where the agent harness is on for the org
(ADR-OPS-08), whose erasure reads them (``uploads_tracked``); with it off an upload's record is exactly what it was
before them.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

from app.config.constants.arangodb import CollectionNames, Connectors
from app.core.org_context import org_of
from app.services.featureflag.platform_settings import is_agent_harness_on
from app.utils.record_access import service_account_upload_permission_edges

if TYPE_CHECKING:
    from collections.abc import Mapping

    from app.config.configuration_service import ConfigurationService

__all__ = [
    "AWAITING_SEND_FIELD",
    "UPLOAD_CONVERSATION_FIELD",
    "UPLOADER_AGENT_FIELD",
    "UPLOADER_KIND_FIELD",
    "UPLOADER_USER_FIELD",
    "AttachmentFile",
    "Uploader",
    "UploaderKind",
    "attachment_documents",
    "mark_sent",
    "recorded_uploader",
    "uploads_tracked",
    "write_attachment_graph",
]

UPLOADER_USER_FIELD = "uploaderUserId"
# The conversation an upload was made in, when the client already had one (CLEANUP-G5).
UPLOAD_CONVERSATION_FIELD = "uploadConversationId"
# True from the upload until a message carries it; an upload still waiting after the grace period is collected.
AWAITING_SEND_FIELD = "awaitingSend"
UPLOADER_KIND_FIELD = "uploaderKind"
UPLOADER_AGENT_FIELD = "uploaderAgentKey"


class UploaderKind(str, Enum):
    PERSON = "person"
    SERVICE_ACCOUNT = "service_account"


@dataclass(frozen=True)
class Uploader:
    """The person an upload belongs to (auth user id) and, for a service account, the agent it came through."""

    user_id: str
    kind: UploaderKind
    agent_key: str | None = None

    @classmethod
    def from_claims(cls, claims: Mapping[str, Any]) -> Uploader:
        user_id = claims.get("userId")
        if not isinstance(user_id, str) or not user_id:
            raise ValueError("an upload needs the user it belongs to")
        if not claims.get("isServiceAccount"):
            return cls(user_id, UploaderKind.PERSON)
        agent_key = claims.get("agentKey")
        return cls(
            user_id,
            UploaderKind.SERVICE_ACCOUNT,
            agent_key if isinstance(agent_key, str) and agent_key else None,
        )

    def record_fields(self) -> dict[str, str]:
        # Neo4j stores no null properties, so an absent agent key is no field at all.
        fields = {UPLOADER_USER_FIELD: self.user_id, UPLOADER_KIND_FIELD: self.kind.value}
        if self.agent_key is not None:
            fields[UPLOADER_AGENT_FIELD] = self.agent_key
        return fields


async def uploads_tracked(config_service: ConfigurationService | None) -> bool:
    """Whether a new upload's record names its uploader and awaits its message: only where the harness is on for
    the org."""
    return await is_agent_harness_on(config_service)


def recorded_uploader(record: Mapping[str, Any]) -> str | None:
    """The uploader's user id an upload's record names; None for an upload written before it was recorded."""
    value = record.get(UPLOADER_USER_FIELD)
    return value if isinstance(value, str) and value else None


@dataclass(frozen=True)
class AttachmentFile:
    record_id: str
    virtual_record_id: str
    file_name: str
    mime_type: str
    size: int
    extension: str
    external_record_id: str


def attachment_record_doc(*, record_id: str, org_id: str, item: Any, external_record_id: str, virtual_record_id: str, now: int) -> dict[str, Any]:  # noqa: ANN401
    """The record written for an uploaded chat attachment: a new record, so it starts its indexing run at ``now``."""
    return {
        "_key": record_id,
        "id": record_id,
        "orgId": org_id,
        "recordName": item.fileName,
        "externalRecordId": external_record_id,
        "recordType": "FILE",
        "origin": "UPLOAD",
        "connectorId": f"attachments_{org_id}",
        "connectorName": Connectors.ATTACHMENTS.value,
        "createdAtTimestamp": now,
        "updatedAtTimestamp": now,
        "sourceCreatedAtTimestamp": now,
        "sourceLastModifiedTimestamp": now,
        "isDeleted": False,
        "isArchived": False,
        "indexingStatus": "NOT_STARTED",
        "extractionStatus": "NOT_STARTED",
        "firstQueuedAt": now,
        "indexingAttempts": 0,
        "version": 1,
        "mimeType": item.mimeType,
        "sizeInBytes": item.size,
        "virtualRecordId": virtual_record_id,
    }


def attachment_documents(
    org_id: str,
    file: AttachmentFile,
    uploader: Uploader,
    now: int,
    *,
    tracked: bool,
    conversation_id: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """The RECORDS and FILES documents of one upload (same key); ``tracked`` (``uploads_tracked``) adds who
    uploaded it, its conversation and the marker that it waits for the message that sends it."""
    tracking: dict[str, Any] = {}
    if tracked:
        tracking = {
            **uploader.record_fields(),
            AWAITING_SEND_FIELD: True,
            # Neo4j stores no null properties: an upload into a new chat has no field.
            **({UPLOAD_CONVERSATION_FIELD: conversation_id} if conversation_id else {}),
        }
    item = SimpleNamespace(fileName=file.file_name, mimeType=file.mime_type, size=file.size)
    record = {
        **attachment_record_doc(
            record_id=file.record_id, org_id=org_id, item=item, external_record_id=file.external_record_id,
            virtual_record_id=file.virtual_record_id, now=now,
        ),
        **tracking,
    }
    file_document = {
        "_key": file.record_id,
        "id": file.record_id,
        "orgId": org_id,
        "name": file.file_name,
        "isFile": True,
        "extension": file.extension,
        "mimeType": file.mime_type,
        "sizeInBytes": file.size,
    }
    return record, file_document


def _grant_edges(uploader: Uploader, org_id: str, user_key: str | None, record_keys: list[str], ts: int) -> list[dict[str, Any]]:
    if uploader.kind is UploaderKind.SERVICE_ACCOUNT:
        return service_account_upload_permission_edges(org_id, record_keys, ts)
    if not user_key:
        raise ValueError("a member's upload is granted to their user node")
    return [
        {
            "from_id": user_key,
            "from_collection": CollectionNames.USERS.value,
            "to_id": key,
            "to_collection": CollectionNames.RECORDS.value,
            "type": "USER",
            "role": "OWNER",
            "createdAtTimestamp": ts,
            "updatedAtTimestamp": ts,
        }
        for key in record_keys
    ]


async def write_attachment_graph(
    graph_provider: Any,  # noqa: ANN401 - IGraphDBProvider
    org_id: str,
    records: list[dict[str, Any]],
    files: list[dict[str, Any]],
    uploader: Uploader,
    user_key: str | None,
    ts: int,
) -> None:
    """Records, files, their type edges and the grant: a member's ``OWNER`` edge, a service account's org grant."""
    await graph_provider.batch_upsert_nodes(records, CollectionNames.RECORDS.value)
    await graph_provider.batch_upsert_nodes(files, CollectionNames.FILES.value)
    await graph_provider.batch_create_edges(
        [
            {
                "_from": f"{CollectionNames.RECORDS.value}/{record['_key']}",
                "_to": f"{CollectionNames.FILES.value}/{record['_key']}",
                "createdAtTimestamp": ts,
                "updatedAtTimestamp": ts,
            }
            for record in records
        ],
        CollectionNames.IS_OF_TYPE.value,
    )
    await graph_provider.batch_create_edges(
        _grant_edges(uploader, org_id, user_key, [record["_key"] for record in records], ts),
        CollectionNames.PERMISSION.value,
    )


async def mark_sent(
    graph_provider: Any,  # noqa: ANN401 - IGraphDBProvider
    org_id: str,
    record_ids: list[str],
    conversation_id: str | None,
) -> list[str]:
    """Marks the org's uploads a message now carries as sent, so the unsent collector never takes them.

    Records that are not this org's chat attachments are skipped, and so is an upload written untracked (no
    ``awaitingSend`` field), which gains no field here. Idempotent. Returns the ids marked.
    """
    marked: list[str] = []
    for record_id in dict.fromkeys(record_ids):
        record = await graph_provider.get_document(record_id, CollectionNames.RECORDS.value)
        if not record or org_of(record) != org_id or record.get("connectorName") != Connectors.ATTACHMENTS.value:
            continue
        if AWAITING_SEND_FIELD not in record:
            continue
        updates: dict[str, Any] = {}
        if record.get(AWAITING_SEND_FIELD) is True:
            updates[AWAITING_SEND_FIELD] = False
        if conversation_id and not record.get(UPLOAD_CONVERSATION_FIELD):
            updates[UPLOAD_CONVERSATION_FIELD] = conversation_id
        if updates:
            await graph_provider.update_node(record_id, CollectionNames.RECORDS.value, updates)
        marked.append(record_id)
    return marked
