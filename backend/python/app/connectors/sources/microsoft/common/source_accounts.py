"""Which Microsoft Entra account each address belongs to, for grants written before the address changed hands.

PipesHub keeps one user per email address, while Entra keys an account by its object id. When an account is
deleted and a new one is created with the same user principal name, "The new account is assigned a new ID value
even though the UPN is the same", and SharePoint and OneDrive keep only the old ID, so the new account is denied
what the old one could open
(https://learn.microsoft.com/en-us/troubleshoot/sharepoint/sharing-and-permissions/fix-site-user-id-mismatch).
Linked by email, the new account would read everything PipesHub granted the old one.

So before a listed account is linked, a PipesHub user last linked to another object id loses this connector's
grants and memberships, and its address is noted as re-used. Afterwards a grant is written only when it names the
account that holds the address now: a Graph identity by its object id; a SharePoint REST principal, which carries
no Entra object id, not at all for a re-used address (it may be the old account's entry in the site's UserInfo
list, and nothing in it tells the two apart).
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from logging import Logger

    from app.config.configuration_service import ConfigurationService
    from app.connectors.core.base.data_processor.data_source_entities_processor import (
        DataSourceEntitiesProcessor,
    )
    from app.models.entities import AppUser
    from app.models.permission import Permission

# How long a loaded state is trusted before a grant decision reads it again: another instance of the connector may have
# admitted a new account for an address since (a reindex worker beside a sync).
STATE_MAX_AGE_SECONDS = 5.0


def source_accounts_key(connector_id: str) -> str:
    # Not a sync point: a full resync deletes those, and the grants it writes again by address are what this guards.
    return f"/services/connectors/{connector_id}/sourceAccounts"


class SourceAccounts:
    def __init__(
        self, processor: "DataSourceEntitiesProcessor", connector_id: str, config_service: "ConfigurationService",
        logger: "Logger", deleted_users: "Callable[[], Awaitable[dict[str, str]]] | None" = None,
    ) -> None:
        self.processor = processor
        self.connector_id = connector_id
        self.config_service = config_service
        self.logger = logger
        # address -> object id of each user in the directory's deleted items, read once to find addresses an earlier
        # version linked to their new account before this state was kept
        self.deleted_users = deleted_users
        self.loaded = False                   # until the saved state is read, no grant is written (may_grant)
        self.loaded_at = 0.0
        self.admission_failed = False         # the stored links could not be read this run: no grant is written
        self.current: dict[str, str] = {}     # address -> object id of the account listed with it in this run
        self.reused: dict[str, str] = {}      # address another account held before -> object id that holds it now
        self.unverified: set[str] = set()     # kept addresses not yet checked against deleted items (read failed)

    async def _state(self) -> dict:
        """The saved state. A failed read raises: without it no grant may be judged."""
        stored = await self.config_service.get_config(
            source_accounts_key(self.connector_id), use_cache=False, raise_on_error=True
        ) or {}
        if not isinstance(stored, dict):
            raise TypeError(f"the saved account state of connector {self.connector_id} is not a mapping")
        reused = stored.get("reused") or {}
        if not isinstance(reused, dict):     # addresses without the account now holding them: no grant reaches them
            reused = dict.fromkeys(reused, "")
        return {
            "withdrawn": dict(stored.get("withdrawn") or {}),
            # the same, by address: a merge of PipesHub users (a verified sign-in) can remove the user it names
            "withdrawnByAddress": {e.lower(): sid for e, sid in (stored.get("withdrawnByAddress") or {}).items()},
            "reused": {e.lower(): sid for e, sid in reused.items()},
            "deletedChecked": bool(stored.get("deletedChecked")),
            # deleted users by address, kept until a listing shows that address
            "deleted": {e.lower(): sid for e, sid in (stored.get("deleted") or {}).items()},
            "unverified": sorted({e.lower() for e in stored.get("unverified") or []}),
        }

    def _take(self, state: dict) -> None:
        self.reused, self.unverified = state["reused"], set(state["unverified"])
        self.loaded, self.loaded_at = True, time.monotonic()

    async def _save(self, state: dict) -> None:
        saved = await self.config_service.set_config(source_accounts_key(self.connector_id), state)
        if not saved:
            raise RuntimeError(f"Could not save which accounts the users of connector {self.connector_id} were linked to")

    async def ensure_loaded(self) -> None:
        """Read the saved state before a grant is judged in any path (a sync, a reindex, a webhook), and again once it
        is older than STATE_MAX_AGE_SECONDS. A failed read raises, and so does a run whose stored links could not be
        read, so the caller writes nothing and keeps what is stored."""
        if self.admission_failed:
            raise RuntimeError(f"the stored users of connector {self.connector_id} could not be read this run")
        if not self.loaded or time.monotonic() - self.loaded_at > STATE_MAX_AGE_SECONDS:
            self._take(await self._state())

    async def recheck(self, permissions: list["Permission"]) -> list["Permission"]:
        """``permissions`` without the user grants the saved state, read again now, refuses: for a write that comes a
        while after its grants were judged (a reindex batch), so an account admitted meanwhile is not handed them."""
        if self.admission_failed:
            raise RuntimeError(f"the stored users of connector {self.connector_id} could not be read this run")
        self._take(await self._state())
        return [p for p in permissions if not (getattr(p.entity_type, "value", p.entity_type) == "USER" and p.email
                                              and not self.may_grant(p.email, p.external_id))]

    async def admit(self, users: list["AppUser"]) -> None:
        """Link ``users`` (``on_new_app_users``), after taking this connector's grants from any PipesHub user whose
        address now belongs to a different account than the one it was linked to."""
        state = await self._state()
        withdrawn, by_address_withdrawn = state["withdrawn"], state["withdrawnByAddress"]
        self._take(state)
        try:
            links = await self.processor.get_app_user_links(self.connector_id)
        except Exception as e:
            # Who held each address before is unknown: nobody is linked and no grant is written this run.
            self.admission_failed, self.current = True, {}
            self.logger.error(f"Could not read the stored users; no user is linked this run: {e}")
            return
        self.admission_failed = False
        by_address = {link.email.lower(): link for link in links if getattr(link, "email", None)}
        by_user = {link.id: link.source_user_id for link in links}
        deleted, deleted_known = await self._deleted_users(state)
        unverified = set(state["unverified"])
        settled: list[tuple[str, str]] = []   # withdrawn records this listing links again, forgotten once linked
        for user in users:
            if not user.email or not user.source_user_id:
                continue
            address, sid = user.email.lower(), user.source_user_id
            link = by_address.get(address)
            if link is not None and link.source_user_id == sid:
                old = deleted.pop(address, None)
                if not deleted_known and address not in self.reused:
                    unverified.add(address)      # cannot be checked against deleted items yet: no object-less grant
                if old is None or old == sid or address in self.reused:
                    continue      # this address kept its account
                before, user_id = old, link.id   # linked to the new account by an earlier version
            else:
                if link is not None:
                    user_id = link.id
                elif by_user or withdrawn or by_address_withdrawn or address in deleted:
                    pipeshub_user = await self.processor.get_user_by_email(user.email, raise_on_error=True)
                    if pipeshub_user is None:
                        continue
                    user_id = pipeshub_user.id
                else:
                    continue      # nobody was linked before: nothing to compare with
                old = deleted.pop(address, None)
                before = (by_user.get(user_id) or withdrawn.get(user_id) or by_address_withdrawn.get(address)
                          or (None if address in self.reused else old))
            if before and before != sid:
                removed = await self.processor.remove_user_access_to_connector(self.connector_id, user.email)
                self.reused[address] = sid
                unverified.discard(address)
                self.logger.info(
                    "The address of user %s belongs to a new account now; removed %d grant(s) of the previous one",
                    user_id, removed,
                )
            if user_id in withdrawn or address in by_address_withdrawn:
                settled.append((user_id, address))
        state.update(withdrawn=withdrawn, withdrawnByAddress=by_address_withdrawn, reused=self.reused, deleted=deleted,
                     unverified=sorted(unverified))
        await self._save(state)
        self._take(state)
        self.current = {u.email.lower(): u.source_user_id for u in users if u.email and u.source_user_id}
        await self.processor.on_new_app_users(users)
        if settled:       # forgotten only once the link is written: a failed write leaves them to compare with
            for user_id, address in settled:
                withdrawn.pop(user_id, None)
                by_address_withdrawn.pop(address, None)
            await self._save(state)

    async def _deleted_users(self, state: dict) -> tuple[dict[str, str], bool]:
        """The deleted users by address (read once, then kept in the state until a listing shows each address), and
        whether that evidence is complete: False while the read has not succeeded yet."""
        deleted = state["deleted"]
        if state["deletedChecked"] or self.deleted_users is None:
            return deleted, True
        try:
            read = {address.lower(): sid for address, sid in (await self.deleted_users()).items()}
        except Exception as e:
            self.logger.warning(f"Could not read the directory's deleted users; trying again next run: {e}")
            return deleted, False
        deleted.update(read)
        state["deletedChecked"] = True
        state["unverified"] = []
        return deleted, True

    async def withdraw(self, users: list) -> None:
        """``withdraw_app_user_access``, remembering which account each withdrawn user was linked to, by PipesHub user
        and by address (its grants stay, so restoring that very account gives them back)."""
        state = await self._state()
        state["withdrawn"].update({u.id: u.source_user_id for u in users if u.id and u.source_user_id})
        state["withdrawnByAddress"].update({
            u.email.lower(): u.source_user_id for u in users if getattr(u, "email", None) and u.source_user_id})
        await self._save(state)
        await self.processor.withdraw_app_user_access(self.connector_id, users)

    def may_grant(self, email: str, object_id: str | None) -> bool:
        """Whether a grant to ``email`` reaches the account that holds it now. ``object_id`` is the Entra object id
        the source named, or None when it named none (a SharePoint REST principal). False until the saved state is
        read (``ensure_loaded`` or ``admit``), and for a grant naming an object id while the stored links could not be
        read this run."""
        if not self.loaded or (self.admission_failed and object_id is not None):
            return False
        email = email.lower()
        if email in self.reused:
            return object_id is not None and object_id == self.reused[email]
        if object_id is None and email in self.unverified:
            return False
        if object_id is not None and email in self.current:
            return self.current[email] == object_id
        return True
