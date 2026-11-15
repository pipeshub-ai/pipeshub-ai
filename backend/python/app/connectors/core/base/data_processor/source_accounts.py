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
from typing import TYPE_CHECKING

from app.config.constants.arangodb import Connectors

if TYPE_CHECKING:
    from collections.abc import Iterable
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


def _account_id(source_user_id: str | None) -> str | None:
    # ``creator_<id>`` is the stand-in id connector_service links a connector's creator with, not a source account.
    return source_user_id if source_user_id and not source_user_id.startswith("creator_") else None


class SourceAccounts:
    """Per address, the source id this connector last linked it to (``linked``) and, for an address another account
    held before, the source id that holds it now (``reused``). Kept in ``BUCKETS`` config values by address hash:
    a run reads and writes only the buckets of the addresses it lists."""

    def __init__(
        self, processor: "DataSourceEntitiesProcessor", connector_id: str, config_service: "ConfigurationService",
        logger: "Logger",
    ) -> None:
        self.processor = processor
        self.connector_id = connector_id
        self.config_service = config_service
        self.logger = logger
        self.loaded = False                   # every bucket read; until a bucket is read, no grant to it is written
        self._read: set[int] = set()
        self._linked: dict[int, dict[str, str]] = {}
        self._reused: dict[int, dict[str, str]] = {}
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
            stored = await self.config_service.get_config(
                source_accounts_key(self.connector_id, bucket), use_cache=False, raise_on_error=True
            ) or {}
            self._linked[bucket] = {e.lower(): sid for e, sid in (stored.get("linked") or {}).items()}
            self._reused[bucket] = {e.lower(): sid for e, sid in (stored.get("reused") or {}).items()}
            self._read.add(bucket)
        await asyncio.gather(*(one(b) for b in set(buckets)))

    async def ensure_loaded(self, emails: Iterable[str] | None = None) -> None:
        """Read, once, the buckets of ``emails`` (every bucket when None) before a grant to them is judged in any
        path (a sync, a reindex, a webhook). A failed read raises, so the caller writes nothing."""
        if self.loaded:
            return
        wanted = set(range(BUCKETS)) if emails is None else {_bucket(e.lower()) for e in emails}
        if not self._read:
            await self._load_legacy()
        await self._load(wanted - self._read)
        self.loaded = self.loaded or len(self._read) == BUCKETS

    async def _load_legacy(self) -> None:
        """Re-used addresses an earlier version saved in one value, until the next user sync moves them (``_seed``)."""
        marker = await self.config_service.get_config(
            source_accounts_key(self.connector_id), use_cache=False, raise_on_error=True
        ) or {}
        self._legacy = {e.lower(): sid for e, sid in _legacy_reused(marker).items()}

    async def _save(self, buckets: Iterable[int]) -> None:
        for bucket in sorted(set(buckets)):
            saved = await self.config_service.set_config(
                source_accounts_key(self.connector_id, bucket),
                {"linked": self._linked.get(bucket, {}), "reused": self._reused.get(bucket, {})},
            )
            if not saved:
                raise RuntimeError(f"Could not save which accounts the users of connector {self.connector_id} were linked to")

    async def _seed(self) -> None:
        """Once per connector: note the account every stored link names, so an address whose account is withdrawn
        before it is ever listed again is still compared when a new account takes it. A state saved in one value by
        an earlier version has its re-used addresses carried over."""
        marker = await self.config_service.get_config(
            source_accounts_key(self.connector_id), use_cache=False, raise_on_error=True
        ) or {}
        if marker.get("seeded"):
            return
        stored = [(u.email.lower(), sid) for u in await self.processor.get_all_app_users(self.connector_id, raise_on_error=True)
                  if u.email and (sid := _account_id(u.source_user_id))]
        legacy = _legacy_reused(marker)
        touched = {_bucket(e) for e, _ in stored} | {_bucket(e.lower()) for e in legacy}
        await self._load(touched - self._read)
        for email, sid in stored:
            self._linked.setdefault(_bucket(email), {}).setdefault(email, sid)
        for email, sid in legacy.items():
            self._reused.setdefault(_bucket(email.lower()), {})[email.lower()] = sid
        await self._save(touched)
        saved = await self.config_service.set_config(source_accounts_key(self.connector_id), {"seeded": True})
        if not saved:
            raise RuntimeError(f"Could not save which accounts the users of connector {self.connector_id} were linked to")

    async def before_link(self, users: list["AppUser"]) -> bool:
        """Before ``users`` are linked, take this connector's grants from any PipesHub user whose address now belongs
        to a different account than the one this connector linked it to before. False when the stored links could
        not be read: then nobody may be linked."""
        try:
            await self._seed()
            links = {link.id: link.source_user_id for link in await self.processor.get_app_user_links(self.connector_id)}
        except Exception as e:
            # Who held each address before is unknown, so nobody is linked; the sync goes on with the stored links.
            self.logger.error(f"Could not read the stored users; no user is linked this run: {e}")
            return False
        linked_ids = set(links.values())
        listed = [(u, account, u.email.lower()) for u in users if u.email and (account := _account_id(u.source_user_id))]
        await self._load({_bucket(email) for _, _, email in listed})     # fresh: another process may have written
        changed: set[int] = set()
        for user, account, email in listed:
            bucket = _bucket(email)
            linked = self._linked.setdefault(bucket, {})
            # The id this connector last linked the address to decides, not the stored link: something else may
            # have rewritten the link to the new id already (an earlier release linked by address alone).
            remembered = linked.get(email)
            if remembered == account:
                continue
            changed.add(bucket)
            if remembered is not None or (links and account not in linked_ids):
                pipeshub_user = await self.processor.get_user_by_email(user.email, raise_on_error=True)
                before = {remembered, _account_id(links.get(pipeshub_user.id))} if pipeshub_user else set()
                # shortcut: two live accounts listed with one address (Jira Data Center and Salesforce allow it)
                # read as a change of hands on every run, so the address keeps only the last-linked account's grants
                # and they are removed and written again each run; keying PipesHub users by source id would tell
                # them apart.
                if before - {None, account}:
                    removed = await self.processor.remove_user_access_to_connector(self.connector_id, user.email)
                    self._reused.setdefault(bucket, {})[email] = account
                    self.logger.info(
                        "The address of user %s belongs to a new account now; removed %d grant(s) of the previous one",
                        pipeshub_user.id, removed,
                    )
            # else: first seen, and its link already names this account. shortcut: a change of hands made before
            # this state existed has nothing to compare with and is not caught.
            linked[email] = account
        await self._save(changed)
        return True

    def may_grant(self, email: str, object_id: str | None) -> bool:
        """Whether a grant to ``email`` reaches the account that holds it now. ``object_id`` is the source id the
        grant named, or None when it named none. False until the saved state is read (``ensure_loaded``). For a
        connector whose grants name accounts by the ids it lists users with (Microsoft Graph); the processor checks
        every connector's grants to re-used addresses (``holds``)."""
        email = email.lower()
        bucket = _bucket(email)
        if not self.loaded and bucket not in self._read:
            return False
        if self._holder(email) is not None:
            return self.holds(email, object_id)
        linked = self._linked.get(bucket, {}).get(email)
        if object_id is not None and linked is not None:
            return linked == object_id
        return True

    def holds(self, email: str, source_id: str | None) -> bool:
        """For a loaded state: whether a grant naming ``email`` and ``source_id`` reaches the account holding the
        address. Only a re-used address is checked; there the grant must name the current account by id."""
        holder = self._holder(email.lower())
        return holder is None or (source_id is not None and source_id == holder)

    def _holder(self, email: str) -> str | None:
        part = self._reused.get(_bucket(email), {})
        return part[email] if email in part else self._legacy.get(email)
