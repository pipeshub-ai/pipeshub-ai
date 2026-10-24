# pyright: ignore-file

"""Build expected Outlook graph entities for integration tests.

Mirrors the connector mapping in
``app/connectors/sources/microsoft/outlook/connector.py`` so the graph-DB entity can be
compared whole against an independently-built expectation via ``assert_graph_entity_with_edges``.

Builders accept the Microsoft Graph SDK entity objects returned by ``OutlookSourceHelper``
read-backs (snake_case attrs: ``.id``, ``.subject``, ``.conversation_id``, ``.web_link``,
``.from_``, ``.to_recipients`` ...) and return backend pydantic models with ``id``/``org_id``
left blank (both are in the validator's default skip-set).

The ``*_SKIP_COMPARE`` frozensets list fields the sync pipeline mutates or that are otherwise
non-deterministic across the two processes (test vs backend) — pass them as ``skip_compare`` so
the assertion focuses on identity + source-derived + structural fields.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from app.config.constants.arangodb import (
    Connectors,
    MimeTypes,
    OriginTypes,
    PermissionModel,
)
from app.connectors.sources.microsoft.common.content_type_utils import (
    attachment_metadata_from_graph,
)
from app.connectors.sources.microsoft.common.outlook_constants import (
    OutlookDefaults,
    mail_content_revision,
    normalize_conversation_index,
)
from app.models.entities import (
    AppUserGroup,
    FileRecord,
    MailRecord,
    RecordGroup,
    RecordGroupType,
    RecordType,
)
from app.utils.time_conversion import datetime_to_epoch_ms

# Fields the sync/index pipeline sets or mutates independently of the source object — skip them so
# the comparison asserts identity + source-derived + mail-specific fields, not pipeline bookkeeping.
_PIPELINE_NOISE = frozenset({
    "created_at", "updated_at", "version", "extraction_status", "record_status",
    "preview_renderable", "block_containers", "semantic_metadata", "is_vlm_ocr_processed",
    "reason", "summary_document_id", "md5_hash", "size_in_bytes", "signed_url",
    "fetch_signed_url", "storage_document_id", "child_record_ids", "related_record_ids",
    "related_external_records", "shared_with_me_record_group_ids", "is_shared", "is_internal",
    "is_placeholder", "hide_weburl", "inherit_permissions", "parent_record_id", "parent_node_id",
    "weburl",
})

MAIL_SKIP_COMPARE = _PIPELINE_NOISE | frozenset({
    "label_ids",
    "is_dependent_node",
    "source_created_at",
    "source_updated_at",
    "external_revision_id",
    "conversation_index",
})
FILE_SKIP_COMPARE = _PIPELINE_NOISE
# inherit_permissions is not persisted on RecordGroup nodes (it is expressed as an INHERIT
# edge), so it always reads back as the default; the suite asserts the edge instead.
RECORD_GROUP_SKIP_COMPARE = frozenset({
    "created_at", "updated_at", "source_created_at", "source_updated_at", "web_url",
    "parent_record_group_id", "is_internal", "hide_children", "inherit_permissions",
})


def mailbox_external_id(user_source_id: str) -> str:
    """External id of a user's mailbox record group (connector ``_mailbox_external_id``)."""
    return f"mailbox:{user_source_id}"


def _email_of(recipient: Any) -> str:
    """Mirror connector ``_extract_email_from_recipient``: recipient.emailAddress.address."""
    if recipient is None:
        return ""
    email_address = getattr(recipient, "email_address", None)
    if email_address is None:
        return ""
    return getattr(email_address, "address", "") or ""


class OutlookExpected:
    """Expected graph entities for Outlook connector integration tests."""

    @staticmethod
    def mail_record(
        message: Any,
        *,
        connector_id: str,
        folder_id: str,
    ) -> MailRecord:
        """Build the expected ``MailRecord`` for a personal-mailbox email (RecordType.MAIL)."""
        subject = getattr(message, "subject", None) or OutlookDefaults.SUBJECT
        return MailRecord(
            id="",
            org_id="",
            record_name=subject,
            record_type=RecordType.MAIL,
            external_record_id=message.id,
            external_revision_id=mail_content_revision(message),
            version=0,
            origin=OriginTypes.CONNECTOR,
            connector_name=Connectors.OUTLOOK,
            connector_id=connector_id,
            source_created_at=datetime_to_epoch_ms(getattr(message, "created_date_time", None)),
            source_updated_at=datetime_to_epoch_ms(getattr(message, "last_modified_date_time", None)),
            mime_type=MimeTypes.HTML.value,
            parent_external_record_id=None,
            external_record_group_id=folder_id,
            record_group_type=RecordGroupType.MAILBOX,
            is_dependent_node=False,
            subject=subject,
            from_email=_email_of(getattr(message, "from_", None)),
            to_emails=[_email_of(r) for r in (getattr(message, "to_recipients", None) or [])],
            cc_emails=[_email_of(r) for r in (getattr(message, "cc_recipients", None) or [])],
            bcc_emails=[_email_of(r) for r in (getattr(message, "bcc_recipients", None) or [])],
            thread_id=getattr(message, "conversation_id", None) or "",
            is_parent=False,
            internet_message_id=getattr(message, "internet_message_id", None) or "",
            conversation_index=normalize_conversation_index(
                getattr(message, "conversation_index", None)
            ),
        )

    @staticmethod
    def group_mail_record(
        post: Any,
        thread_topic: str,
        thread_id: str,
        *,
        connector_id: str,
        group_id: str,
    ) -> MailRecord:
        """Build the expected ``MailRecord`` for an M365 group conversation post (GROUP_MAIL)."""
        return MailRecord(
            id="",
            org_id="",
            record_name=thread_topic,
            record_type=RecordType.GROUP_MAIL,
            external_record_id=post.id,
            external_revision_id=None,
            version=0,
            origin=OriginTypes.CONNECTOR,
            connector_name=Connectors.OUTLOOK,
            connector_id=connector_id,
            source_created_at=datetime_to_epoch_ms(getattr(post, "received_date_time", None)),
            source_updated_at=datetime_to_epoch_ms(getattr(post, "received_date_time", None)),
            mime_type=MimeTypes.HTML.value,
            external_record_group_id=group_id,
            record_group_type=RecordGroupType.GROUP_MAILBOX,
            is_dependent_node=False,
            subject=thread_topic,
            from_email=_email_of(getattr(post, "from_", None)),
            to_emails=[],
            cc_emails=[],
            bcc_emails=[],
            thread_id=thread_id,
            is_parent=False,
            internet_message_id="",
            conversation_index="",
        )

    @staticmethod
    def file_record(
        attachment: Any,
        *,
        connector_id: str,
        parent_message_id: str,
        folder_id: str,
        record_group_type: RecordGroupType = RecordGroupType.MAILBOX,
        parent_record_type: RecordType = RecordType.MAIL,
    ) -> FileRecord:
        """Build the expected ``FileRecord`` for a mail attachment (RecordType.FILE)."""
        content_type = getattr(attachment, "content_type", "") or ""
        file_name, mime_type, extension = attachment_metadata_from_graph(
            getattr(attachment, "name", None), content_type, OutlookDefaults.ATTACHMENT_NAME
        )
        last_modified = getattr(attachment, "last_modified_date_time", None)
        if isinstance(last_modified, str):
            last_modified = datetime.fromisoformat(last_modified.replace("Z", "+00:00"))
        return FileRecord(
            id="",
            org_id="",
            record_name=file_name,
            record_type=RecordType.FILE,
            external_record_id=attachment.id,
            external_revision_id=last_modified.isoformat() if last_modified else None,
            version=0,
            origin=OriginTypes.CONNECTOR,
            connector_name=Connectors.OUTLOOK,
            connector_id=connector_id,
            source_created_at=datetime_to_epoch_ms(last_modified),
            source_updated_at=datetime_to_epoch_ms(last_modified),
            mime_type=mime_type,
            extension=extension,
            parent_external_record_id=parent_message_id,
            parent_record_type=parent_record_type,
            external_record_group_id=folder_id,
            record_group_type=record_group_type,
            is_file=True,
            is_dependent_node=True,
        )

    @staticmethod
    def record_group_folder(
        folder: Any,
        *,
        connector_id: str,
        user_email: str,
        user_source_id: str,
        is_top_level: bool,
    ) -> RecordGroup:
        """Build the expected mail-folder ``RecordGroup`` (group_type=MAILBOX).

        A top-level folder hangs under the user's mailbox record group, a nested one under its
        parent folder.
        """
        folder_name = getattr(folder, "display_name", None) or OutlookDefaults.FOLDER_NAME
        parent = (
            mailbox_external_id(user_source_id) if is_top_level
            else getattr(folder, "parent_folder_id", None)
        )
        return RecordGroup(
            id="",
            org_id="",
            name=folder_name,
            short_name=folder_name,
            description=f"{folder_name} folder for {user_email}",
            external_group_id=folder.id,
            parent_external_group_id=parent,
            connector_name=Connectors.OUTLOOK,
            connector_id=connector_id,
            group_type=RecordGroupType.MAILBOX,
            permission_model=PermissionModel.RECORD_GROUP_LEVEL,
            inherit_permissions=True,
        )

    @staticmethod
    def record_group_mailbox(
        *,
        connector_id: str,
        user_email: str,
        user_source_id: str,
    ) -> RecordGroup:
        """Build the expected per-user mailbox ``RecordGroup``: the one node holding the owner grant."""
        return RecordGroup(
            id="",
            org_id="",
            name=user_email,
            short_name=user_email,
            description=f"Mailbox for {user_email}",
            external_group_id=mailbox_external_id(user_source_id),
            parent_external_group_id=None,
            connector_name=Connectors.OUTLOOK,
            connector_id=connector_id,
            group_type=RecordGroupType.MAILBOX,
            permission_model=PermissionModel.RECORD_GROUP_LEVEL,
            inherit_permissions=False,
        )

    @staticmethod
    def record_group_group_mailbox(
        group: Any,
        *,
        connector_id: str,
    ) -> RecordGroup:
        """Build the expected group-mailbox ``RecordGroup`` (group_type=GROUP_MAILBOX)."""
        group_name = getattr(group, "display_name", None) or OutlookDefaults.UNKNOWN_GROUP_LABEL
        description = f"{group_name} group mailbox"
        group_mail = getattr(group, "mail", None)
        if group_mail:
            description += f" ({group_mail})"
        return RecordGroup(
            id="",
            org_id="",
            name=group_name,
            short_name=group_name,
            description=description,
            external_group_id=group.id,
            parent_external_group_id=None,
            connector_name=Connectors.OUTLOOK,
            connector_id=connector_id,
            group_type=RecordGroupType.GROUP_MAILBOX,
            permission_model=PermissionModel.RECORD_GROUP_LEVEL,
            inherit_permissions=False,
            source_created_at=datetime_to_epoch_ms(getattr(group, "created_date_time", None)),
            source_updated_at=datetime_to_epoch_ms(getattr(group, "created_date_time", None)),
        )

    @staticmethod
    def app_user_group(
        group: Any,
        *,
        connector_id: str,
    ) -> AppUserGroup:
        """Build the expected ``AppUserGroup`` for an M365 group."""
        group_name = getattr(group, "display_name", None) or OutlookDefaults.UNKNOWN_GROUP_LABEL
        return AppUserGroup(
            id="",
            org_id="",
            app_name=Connectors.OUTLOOK,
            connector_id=connector_id,
            source_user_group_id=group.id,
            name=group_name,
            description=getattr(group, "description", None),
            created_at=0,
            updated_at=0,
            source_created_at=None,
            source_updated_at=None,
        )
