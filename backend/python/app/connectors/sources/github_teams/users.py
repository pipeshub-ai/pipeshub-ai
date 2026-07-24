"""
User synchronisation for the GitHub Teams connector.

Implements the 5-phase email-resolution strategy (adapted from the Jira DC
reverse-lookup pattern, novel Phase 3 specific to GitHub):

1. Visible emails      — org member profiles with a public email set.
2. Cached AppUsers      — DB lookup by GitHub numeric id from a prior sync.
3. Commit email extraction — ``search/commits?q=author:{login}`` and read
   ``commit.author.email`` off the hit (skips GitHub's noreply placeholder).
4. Platform reverse lookup — for each known PipesHub email not yet matched,
   ``search/commits?q=author-email:{email}`` (works even for private
   profile emails) with a ``search/users`` fallback for public-only matches.
5. Pseudo-group fallback — preserves permissions for members who still have
   no resolvable email; migrated automatically once a real AppUser appears.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

from app.connectors.core.registry.filters import FilterOperator, SyncFilterKey
from app.models.entities import AppUser, AppUserGroup

from .constants import (
    NOREPLY_EMAIL_SUFFIX,
    PSEUDO_USER_GROUP_PREFIX,
    _GITHUB_USER_ENRICHMENT_CONCURRENCY,
)

if TYPE_CHECKING:
    from app.connectors.sources.github_teams.connector import GitHubTeamsConnector


def _filter_op_val(f: Any) -> str:
    """Lower-cased string value of a Filter's operator."""
    op = f.operator
    return (op.value if hasattr(op, "value") else str(op)).lower()


def _is_noreply_email(email: str | None) -> bool:
    """True for GitHub's private-email placeholder (``{id}+{login}@users.noreply.github.com``).

    Confirms identity but is not usable as a real email for permission matching.
    """
    return bool(email) and NOREPLY_EMAIL_SUFFIX in email.lower()


class UsersSync:
    """Handles all GitHub org member synchronisation steps for ``GitHubTeamsConnector``."""

    def __init__(self, connector: "GitHubTeamsConnector") -> None:
        self.c = connector
        self.logger = connector.logger

    # ------------------------------------------------------------------
    # Entry point
    # ------------------------------------------------------------------

    async def sync_users(self) -> None:
        """Resolve org members to AppUsers via the 5-phase email-resolution pipeline."""
        c = self.c
        if not c.data_source:
            raise Exception("GitHub data source not initialized")

        orgs = await self._resolve_target_orgs()
        if not orgs:
            self.logger.warning("No GitHub organizations resolved; skipping user sync.")
            return
        self.logger.info("GitHub Teams user sync: resolving members for org(s) %s", orgs)

        dict_member: dict[int, Any] = {}
        any_success = False
        for org in orgs:
            res = await c.runtime.ds_call(c.data_source.list_org_members, org)
            if not res.success:
                self.logger.error("Failed to list members for org %s: %s", org, res.error)
                continue
            any_success = True
            for m in res.data or []:
                mid = getattr(m, "id", None)
                if mid is not None:
                    dict_member[mid] = m

        creator_added = self._inject_creator_member_into(dict_member)
        if not any_success and not creator_added:
            raise RuntimeError(
                "GitHub Teams user sync aborted: every configured org failed to "
                "enumerate members and no creator identity was resolved (cannot fall back)."
            )
        if not dict_member:
            self.logger.warning("No GitHub org members found across orgs %s", orgs)
            return

        # ---- Phase 1: visible emails ----
        resolved_email: dict[int, str] = {}
        unresolved_ids: set[int] = set()
        for uid, member in dict_member.items():
            visible_email = self._plain_email_attr(member)
            if visible_email:
                resolved_email[uid] = visible_email
            else:
                unresolved_ids.add(uid)

        if unresolved_ids:
            enriched = await self._enrich_members_with_full_profile(dict_member, unresolved_ids)
            for uid, full_member in enriched.items():
                dict_member[uid] = full_member
                email = self._plain_email_attr(full_member)
                if email:
                    resolved_email[uid] = email
                    unresolved_ids.discard(uid)

        self.logger.info(
            "Phase 1 (visible emails): %s/%s resolved", len(resolved_email), len(dict_member)
        )

        # ---- Phase 2: cached AppUsers ----
        if unresolved_ids:
            newly = await self._resolve_cached_users(unresolved_ids)
            resolved_email.update(newly)
            unresolved_ids -= set(newly.keys())
            self.logger.info("Phase 2 (cached AppUsers): %s additional resolved", len(newly))

        # ---- Phase 3: commit email extraction ----
        if unresolved_ids:
            newly = await self._resolve_via_commit_emails(
                {uid: dict_member[uid] for uid in unresolved_ids}, orgs,
            )
            resolved_email.update(newly)
            unresolved_ids -= set(newly.keys())
            self.logger.info("Phase 3 (commit email extraction): %s additional resolved", len(newly))

        # ---- Phase 4: platform reverse lookup ----
        if unresolved_ids:
            newly = await self._reverse_lookup_platform_users(
                {uid: dict_member[uid] for uid in unresolved_ids}, resolved_email, orgs,
            )
            resolved_email.update(newly)
            unresolved_ids -= set(newly.keys())
            self.logger.info("Phase 4 (platform reverse lookup): %s additional resolved", len(newly))

        # ---- Persist AppUsers for everyone resolved so far ----
        await self._persist_app_users(dict_member, resolved_email)

        # ---- Phase 5: pseudo-group fallback for the remainder ----
        if unresolved_ids:
            await self._create_pseudo_groups({uid: dict_member[uid] for uid in unresolved_ids})
            self.logger.info("Phase 5 (pseudo-groups): %s member(s) pseudo-grouped", len(unresolved_ids))

        self.logger.info(
            "GitHub Teams user sync complete: %s resolved, %s pseudo-grouped",
            len(resolved_email), len(unresolved_ids),
        )

    # ------------------------------------------------------------------
    # Org scope resolution
    # ------------------------------------------------------------------

    async def _resolve_target_orgs(self) -> list[str]:
        """Resolve which org logins to sync users for.

        ``ORG_IDS IN`` is authoritative. Without it, ``REPO_IDS IN`` narrows
        scope to the distinct owning orgs of the configured repos. With
        neither filter (or a ``NOT_IN`` variant), discover every org the
        token can see via ``list_user_orgs`` (requires ``read:org``).
        """
        c = self.c
        sf = c.sync_filters
        org_f = sf.get(SyncFilterKey.ORG_IDS) if sf else None
        repo_f = sf.get(SyncFilterKey.REPO_IDS) if sf else None
        org_active = org_f is not None and not org_f.is_empty()
        repo_active = repo_f is not None and not repo_f.is_empty()

        if org_active:
            op = _filter_op_val(org_f)
            if op == FilterOperator.IN:
                return list(org_f.value)  # type: ignore[arg-type]
            excluded = set(org_f.value)  # type: ignore[arg-type]
            return await self._list_all_visible_orgs(excluded)

        if repo_active:
            op = _filter_op_val(repo_f)
            if op == FilterOperator.IN:
                orgs = sorted({
                    full_name.split("/")[0]
                    for full_name in repo_f.value  # type: ignore[union-attr]
                    if "/" in full_name
                })
                if orgs:
                    return orgs

        return await self._list_all_visible_orgs(excluded=None)

    async def _list_all_visible_orgs(self, excluded: set[str] | None) -> list[str]:
        c = self.c
        res = await c.runtime.ds_call(c.data_source.list_user_orgs)
        if not res.success:
            self.logger.error("Failed to list GitHub orgs visible to this token: %s", res.error)
            return []
        logins = [getattr(o, "login", None) for o in (res.data or [])]
        logins = [login for login in logins if login]
        if excluded:
            logins = [login for login in logins if login not in excluded]
        return logins

    # ------------------------------------------------------------------
    # Phase 1: visible emails
    # ------------------------------------------------------------------

    @staticmethod
    def _plain_email_attr(member: Any) -> str | None:
        """Return ``member.email`` only when it is *already* populated in-memory.

        Never touches PyGithub's lazy-loading path (which would trigger an
        uncontrolled, un-retried, un-timed-out HTTP call on attribute access
        for a partial ``NamedUser``) — this only reads plain attributes off
        already-complete objects (the creator stub, or a member we already
        ran through ``get_user`` in Phase 1's enrichment step).
        """
        raw = member.__dict__.get("email") if isinstance(member, SimpleNamespace) else None
        if raw:
            return raw.strip() or None
        # For real PyGithub NamedUser objects, only trust email if the
        # object was constructed from a *complete* payload (get_user), which
        # PyGithub marks via `.completed`. Partial objects from list_org_members
        # must go through explicit enrichment instead.
        completed = getattr(member, "completed", False)
        if completed:
            email = getattr(member, "email", None)
            if isinstance(email, str) and email.strip():
                return email.strip()
        return None

    async def _enrich_members_with_full_profile(
        self, dict_member: dict[int, Any], ids_to_enrich: set[int]
    ) -> dict[int, Any]:
        """Fetch ``GET /users/:login`` per unresolved member to recover the public email.

        Concurrency bounded by a semaphore sized at
        ``_GITHUB_USER_ENRICHMENT_CONCURRENCY`` to avoid starving the shared
        executor pool. Falls back to the original (partial) member payload
        on any per-user failure so one misbehaving lookup does not abort the sweep.
        """
        import asyncio

        c = self.c
        sem = asyncio.Semaphore(_GITHUB_USER_ENRICHMENT_CONCURRENCY)

        async def fetch_full_user(uid: int) -> tuple[int, Any]:
            member = dict_member[uid]
            login = getattr(member, "login", None)
            if not login:
                return uid, member
            async with sem:
                res = await c.runtime.ds_call(c.data_source.get_user, login)
                if res.success and res.data is not None:
                    return uid, res.data
                self.logger.debug(
                    "Could not fetch full GitHub profile for %s (%s); using partial payload.",
                    login, getattr(res, "error", "unknown"),
                )
                return uid, member

        enriched: dict[int, Any] = {}
        for fut in asyncio.as_completed([fetch_full_user(uid) for uid in ids_to_enrich]):
            uid, obj = await fut
            enriched[uid] = obj
        return enriched

    # ------------------------------------------------------------------
    # Phase 2: cached AppUsers
    # ------------------------------------------------------------------

    async def _resolve_cached_users(self, unresolved_ids: set[int]) -> dict[int, str]:
        """DB lookup by GitHub numeric id from a prior sync — skips re-resolution."""
        c = self.c
        cached = await c.data_entities_processor.get_all_app_users(c.connector_id)
        cached_by_source_id = {
            u.source_user_id: u.email for u in cached if u.source_user_id and u.email
        }
        resolved: dict[int, str] = {}
        for uid in unresolved_ids:
            email = cached_by_source_id.get(str(uid))
            if email:
                resolved[uid] = email
        return resolved

    # ------------------------------------------------------------------
    # Phase 3: commit email extraction
    # ------------------------------------------------------------------

    async def _resolve_via_commit_emails(
        self, unresolved: dict[int, Any], orgs: list[str]
    ) -> dict[int, str]:
        """For each unresolved member, search commits they authored (by login) across
        the org(s) and read the raw git author email off the first usable hit.

        Early-exits per member across orgs once a usable (non-noreply) email is
        found, and stops the whole phase once every member is resolved.
        """
        c = self.c
        resolved: dict[int, str] = {}
        for uid, member in unresolved.items():
            login = getattr(member, "login", None)
            if not login:
                continue
            for org in orgs:
                res = await c.runtime.search_call(
                    c.data_source.search_commits_by_author_login, login, org
                )
                if not res.success or not res.data:
                    continue
                found_email = None
                for commit_result in res.data:
                    commit_obj = getattr(commit_result, "commit", None)
                    git_author = getattr(commit_obj, "author", None) if commit_obj else None
                    email = getattr(git_author, "email", None) if git_author else None
                    if email and not _is_noreply_email(email):
                        found_email = email
                        break
                if found_email:
                    resolved[uid] = found_email
                    break
        return resolved

    # ------------------------------------------------------------------
    # Phase 4: platform reverse lookup
    # ------------------------------------------------------------------

    async def _reverse_lookup_platform_users(
        self,
        unresolved: dict[int, Any],
        resolved_email: dict[int, str],
        orgs: list[str],
    ) -> dict[int, str]:
        """Reverse-lookup PipesHub-known emails against GitHub to resolve users whose
        GitHub profile email is private and who have no attributable commit history.

        For each candidate email (PipesHub org emails minus already-resolved
        ones), tries commit-search first (works for hidden emails), falling
        back to user-search (public emails only, single-match validated).
        Early-exits once every unresolved member has been matched.
        """
        c = self.c
        pipeshub_users = await c.data_entities_processor.get_all_active_users()
        pipeshub_emails = {
            u.email.lower() for u in pipeshub_users if getattr(u, "email", None)
        }
        already_resolved = {e.lower() for e in resolved_email.values()}
        candidate_emails = pipeshub_emails - already_resolved
        if not candidate_emails:
            return {}

        unresolved_by_login = {
            getattr(m, "login", None): uid for uid, m in unresolved.items() if getattr(m, "login", None)
        }
        newly_resolved: dict[int, str] = {}
        for email in candidate_emails:
            if not unresolved_by_login:
                break
            login = await self._try_resolve_email_to_login(email, orgs)
            if login and login in unresolved_by_login:
                uid = unresolved_by_login.pop(login)
                newly_resolved[uid] = email
        return newly_resolved

    async def _try_resolve_email_to_login(self, email: str, orgs: list[str]) -> str | None:
        """Best-effort email -> GitHub login lookup. Commit-search first (works for
        private profile emails); falls back to user-search (public emails only,
        accepted only on an unambiguous single match, mirroring the Jira DC pattern)."""
        c = self.c
        for org in orgs:
            res = await c.runtime.search_call(
                c.data_source.search_commits_by_author_email, email, org
            )
            if res.success and res.data:
                for commit_result in res.data:
                    author = getattr(commit_result, "author", None)
                    login = getattr(author, "login", None) if author else None
                    if login:
                        return login

        res = await c.runtime.search_call(c.data_source.search_users_by_email, email)
        if res.success and res.data:
            if len(res.data) == 1:
                return getattr(res.data[0], "login", None)
            for user in res.data:
                user_email = getattr(user, "email", None)
                if user_email and user_email.lower() == email.lower():
                    return getattr(user, "login", None)
        return None

    # ------------------------------------------------------------------
    # Creator injection
    # ------------------------------------------------------------------

    def _build_creator_member_stub(self) -> Any | None:
        """Return a member-shaped ``SimpleNamespace`` for the connector creator.

        ``None`` when we lack either the GitHub numeric id or the creator
        email — in that state the id-based bypass cannot round-trip cleanly.
        A ``SimpleNamespace`` (not a real ``NamedUser``) has no lazy-loading
        behaviour, so ``_plain_email_attr`` can trust its ``.email`` directly.
        """
        c = self.c
        if not c.creator_email or c._github_user_id is None:
            return None
        return SimpleNamespace(
            id=c._github_user_id,
            login=c._github_login or (c.creator_email.split("@")[0] or "creator"),
            name=c.creator_email,
            email=c.creator_email,
        )

    def _inject_creator_member_into(self, dict_member: dict[int, Any]) -> bool:
        """Ensure the connector creator is present in ``dict_member``.

        Uses ``setdefault`` so a real member row already in the dict is preserved.
        """
        creator = self._build_creator_member_stub()
        if creator is None:
            return False
        dict_member.setdefault(creator.id, creator)
        return True

    # ------------------------------------------------------------------
    # AppUser persistence
    # ------------------------------------------------------------------

    async def _persist_app_users(
        self, dict_member: dict[int, Any], resolved_email: dict[int, str]
    ) -> None:
        """Upsert ``AppUser`` rows for every resolved member and migrate any
        pseudo-group permissions created for them in a prior sync."""
        c = self.c
        if not resolved_email:
            return
        app_users: list[AppUser] = []
        for uid, email in resolved_email.items():
            member = dict_member.get(uid)
            full_name = (getattr(member, "name", None) or getattr(member, "login", None) or email) if member else email
            app_users.append(
                AppUser(
                    app_name=c.connector_name,
                    org_id=c.data_entities_processor.org_id,
                    connector_id=c.connector_id,
                    source_user_id=str(uid),
                    is_active=True,
                    email=email,
                    full_name=full_name,
                )
            )

        await c.data_entities_processor.on_new_app_users(app_users)
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

    # ------------------------------------------------------------------
    # Phase 5: pseudo-group fallback
    # ------------------------------------------------------------------

    async def _create_pseudo_groups(self, unresolved: dict[int, Any]) -> None:
        """Create a pseudo-``AppUserGroup`` per still-unresolved member.

        Preserves the permission edge until a real ``AppUser`` row appears
        (next sync's Phase 1-4, or the user makes their email public) — at
        which point ``migrate_group_to_user_by_external_id`` transfers the
        edges and deletes the pseudo-group.
        """
        c = self.c
        for uid, member in unresolved.items():
            try:
                login = getattr(member, "login", None) or str(uid)
                pseudo_group = AppUserGroup(
                    app_name=c.connector_name,
                    connector_id=c.connector_id,
                    source_user_group_id=str(uid),
                    name=f"{PSEUDO_USER_GROUP_PREFIX}_{login}",
                    org_id=c.data_entities_processor.org_id,
                )
                await c.data_entities_processor.on_new_user_groups([(pseudo_group, [])])
            except Exception as e:
                self.logger.error(
                    "Failed to create pseudo-group for GitHub member %s: %s", uid, e, exc_info=True
                )
