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
    only that edge is accepted here.
    """
    if not graph_provider or not org_id or not virtual_record_id:
        return False
    if not user_id and not is_service_account:
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
        if user_id:
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
        if is_service_account and await _org_permission_grants(
            graph_provider, org_id, record_id, logger,
        ):
            return True
    return False


async def _org_permission_grants(
    graph_provider: Any,
    org_id: str,
    record_id: str,
    logger: logging.Logger,
) -> bool:
    """True when this org holds a permission edge on the record.

    That is the grant `upload_chat_attachments` writes for a service account,
    which has no user node for `check_record_access_with_details` to match.
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
    return bool(edge) and edge.get("type") == "ORGANIZATION" and bool(edge.get("role"))
