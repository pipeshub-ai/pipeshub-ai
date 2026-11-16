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
import hashlib
from types import SimpleNamespace
from typing import TYPE_CHECKING

from app.config.constants.arangodb import Connectors

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Iterable
    from logging import Logger

    from app.config.configuration_service import ConfigurationService
    from app.connectors.core.base.data_processor.data_source_entities_processor import (
        DataSourceEntitiesProcessor,
    )
    from app.models.entities import AppUser

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


def source_accounts_key(connector_id: str, bucket: int | None = None) -> str:
    # Not a sync point: a full resync deletes those, and the grants it writes again by address are what this guards.
    base = f"/services/connectors/{connector_id}/sourceAccounts"
    return base if bucket is None else f"{base}/{bucket:02x}"


def _bucket(address: str) -> int:
    # hashlib, not hash(): the bucket must be the same in every process.
    return hashlib.sha1(address.encode()).digest()[0] % BUCKETS


def _legacy_reused(value: dict) -> dict[str, str]:
    reused = value.get("reused") or {}
    return reused if isinstance(reused, dict) else dict.fromkeys(reused, "")   # no holder kept: fails closed


def _mapping(value: object, connector_id: str) -> dict:
    if not isinstance(value, dict):
        raise TypeError(f"the saved account state of connector {connector_id} is not a mapping")
    return value


def _account_id(source_user_id: str | None) -> str | None:
    # ``creator_<id>`` is the stand-in id connector_service links a connector's creator with, not a source account.
    return source_user_id if source_user_id and not source_user_id.startswith("creator_") else None


class SourceAccounts:
    """Per address, the source id this connector last linked it to (``linked``) and, for an address another account
    held before, the source id that holds it now (``reused``). Kept in ``BUCKETS`` config values by address hash:
    a run reads and writes only the buckets of the addresses it lists."""

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
        self.admission_failed = False         # the stored links could not be read this run: no grant is written
        self._read: set[int] = set()
        self._linked: dict[int, dict[str, str]] = {}
        self._reused: dict[int, dict[str, str]] = {}
        self._other: dict[int, dict[str, list[str]]] = {}
        self._legacy: dict[str, str] = {}     # re-used addresses an earlier version saved in one value

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

    async def _load(self, buckets: Iterable[int]) -> None:
        """Read these buckets. A failed read raises: without the state no grant may be judged."""
        async def one(bucket: int) -> None:
            stored = _mapping(await self.config_service.get_config(
                source_accounts_key(self.connector_id, bucket), use_cache=False, raise_on_error=True
            ) or {}, self.connector_id)
            self._linked[bucket] = {e.lower(): sid for e, sid in (stored.get("linked") or {}).items()}
            self._reused[bucket] = {e.lower(): sid for e, sid in (stored.get("reused") or {}).items()}
            self._other[bucket] = {e.lower(): ids for e, ids in (stored.get("other") or {}).items()}
            self._read.add(bucket)
        await asyncio.gather(*(one(b) for b in set(buckets)))

    async def ensure_loaded(self, emails: Iterable[str] | None = None) -> None:
        """Read, once, the buckets of ``emails`` (every bucket when None) before a grant to them is judged in any
        path (a sync, a reindex, a webhook). A failed read raises, and so does a run whose stored links could not be
        read, so the caller writes nothing and keeps what is stored."""
        if self.admission_failed:
            raise RuntimeError(f"the stored users of connector {self.connector_id} could not be read this run")
        if self.loaded:
            return
        wanted = set(range(BUCKETS)) if emails is None else {_bucket(e.lower()) for e in emails}
        if not self._read:
            await self._load_legacy()
        await self._load(wanted - self._read)
        self.loaded = self.loaded or len(self._read) == BUCKETS

    async def _load_legacy(self) -> None:
        """Re-used addresses an earlier version saved in one value, until the next user sync moves them (``before_link``)."""
        marker = await self._marker()
        self._legacy = {e.lower(): sid for e, sid in _legacy_reused(marker).items()}

    async def _save(self, buckets: Iterable[int]) -> None:
        for bucket in sorted(set(buckets)):
            saved = await self.config_service.set_config(
                source_accounts_key(self.connector_id, bucket),
                {"linked": self._linked.get(bucket, {}), "reused": self._reused.get(bucket, {}),
                 "other": self._other.get(bucket, {})},
            )
            if not saved:
                raise RuntimeError(f"Could not save which accounts the users of connector {self.connector_id} were linked to")

    async def _marker(self) -> dict:
        return _mapping(await self.config_service.get_config(
            source_accounts_key(self.connector_id), use_cache=False, raise_on_error=True
        ) or {}, self.connector_id)

    async def _deleted(self, marker: dict) -> dict[str, str]:
        """The source's deleted accounts by address, read once (``deletedChecked``); a failed read is tried again."""
        if marker.get("deletedChecked") or self.deleted_users is None:
            return {}
        try:
            deleted = {address.lower(): sid for address, sid in (await self.deleted_users()).items()}
        except Exception as e:
            self.logger.warning(f"Could not read the deleted users of connector {self.connector_id}; trying again next run: {e}")
            return {}
        marker["deletedChecked"] = True
        return deleted

    async def before_grants(self, accounts: Iterable[tuple[str | None, str | None]]) -> bool:
        """The check ``before_link`` makes, for accounts a connector grants but does not list (an external
        collaborator, a customer): ``(address, source id)`` pairs from the data the grant comes from, passed before
        the grants are written. Only pairs this process has not seen as they are go through, so a page of grants
        repeating known accounts costs nothing. False as ``before_link``: the stored links could not be read."""
        fresh = []
        for email, source_id in accounts:
            if not email or not (account := _account_id(source_id)):
                continue
            email = email.lower()
            bucket = _bucket(email)
            if bucket in self._read and self._linked.get(bucket, {}).get(email) == account:
                continue
            fresh.append(SimpleNamespace(email=email, source_user_id=account))
        return await self.before_link(fresh) if fresh else True

    async def before_link(self, users: list["AppUser"]) -> bool:
        """Before ``users`` are linked, take this connector's grants from any PipesHub user whose address now belongs
        to a different account than the one it was linked to before. False when the stored links could not be read:
        then nobody is linked and no grant is written this run (``ensure_loaded`` raises)."""
        try:
            marker = await self._marker()
            links = await self.processor.get_app_user_links(self.connector_id)
        except Exception as e:
            self.admission_failed = True
            self.logger.error(f"Could not read the stored users; no user is linked and no grant written this run: {e}")
            return False
        self.admission_failed = False
        noted = dict(marker)
        by_address = {link.email.lower(): sid for link in links
                      if getattr(link, "email", None) and (sid := _account_id(link.source_user_id))}
        seeded = bool(marker.get("seeded"))
        deleted = await self._deleted(marker)
        listed = [(account, u.email.lower()) for u in users if u.email and (account := _account_id(u.source_user_id))]
        touched = {_bucket(email) for _, email in listed}
        if not seeded:
            touched |= {_bucket(e) for e in by_address} | {_bucket(e.lower()) for e in _legacy_reused(marker)}
        await self._load(touched)     # fresh: another process may have written
        changed: set[int] = set()
        if not seeded:
            # Once: note the account every stored link names, so an address whose account is withdrawn before it is
            # listed again is still compared when a new account takes it.
            for email, sid in by_address.items():
                self._linked.setdefault(_bucket(email), {}).setdefault(email, sid)
                changed.add(_bucket(email))
            for email, sid in _legacy_reused(marker).items():
                self._reused.setdefault(_bucket(email.lower()), {}).setdefault(email.lower(), sid)
                changed.add(_bucket(email.lower()))
        for account, email in listed:
            bucket = _bucket(email)
            linked = self._linked.setdefault(bucket, {})
            # Each address is compared with the accounts it was linked to: the one this connector remembers, the one
            # its stored link names (another writer may have rewritten it already), and a deleted account the source
            # still lists with it. The listed id being linked to some other address says nothing about this one.
            before = {linked.get(email), by_address.get(email)}
            if email not in self._reused.get(bucket, {}):
                before.add(deleted.get(email))
            # shortcut: two live accounts listed with one address (Jira Data Center and Salesforce allow it) read as
            # a change of hands on every run, so the address keeps only the last-linked account's grants and they are
            # removed and written again each run; keying PipesHub users by source id would tell them apart.
            if before - {None, account}:
                removed = await self.processor.remove_user_access_to_connector(self.connector_id, email)
                self._reused.setdefault(bucket, {})[email] = account
                self._other.get(bucket, {}).pop(email, None)
                changed.add(bucket)
                self.logger.info(
                    "An address of connector %s belongs to a new account now; removed %d grant(s) of the previous one",
                    self.connector_id, removed,
                )
            # else, nothing remembered: shortcut: a change of hands made before this state existed, with the link
            # already naming the new account, is caught only where ``deleted_users`` still lists the old account.
            if linked.get(email) != account:
                linked[email] = account
                changed.add(bucket)
        await self._save(changed)
        marker = {"seeded": True, **({"deletedChecked": True} if marker.get("deletedChecked") else {})}
        if marker != noted and not await self.config_service.set_config(source_accounts_key(self.connector_id), marker):
            raise RuntimeError(f"Could not save which accounts the users of connector {self.connector_id} were linked to")
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
        if self.holder(email) is not None:
            return self.holds(email, object_id)
        linked = self._linked.get(bucket, {}).get(email)
        if object_id is not None and linked is not None:
            return linked == object_id
        return True

    def holds(self, email: str, source_id: str | None) -> bool:
        """For a loaded state: whether a grant naming ``email`` and ``source_id`` reaches the account holding the
        address. Only a re-used address is checked; there the grant must name the current account by id, either
        the id users are listed with or the other id noted for that account (``note_other_ids``)."""
        email = email.lower()
        holder = self.holder(email)
        if holder is None:
            # shortcut: an address never seen to change hands takes any id; failing closed here would drop every grant
            # to an account a connector does not list. Connectors pass the unlisted accounts their grants name with an
            # id through before_grants (Drive, Dropbox, Box and Zoom outsiders, Zendesk end users); still unchecked:
            # Microsoft guests outside the listing, Jira holders resolved from the payload email, Google group members
            # outside the Workspace (a second id space beside Drive's permissionId), and grantees named by address
            # alone (Dropbox invitees, Zoom alternative hosts and invitees). Upgrade each the same way.
            return True
        return source_id is not None and (
            source_id == holder or self._other.get(_bucket(email), {}).get(email) == [holder, source_id]
        )

    def holder(self, email: str) -> str | None:
        """For a loaded bucket: the listed id of the account that took over ``email``, or None for an address no
        other account held before."""
        email = email.lower()
        part = self._reused.get(_bucket(email), {})
        return part[email] if email in part else self._legacy.get(email)

    async def note_other_ids(self, ids: dict[str, tuple[str, str]]) -> None:
        """Per address, ``(account, other_id)``: the account listed as ``account`` appears on grants as
        ``other_id``, an id from another part of the source's API (Google Drive names a grantee by its
        permissionId, not its Directory id). Kept only for a re-used address held by ``account``, and taken by
        ``holds`` only while ``account`` holds it, so it can never pass a grant of the earlier account."""
        wanted = {e.lower(): pair for e, pair in ids.items()}
        await self._load({_bucket(e) for e in wanted})     # fresh: another process may have written
        changed: set[int] = set()
        for email, (account, other_id) in wanted.items():
            bucket = _bucket(email)
            if self.holder(email) != account or self._other.get(bucket, {}).get(email) == [account, other_id]:
                continue
            self._other.setdefault(bucket, {})[email] = [account, other_id]
            changed.add(bucket)
        await self._save(changed)
