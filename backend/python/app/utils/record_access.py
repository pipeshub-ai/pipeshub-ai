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
from app.utils.error_class import log_failure

# The permission type on the org -> record edge that makes a service account's
# chat upload readable. Distinct from "ORG" (an org-wide share from a
# connector) so that a service account reading attachments is granted uploads
# and nothing a connector shared with the whole org.
SERVICE_ACCOUNT_UPLOAD_PERMISSION_TYPE = "ORGANIZATION"


def service_account_upload_permission_edges(
    org_id: str, record_keys: list[str], timestamp: int,
) -> list[dict[str, Any]]:
    """The org -> record READER edges written for a service account's upload.

    A service account has no user node, so its uploads are granted to the org.
    """
    return [
        {
            "from_id": org_id,
            "from_collection": CollectionNames.ORGS.value,
            "to_id": record_key,
            "to_collection": CollectionNames.RECORDS.value,
            "type": SERVICE_ACCOUNT_UPLOAD_PERMISSION_TYPE,
            "role": "READER",
            "createdAtTimestamp": timestamp,
            "updatedAtTimestamp": timestamp,
        }
        for record_key in record_keys
    ]


def attachment_reader_id(user_id: str | None, user_info: object) -> str | None:
    """Whose grant a chat attachment is read under: the person who sent the turn (R33JGAPS-FU-6).

    A service-account agent's chat runs as the agent (``user_id`` is its account), but the files in it were attached
    by the person chatting, and attaching one is that person's grant to the run they started: so the read is
    checked as them (``callerUserId``), never as the account. Scoped by construction: only the attachments of this
    conversation reach the resolver, and another run of the agent carries its own caller, who cannot read them. A
    request with no person behind it (the Slack bot's own upload) keeps ``user_id``, whose uploads the org edge grants.
    """
    from app.harness.knowledge.caller import UNRESOLVED_CALLER, caller_user_id_of

    caller = caller_user_id_of(user_info)
    return caller if caller is not None and caller != UNRESOLVED_CALLER else user_id


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
            except Exception as exc:
                log_failure(logger, "record_access.check_failed", exc, record_id=record_id, include_error_text=False)
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
    return (
        bool(edge)
        and edge.get("type") == SERVICE_ACCOUNT_UPLOAD_PERMISSION_TYPE
        and bool(edge.get("role"))
    )
