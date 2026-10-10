"""Whether the caller may read a record addressed by virtualRecordId.

Chat attachments carry a client-supplied virtualRecordId. The blob lookup is
keyed on that id alone, so a download has to be refused unless this user can
still read a live record that owns it. A missing id and a failed check return
the same False: telling them apart would say whether the record exists.
"""

from __future__ import annotations

import logging
from typing import Any

from app.config.constants.arangodb import CollectionNames

# The permission type on the org -> record edge that makes a service account's
# chat upload readable. Distinct from "ORG" (an org-wide share from a
# connector) so that a service account reading attachments is granted uploads
# and nothing a connector shared with the whole org.
SERVICE_ACCOUNT_UPLOAD_PERMISSION_TYPE = "ORGANIZATION"


def is_chat_attachment(record: dict | None, org_id: str) -> bool:
    """Whether ``record`` is a file uploaded to a chat of this org."""
    return (
        bool(record)
        and record.get("orgId") == org_id
        and record.get("origin") == "UPLOAD"
        and record.get("connectorId") == f"attachments_{org_id}"
    )


def service_account_upload_permission_edges(
    org_id: str, record_keys: list[str], timestamp: int, uploaded_by: str,
) -> list[dict[str, Any]]:
    """The org -> record READER edges written for a service account's upload.

    A service account has no user node, so its uploads are granted to the org.
    ``uploaded_by`` names the caller, since every such upload carries the same grant.
    """
    return [
        {
            "from_id": org_id,
            "from_collection": CollectionNames.ORGS.value,
            "to_id": record_key,
            "to_collection": CollectionNames.RECORDS.value,
            "type": SERVICE_ACCOUNT_UPLOAD_PERMISSION_TYPE,
            "role": "READER",
            "uploadedBy": uploaded_by,
            "createdAtTimestamp": timestamp,
            "updatedAtTimestamp": timestamp,
        }
        for record_key in record_keys
    ]


async def caller_can_read_virtual_record(
    graph_provider: Any,
    *,
    user_id: str | None,
    org_id: str | None,
    virtual_record_id: str,
    logger: logging.Logger,
    is_service_account: bool = False,
) -> bool:
    """True when this caller may read some live record that owns the virtual record.

    No user, no graph, a lookup error, or no accessible record all deny.
    A service account has no user node, so the user ACL query cannot succeed
    for it. Its uploads are granted with an org permission edge instead, and
    only that edge, written for this caller's own upload, is accepted here.
    """
    if not graph_provider or not org_id or not virtual_record_id or not user_id:
        return False

    try:
        record_ids = await graph_provider.get_records_by_virtual_record_id(
            virtual_record_id, raise_on_error=True,
        )
    except Exception:
        logger.warning(
            "Attachment access check could not resolve virtual record %s",
            virtual_record_id,
            exc_info=True,
        )
        return False

    for record_id in record_ids or []:
        if not record_id:
            continue
        try:
            if await graph_provider.check_record_access_with_details(
                user_id, org_id, record_id,
            ):
                return True
        except Exception:
            logger.warning(
                "Attachment access check failed for record %s",
                record_id,
                exc_info=True,
            )
        if is_service_account and await org_permission_grants(
            graph_provider, org_id, record_id, logger, uploaded_by=user_id,
        ):
            return True
    return False


async def org_permission_grants(
    graph_provider: Any,
    org_id: str,
    record_id: str,
    logger: logging.Logger,
    *,
    uploaded_by: str | None = None,
) -> bool:
    """True when this org holds a permission edge on the record.

    That is the grant `upload_chat_attachments` writes for a service account,
    which has no user node for `check_record_access_with_details` to match.
    With ``uploaded_by``, only the grant written for that caller's own upload.
    """
    try:
        edge = await graph_provider.get_edge(
            from_id=org_id,
            from_collection=CollectionNames.ORGS.value,
            to_id=record_id,
            to_collection=CollectionNames.RECORDS.value,
            collection=CollectionNames.PERMISSION.value,
        )
    except Exception:
        logger.warning(
            "Attachment org-permission check failed for record %s",
            record_id,
            exc_info=True,
        )
        return False
    return (
        bool(edge)
        and edge.get("type") == SERVICE_ACCOUNT_UPLOAD_PERMISSION_TYPE
        and bool(edge.get("role"))
        and (uploaded_by is None or edge.get("uploadedBy") == uploaded_by)
    )
