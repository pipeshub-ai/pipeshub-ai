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

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from logging import Logger

    from app.config.configuration_service import ConfigurationService
    from app.connectors.core.base.data_processor.data_source_entities_processor import (
        DataSourceEntitiesProcessor,
    )
    from app.models.entities import AppUser


def source_accounts_key(connector_id: str) -> str:
    # Not a sync point: a full resync deletes those, and the grants it writes again by address are what this guards.
    return f"/services/connectors/{connector_id}/sourceAccounts"


class SourceAccounts:
    def __init__(
        self, processor: "DataSourceEntitiesProcessor", connector_id: str, config_service: "ConfigurationService",
        logger: "Logger",
    ) -> None:
        self.processor = processor
        self.connector_id = connector_id
        self.config_service = config_service
        self.logger = logger
        self.loaded = False                   # until the saved state is read, no grant is written (may_grant)
        self.current: dict[str, str] = {}     # address -> object id of the account listed with it in this run
        self.reused: dict[str, str] = {}      # address another account held before -> object id that holds it now

    async def _state(self) -> tuple[dict[str, str], dict[str, str]]:
        """The saved state. A failed read raises: without it no grant may be judged."""
        stored = await self.config_service.get_config(
            source_accounts_key(self.connector_id), use_cache=False, raise_on_error=True
        ) or {}
        reused = stored.get("reused") or {}
        if not isinstance(reused, dict):     # addresses without the account now holding them: no grant reaches them
            reused = dict.fromkeys(reused, "")
        return dict(stored.get("withdrawn") or {}), {e.lower(): sid for e, sid in reused.items()}

    async def _save(self, withdrawn: dict[str, str], reused: dict[str, str]) -> None:
        saved = await self.config_service.set_config(
            source_accounts_key(self.connector_id), {"withdrawn": withdrawn, "reused": reused}
        )
        if not saved:
            raise RuntimeError(f"Could not save which accounts the users of connector {self.connector_id} were linked to")

    async def ensure_loaded(self) -> None:
        """Read the saved state once, before the first grant is judged in any path (a sync, a reindex, a webhook).
        A failed read raises, so the caller writes nothing."""
        if not self.loaded:
            _, self.reused = await self._state()
            self.loaded = True

    async def admit(self, users: list["AppUser"]) -> None:
        """Link ``users`` (``on_new_app_users``), after taking this connector's grants from any PipesHub user whose
        address now belongs to a different account than the one it was linked to."""
        withdrawn, self.reused = await self._state()
        self.loaded = True
        try:
            links = {link.id: link.source_user_id for link in await self.processor.get_app_user_links(self.connector_id)}
        except Exception as e:
            # Who held each address before is unknown, so nobody is linked; the sync goes on with the stored links.
            self.logger.error(f"Could not read the stored users; no user is linked this run: {e}")
            return
        linked = set(links.values())
        for user in users:
            if not user.email or not user.source_user_id or user.source_user_id in linked:
                continue
            if not links and not withdrawn:
                break   # nobody was linked before: nothing to compare with
            pipeshub_user = await self.processor.get_user_by_email(user.email, raise_on_error=True)
            if pipeshub_user is None:
                continue
            before = links.get(pipeshub_user.id) or withdrawn.get(pipeshub_user.id)
            if before and before != user.source_user_id:
                removed = await self.processor.remove_user_access_to_connector(self.connector_id, user.email)
                self.reused[user.email.lower()] = user.source_user_id
                self.logger.info(
                    "The address of user %s belongs to a new account now; removed %d grant(s) of the previous one",
                    pipeshub_user.id, removed,
                )
            withdrawn.pop(pipeshub_user.id, None)
        await self._save(withdrawn, self.reused)
        self.current = {u.email.lower(): u.source_user_id for u in users if u.email and u.source_user_id}
        await self.processor.on_new_app_users(users)

    async def withdraw(self, users: list) -> None:
        """``withdraw_app_user_access``, remembering which account each withdrawn user was linked to (its grants stay,
        so restoring that very account gives them back)."""
        withdrawn, reused = await self._state()
        withdrawn.update({u.id: u.source_user_id for u in users if u.id and u.source_user_id})
        await self._save(withdrawn, reused)
        await self.processor.withdraw_app_user_access(self.connector_id, users)

    def may_grant(self, email: str, object_id: str | None) -> bool:
        """Whether a grant to ``email`` reaches the account that holds it now. ``object_id`` is the Entra object id
        the source named, or None when it named none (a SharePoint REST principal). False until the saved state is
        read (``ensure_loaded`` or ``admit``)."""
        if not self.loaded:
            return False
        email = email.lower()
        if email in self.reused:
            return object_id is not None and object_id == self.reused[email]
        if object_id is not None and email in self.current:
            return self.current[email] == object_id
        return True
