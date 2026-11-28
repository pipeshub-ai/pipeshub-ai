# Copyright (c) 2026 PipesHub Inc.
# Licensed under the PipesHub Enterprise License. See the LICENSE file in this
# ee folder. Free for development, testing and evaluation. Production use needs
# a PipesHub licence key.

"""Jira Data Center: reading who may see what from Jira.

Project permission schemes, groups with their members, project roles and leads, and issue security levels.
The free connector keeps listing and syncing projects and issues and calls these methods; without a licence
key a company-wide connector reads none of this (the processor's licence gate).
"""

import json
from collections.abc import Awaitable, Callable
from typing import Any, NamedTuple, Optional

import httpx  # type: ignore

from app.config.constants.arangodb import (
    AccessRule,
)
from app.config.constants.http_status_code import HttpStatusCode
from app.connectors.sources.atlassian.core.read_outcome import (
    StoredAccessReadError,
)
from app.ee.permission_sync.jira_issue_security import (
    BROWSE_ISSUE_HOLDER_TYPES,
    UNREADABLE_REASONS,
    IssueSecurityContext,
    SchemeKnowledge,
    browse_issue_holders,
    classify_scheme_response,
    collect_paged_values,
    custom_field_ids_from_members,
    http_status_is_transient,
    index_members,
    pre_walk_marker,
    refuses_a_scope,
    roles_for_levels,
    scheme_has_no_levels,
    unreadable_security_notices,
)
from app.models.entities import (
    AppRole,
    AppUser,
    AppUserGroup,
    Record,
    TicketRecord,
    get_epoch_timestamp_in_ms,
)
from app.models.permission import EntityType, Permission, PermissionType
from app.services.notification.types import (
    NotificationSeverity,
    NotificationType,
)
from app.sources.client.http.http_response import HTTPResponse
from app.sources.client.http.http_retry import call_with_retry
from app.sources.external.jira.jira import JiraDataSource


def _browse_holders_of(answer: object) -> list[dict[str, Any]] | None:
    """The per-issue Browse holders of a permission grants answer; None unless ``permissions`` is a list of grants,
    since a malformed answer is not "no holders" (an empty list is, and drops their grants)."""
    grants = answer.get("permissions") if isinstance(answer, dict) else None
    if not isinstance(grants, list) or not all(isinstance(grant, dict) for grant in grants):
        return None
    return browse_issue_holders(grants)


GROUPS_PICKER_MAX: int = 1000  # maxResults for GET /rest/api/2/groups/picker


class GroupPickerPage(NamedTuple):
    """One ``/groups/picker`` answer. ``cut_off`` when Jira matched more groups than it returned."""

    groups: list[dict[str, Any]]
    cut_off: bool = False


class GroupMemberships(NamedTuple):
    """Group name -> members for role resolution. None marks a group whose members are unknown.

    ``cut_off`` when the group list stopped at the picker limit: a group missing from
    ``members`` may then exist past it, so its members are unknown too.
    """

    members: dict[str, Optional[list[AppUser]]]
    cut_off: bool = False


def _normalize_jira_dc_group_row(raw: dict[str, Any]) -> dict[str, Any] | None:
    """Normalize a DC groups/picker row to ``{name, groupId}``, both the group's name. Data Center has no other group id:
    GroupSuggestionBean holds ``name``, ``html`` and ``labels``, GET group/member takes "groupname: The group name", and
    level and permission holders name a group by name (10.2, 10.7 and 11.3 specifications,
    https://dac-static.atlassian.com/server/jira/platform/jira_software_dc_11003_swagger.v3.json). An id in a row would be a
    second key space, in which one group's id can be another group's name."""
    name = raw.get("name")
    if not name:
        return None
    return {"name": str(name), "groupId": str(name)}


class JiraDataCenterPermissionsMixin:
    """The permission-reading half of ``JiraDataCenterConnector``."""

    async def _copies_no_source_access(self) -> bool:
        """Without a licence key a company-wide connector reads no access from Jira. The processor then keeps every
        stored grant, group and role whatever is handed to it (``skips_permission_writes``), so the gated answers
        below are empty, not None: None is this connector's "could not read", which leaves the project, space or
        page unsaved or skipped, as unreadable, on every run."""
        return await self.data_entities_processor.skips_permission_writes(self.connector_id)

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

    async def _fallback_permissions_for_forbidden_scheme(
        self,
        project_key: str,
        status: int,
        stage: str,
    ) -> Optional[list[Permission]]:
        """Build a single-user BROWSE permission for the configuring user when
        the permission-scheme endpoints return 401/403 for this project.

        Mirrors the ``_app_roles_forbidden`` fallback in
        ``_fetch_application_roles_to_groups_mapping``: rather than indexing
        the project with no ACLs (which would silently hide it from search
        results across the org), give the configuring user direct READ access
        so they can still discover their own data.
        """
        if self.creator_email:
            self.logger.warning(
                "⚠️ %s for %s returned %s — configuring user lacks Administer "
                "Projects. Granting configuring user '%s' direct BROWSE access "
                "instead of dropping all ACLs for this project.",
                stage, project_key, status, self.creator_email,
            )
            await self.notify(
                type=NotificationType.CONNECTOR_WARNING,
                severity=NotificationSeverity.WARNING,
                title=self._notification_title(f"couldn't read permissions for project {project_key}"),
                message=(
                    f"The connector's Jira account can't read the permission scheme for "
                    f"{project_key}. Grant it project admin access; until then, only the "
                    "connector owner can access this project's issues in PipesHub."
                ),
            )
            return [Permission(
                entity_type=EntityType.USER,
                email=self.creator_email,
                type=PermissionType.READ,
            )]

        # A 403 doesn't say the project grants no one; saving [] would replace its stored access.
        self.logger.warning(
            "⚠️ %s for %s returned %s and no configuring user email resolved — "
            "keeping the project's stored access.",
            stage, project_key, status,
        )
        return None

    async def _fetch_project_permission_scheme(
        self,
        project_key: str,
        app_roles_mapping: dict[str, list[dict[str, str]]] = None,
        user_by_key: dict[str, "AppUser"] = None
    ) -> Optional[list[Permission]]:
        """
        Fetch permission holders for a project from its Permission Scheme (Data Center).

        Two calls, uniform across builds: ``GET /project/{key}/permissionscheme``
        (no expand) for the scheme id, then ``GET /permissionscheme/{schemeId}/permission``
        for the grants. ``?expand=all`` on the project endpoint is avoided because it
        500s on some DC/Server builds (internal RequestScope NPE from lazy holder
        expansion); the standalone grants endpoint expands no holders inline.

        Permission Schemes grant permissions (like BROWSE_PROJECTS) through different holder types:
        - group: Direct group permissions (e.g., "jira-software-users")
        - applicationRole: Product access (e.g., "jira-software") - resolved to associated groups
        - user: Individual user permissions (by accountId/email)
        - anyone: All authenticated users (org-level access)
        - projectRole: Project-specific roles (e.g., "Administrators", "Developers") inside that user or groups in role
        - projectLead: The project's designated lead user
        - sd.customer.portal.only: JSM portal customers (external users)
        - groupCustomField/userCustomField: Dynamic permissions based on issue fields

        Returns None when the scheme could not be read (on a 401/403, only when the
        configuring user's email can't be resolved for the fallback grant), so the caller
        keeps the project's stored access instead of replacing it.
        """
        if await self._copies_no_source_access():
            return []
        permissions: list[Permission] = []
        self._browse_issue_holders.pop(project_key, None)

        try:
            datasource = await self._get_fresh_datasource()

            # Resolve the scheme in two steps instead of one ``?expand=all`` call.
            # ``GET /project/{key}/permissionscheme?expand=all`` 500s on some
            # DC/Server builds (internal RequestScope NPE — holders are expanded
            # lazily during serialization, after the request scope is torn down).
            # A bare scheme lookup + the standalone grants endpoint expands no
            # holders inline, so it works uniformly across builds.

            # Step 1: the scheme id assigned to this project (no expand).
            scheme_response = await datasource.get_assigned_permission_scheme_v2(
                projectKeyOrId=project_key,
            )

            if scheme_response.status != HttpStatusCode.OK.value:
                # ``GET /project/{key}/permissionscheme`` requires *Administer
                # Projects* (or global *Administer Jira*). Non-admin sync users
                # get a 401/403 with ``"You cannot edit the configuration of
                # this project."`` — same shape as the applicationroles 403
                # handled in ``_fetch_application_roles_to_groups_mapping``.
                # Mirror that fallback: rather than silently dropping all
                # BROWSE holders (which leaves the project indexed with empty
                # ACLs), grant the configuring user direct BROWSE so they can
                # at least see their own project content.
                if scheme_response.status in (
                    HttpStatusCode.UNAUTHORIZED.value,
                    HttpStatusCode.FORBIDDEN.value,
                ):
                    return await self._fallback_permissions_for_forbidden_scheme(
                        project_key=project_key,
                        status=scheme_response.status,
                        stage="permission scheme",
                    )
                self.logger.warning(f"⚠️ Failed to fetch permission scheme for {project_key}: {scheme_response.text()}")
                return None

            scheme_id = scheme_response.json().get("id")
            # Jira's built-in "Default Permission Scheme" has id 0, so only a missing id counts.
            if scheme_id is None:
                self.logger.warning(
                    "⚠️ Permission scheme for %s has no id — cannot fetch grants",
                    project_key,
                )
                return None

            # Step 2: grants from the standalone endpoint. No expand — the grant
            # ``holder.parameter`` (group name / user key / role id) is always
            # present, user emails resolve via ``user_by_key``, and expanding
            # holders inline is what triggers the NPE above.
            grants_response = await datasource.get_permission_scheme_grants_v2(
                schemeId=scheme_id,
            )

            if grants_response.status != HttpStatusCode.OK.value:
                if grants_response.status in (
                    HttpStatusCode.UNAUTHORIZED.value,
                    HttpStatusCode.FORBIDDEN.value,
                ):
                    return await self._fallback_permissions_for_forbidden_scheme(
                        project_key=project_key,
                        status=grants_response.status,
                        stage=f"permission grants (scheme {scheme_id})",
                    )
                self.logger.warning(
                    "⚠️ Failed to fetch permission grants for scheme %s: %s",
                    scheme_id,
                    grants_response.text(),
                )
                return None

            grants_answer = grants_response.json()
            holders = _browse_holders_of(grants_answer)
            if holders is None:
                # Not a PermissionGrantsBean: the project's stored access and holders stay as they are.
                self.logger.warning("Permission grants of scheme %s are not a list of grants; keeping %s as stored",
                                    scheme_id, project_key)
                return None
            self._browse_issue_holders[project_key] = holders
            permission_grants = grants_answer["permissions"]

            # Step 3: Filter for BROWSE_PROJECTS permission (determines who can see the project)
            relevant_permission_types = ["BROWSE_PROJECTS"]

            seen_holders = set()

            for grant in permission_grants:
                permission_name = grant.get("permission")

                if permission_name not in relevant_permission_types:
                    continue

                holder = grant.get("holder", {})
                holder_type = holder.get("type")
                holder_param = holder.get("parameter")
                holder_value = holder.get("value")

                # Create unique key for deduplication
                holder_key = f"{holder_type}:{holder_value or holder_param}"
                if holder_key in seen_holders:
                    continue
                seen_holders.add(holder_key)

                # Process different holder types and create Permission objects
                if holder_type == "group":
                    # DC uses ``parameter``; Cloud often uses ``value`` — accept either.
                    group_id = holder_param or holder_value
                    if group_id:
                        permissions.append(Permission(
                            entity_type=EntityType.GROUP,
                            external_id=group_id,
                            type=PermissionType.READ
                        ))

                elif holder_type == "applicationRole":
                    role_key = holder_param

                    if role_key and app_roles_mapping and role_key in app_roles_mapping:
                        role_groups = app_roles_mapping[role_key]
                        for group_info in role_groups:
                            # The group's name, as ``AppUserGroup.source_user_group_id``
                            # (``_normalize_jira_dc_group_row``), so ``on_new_record_groups`` resolves the edge.
                            group_key_value = group_info.get("groupId") or group_info.get("name")
                            if group_key_value:
                                group_key = f"group:{group_key_value}"
                                if group_key not in seen_holders:
                                    seen_holders.add(group_key)
                                    permissions.append(Permission(
                                        entity_type=EntityType.GROUP,
                                        external_id=group_key_value,
                                        type=PermissionType.READ
                                    ))
                    elif not role_key:
                        # Bare applicationRole (no parameter) = "any licensed user"
                        permissions.append(Permission(
                            entity_type=EntityType.ORG,
                            external_id="all_licensed_users",
                            type=PermissionType.READ
                        ))
                    elif self._app_roles_forbidden and self.creator_email:
                        # API returned 403 — can't resolve role to groups,
                        # grant only the configuring user instead of over-granting to ORG
                        user_key = f"user:{self.creator_email.lower()}"
                        if user_key not in seen_holders:
                            seen_holders.add(user_key)
                            permissions.append(Permission(
                                entity_type=EntityType.USER,
                                email=self.creator_email,
                                type=PermissionType.READ,
                            ))
                            self.logger.info(
                                "applicationRole '%s' unresolvable (403) — granting configuring user '%s' direct access on %s",
                                role_key, self.creator_email, project_key
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

                elif holder_type == "user" and holder_param:
                    # Only an account in the synced directory is granted, never one an inline holder email names: that
                    # may be an account withdrawn this sync. The holder's "user" bean is an expandable element
                    # (https://docs.atlassian.com/software/jira/docs/api/REST/9.12.0/#api/2/permissionscheme-getPermissionSchemeGrants,
                    # "expand":"user,group,projectRole,field,all"), and this read does not expand. The parameter is the
                    # user key on every supported release, never a username (which another account may hold once
                    # renamed): Jira stores "the userkey, not the username"
                    # (https://developer.atlassian.com/server/jira/platform/renamable-users-in-jira-6-0/), and the
                    # single-user grant reads its argument as a "user key string" in the Java API of 8.5.0, 9.12.0,
                    # 10.3.0 and 11.0.0 (e.g. https://docs.atlassian.com/software/jira/docs/api/11.0.0/com/atlassian/jira/security/type/SingleUser.html).
                    listed = (user_by_key or {}).get(holder_param)
                    resolved_email = listed.email if listed else None
                    if resolved_email:
                        permissions.append(Permission(
                            entity_type=EntityType.USER,
                            email=resolved_email,
                            type=PermissionType.READ
                        ))
                    else:
                        self.logger.debug(f"  {project_key}: User permission skipped - cannot resolve key '{holder_param}'")

                elif holder_type == "anyone":
                    # All authenticated users have access handle public condition
                    permissions.append(Permission(
                        entity_type=EntityType.ORG,
                        external_id="anyone_authenticated",
                        type=PermissionType.READ
                    ))

                elif holder_type == "projectRole":
                    project_role = holder.get("projectRole", {})
                    role_name = project_role.get("name", f"Role_{holder_param}")
                    role_id = holder_param or project_role.get("id")

                    if role_name == "atlassian-addons-project-access":
                        continue

                    permissions.append(Permission(
                        entity_type=EntityType.ROLE,
                        external_id=f"{project_key}_{role_id}",
                        type=PermissionType.READ
                    ))

                elif holder_type == "sd.customer.portal.only":
                    # JSM Service Desk customers (portal access)
                    # These are external customers who only access via the service desk portal
                    # Their access is limited to their own tickets through the portal UI
                    self.logger.debug(f"  {project_key}: Skipping JSM portal customers (external users, not synced)")

                elif holder_type == "projectLead":
                    permissions.append(Permission(
                        entity_type=EntityType.ROLE,
                        external_id=f"{project_key}_projectLead",
                        type=PermissionType.READ
                    ))

                elif holder_type in BROWSE_ISSUE_HOLDER_TYPES:
                    # Named per issue: granted on each issue (core/jira_issue_security.py), not here.
                    continue

                else:
                    self.logger.warning(f"⚠️  {project_key}: Unknown holder type '{holder_type}' with param '{holder_param}' - skipping")

            return permissions

        except Exception as e:
            self.logger.error(f"❌ Error fetching permission scheme for project {project_key}: {e}", exc_info=True)
            return None

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

    async def _sync_user_groups(self, jira_users: list[AppUser]) -> Optional[GroupMemberships]:
        """
        Sync user groups and return their members, for resolving project roles.

        A group whose members could not be read maps to None and is not saved. Returns
        None when the group list itself could not be read, so roles can't be resolved
        this run. A list cut off at the picker limit still saves the groups it holds
        (saving only upserts those groups, so the ones past the limit keep what is
        stored) and comes back marked ``cut_off``.
        """
        if await self._copies_no_source_access():
            return GroupMemberships({})
        try:
            self.logger.info("🚀 Starting Jira user group synchronization")

            page = await self._fetch_groups()
            if page is None:
                return None
            groups, cut_off = page
            if not groups:
                self.logger.info("ℹ️ No groups found in Jira")
                return GroupMemberships({}, cut_off)

            self.logger.info(f"👥 Found {len(groups)} groups. Fetching members...")

            # Create user_key -> AppUser lookup for efficient matching
            user_by_key = {user.source_user_id: user for user in jira_users if user.source_user_id}

            user_groups_batch = []
            # Group name -> members, for role actor lookup (``_normalize_jira_dc_group_row``: the name is the group's id)
            groups_members_map: dict[str, Optional[list[AppUser]]] = {}

            for group in groups:
                try:
                    group_id = group.get("groupId")
                    group_name = group.get("name")

                    if not group_id or not group_name:
                        continue

                    self.logger.debug(f"Processing group: {group_name} ({group_id})")

                    # Create AppUserGroup (always create, even if no members)
                    user_group = AppUserGroup(
                        app_name=self.connector_name,
                        connector_id=self.connector_id,
                        source_user_group_id=group_id,
                        name=group_name,
                        org_id=self.data_entities_processor.org_id,
                        description=f"Jira user group: {group_name}"
                    )

                    # Fetch member keys for this group
                    member_keys = await self._fetch_group_members(group_id, group_name)
                    if member_keys is None:
                        # Saving the group now would replace its members with an empty list.
                        self.logger.warning(
                            "Keeping the stored members of group %s: its member list could not be read",
                            group_name,
                        )
                        groups_members_map[group_name] = None
                        continue

                    # Map member keys to AppUser objects
                    app_users = []
                    skipped_members = 0
                    if member_keys:
                        for user_key in member_keys:
                            user = user_by_key.get(user_key)
                            if user:
                                app_users.append(user)
                            else:
                                skipped_members += 1

                    if skipped_members:
                        self.logger.debug(
                            "Group %s: %s member(s) skipped (no AppUser; hidden email or not in PipesHub)",
                            group_name,
                            skipped_members,
                        )

                    groups_members_map[group_name] = app_users

                    # Add group to batch (with or without members)
                    user_groups_batch.append((user_group, app_users))

                    if app_users:
                        self.logger.debug(f"Group {group_name}: {len(app_users)} members")
                    else:
                        self.logger.debug(f"Group {group_name}: no members with email")

                except Exception as group_error:
                    self.logger.error(f"❌ Failed to process group {group.get('name')}: {group_error}")
                    # Unknown members, not no members: roles that include this group keep what is stored.
                    self._note_unread(None)
                    if group.get("name"):
                        groups_members_map[group["name"]] = None
                    continue

            if self._user_read_unfinished:
                user_groups_batch = await self._without_stored_members("user_group", user_groups_batch)

            # One transaction per group: a large site's groups in one outlive the transaction timeout.
            if user_groups_batch:
                for entry in user_groups_batch:
                    await self.data_entities_processor.on_new_user_groups([entry])
            else:
                self.logger.info("ℹ️ No groups with valid members to sync")

            return GroupMemberships(groups_members_map, cut_off)

        except StoredAccessReadError:
            raise
        except Exception as e:
            self.logger.error(f"❌ Error syncing user groups: {e}")
            self._note_unread(None)
            await self._notify_group_sync_failed()
            return None

    async def _fetch_groups(self) -> Optional[GroupPickerPage]:
        """List DC groups via ``GET /rest/api/2/groups/picker?query=&maxResults=1000``.

        Returns None when the list could not be read (an error status, an unexpected
        response shape or a network error), so callers don't mistake it for "no groups".
        The picker has no offset, so when Jira matched more groups than it returned the
        page is marked ``cut_off``: the groups past the limit can't be read at all.
        """
        if not self.data_source:
            raise ValueError("DataSource not initialized")

        try:
            datasource = await self._get_fresh_datasource()
            response = await datasource.groups_picker_get_v2(
                query="",
                maxResults=GROUPS_PICKER_MAX,
            )

            if response.status != HttpStatusCode.OK.value:
                self.logger.warning(
                    "DC /groups/picker failed (%s): %s",
                    response.status,
                    response.text()[:300],
                )
                if response.status == HttpStatusCode.FORBIDDEN.value:
                    await self._notify_group_sync_failed()
                # The groups are unknown, so on a refusal every project's stored grants, and the
                # groups and roles they name, are kept.
                self._note_unread(response.status, ("projects", ""))
                self._note_unread(response.status, ("user_groups", ""))
                self._note_unread(response.status, ("project_role_ids", ""))
                return None

            # A missing or malformed list, or any malformed row, is not "no groups": the
            # groups it leaves out would look gone to a full sync's unlink.
            payload = response.json()
            if not isinstance(payload, dict):
                self._note_unread(None)
                return None

            raw_groups = payload.get("groups")
            if not isinstance(raw_groups, list):
                self.logger.warning("DC /groups/picker answered without a group list")
                self._note_unread(None)
                return None

            total = payload.get("total")
            cut_off = isinstance(total, int) and not isinstance(total, bool) and total > len(raw_groups)
            if cut_off:
                self.logger.warning(
                    "More than %s groups (Jira reports %s): roles that include groups past "
                    "the first %s were left unchanged.",
                    len(raw_groups),
                    total,
                    len(raw_groups),
                )

            groups = [
                _normalize_jira_dc_group_row(row) if isinstance(row, dict) else None
                for row in raw_groups
            ]
            if any(group is None for group in groups):
                self.logger.warning("DC /groups/picker answered with a group row that has no name")
                self._note_unread(None)
                return None

            self.logger.info(
                "👥 Fetched %s groups from /groups/picker (server total=%s)",
                len(groups),
                payload.get("total"),
            )
            return GroupPickerPage(groups, cut_off)

        except Exception as e:
            self.logger.error("❌ Error fetching groups via /groups/picker: %s", e)
            self._note_unread(None)
            return None

    def _resolve_dc_app_user_from_role_actor(
        self,
        actor: dict[str, Any],
        user_by_account_id: dict[str, AppUser],
        name_to_source_id: dict[str, str] | None = None,
    ) -> AppUser | None:
        """Resolve a DC project-role user actor to a synced ``AppUser``: by a key it carries, or by its username
        through ``name_to_source_id``. A username is never looked up as a key.

        A role actor names its user by username and carries no email ("actors": id, displayName, type, name,
        avatarUrl; https://docs.atlassian.com/software/jira/docs/api/REST/9.12.0/#api/2/project/{projectIdOrKey}/role-getProjectRole),
        and an email may be shared with another account. A key can equal another account's username: existing users
        were given "a key that is equal to the lowercase of the username" and usernames can change
        (https://developer.atlassian.com/server/jira/platform/renamable-users-in-jira-6-0/). Every synced account's
        username is in ``name_to_source_id`` (``_fetch_users`` records it from each row it resolves), so a username
        missing from it names no synced account, and resolves to none.
        """
        actor_user = actor.get("actorUser") or {}
        if not isinstance(actor_user, dict):
            actor_user = {}

        for identifier in (actor_user.get("accountId"), actor_user.get("key"), actor.get("key")):
            if identifier:
                user = user_by_account_id.get(identifier)
                if user:
                    return user

        actor_name = actor_user.get("name") or actor.get("name")
        source_id = (name_to_source_id or {}).get(actor_name.lower()) if actor_name else None
        return user_by_account_id.get(source_id) if source_id else None

    async def _sync_project_roles(
        self,
        project_keys: list[str],
        jira_users: list[AppUser],
        groups_members_map: dict[str, Optional[list[AppUser]]] = None,
        *,
        groups_cut_off: bool = False,
    ) -> None:
        """
        Sync project roles as AppRole entities using DC
        ``GET /rest/api/2/project/.../role`` and ``GET /rest/api/2/project/.../role/{id}``.

        groups_members_map: Mapping of group_id/name -> list of AppUser members (from _sync_user_groups);
            None marks a group whose members could not be read.
        groups_cut_off: The group list stopped at the picker limit, so a group missing
            from the map may exist past it; a role that includes one keeps its stored members.
        """
        if await self._copies_no_source_access():
            return
        if not self.data_source:
            raise ValueError("DataSource not initialized")

        if groups_members_map is None:
            groups_members_map = {}

        self.logger.info(f"🔐 Syncing project roles for {len(project_keys)} projects (DC GET /rest/api/2/project/.../role)...")

        # accountId -> AppUser lookup
        user_by_account_id: dict[str, AppUser] = {
            user.source_user_id: user
            for user in jira_users
            if user.source_user_id
        }

        roles_to_sync: list[tuple[AppRole, list[AppUser]]] = []
        total_roles = 0
        total_members = 0
        failed_project_keys: list[str] = []

        for project_key in project_keys:
            try:
                # Step 1: List role URLs for this project (GET /rest/api/2/project/{key}/role)
                datasource = await self._get_fresh_datasource()
                response = await datasource.get_project_roles_v2(projectIdOrKey=project_key)

                if response.status != HttpStatusCode.OK.value:
                    self.logger.warning(f"⚠️ Failed to fetch roles for project {project_key}: {response.status}")
                    self._note_unread(response.status, ("project_roles", project_key))
                    failed_project_keys.append(project_key)
                    continue

                roles_dict = response.json()
                if not isinstance(roles_dict, dict):
                    # Not "no roles": the stored ones would look gone to a full sync's unlink.
                    self.logger.warning(f"⚠️ The role list of project {project_key} is not a mapping")
                    self._note_unread(None)
                    failed_project_keys.append(project_key)
                    continue

                if not roles_dict:
                    self.logger.debug(f"No roles found for project {project_key}")
                    continue

                # Step 2: For each role, fetch role details including actors
                role_detail_failed = False
                for role_name, role_url in roles_dict.items():
                    try:
                        # Skip app-only roles
                        if role_name == "atlassian-addons-project-access":
                            continue

                        # Extract role id from URL (string; DC may use non-numeric ids)
                        role_id = role_url.rstrip('/').split('/')[-1]

                        # Fetch role details with actors (GET /rest/api/2/project/{key}/role/{id})
                        role_datasource = await self._get_fresh_datasource()
                        role_response = await role_datasource.get_project_role_v2(
                            projectIdOrKey=project_key,
                            id=role_id,
                            excludeInactiveUsers=True,
                        )

                        if role_response.status != HttpStatusCode.OK.value:
                            self.logger.warning(f"  {project_key}: Failed to fetch role {role_name}: {role_response.status}")
                            self._note_unread(role_response.status, ("role", f"{project_key}_{role_id}"))
                            role_detail_failed = True
                            continue

                        role_data = role_response.json()
                        actors = role_data.get("actors", [])

                        # Extract available fields from role_data
                        role_name_display = role_data.get("name", role_name)

                        # Build AppRole with external_id matching Permission format
                        app_role = AppRole(
                            app_name=self.connector_name,
                            connector_id=self.connector_id,
                            source_role_id=f"{project_key}_{role_id}",
                            name=f"{project_key} - {role_name_display}",
                            org_id=self.data_entities_processor.org_id,
                            source_created_at=None,  # Jira API doesn't provide role creation timestamp
                            source_updated_at=None   # Jira API doesn't provide role update timestamp
                        )

                        # Step 3: Extract member users from actors
                        member_users: list[AppUser] = []
                        unreadable_group: Optional[str] = None
                        group_past_cap: str | None = None

                        for actor in actors:
                            actor_type = actor.get("type", "")

                            if actor_type == "atlassian-user-role-actor":
                                user = self._resolve_dc_app_user_from_role_actor(
                                    actor,
                                    user_by_account_id,
                                    self._dc_name_to_source_id,
                                )

                                if user:
                                    member_users.append(user)
                                else:
                                    actor_user = actor.get("actorUser") or {}
                                    self.logger.debug(
                                        f"  {project_key}/{role_name}: User not found - "
                                        f"accountId={actor_user.get('accountId')}, "
                                        f"key={actor_user.get('key') or actor.get('key')}, "
                                        f"name={actor_user.get('name') or actor.get('name')}, "
                                        f"email={actor_user.get('emailAddress')}"
                                    )

                            elif actor_type == "atlassian-group-role-actor":
                                # A group actor names its group by name (RoleActorBean: ``name``), the map's only key.
                                group_name = actor.get("name") or actor.get("displayName")

                                group_members: Optional[list[AppUser]] = []
                                if group_name and group_name in groups_members_map:
                                    group_members = groups_members_map[group_name]
                                elif groups_cut_off:
                                    group_past_cap = group_name
                                    break
                                else:
                                    self.logger.debug(
                                        f"  {project_key}/{role_name}: Group actor '{group_name}' not found in synced groups"
                                    )

                                if group_members is None:
                                    unreadable_group = group_name
                                    break

                                self.logger.debug(
                                    f"  {project_key}/{role_name}: Group actor '{group_name}' "
                                    f"found {len(group_members)} members"
                                )
                                # Add all group members directly to role members (USER->ROLE, not GROUP->ROLE)
                                member_users.extend(group_members)

                        if group_past_cap:
                            # Past the picker limit on every sync, so not a failure to report;
                            # _fetch_groups already warns about the limit once per run. Lasting,
                            # so the kept role is noted as seen.
                            self._kept_after_refusal.append(("role", f"{project_key}_{role_id}"))
                            self.logger.info(
                                f"  {project_key}: Keeping the stored members of role {role_name}: "
                                f"group '{group_past_cap}' is past the first groups Jira lists"
                            )
                            continue

                        if unreadable_group:
                            # Saving the role now would drop that group's members from it. The group
                            # read already decided whether this sync is incomplete; the kept role is
                            # noted as seen either way.
                            self._kept_after_refusal.append(("role", f"{project_key}_{role_id}"))
                            self.logger.warning(
                                f"  {project_key}: Keeping the stored members of role {role_name}: "
                                f"members of group '{unreadable_group}' could not be read"
                            )
                            role_detail_failed = True
                            continue

                        roles_to_sync.append((app_role, member_users))
                        total_roles += 1
                        total_members += len(member_users)

                    except Exception as role_error:
                        self.logger.error(
                            f"  {project_key}: Error processing role {role_name}: {role_error}"
                        )
                        self._note_unread(None)
                        role_detail_failed = True
                        continue

                if role_detail_failed:
                    failed_project_keys.append(project_key)

            except Exception as project_error:
                self._note_unread(None)
                failed_project_keys.append(project_key)
                self.logger.error(f"❌ Error syncing roles for project {project_key}: {project_error}")
                continue

        if failed_project_keys:
            preview = ", ".join(failed_project_keys[:10])
            if len(failed_project_keys) > 10:
                preview = f"{preview}, and {len(failed_project_keys) - 10} more"
            self.logger.warning(
                "⚠️ Project role sync failed for %s/%s projects: %s",
                len(failed_project_keys), len(project_keys), preview,
            )
            await self.notify(
                type=NotificationType.CONNECTOR_ROLE_SYNC_ERROR,
                severity=NotificationSeverity.WARNING,
                title=self._notification_title("couldn't sync project roles"),
                message=(
                    f"Couldn't sync roles for: {preview}. "
                    "This is usually because the connector's Jira account lacks Administer Projects "
                    "on those projects, but can also be temporary — existing roles are "
                    "preserved and will retry next sync."
                ),
            )

        if self._user_read_unfinished:
            roles_to_sync = await self._without_stored_members("role", roles_to_sync)

        # Step 4: Sync all roles in batch
        if roles_to_sync:
            await self.data_entities_processor.on_new_app_roles(roles_to_sync)
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

        # Build accountId -> AppUser lookup
        user_by_account_id: dict[str, AppUser] = {
            user.source_user_id: user
            for user in jira_users
            if user.source_user_id
        }

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
                    # Classic DC projects expose ``lead`` with ``key``/``name`` only;
                    # newer DC builds may also expose ``accountId``. ``_fetch_users``
                    # populates ``source_user_id`` from ``accountId or key or name``
                    # (~1326), so accept the same fallback chain here.
                    lead_identifier = (
                        lead_data.get("accountId")
                        or lead_data.get("key")
                        or lead_data.get("name")
                    )
                    lead_display_name = lead_data.get("displayName", "Unknown")

                    if lead_identifier:
                        lead_user = self._synced_account(lead_data, user_by_account_id)

                        if not lead_user and self._user_read_unfinished:
                            unresolved_leads[app_role.source_role_id] = (project_key, project.get("id"))
                        if not lead_user:
                            self.logger.warning(f"Project lead {lead_display_name} not found in synced users for {project_key}")
                    else:
                        self.logger.warning(f"No identifier (accountId/key/name) for project lead in {project_key}")
                else:
                    self.logger.debug(f"No lead for project {project_key} - syncing role to clean up old edges")

                # Always sync the role (even with empty members list) to ensure old edges are deleted
                members = [lead_user] if lead_user else []
                lead_roles_to_sync.append((app_role, members))
                total_leads += 1


            except Exception as lead_error:
                self._note_unread(None)
                self.logger.error(f"Error processing lead for project {project.get('key')}: {lead_error}")
                continue

        if unresolved_leads:
            lead_roles_to_sync = await self._without_unresolved_stored_leads(lead_roles_to_sync, unresolved_leads)

        # Sync all project lead roles in batch
        if lead_roles_to_sync:
            await self.data_entities_processor.on_new_app_roles(lead_roles_to_sync)
        else:
            self.logger.info("No project leads to sync")

    async def _security_read(
        self, call: Callable[[JiraDataSource], Awaitable[Any]], label: str,
    ) -> tuple[int, dict[str, Any] | None]:
        """One read-only issue security call: ``(status, JSON body)``. A request that never
        got an answer is status 0, and one that kept getting 429 or 5xx has that status;
        ``http_status_is_transient`` reads both as worth retrying."""
        async def op() -> HTTPResponse:
            return await call(await self._get_fresh_datasource())

        try:
            response = await call_with_retry(op, logger=self.logger, label=label)
        except httpx.HTTPStatusError as e:
            return e.response.status_code, None
        except httpx.HTTPError as e:
            self.logger.warning("Could not read %s: %s", label, e)
            return 0, None
        try:
            body = response.json()
        except Exception:
            body = None
        return response.status, body if isinstance(body, dict) else None

    async def _load_issue_security(self, project_key: str, *, cached: bool = True) -> IssueSecurityContext:
        """Read the project's issue security scheme and its level members.

        Two calls per project, and one members read per distinct scheme per sync. A read that
        fails leaves ``knowledge`` UNKNOWN (``defer_checkpoint`` when it may succeed on retry),
        so a leveled issue is closed rather than opened to the project. ``cached=False`` reads
        the members afresh and leaves the sync's cache alone (a reindex runs between syncs).
        """
        if not self._enforces_issue_security:
            return IssueSecurityContext(knowledge=SchemeKnowledge.NONE, enforce=False)
        if await self._copies_no_source_access():
            # Not "no scheme" (that re-reads every stored issue and saves an empty level map): nothing read, nothing
            # changed. Holders unknown ("?"), so the first licensed read re-reads what they name.
            return IssueSecurityContext(knowledge=SchemeKnowledge.NONE, enforce=False, unlicensed=True, browse_holders=None)
        status, body = await self._security_read(
            lambda ds: ds.get_project_issue_security_scheme_v2(projectKeyOrId=project_key),
            f"the issue security scheme of {project_key}",
        )
        # The account lists this project, so a 404 that carries an error message is Jira
        # saying the project has no scheme.
        knowledge, scheme_id = classify_scheme_response(status, body, project_is_visible=True)
        context = IssueSecurityContext(
            knowledge=knowledge,
            scheme_id=scheme_id,
            scheme_forbidden=status == HttpStatusCode.FORBIDDEN.value,
            # A not-found that does not say "no scheme" may pass: the stored state is kept, not cleared.
            defer_checkpoint=http_status_is_transient(status)
            or (knowledge == SchemeKnowledge.UNKNOWN and status == HttpStatusCode.NOT_FOUND.value),
            read_status=status if knowledge == SchemeKnowledge.UNKNOWN else None,
            scope_refused=knowledge == SchemeKnowledge.UNKNOWN and refuses_a_scope(body),
        )
        if knowledge != SchemeKnowledge.PRESENT or scheme_id is None:
            return context
        if scheme_has_no_levels(body):
            context.members_readable = True
            return context
        members = self._scheme_members.get(scheme_id) if cached else None
        if members is None:
            async def fetch_page(start_at: int) -> tuple[int, dict[str, Any] | None]:
                return await self._security_read(
                    lambda ds: ds.get_issue_security_level_members_v2(
                        issueSecuritySchemeId=scheme_id, startAt=start_at, maxResults=100,
                        expand="user,group,projectRole",
                    ),
                    f"the issue security level members of scheme {scheme_id}",
                )

            members_status, values = await collect_paged_values(fetch_page)
            if values is None:
                # This route is not in Data Center's documented REST API (10.3 and 11.2 document only
                # /issuesecurityschemes, /issuesecurityschemes/{id}, /project/{key}/issuesecuritylevelscheme,
                # /project/{key}/securitylevel and /securitylevel/{id}; https://developer.atlassian.com/server/jira/platform/rest/v11002/),
                # so no answer of it is lasting: stored grants are kept and the read is retried.
                context.defer_checkpoint = True
                context.read_status = members_status
                return context  # members_readable stays False: leveled issues are closed
            members = (True, index_members(self._groups_by_name(values)))
            if cached:
                self._scheme_members[scheme_id] = members
        context.members_readable, context.members_by_level = members
        context.custom_field_ids = custom_field_ids_from_members(context.members_by_level)
        return context

    @staticmethod
    def _groups_by_name(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Data Center stores a group under its name, which a group holder carries in ``parameter``."""
        return [
            {**row, "holder": {**row["holder"], "value": row["holder"]["parameter"]}}
            if isinstance(row.get("holder"), dict) and row["holder"].get("type") == "group"
            and row["holder"].get("parameter") else row
            for row in rows
        ]

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
            )

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
            if fresh:
                try:
                    await self._mark_browse_holders_pending(project_key, security.browse_fingerprint())
                except Exception as e:
                    # Unmarked, a grant from these holders could outlive a scheme changed back before the next sync:
                    # this reindex writes as if they were unread (no grant from them; a level is still applied).
                    self.logger.warning("Could not mark the Browse holders of %s for a re-read: %s", project_key, e)
                    security.browse_holders = None
        return security

    async def _mark_browse_holders_pending(self, project_key: str, fingerprint: str) -> None:
        """A reindex that could not read the holders, or read other holders than the last sync applied to the
        project, wrote this issue by them; the `?` marker makes the next sync that reads them re-read the project's
        issues, even if the scheme is changed back meanwhile and its digest matches the stored one again."""
        stored = ((await self._get_project_sync_checkpoint(project_key)) or {}).get("browse_holders") or ""
        if stored not in (fingerprint, "?"):
            await self._update_project_sync_checkpoint(project_key, browse_holders="?")

    async def _read_browse_issue_holders(self, project_key: str) -> list[dict[str, Any]] | None:
        try:
            status, scheme = await self._security_read(
                lambda ds: ds.get_assigned_permission_scheme_v2(projectKeyOrId=project_key),
                f"the permission scheme of {project_key}",
            )
            scheme_id = scheme.get("id") if status == HttpStatusCode.OK.value and scheme else None
            if scheme_id is None:
                return None
            status, grants = await self._security_read(
                lambda ds: ds.get_permission_scheme_grants_v2(schemeId=scheme_id),
                f"the permission grants of scheme {scheme_id}",
            )
            if status != HttpStatusCode.OK.value or grants is None:
                return None
            return _browse_holders_of(grants)
        except Exception as e:
            self.logger.warning("Could not read the permission scheme of %s: %s", project_key, e)
            return None

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

    async def _mark_levels_to_retry(self, project_key: str) -> None:
        """``_save_levels_retry``, logging a failure instead of raising it."""
        try:
            await self._save_levels_retry(project_key)
        except Exception as e:
            # Never let this cost the close: the holders then wait for the issue's next change at source.
            self.logger.warning("Could not save the retry marker for project %s: %s", project_key, e)

    async def _save_levels_retry(self, project_key: str) -> None:
        """Save the retry marker in the project's level map, if it is not there."""
        if not project_key:
            return
        try:
            stored = json.loads((await self._get_project_sync_checkpoint(project_key) or {}).get("security_levels"))
        except (TypeError, ValueError):
            stored = None
        marker = pre_walk_marker(stored if isinstance(stored, dict) else None)
        if marker != stored:
            await self._update_project_sync_checkpoint(project_key, security_levels=json.dumps(marker, sort_keys=True))

    async def _record_security_levels(
        self, project_key: str, levels: dict[str, str], *, force: bool = False,
    ) -> None:
        """Save the level map only after a clean walk, so a failed one repeats next sync.
        ``force`` saves a retry marker, which only ever asks for more reading."""
        if force or not self._issue_build_failures:
            await self._update_project_sync_checkpoint(project_key, security_levels=json.dumps(levels, sort_keys=True))

    @staticmethod
    def _hold_grants_while_holders_unread(
        record: Record, security: IssueSecurityContext | None, stored: Record | None, *, changed: bool,
    ) -> None:
        """While the Browse holders are unread, an open top-level issue unchanged since it was stored keeps the
        grants stored for it (its holders'), whether its level came back null or not at all. One stored RESTRICTED
        had a level, whose holders those grants are, so it loses them; so does one that changed, whose reporter,
        assignee or fields may no longer name those people: it is read by its project until the holders are read."""
        if (
            security is None or not security.enforce or security.browse_holders is not None or stored is None
            or record.parent_external_record_id or record.access_rule == AccessRule.RESTRICTED
        ):
            return
        if stored.access_rule == AccessRule.RESTRICTED or changed:
            record.access_rule, record.rewrite_permissions = AccessRule.STRICT, True
        else:
            record.access_rule, record.rewrite_permissions = AccessRule.OPEN, False

    async def _keeps_stored_grants(self, record: Record) -> bool:
        """An issue already stored whose access rule was set without its grants (a level or the
        Browse holders not read, which may pass on a retry), so the grants it has stay."""
        context = self._issue_security.get(record.external_record_group_id)
        browse_unread = context is not None and context.browse_holders is None
        if (
            not isinstance(record, TicketRecord) or record.rewrite_permissions or record.is_placeholder
            or not (record.access_rule == AccessRule.RESTRICTED or (record.access_rule == AccessRule.OPEN and browse_unread))
        ):
            return False
        stored = await self.data_entities_processor.get_record_by_external_id(
            connector_id=self.connector_id, external_record_id=record.external_record_id
        )
        return stored is not None and not stored.is_placeholder

    def _synced_account(self, ref: dict[str, Any], user_by_key: dict[str, AppUser]) -> AppUser | None:
        key = self._account_key(ref)
        return user_by_key.get(key) if key else None
