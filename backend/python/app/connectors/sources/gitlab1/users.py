"""
User synchronisation for the GitLab connector.

Responsibilities:
- Resolve the sync scope (which groups/projects to walk for members).
- Walk group and project member listings (scoped and unscoped paths).
- Enrich ``GroupMember`` objects with full ``User`` objects to retrieve ``public_email``.
- Inject the connector creator into the member set.
- Persist ``AppUser`` rows and migrate pseudo-group permissions.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

from gitlab.v4.objects import GroupMember

from app.connectors.core.registry.filters import FilterOperator, SyncFilterKey
from app.models.entities import AppUser

from .constants import _GITLAB_USER_ENRICHMENT_CONCURRENCY

if TYPE_CHECKING:
    from app.connectors.sources.gitlab1.connector import GitLabConnector


def _filter_op_val(f: Any) -> str:
    """Lower-cased string value of a Filter's operator."""
    op = f.operator
    return (op.value if hasattr(op, "value") else str(op)).lower()


class UsersSync:
    """Handles all GitLab user-synchronisation steps for ``GitLabConnector``."""

    def __init__(self, connector: "GitLabConnector") -> None:
        self.c = connector
        self.logger = connector.logger

    # ------------------------------------------------------------------
    # Entry point
    # ------------------------------------------------------------------

    async def sync_users(self) -> None:
        """Fetch all active GitLab users of groups and projects.

        Honors ``sync_filters``: when ``GROUP_IDS`` or ``PROJECT_IDS``
        (``IN`` or ``NOT_IN``) is configured, only walks members of in-scope
        entities.  Without that early scoping a connector targeting a few
        top-level groups on a large EE deployment would scan every group the
        bot account is Guest+ on.
        """
        scope = await self._resolve_user_sync_scope()
        if scope is not None:
            group_paths, project_paths = scope
            self.logger.info(
                "Scoped user sync: %s group(s), %s project(s) from sync_filters",
                len(group_paths),
                len(project_paths),
            )
            await self._sync_users_scoped(group_paths, project_paths)
            return
        await self._sync_users_unscoped()

    # ------------------------------------------------------------------
    # Scope resolution
    # ------------------------------------------------------------------

    async def _resolve_user_sync_scope(
        self,
    ) -> tuple[list[str], list[str]] | None:
        """Resolve ``(group_targets, project_targets)`` for the user-sync walk.

        Mirrors ``_resolve_projects_with_filters`` so user discovery walks
        the same universe of groups and projects as project discovery.

        - ``PROJECT_IDS IN`` is authoritative when set: user-sync only walks
          those projects.  ``GROUP_IDS IN`` is ignored as a sync widener in this
          case.
        - ``GROUP_IDS IN`` (without ``PROJECT_IDS IN``): walk members of the
          listed groups + every subgroup project they own.
        - ``NOT_IN``: materialise the visible set once and drop excluded ones.
        - Returns ``None`` when no group/project sync filter is configured;
          callers then fall back to the unscoped sweep.
        """
        sf = self.c.sync_filters
        if not sf:
            return None

        grp_f = sf.get(SyncFilterKey.GROUP_IDS)
        proj_f = sf.get(SyncFilterKey.PROJECT_IDS)
        grp_active = grp_f is not None and not grp_f.is_empty()
        proj_active = proj_f is not None and not proj_f.is_empty()

        if not grp_active and not proj_active:
            return None

        grp_op = _filter_op_val(grp_f) if grp_active else None
        proj_op = _filter_op_val(proj_f) if proj_active else None

        group_targets: list[str] = []
        project_targets: list[str] = []

        proj_in_short_circuits_grp_in = proj_active and proj_op == FilterOperator.IN

        if grp_active and grp_op == FilterOperator.IN and not proj_in_short_circuits_grp_in:
            group_targets = list(grp_f.value)  # type: ignore[arg-type]
        elif grp_active and grp_op == FilterOperator.NOT_IN:
            excluded = list(grp_f.value)  # type: ignore[arg-type]
            groups_res = await self.c.scope.paged_list_groups_with_role_fallback(
                per_page=100,
                progress_label="list_groups NOT_IN user-sync scope",
            )
            if not groups_res.success:
                self.logger.error(
                    "Could not list groups for NOT_IN user-sync scope: %s", groups_res.error
                )
            else:
                group_targets = [
                    getattr(g, "full_path", None) or ""
                    for g in (groups_res.data or [])
                    if not any(
                        (getattr(g, "full_path", None) or "").startswith(e + "/")
                        or (getattr(g, "full_path", None) or "") == e
                        for e in excluded
                    )
                ]
                group_targets = [p for p in group_targets if p]

        if proj_active and proj_op == FilterOperator.IN:
            project_targets = list(proj_f.value)  # type: ignore[arg-type]
        elif proj_active and proj_op == FilterOperator.NOT_IN:
            excluded_proj = list(proj_f.value)  # type: ignore[arg-type]
            # When GROUP_IDS NOT_IN is also configured, projects under any
            # excluded group prefix must also be dropped to stay consistent
            # with _resolve_projects_with_filters.
            group_prefixes = (
                list(grp_f.value)  # type: ignore[arg-type]
                if grp_active and grp_op == FilterOperator.NOT_IN
                else []
            )
            proj_res = await self.c.scope.paged_list_projects_with_role_fallback(
                pagination="keyset",
                order_by="id",
                sort="asc",
                per_page=100,
                progress_label="list_projects NOT_IN user-sync scope",
            )
            if not proj_res.success:
                self.logger.error(
                    "Could not list projects for NOT_IN user-sync scope: %s", proj_res.error
                )
            else:
                excluded_proj_set = set(excluded_proj)
                for p in (proj_res.data or []):
                    pth = getattr(p, "path_with_namespace", None)
                    if not pth or pth in excluded_proj_set:
                        continue
                    if group_prefixes:
                        ns_path = getattr(getattr(p, "namespace", None), "full_path", None) or ""
                        if any(
                            ns_path == gp or ns_path.startswith(gp + "/")
                            for gp in group_prefixes
                        ):
                            continue
                    project_targets.append(pth)

        # Return the tuple even when both lists are empty — returning None here
        # would fall back to the unscoped full-tenant scan, defeating the filter.
        return group_targets, project_targets

    # ------------------------------------------------------------------
    # Scoped user sync
    # ------------------------------------------------------------------

    async def _sync_users_scoped(
        self, group_paths: list[str], project_paths: list[str]
    ) -> None:
        """Walk members of explicitly-configured groups/projects only.

        Member discovery must cover the same project universe as
        ``_resolve_projects_with_filters``.  ``GET /groups/:id/members/all``
        only inherits from *ancestor* groups, not descendants, so a configured
        top-level group does not reach users added directly to a subgroup.
        This method expands each group via ``list_group_projects(include_subgroups=True)``
        to close that gap.

        Falls back to creator-only access when every configured target fails,
        mirroring the Jira ``_fallback_permissions_for_forbidden_scheme`` pattern.
        """
        c = self.c
        dict_member: dict[int, GroupMember] = {}
        total_groups_synced = total_groups_skipped = 0
        total_projects_synced = total_projects_skipped = 0
        total_member_rows_seen = 0
        any_success = False
        walked_project_ids: set[int] = set()

        async def _walk_project_members(
            project_id: int | str, label: str
        ) -> tuple[bool, int]:
            """Returns ``(succeeded_with_data, member_rows_returned)``."""
            try:
                pres = await c.runtime.ds_call(
                    c.data_source.list_project_members_all,
                    project_id=project_id,
                    get_all=True,
                )
                if not pres.success:
                    self.logger.error("Error fetching members for %s: %s", label, pres.error)
                    return False, 0
                rows = list(pres.data or [])
                for member in rows:
                    dict_member[member.id] = member
                return bool(rows), len(rows)
            except Exception as e:
                self.logger.error("Error in syncing users for %s: %s", label, e, exc_info=True)
                return False, 0

        for i, group_path in enumerate(group_paths, start=1):
            self.logger.info(
                "syncing users for configured group %s/%s (%s)",
                i, len(group_paths), group_path,
            )
            try:
                members_res = await c.runtime.ds_call(
                    c.data_source.list_group_members_all,
                    group_id=group_path,
                    get_all=True,
                )
                if not members_res.success:
                    self.logger.error(
                        "Error fetching members for configured group %s: %s",
                        group_path, members_res.error,
                    )
                    total_groups_skipped += 1
                else:
                    rows = list(members_res.data or [])
                    for member in rows:
                        dict_member[member.id] = member
                    if rows:
                        any_success = True
                    total_groups_synced += 1
            except Exception as e:
                self.logger.error(
                    "Error in syncing users for group %s: %s", group_path, e, exc_info=True
                )

            try:
                gres = await c.runtime.paged_list(
                    c.data_source.list_group_projects,
                    group_path,
                    include_subgroups=True,
                    progress_label=f"list_group_projects user-sync({group_path})",
                )
            except Exception as e:
                self.logger.error("Error expanding subgroup projects for %s: %s", group_path, e, exc_info=True)
                continue
            if not gres.success:
                self.logger.error(
                    "Could not fully list subgroup projects for user sync of "
                    "group %s: %s. Walking partial list (%s project(s)).",
                    group_path, gres.error, len(gres.data or []),
                )
            projects = list(gres.data or [])
            if not projects:
                continue
            total = len(projects)
            group_member_rows = group_projects_walked = 0
            for j, p in enumerate(projects, start=1):
                pid = getattr(p, "id", None)
                if pid is None or pid in walked_project_ids:
                    continue
                walked_project_ids.add(pid)
                ppath = getattr(p, "path_with_namespace", None) or str(pid)
                self.logger.debug(
                    "syncing users for subgroup project %s/%s under %s (%s)",
                    j, total, group_path, ppath,
                )
                ok, n_rows = await _walk_project_members(pid, f"subgroup project {ppath}")
                if ok:
                    any_success = True
                    total_projects_synced += 1
                    group_projects_walked += 1
                    group_member_rows += n_rows
                    total_member_rows_seen += n_rows
                else:
                    total_projects_skipped += 1
            self.logger.info(
                "Group %s subgroup-expansion: %s/%s project(s) walked, %s member row(s) "
                "(running unique members: %s)",
                group_path, group_projects_walked, total,
                group_member_rows, len(dict_member),
            )

        for i, project_path in enumerate(project_paths, start=1):
            self.logger.info(
                "syncing users for configured project %s/%s (%s)",
                i, len(project_paths), project_path,
            )
            ok, n_rows = await _walk_project_members(project_path, f"configured project {project_path}")
            if ok:
                any_success = True
                total_projects_synced += 1
                total_member_rows_seen += n_rows
                self.logger.info("Configured project %s: members_all returned %s row(s)", project_path, n_rows)
            else:
                total_projects_skipped += 1

        creator_added = self._inject_creator_member_into(dict_member)
        all_failed = bool(group_paths or project_paths) and not any_success
        if all_failed and not creator_added:
            raise RuntimeError(
                "GitLab user sync aborted: every configured group/project failed "
                "to enumerate members and no creator identity was resolved (cannot fall back)."
            )
        if all_failed:
            self.logger.warning(
                "GitLab user sync: every configured group/project failed to enumerate members; "
                "relying on creator-only access (%s) for this run.",
                c.creator_email,
            )

        avg_rows_per_project = (
            total_member_rows_seen / total_projects_synced if total_projects_synced else 0
        )
        self.logger.info(
            "Scoped user-sync summary: %s unique member(s) from %s member row(s) across %s "
            "project(s) (avg %.2f rows/project)",
            len(dict_member), total_member_rows_seen, total_projects_synced, avg_rows_per_project,
        )
        dict_member = await self._enrich_members_with_full_user(dict_member)
        await self._sync_users_from_projects_groups(dict_member)
        self.logger.info("Users sync and migration of pseudo groups complete")

    # ------------------------------------------------------------------
    # Unscoped user sync
    # ------------------------------------------------------------------

    async def _sync_users_unscoped(self) -> None:
        """Fall-back path: scan every group/project visible to the bot.

        Streams via ``paged_list`` so the operator sees per-page INFO progress
        instead of one opaque blocking call.  Still expensive on large tenants —
        prefer scoping via ``GROUP_IDS`` / ``PROJECT_IDS`` sync filters.
        """
        c = self.c
        scope_kwargs = c.scope.list_groups_scope_kwargs()
        groups_res = await c.scope.paged_list_groups_with_role_fallback(
            per_page=100,
            progress_label=f"list_groups ({scope_kwargs})",
        )
        total_groups_synced = total_groups_skipped = 0
        total_projects_synced = total_projects_skipped = 0
        dict_member: dict[int, Any] = {}
        groups_failed = not groups_res.success
        if groups_failed:
            self.logger.error(
                "Error in fetching groups: %s, continuing with projects members", groups_res.error
            )
        if groups_res.data:
            groups = groups_res.data
            total = len(groups)
            for i, group in enumerate(groups, start=1):
                try:
                    group_id = getattr(group, "id", None)
                    if group_id is None:
                        self.logger.warning("Group missing ID, skipping")
                        total_groups_skipped += 1
                        continue
                    self.logger.info(
                        "syncing users for group %s/%s id=%s", i, total, group_id
                    )
                    members_res = await c.runtime.ds_call(
                        c.data_source.list_group_members_all,
                        group_id=group_id,
                        get_all=True,
                    )
                    if not members_res.success:
                        self.logger.info("Error in fetching members for group %s", group_id)
                        total_groups_skipped += 1
                        continue
                    for member in members_res.data:
                        dict_member[member.id] = member
                    total_groups_synced += 1
                except Exception as e:
                    self.logger.error(
                        "Error in syncing users for group %s: %s", group_id, e, exc_info=True
                    )

        proj_scope_kwargs = c.scope.list_projects_scope_kwargs()
        projects_res = await c.scope.paged_list_projects_with_role_fallback(
            pagination="keyset",
            order_by="id",
            sort="asc",
            per_page=100,
            progress_label=f"list_projects ({proj_scope_kwargs})",
        )
        projects_failed = not projects_res.success
        if projects_failed:
            self.logger.error("Error in fetching projects: %s", projects_res.error)

        creator_added = self._inject_creator_member_into(dict_member)
        if groups_failed and projects_failed and not creator_added:
            raise RuntimeError(
                "GitLab user sync aborted: both list_groups and list_projects "
                f"failed (groups: {groups_res.error}; projects: {projects_res.error}) "
                "and no creator identity was resolved (cannot fall back)."
            )
        if groups_failed and projects_failed:
            self.logger.warning(
                "GitLab user sync (unscoped): both list_groups (%s) and list_projects (%s) "
                "failed; relying on creator-only access (%s) for this run.",
                groups_res.error, projects_res.error, c.creator_email,
            )

        if projects_res.data:
            projects = projects_res.data
            total = len(projects)
            for i, project in enumerate(projects, start=1):
                try:
                    project_id = getattr(project, "id", None)
                    if project_id is None:
                        self.logger.warning("Project missing ID, skipping")
                        total_projects_skipped += 1
                        continue
                    self.logger.info(
                        "syncing users for project %s/%s id=%s", i, total, project_id
                    )
                    members_res = await c.runtime.ds_call(
                        c.data_source.list_project_members_all,
                        project_id=project_id,
                        get_all=True,
                    )
                    if not members_res.success:
                        self.logger.error("Error in fetching members for project %s", project_id)
                        total_projects_skipped += 1
                        continue
                    for member in members_res.data:
                        dict_member[member.id] = member
                    total_projects_synced += 1
                except Exception as e:
                    self.logger.error("Error in syncing users for project: %s", e, exc_info=True)

        self.logger.info(
            "Total groups synced: %s, Total groups skipped: %s",
            total_groups_synced, total_groups_skipped,
        )
        self.logger.info(
            "Total projects synced: %s, Total projects skipped: %s",
            total_projects_synced, total_projects_skipped,
        )
        dict_member = await self._enrich_members_with_full_user(dict_member)
        await self._sync_users_from_projects_groups(dict_member)
        self.logger.info("Users sync and migration of pseudo groups complete")

    # ------------------------------------------------------------------
    # Enrichment and creator injection
    # ------------------------------------------------------------------

    async def _enrich_members_with_full_user(
        self, dict_member: dict[int, Any]
    ) -> dict[int, Any]:
        """Fetch ``GET /users/:id`` per unique member to recover ``public_email``.

        The members API does not include ``public_email``; the full ``/users/:id``
        response does.  Concurrency is bounded by a ``Semaphore`` sized at
        ``_GITLAB_USER_ENRICHMENT_CONCURRENCY`` to avoid filling the dedicated
        executor pool and starving picker queries and paged sweeps.

        Falls back to the original member payload on any per-user failure so a
        single misbehaving user endpoint does not abort the whole sweep.
        """
        c = self.c
        total = len(dict_member)
        sem = asyncio.Semaphore(_GITLAB_USER_ENRICHMENT_CONCURRENCY)
        progress_every = 200

        async def fetch_full_user(member_id: int, member: Any) -> tuple[int, Any]:
            async with sem:
                try:
                    user_res = await c.runtime.ds_call(
                        c.data_source.get_user,
                        member_id,
                        _gitlab_timeout=120.0,
                    )
                    if user_res.success and user_res.data is not None:
                        return member_id, user_res.data
                    self.logger.warning(
                        "Could not fetch full GitLab user id=%s (%s); using member payload.",
                        member_id,
                        getattr(user_res, "error", "unknown"),
                    )
                    return member_id, member
                except Exception as e:
                    self.logger.warning(
                        "Exception fetching GitLab user id=%s; using member payload: %s",
                        member_id, e, exc_info=True,
                    )
                    return member_id, member

        enriched: dict[int, Any] = {}
        last_progress_logged = 0
        coros = [fetch_full_user(mid, mem) for mid, mem in dict_member.items()]
        for fut in asyncio.as_completed(coros):
            member_id, user_obj = await fut
            enriched[member_id] = user_obj
            done = len(enriched)
            if done - last_progress_logged >= progress_every or done == total:
                self.logger.info("Enriching GitLab members: %s/%s done", done, total)
                last_progress_logged = done

        self.logger.info("Enriched %s GitLab members with full user objects", len(enriched))
        return enriched

    def _build_creator_member_stub(self) -> Any | None:
        """Return a ``GroupMember``-shaped namespace for the connector creator.

        Returns ``None`` when we lack either the GitLab numeric id or the creator
        email — in that state the id-based bypass cannot round-trip cleanly.
        Uses ``SimpleNamespace`` rather than a real ``GroupMember`` to avoid
        python-gitlab SDK constructor coupling.
        """
        c = self.c
        if not c.creator_email or c._gitlab_user_id is None:
            return None
        return SimpleNamespace(
            id=c._gitlab_user_id,
            username=(c.creator_email.split("@")[0] or "creator"),
            name=c.creator_email,
            public_email=c.creator_email,
            email=c.creator_email,
            access_level=50,  # OWNER — creator owns the records they sync
        )

    def _inject_creator_member_into(self, dict_member: dict[int, Any]) -> bool:
        """Ensure the connector creator is present in ``dict_member``.

        GitLab Admin and EE Auditor personas have cross-instance read access via
        a user-level flag, not a membership row.  They are therefore omitted from
        ``/members/all`` listings even though they can read every record.  Without
        this injection they end up with no ``AppUser`` row and lose PipesHub access.

        Uses ``setdefault`` so a real member row already in the dict is preserved.
        Returns ``True`` when the creator could be represented (stub built and
        either injected or already present).
        """
        creator = self._build_creator_member_stub()
        if creator is None:
            return False
        dict_member.setdefault(creator.id, creator)
        return True

    # ------------------------------------------------------------------
    # AppUser persistence
    # ------------------------------------------------------------------

    async def _sync_users_from_projects_groups(
        self, dict_member: dict[int, Any]
    ) -> None:
        """Create ``AppUser`` rows from the enriched member dict.

        Special case: the row whose ``id`` matches the connector creator's
        GitLab numeric id always becomes an ``AppUser`` using
        ``creator_email``, even when ``public_email`` is empty.  Without this
        bypass the creator becomes a pseudo-group keyed by a numeric id their
        PipesHub identity never matches.
        """
        c = self.c
        total_users_synced = total_users_skipped = 0
        app_users: list[AppUser] = []
        for member_id, member in dict_member.items():
            raw_email = getattr(member, "public_email", None)
            if isinstance(raw_email, str) and raw_email.strip():
                user_email = raw_email.strip()
            else:
                fallback = getattr(member, "email", None)
                user_email = fallback.strip() if isinstance(fallback, str) else ""

            # Creator bypass: if this is the configuring user and GitLab
            # did not surface an email for them, use the PipesHub email.
            if (
                not user_email
                and c._gitlab_user_id is not None
                and member_id == c._gitlab_user_id
                and c.creator_email
            ):
                user_email = c.creator_email
                self.logger.info(
                    "Resolved creator AppUser %r via PipesHub identity "
                    "(public_email not set on GitLab profile, gitlab id=%s).",
                    user_email, member_id,
                )

            if not user_email:
                total_users_skipped += 1
                self.logger.debug(
                    "Email not found for user %s with id %s, skipping",
                    getattr(member, "username", "unknown"), member_id,
                )
            else:
                app_users.append(
                    AppUser(
                        app_name=c.connector_name,
                        org_id=c.data_entities_processor.org_id,
                        connector_id=c.connector_id,
                        source_user_id=str(member_id),
                        is_active=True,
                        email=user_email,
                        full_name=getattr(member, "name", user_email),
                    )
                )

        if app_users:
            await c.data_entities_processor.on_new_app_users(app_users)
            total_users_synced += len(app_users)
            for user in app_users:
                try:
                    await c.data_entities_processor.migrate_group_to_user_by_external_id(
                        group_external_id=user.source_user_id,
                        user_email=user.email,
                        connector_id=c.connector_id,
                    )
                except Exception as e:
                    self.logger.warning(
                        "Failed to migrate pseudo-group permissions for user %s: %s",
                        user.email, e, exc_info=True,
                    )

        self.logger.info(
            "Total users synced: %s, Total users skipped: %s",
            total_users_synced, total_users_skipped,
        )
