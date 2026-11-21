"""The stored Microsoft accounts a user listing could not vouch for, confirmed one by one.

Shared by the Outlook, OneDrive and SharePoint connectors. A listing that answered (complete, cut short or empty)
vouches for the enabled accounts it shows. Every other stored account is read by id (``GET /users/{id}``, which
"returns a ``404 Not Found`` error code" when "a user with the ID doesn't exist",
https://learn.microsoft.com/en-us/graph/api/user-get): one that is gone or disabled loses the connector, one found
enabled keeps it. A deleted user is moved to deleted items and is restorable for 30 days
(https://learn.microsoft.com/en-us/graph/api/user-delete); restoring it lists it again, and the next sync links it again.

A read that fails is not an answer. An account no read could confirm keeps its access for
``UNCONFIRMED_SYNCS_BEFORE_CLOSE`` syncs in a row, then loses it: a failure that lasts may hide an account Microsoft
has since removed. One confirming read starts the count again.
"""

import json
import os
from collections.abc import Awaitable, Callable
from typing import Any

from app.connectors.core.base.sync_point.sync_point import SyncPoint
from app.services.notification.types import NotificationSeverity, NotificationType

UNCONFIRMED_SYNCS_BEFORE_CLOSE = 3
ACCOUNT_CHECK_KEY = "unconfirmed-accounts"
# Each read is one request on the app's directory budget (3,500 to 8,000 resource units per 10 seconds per app and
# tenant, https://learn.microsoft.com/en-us/graph/throttling-limits); a listing cut on page one of a large directory would
# otherwise read every account in one sync. The rest wait for the next sync, the longest unconfirmed first.
LOOKUPS_ENV = "MICROSOFT_USER_LOOKUPS_PER_SYNC"
DEFAULT_LOOKUPS_PER_SYNC = 500


def lookups_per_sync() -> int:
    raw = os.getenv(LOOKUPS_ENV, str(DEFAULT_LOOKUPS_PER_SYNC))
    try:
        n = int(raw)
    except ValueError:
        n = 0
    if n < 1:
        raise ValueError(f"{LOOKUPS_ENV} must be a whole number of at least 1, got {raw!r}")
    return n


async def withdraw_unconfirmed_accounts(
    connector: Any,
    sync_point: SyncPoint,
    lookup: Callable[[str], Awaitable[str]],
    listed: set[str],
    disabled: set[str],
    complete: bool,
) -> bool:
    """Withdraw the stored accounts that are gone or disabled, by this sync's listing (``listed``, with ``disabled``
    among them) or by a read of the account (``lookup``: ``gone``, ``disabled`` or ``present``; raises when it fails),
    and those no read confirmed for ``UNCONFIRMED_SYNCS_BEFORE_CLOSE`` syncs. ``complete`` says the listing was proven
    whole; an empty ``listed`` with ``complete`` False is a listing that failed.

    Returns whether the listing can be trusted: it was complete and every stored account it does not list was
    confirmed gone."""
    processor, log = connector.data_entities_processor, connector.logger
    try:
        stored = await processor.get_app_user_links(connector.connector_id)
    except Exception as e:
        log.error(f"Could not read the stored users; no user loses access this run: {e}")
        return False
    try:
        state = await sync_point.read_sync_point(ACCOUNT_CHECK_KEY) or {}
    except Exception as e:
        # Without the count no account can pass the grace this run; the reads still confirm.
        log.warning(f"Could not read the unconfirmed-account count; no account is closed by it this run: {e}")
        state = None
    try:
        saved = json.loads((state or {}).get("streaks") or "{}")
    except (TypeError, ValueError):
        saved = {}
    streaks = {k: v for k, v in saved.items() if isinstance(v, int)} if isinstance(saved, dict) else {}

    trusted = complete and bool(listed)
    gone, unlisted = [], []
    for u in stored:
        sid = u.source_user_id
        if not sid:
            continue
        if sid in disabled:
            gone.append(u)
        elif sid not in listed:
            unlisted.append(u)

    cursor = (state or {}).get("cursor") or ""
    # Accounts already counting go first, so a lasting failure closes them; the rest in turn from where the last sync stopped.
    counting = sorted((u for u in unlisted if u.source_user_id in streaks), key=lambda u: (-streaks[u.source_user_id], u.source_user_id))
    rest = sorted((u for u in unlisted if u.source_user_id not in streaks), key=lambda u: u.source_user_id)
    rest = [u for u in rest if u.source_user_id > cursor] + [u for u in rest if u.source_user_id <= cursor]
    cap = lookups_per_sync()
    asked = (counting + rest)[:cap]
    waiting = len(unlisted) - len(asked)
    closed, confirmed = [], set()
    for u in asked:
        sid = u.source_user_id
        try:
            status = await lookup(sid)
        except Exception as e:
            trusted = False
            streaks[sid] = streaks.get(sid, 0) + 1
            if state is not None and streaks[sid] >= UNCONFIRMED_SYNCS_BEFORE_CLOSE:
                closed.append(u)
            else:
                log.error(f"Could not confirm user {sid} is gone; keeping the access ({streaks[sid]} of "
                          f"{UNCONFIRMED_SYNCS_BEFORE_CLOSE} syncs): {e}")
            continue
        confirmed.add(sid)
        if status != "gone":
            trusted = False
        if status != "present":
            gone.append(u)
    if waiting:
        trusted = False
        log.info(f"{waiting} stored account(s) the listing did not show wait for a later sync's check "
                 f"(at most {cap} reads per sync, {LOOKUPS_ENV})")

    lost = gone + closed
    if lost:
        # The link is the connector's only gate for these connectors, so the user's own grants stay: an account restored
        # or confirmed again is linked by the next listing and has its access back at once.
        await processor.withdraw_app_user_access(connector.connector_id, lost)
    if closed:
        log.error(f"The user listing is unverified past the grace: {len(closed) + len(gone)} of {len(asked)} linked "
                  f"people asked about this run were gone or not confirmed active and lose the app link ({len(stored)} linked)")
        await connector.notify(
            type=NotificationType.CONNECTOR_USER_SYNC_ERROR,
            severity=NotificationSeverity.WARNING,
            title=f"{connector.connector_name.value}: accounts Microsoft could not confirm lost access",
            message=(f"{len(closed)} linked account(s) could not be confirmed active by Microsoft for "
                     f"{UNCONFIRMED_SYNCS_BEFORE_CLOSE} syncs in a row, so they no longer have access through this "
                     "connector. They get it back on the first sync that lists them."),
            recipient_user_ids=[connector.created_by],
        )
    if state is not None:
        # Only accounts still linked and still unconfirmed keep counting: a confirmed, closed or listed one starts again.
        still = {u.source_user_id for u in unlisted} - confirmed - {u.source_user_id for u in closed}
        keep = {k: v for k, v in streaks.items() if k in still}
        rest_ids = {u.source_user_id for u in rest}
        last = next((u.source_user_id for u in reversed(asked) if u.source_user_id in rest_ids), cursor)
        new_state = {"streaks": json.dumps(keep, sort_keys=True), "cursor": last}
        if new_state != {"streaks": state.get("streaks") or "{}", "cursor": cursor}:
            try:
                await sync_point.update_sync_point(ACCOUNT_CHECK_KEY, new_state)
            except Exception as e:
                log.warning(f"Could not save the unconfirmed-account count: {e}")
    return trusted
