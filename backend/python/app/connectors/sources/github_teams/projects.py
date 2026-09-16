"""
Repository (project) synchronisation for the GitHub Teams connector.

Responsibilities:
- Resolve the set of repositories to sync (applying ``ORG_IDS`` / ``REPO_IDS`` filters).
- Create the org -> repo -> {work-items, pull-requests, code-repository} ``RecordGroup``
  hierarchy, keyed by the stable numeric ``repo.id`` (never the mutable ``full_name``).
- Map GitHub collaborator roles to USER ``Permission`` objects.
  ``affiliation=all`` already expands team members, outside collaborators,
  default-org-permission members, and owners — no separate team-as-group grant.
- Grant visibility-derived read: ORG on public repos, per org-member USER on
  internal repos. Private repos get only collaborator grants.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.sources.external.github.github_async import GhObject

from app.connectors.core.registry.filters import FilterOperator, SyncFilterKey
from app.models.entities import RecordGroup, RecordGroupType
from app.models.permission import EntityType, Permission, PermissionType

from .constants import AFFILIATION_ALL

if TYPE_CHECKING:
    from app.connectors.sources.github_teams.connector import GitHubTeamsConnector


def _highest_role_from_collaborator_permissions(perms: Any) -> str | None:
    """Reduce a collaborator ``permissions`` object to a single role string.

    Checked in descending order of privilege since the payload carries
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
        self._org_permission_accumulator: dict[int, dict[tuple[str, str], Permission]] = {}
        # org numeric id -> login (for the org group's display name / weburl)
        self._org_record_group_meta: dict[int, str] = {}
        # Orgs whose accumulated permissions changed since the last flush.
        self._dirty_org_ids: set[int] = set()

    # ------------------------------------------------------------------
    # Entry point
    # ------------------------------------------------------------------

    async def sync_all_repos(self) -> None:
        """Discover repos and run the per-repo sync pipeline."""
        c = self.c
        if not c.data_source:
            raise Exception("GitHub data source not initialized")

        self._org_permission_accumulator = {}
        self._org_record_group_meta = {}
        self._dirty_org_ids = set()

        repos = await self._resolve_repos_with_filters()
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

        # Each repo already flushed its own org, so this is normally a no-op; it
        # exists to retry an org left dirty by a failed mid-sync flush.
        await self._flush_org_record_groups()

    async def _sync_repo(self, repo: GhObject) -> None:
        """Sync one repo: permissions -> record group hierarchy -> issues/PRs/code."""
        c = self.c
        owner_login = repo.owner.login
        repo_name = repo.name

        try:
            permissions = await self._sync_repo_members(owner_login, repo_name, repo)
        except Exception as e:
            permissions = self._permissions_without_collaborators(repo, e)
            if permissions is None:
                return

        permissions.extend(self._visibility_permissions(repo))
        permissions = _dedupe_highest_permissions(permissions)
        self._accumulate_org_permissions(repo.owner, permissions)
        # Before the repo group, not after every repo: the org group is the only
        # one the platform links to the App (a group with a parent never gets
        # that edge), and connector stats count by walking DOWN from the App.
        # Flushing at the end left every record of the sync unreachable — and so
        # counted as zero — until the final write. Flushing here also means the
        # repo group's parent lookup finds a real group instead of creating a
        # bare placeholder.
        await self._flush_org_record_groups()
        await self._create_record_group_hierarchy(repo, permissions)

        for step_name, step in (
            # Issues and PRs page from their own endpoints: the /issues listing
            # returns PR stubs too, but those lack head refs and reviewers, and
            # recovering them cost one get_pull per PR.
            ("issues", lambda: c.issues.fetch_issues_batched(repo)),
            ("pull_requests", lambda: c.pull_requests.fetch_prs_batched(repo)),
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

    async def _resolve_repos_with_filters(self) -> list[GhObject]:
        """Resolve repos to sync from sync filters.

        Semantics:
        - ``REPO_IDS IN`` is authoritative: only listed repos sync (values are
          ``owner/repo`` full names).
        - ``ORG_IDS IN`` (without ``REPO_IDS IN``): all repos under each listed org.
        - Neither filter: discover every org visible to the token.
        - ``NOT_IN`` variants are subtractive.
        """
        c = self.c
        sf = c.sync_filters
        org_f = sf.get(SyncFilterKey.ORG_IDS) if sf else None
        repo_f = sf.get(SyncFilterKey.REPO_IDS) if sf else None
        org_vals = list(org_f.value) if (org_f and not org_f.is_empty()) else []  # type: ignore[arg-type]
        repo_vals = list(repo_f.value) if (repo_f and not repo_f.is_empty()) else []  # type: ignore[arg-type]
        org_op = org_f.operator_value if org_vals else None
        repo_op = repo_f.operator_value if repo_vals else None

        org_in = org_vals if org_op == FilterOperator.IN else []
        org_not_in = org_vals if org_op == FilterOperator.NOT_IN else []
        repo_in = repo_vals if repo_op == FilterOperator.IN else []
        repo_not_in = repo_vals if repo_op == FilterOperator.NOT_IN else []

        by_id: dict[int, GhObject] = {}

        if repo_in:
            for full_name in repo_in:
                if "/" not in full_name:
                    self.logger.error("Skipping malformed repo filter value (expected owner/repo): %s", full_name)
                    continue
                owner, name = full_name.split("/", 1)
                res = await c.runtime.ds_call(c.data_source.get_repo, owner, name)
                if not res.success or not res.data:
                    self.logger.error("Repository not found or inaccessible: %s (%s)", full_name, res.error)
                    continue
                by_id[int(res.data.id)] = res.data
        else:
            if org_in:
                orgs, orgs_ok = org_in, True
            else:
                orgs, orgs_ok = await c.users._resolve_target_orgs()
            if not orgs_ok:
                self.logger.error("GitHub org discovery failed; no repositories to sync this run.")
                return []
            for org in orgs:
                res = await c.runtime.ds_call(c.data_source.list_org_repos, org)
                if not res.success:
                    self.logger.error("Could not list repos for org %s: %s", org, res.error)
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
        return candidates

    # ------------------------------------------------------------------
    # Repo member / permission sync
    # ------------------------------------------------------------------

    async def _sync_repo_members(
        self, owner: str, repo: str, repo_obj: GhObject | None = None,
    ) -> list[Permission]:
        """Collaborators -> USER permissions. Overridden in the personal connector.

        ``affiliation=all`` already includes people who reach the repo through a
        team, so a second GitHub-team AppUserGroup grant would only duplicate
        those USER edges.
        """
        c = self.c
        permissions: list[Permission] = []

        collab_res = await c.runtime.ds_call(
            c.data_source.list_collaborators, owner, repo, AFFILIATION_ALL
        )
        if not collab_res.success:
            raise CollaboratorsUnavailable(
                f"list_collaborators failed: {collab_res.error}",
                status_code=getattr(collab_res, "status_code", None),
            )
        collaborators = collab_res.data or []

        # Individual-owned repo (owner is a user account, not an org): there is
        # no org to enumerate members from, so these collaborators exist in no
        # principal listing and would all be unbound below — a private repo
        # visible to nobody. Bind them to AppUsers up front; the unchanged
        # grant loop then resolves them exactly like org principals. Org-owned
        # repos never enter this branch.
        owner_type = getattr(getattr(repo_obj, "owner", None), "type", None)
        if owner_type == "User" and collaborators:
            await c.users.resolve_collaborator_principals({
                int(u.id): u
                for u in collaborators
                if getattr(u, "id", None) is not None
            })

        for user in collaborators:
            perm = await self._transform_collaborator_to_permission(user)
            if perm:
                permissions.append(perm)
        if len(permissions) < len(collaborators):
            self.logger.debug(
                "%s/%s: %s of %s collaborator(s) granted; the rest are identities not yet "
                "bound to a PipesHub user (they gain access once resolved) or unmapped roles.",
                owner, repo, len(permissions), len(collaborators),
            )

        return permissions

    def _permissions_without_collaborators(
        self, repo: GhObject, error: Exception
    ) -> list[Permission] | None:
        """Decide what to do when the collaborator listing failed.

        Returns the permission list to continue with, or ``None`` to skip the
        repo. Skipping matters because ``on_new_record_groups`` DELETES a record
        group's permission edges before recreating them from the list passed in
        — syncing an empty list would revoke everyone until the next good sync,
        whereas writing nothing leaves last sync's edges and content untouched.

        The rule is therefore not "always skip on failure" but "never write an
        ACL we cannot justify". Visibility alone yields a complete, correct floor
        for public and internal repos, so those can sync without the collaborator
        list at all — it would only have added finer WRITE/OWNER grants we are not
        entitled to see. A private repo has no floor, so it still skips.

        Only a *structural* denial takes the floor: ``/collaborators`` requires
        push access, so read-only access to any repo returns 403 permanently. A
        transient failure keeps the old behaviour, since downgrading every
        maintainer to READ for one cycle is worse than syncing nothing.
        """
        structural = isinstance(error, CollaboratorsUnavailable) and error.is_structural
        floor = self._visibility_permissions(repo) if structural else []

        if structural and floor:
            # Expected for any repo the token can read but not push to (public /
            # internal repos outside our orgs) — routine, not a fault.
            self.logger.info(
                "Collaborators not listable for %s (HTTP %s — endpoint needs push access); "
                "syncing with visibility-derived access (%s grant(s)).",
                repo.full_name, getattr(error, "status_code", None), len(floor),
            )
            return []

        if structural:
            self.logger.error(
                "Cannot list collaborators for %s (%s) and its visibility (%s) implies no "
                "access for anyone, so there is no safe permission set to write. Skipping "
                "this repo — grant the connector token push access on it, or drop it from "
                "the repo filter.",
                repo.full_name, error, getattr(repo, "visibility", "private"),
            )
            return None

        self.logger.error(
            "Error resolving members for %s: %s. Skipping this repo for this run; "
            "its existing permissions and content are left unchanged.",
            repo.full_name, error, exc_info=True,
        )
        return None

    def _visibility_permissions(self, repo: GhObject) -> list[Permission]:
        """Grants implied by the repo's visibility rather than by any explicit
        collaborator relationship.

        Visibility-derived access appears in no listing — a public repo does not
        enumerate all of GitHub as collaborators — so it has to be modelled from
        the visibility value itself.

        ``public``: readable by anyone with a GitHub account, so mirroring it as
        readable by the whole PipesHub org matches reality — those users could
        open it on github.com regardless.

        ``internal``: "Organization members have read permissions to all internal
        repositories in an enterprise, including those in organizations they are
        not a member of", and internal repos are explicitly NOT visible to
        outside collaborators. An ORG grant would therefore over-grant on both
        counts (PipesHub users outside the enterprise, and outside
        collaborators), so this emits one USER grant per resolved org member
        instead. Members of enterprise orgs this connector does not sync are
        missed — an under-grant, and the safe direction.

        ``private``: nothing; access comes solely from collaborator grants.
        """
        visibility = (getattr(repo, "visibility", None) or "").lower()
        if not visibility:
            # Older payloads expose only the `private` boolean.
            visibility = "private" if getattr(repo, "private", True) else "public"
        if visibility == "public":
            return [Permission(type=PermissionType.READ, entity_type=EntityType.ORG)]
        if visibility == "internal":
            return [
                Permission(email=email, type=PermissionType.READ, entity_type=EntityType.USER)
                for email in sorted(self.c.users.org_member_emails())
            ]
        return []

    async def _transform_collaborator_to_permission(self, user: GhObject) -> Permission | None:
        """``NamedUser.permissions`` -> highest role -> ``Permission``."""
        role = _highest_role_from_collaborator_permissions(getattr(user, "permissions", None))
        ptype = _permission_type_from_role(role)
        if ptype is None:
            self.logger.warning(
                "Collaborator %s carries role %r, which maps to no PermissionType; "
                "granting nothing. Custom repository roles are not yet mapped.",
                getattr(user, "login", user.id), role,
            )
            return None
        return await self._create_user_permission(str(user.id), ptype)

    async def _create_user_permission(self, source_user_id: str, ptype: PermissionType) -> Permission | None:
        """Look up an ``AppUser`` by GitHub numeric id and build its permission.

        Returns ``None`` when the principal was never bound to a PipesHub user,
        which grants nothing. There is no placeholder to fall back to: a
        principal with no ``AppUser`` has no identity to grant access to, and
        because ``on_new_record_groups`` rebuilds a record group's permission
        edges every sync, the edge appears on its own once the identity resolves.
        """
        c = self.c
        try:
            async with c.data_store_provider.transaction() as tx_store:
                user = await tx_store.get_user_by_source_id(
                    source_user_id=source_user_id, connector_id=c.connector_id,
                )
                if user:
                    return Permission(email=user.email, type=ptype, entity_type=EntityType.USER)
                # Unbound identity — counted once per repo in _sync_repo_members.
                return None
        except Exception as e:
            self.logger.error("Failed to create permission for GitHub user %s: %s", source_user_id, e)
            return None

    # ------------------------------------------------------------------
    # Record group hierarchy
    # ------------------------------------------------------------------

    async def _create_record_group_hierarchy(self, repo: GhObject, permissions: list[Permission]) -> None:
        """Create/update the repo RG and its three children (work-items, PRs, code).

        Every permission maps to all four groups (org RG excluded — it carries
        the accumulated union of every repo's permissions in that org and is
        upserted by ``_flush_org_record_groups``). ``external_group_id`` is
        anchored on the stable numeric ``repo.id``, so this is safe to call
        on every sync regardless of intervening renames.
        """
        c = self.c
        repo_rg = RecordGroup(
            org_id=c.data_entities_processor.org_id,
            name=repo.full_name,
            group_type=RecordGroupType.REPOSITORY.value,
            connector_name=c.connector_name,
            connector_id=c.connector_id,
            external_group_id=str(repo.id),
            parent_external_group_id=self._org_parent_external_id(repo.owner.id),
            web_url=getattr(repo, "html_url", None),
        )
        # The ACL lives ONLY on the repo group. The three child groups set
        # inherit_permissions=True, which makes the processor write a
        # child->parent INHERIT_PERMISSIONS edge; records already inherit into
        # their child group, so access resolves record -> child -> repo (2 hops,
        # inside every access-query bound). One ACL written/deleted per repo per
        # sync instead of four identical copies.
        #
        # The repo group itself must NOT inherit: its parent (org-{id})
        # carries the union of every repo's grants, and inheriting that union
        # would leak each repo to every other repo's users.
        work_items_rg = RecordGroup(
            org_id=c.data_entities_processor.org_id,
            name="Issues",
            group_type=RecordGroupType.PROJECT.value,
            connector_name=c.connector_name,
            connector_id=c.connector_id,
            external_group_id=f"{repo.id}-work-items",
            parent_external_group_id=str(repo.id),
            inherit_permissions=True,
        )
        pull_requests_rg = RecordGroup(
            org_id=c.data_entities_processor.org_id,
            name="Pull requests",
            group_type=RecordGroupType.PROJECT.value,
            connector_name=c.connector_name,
            connector_id=c.connector_id,
            external_group_id=f"{repo.id}-pull-requests",
            parent_external_group_id=str(repo.id),
            inherit_permissions=True,
        )
        code_repo_rg = RecordGroup(
            org_id=c.data_entities_processor.org_id,
            name="Code repository",
            group_type=RecordGroupType.PROJECT.value,
            connector_name=c.connector_name,
            connector_id=c.connector_id,
            external_group_id=f"{repo.id}-code-repository",
            parent_external_group_id=str(repo.id),
            inherit_permissions=True,
        )
        await c.data_entities_processor.on_new_record_groups(
            [
                (repo_rg, permissions),
                (work_items_rg, []),
                (pull_requests_rg, []),
                (code_repo_rg, []),
            ]
        )

    def _org_parent_external_id(self, org_numeric_id: int) -> str:
        # Numeric owner id, not login: orgs can be renamed, and a login-keyed
        # id would orphan the old group and re-create it under the new name.
        return f"org-{org_numeric_id}"

    def _accumulate_org_permissions(self, owner: Any, permissions: list[Permission]) -> None:
        """Union this repo's permissions into the running per-org set.

        Keyed on ``(entity_type, email_or_external_id)`` so the same
        principal appearing on multiple repos in the org is not duplicated.

        Marks the org dirty only when this repo actually widened the set, so
        the per-repo flush costs one write per *change* rather than one per
        repo. A first-seen org is always dirty even with no permissions — the
        group still has to exist for its records to be reachable.
        """
        org_id = int(owner.id)
        self._org_record_group_meta[org_id] = owner.login
        bucket = self._org_permission_accumulator.get(org_id)
        changed = bucket is None
        if bucket is None:
            bucket = self._org_permission_accumulator[org_id] = {}
        for p in permissions:
            key = (p.entity_type.value, p.email or p.external_id or "")
            existing = bucket.get(key)
            if existing is None or _permission_rank(p.type) > _permission_rank(existing.type):
                bucket[key] = p
                changed = True
        if changed:
            self._dirty_org_ids.add(org_id)

    async def _flush_org_record_groups(self) -> None:
        """Upsert the org-level ``RecordGroup`` for every org whose set changed.

        Called once per repo rather than once per sync so the group — and the
        BELONGS_TO edge to the App that only it carries — exists before any
        record is written. The permission set is a union that only ever widens,
        so an intermediate flush under-grants relative to the final state,
        never over-grants, and the last one lands on exactly the set a
        single end-of-sync flush would have produced.

        ``on_new_record_groups`` upserts by external id, so re-flushing updates
        the same group in place.
        """
        c = self.c
        for org_id in sorted(self._dirty_org_ids):
            bucket = self._org_permission_accumulator.get(org_id) or {}
            org_login = self._org_record_group_meta[org_id]
            org_rg = RecordGroup(
                org_id=c.data_entities_processor.org_id,
                name=org_login,
                group_type=RecordGroupType.REPOSITORY.value,
                connector_name=c.connector_name,
                connector_id=c.connector_id,
                external_group_id=self._org_parent_external_id(org_id),
                web_url=f"https://github.com/{org_login}",
            )
            await c.data_entities_processor.on_new_record_groups([(org_rg, list(bucket.values()))])
            # Cleared per org, after its own write: a failure leaves that org
            # dirty so the end-of-sync flush retries it.
            self._dirty_org_ids.discard(org_id)


class CollaboratorsUnavailable(Exception):
    """The repo's collaborator listing could not be read.

    Carries the HTTP status so the caller can tell a structural denial (403 —
    ``/collaborators`` requires *push* access, so any repo we can only read
    returns this permanently) from a transient failure worth preserving the
    previous sync's grants for.
    """

    def __init__(self, message: str, *, status_code: int | None) -> None:
        super().__init__(message)
        self.status_code = status_code

    @property
    def is_structural(self) -> bool:
        return self.status_code in (403, 404)


def _dedupe_highest_permissions(permissions: list[Permission]) -> list[Permission]:
    """Collapse repeated grants for one principal, keeping the strongest.

    An internal repo grants READ to every org member, which restates anyone who
    already holds WRITE or OWNER as a collaborator. Two edges for one principal
    would otherwise reach ``on_new_record_groups``.
    """
    best: dict[tuple[str, str], Permission] = {}
    for p in permissions:
        key = (p.entity_type.value, p.email or p.external_id or "")
        existing = best.get(key)
        if existing is None or _permission_rank(p.type) > _permission_rank(existing.type):
            best[key] = p
    return list(best.values())


def _permission_rank(ptype: PermissionType) -> int:
    """Total order over ``PermissionType`` for keeping the highest grant per principal."""
    return {
        PermissionType.OWNER: 3,
        PermissionType.WRITE: 2,
        PermissionType.READ: 1,
        PermissionType.COMMENT: 1,
        PermissionType.OTHER: 0,
    }.get(ptype, 0)
