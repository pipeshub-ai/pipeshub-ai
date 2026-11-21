"""Which source account each address belongs to, for grants written before the address changed hands.

PipesHub keeps one user per email address, while a source keys an account by its own id. Where a source lets a
deleted account's address go to a new account, the new account has a new id: Microsoft Entra, for one, says "The new
account is assigned a new ID value even though the UPN is the same"
(https://learn.microsoft.com/en-us/troubleshoot/sharepoint/sharing-and-permissions/fix-site-user-id-mismatch).
Linked by email, the new account would read everything PipesHub granted the old one.

So before a listed account is linked (``on_new_app_users``), a PipesHub user last linked to another source id loses
this connector's grants and memberships, and its address is noted as re-used. Afterwards a grant to a re-used
address is written only when it names, by source id, the account that holds the address now; a grant that carries
only the address fails closed, since nothing in it tells the old account from the new one.
"""

from __future__ import annotations

import asyncio
import contextlib
import contextvars
import hashlib
import time
import uuid
import weakref
from typing import TYPE_CHECKING

from app.config.constants.arangodb import CollectionNames, Connectors
from app.utils.time_conversion import get_epoch_timestamp_in_ms

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable, Iterable
    from logging import Logger

    from app.config.configuration_service import ConfigurationService
    from app.connectors.core.base.data_processor.data_source_entities_processor import (
        DataSourceEntitiesProcessor,
    )
    from app.connectors.core.base.data_store.data_store import TransactionStore
    from app.models.entities import AppUser
    from app.models.permission import Permission

# Connectors whose linked users are not accounts of a source directory: a personal connector links the person who
# set it up, whoever the source account is, and these storage, database and web connectors link PipesHub's own users.
# Their source user ids may change without the address changing hands.
NOT_SOURCE_ACCOUNTS = frozenset({
    Connectors.GOOGLE_DRIVE, Connectors.GOOGLE_MAIL, Connectors.OUTLOOK_INDIVIDUAL, Connectors.NOTION_PERSONAL,
    Connectors.SLACK, Connectors.DROPBOX_PERSONAL, Connectors.GITHUB, Connectors.GITLAB_PERSONAL,
    Connectors.JIRA_PERSONAL, Connectors.JIRA_CLOUD_PERSONAL, Connectors.JIRA_DATA_CENTER_PERSONAL,
    Connectors.CONFLUENCE_DATA_CENTER_PERSONAL, Connectors.NEXTCLOUD, Connectors.KNOWLEDGE_BASE,
    Connectors.S3, Connectors.MINIO, Connectors.GCS, Connectors.AZURE_BLOB, Connectors.AZURE_FILES, Connectors.SMB,
    Connectors.CIFS, Connectors.LOCAL_FS, Connectors.LOCAL_REPO, Connectors.WEB, Connectors.RSS,
    Connectors.SNOWFLAKE, Connectors.POSTGRESQL, Connectors.MARIADB, Connectors.DEMO,
})


BUCKETS = 256     # the state is split by address so no one config value grows with the directory
# How long a loaded state is trusted before a grant decision checks it again: another instance of the connector may
# have admitted a new account for an address since (a reindex worker beside a sync).
STATE_MAX_AGE_SECONDS = 1.0


def source_accounts_key(connector_id: str, bucket: int | None = None) -> str:
    # Not a sync point: a full resync deletes those, and the grants it writes again by address are what this guards.
    base = f"/services/connectors/{connector_id}/sourceAccounts"
    return base if bucket is None else f"{base}/{bucket:02x}"


def graph_mode_key(connector_id: str) -> str:
    """``{"enabled": true}`` keeps this connector's accounts as SourceAccount nodes (``SourceAccountGraph``). Off when absent."""
    return f"{source_accounts_key(connector_id)}/graph"


def account_node_id(org_id: str, connector_id: str, source_user_id: str) -> str:
    """The SourceAccount node of (org, connector, source id): derived, so every writer names the same node without a read."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"pipeshub:sourceAccount:{org_id}:{connector_id}:{source_user_id}"))


def _bucket(address: str) -> int:
    # hashlib, not hash(): the bucket must be the same in every process.
    return hashlib.sha256(address.encode()).digest()[0] % BUCKETS


def _legacy_reused(value: dict) -> dict[str, str]:
    reused = value.get("reused") or {}
    return reused if isinstance(reused, dict) else dict.fromkeys(reused, "")   # no holder kept: fails closed


def _lower(values: dict | None) -> dict[str, str]:
    return {e.lower(): sid for e, sid in (values or {}).items()}


_SAVE_ATTEMPTS = 5


class _Conflict(Exception):
    """A bucket changed between this run's read and its save."""


class _NeedsFence(Exception):
    """An address changes hands: the admission starts again holding the grant fence."""


class GrantFence:
    """Grant writes (``shared``: from judging a grant to committing it) against an admission that removes an address's
    old account's grants (``exclusive``). The admission waits for the writes under way, which commit before it removes,
    and holds back new ones until the new holder is saved, so none commits a grant judged before the change after the
    removal. shortcut: one event loop of one process; across processes a write judged before another process admits
    the address can still commit after its removal (STATE_MAX_AGE_SECONDS plus one transaction), until a shared fence
    (a lock in the store) closes that."""

    def __init__(self) -> None:
        self._cond = asyncio.Condition()
        self._writes = 0
        self._admitting = 0        # admissions waiting or running: new writes wait behind them
        self._admission_running = False
        self._held: contextvars.ContextVar[bool] = contextvars.ContextVar(f"grant_fence_{id(self)}", default=False)

    @contextlib.asynccontextmanager
    async def shared(self) -> "AsyncIterator[None]":
        if self._held.get():       # nested in a write this task already counts
            yield
            return
        async with self._cond:
            await self._cond.wait_for(lambda: not self._admitting)
            self._writes += 1
        token = self._held.set(True)
        try:
            yield
        finally:
            self._held.reset(token)
            async with self._cond:
                self._writes -= 1
                self._cond.notify_all()

    @contextlib.asynccontextmanager
    async def exclusive(self) -> "AsyncIterator[None]":
        if self._held.get():
            raise RuntimeError("an admission cannot run inside a grant write: it would wait for itself")
        async with self._cond:
            self._admitting += 1
            try:
                await self._cond.wait_for(lambda: not self._writes and not self._admission_running)
            except BaseException:
                self._admitting -= 1
                self._cond.notify_all()
                raise
            self._admission_running = True
        try:
            yield
        finally:
            async with self._cond:
                self._admission_running = False
                self._admitting -= 1
                self._cond.notify_all()


_FENCES: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, GrantFence]" = weakref.WeakKeyDictionary()


def grant_fence() -> GrantFence:
    """This event loop's fence (an asyncio primitive serves one loop)."""
    loop = asyncio.get_running_loop()
    fence = _FENCES.get(loop)
    if fence is None:
        fence = _FENCES[loop] = GrantFence()
    return fence


def _mapping(value: object, connector_id: str) -> dict:
    if not isinstance(value, dict):
        raise TypeError(f"the saved account state of connector {connector_id} is not a mapping")
    return value


def _account_id(source_user_id: str | None) -> str | None:
    # ``creator_<id>`` is the stand-in id connector_service links a connector's creator with, not a source account.
    return source_user_id if source_user_id and not source_user_id.startswith("creator_") else None


class SourceAccounts:
    """Per address, in ``BUCKETS`` config values by address hash: ``linked``, the source id this connector last linked
    the address to (it stays after the account is withdrawn); ``reused``, for an address another account held before,
    the source id that holds it now; and ``deleted``, a deleted account the source still lists with the address,
    kept until a listing shows that address. A run reads and writes only the buckets of the addresses it lists.

    The value at the base key (the marker) says whether the stored links were noted once (``seeded``), whether the
    source's deleted accounts were read (``deletedChecked``, absent for a connector that reads none) and a
    ``version`` that every change replaces, so a loaded copy older than STATE_MAX_AGE_SECONDS is read again only when
    something changed."""

    def __init__(
        self, processor: "DataSourceEntitiesProcessor", connector_id: str, config_service: "ConfigurationService",
        logger: "Logger", deleted_users: "Callable[[], Awaitable[dict[str, str]]] | None" = None,
    ) -> None:
        self.processor = processor
        self.connector_id = connector_id
        self.config_service = config_service
        self.logger = logger
        # Optional: address -> source id of each deleted account the source still lists (Microsoft's deleted items),
        # read once to find addresses an earlier version linked to their new account before this state was kept.
        self.deleted_users = deleted_users
        self.loaded = False                   # every bucket read; until a bucket is read, no grant to it is written
        self.loaded_at = 0.0                  # when the marker was last checked
        self.admission_failed = False         # the stored links could not be read this run: no grant is written
        self.deleted_pending = False          # deleted accounts not read yet: an address may still be a moved one
        self._version: object = None
        self._read: set[int] = set()          # buckets loaded since the marker last changed
        self._epoch = 0                       # bumped when the marker changes: a load begun before marks nothing read
        self._linked: dict[int, dict[str, str]] = {}
        self._reused: dict[int, dict[str, str]] = {}
        self._legacy: dict[str, str] = {}     # re-used addresses an earlier version saved in one value
        self._stored: dict[int, dict | None] = {}     # each bucket's value as read, for the compare-and-set
        self._deleted: dict[int, dict[str, str]] = {}  # deleted-account evidence no listing has reconciled yet
        # One read-update-save at a time per connector in this process (a connector may list users in parallel tasks);
        # across processes each bucket is saved by compare-and-set and the run starts over on a conflict.
        self._lock = asyncio.Lock()

    # Flat views, for reading and for tests that set a state directly.
    @property
    def current(self) -> dict[str, str]:
        return {e: sid for part in self._linked.values() for e, sid in part.items()}

    @current.setter
    def current(self, value: dict[str, str]) -> None:
        self._linked = {}
        for e, sid in value.items():
            self._linked.setdefault(_bucket(e.lower()), {})[e.lower()] = sid

    @property
    def reused(self) -> dict[str, str]:
        return {e: sid for part in self._reused.values() for e, sid in part.items()}

    @reused.setter
    def reused(self, value: dict[str, str]) -> None:
        self._reused = {}
        for e, sid in value.items():
            self._reused.setdefault(_bucket(e.lower()), {})[e.lower()] = sid

    async def _get(self, key: str) -> dict:
        """A saved value; a failed read raises, and so does one that is not a mapping: no grant may be judged."""
        return _mapping(await self.config_service.get_config(key, use_cache=False, raise_on_error=True) or {},
                        self.connector_id)

    async def _load(self, buckets: Iterable[int]) -> dict[int, dict[str, str]]:
        """Read these buckets; returns each one's kept deleted-account evidence."""
        deleted: dict[int, dict[str, str]] = {}
        epoch = self._epoch

        async def one(bucket: int) -> None:
            raw = await self.config_service.get_config(
                source_accounts_key(self.connector_id, bucket), use_cache=False, raise_on_error=True)
            stored = _mapping(raw or {}, self.connector_id)
            self._stored[bucket] = raw or None
            self._linked[bucket], self._reused[bucket] = _lower(stored.get("linked")), _lower(stored.get("reused"))
            deleted[bucket] = self._deleted[bucket] = _lower(stored.get("deleted"))
            if self._epoch == epoch:   # else read before a change this process has seen: read again when wanted
                self._read.add(bucket)
        await asyncio.gather(*(one(b) for b in set(buckets)))
        return deleted

    def _take_marker(self, marker: dict, fresh: Iterable[int] = ()) -> None:
        # ``saving``: a save that may have stopped between its buckets and its marker, so no loaded copy is current.
        version = object() if marker.get("saving") else marker.get("version")
        if version != self._version:
            # Some bucket changed: only those read after this marker (``fresh``) are current; the rest are read again.
            self._read &= set(fresh)
            self.loaded = len(self._read) == BUCKETS
            self._epoch += 1
        self._version = version
        self.deleted_pending = marker.get("deletedChecked") is False
        self._legacy = {} if marker.get("seeded") else _lower(_legacy_reused(marker))
        self.loaded_at = time.monotonic()

    async def ensure_loaded(self, emails: Iterable[str] | None = None) -> None:
        """Read the buckets of ``emails`` (every bucket when None) before a grant to them is judged in any path (a
        sync, a reindex, a webhook), and read again what is loaded once it is older than STATE_MAX_AGE_SECONDS and
        has changed since. A failed read raises, and so does a run whose stored links could not be read, so the
        caller writes nothing and keeps what is stored."""
        if self.admission_failed:
            raise RuntimeError(f"the stored users of connector {self.connector_id} could not be read this run")
        wanted = set(range(BUCKETS)) if emails is None else {_bucket(e.lower()) for e in emails}
        if not self.loaded_at or time.monotonic() - self.loaded_at > STATE_MAX_AGE_SECONDS:
            self._take_marker(await self._get(source_accounts_key(self.connector_id)))
        if self.loaded:
            return
        missing = wanted - self._read
        if missing:
            async with self._lock:
                await self._load(missing)
        self.loaded = len(self._read) == BUCKETS

    async def recheck(self, permissions: list["Permission"]) -> list["Permission"]:
        """``permissions`` without the user grants the saved state, read again now, refuses: for a write that comes a
        while after its grants were judged (a reindex batch), so an account admitted meanwhile is not handed them."""
        if self.admission_failed:
            raise RuntimeError(f"the stored users of connector {self.connector_id} could not be read this run")
        users = [p for p in permissions if getattr(p.entity_type, "value", p.entity_type) == "USER" and p.email]
        async with self._lock:
            self._take_marker(await self._get(source_accounts_key(self.connector_id)))
            await self._load({_bucket(p.email.lower()) for p in users})
        # Not may_grant: a SharePoint principal's grant carries its site user id, which never equals the Entra object
        # id the address is linked to. What matters here is an admission made meanwhile, which re-used addresses and
        # deleted-account evidence record.
        refused = {id(p) for p in users if not self.admits(p.email, p.external_id)}
        return [p for p in permissions if id(p) not in refused]

    async def _save(self, buckets: Iterable[int], deleted: dict[int, dict[str, str]]) -> None:
        """Save each bucket only if it still holds what this run read (another process may have saved it since)."""
        for bucket in sorted(set(buckets)):
            value = {"linked": dict(self._linked.get(bucket, {})), "reused": dict(self._reused.get(bucket, {}))}
            if deleted.get(bucket):
                value["deleted"] = dict(deleted[bucket])
            if not await self.config_service.compare_and_set_config(
                source_accounts_key(self.connector_id, bucket), self._stored.get(bucket), value,
            ):
                raise _Conflict(bucket)
            self._stored[bucket] = value

    async def _read_deleted(self) -> dict[str, str] | None:
        """The source's deleted accounts by address, or None when the read failed (tried again next run)."""
        try:
            return _lower(await self.deleted_users())
        except Exception as e:
            self.logger.warning(f"Could not read the deleted users of connector {self.connector_id}; trying again next run: {e}")
            return None

    async def _update_marker(
        self, *, changed: bool, deleted_checked: bool | None, fresh: Iterable[int], saving: bool = False,
        seeded: bool = True,
    ) -> None:
        """Merge this run into the marker by compare-and-set against what is stored now, not what the run read at its
        start: another replica may have written a newer version meanwhile, and writing an older one back would stop
        other processes from reloading. A change writes a new version; otherwise the stored one stands. The flags
        only ever turn on. ``saving`` flags the marker before a run saves its buckets: if the save after them fails,
        the next run still writes a new version, though the buckets then already hold what it would save."""
        key = source_accounts_key(self.connector_id)
        for _ in range(_SAVE_ATTEMPTS):
            raw = await self.config_service.get_config(key, use_cache=False, raise_on_error=True)
            stored = _mapping(raw or {}, self.connector_id)
            # A token, not a counter: two processes bumping one counter would write one number for different changes.
            if saving:
                noted = {**stored, "version": uuid.uuid4().hex, "saving": True}
            else:
                # shortcut: another run's final save clears a flag set by a run still saving; that run's own final save
                # then publishes, so only both failing at once is missed. Key the flag by run if that shows up.
                # Not ``seeded``: an earlier version's single-value fields stay until a listing moves them to buckets.
                kept = {"seeded": True} if seeded else {k: v for k, v in stored.items() if k != "saving"}
                noted = {**kept,
                         "version": uuid.uuid4().hex if changed or stored.get("saving") else stored.get("version")}
                if deleted_checked is not None or "deletedChecked" in stored:
                    noted["deletedChecked"] = bool(stored.get("deletedChecked")) or bool(deleted_checked)
            if noted == stored or await self.config_service.compare_and_set_config(key, raw or None, noted):
                self._take_marker(noted, fresh)
                return
        raise RuntimeError(f"The account state of connector {self.connector_id} kept changing while it was saved")

    async def note_withdrawn(self, users: list) -> None:
        """Before ``users`` are withdrawn, note the account each one is linked to by its address, for a run that
        withdraws without listing first (an incremental delete, a listing with nobody active): a new account taking
        the address later is then still compared with it. A failed read or save raises, so nobody is withdrawn
        unnoted."""
        gone = {u.id: sid for u in users if u.id and (sid := _account_id(getattr(u, "source_user_id", None)))}
        if not gone:
            return
        by_user = {link.id: getattr(link, "email", None) for link in await self.processor.get_app_user_links(self.connector_id)}
        noted = {email.lower(): sid for u in users if (sid := gone.get(u.id))
                 and (email := getattr(u, "email", None) or by_user.get(u.id))}
        if not noted:
            return
        async with self._lock:
            buckets = {_bucket(e) for e in noted}
            for _ in range(_SAVE_ATTEMPTS):
                deleted = await self._load(buckets)
                changed = set()
                for email, sid in noted.items():
                    if self._linked.setdefault(_bucket(email), {}).setdefault(email, sid) == sid:
                        changed.add(_bucket(email))
                if changed:
                    await self._update_marker(changed=True, deleted_checked=None, fresh=buckets, saving=True)
                try:
                    await self._save(changed, deleted)
                except _Conflict:
                    continue
                if changed:
                    await self._update_marker(changed=True, deleted_checked=None, fresh=buckets, seeded=False)
                return
            raise RuntimeError(f"The account state of connector {self.connector_id} kept changing while it was saved")

    @property
    def lock(self) -> asyncio.Lock:
        """The connector's one read-update-save at a time in this process; the SourceAccount writer takes it too."""
        return self._lock

    async def before_link(
        self, users: list["AppUser"], write: "Callable[[], Awaitable[None]] | None" = None,
    ) -> bool:
        """Before ``users`` are linked, take this connector's grants from any PipesHub user whose address now belongs
        to a different account than the one it was linked to before. False when the stored links could not be read:
        then nobody is linked and no grant is written this run (``ensure_loaded`` raises). ``write`` (the link write)
        runs after a successful check under the same lock, so no other listing of the connector runs in between."""
        try:
            return await self._admit(users, fenced=False, write=write)
        except _NeedsFence:
            # Taken before this connector's lock, as grant writes take them: a write holding the fence may be waiting
            # for the lock to load a bucket.
            async with grant_fence().exclusive():
                return await self._admit(users, fenced=True, write=write)

    async def _admit(
        self, users: list["AppUser"], *, fenced: bool, write: "Callable[[], Awaitable[None]] | None",
    ) -> bool:
        async with self._lock:
            for _ in range(_SAVE_ATTEMPTS):
                try:
                    admitted = await self._before_link(users, fenced=fenced)
                    # ``write`` (the link itself) runs under the same lock: another admission for one of these addresses
                    # cannot link a different account between this check and that write.
                    # shortcut: one process; across processes the later link write can still name the older account.
                    if admitted and write is not None:
                        await write()
                    return admitted
                except _Conflict as conflict:
                    # Another process saved this bucket meanwhile: read it again and decide again (removing a
                    # grant twice is harmless).
                    self.logger.info("The account state of connector %s changed while it was saved (bucket %s); "
                                     "reading it again", self.connector_id, conflict.args[0])
                    continue
            raise RuntimeError(f"The account state of connector {self.connector_id} kept changing while it was saved")

    async def _before_link(self, users: list["AppUser"], *, fenced: bool) -> bool:
        try:
            marker = await self._get(source_accounts_key(self.connector_id))
            links = await self.processor.get_app_user_links(self.connector_id)
        except Exception as e:
            self.admission_failed = True
            self.logger.error(f"Could not read the stored users; no user is linked and no grant written this run: {e}")
            return False
        self.admission_failed = False
        by_address = {link.email.lower(): sid for link in links
                      if getattr(link, "email", None) and (sid := _account_id(link.source_user_id))}
        seeded = bool(marker.get("seeded"))
        read_deleted = None
        if self.deleted_users is not None and not marker.get("deletedChecked"):
            read_deleted = await self._read_deleted()
        # What an earlier version saved in one value: its re-used addresses, deleted evidence and withdrawn accounts.
        legacy = {} if seeded else {"reused": _lower(_legacy_reused(marker)), "deleted": _lower(marker.get("deleted")),
                                    "linked": _lower(marker.get("withdrawnByAddress"))}
        new_deleted = {**legacy.get("deleted", {}), **(read_deleted or {})}
        listed = [(account, u.email.lower()) for u in users if u.email and (account := _account_id(u.source_user_id))]
        touched = {_bucket(email) for _, email in listed} | {_bucket(e) for e in new_deleted}
        if not seeded:
            touched |= {_bucket(e) for part in (by_address, legacy["reused"], legacy["linked"]) for e in part}
        deleted = await self._load(touched)     # fresh: another process may have written
        changed: set[int] = set()

        def note(part: dict[int, dict[str, str]], email: str, sid: str, *, keep: bool = True) -> None:
            entries = part.setdefault(_bucket(email), {})
            if entries.get(email) != sid and not (keep and email in entries):
                entries[email] = sid
                changed.add(_bucket(email))

        if not seeded:
            # Once: note the account every stored link names (and the withdrawn ones an earlier version kept), so an
            # address whose account is withdrawn before it is listed again is still compared when a new one takes it.
            for email, sid in {**legacy["linked"], **by_address}.items():
                note(self._linked, email, sid)
            for email, sid in legacy["reused"].items():
                note(self._reused, email, sid)
        for email, sid in new_deleted.items():
            note(deleted, email, sid)
        for account, email in listed:
            bucket = _bucket(email)
            linked, reused = self._linked.setdefault(bucket, {}), self._reused.setdefault(bucket, {})
            old = deleted.get(bucket, {}).pop(email, None)       # the listing reconciles the deleted evidence
            if old is not None:
                changed.add(bucket)
            # Each address is compared with the accounts it was linked to: the one this connector remembers, the one
            # its stored link names (another writer may have rewritten it already), and a deleted account the source
            # still lists with it. The listed id being linked to some other address says nothing about this one.
            before = {linked.get(email), by_address.get(email), None if email in reused else old}
            if before - {None, account} and not fenced:
                raise _NeedsFence
            # shortcut: two live accounts listed with one address (Jira Data Center and Salesforce allow it) read as
            # a change of hands on every run, so the address keeps only the last-linked account's grants and they are
            # removed and written again each run; keying PipesHub users by source id would tell them apart.
            if before - {None, account}:
                removed = await self.processor.remove_user_access_to_connector(self.connector_id, email)
                reused[email] = account
                changed.add(bucket)
                self.logger.info(
                    "An address of connector %s belongs to a new account now; removed %d grant(s) of the previous one",
                    self.connector_id, removed,
                )
            # else, nothing remembered: shortcut: a change of hands made before this state existed, with the link
            # already naming the new account, is caught only where ``deleted_users`` lists the old account.
            if linked.get(email) != account:
                linked[email] = account
                changed.add(bucket)
        if changed:
            await self._update_marker(changed=True, deleted_checked=None, fresh=touched, saving=True)
        await self._save(changed, deleted)
        await self._update_marker(
            changed=bool(changed),
            deleted_checked=None if self.deleted_users is None else read_deleted is not None,
            fresh=touched,
        )
        return True

    def may_grant(self, email: str, object_id: str | None) -> bool:
        """Whether a grant to ``email`` reaches the account that holds it now. ``object_id`` is the source id the
        grant named, or None when it named none. False until the saved state is read (``ensure_loaded``). For a
        connector whose grants name accounts by the ids it lists users with (Microsoft Graph); the processor checks
        every connector's grants to re-used addresses (``holds``)."""
        email = email.lower()
        bucket = _bucket(email)
        if (not self.loaded and bucket not in self._read) or (self.admission_failed and object_id is not None):
            return False
        if self._holder(email) is not None:
            return self.holds(email, object_id)
        if not self._evidence_allows(email, object_id):
            return False
        if object_id is None and self.deleted_pending:
            # shortcut: until the source's deleted accounts are read, any address may be one an earlier version moved
            # to a new account, so a grant naming no account (a SharePoint principal) waits; one naming its account
            # is judged by id. Narrow to addresses linked before this state once that read is known to fail for good.
            return False
        linked = self._linked.get(bucket, {}).get(email)
        if object_id is not None and linked is not None:
            return linked == object_id
        return True

    def holds(self, email: str, source_id: str | None) -> bool:
        """For a loaded state: whether a grant naming ``email`` and ``source_id`` reaches the account holding the
        address. Only a re-used address is checked; there the grant must name the current account by id."""
        holder = self._holder(email.lower())
        return holder is None or (source_id is not None and source_id == holder)

    def admits(self, email: str, source_id: str | None) -> bool:
        """For a loaded state: ``holds``, and an address with unreconciled deleted-account evidence takes only a grant
        naming the account it is linked to (``_evidence_allows``)."""
        return self.holds(email, source_id) and self._evidence_allows(email.lower(), source_id)

    def _evidence_allows(self, email: str, source_id: str | None) -> bool:
        """An address a deleted account still holds in the source's deleted accounts, and that no listing has shown
        since, takes only a grant naming the account it is linked to: not the deleted one, not one naming nobody."""
        deleted = self._deleted.get(_bucket(email), {})
        if email not in deleted or email in self._reused.get(_bucket(email), {}):
            return True
        linked = self._linked.get(_bucket(email), {}).get(email)
        # The linked account may be the deleted one itself (not listed again yet): refused until a listing shows it.
        return source_id is not None and source_id == linked and source_id != deleted[email]

    def _holder(self, email: str) -> str | None:
        part = self._reused.get(_bucket(email), {})
        return part[email] if email in part else self._legacy.get(email)


ACCOUNTS = CollectionNames.SOURCE_ACCOUNTS.value
LINKS = CollectionNames.AUTHENTICATED_AS.value
DIRECTORY = "directory"           # a link made because the account's address is the user's (``relink``)
AUTHENTICATOR = "authenticator"   # a link made because the user signed the connector in as the account


class SourceAccountGraph:
    """A connector's source accounts as SourceAccount nodes, one per (connector, source id), linked to the PipesHub user
    that holds the address by an ``authenticatedAs`` edge (``via``: ``directory``). The id is the one ``SourceAccounts``
    keys its addresses by (``_account_id``), so the registry and the graph name each account alike.

    An address that two live accounts of the connector share links nobody: nothing in a sign-in tells which of them is
    the person. A grant naming a known account by its source id is written on the account (``principal``)."""

    def __init__(self, connector_id: str, org_id: str, logger: "Logger") -> None:
        self.connector_id = connector_id
        self.org_id = org_id
        self.logger = logger
        self._known: set[str] = set()     # account node ids read or written by this process

    def node_id(self, source_user_id: str | None) -> str | None:
        sid = _account_id(source_user_id)
        return account_node_id(self.org_id, self.connector_id, sid) if sid else None

    async def principal(self, tx_store: "TransactionStore", source_user_id: str | None) -> tuple[str, str] | None:
        """The account node a grant naming ``source_user_id`` is written on, or None when no such account is stored (the
        caller then resolves the grant as before). A failed read raises: a replace would otherwise drop the grant."""
        node = self.node_id(source_user_id)
        if node is None:
            return None
        if node not in self._known:
            if not await self.stored(tx_store, [node]):
                return None
            self._known.add(node)
        return node, ACCOUNTS

    async def holding(self, tx_store: "TransactionStore", address: str) -> list[str]:
        """The keys of this connector's accounts (any state) whose address is ``address``: what a grant or a membership revoked by
        address was written on. A failed read raises."""
        return [k for a in await tx_store.get_nodes_by_field_in(ACCOUNTS, "email", [address.lower()], raise_on_error=True)
                if (k := a.get("_key") or a.get("id")) == self.node_id(a.get("sourceUserId"))]

    @staticmethod
    async def stored(tx_store: "TransactionStore", nodes: list[str]) -> list[dict]:
        """The stored account documents among ``nodes``. A failed read raises."""
        return await tx_store.get_nodes_by_field_in(ACCOUNTS, "_key", nodes, raise_on_error=True)

    async def upsert(self, tx_store: "TransactionStore", users: list["AppUser"], *, state: str = "listed") -> None:
        """Write the accounts of ``users`` (an id and an address each) in ``state``, keeping each one's earlier addresses,
        then link the addresses they had and have now."""
        now = get_epoch_timestamp_in_ms()
        wanted = {}
        for u in users:
            node = self.node_id(u.source_user_id)
            if node and u.email:
                wanted[node] = u
        if not wanted:
            return
        stored = {d.get("_key") or d.get("id"): d for d in await self.stored(tx_store, list(wanted))}
        docs, addresses = [], set()
        for node, u in wanted.items():
            old = stored.get(node) or {}
            email = u.email.lower()
            previous = list(old.get("previousEmails") or [])
            if old.get("email") and old["email"] != email and old["email"] not in previous:
                previous.append(old["email"])
            addresses |= {email, *previous}
            docs.append({
                "id": node, "orgId": self.org_id, "connectorId": self.connector_id, "sourceUserId": _account_id(u.source_user_id),
                "email": email, "previousEmails": previous, "fullName": u.full_name or None, "isGuest": bool(u.is_guest),
                "state": state, "createdAtTimestamp": old.get("createdAtTimestamp") or now, "updatedAtTimestamp": now,
            })
        await tx_store.batch_upsert_nodes(docs, ACCOUNTS)
        # not added to _known here: the transaction may still roll back, and principal() then has to read the store again
        await self.relink(tx_store, sorted(addresses))

    async def relink(self, tx_store: "TransactionStore", addresses: list[str]) -> None:
        """Link each address's PipesHub user to the one live account of this connector with that address, and to nothing
        when there is none or several, or when the user is deactivated; every other directory link of that user in this connector
        goes. The store makes the choice under locks (``set_directory_link``), so a sync and a user event cannot race it."""
        if not addresses:
            return
        accounts = [a for a in await tx_store.get_nodes_by_field_in(ACCOUNTS, "email", addresses, raise_on_error=True)
                    # the id is derived from (org, connector, source id), so a match is this connector's account in this org
                    if (a.get("_key") or a.get("id")) == self.node_id(a.get("sourceUserId")) and a.get("state") != "withdrawn"]
        users = await tx_store.get_users_by_emails(addresses, raise_on_error=True, org_id=self.org_id)
        for address in addresses:
            user = users.get(address)
            if user is None:
                continue
            if sum(a.get("email") == address for a in accounts) > 1:
                self.logger.warning(
                    "Connector %s lists several accounts with one address; their PipesHub user is linked to none of them", self.connector_id,
                )
            await tx_store.set_directory_link(user.id, self.connector_id, address, self.org_id)

    async def withdraw(self, tx_store: "TransactionStore", users: list["AppUser"]) -> None:
        """Mark the stored accounts of ``users`` withdrawn (kept: they hold the grants, and the addresses they had), then
        link their addresses again: a withdrawn account links nobody."""
        nodes = [n for n in (self.node_id(getattr(u, "source_user_id", None)) for u in users) if n]
        if not nodes:
            return
        stored = await self.stored(tx_store, nodes)
        if not stored:
            return
        now = get_epoch_timestamp_in_ms()
        await tx_store.batch_upsert_nodes(
            [{"id": d.get("_key") or d.get("id"), "state": "withdrawn", "updatedAtTimestamp": now} for d in stored], ACCOUNTS)
        await self.relink(tx_store, sorted({e for d in stored for e in (d.get("email"), *(d.get("previousEmails") or [])) if e}))
