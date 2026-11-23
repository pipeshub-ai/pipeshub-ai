"""GitHub Teams: reading who may see a repository from GitHub.

Collaborators with their roles, and the read a repository's visibility gives (the whole company for a
public one, each org member for an internal one). The free connector keeps syncing repositories, issues,
pull requests and code through ``ProjectsSync``; without a licence key a company-wide connector reads
none of this (the processor's licence gate). The personal connector (``GitHubPersonalProjectsSync``)
overrides ``_sync_repo_members`` and ``_visibility_permissions``, so it reads none of it either.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.connectors.sources.github_teams.constants import AFFILIATION_ALL
from app.models.permission import EntityType, Permission, PermissionType

if TYPE_CHECKING:
    from app.sources.external.github.github_async import GhObject


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


class GitHubTeamsPermissionsMixin:
    """The permission-reading half of ``ProjectsSync`` (``self.c`` is the connector)."""

    async def _copies_no_source_access(self) -> bool:
        """Without a licence key a company-wide connector reads no access from GitHub. The processor then keeps
        every stored grant whatever is handed to it (``skips_permission_writes``), so a repository is written
        with no grants: still synced, and not skipped as one whose collaborators could not be read."""
        return await self.c.data_entities_processor.skips_permission_writes(self.c.connector_id)

    async def _repo_permissions(self, owner_login: str, repo_name: str, repo: GhObject) -> list[Permission] | None:
        """Collaborator and visibility grants of one repository; None skips it (see
        ``_permissions_without_collaborators``). The personal connector reads neither: it overrides both."""
        if await self._copies_no_source_access():
            return []
        c = self.c
        try:
            permissions = await self._sync_repo_members(owner_login, repo_name, repo)
        except Exception as e:
            permissions = self._permissions_without_collaborators(repo, e)
            if permissions is None:
                return None

        visibility_permissions = self._visibility_permissions(repo)
        if any(p.entity_type == EntityType.ORG for p in visibility_permissions):
            await c.data_entities_processor.ensure_team_app_edge(c.connector_id)
        permissions.extend(visibility_permissions)
        return permissions

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
            user = await c.data_entities_processor.get_user_by_source_id(
                source_user_id=source_user_id, connector_id=c.connector_id,
            )
            if user:
                return Permission(email=user.email, type=ptype, entity_type=EntityType.USER)
            # Unbound identity — counted once per repo in _sync_repo_members.
            return None
        except Exception as e:
            self.logger.error("Failed to create permission for GitHub user %s: %s", source_user_id, e)
            return None
