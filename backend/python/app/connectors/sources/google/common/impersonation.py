"""
Impersonation-candidate resolution shared by the Google Workspace connectors, which all
reach records through domain-wide delegation. Only the "who do we impersonate, in what
order" decision lives here; each connector keeps ownership of building its own API client
from the chosen identity (`_get_drive_service_with_fallback`,
`_get_gmail_service_with_fallback`).
"""

from logging import Logger
from typing import List, Optional, Set, Tuple

from app.config.constants.arangodb import CollectionNames
from app.connectors.core.base.data_store.data_store import DataStoreProvider
from app.models.entities import User

DELEGATION_ERROR_MARKER = "unauthorized_client"

# Each candidate costs the caller a real impersonation token exchange plus an API
# call, sequentially, on a user-facing request path. This caps worst-case latency
# for records shared with an unbounded number of users; it's generous enough that
# it should only ever be hit by pathologically over-shared records.
MAX_IMPERSONATION_CANDIDATES = 25


def is_delegation_error(error: Exception) -> bool:
    """
    Whether a Google API failure means domain-wide delegation is not authorized for the
    impersonated user — they belong to a different Workspace/tenant than the one this
    connector's service account can be delegated for. Any other failure (file not found,
    transient network error, etc.) is not retryable with a different user.
    """
    return DELEGATION_ERROR_MARKER in str(error)


async def resolve_explicit_user(
    logger: Logger,
    data_store_provider: DataStoreProvider,
    user_id: Optional[str],
) -> Optional[User]:
    """
    Resolve an explicitly-requested user_id straight to a User, with no permission-
    holder lookup involved. Callers that already know exactly who to impersonate
    (e.g. the public /api/v1/stream/record route, which passes the actual
    authenticated caller from request.state.user) don't need — and shouldn't pay
    the cost of — the broader candidate search in get_impersonation_candidates;
    that search exists only for callers that don't have a user_id at all (e.g. the
    internal indexing stream route, whose JWT carries no user identity).
    """
    if not user_id or user_id == "None":
        return None
    async with data_store_provider.transaction() as tx_store:
        user = await tx_store.get_user_by_user_id(user_id)
    email = user.get("email") if user else None
    if not email:
        logger.warning(f"User not found for user_id {user_id}, falling back to users with permission to node")
        return None
    logger.info(f"Retrieved user email {email} for user_id {user_id}")
    return User(email=email, is_active=user.get("isActive"))


async def get_impersonation_candidates(
    data_store_provider: DataStoreProvider,
    record_id: str,
    synced_user_emails: Set[str],
    logger: Optional[Logger] = None,
) -> List[User]:
    """
    Build an ordered list of every user with permission to a record, to try
    impersonating for domain-wide-delegation access, sorted so users the calling
    connector's own workspace sync has confirmed exist (synced_user_emails) come
    first — a record can be shared with users outside this Workspace/tenant whose
    credentials the connector's service account can never be delegated to
    impersonate (e.g. an external owner who shared the file with one of our synced
    users).

    When synced_user_emails is empty — right after a restart, or for a connector
    instance that was lazily created just to stream and never ran a full user sync —
    every permission holder is left as an equally-ranked candidate rather than
    excluded. The caller tries them one by one, so an unpopulated cache degrades to
    "try everyone" instead of failing outright.

    Capped at MAX_IMPERSONATION_CANDIDATES: each candidate costs the caller a real
    token exchange plus an API call, so an over-shared record shouldn't be able to
    make a single request perform an unbounded number of sequential round-trips.
    """
    async with data_store_provider.transaction() as tx_store:
        permission_holders = await tx_store.get_users_with_permission_to_node(
            record_id, CollectionNames.RECORDS.value
        )

    def sort_key(user: User) -> Tuple[int, int]:
        email_lower = (user.email or "").lower()
        in_synced_workspace = 0 if email_lower in synced_user_emails else 1
        inactive = 1 if user.is_active is False else 0
        return (in_synced_workspace, inactive)

    ordered: List[User] = []
    seen_emails: set[str] = set()
    for user in sorted(permission_holders, key=sort_key):
        email_lower = (user.email or "").lower()
        if not email_lower or email_lower in seen_emails:
            continue
        seen_emails.add(email_lower)
        ordered.append(user)

    if len(ordered) > MAX_IMPERSONATION_CANDIDATES:
        if logger:
            logger.warning(
                f"Record {record_id} has {len(ordered)} impersonation candidates; "
                f"trying only the first {MAX_IMPERSONATION_CANDIDATES}"
            )
        ordered = ordered[:MAX_IMPERSONATION_CANDIDATES]

    return ordered
