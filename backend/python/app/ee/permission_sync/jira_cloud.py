"""Jira Cloud: reading who may see what from Jira.

Project permission schemes, groups with their members, project roles and leads, issue security levels
and comment restrictions. The free connector keeps listing and syncing projects and issues and calls these
methods; without a licence key a company-wide connector reads none of this (the processor's licence gate).
"""

import json
from typing import (
    Any,
    Optional,
)
from uuid import uuid4

from app.config.constants.arangodb import (
    AccessRule,
)
from app.config.constants.http_status_code import HttpStatusCode
from app.connectors.sources.atlassian.core.read_outcome import (
    StoredAccessReadError,
)
from app.connectors.utils.record_ids import new_record_id
from app.ee.permission_sync.jira_comment_access import (
    COMMENT_RECORD_PREFIX,
    is_restricted_comment,
    restricted_comment_grants,
)
from app.ee.permission_sync.jira_issue_security import (
    BROWSE_ISSUE_HOLDER_TYPES,
    HOLDER_GROUP,
    HOLDER_USER,
    UNREADABLE_REASONS,
    IssueSecurityContext,
    SchemeKnowledge,
    browse_issue_holders,
    classify_scheme_response,
    collect_paged_values,
    custom_field_ids_from_members,
    http_status_is_transient,
    index_members,
    level_group_external_id,
    level_group_grants,
    level_readers,
    plan_level_reread,
    refuses_a_scope,
    roles_for_levels,
    scheme_has_no_levels,
    unreadable_security_notices,
)
from app.models.entities import (
    AppRole,
    AppUser,
    AppUserGroup,
    CommentRecord,
    MimeTypes,
    OriginTypes,
    Record,
    RecordGroup,
    RecordGroupType,
    RecordType,
    TicketRecord,
)
from app.models.permission import EntityType, Permission, PermissionType
from app.services.notification.types import (
    NotificationSeverity,
    NotificationType,
)
from app.utils.time_conversion import get_epoch_timestamp_in_ms

GROUP_PAGE_SIZE: int = 50


# --- Permission-scheme vocabulary (GET /rest/api/3/permissionscheme/{id}/permission) ---
# The only grant that decides who can see a project's issues at all; every other grant type
# in the scheme is ignored when building the RecordGroup ACL.
BROWSE_PROJECTS_PERMISSION: str = "BROWSE_PROJECTS"


# holder.type values. Anything not listed here is logged as unknown and skipped.
HOLDER_TYPE_GROUP: str = "group"


HOLDER_TYPE_APPLICATION_ROLE: str = "applicationRole"


HOLDER_TYPE_USER: str = "user"


HOLDER_TYPE_ANYONE: str = "anyone"


HOLDER_TYPE_PROJECT_ROLE: str = "projectRole"


HOLDER_TYPE_PROJECT_LEAD: str = "projectLead"


# JSM portal customers — external users PipesHub does not sync.
HOLDER_TYPE_SD_PORTAL_CUSTOMER: str = "sd.customer.portal.only"


# App-only project role: its members are Atlassian apps, not people, so it grants nobody.
ATLASSIAN_ADDONS_PROJECT_ROLE: str = "atlassian-addons-project-access"


# Synthetic ORG-scope external ids for grants not tied to a concrete group or user.
ORG_SCOPE_ANYONE_AUTHENTICATED: str = "anyone_authenticated"


# actor.type values in a project role's actor list
# (GET /rest/api/3/project/{projectIdOrKey}/role/{id}).
ROLE_ACTOR_TYPE_USER: str = "atlassian-user-role-actor"


ROLE_ACTOR_TYPE_GROUP: str = "atlassian-group-role-actor"


# Marks a group a level names by a name no read resolved to an id: it names no group read, so its members are unknown.
_UNRESOLVED_GROUP: str = "unresolved-group-name:"


# Groups a reindex reads again per project to apply its issues' levels (``_reindex_level_readers``).
REINDEX_GROUP_READS: int = 20


def _listed(members: list[AppUser], listed: dict[str, AppUser]) -> list[AppUser]:
    """The members a group or role write holds: those the user list read this time (``_member_directory``)."""
    return [m for m in members if m.source_user_id in listed]


class JiraCloudPermissionsMixin:
    """The permission-reading half of ``JiraConnector``."""

    async def _copies_no_source_access(self) -> bool:
        """Without a licence key a company-wide connector reads no access from Jira."""
        return await self.data_entities_processor.permission_sync_gated(self.connector_id)

    async def _without_stored_members(
        self, entity: str, items: list[tuple[Any, list[AppUser]]]
    ) -> list[tuple[Any, list[AppUser]]]:
        """On an unfinished user read, leave out the groups or roles (``entity`` is
        ``user_group`` or ``role``) whose members are stored, so they keep them. One with
        no members stored is saved with the members that were read."""
        kept: list[tuple[Any, list[AppUser]]] = []
        for item, members in items:
            external_id = item.source_user_group_id if entity == "user_group" else item.source_role_id
            if await self._has_stored_access(entity, external_id):
                self.logger.warning(
                    "Keeping the stored members of %s: the Jira user list could not be read in full", item.name
                )
                continue
            kept.append((item, members))
        return kept

    async def _without_unresolved_stored_leads(
        self,
        lead_roles: list[tuple[AppRole, list[AppUser]]],
        unresolved_leads: dict[str, tuple[str, Optional[str]]],
    ) -> list[tuple[AppRole, list[AppUser]]]:
        """Leave out the lead roles whose lead the short user list did not have, when that
        lead role or its project has stored access. A renamed project's saved lead role is
        under the old key, so its project's stored access keeps an empty lead role under the
        new key from being saved."""
        kept: list[tuple[AppRole, list[AppUser]]] = []
        for role, members in lead_roles:
            unresolved = unresolved_leads.get(role.source_role_id)
            if unresolved is not None:
                project_key, project_id = unresolved
                if await self._has_stored_access("role", role.source_role_id) or (
                    project_id and await self._has_stored_access("record_group", str(project_id))
                ):
                    self.logger.warning(
                        "Keeping the stored lead of project %s: the lead was not in the Jira user list, "
                        "which could not be read in full",
                        project_key,
                    )
                    continue
            kept.append((role, members))
        return kept

    def _member_directory(self, jira_users: list[AppUser]) -> tuple[dict[str, AppUser], dict[str, AppUser]]:
        """``(listed, members)``: the accounts the user list read this time, which group and role writes hold, and those plus
        the listed accounts with product access whose email is hidden (``_listed_without_email``), which level readers
        also hold, by account. Not the linked accounts a list cut short did not show: a group read says nothing of them."""
        listed = {user.source_user_id: user for user in jira_users if user.source_user_id}
        return listed, {**{u.source_user_id: u for u in self._listed_without_email() if u.source_user_id}, **listed}

    async def _fallback_permissions_for_forbidden_scheme(
        self,
        project_key: str,
        status: int,
        stage: str,
    ) -> Optional[list[Permission]]:
        """Build a single-user BROWSE permission for the authenticated Jira account
        when the permission-scheme endpoints return 403 for this project (the
        account isn't a project admin). 401/transient failures return None from
        the scheme fetch; the caller keeps the project's stored access.

        Mirrors the ``_app_roles_forbidden`` fallback in
        ``_fetch_application_roles_to_groups_mapping``: rather than indexing
        the project with no ACLs (which would silently hide it from search
        results across the org), give the account that fetched the issues
        direct READ access. The connector creator reaches it through the
        ``authenticatedAs`` link when their PipesHub email differs.
        """
        jira_email = self._authenticated_jira_email
        if jira_email:
            self.logger.warning(
                "⚠️ %s for %s returned %s — Jira account lacks Administer Projects. "
                "Granting Jira account '%s' direct BROWSE access instead of dropping "
                "all ACLs for this project.",
                stage, project_key, status, jira_email,
            )
            await self.notify(
                type=NotificationType.CONNECTOR_WARNING,
                severity=NotificationSeverity.WARNING,
                title=self._notification_title(f"couldn't read permissions for project {project_key}"),
                message=(
                    f"The connector's Jira account ({jira_email}) can't read the permission scheme "
                    f"for {project_key}. Grant it project admin on {project_key}; until then, only that "
                    "Jira account (and the connector owner through it) can access this project's "
                    "issues in PipesHub."
                ),
                payload={
                    "redirect_link": f"{self.site_url}/plugins/servlet/project-config/{project_key}/permissions",
                },
            )
            return [Permission(
                entity_type=EntityType.USER,
                email=jira_email,
                type=PermissionType.READ,
            )]

        # A 403 doesn't say the project grants no one; saving [] would replace its stored access.
        self.logger.warning(
            "⚠️ %s for %s returned %s and the authenticated Jira account email is unknown — "
            "keeping the project's stored access.",
            stage, project_key, status,
        )
        return None

    async def _fetch_project_permission_scheme(
        self,
        project_key: str,
        app_roles_mapping: dict[str, list[dict[str, str]]] = None,
        user_by_account_id: dict[str, "AppUser"] = None
    ) -> Optional[list[Permission]]:
        """
        Fetch permission holders for a project from its Permission Scheme.

        Permission Schemes grant permissions (like BROWSE_PROJECTS) through different holder types:
        - group: Direct group permissions (e.g., "jira-software-users")
        - applicationRole: Product access (e.g., "jira-software") - resolved to associated groups
        - user: Individual user permissions (by accountId/email)
        - anyone: All authenticated users (org-level access)
        - projectRole: Project-specific roles (e.g., "Administrators", "Developers") inside that user or groups in role
        - projectLead: The project's designated lead user
        - sd.customer.portal.only: JSM portal customers (external users)
        - groupCustomField/userCustomField: Dynamic permissions based on issue fields

        Returns the BROWSE holders, or ``None`` when the scheme couldn't be determined due to a
        transient failure (429 after retries / 5xx / parse error). The caller then keeps the
        project's stored access and still syncs its issues. An empty list means the scheme was
        read and legitimately grants BROWSE to no one.
        """
        if await self._copies_no_source_access():
            return []
        permissions: list[Permission] = []
        self._browse_issue_holders.pop(project_key, None)

        try:
            # Step 1: Get the permission scheme assigned to this project (transport + 429 retry)
            scheme_response = await self._call_with_retry(
                lambda ds: ds.get_assigned_permission_scheme(projectKeyOrId=project_key, expand="all"),
                ctx=f"permission scheme for {project_key}",
            )

            if scheme_response.status != HttpStatusCode.OK.value:
                # Only a 403 is a genuine permission problem (the account isn't a project admin) →
                # grant the creator direct BROWSE so the project isn't hidden, and notify. A 401
                # (auth/token) or a 5xx/429 is transient — return None; the caller keeps the
                # project's stored access this run.
                if scheme_response.status == HttpStatusCode.FORBIDDEN.value:
                    return await self._fallback_permissions_for_forbidden_scheme(
                        project_key=project_key,
                        status=scheme_response.status,
                        stage="permission scheme",
                    )
                self.logger.warning(
                    f"⚠️ Could not fetch permission scheme for {project_key} "
                    f"(HTTP {scheme_response.status}); keeping the project's stored access"
                )
                return None

            scheme_data = scheme_response.json()
            scheme_id = scheme_data.get("id")
            # Jira's built-in "Default Permission Scheme" has id 0, so only a missing id counts.
            if scheme_id is None:
                # Without an id the grants URL is malformed and can only fail; skip rather
                # than burn the retry budget. The caller keeps the project's stored access.
                self.logger.warning(
                    f"⚠️ Permission scheme for {project_key} has no id; "
                    "keeping the project's stored access"
                )
                return None

            # Step 2: Get all permission grants in this scheme (transport + 429 retry)
            grants_response = await self._call_with_retry(
                lambda ds: ds.get_permission_scheme_grants(schemeId=scheme_id, expand="all"),
                ctx=f"permission grants (scheme {scheme_id}) for {project_key}",
            )

            if grants_response.status != HttpStatusCode.OK.value:
                # Same rule as the scheme fetch above: 403 → creator-browse fallback + notify;
                # 401/5xx/429 → None; the caller keeps the project's stored access.
                if grants_response.status == HttpStatusCode.FORBIDDEN.value:
                    return await self._fallback_permissions_for_forbidden_scheme(
                        project_key=project_key,
                        status=grants_response.status,
                        stage=f"permission grants (scheme {scheme_id})",
                    )
                self.logger.warning(
                    f"⚠️ Could not fetch permission grants for scheme {scheme_id} "
                    f"({project_key}, HTTP {grants_response.status}); "
                    "keeping the project's stored access"
                )
                return None

            grants_data = grants_response.json()
            permission_grants = grants_data.get("permissions", [])
            self._browse_issue_holders[project_key] = browse_issue_holders(permission_grants)

            # Step 3: Resolve each BROWSE_PROJECTS grant to holders. Isolate per-grant so a
            # single malformed grant can't collapse the whole project's ACL to empty.
            seen_holders: set[str] = set()

            for grant in permission_grants:
                try:
                    permissions.extend(
                        self._permissions_for_browse_grant(
                            grant, project_key, app_roles_mapping or {}, user_by_account_id or {}, seen_holders
                        )
                    )
                except Exception as e:
                    self.logger.warning(
                        f"⚠️ {project_key}: skipping a malformed permission grant: {e}", exc_info=True
                    )
                    continue

            return permissions

        except Exception as e:
            # Couldn't determine the scheme (transport exhaustion / parse error): the caller
            # keeps the project's stored access.
            self.logger.error(f"❌ Error fetching permission scheme for project {project_key}: {e}", exc_info=True)
            return None

    async def _attach_browse_holders(
        self, security: IssueSecurityContext, project_key: str, *, fresh: bool = False,
    ) -> IssueSecurityContext:
        """Give the context the project's per-issue Browse holders: from this sync's scheme read, or
        with ``fresh`` (a reindex between syncs) read now, leaving the sync's copy alone. They stay
        None when the scheme could not be read."""
        if security.enforce:
            holders = await self._read_browse_issue_holders(project_key) if fresh else (
                self._browse_issue_holders.get(project_key)
            )
            security.browse_holders = holders
            security.custom_field_ids = sorted({
                *security.custom_field_ids, *custom_field_ids_from_members({"browse": holders or []}),
            })
            if fresh and holders is None:
                await self._mark_browse_holders_unread(project_key)
        return security

    async def _mark_browse_holders_unread(self, project_key: str) -> None:
        """A reindex that could not read the holders may have dropped a stored reporter or assignee
        grant; the `?` marker makes the next sync that reads them re-read the project's issues."""
        try:
            stored = ((await self._get_project_sync_checkpoint(project_key)) or {}).get("browse_holders") or ""
            if stored not in ("", "?"):
                await self._update_project_sync_checkpoint(project_key, browse_holders="?")
        except Exception as e:
            self.logger.warning("Could not mark the Browse holders of %s unread: %s", project_key, e)

    async def _read_browse_issue_holders(self, project_key: str) -> list[dict[str, Any]] | None:
        try:
            scheme = await self._call_with_retry(
                lambda ds: ds.get_assigned_permission_scheme(projectKeyOrId=project_key),
                ctx=f"permission scheme for {project_key}",
            )
            scheme_id = scheme.json().get("id") if scheme.status == HttpStatusCode.OK.value else None
            if scheme_id is None:
                return None
            grants = await self._call_with_retry(
                lambda ds: ds.get_permission_scheme_grants(schemeId=scheme_id, expand="all"),
                ctx=f"permission grants for {project_key}",
            )
            if grants.status != HttpStatusCode.OK.value:
                return None
            return browse_issue_holders(grants.json().get("permissions", []))
        except Exception as e:
            self.logger.warning("Could not read the permission scheme of %s: %s", project_key, e)
            return None

    def _keeps_stored_grants(self, record: Record) -> bool:
        context = self._issue_security.get(getattr(record, "external_record_group_id", None))
        # Every issue access path sets rewrite_permissions, except the ones that keep the stored grants (holders or
        # a level read that will be retried): those must not reach a full sync's first write, which replaces them.
        return (
            context is not None and context.enforce
            and isinstance(record, TicketRecord) and not getattr(record, "rewrite_permissions", False)
        )

    def _permissions_for_browse_grant(
        self,
        grant: dict[str, Any],
        project_key: str,
        app_roles_mapping: dict[str, list[dict[str, str]]],
        user_by_account_id: dict[str, "AppUser"],
        seen_holders: set,
    ) -> list[Permission]:
        """Resolve a single permission-scheme grant to the BROWSE Permission(s) it implies.

        Only BROWSE_PROJECTS grants produce permissions; anything else returns []. Mutates
        ``seen_holders`` for cross-grant dedup. Kept isolated (per-grant try/except in the
        caller) so one malformed grant can't drop the whole project's ACL.
        """
        grant_permissions: list[Permission] = []

        if grant.get("permission") != BROWSE_PROJECTS_PERMISSION:
            return grant_permissions

        holder = grant.get("holder", {}) or {}
        holder_type = holder.get("type")
        holder_param = holder.get("parameter")
        holder_value = holder.get("value")

        # Dedup identical holders across grants
        holder_key = f"{holder_type}:{holder_value or holder_param}"
        if holder_key in seen_holders:
            return grant_permissions
        seen_holders.add(holder_key)

        if holder_type == HOLDER_TYPE_GROUP and holder_value:
            grant_permissions.append(Permission(
                entity_type=EntityType.GROUP,
                external_id=holder_value,
                type=PermissionType.READ
            ))

        elif holder_type == HOLDER_TYPE_APPLICATION_ROLE:
            role_key = holder_param

            # A bare holder is "Any logged in user": any account that can log in to Jira, i.e.
            # one with product access. That is the union of the groups of every application
            # role, not an ORG grant (an active account without a licence would otherwise see
            # the project). Atlassian notes the scheme does not itself check application access
            # (JRASERVER-68771); accounts without it cannot log in, and portal customers are not
            # synced, so the union is the accounts that can reach the project.
            if role_key:
                role_groups = app_roles_mapping.get(role_key) if app_roles_mapping else None
            else:
                role_groups = [g for groups in (app_roles_mapping or {}).values() for g in groups] or None
                if role_groups is not None and self._app_roles_unread:
                    # The union needs every role, and one could not be read: run_sync keeps
                    # this project's stored access rather than saving a partial list.
                    self._projects_naming_unread_roles.add(project_key)

            if role_groups is not None:
                for group_info in role_groups:
                    group_id = group_info.get("groupId")
                    if group_id:
                        group_key = f"group:{group_id}"
                        if group_key not in seen_holders:
                            seen_holders.add(group_key)
                            grant_permissions.append(Permission(
                                entity_type=EntityType.GROUP,
                                external_id=group_id,
                                type=PermissionType.READ
                            ))
            elif self._app_roles_forbidden and self._authenticated_jira_email:
                # API returned 403 — can't resolve role to groups; grant only the
                # authenticated Jira account instead of over-granting to ORG
                jira_email = self._authenticated_jira_email
                user_key = f"user:{jira_email.lower()}"
                if user_key not in seen_holders:
                    seen_holders.add(user_key)
                    grant_permissions.append(Permission(
                        entity_type=EntityType.USER,
                        email=jira_email,
                        type=PermissionType.READ,
                    ))
                    self.logger.info(
                        "applicationRole '%s' unresolvable (403) — granting Jira account '%s' direct access on %s",
                        role_key, jira_email, project_key
                    )
            elif self._app_roles_unread:
                # The role's groups are unknown, not none: run_sync keeps this project's
                # stored access, or saves what was read when nothing is stored.
                self._projects_naming_unread_roles.add(project_key)
                self.logger.warning(
                    "Cannot resolve applicationRole '%s' for project %s: the application roles "
                    "could not be read",
                    role_key, project_key,
                )
            else:
                self.logger.warning(
                    "Cannot resolve applicationRole '%s' for project %s — skipping",
                    role_key, project_key
                )

        elif holder_type == HOLDER_TYPE_USER and holder_param:
            # holder_param is the accountId; resolve via AppUser map first, fall back to email
            user_data = holder.get("user") or {}
            user_email = user_data.get("emailAddress")

            resolved_email = None
            if user_by_account_id and holder_param in user_by_account_id:
                resolved_email = user_by_account_id[holder_param].email
            elif user_data.get("active") is False:
                # A deactivated account can no longer sign in to Jira; the grant is dormant.
                self.logger.debug(f"  {project_key}: User permission skipped - account '{holder_param}' is inactive")
                return grant_permissions
            elif (
                # Product access unknown (None) is not access: the project's stored access is kept instead.
                user_email and self._user_read_unfinished
                and self._licensed_account_ids is not None and holder_param in self._licensed_account_ids
                and user_data.get("accountType", "atlassian") == "atlassian"
            ):
                # Only a user list cut short leaves out a licensed account. After a complete read,
                # one missing from it has no Jira product access (or is a portal customer), and
                # Jira treats it as anonymous whatever the scheme names.
                resolved_email = user_email

            if resolved_email:
                grant_permissions.append(Permission(
                    entity_type=EntityType.USER,
                    email=resolved_email,
                    type=PermissionType.READ
                ))
            else:
                self.logger.debug(f"  {project_key}: User permission skipped - cannot resolve accountId '{holder_param}'")

        elif holder_type == HOLDER_TYPE_ANYONE:
            # All authenticated users have access — handle public condition
            grant_permissions.append(Permission(
                entity_type=EntityType.ORG,
                external_id=ORG_SCOPE_ANYONE_AUTHENTICATED,
                type=PermissionType.READ
            ))

        elif holder_type == HOLDER_TYPE_PROJECT_ROLE:
            project_role = holder.get("projectRole", {}) or {}
            role_name = project_role.get("name", f"Role_{holder_param}")
            role_id = holder_param or project_role.get("id")

            if role_name == ATLASSIAN_ADDONS_PROJECT_ROLE:
                return grant_permissions

            grant_permissions.append(Permission(
                entity_type=EntityType.ROLE,
                external_id=f"{project_key}_{role_id}",
                type=PermissionType.READ
            ))

        elif holder_type == HOLDER_TYPE_SD_PORTAL_CUSTOMER:
            # JSM Service Desk customers (portal access) — external, not synced
            self.logger.debug(f"  {project_key}: Skipping JSM portal customers (external users, not synced)")

        elif holder_type == HOLDER_TYPE_PROJECT_LEAD:
            grant_permissions.append(Permission(
                entity_type=EntityType.ROLE,
                external_id=f"{project_key}_projectLead",
                type=PermissionType.READ
            ))

        elif holder_type in BROWSE_ISSUE_HOLDER_TYPES:
            # Named per issue: granted on each issue (core/jira_issue_security.py), not here.
            return grant_permissions

        else:
            self.logger.warning(f"⚠️  {project_key}: Unknown holder type '{holder_type}' with param '{holder_param}' - skipping")

        return grant_permissions

    async def _notify_group_sync_failed(self) -> None:
        await self.notify(
            type=NotificationType.CONNECTOR_GROUP_SYNC_ERROR,
            severity=NotificationSeverity.WARNING,
            title=self._notification_title("couldn't sync user groups"),
            message=(
                "Couldn't load groups from Jira. The connector's Jira account needs "
                "the Browse users and groups global permission."
            ),
        )

    async def _process_group(
        self,
        group: dict[str, Any],
        user_by_account_id: dict[str, "AppUser"],
    ) -> Optional[tuple[str, str, Optional[AppUserGroup], Optional[list["AppUser"]]]]:
        """Build an AppUserGroup and resolve its members.

        Returns ``(group_id, group_name, user_group, app_users)`` or ``None``
        when the group is left out on purpose. ``app_users`` (and ``user_group``
        after an error) is ``None`` when the group's members could not be read.
        """
        try:
            group_id = group.get("groupId")
            group_name = group.get("name")

            if not group_id or not group_name:
                return None

            if group_name.startswith("atlassian-addons"):
                self.logger.debug("Skipping system group: %s", group_name)
                return None

            self.logger.debug(f"Processing group: {group_name} ({group_id})")

            user_group = AppUserGroup(
                app_name=self.connector_name,
                connector_id=self.connector_id,
                source_user_group_id=group_id,
                name=group_name,
                org_id=self.data_entities_processor.org_id,
                description=f"Jira user group: {group_name}",
            )

            member_account_ids, members_ok = await self._fetch_group_members(group_id, group_name)
            # Saving the group with no members would replace its stored members, taking away
            # everyone's access through it until a later sync succeeds. Keep what is stored.
            if not members_ok:
                self.logger.warning(
                    f"⚠️ Keeping the stored members of group {group_name}: its member list could not be read"
                )
                return (group_id, group_name, user_group, None)

            app_users: list[AppUser] = []
            skipped_members = 0
            if member_account_ids:
                for account_id in member_account_ids:
                    user = user_by_account_id.get(account_id)
                    if user:
                        app_users.append(user)
                    else:
                        skipped_members += 1

            if skipped_members:
                self.logger.debug(
                    "Group %s: %s member(s) skipped (no AppUser; private email or not in PipesHub)",
                    group_name,
                    skipped_members,
                )

            if app_users:
                self.logger.debug(f"Group {group_name}: {len(app_users)} members")
            else:
                self.logger.debug(f"Group {group_name}: no resolved members")

            return (group_id, group_name, user_group, app_users)

        except Exception as group_error:
            self.logger.error(f"❌ Failed to process group {group.get('name')}: {group_error}")
            # Unknown members, not no members: roles that include this group keep what is stored.
            self._kept_unread_access = True
            return (group.get("groupId"), group.get("name"), None, None)

    async def _sync_user_groups(self, jira_users: list[AppUser]) -> Optional[dict[str, Optional[list[AppUser]]]]:
        """
        Sync user groups and return a mapping of group_id/name -> list of AppUser members.
        This mapping is used to resolve group members for project roles. A group whose
        members could not be read maps to None and is not saved. Returns None when the
        group list itself could not be read in full, so roles can't be resolved this run.
        """
        self._group_ids_by_name = {}
        # A group this read leaves unread is unknown to level readers, though the graph keeps its stored members.
        self._group_members = {}
        if await self._copies_no_source_access():
            return {}
        try:
            self.logger.info("🚀 Starting Jira user group synchronization")

            groups, fetch_failed = await self._fetch_groups()
            if fetch_failed and self._group_fetch_interrupted:
                # Memberships and roles keep their stored members, so a person removed in Jira
                # keeps access until a sync reads the groups again. Say so.
                await self.notify(
                    type=NotificationType.CONNECTOR_GROUP_SYNC_ERROR,
                    severity=NotificationSeverity.WARNING,
                    title=self._notification_title("couldn't read all user groups"),
                    message=(
                        "Jira's group list could not be read in full this sync. Groups and project roles "
                        "keep the members they had; changes made in Jira apply after the next successful sync."
                    ),
                )
            if fetch_failed and not groups:
                # Notify about a missing permission only on a genuine 403; a 401 auth/token
                # failure is already reported by the connection/sync-failed notification.
                if self._group_bulk_forbidden:
                    await self._notify_group_sync_failed()
                return None
            if not groups:
                self.logger.info("ℹ️ No groups found in Jira")
                return {}

            self.logger.info(f"👥 Found {len(groups)} groups. Fetching members...")

            # Create accountId -> AppUser lookup (accountId is always present in group member responses)
            listed, user_by_account_id = self._member_directory(jira_users)

            # Fetch members concurrently (read-only); assemble + single write below.
            results = await self._map_bounded(
                groups, lambda g: self._process_group(g, user_by_account_id)
            )

            user_groups_batch = []
            # Mapping: group_id -> members. By id only: a group's name may equal another group's id. A name resolves to an
            # id through ``group_ids_by_name``.
            groups_members_map: dict[str, Optional[list[AppUser]]] = {}
            group_ids_by_name: dict[str, str] = {}

            for res in results:
                if res is None:
                    continue
                group_id, group_name, user_group, app_users = res
                if group_id and group_name:
                    group_ids_by_name[group_name] = group_id
                if group_id:
                    groups_members_map[group_id] = app_users
                if app_users is None or user_group is None:
                    continue
                # Add group to batch (with or without members)
                user_groups_batch.append((user_group, _listed(app_users, listed)))

            self._group_members = {k: v for k, v in groups_members_map.items() if v is not None}

            if self._user_read_unfinished:
                user_groups_batch = await self._without_stored_members("user_group", user_groups_batch)

            # One transaction per group, as the Confluence connector writes them: a large
            # site's groups in a single transaction (one user lookup per membership) outlive
            # the transaction timeout, and the rollback leaves the connector with no group.
            if user_groups_batch:
                for entry in user_groups_batch:
                    await self.data_entities_processor.on_new_user_groups([entry])
            else:
                self.logger.info("ℹ️ No groups with valid members to sync")

            # A list cut short may miss a group that took a name since: no name resolves then.
            self._group_ids_by_name = {} if fetch_failed else group_ids_by_name
            return None if fetch_failed else groups_members_map

        except StoredAccessReadError:
            raise
        except Exception as e:
            self.logger.error(f"❌ Error syncing user groups: {e}")
            self._note_unread(None)
            await self._notify_group_sync_failed()
            return None

    async def _fetch_groups(self) -> tuple[list[dict[str, Any]], bool]:
        """
        Fetch all Jira groups using the bulk_get_groups API.

        Returns:
            (groups, fetch_failed) — fetch_failed is True when any page of the groups API
            failed (permission/API error), so ``groups`` may be only the pages before it;
            it is False when Jira simply has an empty group list.
        """
        if not self.data_source:
            raise ValueError("DataSource not initialized")

        groups: list[dict[str, Any]] = []
        fetch_failed = False
        # Only a 403 means the account lacks Browse users and groups; a 401 is an auth/token
        # failure reported by the connection/sync-failed notification. Track it so the caller
        # notifies about a missing permission only for a genuine 403.
        self._group_bulk_forbidden = False
        self._group_fetch_interrupted = False
        start_at = 0
        max_results = GROUP_PAGE_SIZE

        while True:
            try:
                response = await self._call_with_retry(
                    lambda ds, start_at=start_at: ds.bulk_get_groups(
                        startAt=start_at,
                        maxResults=max_results
                    ),
                    ctx="listing groups",
                )

                if response.status != HttpStatusCode.OK.value:
                    self.logger.error(f"Failed to fetch groups: {response.text()}")
                    fetch_failed = True
                    self._group_fetch_interrupted = response.status not in (
                        HttpStatusCode.UNAUTHORIZED.value, HttpStatusCode.FORBIDDEN.value,
                    )
                    if not groups:
                        self._group_bulk_forbidden = (
                            response.status == HttpStatusCode.FORBIDDEN.value
                        )
                    # The groups past a refusal (403) are unknown, so every project's stored
                    # grants, the groups and roles they name, every stored group, and each
                    # project's roles are kept.
                    self._note_unread(response.status, ("projects", ""))
                    self._note_unread(response.status, ("user_groups", ""))
                    self._note_unread(response.status, ("project_role_ids", ""))
                    break

                groups_data = response.json()
                batch_groups = groups_data.get("values") if isinstance(groups_data, dict) else None
                if not isinstance(batch_groups, list) or not all(
                    isinstance(row, dict)
                    and isinstance(row.get("groupId"), str) and row["groupId"]
                    and isinstance(row.get("name"), str) and row["name"]
                    for row in batch_groups
                ):
                    # A missing or malformed list, or a malformed row, is not "no more groups":
                    # the groups it leaves out would look gone to a full sync's unlink.
                    self.logger.error(f"❌ Group page at offset {start_at} is not a page of groups")
                    fetch_failed = True
                    self._note_unread(None)
                    break

                if not batch_groups:
                    break

                groups.extend(batch_groups)

                # isLast decides where the list ends; a short page is the end only without it.
                is_last = groups_data.get("isLast")
                if is_last is True or (is_last is None and len(batch_groups) < max_results):
                    break

                start_at += len(batch_groups)

            except Exception as e:
                self.logger.error(f"❌ Error fetching groups at offset {start_at}: {e}")
                fetch_failed = True
                self._note_unread(None)
                break

        self.logger.info(f"👥 Fetched {len(groups)} total groups")
        return groups, fetch_failed

    async def _fetch_project_roles(
        self,
        project_key: str,
        user_by_email: dict[str, "AppUser"],
        user_by_account_id: dict[str, "AppUser"],
        groups_members_map: dict[str, Optional[list["AppUser"]]],
    ) -> tuple[str, list[tuple[AppRole, list["AppUser"]]], bool]:
        """Fetch a project's roles + actors and build ``(AppRole, members)`` tuples.

        Returns ``(project_key, roles, failed)``. A role that includes a group whose
        members could not be read is left out (keeping its stored members) and marks
        the project as failed.
        """
        project_roles: list[tuple[AppRole, list[AppUser]]] = []
        role_skipped = False
        try:
            response = await self._call_with_retry(
                lambda ds: ds.get_project_roles(projectIdOrKey=project_key),
                ctx=f"roles for project {project_key}",
            )

            if response.status != HttpStatusCode.OK.value:
                self.logger.warning(f"⚠️ Failed to fetch roles for project {project_key}: HTTP {response.status}")
                self._note_unread(response.status, ("project_roles", project_key))
                return project_key, project_roles, True

            roles_dict = response.json()
            if not isinstance(roles_dict, dict):
                # Not "no roles": the stored ones would look gone to a full sync's unlink.
                self.logger.warning(f"⚠️ The role list of project {project_key} is not a mapping")
                self._note_unread(None)
                return project_key, project_roles, True

            if not roles_dict:
                self.logger.debug(f"No roles found for project {project_key}")
                return project_key, project_roles, False

            for role_name, role_url in roles_dict.items():
                try:
                    if role_name == ATLASSIAN_ADDONS_PROJECT_ROLE:
                        continue

                    role_id = role_url.rstrip('/').split('/')[-1]
                    self._role_ids[role_name] = role_id

                    role_response = await self._call_with_retry(
                        lambda ds: ds.get_project_role(
                            projectIdOrKey=project_key,
                            id=int(role_id),
                            excludeInactiveUsers=True,
                        ),
                        ctx=f"role {role_name} for project {project_key}",
                    )

                    if role_response.status != HttpStatusCode.OK.value:
                        # The role keeps its stored members; it is not "gone".
                        self.logger.warning(f"  {project_key}: Failed to fetch role {role_name}: {role_response.status}")
                        self._note_unread(role_response.status, ("role", f"{project_key}_{role_id}"))
                        role_skipped = True
                        continue

                    role_data = role_response.json()
                    actors = role_data.get("actors", [])
                    role_name_display = role_data.get("name", role_name)

                    app_role = AppRole(
                        app_name=self.connector_name,
                        connector_id=self.connector_id,
                        source_role_id=f"{project_key}_{role_id}",
                        name=f"{project_key} - {role_name_display}",
                        org_id=self.data_entities_processor.org_id,
                        source_created_at=None,
                        source_updated_at=None,
                    )

                    member_users: list[AppUser] = []
                    unreadable_group: Optional[str] = None

                    for actor in actors:
                        actor_type = actor.get("type", "")

                        if actor_type == ROLE_ACTOR_TYPE_USER:
                            actor_user = actor.get("actorUser", {})
                            account_id = actor_user.get("accountId")
                            email = actor_user.get("emailAddress")

                            user = None
                            if account_id:
                                user = user_by_account_id.get(account_id)
                            if not user and email:
                                user = user_by_email.get(email.lower())

                            if user:
                                member_users.append(user)
                            else:
                                self.logger.debug(
                                    f"  {project_key}/{role_name}: User not found - "
                                    f"accountId={account_id}, email={email}"
                                )

                        elif actor_type == ROLE_ACTOR_TYPE_GROUP:
                            group_name = actor.get("name") or actor.get("displayName")
                            group_id = actor.get("groupId") or self._group_ids_by_name.get(group_name or "")

                            group_members: Optional[list[AppUser]] = []
                            if group_id and group_id in groups_members_map:
                                group_members = groups_members_map[group_id]
                            else:
                                self.logger.debug(
                                    f"  {project_key}/{role_name}: Group actor '{group_name}' "
                                    f"(id: {group_id}) not found in synced groups"
                                )

                            if group_members is None:
                                unreadable_group = group_name or group_id
                                break

                            self.logger.debug(
                                f"  {project_key}/{role_name}: Group actor '{group_name}' (id: {group_id}) "
                                f"found {len(group_members)} members"
                            )
                            member_users.extend(group_members)

                    if unreadable_group:
                        # Saving the role now would drop that group's members from it. The group
                        # read already decided whether this sync is incomplete; the kept role is
                        # noted as seen either way.
                        self.logger.warning(
                            f"  {project_key}: Keeping the stored members of role {role_name}: "
                            f"members of group '{unreadable_group}' could not be read"
                        )
                        self._kept_after_refusal.append(("role", f"{project_key}_{role_id}"))
                        role_skipped = True
                        continue

                    project_roles.append((app_role, member_users))

                except Exception as role_error:
                    self.logger.error(
                        f"  {project_key}: Error processing role {role_name}: {role_error}"
                    )
                    self._note_unread(None)
                    role_skipped = True
                    continue

            return project_key, project_roles, role_skipped

        except Exception as project_error:
            self.logger.error(f"❌ Error syncing roles for project {project_key}: {project_error}")
            self._note_unread(None)
            return project_key, project_roles, True

    async def _sync_project_roles(
        self,
        project_keys: list[str],
        jira_users: list[AppUser],
        groups_members_map: dict[str, Optional[list[AppUser]]] = None
    ) -> None:
        """
        Sync project roles as AppRole entities.
        groups_members_map: Mapping of group_id/name -> list of AppUser members (from _sync_user_groups);
            None marks a group whose members could not be read.
        """
        if not self.data_source:
            raise ValueError("DataSource not initialized")

        if groups_members_map is None:
            groups_members_map = {}
        # Role names from this read only: one an earlier sync read may since belong to another role (renamed).
        self._role_ids = {}
        self._role_members = {}  # a role this read leaves unread is unknown to level readers (``_group_members``)
        if await self._copies_no_source_access():
            return

        self.logger.info(f"🔐 Syncing project roles for {len(project_keys)} projects...")

        # Build email -> AppUser lookup for fast member resolution
        user_by_email: dict[str, AppUser] = {
            user.email.lower(): user
            for user in jira_users
            if user.email
        }

        # Also build accountId -> AppUser lookup (``_member_directory``)
        listed, user_by_account_id = self._member_directory(jira_users)

        roles_to_sync: list[tuple[AppRole, list[AppUser]]] = []
        total_roles = 0
        total_members = 0
        failed_project_keys: list[str] = []

        # Fetch each project's roles concurrently (read-only); batch the single write below.
        role_fetch_results = await self._map_bounded(
            project_keys,
            lambda pk: self._fetch_project_roles(
                pk, user_by_email, user_by_account_id, groups_members_map
            ),
        )
        for project_key, project_roles, failed in role_fetch_results:
            if failed:
                failed_project_keys.append(project_key)
            roles_to_sync.extend(project_roles)
            # The members read, before a short user read keeps a role's stored ones in the graph.
            self._role_members.update({role.source_role_id: members for role, members in project_roles})
            total_roles += len(project_roles)
            total_members += sum(len(members) for _, members in project_roles)

        if failed_project_keys:
            preview = ", ".join(failed_project_keys[:10])
            if len(failed_project_keys) > 10:
                preview = f"{preview}, and {len(failed_project_keys) - 10} more"
            self.logger.warning(
                "Project role sync failed for %s/%s projects: %s",
                len(failed_project_keys), len(project_keys), preview,
            )
            await self.notify(
                type=NotificationType.CONNECTOR_ROLE_SYNC_ERROR,
                severity=NotificationSeverity.WARNING,
                title=self._notification_title("couldn't sync project roles"),
                message=(
                    f"Couldn't sync roles for: {preview}. "
                    "This is usually because the connector's Jira account lacks Administer Projects "
                    "on those projects, but can also be temporary rate limiting — existing roles are "
                    "preserved and will retry next sync."
                ),
            )

        if self._user_read_unfinished:
            roles_to_sync = await self._without_stored_members("role", roles_to_sync)

        # Step 4: Sync all roles in batch
        if roles_to_sync:
            await self.data_entities_processor.on_new_app_roles([(role, _listed(m, listed)) for role, m in roles_to_sync])
            self.logger.info(
                f"✅ Synced {total_roles} project roles with {total_members} direct user members"
            )
        else:
            self.logger.info("ℹ️ No project roles to sync")

    async def _sync_project_lead_roles(
        self,
        raw_projects: list[dict[str, Any]],
        jira_users: list[AppUser]
    ) -> None:
        """
        Sync project lead as AppRole for each project.
        """
        if await self._copies_no_source_access():
            return

        # Build accountId -> AppUser lookup (``_member_directory``)
        listed, user_by_account_id = self._member_directory(jira_users)

        lead_roles_to_sync: list[tuple[AppRole, list[AppUser]]] = []
        # Lead role id -> (project key, project id) for leads the short user list did not have.
        unresolved_leads: dict[str, tuple[str, Optional[str]]] = {}
        total_leads = 0

        # Iterate through raw project data already fetched with lead
        for project in raw_projects:
            try:
                project_key = project.get("key")
                lead_data = project.get("lead")

                # Create AppRole for project lead (even if no lead exists - to clean up old edges)
                # Extract project timestamps if available
                project_created = project.get("createdAt")
                project_updated = project.get("updatedAt")

                app_role = AppRole(
                    app_name=self.connector_name,
                    connector_id=self.connector_id,
                    source_role_id=f"{project_key}_projectLead",
                    name=f"{project_key} - Project Lead",
                    org_id=self.data_entities_processor.org_id,
                    source_created_at=self._parse_jira_timestamp(project_created) if project_created else None,
                    source_updated_at=self._parse_jira_timestamp(project_updated) if project_updated else None
                )

                # Determine lead user (if any)
                lead_user = None
                if lead_data:
                    lead_account_id = lead_data.get("accountId")
                    lead_display_name = lead_data.get("displayName", "Unknown")

                    if lead_account_id:
                        # Find the lead user in synced users
                        lead_user = user_by_account_id.get(lead_account_id)

                        if not lead_user and self._user_read_unfinished:
                            unresolved_leads[app_role.source_role_id] = (project_key, project.get("id"))
                        if not lead_user:
                            self.logger.warning(f"Project lead {lead_display_name} not found in synced users for {project_key}")
                    else:
                        self.logger.warning(f"No accountId for project lead in {project_key}")
                else:
                    self.logger.debug(f"No lead for project {project_key} - syncing role to clean up old edges")

                # Always sync the role (even with empty members list) to ensure old edges are deleted
                members = [lead_user] if lead_user else []
                lead_roles_to_sync.append((app_role, members))
                total_leads += 1


            except Exception as lead_error:
                self._kept_unread_access = True
                self.logger.error(f"Error processing lead for project {project.get('key')}: {lead_error}")
                continue

        # A lead not read (its project failed, or the short user list lacks them) is unknown to level readers.
        self._role_members = {k: v for k, v in self._role_members.items() if not k.endswith("_projectLead")}
        self._role_members.update({role.source_role_id: members for role, members in lead_roles_to_sync
                                   if role.source_role_id not in unresolved_leads})
        if unresolved_leads:
            lead_roles_to_sync = await self._without_unresolved_stored_leads(lead_roles_to_sync, unresolved_leads)

        # Sync all project lead roles in batch
        if lead_roles_to_sync:
            await self.data_entities_processor.on_new_app_roles([(role, _listed(m, listed)) for role, m in lead_roles_to_sync])
        else:
            self.logger.info("No project leads to sync")

    async def _load_issue_security(self, project_key: str, *, cached: bool = True) -> IssueSecurityContext:
        """Read the project's issue security scheme and its level members.

        Two calls per project, and one members read per distinct scheme per sync. A read that
        fails leaves ``knowledge`` UNKNOWN (``defer_checkpoint`` when it may succeed on retry),
        so a leveled issue is closed rather than opened to the project. ``cached=False`` reads
        the members afresh and leaves the sync's cache alone (the reindex path runs between syncs).
        """
        if not self._enforces_issue_security:
            return IssueSecurityContext(knowledge=SchemeKnowledge.NONE, enforce=False)
        if await self._copies_no_source_access():
            return IssueSecurityContext(knowledge=SchemeKnowledge.NONE, enforce=False)
        response = await self._call_with_retry(
            lambda ds: ds.get_project_issue_security_scheme(projectKeyOrId=project_key),
            ctx=f"reading the issue security scheme of {project_key}",
        )
        try:
            body = response.json()
        except Exception:
            body = None
        knowledge, scheme_id = classify_scheme_response(response.status, body)
        context = IssueSecurityContext(
            knowledge=knowledge,
            scheme_id=scheme_id,
            scheme_forbidden=response.status == HttpStatusCode.FORBIDDEN.value,
            defer_checkpoint=http_status_is_transient(response.status),
            read_status=response.status if knowledge == SchemeKnowledge.UNKNOWN else None,
            scope_refused=knowledge == SchemeKnowledge.UNKNOWN and refuses_a_scope(body),
        )
        if knowledge != SchemeKnowledge.PRESENT or scheme_id is None:
            return context
        context.level_names = {
            str(level["id"]): str(level.get("name") or level["id"])
            for level in body.get("levels") or [] if isinstance(level, dict) and level.get("id") is not None
        } if isinstance(body.get("levels"), list) else {}
        if scheme_has_no_levels(body):
            context.members_readable = True
            return context
        members = self._scheme_members.get(scheme_id) if cached else None
        if members is None:
            refused: list[bool] = []

            async def fetch_page(start_at: int) -> tuple[int, dict[str, Any] | None]:
                resp = await self._call_with_retry(
                    lambda ds: ds.get_security_level_members(
                        schemeId=[scheme_id], startAt=str(start_at), maxResults="100",
                        expand="user,group,projectRole",
                    ),
                    ctx=f"reading issue security level members of scheme {scheme_id}",
                )
                page = self._safe_json_parse(resp, "issue security level members")
                refused.append(refuses_a_scope(page))
                return resp.status, page

            status, values = await collect_paged_values(fetch_page)
            if values is None:
                context.defer_checkpoint = http_status_is_transient(status)
                context.read_status = status
                context.scope_refused = bool(refused and refused[-1])
                return context  # members_readable stays False: leveled issues are closed
            members = (True, index_members(values))
            if cached:
                self._scheme_members[scheme_id] = members
        context.members_readable, context.members_by_level = members
        context.custom_field_ids = custom_field_ids_from_members(context.members_by_level)
        return context

    async def _warn_unreadable_security(self) -> None:
        """One warning per reason for the projects whose issue security levels could not be
        applied this sync, and one notification for the connector's owner."""
        lines: list[str] = []
        for reason, effect, project_keys, line in unreadable_security_notices(
            self._unreadable_security, self._never_tightened_security,
        ):
            self.logger.warning(
                "Issues with a security level in %s project(s) %s until their levels can be read; %s: %s",
                len(project_keys), effect, UNREADABLE_REASONS[reason], ", ".join(project_keys),
            )
            lines.append(line)
        if lines:
            await self.notify(
                type=NotificationType.CONNECTOR_WARNING,
                severity=NotificationSeverity.WARNING,
                title=self._notification_title("can't read issue security levels"),
                message=" ".join(lines),
            )

    async def _read_level_roles(self) -> None:
        """The role list security levels apply this sync (``roles_for_levels``): the one read, or the last one kept."""
        read = not (self._app_roles_forbidden or self._app_roles_unread)
        self._level_roles, expired = await roles_for_levels(
            self.issues_sync_point, self._app_roles_mapping, read=read, now_ms=get_epoch_timestamp_in_ms(),
            max_age_ms=self.ROLE_LIST_MAX_AGE_MS,
        )
        if expired:
            await self.notify(
                type=NotificationType.CONNECTOR_WARNING,
                severity=NotificationSeverity.WARNING,
                title=self._notification_title("cannot read Jira roles"),
                message="Jira roles unreadable: grant the connector account Jira admin to restore role-based access.",
                payload={"redirect_link": None},
            )

    def _level_readers(
        self, security: IssueSecurityContext, project_key: str, users: dict[str, AppUser],
    ) -> dict[str, frozenset[tuple[str, str]] | None]:
        """Each level's readers through its same-on-every-issue holders, from the members last saved."""
        out: dict[str, frozenset[tuple[str, str]] | None] = {}
        for level_id, holders in security.members_by_level.items():
            grants, skipped = level_group_grants(
                self._by_group_id(holders), project_key=project_key, user_by_id=users, app_roles_mapping=self._level_roles_by_id())
            # A user holder outside the directory reads nothing, so it leaves the others known; any other holder skipped
            # (an unmapped application role) may hold anyone.
            out[level_id] = None if set(skipped) - {HOLDER_USER} else level_readers(
                grants, self._group_members, self._role_members)
        return out

    def _by_group_id(self, holders: list[Any]) -> list[Any]:
        """A level's holders with each group named only by its name resolved to its id (``_group_ids_by_name``): members are
        held by group id, and a name may equal another group's id. One that does not resolve names no group read."""
        out = []
        for holder in holders:
            if isinstance(holder, dict) and holder.get("type") == HOLDER_GROUP and not holder.get("value"):
                name = str(holder.get("parameter") or "")
                out.append({**holder, "value": self._group_ids_by_name.get(name) or f"{_UNRESOLVED_GROUP}{name}"})
            else:
                out.append(holder)
        return out

    def _level_roles_by_id(self) -> dict[str, list[dict[str, Any]]]:
        """``_level_roles`` with each group named only by its name resolved to its id, as ``_by_group_id``."""
        return {key: [g if g.get("groupId") else {**g, "groupId": self._group_ids_by_name.get(str(g.get("name") or ""))
                                                  or f"{_UNRESOLVED_GROUP}{g.get('name')}"} for g in groups]
                for key, groups in self._level_roles.items()}

    async def _reindex_level_readers(
        self, security: IssueSecurityContext, project_key: str, synced: dict[str, AppUser],
    ) -> dict[str, frozenset[tuple[str, str]] | None]:
        """``_level_readers`` for a reindex, from members read now: the sync's are as old as the last sync, and a person Jira
        removed since must not read an issue alone through them. The groups the levels name are read again, at most
        ``REINDEX_GROUP_READS`` per project, from the accounts a sync would hold (``_member_directory``). A role or a project
        lead, one not read in full, or one past the cap is unknown: its level grants no one alone (``LEVEL_MEMBERS_UNREAD``),
        and the issue waits for the next sync. The sync's member maps are left as they are."""
        users = {**{u.source_user_id: u for u in self._listed_without_email() if u.source_user_id}, **synced}
        groups: dict[str, list[AppUser]] = {}
        tried: set[str] = set()
        out: dict[str, frozenset[tuple[str, str]] | None] = {}
        for level_id, holders in security.members_by_level.items():
            grants, skipped = level_group_grants(
                self._by_group_id(holders), project_key=project_key, user_by_id=self._grant_directory(synced),
                app_roles_mapping=self._level_roles_by_id())
            if set(skipped) - {HOLDER_USER}:
                out[level_id] = None
                continue
            for grant in grants:
                group_id = str(grant.external_id)
                if (grant.entity_type != EntityType.GROUP or group_id in tried or len(tried) >= REINDEX_GROUP_READS
                        or group_id.startswith(_UNRESOLVED_GROUP)):
                    continue
                tried.add(group_id)
                try:
                    ids, ok = await self._fetch_group_members(group_id, group_id, note=False)
                except Exception as e:
                    self.logger.warning(f"Could not read the members of group {group_id} for a reindex: {e}")
                    ok = False
                if ok:
                    groups[group_id] = [users[i] for i in ids if i in users]
            out[level_id] = level_readers(grants, groups, {})
        return out

    def _plan_level_reread(
        self, security: IssueSecurityContext, stored: dict[str, str] | None, *, is_new_project: bool,
    ) -> tuple[str | None, list[str] | None, dict[str, str]]:
        return plan_level_reread(
            security, stored, is_new_project=is_new_project, app_roles_mapping=self._level_roles, split=True,
        )

    async def _write_level_groups(
        self, project_id: str, project_key: str, security: IssueSecurityContext, level_ids: list[str],
        user_by_account_id: dict[str, AppUser],
    ) -> None:
        """Write the record group of each level: its holders that name the same people on every
        issue, once (``level_group_grants``). A level the scheme no longer lists is written with
        none, so its issues close at once. One write per level, whatever the number of its issues."""
        groups: list[tuple[RecordGroup, list[Permission]]] = []
        for level_id in level_ids:
            grants, skipped = level_group_grants(
                security.members_by_level.get(level_id, []), project_key=project_key,
                user_by_id=user_by_account_id, app_roles_mapping=self._level_roles,
            )
            if skipped:
                self.logger.warning(
                    f"Security level {level_id} of {project_key}: holders of type {skipped} were not granted")
                external_id = level_group_external_id(project_id, level_id)
                # Only an unfinished user read leaves a user holder unknown; a role holder resolves (or not) from the role
                # list the levels apply (``roles_for_levels``), so past its bound the group drops the roles' groups.
                if self._user_read_unfinished and HOLDER_USER in skipped and await \
                        self.data_entities_processor.get_record_group_by_external_id(self.connector_id, external_id) is not None:
                    # A holder unread is not out of the level: the stored group keeps its grants, seen by a full sync.
                    await self.data_entities_processor.note_kept_record_group(self.connector_id, external_id)
                    continue
            groups.append((RecordGroup(
                id=str(uuid4()),
                org_id=self.data_entities_processor.org_id,
                external_group_id=level_group_external_id(project_id, level_id),
                parent_external_group_id=project_id,
                connector_id=self.connector_id,
                connector_name=self.connector_name,
                name=f"Security level: {security.level_names.get(level_id, level_id)}",
                group_type=RecordGroupType.PROJECT,
                # RESTRICTED under its project: a reader needs a grant here and to browse the project.
                inherit_permissions=True,
                access_rule=AccessRule.RESTRICTED,
                access_only=True,  # its issues are listed where they are filed, not under it
            ), grants))
        if groups:
            await self.data_entities_processor.on_new_record_groups(groups)

    async def _ensure_level_group(
        self, project_id: str, project_key: str, security: IssueSecurityContext, level_id: str | None,
        user_by_account_id: dict[str, AppUser],
    ) -> list[str]:
        """Outside a sync: write the level's record group with the holders read now, as a sync does (a level added
        since, or holders changed since: the issue inherits the group). Returns the holder types its grants would
        skip, so the caller keeps the stored record rather than write a partial audience (application roles are
        read by the sync)."""
        _, skipped = level_group_grants(
            security.members_by_level.get(level_id or "", []), project_key=project_key,
            user_by_id=user_by_account_id, app_roles_mapping=self._level_roles,
        )
        if not skipped and level_id:
            await self._write_level_groups(project_id, project_key, security, [level_id], user_by_account_id)
        return skipped

    async def _record_security_levels(
        self, project_key: str, levels: dict[str, str], *, force: bool = False,
    ) -> None:
        """Save the level map only after a clean walk, so a failed one repeats next sync.
        ``force`` saves a retry marker, which only ever asks for more re-reading."""
        if force or not self._issue_build_failures:
            await self._update_project_sync_checkpoint(
                project_key, security_levels=json.dumps(levels, sort_keys=True)
            )

    def _product_access_groups(self) -> list[str] | None:
        """Ids of the groups that give Jira product access, or None when they could not be read in full."""
        if self._app_roles_unread or self._app_roles_forbidden or not self._app_roles_mapping:
            return None
        return sorted({str(g["groupId"]) for groups in self._app_roles_mapping.values() for g in groups if g.get("groupId")})

    async def _restricted_comment_records(
        self, issue_record: TicketRecord, comments: list[Any], project_key: str, issue_key: str, *, stored: bool,
    ) -> tuple[list[tuple[Record, list[Permission], bool]], list[str], bool]:
        """A record under the issue for each comment only some of its readers may read, the ids
        of the stored ones that are gone or now public (the issue's own content shows those), with
        the files filed under them (a file still on the issue is filed again by this read), and
        whether a comment's role, group or product access could not be resolved.

        Each is PARENT_AND_GRANT under the issue: read by those who read the issue, by any path (a
        reporter who reads it only as its reporter, too), and are named by its ``visibility`` (or, for
        an internal note, have product access), as Jira shows it.
        """
        stored_by_id = {
            r.external_record_id: r for r in await self._stored_comment_records(issue_record.external_record_id)
        } if stored else {}
        product_access_groups = self._product_access_groups()
        out: list[tuple[Record, list[Permission], bool]] = []
        unresolved = False
        for comment in comments:
            if not isinstance(comment, dict) or not comment.get("id") or not is_restricted_comment(comment):
                continue
            external_id = f"{COMMENT_RECORD_PREFIX}{comment['id']}"
            existing = stored_by_id.pop(external_id, None)
            created = self._parse_jira_timestamp(comment.get("created") or "")
            updated = self._parse_jira_timestamp(comment.get("updated") or "") or created
            changed = existing is None or existing.source_updated_at != updated
            author = comment.get("author") if isinstance(comment.get("author"), dict) else {}
            record = CommentRecord(
                id=existing.id if existing else new_record_id(self.connector_id, external_id),
                org_id=self.data_entities_processor.org_id,
                record_name=f"Comment by {author.get('displayName') or 'Unknown'} on {issue_key}",
                record_type=RecordType.COMMENT,
                external_record_id=external_id,
                external_revision_id=str(updated) if updated else None,
                version=0 if existing is None else existing.version + int(changed),
                origin=OriginTypes.CONNECTOR,
                connector_name=self.connector_name,
                connector_id=self.connector_id,
                mime_type=MimeTypes.BLOCKS.value,
                parent_external_record_id=issue_record.external_record_id,
                parent_record_type=RecordType.TICKET,
                external_record_group_id=issue_record.external_record_group_id,
                record_group_type=RecordGroupType.PROJECT,
                source_created_at=created,
                source_updated_at=updated,
                created_at=created,
                updated_at=updated,
                weburl=f"{issue_record.weburl}?focusedCommentId={comment['id']}" if issue_record.weburl else None,
                author_source_id=str(author.get("accountId") or ""),
                preview_renderable=False,
                is_dependent_node=True,
                parent_node_id=issue_record.id,
                inherit_permissions=True,
                rewrite_permissions=True,
                access_rule=AccessRule.PARENT_AND_GRANT,
            )
            record.indexing_status = issue_record.indexing_status
            grants = restricted_comment_grants(
                comment, project_key=project_key, role_ids=self._role_ids, group_ids_by_name=self._group_ids_by_name,
                product_access_groups=product_access_groups,
            )
            if not grants:
                unresolved = True
                self.logger.warning(
                    "Comment %s of issue %s names no role, group or product access that could be read; "
                    "no one reads it until a sync resolves it", comment["id"], issue_key,
                )
            out.append((record, grants, changed))
        return out, await self._with_their_files(list(stored_by_id.values())), unresolved
