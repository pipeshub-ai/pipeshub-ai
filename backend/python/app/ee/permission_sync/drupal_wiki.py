"""Drupal Wiki: reading who may see what from Drupal Wiki.

Groups with their members, and the grants a space's access status and member list give. The free
connector keeps listing spaces and syncing pages and calls these methods; without a licence key a
company-wide connector reads none of this (the processor's licence gate), and reads no private
space's member list either (``DrupalWikiConnector._sync_spaces``).
"""

from typing import Any

from app.config.constants.arangodb import Connectors
from app.connectors.sources.drupal_wiki.vocabulary import (
    OPEN_SPACE_ACCESS_STATUSES,
    PAGE_SIZE,
    DrupalWikiAuthError,
    DrupalWikiSyncError,
    _as_int,
)
from app.models.entities import AppUser, AppUserGroup
from app.models.permission import EntityType, Permission, PermissionType
from app.sources.client.drupal_wiki.drupal_wiki import describe_exception


class DrupalWikiPermissionsMixin:
    """The permission-reading half of ``DrupalWikiConnector``."""

    async def _copies_no_source_access(self) -> bool:
        """Without a licence key a company-wide connector reads no access from Drupal Wiki. The processor then
        keeps every stored grant, group and membership whatever is handed to it (``skips_permission_writes``), so
        a space is written with no grants: as a container, not as "nobody may read it", and without the warning
        a space with no members gets."""
        return await self.data_entities_processor.skips_permission_writes(self.connector_id)

    async def _sync_groups(self, users_by_id: dict[str, AppUser]) -> None:
        """Sync every group with its members.

        Membership is replaced wholesale, so a member removed in the wiki loses access
        on the next run. Groups deleted in the wiki are left in place, as Jira's
        connector does: their grants disappear anyway when space permissions are
        rewritten, so a leftover group node grants nothing.
        """
        if await self._copies_no_source_access():
            return
        synced = 0
        read: set[str] = set()
        all_read = True
        page = 0
        while True:
            payload = self._require_ok(
                await self.data_source.list_groups(page=page, size=PAGE_SIZE), "reading the list of groups"
            )
            content = payload.get("content") or []
            groups_with_members: list[tuple[AppUserGroup, list[AppUser]]] = []
            for group in content:
                group_id = _as_int(group.get("id"))
                if group_id is None:
                    self.logger.warning(
                        "Ignoring a Drupal Wiki group that arrived without an id"
                    )
                    continue
                try:
                    detail = self._require_ok(
                        await self.data_source.get_group(group_id), f"reading the members of group {group_id}"
                    )
                except DrupalWikiAuthError:
                    raise
                except Exception as e:
                    # Passing an empty member list here would wipe the group's edges,
                    # so a failed fetch leaves the previous membership in place.
                    self._fail(
                        str(e) if isinstance(e, DrupalWikiSyncError)
                        else f"Could not read the members of group {group_id}: {describe_exception(e)}"
                    )
                    all_read = False
                    continue

                read.add(str(group_id))
                members = [
                    users_by_id[str(member.get("id"))]
                    for member in (detail.get("members") or [])
                    if str(member.get("id")) in users_by_id
                    and users_by_id[str(member.get("id"))].is_active
                ]
                source = group.get("groupSource")
                groups_with_members.append((
                    AppUserGroup(
                        app_name=Connectors.DRUPAL_WIKI,
                        connector_id=self.connector_id,
                        org_id=self.data_entities_processor.org_id,
                        source_user_group_id=str(group_id),
                        name=group.get("name") or f"Group {group_id}",
                        description=f"source={source}" if source else None,
                    ),
                    members,
                ))

            if groups_with_members:
                await self.data_entities_processor.on_new_user_groups(groups_with_members)
                synced += len(groups_with_members)
            if payload.get("last", True) or not content:
                break
            page += 1

        self._groups_read = read if all_read else None
        self.logger.info("Synced %d Drupal Wiki group(s)", synced)

    def _build_space_permissions(
        self,
        space: dict[str, Any],
        members: dict[str, Any] | None,
    ) -> list[Permission]:
        """Permissions for one space.

        Open spaces (``AUTHENTICATED``/``ANONYMOUS``) are readable by every wiki user,
        which maps to a single org-wide read. Private spaces are readable only by their
        members, which needs ``members`` from the internal GraphQL API.
        """
        access_status = (space.get("accessStatus") or "").upper()
        if access_status in OPEN_SPACE_ACCESS_STATUSES:
            return [Permission(
                external_id=None, email=None,
                type=PermissionType.READ, entity_type=EntityType.ORG,
            )]

        if not members:
            return []

        # Holding any role on a space means being able to see it: a revision approver
        # or technical reviewer cannot do their job without reading the page, and every
        # other right is strictly more than read. So membership is the grant, and it is
        # always READ -- nothing downstream separates the kinds, as in the Jira connector.
        permissions: dict[tuple[str, str], Permission] = {}
        disabled = self._disabled_emails or set()
        for user in members.get("users") or []:
            email = (user.get("email") or "").strip()
            if not email or email.lower() in disabled:
                continue
            permissions.setdefault(("user", email.lower()), Permission(
                external_id=None,
                email=email,
                type=PermissionType.READ,
                entity_type=EntityType.USER,
            ))

        for group in members.get("groups") or []:
            group_id = group.get("id")
            if group_id is None:
                continue
            external_id = str(group_id)
            permissions.setdefault(("group", external_id), Permission(
                external_id=external_id,
                email=None,
                type=PermissionType.READ,
                entity_type=EntityType.GROUP,
            ))

        return list(permissions.values())
