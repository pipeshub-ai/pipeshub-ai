"""
User synchronisation for the GitHub Teams connector.

Principals are the union of org members and *outside collaborators* — GitHub
excludes the latter from ``/orgs/{org}/members``, yet they hold real repo
permissions, so enumerating only members leaves them permanently unresolvable.

Each principal is resolved to an email by three phases, cheapest first:

A. Cached AppUsers    — DB lookup by GitHub numeric id from a prior sync (free).
B. Verified domains   — GraphQL ``organizationVerifiedDomainEmails``, batched
   with one aliased ``user(login:)`` field per member (~1 query per 100).
   Returns the *corporate* address even when the profile email is private —
   the only phase that beats the private-email ceiling. Needs an org-owner
   token and a verified domain on the org; anything less degrades silently.
C. Profile emails     — ``GET /users/{login}`` for the public profile email.

A principal whose email resolves to an active PipesHub user becomes an
``AppUser``; anyone else gets no node and no permission edge. There is no
placeholder: ``on_new_record_groups`` deletes and recreates a record group's
permission edges on every sync, so an identity that resolves later simply
gains its edge on that sync.
"""

from __future__ import annotations

import asyncio
import re
from typing import TYPE_CHECKING, Any

from app.connectors.core.registry.filters import FilterOperator, SyncFilterKey
from app.models.entities import AppUser

from .constants import (
    _GITHUB_USER_ENRICHMENT_CONCURRENCY,
    NOREPLY_EMAIL_SUFFIX,
    VERIFIED_DOMAIN_EMAIL_BATCH,
)

if TYPE_CHECKING:
    from app.connectors.sources.github_teams.connector import GitHubTeamsConnector


def _is_noreply_email(email: str | None) -> bool:
    """True for GitHub's private-email placeholder (``{id}+{login}@users.noreply.github.com``).

    Confirms identity but is not usable as a real email for permission matching.
    """
    return bool(email) and NOREPLY_EMAIL_SUFFIX in email.lower()


# GitHub login charset (also valid for org logins). Logins are interpolated
# into GraphQL string literals, so anything outside this set is skipped rather
# than escaped.
_GITHUB_LOGIN_RE = re.compile(r"[A-Za-z0-9-]{1,39}")


class UsersSync:
    """Handles all GitHub org member synchronisation steps for ``GitHubTeamsConnector``."""

    def __init__(self, connector: "GitHubTeamsConnector") -> None:
        self.c = connector
        self.logger = connector.logger
        # Emails of principals that came from /orgs/{org}/members, i.e. enterprise
        # members. Internal repos are readable by exactly this set and explicitly
        # NOT by outside collaborators, so the repo pass needs the two kept apart.
        self._org_member_emails: set[str] = set()
        # Principals user sync enumerated this run, plus repo collaborators the
        # individual-repo resolution path already attempted — both guard against
        # paying a profile fetch per repo for the same unresolvable identity.
        self._principal_ids: set[int] = set()
        self._collab_resolution_attempted: set[int] = set()

    def org_member_emails(self) -> set[str]:
        """Resolved emails of org members (never outside collaborators).

        Empty when user sync has not run or resolved nobody, which makes the
        internal-repo grant fall back to explicit collaborators only.
        """
        return self._org_member_emails

    # ------------------------------------------------------------------
    # Entry point
    # ------------------------------------------------------------------

    async def sync_users(self) -> None:
        """Discover every principal on the target orgs and bind those we can
        identify to ``AppUser`` rows. See the module docstring for the phases."""
        c = self.c
        if not c.data_source:
            raise Exception("GitHub data source not initialized")

        orgs, orgs_ok = await self._resolve_target_orgs()
        if not orgs_ok:
            raise RuntimeError(
                "GitHub Teams user sync aborted: org discovery failed. Continuing "
                "would treat the empty result as 'no orgs' and drop every AppUser."
            )
        if not orgs:
            self.logger.warning("No GitHub organizations resolved; skipping user sync.")
            return
        self.logger.info("User sync: %s org(s)", len(orgs))

        dict_member, any_success = await self._discover_principals(orgs)

        if not any_success:
            raise RuntimeError(
                "GitHub Teams user sync aborted: every configured org failed to "
                "enumerate members, so an empty result cannot be trusted."
            )
        if not dict_member:
            self.logger.warning("No GitHub org members found")
            return

        resolved_email: dict[int, str] = {}
        unresolved_ids: set[int] = set(dict_member)

        # ---- Phase A: cached AppUsers ----
        # First because it is the only free phase: the listings return partial
        # objects carrying no email, so every principal would otherwise cost a
        # GET /users/:login per sync to re-learn what this one query knows.
        newly = await self._resolve_cached_users(unresolved_ids)
        resolved_email.update(newly)
        unresolved_ids -= set(newly.keys())

        # ---- Phase B: org-verified-domain emails (GraphQL, batched) ----
        # Before the per-profile fan-out because it is cheaper (~1 query per
        # 100 members vs 1 call per member) and better: it returns the member's
        # corporate address — the one the PipesHub directory knows — where a
        # public profile email is often a personal one that matches nobody.
        if unresolved_ids:
            phase_b = await self._resolve_via_verified_domains(
                dict_member, unresolved_ids, orgs
            )
            resolved_email.update(phase_b)
            unresolved_ids -= set(phase_b.keys())

        # ---- Phase C: public profile emails ----
        # Read what the listing already carries before paying for a fetch. Org
        # listings return partial objects so this usually resolves nobody, but
        # it is free and any payload that does carry an email skips a round trip.
        phase_c: dict[int, str] = {}
        for uid in list(unresolved_ids):
            email = self._plain_email_attr(dict_member[uid])
            if email:
                phase_c[uid] = email
                resolved_email[uid] = email
                unresolved_ids.discard(uid)

        if unresolved_ids:
            enriched = await self._enrich_members_with_full_profile(dict_member, unresolved_ids)
            for uid, full_member in enriched.items():
                dict_member[uid] = full_member
                email = self._plain_email_attr(full_member)
                if email:
                    phase_c[uid] = email
                    resolved_email[uid] = email
                    unresolved_ids.discard(uid)

        await self._persist_app_users(dict_member, resolved_email)
        member_ids = getattr(self, "_member_ids", set())
        self._org_member_emails = {
            email for uid, email in resolved_email.items() if uid in member_ids
        }

        self.logger.info(
            "Total users synced: %s, Total users skipped: %s",
            len(resolved_email), len(unresolved_ids),
        )

    async def _discover_principals(self, orgs: list[str]) -> tuple[dict[int, Any], bool]:
        """Every GitHub account that can hold a permission on these orgs' repos.

        Outside collaborators are a separate endpoint: GitHub omits them from
        ``/orgs/{org}/members``, but they appear in each repo's collaborator
        listing and so receive permission edges. Enumerating them here — rather
        than while walking repos — keeps user sync ahead of repo sync.

        Which endpoint a principal came from is recorded in ``self._member_ids``:
        internal repos are readable by org members and explicitly not by outside
        collaborators, so the two cannot be conflated.

        Returns ``(principals_by_id, any_success)``. ``any_success`` is False
        only when *no* org yielded a member listing, which callers must not
        confuse with a genuinely empty org.
        """
        c = self.c
        principals: dict[int, Any] = {}
        self._member_ids: set[int] = set()
        self._collab_resolution_attempted = set()
        any_success = False

        def collect(rows: Any, *, is_member: bool) -> int:
            kept = 0
            for row in rows or []:
                rid = getattr(row, "id", None)
                # Bots hold no PipesHub identity; enumerating them costs a
                # profile fetch to learn nothing.
                if rid is not None and getattr(row, "type", "User") == "User":
                    principals[rid] = row
                    if is_member:
                        self._member_ids.add(rid)
                    kept += 1
            return kept

        for org in orgs:
            res = await c.runtime.ds_call(c.data_source.list_org_members, org)
            if not res.success:
                # A 404 is an answer, not an outage: the login is a user account,
                # not an organization (personal repos reach here through the
                # REPO_IDS filter). Counting it as success keeps a definitive
                # "no org members" from aborting the whole sync the way an
                # unreachable org must.
                if getattr(res, "status_code", None) == 404:
                    any_success = True
                    self.logger.info(
                        "%s is not an organization (404 on member listing); it has no org "
                        "members. Repos owned by it rely on visibility and explicit "
                        "collaborator grants.", org,
                    )
                    continue
                self.logger.error("Failed to list members for org %s: %s", org, res.error)
                continue
            any_success = True
            member_count = collect(res.data, is_member=True)

            outside_res = await c.runtime.ds_call(
                c.data_source.list_org_outside_collaborators, org
            )
            if not outside_res.success:
                self.logger.warning(
                    "Could not list outside collaborators for org %s: %s. Any that hold repo "
                    "access will be unresolvable this sync.", org, outside_res.error,
                )
                continue
            self.logger.info(
                "Org %s: %s member(s), %s outside collaborator(s)",
                org, member_count, collect(outside_res.data, is_member=False),
            )

        self._principal_ids = set(principals)
        return principals, any_success

    async def resolve_collaborator_principals(self, collaborators: dict[int, Any]) -> set[int]:
        """Bind an individual-owned repo's collaborators to ``AppUser`` rows.

        A repo owned by a *user account* has no org to enumerate —
        ``/orgs/{login}/members`` 404s — so its collaborators appear in no
        principal listing, and the grant step would find no identity to attach
        (private individual repos ended up visible to nobody). The repo's own
        collaborator rows are the only identity source; they are resolved here
        with the public-profile phase (verified domains are org-scoped, and
        Phase A's role is played by the grant step's own AppUser lookup).

        Additive to the org flow, which never calls this: org-repo
        collaborators are always enumerated up front. Ids user sync already
        saw, and ids already attempted this run, are skipped so an org member
        collaborating on an individual repo can't cost one profile fetch per
        repo per sync. Returns the ids that resolved.
        """
        fresh = {
            uid: row for uid, row in collaborators.items()
            if uid not in self._principal_ids
            and uid not in self._collab_resolution_attempted
            and getattr(row, "type", "User") == "User"
        }
        if not fresh:
            return set()
        self._collab_resolution_attempted.update(fresh)

        resolved: dict[int, str] = {}
        unresolved = set(fresh)
        for uid in list(unresolved):
            email = self._plain_email_attr(fresh[uid])
            if email:
                resolved[uid] = email
                unresolved.discard(uid)
        if unresolved:
            enriched = await self._enrich_members_with_full_profile(fresh, unresolved)
            for uid, full in enriched.items():
                fresh[uid] = full
                email = self._plain_email_attr(full)
                if email:
                    resolved[uid] = email
                    unresolved.discard(uid)

        if resolved:
            await self._persist_app_users(fresh, resolved)
        if unresolved:
            self.logger.info(
                "%s collaborator(s) on an individual-owned repo expose no public "
                "email; they receive no access until their identity resolves.",
                len(unresolved),
            )
        return set(resolved)

    # ------------------------------------------------------------------
    # Org scope resolution
    # ------------------------------------------------------------------

    async def _resolve_target_orgs(self) -> tuple[list[str], bool]:
        """Resolve which org logins to sync users for.

        ``ORG_IDS IN`` is authoritative. Without it, ``REPO_IDS IN`` narrows
        scope to the distinct owning orgs of the configured repos. With
        neither filter (or a ``NOT_IN`` variant), discover every org the
        token can see via ``list_user_orgs`` (requires ``read:org``).

        Returns ``(orgs, ok)``. ``ok`` is False when discovery itself failed,
        which callers must not confuse with "this token sees no orgs" — the
        latter is a legitimate empty result, the former is transient and would
        otherwise read as "every org disappeared".
        """
        c = self.c
        sf = c.sync_filters
        org_f = sf.get(SyncFilterKey.ORG_IDS) if sf else None
        repo_f = sf.get(SyncFilterKey.REPO_IDS) if sf else None
        org_active = org_f is not None and not org_f.is_empty()
        repo_active = repo_f is not None and not repo_f.is_empty()

        if org_active:
            op = org_f.operator_value
            if op == FilterOperator.IN:
                return list(org_f.value), True  # type: ignore[arg-type]
            excluded = set(org_f.value)  # type: ignore[arg-type]
            return await self._list_all_visible_orgs(excluded)

        if repo_active:
            op = repo_f.operator_value
            if op == FilterOperator.IN:
                orgs = sorted({
                    full_name.split("/")[0]
                    for full_name in repo_f.value  # type: ignore[union-attr]
                    if "/" in full_name
                })
                if orgs:
                    return orgs, True

        return await self._list_all_visible_orgs(excluded=None)

    async def _list_all_visible_orgs(self, excluded: set[str] | None) -> tuple[list[str], bool]:
        c = self.c
        res = await c.runtime.ds_call(c.data_source.list_user_orgs)
        if not res.success:
            self.logger.error("Failed to list GitHub orgs visible to this token: %s", res.error)
            return [], False
        logins = [getattr(o, "login", None) for o in (res.data or [])]
        logins = [login for login in logins if login]
        if excluded:
            logins = [login for login in logins if login not in excluded]
        return logins, True

    # ------------------------------------------------------------------
    # Phase B: org-verified-domain emails (GraphQL, batched)
    # ------------------------------------------------------------------

    async def _resolve_via_verified_domains(
        self, dict_member: dict[int, Any], unresolved_ids: set[int], orgs: list[str]
    ) -> dict[int, str]:
        """Resolve org members' corporate emails via ``organizationVerifiedDomainEmails``.

        One aliased ``user(login:)`` field per member, ``VERIFIED_DOMAIN_EMAIL_BATCH``
        per query, spending GraphQL's separate point budget (~1 point each).
        Only org members are queried — the field is defined per-membership, so
        outside collaborators can never carry a verified-domain address.

        Best-effort by design: the field returns data only when the token
        belongs to an org owner AND the org has a verified domain. Any failure
        (missing scope, no verified domain, unknown login in the chunk) stops
        this org's sweep and later phases take over — never the sync.
        """
        c = self.c
        member_ids = getattr(self, "_member_ids", set())
        candidates: list[tuple[int, str]] = []
        for uid in sorted(unresolved_ids & member_ids):
            login = getattr(dict_member[uid], "login", None)
            if isinstance(login, str) and _GITHUB_LOGIN_RE.fullmatch(login):
                candidates.append((uid, login))
        if not candidates:
            return {}

        resolved: dict[int, str] = {}
        for org in orgs:
            if not _GITHUB_LOGIN_RE.fullmatch(org):
                continue
            remaining = [(uid, login) for uid, login in candidates if uid not in resolved]
            if not remaining:
                break
            for start in range(0, len(remaining), VERIFIED_DOMAIN_EMAIL_BATCH):
                chunk = remaining[start : start + VERIFIED_DOMAIN_EMAIL_BATCH]
                query = self._verified_domain_query(org, [login for _, login in chunk])
                res = await c.runtime.ds_call(c.data_source.graphql_query, query)
                if not res.success:
                    self.logger.info(
                        "Verified-domain email lookup unavailable for org %s: %s. "
                        "(Requires an org-owner token and a verified domain on the "
                        "org.) Profile emails will cover these members instead.",
                        org, str(getattr(res, "error", ""))[:200],
                    )
                    break
                data = res.data if isinstance(res.data, dict) else {}
                for idx, (uid, _login) in enumerate(chunk):
                    node = data.get(f"u{idx}")
                    emails = (
                        node.get("organizationVerifiedDomainEmails")
                        if isinstance(node, dict) else None
                    )
                    for email in emails or []:
                        if (
                            isinstance(email, str)
                            and email.strip()
                            and not _is_noreply_email(email)
                        ):
                            resolved[uid] = email.strip()
                            break
        return resolved

    @staticmethod
    def _verified_domain_query(org: str, logins: list[str]) -> str:
        """Aliased batch query: ``uN: user(login:...) { organizationVerifiedDomainEmails }``.

        Inputs are pre-validated against ``_GITHUB_LOGIN_RE``, so plain
        interpolation cannot break out of the string literals.
        """
        fields = "\n".join(
            f'  u{i}: user(login: "{login}") '
            f'{{ organizationVerifiedDomainEmails(login: "{org}") }}'
            for i, login in enumerate(logins)
        )
        return f"query {{\n{fields}\n}}"

    # ------------------------------------------------------------------
    # Phase C: visible emails
    # ------------------------------------------------------------------

    @staticmethod
    def _plain_email_attr(member: Any) -> str | None:
        """``member.email`` when the payload carries one, else None.

        The ``/orgs/{org}/members`` listing payload has no ``email`` field at
        all, so listing members resolve to None here and go through the
        explicit ``get_user`` enrichment instead.
        """
        email = getattr(member, "email", None)
        if isinstance(email, str) and email.strip() and not _is_noreply_email(email):
            return email.strip()
        return None

    async def _enrich_members_with_full_profile(
        self, dict_member: dict[int, Any], ids_to_enrich: set[int]
    ) -> dict[int, Any]:
        """Fetch ``GET /users/:login`` per unresolved member to recover the public email.

        Concurrency bounded by ``_GITHUB_USER_ENRICHMENT_CONCURRENCY``. Falls
        back to the original (partial) member payload on any per-user failure
        so one misbehaving lookup does not abort the sweep.
        """
        c = self.c
        sem = asyncio.Semaphore(_GITHUB_USER_ENRICHMENT_CONCURRENCY)

        async def fetch_full_user(uid: int) -> tuple[int, Any, bool]:
            member = dict_member[uid]
            login = getattr(member, "login", None)
            if not login:
                return uid, member, True
            async with sem:
                res = await c.runtime.ds_call(c.data_source.get_user, login)
                if res.success and res.data is not None:
                    return uid, res.data, True
                return uid, member, False

        enriched: dict[int, Any] = {}
        failed = 0
        for fut in asyncio.as_completed([fetch_full_user(uid) for uid in ids_to_enrich]):
            uid, obj, ok = await fut
            enriched[uid] = obj
            if not ok:
                failed += 1
        if failed:
            self.logger.info(
                "Could not fetch full GitHub profile for %s user(s); using partial payload",
                failed,
            )
        return enriched

    # ------------------------------------------------------------------
    # Phase A: cached AppUsers
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
    # AppUser persistence
    # ------------------------------------------------------------------

    async def _persist_app_users(
        self, dict_member: dict[int, Any], resolved_email: dict[int, str]
    ) -> None:
        """Upsert an ``AppUser`` for every principal with a resolved email.

        The email is not checked against the PipesHub directory. An address the
        platform has not seen yet is still that person's identity: the account
        is created here and the permission edge attaches to it, so access is
        already in place when they are provisioned or sign in with it. Gating on
        the directory instead would silently drop real collaborators — see
        ``gitlab/users.py`` for the same contract.
        """
        c = self.c
        if not resolved_email:
            return

        app_users: list[AppUser] = []
        for uid, email in resolved_email.items():
            member = dict_member.get(uid)
            # `login` is always on the list payload so reading it is free;
            # `name` is not, and on a partial NamedUser would lazily fetch.
            full_name = (
                (getattr(member, "name", None) or getattr(member, "login", None) or email)
                if member else email
            )
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

        if not app_users:
            return

        await c.data_entities_processor.on_new_app_users(app_users)
