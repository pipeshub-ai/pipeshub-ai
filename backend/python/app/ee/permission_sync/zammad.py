"""Zammad: reading who may see what from Zammad.

Roles with their members (an agent reads a group's tickets through a role or their own group
access, ``zammad_access``), and the roles that may read or edit each knowledge-base category,
which archived and draft answers are granted to. The free connector keeps listing and syncing
groups, tickets and the knowledge base and calls these methods; without a licence key a
company-wide connector reads none of this (the processor's licence gate).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Dict, List, cast
from uuid import uuid4

from app.config.constants.arangodb import Connectors
from app.config.constants.http_status_code import HttpStatusCode
from app.connectors.sources.zammad.read_outcome import ReadOutcome, read_failure
from app.models.entities import AppRole, AppUser

if TYPE_CHECKING:
    from app.sources.external.zammad.zammad import ZammadDataSource


@dataclass(frozen=True)
class CategoryAccess:
    """The roles with editor and with reader access to one category (editors are not repeated as readers)."""

    editor_role_ids: tuple[int, ...] = ()
    reader_role_ids: tuple[int, ...] = ()

    @classmethod
    def from_permissions_response(cls, data: Mapping[str, object]) -> "CategoryAccess":
        """From ``GET /knowledge_bases/{kb}/categories/{id}/permissions``.

        ``roles_editor`` are the roles holding ``knowledge_base.editor``,
        ``roles_reader`` those holding ``knowledge_base.reader`` but not editor,
        ``permissions`` the category's effective rows.
        """
        editor_roles = _role_ids(data.get("roles_editor"))
        reader_roles = _role_ids(data.get("roles_reader")) - editor_roles
        rows: dict[int, str] = {}
        raw_rows = data.get("permissions")
        for raw_row in cast(list[object], raw_rows) if isinstance(raw_rows, list) else []:
            if not isinstance(raw_row, Mapping):
                continue
            row = cast(Mapping[str, object], raw_row)
            role_id = row.get("role_id")
            if isinstance(role_id, int) and not isinstance(role_id, bool):
                rows[role_id] = str(row.get("access"))
        editors: set[int] = set()
        readers: set[int] = set()
        for role_id in editor_roles:
            access = rows.get(role_id, "editor")
            if access == "editor":
                editors.add(role_id)
            elif access == "reader":
                readers.add(role_id)
        for role_id in reader_roles:
            if rows.get(role_id, "reader") in ("editor", "reader"):
                readers.add(role_id)
        return cls(tuple(sorted(editors)), tuple(sorted(readers)))


def _role_ids(roles: object) -> set[int]:
    ids: set[int] = set()
    if isinstance(roles, Iterable) and not isinstance(roles, (str, bytes, Mapping)):
        for role in cast(Iterable[object], roles):
            role_id = cast(Mapping[str, object], role).get("id") if isinstance(role, Mapping) else role
            if isinstance(role_id, int) and not isinstance(role_id, bool):
                ids.add(role_id)
    return ids


class ZammadPermissionsMixin:
    """The permission-reading half of ``ZammadConnector``."""

    async def _copies_no_source_access(self) -> bool:
        """Without a licence key a company-wide connector reads no access from Zammad. The processor then keeps
        every stored grant, group member and role, whatever is handed to it (``skips_permission_writes``), so the
        answers below only keep the run whole: no roles, read in full, and categories read with no role grants.
        Reported as unread, every group, role and category would count as a failed part of the run."""
        return await self.data_entities_processor.skips_permission_writes(self.connector_id)

    async def _fetch_roles(self) -> tuple[list[dict[str, Any]], ReadOutcome]:
        """Every role, with its permission names (``expand``), and how far the read got."""
        if await self._copies_no_source_access():
            return [], ReadOutcome.COMPLETE
        datasource = await self._get_fresh_datasource()
        roles: list[dict[str, Any]] = []
        page = 1
        per_page = 100
        while True:
            response = await datasource.list_roles(page=page, per_page=per_page, expand=True)
            if not response.success:
                outcome = read_failure(response.status_code)
                hint = ""
                if response.status_code == HttpStatusCode.FORBIDDEN.value:
                    # Zammad before 6.5 lists roles to admin.role only.
                    hint = "; before Zammad 6.5 the token needs the admin.role permission"
                self.logger.warning(
                    f"Failed to read Zammad roles (page {page}, HTTP {response.status_code}){hint}: "
                    f"{response.error or response.message}"
                )
                return roles, outcome
            data = response.data
            if not data:
                return roles, ReadOutcome.COMPLETE
            if not isinstance(data, list) or not all(isinstance(r, dict) for r in data):
                self.logger.warning(f"Zammad answered the role list (page {page}) with something that is not a list of roles")
                return roles, ReadOutcome.INTERRUPTED
            if any(r.get("permission_ids") and not isinstance(r.get("permissions"), list) for r in data):
                # Without the names nobody would count as an agent, and every member list would be emptied.
                self.logger.warning("Zammad listed roles without their permission names (expand); members stay as stored")
                return roles, ReadOutcome.INTERRUPTED
            roles.extend(data)
            if len(data) < per_page:
                return roles, ReadOutcome.COMPLETE
            page += 1

    async def _sync_roles(
        self,
        roles_data: list[dict[str, Any]],
        user_email_map: Dict[str, AppUser]
    ) -> None:
        """Write the roles as AppRoles with their members.

        An inactive role is written with no members: Zammad grants nothing
        through it, so nobody keeps knowledge-base access through it.
        """
        if await self._copies_no_source_access():
            return
        app_roles: list[tuple[AppRole, list[AppUser]]] = []
        for role_data in roles_data:
            role_id = role_data.get("id")
            if not role_id:
                continue

            role_name = role_data.get("name", f"Role {role_id}")
            active = role_data.get("active", True) is not False

            # Parse timestamps
            created_at = self._parse_zammad_datetime(role_data.get("created_at", ""))
            updated_at = self._parse_zammad_datetime(role_data.get("updated_at", ""))

            # Get users assigned to this role by checking user_id_to_data mapping
            role_users: list[AppUser] = []
            if active:
                for user_data in self._user_id_to_data.values():
                    if role_id in user_data.get("role_ids", []):
                        email = user_data.get("email")
                        if email and email in user_email_map:
                            role_users.append(user_email_map[email])

            app_role = AppRole(
                id=str(uuid4()),
                org_id=self.data_entities_processor.org_id,
                source_role_id=str(role_id),
                connector_id=self.connector_id,
                app_name=Connectors.ZAMMAD,
                name=role_name,
                source_created_at=created_at if created_at else None,
                source_updated_at=updated_at if updated_at else None,
            )
            app_roles.append((app_role, role_users))

        if app_roles:
            await self.data_entities_processor.on_new_app_roles(app_roles)
            self.logger.info(f"✅ Synced {len(app_roles)} roles")
        else:
            self.logger.info("No roles found")

    async def _kb_category_access(
        self, datasource: ZammadDataSource, kb_id: int, cat_id: int
    ) -> tuple[list[int], list[int], bool]:
        """The category's editor and reader roles, and whether they were read (False: keep what is stored)."""
        if await self._copies_no_source_access():
            return [], [], True
        # Effective access per role: the category's (inherited) rows, and the
        # default of every role without a row. Only the permissions endpoint
        # says which roles hold knowledge_base.editor / .reader, so it is read
        # for every category; the listing's permissions_effective has rows only.
        editor_role_ids: List[int] = []
        reader_role_ids: List[int] = []
        # False when the category's permissions couldn't be read for a reason
        # that passes: saving it would replace its stored grants with none.
        permissions_read = True
        try:
            perm_response = await datasource.get_kb_category_permissions(
                kb_id=kb_id,
                cat_id=cat_id
            )
        except Exception as e:
            perm_response = None
            self.logger.warning(f"Failed to fetch permissions for category {cat_id}: {e}")
        perm_data = perm_response.data if perm_response else None
        if perm_response and perm_response.success and isinstance(perm_data, dict):
            access = CategoryAccess.from_permissions_response(perm_data)
            editor_role_ids = list(access.editor_role_ids)
            reader_role_ids = list(access.reader_role_ids)
        elif (
            perm_response is None
            or perm_response.success
            or read_failure(perm_response.status_code) is ReadOutcome.INTERRUPTED
        ):
            permissions_read = False
            self._incomplete.append(f"the permissions of knowledge base category {cat_id}")
            self.logger.warning(
                f"Could not read the permissions of KB category {cat_id}; it and its "
                "archived and draft answers keep their stored access this sync"
            )
        else:
            # Refused (only editors may read them), or gone (404): saved with no
            # role grants, so nobody keeps access through it that Zammad can't confirm.
            self.logger.warning(
                f"Zammad refused the permissions of KB category {cat_id} "
                f"(HTTP {perm_response.status_code}); saving it with no role grants. "
                "The token needs knowledge_base.editor for internal and draft answers to be shared"
            )
        return editor_role_ids, reader_role_ids, permissions_read

    async def _kb_category_editor_role_ids(
        self, datasource: ZammadDataSource, kb_id: int, cat_id: int
    ) -> list[int]:
        """The roles that may edit the category, which its archived and draft answers are granted to. Raises when
        the read fails; an answer that could not be read is empty."""
        if await self._copies_no_source_access():
            return []
        perm_response = await datasource.get_kb_category_permissions(
            kb_id=kb_id,
            cat_id=cat_id
        )
        if perm_response.success and isinstance(perm_response.data, dict):
            return list(
                CategoryAccess.from_permissions_response(perm_response.data).editor_role_ids
            )
        return []
