"""GitLab: reading who may see what from GitLab.

Project and group members with their access levels (as user grants, or pseudo-groups for members
without an email), and the authors and assignees who keep a confidential issue. The free connector
keeps syncing projects, groups, issues, merge requests and code through ``ProjectsSync`` and
``IssuesSync``; without a licence key a company-wide connector reads none of this (the processor's
licence gate). The personal connector (``GitLabPersonalProjectsSync``) overrides the project and group
entry points and never asks for confidential-issue grants, so it reads none of it either.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.config.constants.arangodb import Connectors
from app.connectors.sources.gitlab.constants import PSEUDO_USER_GROUP_PREFIX
from app.models.entities import AppUserGroup
from app.models.permission import EntityType, Permission, PermissionType

if TYPE_CHECKING:
    from collections.abc import Iterable
    from typing import Any

    from gitlab.v4.objects import GroupMember, Project


def member_has_access(member: object) -> bool:
    """GitLab gives nothing to a blocked, deactivated or banned user, nor to a member still
    awaiting a seat; members listings include all of them."""
    state = getattr(member, "state", None)
    membership = getattr(member, "membership_state", None)
    return (state if isinstance(state, str) else "active") == "active" and membership != "awaiting"


def project_reader_ids(members: Iterable[GroupMember]) -> set[int]:
    """GitLab ids of the members who can read the project: active, at Guest or above."""
    return {
        int(m.id) for m in members
        if member_has_access(m) and (getattr(m, "access_level", 0) or 0) >= 10
    }


class GitLabProjectsPermissionsMixin:
    """The permission-reading half of ``ProjectsSync`` (``self.c`` is the connector)."""

    async def _copies_no_source_access(self) -> bool:
        """Without a licence key a company-wide connector reads no access from GitLab. The processor then keeps
        every stored grant and pseudo-group whatever is handed to it (``skips_permission_writes``), so a project
        or group is written with no grants: still synced, and not taken for one whose members could not be read."""
        return await self.c.data_entities_processor.skips_permission_writes(self.c.connector_id)

    async def _group_member_permissions(self, group_path: str) -> list[Permission] | None:
        """The grants of a GitLab group's members at Guest or above, and the creator's; None when the members
        could not be read, which keeps the group's stored access."""
        if await self._copies_no_source_access():
            return []
        c = self.c
        members_res = await c.runtime.ds_call(
            c.data_source.list_group_members, group_id=group_path, get_all=True,
        )
        group_permissions: list[Permission] | None = None
        if not members_res.success:
            self.logger.warning(
                "Could not list members for GitLab group %s: %s. Keeping its stored access.",
                group_path, members_res.error,
            )
            c.note_failed_read(f"members of group {group_path}", members_res)
        else:
            group_permissions = []
            for member in members_res.data or []:
                # Minimal Access (5) cannot see the group's projects. Guest is 10.
                if (getattr(member, "access_level", 0) or 0) < 10 or not member_has_access(member):
                    continue
                permission = await self._transform_restrictions_to_permissions(member)
                if permission:
                    group_permissions.append(permission)

            # Always ensure the creator has access to the group node
            creator_permission = c.creator_user_permission()
            if creator_permission is not None and not any(
                getattr(p, "email", None) == creator_permission.email for p in group_permissions
            ):
                group_permissions.append(creator_permission)
        return group_permissions

    async def _sync_project_members_as_pseudo(self, project: Project) -> None:
        """Sync project members; create RecordGroups with correct permissions.

        Falls back to creator-only permissions when ``list_project_members_all``
        fails or returns an empty list (GitLab returns ``200 OK + []`` instead of
        403 for tokens below the required role on private projects).
        """
        c = self.c
        if await self._copies_no_source_access():
            # The five groups are still written, as containers for the project's content.
            await c.data_entities_processor.on_new_record_groups(
                [(group, []) for group in self._build_project_record_groups(project)]
            )
            return
        project_id = project.id
        project_name = project.name
        dict_member: dict[int, GroupMember] = {}
        self.logger.info("Syncing users for project %s", project_name)
        c._project_reader_ids.pop(int(project_id), None)
        members_res = await c.runtime.ds_call(
            c.data_source.list_project_members_all, project_id=project_id, get_all=True,
        )
        listing_failed = not members_res.success
        listing_empty = members_res.success and not (members_res.data or [])

        if listing_failed:
            c.note_failed_read(f"members of project {project.path_with_namespace}", members_res)
        if (listing_failed or listing_empty) and await c.data_entities_processor.has_stored_permissions(
            "record_group", c.connector_id, str(project_id), keep=True,
        ):
            # A list that could not be read, or came back empty, is not "nobody": the
            # stored grants stay until a real list replaces them.
            self.logger.warning(
                "Members of project %s (%s) unreadable or empty; keeping its stored access.",
                project_id, project_name,
            )
            await c.data_entities_processor.on_new_record_groups(
                [(group, None) for group in self._build_project_record_groups(project)]
            )
            return
        if listing_failed or listing_empty:
            creator_perm = c.creator_user_permission()
            if creator_perm is None:
                if listing_failed:
                    self.logger.error(
                        "Error fetching members for project %s (%s): %s",
                        project_id, project_name, getattr(members_res, "error", "unknown"),
                    )
                else:
                    self.logger.info("No members found for project %s", project_id)
                return
            if listing_failed:
                self.logger.error(
                    "Error fetching members for project %s (%s): %s. "
                    "Falling back to creator-only permissions.",
                    project_id, project_name, getattr(members_res, "error", "unknown"),
                )
            else:
                self.logger.warning(
                    "No members returned for project %s (%s); GitLab returns [] for tokens "
                    "below the required role on private projects. Falling back to creator-only.",
                    project_id, project_name,
                )
            await self._apply_creator_fallback_for_project(project)
            return

        members = members_res.data
        # Keep highest access_level when a user appears via multiple sources
        for member in members:
            existing = dict_member.get(member.id)
            if existing is None:
                dict_member[member.id] = member
                continue
            new_level = getattr(member, "access_level", 0) or 0
            old_level = getattr(existing, "access_level", 0) or 0
            if new_level > old_level:
                dict_member[member.id] = member

        # Inject creator so Admin/Auditor personas (no membership row) get access.
        # The project folder is granted to every member at Guest or above. Restricting
        # it to direct members drops people who arrive through the group, a filter
        # that omitted an ancestor, or a project/group share. Work items inherit the
        # project, so a duplicate grant is harmless and a missing one hides the folder.
        c.users._inject_creator_member_into(dict_member)
        c._project_reader_ids[int(project_id)] = project_reader_ids(dict_member.values())

        permission_project_level: list[Permission] = []
        permission_work_items_level: list[Permission] = []
        permission_confidential_level: list[Permission] = []
        permission_code_repo_level: list[Permission] = []
        permission_merge_requests_level: list[Permission] = []

        for member in dict_member.values():
            if not member_has_access(member):
                continue
            permission = await self._transform_restrictions_to_permissions(member)
            if not permission:
                continue
            level: int = getattr(member, "access_level", 0) or 0
            if level < 10:
                continue
            permission_project_level.append(permission)
            if level == 10:
                permission_work_items_level.append(permission)
            elif level >= 20:
                permission_work_items_level.append(permission)
                permission_confidential_level.append(permission)
                permission_merge_requests_level.append(permission)
                permission_code_repo_level.append(permission)
            elif level >= 15:
                permission_work_items_level.append(permission)
                permission_confidential_level.append(permission)
            else:
                self.logger.warning(
                    "Member %s has unrecognized access level %s, skipping", member.name, level
                )

        (
            project_record_group,
            work_items_record_group,
            confidential_record_group,
            merge_requests_record_group,
            code_repo_record_group,
        ) = self._build_project_record_groups(project)
        await c.data_entities_processor.on_new_record_groups(
            [
                (project_record_group, permission_project_level),
                (work_items_record_group, permission_work_items_level),
                (confidential_record_group, permission_confidential_level),
                (code_repo_record_group, permission_code_repo_level),
                (merge_requests_record_group, permission_merge_requests_level),
            ]
        )

    async def _transform_restrictions_to_permissions(
        self, member: GroupMember
    ) -> Permission | None:
        """Build a ``Permission`` object for a member by looking up the AppUser or creating a pseudo-group."""
        return await self._create_permission_from_principal(
            EntityType.USER.value,
            str(member.id),
            PermissionType.OWNER.value,
            create_pseudo_group_if_missing=True,
        )

    async def _create_permission_from_principal(
        self,
        principal_type: str,
        principal_id: str,
        permission_type: PermissionType,
        *,
        create_pseudo_group_if_missing: bool = False,
    ) -> Permission | None:
        """Look up a principal in the DB and return the appropriate ``Permission`` object.

        When a user is not found and ``create_pseudo_group_if_missing`` is True,
        creates a pseudo-group to preserve the permission until a real AppUser
        row is created on the next sync.
        """
        c = self.c
        try:
            if principal_type == EntityType.USER.value:
                user = await c.data_entities_processor.get_user_by_source_id(
                    source_user_id=principal_id,
                    connector_id=c.connector_id,
                )
                if user:
                    return Permission(
                        email=user.email,
                        type=permission_type,
                        entity_type=EntityType.USER,
                    )
                if create_pseudo_group_if_missing:
                    pseudo_group = await c.data_entities_processor.get_user_group_by_external_id(
                        connector_id=c.connector_id,
                        external_id=principal_id,
                    )
                    if not pseudo_group:
                        pseudo_group = await self._create_pseudo_group(principal_id)
                    else:
                        await self._keep_pseudo_group(principal_id)
                    if pseudo_group:
                        self.logger.debug(
                            "Using pseudo-group for user %s (no email available)", principal_id
                        )
                        return Permission(
                            external_id=pseudo_group.source_user_group_id,
                            type=permission_type,
                            entity_type=EntityType.GROUP,
                        )
                self.logger.debug("User %s not found in DB, skipping permission", principal_id)
                return None
        except Exception as e:
            self.logger.error("Failed to create permission from principal: %s", e)
            return None

    async def _keep_pseudo_group(self, account_id: str) -> None:
        """Note every stored copy of a reused pseudo-group kept, so the unlink after a full
        sync leaves them alone (older syncs could store several, and grants may sit on any);
        a lookup that fails makes the run incomplete instead."""
        c = self.c
        try:
            await c.data_entities_processor.note_kept_user_group(c.connector_id, account_id)
        except Exception as e:
            self.logger.error("Could not keep the pseudo-group of user %s: %s", account_id, e)
            c.note_sync_problem(f"pseudo-group of user {account_id}: could not be kept: {e}")

    async def _create_pseudo_group(self, account_id: str) -> AppUserGroup | None:
        """Create a pseudo-group for a user without an email address.

        The pseudo-group preserves the permission until the user's real
        AppUser row is created on the next sync.
        """
        c = self.c
        try:
            pseudo_group = AppUserGroup(
                app_name=Connectors.GITLAB,
                connector_id=c.connector_id,
                source_user_group_id=account_id,
                name=f"{PSEUDO_USER_GROUP_PREFIX}_{account_id}",
                org_id=c.data_entities_processor.org_id,
            )
            await c.data_entities_processor.on_new_user_groups([(pseudo_group, [])])
            self.logger.info("Created pseudo-group for user without email: %s", account_id)
            return pseudo_group
        except Exception as e:
            self.logger.error("Failed to create pseudo-group for %s: %s", account_id, e)
            return None


class GitLabIssuesPermissionsMixin:
    """The permission-reading half of ``IssuesSync``: who keeps a confidential issue. Without a licence key
    nobody new does; the processor keeps the stored grants."""

    async def _confidential_exception_permissions(self, issue: Any) -> list[Permission]:
        """Principals who see a confidential issue regardless of their project role.

        Resolved through the same path as member permissions, so an author without a
        resolvable identity is parked on a pseudo-group rather than dropped.
        """
        if await self.c.data_entities_processor.skips_permission_writes(self.c.connector_id):
            return []
        c = self.c
        candidates: list[int] = []
        author = getattr(issue, "author", None) or {}
        people = [author, *(getattr(issue, "assignees", None) or [])]
        # A blocked or banned author or assignee reads nothing on GitLab.
        candidates.extend(
            int(person["id"]) for person in people
            if isinstance(person, dict) and person.get("id") is not None
            and (person.get("state") or "active") == "active"
        )
        # The exception sits on top of reading the project: an author removed from it, or one
        # who filed it while the project was public, reads nothing there on GitLab. The grant
        # is direct, so it would outlive the project membership it depends on.
        readers = await self._project_readers(issue.project_id)
        if readers is None:
            self.logger.warning(
                "Members of project %s unreadable; confidential issue %s gets no author or assignee grant",
                issue.project_id, getattr(issue, "iid", issue.id),
            )
            return []
        candidates = [user_id for user_id in candidates if user_id in readers]

        permissions: list[Permission] = []
        seen: set[int] = set()
        for user_id in candidates:
            if user_id in seen:
                continue
            seen.add(user_id)
            permission = await c.projects._create_permission_from_principal(
                EntityType.USER.value,
                str(user_id),
                PermissionType.OWNER.value,
                create_pseudo_group_if_missing=True,
            )
            if permission:
                permissions.append(permission)
        return permissions

    async def _project_readers(self, project_id: int | str) -> set[int] | None:
        """This run's reader ids of the project; read now when a reindex comes outside a sync. None if unknown."""
        c = self.c
        readers = c._project_reader_ids.get(int(project_id))
        if readers is not None:
            return readers
        res = await c.runtime.ds_call(c.data_source.list_project_members_all, project_id=int(project_id), get_all=True)
        if not res.success or not res.data:
            return None
        return project_reader_ids(res.data)
