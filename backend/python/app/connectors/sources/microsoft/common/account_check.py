"""The stored Microsoft accounts a user listing could not vouch for, confirmed one by one.

Shared by the Outlook, OneDrive and SharePoint connectors. A listing that answered (complete, cut short or empty)
vouches for the enabled accounts it shows. Every other stored account is read by id (``GET /users/{id}``, which
"returns a ``404 Not Found`` error code" when "a user with the ID doesn't exist",
https://learn.microsoft.com/en-us/graph/api/user-get): one that is gone or disabled loses the connector, one found
enabled keeps it. A deleted user is moved to deleted items and is restorable for 30 days
(https://learn.microsoft.com/en-us/graph/api/user-delete); restoring it lists it again, and the next sync links it again.

A read that fails is not an answer. An account whose reads Microsoft refuses (an error status) keeps its access for
``UNCONFIRMED_SYNCS_BEFORE_CLOSE`` syncs in a row, then loses it: a refusal that lasts may hide an account Microsoft
has since removed. One confirming read starts the count again. A read that never reached Microsoft is not counted.
"""

import json
import os
from collections.abc import Awaitable, Callable
from logging import Logger
from typing import TYPE_CHECKING

from msgraph.generated.models.o_data_errors.o_data_error import ODataError

from app.connectors.core.base.sync_point.sync_point import SyncPoint
from app.services.notification.types import (
    NotificationOutcome,
    NotificationSeverity,
    NotificationType,
)

if TYPE_CHECKING:
    from app.connectors.core.base.connector.connector_service import BaseConnector

UNCONFIRMED_SYNCS_BEFORE_CLOSE = 3
ACCOUNT_CHECK_KEY = "unconfirmed-accounts"
# Each read is one request on the app's directory budget (3,500 to 8,000 resource units per 10 seconds for one app in
# one directory, https://learn.microsoft.com/en-us/graph/throttling-limits); a listing cut on page one of a large directory would
# otherwise read every account in one sync. Half the reads go to accounts already counting, half to the others, each half
# in turn from where the last sync stopped, so neither kind can starve the other; the rest wait for the next sync.
LOOKUPS_ENV = "MICROSOFT_USER_LOOKUPS_PER_SYNC"
DEFAULT_LOOKUPS_PER_SYNC = 500


def lookups_per_sync() -> int:
    raw = os.getenv(LOOKUPS_ENV, str(DEFAULT_LOOKUPS_PER_SYNC))
    try:
        n = int(raw)
    except ValueError:
        n = 0
    if n < 2:
        # One read each for the accounts already counting and for the others.
        raise ValueError(f"{LOOKUPS_ENV} must be a whole number of at least 2, got {raw!r}")
    return n


async def withdraw_unconfirmed_accounts(
    connector: "BaseConnector",
    sync_point: SyncPoint,
    lookup: Callable[[str], Awaitable[str]],
    listed: set[str],
    disabled: set[str],
    *,
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

    saved_state = state or {}
    cursor, counting_cursor = saved_state.get("cursor") or "", saved_state.get("counting_cursor") or ""
    counting = _in_turn([u for u in unlisted if u.source_user_id in streaks], counting_cursor)
    rest = _in_turn([u for u in unlisted if u.source_user_id not in streaks], cursor)
    cap = lookups_per_sync()
    n_counting = min(len(counting), cap - min(len(rest), cap // 2))
    asked = counting[:n_counting] + rest[:cap - n_counting]
    waiting = len(unlisted) - len(asked)
    closed, confirmed = [], set()
    for u in asked:
        sid = u.source_user_id
        try:
            status = await lookup(sid)
        except Exception as e:
            trusted = False
            if not isinstance(e, ODataError):
                # Not an answer from Microsoft (no client, a transport failure): kept, and not counted.
                log.error(f"Could not read user {sid}; keeping the access: {e}")
                continue
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

    rest_ids = {u.source_user_id for u in rest}
    pending = _ids(saved_state.get("pending_notice")) | {u.source_user_id for u in closed}
    new_state = {
        # Closed accounts keep counting until their withdrawal commits; the next sync no longer finds them linked.
        "streaks": json.dumps({k: v for k, v in streaks.items() if k in {u.source_user_id for u in unlisted} - confirmed},
                              sort_keys=True),
        "cursor": next((u.source_user_id for u in reversed(asked) if u.source_user_id in rest_ids), cursor),
        "counting_cursor": next((u.source_user_id for u in reversed(asked) if u.source_user_id not in rest_ids), counting_cursor),
        "pending_notice": json.dumps(sorted(pending)),
    }
    if state is not None:
        # Saved before the withdrawal: the owner's notice of a close stays owed until it is sent.
        await _save(sync_point, log, saved_state, new_state)
    lost = gone + closed
    if lost:
        # The link is the connector's only gate for these connectors, so the user's own grants stay: an account restored
        # or confirmed again is linked by the next listing and has its access back at once.
        await processor.withdraw_app_user_access(connector.connector_id, lost)
    if closed:
        log.error(f"The user listing is unverified past the grace: {len(closed) + len(gone)} of {len(asked)} linked "
                  f"people asked about this run were gone or not confirmed active and lose the app link ({len(stored)} linked)")
    if state is not None and pending:
        # Not suppressed as a repeat: each close is sent once, through ``pending_notice``.
        outcome = await connector.notify_and_wait(
            type=NotificationType.CONNECTOR_USER_SYNC_ERROR,
            severity=NotificationSeverity.WARNING,
            title=f"{connector.connector_name.value}: accounts Microsoft could not confirm lost access",
            message=(f"{len(pending)} linked account(s) could not be confirmed active by Microsoft for "
                     f"{UNCONFIRMED_SYNCS_BEFORE_CLOSE} syncs in a row, so they no longer have access through this "
                     "connector. They get it back on the first sync that lists them."),
            recipient_user_ids=[connector.created_by],
            suppress_repeats=False,
        )
        if outcome is NotificationOutcome.SENT:
            await _save(sync_point, log, new_state, new_state | {"pending_notice": "[]"})
        else:
            log.info(f"The notice of {len(pending)} closed account(s) was not delivered ({outcome.value}); the next sync sends it")
    return trusted


def _in_turn(users: list, cursor: str) -> list:
    """``users`` by id, starting after ``cursor`` and wrapping round."""
    users = sorted(users, key=lambda u: u.source_user_id)
    return [u for u in users if u.source_user_id > cursor] + [u for u in users if u.source_user_id <= cursor]


def _ids(raw: object) -> set[str]:
    try:
        ids = json.loads(raw or "[]")
    except (TypeError, ValueError):
        return set()
    return {str(i) for i in ids} if isinstance(ids, list) else set()


async def _save(sync_point: SyncPoint, log: Logger, before: dict, after: dict) -> None:
    empty = {"streaks": "{}", "pending_notice": "[]"}
    if all((before.get(k) or empty.get(k, "")) == (v or empty.get(k, "")) for k, v in after.items()):
        return
    try:
        await sync_point.update_sync_point(ACCOUNT_CHECK_KEY, after)
    except Exception as e:
        log.warning(f"Could not save the unconfirmed-account count: {e}")
