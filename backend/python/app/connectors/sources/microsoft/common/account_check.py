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
from app.connectors.sources.microsoft.common.msgraph_client import EmptyGraphAnswer
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
            if not isinstance(e, (ODataError, EmptyGraphAnswer)):
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
    still_unconfirmed = {u.source_user_id for u in unlisted} - confirmed
    # An owed notice for an account this listing shows enabled again is dropped: its link is already back.
    pending = (_ids(saved_state.get("pending_notice")) - (listed - disabled)) | {u.source_user_id for u in closed}
    new_state = {
        # Closed accounts keep counting until their withdrawal commits; the next sync no longer finds them linked.
        "streaks": json.dumps({k: v for k, v in streaks.items() if k in still_unconfirmed}, sort_keys=True),
        "cursor": next((u.source_user_id for u in reversed(asked) if u.source_user_id in rest_ids), cursor),
        "counting_cursor": next((u.source_user_id for u in reversed(asked) if u.source_user_id not in rest_ids), counting_cursor),
        "pending_notice": json.dumps(sorted(pending)),
    }
    # Saved before the withdrawal: the owner's notice of a close stays owed until it is sent.
    if state is not None and not await _save(sync_point, log, saved_state, new_state) and closed:
        # Without the saved notice a close would go untold if the notice then failed; this sync closes nobody.
        log.error(f"Could not save the owed notice; {len(closed)} account(s) past the grace keep access this sync")
        closed = []
        pending = _ids(saved_state.get("pending_notice")) - (listed - disabled)
    lost = gone + closed
    if lost:
        # The link is the connector's only gate for these connectors, so the user's own grants stay: an account restored
        # or confirmed again is linked by the next listing and has its access back at once.
        await processor.withdraw_app_user_access(connector.connector_id, lost)
    if closed:
        asked_ids = {u.source_user_id for u in asked}
        read_gone = sum(u.source_user_id in asked_ids for u in gone)
        log.error(f"The user listing is unverified past the grace: {len(closed) + read_gone} of {len(asked)} linked "
                  f"people asked about this run were gone or not confirmed active and lose the app link ({len(stored)} linked)")
        if len(gone) > read_gone:
            log.info(f"{len(gone) - read_gone} more linked account(s) the listing showed disabled lose the app link")
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


async def _save(sync_point: SyncPoint, log: Logger, before: dict, after: dict) -> bool:
    """Whether ``after`` is what the sync point holds now."""
    empty = {"streaks": "{}", "pending_notice": "[]"}
    if all((before.get(k) or empty.get(k, "")) == (v or empty.get(k, "")) for k, v in after.items()):
        return True
    try:
        await sync_point.update_sync_point(ACCOUNT_CHECK_KEY, after)
    except Exception as e:
        log.warning(f"Could not save the unconfirmed-account count: {e}")
        return False
    return True


GROUP_CHECK_KEY = "unread-group-members"


class GroupMemberGrace:
    """The same bound for a group whose member read Microsoft refuses: its stored members keep the group's access for
    ``UNCONFIRMED_SYNCS_BEFORE_CLOSE`` syncs in a row, then lose it. A refusal that lasts (a 403 on a group whose
    membership is hidden, which needs ``Member.Read.Hidden``,
    https://learn.microsoft.com/en-us/graph/api/group-list-transitivemembers) would otherwise let a person removed
    from the group keep it for good. A read that succeeds starts the count again and writes the members back; a read
    that never reached Microsoft keeps the members and is not counted; a sync that does not read the group leaves
    its count as it is. Every group read is attempted each sync (no read budget), so the count needs no turn order.

    The owner's notice of a close is owed from the moment the close is decided (saved before the empty write) until
    the notification service takes it, as for accounts.

    ``load`` once per sync, ``read`` or ``refused`` per group read, ``finish`` at the end. Before ``load`` it closes
    nothing and counts nothing."""

    def __init__(self, connector: "BaseConnector", sync_point: SyncPoint) -> None:
        self.connector, self.sync_point = connector, sync_point
        self.streaks: dict[str, int] | None = None
        self.pending: set[str] = set()
        self.saved: dict[str, str] = {}
        self.closed: list[str] = []
        self.counted: set[str] = set()
        self.blocked = False

    async def load(self) -> None:
        self.closed, self.counted, self.blocked = [], set(), False
        try:
            state = await self.sync_point.read_sync_point(GROUP_CHECK_KEY) or {}
        except Exception as e:
            # Without the count no group can pass the grace this sync; the reads still write what they get.
            self.connector.logger.warning(f"Could not read the unread-group-member count; no group loses its members by it this sync: {e}")
            self.streaks = None
            return
        try:
            saved = json.loads(state.get("streaks") or "{}")
        except (TypeError, ValueError):
            saved = {}
        self.streaks = {k: v for k, v in saved.items() if isinstance(v, int)} if isinstance(saved, dict) else {}
        self.pending = _ids(state.get("pending_notice"))
        self.saved = self._state()

    def read(self, group_id: str) -> None:
        if self.streaks is not None:
            self.streaks.pop(group_id, None)
            # Its members are written back, so an owed notice of its close would no longer be true.
            self.pending.discard(group_id)

    def closed_ids(self) -> list[str]:
        """The groups already past the grace that this sync has not read yet."""
        return sorted(k for k, v in (self.streaks or {}).items() if v >= UNCONFIRMED_SYNCS_BEFORE_CLOSE and k not in self.counted)

    async def refused(self, group_id: str, error: BaseException) -> bool:
        """Count a failed member read. True when the group's stored members must lose its access now."""
        if self.streaks is None or not isinstance(error, (ODataError, EmptyGraphAnswer)):
            return False
        if group_id not in self.counted:  # a group read twice in one sync counts once
            self.counted.add(group_id)
            self.streaks[group_id] = self.streaks.get(group_id, 0) + 1
        n = self.streaks[group_id]
        if n == UNCONFIRMED_SYNCS_BEFORE_CLOSE and group_id not in self.closed:
            owed_before = group_id in self.pending
            self.pending.add(group_id)
            # The notice is owed before the caller writes the group empty. Without it saved a close would go untold if
            # the notice then failed, so this sync closes no group, and this one stays one sync short of the bound.
            if self.blocked or not await self._save():
                if not self.blocked:
                    self.connector.logger.error("Could not save the owed group notice; groups past the grace keep their members this sync")
                self.blocked = True
                if not owed_before:
                    self.pending.discard(group_id)
                self.streaks[group_id] = n - 1
                return False
            self.closed.append(group_id)
        return n >= UNCONFIRMED_SYNCS_BEFORE_CLOSE

    async def finish(self) -> None:
        if self.streaks is None:
            return
        log = self.connector.logger
        counting = {k: v for k, v in self.streaks.items() if v < UNCONFIRMED_SYNCS_BEFORE_CLOSE}
        if counting:
            log.warning(f"{len(counting)} group(s) keep their stored members while Microsoft refuses the member read "
                        f"(longest {max(counting.values())} of {UNCONFIRMED_SYNCS_BEFORE_CLOSE} syncs)")
        if self.closed:
            log.error(f"The group members are unverified past the grace: {len(self.closed)} group(s) whose member read "
                      f"Microsoft refused for {UNCONFIRMED_SYNCS_BEFORE_CLOSE} syncs in a row lose their stored members' access")
        if self.pending:
            # Not suppressed as a repeat: each close is sent once, through ``pending_notice``.
            outcome = await self.connector.notify_and_wait(
                type=NotificationType.CONNECTOR_GROUP_SYNC_ERROR,
                severity=NotificationSeverity.WARNING,
                title=f"{self.connector.connector_name.value}: groups Microsoft would not list lost their members",
                message=(f"Microsoft refused to list the members of {len(self.pending)} group(s) for "
                         f"{UNCONFIRMED_SYNCS_BEFORE_CLOSE} syncs in a row, so their members no longer have access "
                         "through these groups. It comes back on the first sync that can read the members."),
                recipient_user_ids=[self.connector.created_by],
                suppress_repeats=False,
            )
            if outcome is NotificationOutcome.SENT:
                self.pending = set()
            else:
                log.info(f"The notice of {len(self.pending)} closed group(s) was not delivered ({outcome.value}); the next sync sends it")
        await self._save()

    def _state(self) -> dict[str, str]:
        return {"streaks": json.dumps(self.streaks or {}, sort_keys=True), "pending_notice": json.dumps(sorted(self.pending))}

    async def _save(self) -> bool:
        """Whether the sync point holds this state now."""
        state = self._state()
        if state == self.saved:
            return True
        try:
            await self.sync_point.update_sync_point(GROUP_CHECK_KEY, state)
        except Exception as e:
            self.connector.logger.warning(f"Could not save the unread-group-member count: {e}")
            return False
        self.saved = state
        return True
