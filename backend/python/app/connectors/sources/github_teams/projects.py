"""
Repository (project) synchronisation for the GitHub Teams connector.

Responsibilities:
- Resolve the set of repositories to sync (applying ``ORG_IDS`` / ``REPO_IDS`` filters).
- Create the org -> repo -> {work-items, pull-requests, code-repository} ``RecordGroup``
  hierarchy, keyed by the stable numeric ``repo.id`` (never the mutable ``full_name``).
- Map GitHub collaborator roles and team access levels to ``Permission`` objects.
- Sync GitHub Team membership via ``on_new_user_groups`` (add/remove team member detection).
- Detect deleted/transferred repos via a ``repo-inventory`` ``SyncPoint`` and cascade-delete
  their record groups and records.
- Fall back to creator-only permissions whenever collaborator/team listing fails, so a
  transient API error never orphans a repo's records from every viewer.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from github.NamedUser import NamedUser  # type: ignore
from github.Repository import Repository  # type: ignore
from github.Team import Team  # type: ignore

from app.connectors.core.base.sync_point.sync_point import SyncDataPointType, SyncPoint
from app.connectors.core.registry.filters import FilterOperator, SyncFilterKey
from app.models.entities import AppUserGroup, RecordGroup, RecordGroupType
from app.models.permission import EntityType, Permission, PermissionType

from .constants import AFFILIATION_ALL, PSEUDO_USER_GROUP_PREFIX
from .models import GitHubLiterals

if TYPE_CHECKING:
    from app.connectors.sources.github_teams.connector import GitHubTeamsConnector


def _filter_op_val(f: Any) -> str:
    """Lower-cased string value of a Filter's operator."""
    op = f.operator
    return (op.value if hasattr(op, "value") else str(op)).lower()


def _highest_role_from_collaborator_permissions(perms: Any) -> str | None:
    """Reduce a PyGithub ``Permissions`` object to a single role string.

    Checked in descending order of privilege since ``Permissions`` carries
    independent booleans (e.g. ``admin`` implies all lower ones are also True).
    """
    if perms is None:
        return None
    for attr, role in (("admin", "admin"), ("maintain", "maintain"), ("push", "push"), ("triage", "triage"), ("pull", "pull")):
        if getattr(perms, attr, False):
            return role
    return None


def _permission_type_from_role(role: str | None) -> PermissionType | None:
    """Map a GitHub role string to a ``PermissionType``.

    Every role maps to access on *all* child record groups (issues, PRs,
    code) — unlike GitLab, GitHub's ``pull`` role already grants read access
    to code alongside issues/PRs, so there is no per-child tiering needed.
    """
    if role == "admin":
        return PermissionType.OWNER
    if role in ("maintain", "push"):
        return PermissionType.WRITE
    if role in ("triage", "pull"):
        return PermissionType.READ
    return None


class ProjectsSync:
    """Handles repo-level record-group creation and permission syncing.

    Overridden (permission hooks only) by the personal-connector variant —
    see ``github/connector.py::GitHubPersonalProjectsSync``.
    """

    def __init__(self, connector: "GitHubTeamsConnector") -> None:
        self.c = connector
        self.logger = connector.logger
        self._org_permission_accumulator: dict[str, dict[tuple[str, str], Permission]] = {}
        self._org_record_group_meta: dict[str, Any] = {}

    # ------------------------------------------------------------------
    # Entry point
    # ------------------------------------------------------------------

    async def sync_all_repos(self) -> None:
        """Discover repos, detect deletions, and run the per-repo sync pipeline."""
        c = self.c
        if not c.data_source:
            raise Exception("GitHub data source not initialized")

        self._org_permission_accumulator = {}
        self._org_record_group_meta = {}

        repos, discovery_complete = await self._resolve_repos_with_filters()
        current_ids = {int(r.id) for r in repos}
        if discovery_complete:
            await self._detect_deleted_repos(current_ids)
        else:
            self.logger.warning(
                "GitHub repo discovery was incomplete this sync (one or more listing "
                "calls failed); skipping deletion detection and inventory update so a "
                "transient error cannot orphan records."
            )

        if not repos:
            self.logger.warning("No GitHub repositories to sync after applying filters")
            return

        for repo in repos:
            try:
                await self._sync_repo(repo)
            except Exception as e:
                self.logger.error(
                    "Unhandled error syncing GitHub repo %s (id=%s); continuing: %s",
                    getattr(repo, "full_name", "?"), getattr(repo, "id", "?"), e, exc_info=True,
                )

        await self._flush_org_record_groups()
        if discovery_complete:
            await self._update_repo_inventory(current_ids)

    async def _sync_repo(self, repo: Repository) -> None:
        """Sync one repo: permissions -> record group hierarchy -> issues/PRs/code."""
        c = self.c
        owner_login = repo.owner.login
        repo_name = repo.name

        try:
            permissions = await self._sync_repo_members(owner_login, repo_name)
        except Exception as e:
            self.logger.error(
                "Error resolving members for %s: %s. Falling back to creator-only permissions.",
                repo.full_name, e, exc_info=True,
            )
            permissions = self._creator_only_permissions()

        self._accumulate_org_permissions(owner_login, permissions)
        await self._create_record_group_hierarchy(repo, permissions)

        for step_name, step in (
            # GitHub's issues endpoint returns PRs too (distinguished by a
            # ``pull_request`` key); fetching once and splitting here — rather
            # than a second, separately-paginated PR listing call — halves the
            # core-budget cost of this step.
            ("issues_and_pull_requests", lambda: c.issues.fetch_issues_batched(repo)),
            ("code", lambda: c.repos.run(repo)),
        ):
            try:
                await step()
            except Exception as e:
                self.logger.error(
                    "Unhandled error syncing %s for repo %s (id=%s); continuing: %s",
                    step_name, repo.full_name, repo.id, e, exc_info=True,
                )

    # ------------------------------------------------------------------
    # Repo resolution
    # ------------------------------------------------------------------

    async def _resolve_repos_with_filters(self) -> tuple[list[Repository], bool]:
        """Resolve repos to sync from sync filters.

        Semantics:
        - ``REPO_IDS IN`` is authoritative: only listed repos sync (values are
          ``owner/repo`` full names).
        - ``ORG_IDS IN`` (without ``REPO_IDS IN``): all repos under each listed org.
        - Neither filter: discover every org visible to the token.
        - ``NOT_IN`` variants are subtractive.

        Returns ``(repos, discovery_complete)``. ``discovery_complete`` is False
        whenever any lookup failed (malformed filter value, ``get_repo`` miss,
        or ``list_org_repos`` failure) — callers must not treat the returned
        set as authoritative for deletion detection in that case, since a
        transient error would otherwise look identical to a real deletion.
        """
        c = self.c
        sf = c.sync_filters
        org_f = sf.get(SyncFilterKey.ORG_IDS) if sf else None
        repo_f = sf.get(SyncFilterKey.REPO_IDS) if sf else None
        org_vals = list(org_f.value) if (org_f and not org_f.is_empty()) else []  # type: ignore[arg-type]
        repo_vals = list(repo_f.value) if (repo_f and not repo_f.is_empty()) else []  # type: ignore[arg-type]
        org_op = _filter_op_val(org_f) if org_vals else None
        repo_op = _filter_op_val(repo_f) if repo_vals else None

        org_in = org_vals if org_op == FilterOperator.IN else []
        org_not_in = org_vals if org_op == FilterOperator.NOT_IN else []
        repo_in = repo_vals if repo_op == FilterOperator.IN else []
        repo_not_in = repo_vals if repo_op == FilterOperator.NOT_IN else []

        by_id: dict[int, Repository] = {}
        discovery_complete = True

        if repo_in:
            for full_name in repo_in:
                if "/" not in full_name:
                    self.logger.error("Skipping malformed repo filter value (expected owner/repo): %s", full_name)
                    discovery_complete = False
                    continue
                owner, name = full_name.split("/", 1)
                res = await c.runtime.ds_call(c.data_source.get_repo, owner, name)
                if not res.success or not res.data:
                    self.logger.error("Repository not found or inaccessible: %s (%s)", full_name, res.error)
                    discovery_complete = False
                    continue
                by_id[int(res.data.id)] = res.data
        else:
            orgs = org_in or await c.users._resolve_target_orgs()
            for org in orgs:
                res = await c.runtime.ds_call(c.data_source.list_org_repos, org)
                if not res.success:
                    self.logger.error("Could not list repos for org %s: %s", org, res.error)
                    discovery_complete = False
                    continue
                for r in res.data or []:
                    by_id[int(r.id)] = r

        candidates = list(by_id.values())
        if repo_not_in:
            excluded = set(repo_not_in)
            candidates = [r for r in candidates if getattr(r, "full_name", None) not in excluded]
        if org_not_in:
            excluded_orgs = set(org_not_in)
            candidates = [r for r in candidates if getattr(r.owner, "login", None) not in excluded_orgs]

        return candidates, discovery_complete

    # ------------------------------------------------------------------
    # Repo member / permission sync
    # ------------------------------------------------------------------

    async def _sync_repo_members(self, owner: str, repo: str) -> list[Permission]:
        """Collaborators + teams -> permissions. Overridden in the personal connector."""
        c = self.c
        permissions: list[Permission] = []

        collab_res = await c.runtime.ds_call(
            c.data_source.list_collaborators, owner, repo, AFFILIATION_ALL
        )
        if not collab_res.success:
            raise RuntimeError(f"list_collaborators failed: {collab_res.error}")
        for user in collab_res.data or []:
            perm = await self._transform_collaborator_to_permission(user)
            if perm:
                permissions.append(perm)

        teams_res = await c.runtime.ds_call(c.data_source.list_repo_teams, owner, repo)
        if not teams_res.success:
            self.logger.warning(
                "Could not list teams for %s/%s: %s (collaborator permissions still applied).",
                owner, repo, teams_res.error,
            )
        else:
            for team in teams_res.data or []:
                perm = await self._transform_team_to_permission(owner, team)
                if perm:
                    permissions.append(perm)

        creator_permission = c.creator_user_permission()
        if creator_permission is not None and not any(
            getattr(p, "email", None) == creator_permission.email for p in permissions
        ):
            permissions.append(creator_permission)

        return permissions

    async def _transform_collaborator_to_permission(self, user: NamedUser) -> Permission | None:
        """``NamedUser.permissions`` -> role -> ``Permission`` (USER, or pseudo-GROUP fallback)."""
        role = _highest_role_from_collaborator_permissions(getattr(user, "permissions", None))
        ptype = _permission_type_from_role(role)
        if ptype is None:
            return None
        return await self._create_user_permission(str(user.id), ptype)

    async def _transform_team_to_permission(self, org: str, team: Team) -> Permission | None:
        """GROUP permission by team slug; also replaces the team's membership edges.

        Fetching membership on every sync (rather than diffing) means
        add/remove-team-member is picked up automatically via
        ``on_new_user_groups``'s "delete existing edges, recreate" semantics.
        """
        c = self.c
        role = getattr(team, "permission", None)
        ptype = _permission_type_from_role(role)
        if ptype is None:
            return None

        members_res = await c.runtime.ds_call(c.data_source.list_team_members, org, team.slug)
        app_users = []
        if not members_res.success:
            self.logger.warning(
                "Could not list members for team %s/%s: %s; membership edges will not be updated this sync.",
                org, team.slug, members_res.error,
            )
        else:
            async with c.data_store_provider.transaction() as tx_store:
                for member in members_res.data or []:
                    resolved_user = await tx_store.get_user_by_source_id(
                        source_user_id=str(member.id), connector_id=c.connector_id,
                    )
                    if resolved_user:
                        app_users.append(resolved_user)

            team_group = AppUserGroup(
                app_name=c.connector_name,
                connector_id=c.connector_id,
                source_user_group_id=str(team.id),
                name=team.name,
                org_id=c.data_entities_processor.org_id,
            )
            await c.data_entities_processor.on_new_user_groups([(team_group, app_users)])

        return Permission(external_id=str(team.id), type=ptype, entity_type=EntityType.GROUP)

    async def _create_user_permission(self, source_user_id: str, ptype: PermissionType) -> Permission | None:
        """Look up an AppUser by GitHub numeric id; fall back to a pseudo-group.

        Mirrors GitLab's ``_create_permission_from_principal`` so a
        collaborator resolved after Phase 1-4 email resolution (or still only
        pseudo-grouped after Phase 5) gets the correct permission edge either way.
        """
        c = self.c
        try:
            async with c.data_store_provider.transaction() as tx_store:
                user = await tx_store.get_user_by_source_id(
                    source_user_id=source_user_id, connector_id=c.connector_id,
                )
                if user:
                    return Permission(email=user.email, type=ptype, entity_type=EntityType.USER)

                pseudo_group = await tx_store.get_user_group_by_external_id(
                    connector_id=c.connector_id, external_id=source_user_id,
                )
                if not pseudo_group:
                    pseudo_group = await self._create_pseudo_group(source_user_id)
                if pseudo_group:
                    return Permission(
                        external_id=pseudo_group.source_user_group_id,
                        type=ptype,
                        entity_type=EntityType.GROUP,
                    )
                return None
        except Exception as e:
            self.logger.error("Failed to create permission for GitHub user %s: %s", source_user_id, e)
            return None

    async def _create_pseudo_group(self, github_user_id: str) -> AppUserGroup | None:
        """Create a pseudo-group for a collaborator without a resolvable email yet."""
        c = self.c
        try:
            pseudo_group = AppUserGroup(
                app_name=c.connector_name,
                connector_id=c.connector_id,
                source_user_group_id=github_user_id,
                name=f"{PSEUDO_USER_GROUP_PREFIX}_{github_user_id}",
                org_id=c.data_entities_processor.org_id,
            )
            await c.data_entities_processor.on_new_user_groups([(pseudo_group, [])])
            return pseudo_group
        except Exception as e:
            self.logger.error("Failed to create pseudo-group for GitHub user %s: %s", github_user_id, e)
            return None

    def _creator_only_permissions(self) -> list[Permission]:
        creator_permission = self.c.creator_user_permission()
        return [creator_permission] if creator_permission is not None else []

    # ------------------------------------------------------------------
    # Record group hierarchy
    # ------------------------------------------------------------------

    async def _create_record_group_hierarchy(self, repo: Repository, permissions: list[Permission]) -> None:
        """Create/update the repo RG and its three children (work-items, PRs, code).

        Every permission maps to all four groups (org RG excluded — it is
        upserted once at the end of the sync with the accumulated union of
        every repo's permissions in that org). ``external_group_id`` is
        anchored on the stable numeric ``repo.id``, so this is safe to call
        on every sync regardless of intervening renames.
        """
        c = self.c
        org_login = repo.owner.login
        repo_rg = RecordGroup(
            org_id=c.data_entities_processor.org_id,
            name=repo.full_name,
            group_type=RecordGroupType.REPOSITORY.value,
            connector_name=c.connector_name,
            connector_id=c.connector_id,
            external_group_id=str(repo.id),
            parent_external_group_id=self._org_parent_external_id(org_login),
            web_url=getattr(repo, "html_url", None),
        )
        work_items_rg = RecordGroup(
            org_id=c.data_entities_processor.org_id,
            name="Issues",
            group_type=RecordGroupType.PROJECT.value,
            connector_name=c.connector_name,
            connector_id=c.connector_id,
            external_group_id=f"{repo.id}-work-items",
            parent_external_group_id=str(repo.id),
        )
        pull_requests_rg = RecordGroup(
            org_id=c.data_entities_processor.org_id,
            name="Pull requests",
            group_type=RecordGroupType.PROJECT.value,
            connector_name=c.connector_name,
            connector_id=c.connector_id,
            external_group_id=f"{repo.id}-pull-requests",
            parent_external_group_id=str(repo.id),
        )
        code_repo_rg = RecordGroup(
            org_id=c.data_entities_processor.org_id,
            name="Code repository",
            group_type=RecordGroupType.PROJECT.value,
            connector_name=c.connector_name,
            connector_id=c.connector_id,
            external_group_id=f"{repo.id}-code-repository",
            parent_external_group_id=str(repo.id),
        )
        await c.data_entities_processor.on_new_record_groups(
            [
                (repo_rg, permissions),
                (work_items_rg, permissions),
                (pull_requests_rg, permissions),
                (code_repo_rg, permissions),
            ]
        )

    def _org_parent_external_id(self, org_login: str) -> str:
        return f"org-{org_login}"

    def _accumulate_org_permissions(self, org_login: str, permissions: list[Permission]) -> None:
        """Union this repo's permissions into the running per-org set.

        Keyed on ``(entity_type, email_or_external_id)`` so the same
        principal appearing on multiple repos in the org is not duplicated.
        Flushed once via ``_flush_org_record_groups`` after all repos are processed.
        """
        bucket = self._org_permission_accumulator.setdefault(org_login, {})
        for p in permissions:
            key = (p.entity_type.value, p.email or p.external_id or "")
            existing = bucket.get(key)
            if existing is None or _permission_rank(p.type) > _permission_rank(existing.type):
                bucket[key] = p

    async def _flush_org_record_groups(self) -> None:
        """Upsert one org-level ``RecordGroup`` per org touched this sync.

        Runs after every repo in the org has been processed (accumulator is
        complete), and after ``_create_record_group_hierarchy`` has already
        created repo RGs referencing this org RG as a placeholder parent —
        ``on_new_record_groups`` finds and updates that placeholder in place.
        """
        c = self.c
        for org_login, bucket in self._org_permission_accumulator.items():
            org_rg = RecordGroup(
                org_id=c.data_entities_processor.org_id,
                name=org_login,
                group_type=RecordGroupType.REPOSITORY.value,
                connector_name=c.connector_name,
                connector_id=c.connector_id,
                external_group_id=self._org_parent_external_id(org_login),
                web_url=f"https://github.com/{org_login}",
            )
            await c.data_entities_processor.on_new_record_groups([(org_rg, list(bucket.values()))])

    # ------------------------------------------------------------------
    # Deleted / transferred repo detection
    # ------------------------------------------------------------------

    async def _detect_deleted_repos(self, current_ids: set[int]) -> None:
        """Diff the current repo listing against the last-known inventory and cascade-delete
        repos that disappeared (deleted, or transferred out of every synced org).
        """
        c = self.c
        sync_point = self._inventory_sync_point()
        state = await sync_point.read_sync_point(GitHubLiterals.REPO_INVENTORY.value)
        previous_ids = set(state.get(GitHubLiterals.REPO_IDS.value, []))
        removed_ids = previous_ids - current_ids
        if not removed_ids:
            return

        self.logger.info("Detected %s GitHub repo(s) removed from sync scope: %s", len(removed_ids), removed_ids)
        for repo_id in removed_ids:
            try:
                await self._cascade_delete_repo(repo_id)
            except Exception as e:
                self.logger.error("Failed to cascade-delete GitHub repo id=%s: %s", repo_id, e, exc_info=True)

    async def _cascade_delete_repo(self, repo_id: int) -> None:
        """Delete a repo's records (recursively) then its four record groups."""
        c = self.c
        child_external_ids = [
            str(repo_id),
            f"{repo_id}-work-items",
            f"{repo_id}-pull-requests",
            f"{repo_id}-code-repository",
        ]
        for external_id in child_external_ids:
            record_ids = await self._list_record_ids_for_group(external_id)
            if record_ids:
                await c.data_entities_processor.on_records_deleted_cascade(record_ids, c.connector_id)
            await c.data_entities_processor.on_record_group_deleted(external_id, c.connector_id)

    async def _list_record_ids_for_group(self, external_group_id: str) -> list[str]:
        """Page through every record under a record group (by external id) and
        return their internal DB ids, ready for ``on_records_deleted_cascade``."""
        c = self.c
        async with c.data_store_provider.transaction() as tx_store:
            rg = await tx_store.get_record_group_by_external_id(
                connector_id=c.connector_id, external_id=external_group_id,
            )
            if not rg:
                return []
            record_ids: list[str] = []
            offset = 0
            page_size = 500
            while True:
                page = await tx_store.get_records_by_status(
                    org_id=c.data_entities_processor.org_id,
                    connector_id=c.connector_id,
                    status_filters=None,
                    limit=page_size,
                    offset=offset,
                    record_group_id=rg.id,
                )
                if not page:
                    break
                record_ids.extend(r.id for r in page)
                if len(page) < page_size:
                    break
                offset += page_size
            return record_ids

    async def _update_repo_inventory(self, current_ids: set[int]) -> None:
        sync_point = self._inventory_sync_point()
        await sync_point.update_sync_point(
            GitHubLiterals.REPO_INVENTORY.value,
            {GitHubLiterals.REPO_IDS.value: sorted(current_ids)},
        )

    def _inventory_sync_point(self) -> SyncPoint:
        c = self.c
        return SyncPoint(
            connector_id=c.connector_id,
            org_id=c.data_entities_processor.org_id,
            sync_data_point_type=SyncDataPointType.RECORD_GROUPS,
            data_store_provider=c.data_store_provider,
        )


def _permission_rank(ptype: PermissionType) -> int:
    """Total order over ``PermissionType`` for keeping the highest grant per principal."""
    return {
        PermissionType.OWNER: 3,
        PermissionType.WRITE: 2,
        PermissionType.READ: 1,
        PermissionType.COMMENT: 1,
        PermissionType.OTHER: 0,
    }.get(ptype, 0)
