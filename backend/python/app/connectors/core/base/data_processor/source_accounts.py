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

from typing import TYPE_CHECKING

from app.config.constants.arangodb import Connectors

if TYPE_CHECKING:
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


def source_accounts_key(connector_id: str) -> str:
    # Not a sync point: a full resync deletes those, and the grants it writes again by address are what this guards.
    return f"/services/connectors/{connector_id}/sourceAccounts"


def _account_id(source_user_id: str | None) -> str | None:
    # ``creator_<id>`` is the stand-in id connector_service links a connector's creator with, not a source account.
    return source_user_id if source_user_id and not source_user_id.startswith("creator_") else None


class SourceAccounts:
    def __init__(
        self, processor: "DataSourceEntitiesProcessor", connector_id: str, config_service: "ConfigurationService",
        logger: "Logger",
    ) -> None:
        self.processor = processor
        self.connector_id = connector_id
        self.config_service = config_service
        self.logger = logger
        self.loaded = False                   # until the saved state is read, no grant is written
        self.current: dict[str, str] = {}     # address -> source id this connector last linked it to
        self.reused: dict[str, str] = {}      # address another account held before -> source id that holds it now

    async def _state(self) -> dict[str, str]:
        """Read the saved state; returns who each withdrawn user was linked to. A failed read raises: without it no
        grant may be judged."""
        stored = await self.config_service.get_config(
            source_accounts_key(self.connector_id), use_cache=False, raise_on_error=True
        ) or {}
        reused = stored.get("reused") or {}
        if not isinstance(reused, dict):     # addresses without the account now holding them: no grant reaches them
            reused = dict.fromkeys(reused, "")
        self.reused = {e.lower(): sid for e, sid in reused.items()}
        self.current = {e.lower(): sid for e, sid in (stored.get("linked") or {}).items()}
        self.loaded = True
        return dict(stored.get("withdrawn") or {})

    async def ensure_loaded(self) -> None:
        """Read the saved state once, before the first grant is judged in any path (a sync, a reindex, a webhook).
        A failed read raises, so the caller writes nothing."""
        if not self.loaded:
            await self._state()

    async def _save(self, withdrawn: dict[str, str]) -> None:
        # shortcut: the state holds one entry per linked address; a directory of tens of thousands of users makes a
        # value of a few MB, over etcd's default request limit. Move it to its own store if etcd backs large ones.
        saved = await self.config_service.set_config(
            source_accounts_key(self.connector_id),
            {"withdrawn": withdrawn, "reused": self.reused, "linked": self.current},
        )
        if not saved:
            raise RuntimeError(f"Could not save which accounts the users of connector {self.connector_id} were linked to")

    async def before_link(self, users: list["AppUser"]) -> bool:
        """Before ``users`` are linked, take this connector's grants from any PipesHub user whose address now belongs
        to a different account than the one this connector linked it to before. False when the stored links could
        not be read: then nobody may be linked."""
        withdrawn = await self._state()
        try:
            links = {link.id: link.source_user_id for link in await self.processor.get_app_user_links(self.connector_id)}
        except Exception as e:
            # Who held each address before is unknown, so nobody is linked; the sync goes on with the stored links.
            self.logger.error(f"Could not read the stored users; no user is linked this run: {e}")
            return False
        state = (dict(withdrawn), dict(self.reused), dict(self.current))
        linked = set(links.values())
        for user in users:
            account = _account_id(user.source_user_id)
            if not user.email or not account:
                continue
            email = user.email.lower()
            # The id this connector last linked the address to decides, not the stored link: something else may
            # have rewritten the link to the new id already (an earlier release linked by address alone).
            remembered = self.current.get(email)
            if remembered == account:
                withdrawn = {uid: sid for uid, sid in withdrawn.items() if sid != account}   # restored, if it was gone
                continue
            if remembered is None and not withdrawn and (not links or account in linked):
                # shortcut: an address first seen after its link already names this account has nothing to compare
                # with, so a change of hands made before this state existed is not caught; a full resync rewrites
                # the old account's grants away only where the source no longer lists them.
                self.current[email] = account
                continue
            pipeshub_user = await self.processor.get_user_by_email(user.email, raise_on_error=True)
            if pipeshub_user is not None:
                before = {_account_id(x) for x in (remembered, withdrawn.get(pipeshub_user.id), links.get(pipeshub_user.id))}
                # shortcut: two live accounts listed with one address (Jira Data Center and Salesforce allow it) read
                # as a change of hands on every run, so the address keeps only the last-linked account's grants and
                # they are removed and written again each run; keying PipesHub users by source id would tell them apart.
                if before - {None, account}:
                    removed = await self.processor.remove_user_access_to_connector(self.connector_id, user.email)
                    self.reused[email] = account
                    self.logger.info(
                        "The address of user %s belongs to a new account now; removed %d grant(s) of the previous one",
                        pipeshub_user.id, removed,
                    )
                withdrawn.pop(pipeshub_user.id, None)
            self.current[email] = account
        if (withdrawn, self.reused, self.current) != state:
            await self._save(withdrawn)
        return True

    async def note_withdrawn(self, users: list) -> None:
        """Remember which account each user about to be withdrawn was linked to (its grants stay, so restoring that
        very account gives them back)."""
        gone = {u.id: sid for u in users if u.id and (sid := _account_id(getattr(u, "source_user_id", None)))}
        if not gone:
            return
        withdrawn = await self._state()
        withdrawn.update(gone)
        await self._save(withdrawn)

    def may_grant(self, email: str, object_id: str | None) -> bool:
        """Whether a grant to ``email`` reaches the account that holds it now. ``object_id`` is the source id the
        grant named, or None when it named none. False until the saved state is read (``ensure_loaded``). For a
        connector whose grants name accounts by the ids it lists users with (Microsoft Graph); the processor checks
        every connector's grants to re-used addresses (``holds``)."""
        if not self.loaded:
            return False
        email = email.lower()
        if email in self.reused:
            return self.holds(email, object_id)
        if object_id is not None and email in self.current:
            return self.current[email] == object_id
        return True

    def holds(self, email: str, source_id: str | None) -> bool:
        """For a loaded state: whether a grant naming ``email`` and ``source_id`` reaches the account holding the
        address. Only a re-used address is checked; there the grant must name the current account by id."""
        holder = self.reused.get(email.lower())
        return holder is None or (source_id is not None and source_id == holder)
