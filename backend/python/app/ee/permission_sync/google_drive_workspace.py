"""Google Drive for Workspace: reading who may see what from Google Workspace.

Google groups with their members, the groups a discoverable ("anyone" or domain) share reaches, and the
grants below a folder that turned limited. The free connector keeps listing and syncing drives and files and
calls these methods; without a licence key it reads no Google groups (the processor's licence gate).
"""

import asyncio
import hashlib
import json
import secrets
from contextvars import ContextVar
from typing import TYPE_CHECKING, Dict, List, Optional

from googleapiclient.errors import HttpError

from app.config.constants.arangodb import (
    FOLDER_MIME_TYPES,
    CollectionNames,
    MimeTypes,
)
from app.config.constants.http_status_code import HttpStatusCode
from app.connectors.core.base.data_processor.data_source_entities_processor import (
    PERMISSION_HIERARCHY,
)
from app.connectors.sources.google.common.impersonation import (
    MAX_IMPERSONATION_CANDIDATES,
)
from app.connectors.sources.google.drive.team.drive_access import (
    DrivePermissionBatch,
    item_inherits_parent,
)
from app.connectors.sources.google.drive.utils import drive_full_sync_reconciler
from app.connectors.sources.google.drive.utils.checkpoint_hold import MAX_HELD_RUNS
from app.connectors.sources.google.drive.utils.folder_filter_utils import (
    drive_error_reasons,
    is_permission_denied_403,
)
from app.models.entities import (
    AppUser,
    AppUserGroup,
    FileRecord,
    Record,
)
from app.models.permission import EntityType, Permission, PermissionType
from app.services.graph_db.common.record_visibility import RecordVisibility
from app.sources.external.google.drive.drive import GoogleDriveDataSource
from app.utils.time_conversion import get_epoch_timestamp_in_ms, parse_timestamp

if TYPE_CHECKING:
    pass


# Groups holding the enabled people a discoverable share is open to: everyone ("anyone" shares), and one per
# verified domain (domain shares). Ids have no "@", so they are never mistaken for a Workspace group.
ALL_MEMBERS_GROUP_ID = "workspace-members"

DOMAIN_MEMBERS_GROUP_PREFIX = "domain-members:"

# Google's answers, per verified domain D and each other verified domain E, to whether a share with D reaches the
# people of E, and how long one is trusted before it is asked again.
DOMAIN_REACH_SYNC_POINT_PREFIX = "domain_share_reach_"

DOMAIN_REACH_MAX_AGE_MS = 7 * 24 * 60 * 60 * 1000

# A check that learns nothing keeps the last answer; a yes not answered again for this long reaches D's own people only.
DOMAIN_REACH_KEEP_MAX_MS = 30 * 24 * 60 * 60 * 1000

# Files kept per domain to ask that with, and at most this many with the same owners.
REACH_CANDIDATES = 6

REACH_CANDIDATES_PER_OWNER = 2

# The membership digests of the audience groups as last written, so an unchanged group is not written again.
AUDIENCE_DIGESTS_SYNC_POINT_KEY = "audience_group_digests"

# All of them are written again once the last full write is this old: an edge removed in the store, not at the source
# (a person removed and added back), comes back.
AUDIENCE_REWRITE_MAX_AGE_MS = 24 * 60 * 60 * 1000

# On the connector's checkpoint: the shared drives every member has listed again once, so no grant an earlier
# version gave a reader of an item to stand in for their membership is left on the items they can see.
STAND_IN_CLEANUP_SYNC_POINT_KEY = "stand_in_grants_cleared"

STAND_IN_CLEANED_DRIVES = "drives"

# Descendants checked per run; the rest wait for the next run.
LIMITED_SUBTREE_MAX_CHECKS = 2000


DRIVE_RESTRICTIONS_SYNC_POINT_PREFIX = "shared_drive_restrictions_"

# Stored beside the member groups' digests: the drive's direct members, whom a members-only drive's grants depend on.
DRIVE_DIRECT_MEMBERS_MARK = "drive-members"

# People of an audience asked whether they can still open a file. Everyone in an audience has the same access, so
# a clean "no" from each of them shows the share is gone; more than one allows for a person who cannot use Drive.
AUDIENCE_PROBES = 3

# Set when a drive's items were synced under assumed restrictions: the next answer that has them lists it in full.
DRIVE_RESTRICTIONS_ASSUMED = "assumed"

# On the connector's checkpoint: stored My Drive items whose permission list came back empty, with the runs in a
# row it did, as JSON text of {file id: runs}. After MAX_HELD_RUNS their grants are cut down to what is proven.
EMPTY_LIST_RUNS_SYNC_POINT_KEY = "empty_permission_list_runs"

# Per user: items below a folder they lost whose check of their own access failed, asked again on later runs. Not an
# undecided removal: nothing reported these items removed, so they are never settled as one.
# Items whose sharing nobody synced can read again and whose holders' check failed, asked again on later runs.
HOLDER_RECHECK_SYNC_POINT_KEY = "drive_holder_recheck"

# The ids of the items that failed in the listing this task is reading (each user's sync runs in its own task).
_failed_items: ContextVar[set[str] | None] = ContextVar("drive_failed_items", default=None)

FILTER_CLEANUP_PAGE_SIZE = 500


def _failure_summary(error: Exception) -> str:
    """What a failed source call said, safe to log: the HTTP status and Drive's reasons, never the request."""
    if isinstance(error, HttpError):
        reasons = ", ".join(sorted(drive_error_reasons(error))) or "no reason given"
        return f"HTTP {error.resp.status}: {reasons}"
    return type(error).__name__


def _is_audience_group(group_id: str | None) -> bool:
    return bool(group_id) and (group_id == ALL_MEMBERS_GROUP_ID or group_id.startswith(DOMAIN_MEMBERS_GROUP_PREFIX))


def _role_rank(permission: Permission | None, stored_role: str | None = None) -> int:
    """How much a grant (or, without one, a stored role) allows, to keep a person's highest."""
    return PERMISSION_HIERARCHY.get(permission.type.value if permission else stored_role or "", 0)


class _EmptyPermissionList(RuntimeError):
    """A permission list with no entries where the item must have some: not an answer for it."""


class _RemovalUndecided(RuntimeError):
    """Whether anyone but the user who lost a file can still open it is not known yet."""


def _email_domain(email: str | None) -> str:
    return (email or "").lower().rpartition("@")[2]


def _sole_domain_share(permissions: list[dict]) -> tuple[str, list[str]] | None:
    """(domain, owner emails) when a file's only permissions are its owners and one discoverable domain share: then
    a person of another domain can open it only if that share reaches them. None otherwise."""
    live = [p for p in permissions if not p.get("deleted")]
    owners = [p for p in live if p.get("role") == "owner"]
    shares = [p for p in live if p.get("role") != "owner"]
    if not owners or any(p.get("type") != "user" or not p.get("emailAddress") for p in owners) or len(shares) != 1:
        return None
    share = shares[0]
    if share.get("type") != "domain" or share.get("allowFileDiscovery") is not True or not share.get("domain"):
        return None
    return share["domain"].lower(), [p["emailAddress"].lower() for p in owners]


def _add_reach_candidate(candidates: list[dict], candidate: dict) -> None:
    if (
        len(candidates) < REACH_CANDIDATES
        and all(c["file_id"] != candidate["file_id"] for c in candidates)
        and sum(c["owners"] == candidate["owners"] for c in candidates) < REACH_CANDIDATES_PER_OWNER
    ):
        candidates.append(candidate)


class GoogleDriveWorkspacePermissionsMixin:
    """The permission-reading half of ``GoogleDriveTeamConnector``. The list read itself (``_fetch_permissions``) stays in
    the connector: it also reads each shared drive's member list, which the free connector needs to decide what to keep."""

    async def _copies_no_source_access(self) -> bool:
        """Without a licence key a company-wide connector reads no access from Google Workspace. The processor then
        keeps every stored grant, inherit edge and group whatever it is handed (``skips_permission_writes``), so the
        answers here only keep the run whole: an item gets no grants, as one whose list came back empty, instead of a
        failure that would hold every feed; a drive's restrictions are not compared, so it is not listed again for
        them; and no grant is checked or dropped, so nothing is parked to be asked again."""
        return await self.data_entities_processor.skips_permission_writes(self.connector_id)

    async def _write_audience_groups(self) -> None:
        """The groups a discoverable share grants: every enabled person ("anyone" shares), and one group per domain
        (domain shares) holding the enabled people whose address is on that domain, and those of each other domain
        that Google has shown a share with it reaches (`_domain_reach`).

        Groups, not an organization grant, because an organization grant also reaches guests and accounts
        that cannot sign in. A domain whose people are all gone is written empty, so no one keeps its access, and
        a change in Google's answer changes who is in the groups, not the grants on each file. A group whose people
        are the same as when it was last written is not written again, until AUDIENCE_REWRITE_MAX_AGE_MS has passed
        since all of them were.
        """
        enabled = [u for u in self.synced_users if u.is_active and not self._is_inactive_email(u.email)]
        by_domain: dict[str, list[AppUser]] = {}
        for user in enabled:
            by_domain.setdefault(_email_domain(user.email), []).append(user)
        domains_read = True
        try:
            domains = await self._workspace_domains()
        except Exception as e:
            self.logger.warning(f"Workspace domains could not be read; the domain groups only narrow this run: {e}")
            domains, domains_read = set(), False
        try:
            reach = await self._domain_reach(domains, enabled)
        except Exception as e:
            self.logger.warning(f"Could not settle how far domain shares reach; each reaches its own domain only: {e}")
            reach = {}
        audiences = {ALL_MEMBERS_GROUP_ID: ("Workspace members", enabled)}
        audiences.update({
            DOMAIN_MEMBERS_GROUP_PREFIX + d: (
                f"Members of {d}", [u for e in sorted({d, *reach.get(d, ())}) for u in by_domain.get(e, [])]
            )
            for d in set(by_domain) | domains
        })
        # The domains whose people each group may hold (None: everyone).
        allowed: dict[str, set[str] | None] = {ALL_MEMBERS_GROUP_ID: None}
        allowed.update({DOMAIN_MEMBERS_GROUP_PREFIX + d: {d, *reach.get(d, ())} for d in set(by_domain) | domains})
        try:
            stored_groups = {}
            all_stored = await self.data_entities_processor.get_all_user_groups(self.connector_id, raise_on_error=True)
            for stored in all_stored:
                gone = stored.source_user_group_id or ""
                stored_groups[gone] = stored
                if gone.startswith(DOMAIN_MEMBERS_GROUP_PREFIX) and gone not in audiences:
                    audiences[gone] = (stored.name, [])
                    allowed[gone] = set()  # no longer a verified domain: nobody
            if not domains_read:
                # Unknown is not empty: who a domain's group reaches cannot be worked out without the domains, so
                # each keeps its stored people, less those who cannot sign in, until they are read.
                allowed.update({g: None for g in audiences if g.startswith(DOMAIN_MEMBERS_GROUP_PREFIX)})
                self._sync_ledger.note_incomplete()
            if self._listing_cut or not domains_read:
                await self._write_audience_groups_on_cut_listing(audiences, allowed, stored_groups)
                self._audience_groups = set(audiences)
                self._audience_loaded = True
                return
            digests: dict[str, str] = {}
            for group_id, (_, members) in audiences.items():
                people = {f"{(u.email or '').lower()}|{u.source_user_id}" for u in members}
                digests[group_id] = f"{group_id}|{self._people_digest(people)}"
            try:
                saved = await self.drive_delta_sync_point.read_sync_point(AUDIENCE_DIGESTS_SYNC_POINT_KEY) or {}
            except Exception as e:
                self.logger.warning(f"Could not read which discoverable-share groups are up to date; writing them all: {e}")
                saved = {}
            now = get_epoch_timestamp_in_ms()
            full = now - int(saved.get("written_at") or 0) >= AUDIENCE_REWRITE_MAX_AGE_MS
            written = set() if full else set(saved.get("digests") or [])
            changed = sorted(g for g, digest in digests.items() if digest not in written or g not in stored_groups)
            # Not written, so a full sync in progress must be told they are kept, or the unlink after it removes
            # their members and every grant made to them.
            unchanged = set(digests) - set(changed)
            self.data_entities_processor.note_kept_user_groups(
                self.connector_id, [g for g in all_stored if g.source_user_group_id in unchanged]
            )
            if changed:
                # Forgotten first: a write that fails part way may have stored some of them, and none may then be
                # skipped as unchanged while the store holds other people than they should.
                await self.drive_delta_sync_point.update_sync_point(AUDIENCE_DIGESTS_SYNC_POINT_KEY, {
                    "digests": sorted(written - {e for e in written if e.rpartition("|")[0] in changed}),
                })
                await self.data_entities_processor.on_new_user_groups([
                    (
                        AppUserGroup(
                            source_user_group_id=group_id,
                            app_name=self.connector_name,
                            connector_id=self.connector_id,
                            name=audiences[group_id][0],
                            description="Enabled people a discoverable share is open to",
                        ),
                        audiences[group_id][1],
                    )
                    for group_id in changed
                ])
                await self.drive_delta_sync_point.update_sync_point(AUDIENCE_DIGESTS_SYNC_POINT_KEY, {
                    "digests": sorted(digests.values()),
                    "written_at": now if full else int(saved.get("written_at") or 0),
                })
        except Exception as e:
            # Like a group that failed in the listing: the stored groups stay as they were, grants are still
            # written to them, and the run is reported incomplete so the write is tried again.
            self._sync_ledger.note_incomplete()
            self.logger.error(f"Error writing the discoverable-share groups; the stored ones are kept: {e}", exc_info=True)
            return
        self._audience_groups = set(audiences)
        self._audience_members = {g: {(u.email or "").lower() for u in members} for g, (_, members) in audiences.items()}
        self._audience_loaded = True

    async def _write_audience_groups_on_cut_listing(
        self, audiences: dict[str, tuple[str, list[AppUser]]], allowed: dict[str, set[str] | None], stored_groups: dict
    ) -> None:
        """A cut user listing, or one read without the verified domains, may only narrow: a person it leaves out (or
        a domain group's person whose reach cannot be worked out) is unknown and keeps their place. Each group
        keeps its stored people, less those the listing shows unable to sign in and those on a domain the group may no
        longer hold (a narrower answer from Google applies at once), plus the listed people it should hold. Each group is
        then written in full again on the next complete listing."""
        # Forgotten first, so that write is not skipped as unchanged.
        await self.drive_delta_sync_point.update_sync_point(AUDIENCE_DIGESTS_SYNC_POINT_KEY, {"digests": [], "written_at": 0})
        self._audience_members = {}
        for group_id, (name, listed) in audiences.items():
            domains = allowed.get(group_id)
            held: set[str] = set()
            if group_id in stored_groups:
                for user in await self.data_entities_processor.get_users_with_permission_to_node(
                    stored_groups[group_id].id, CollectionNames.GROUPS.value, raise_on_error=True
                ):
                    email = (user.email or "").lower()
                    if not email:
                        continue
                    if self._is_inactive_email(email) or (domains is not None and _email_domain(email) not in domains):
                        await self.data_entities_processor.on_user_group_member_removed(group_id, email, self.connector_id)
                    else:
                        held.add(email)
            await self.data_entities_processor.on_new_user_groups([(
                AppUserGroup(
                    source_user_group_id=group_id,
                    app_name=self.connector_name,
                    connector_id=self.connector_id,
                    name=name,
                    description="Enabled people a discoverable share is open to",
                ),
                listed,
            )], replace_members=False)
            self._audience_members[group_id] = held | {(u.email or "").lower() for u in listed}

    async def _domain_reach(self, domains: set[str], enabled: list[AppUser]) -> dict[str, set[str]]:
        """For each verified domain D, the other verified domains whose people Google has shown a share with D reaches.

        Google documents only that a domain permission refers to its domain, not whether it reaches the account's
        other domains, so each pair of domains is asked on its own (`_reach_pair`): one answer says nothing of
        another. A yes counts only once a later run, asking other people, says yes too; a no counts at once. An answer
        is kept with the domains it was asked for, until it is DOMAIN_REACH_MAX_AGE_MS old or the domains change; a
        pair without one is asked again on the next run that has a file to ask with. Until then the share reaches D's
        own people only. A check that learns nothing (an error, or no file or person to ask) leaves the stored answer
        as it is and asks again next run, but a yes not answered again within DOMAIN_REACH_KEEP_MAX_MS is dropped.
        """
        if len(domains) < 2:
            return {}
        now = get_epoch_timestamp_in_ms()
        # Stamped with the run's start, so "from an earlier run" is exact: two runs never start in the same millisecond,
        # but one may start in the millisecond the last one wrote its answers.
        stamp = self._run_started_at or now
        reach: dict[str, set[str]] = {}
        for domain in sorted(domains):
            key = f"{DOMAIN_REACH_SYNC_POINT_PREFIX}{domain}"
            stored = await self.drive_delta_sync_point.read_sync_point(key) or {}
            # JSON text: the graph stores refuse nested maps.
            state = json.loads(stored.get("state") or "{}") if stored.get("domains") == sorted(domains) else {}
            answers: dict[str, list] = state.get("answers") or {}
            found = self._reach_candidates.get(domain) or []
            candidates: list[dict] = list(state.get("candidates") or [])
            for candidate in found:
                _add_reach_candidate(candidates, candidate)
            ordered: list[dict] | None = None
            for other in sorted(domains - {domain}):
                # [answer, when asked, the people who last said yes, when they did]
                covered, checked_at, yes_by, yes_at = answers.get(other) or [None, 0, [], 0]
                if now - int(checked_at) >= DOMAIN_REACH_MAX_AGE_MS or (
                    covered is None and (int(checked_at) < self._run_started_at or found)
                ):
                    if ordered is None:
                        ordered, candidates = await self._reach_candidates_in_order(domain, candidates, enabled)
                    result, people = await self._reach_pair(domain, other, ordered, enabled, set(yes_by))
                    if result:
                        # Someone who can briefly share each file with the person asked could make one run say yes.
                        confirmed = bool(yes_at) and int(yes_at) < self._run_started_at and not people & set(yes_by)
                        covered = True if confirmed else None
                        answers[other] = [covered, stamp, sorted(people), stamp]
                    elif result is False:
                        covered = False
                        answers[other] = [False, stamp, [], 0]
                    elif covered is None:
                        answers[other] = [None, stamp, yes_by, yes_at]
                    elif covered and now - int(checked_at) >= DOMAIN_REACH_KEEP_MAX_MS:
                        covered = None
                        answers[other] = [None, stamp, [], 0]
                        self.logger.warning(
                            f"Could not check again for {DOMAIN_REACH_KEEP_MAX_MS // 86_400_000} days whether a share "
                            f"with {domain} reaches the people of {other}; it reaches {domain} only until it is"
                        )
                    # Otherwise nothing was learned: the stored answer stands and is asked again next run.
                    if result is not None:
                        self.logger.info(
                            f"A share with {domain} {'does not reach' if result is False else 'reaches'} the people of "
                            f"{other}{'' if result is False or covered else '; asked again next run, of other people'}"
                        )
                if covered:
                    reach.setdefault(domain, set()).add(other)
            if ordered is not None or candidates != list(state.get("candidates") or []):
                await self.drive_delta_sync_point.update_sync_point(key, {
                    "domains": sorted(domains), "state": json.dumps({"answers": answers, "candidates": candidates}),
                })
        return reach

    async def _owner_view(self, source: GoogleDriveDataSource, file_id: str) -> tuple[str, tuple | None, list[str]] | None:
        """A My Drive file as its owner reads it now: a digest of everything the check depends on (its folder and
        every permission), its sole domain share as (domain, owners), and its folders. None in a shared drive or with
        more permissions than one page holds."""
        meta = await source.files_get(fileId=file_id, fields="id, driveId, parents, trashed", supportsAllDrives=True)
        listed = await source.permissions_list(
            fileId=file_id, supportsAllDrives=True, pageSize=100,
            fields="nextPageToken, permissions(id, type, role, domain, emailAddress, deleted, allowFileDiscovery)",
        )
        if meta.get("driveId") or meta.get("trashed") or listed.get("nextPageToken"):
            return None
        permissions = listed.get("permissions") or []
        sole = _sole_domain_share(permissions)
        parents = sorted(meta.get("parents") or [])
        state = json.dumps([parents, sorted(json.dumps(p, sort_keys=True) for p in permissions)])
        return hashlib.sha256(state.encode()).hexdigest()[:16], (sole[0], sorted(sole[1])) if sole else None, parents

    async def _reach_candidates_in_order(
        self, domain: str, candidates: list[dict], enabled: list[AppUser]
    ) -> tuple[list[dict], list[dict]]:
        """The candidates to ask with: still shared with the domain alone, outside a shared drive, and looking as
        they did on an earlier run, those at the top of the owner's My Drive first. And the candidates to keep, with
        how each looks now; only those found no longer fit to ask with (deleted, in the trash, shared otherwise) are
        left out, and one that could not be read for another reason is kept."""
        ranked: list[tuple[bool, dict]] = []
        keep: list[dict] = []
        for candidate in candidates:
            owner = next((u for u in enabled if (u.email or "").lower() in candidate["owners"]), None)
            if owner is None:
                continue
            try:
                source = await self._build_user_drive_data_source(owner)
                view = await self._owner_view(source, candidate["file_id"])
                if view is None or view[1] != (domain, candidate["owners"]):
                    continue
                root = (await source.files_get(fileId="root", fields="id")).get("id")
            except Exception as e:
                if isinstance(e, HttpError) and e.resp.status == HttpStatusCode.NOT_FOUND.value:
                    continue  # deleted, or no longer the owner's: it makes room for another file
                self.logger.warning(f"Could not read file {candidate['file_id']} to check how far a share with {domain} reaches: {e}")
                keep.append(candidate)
                continue
            if candidate.get("view") != view[0]:
                # New, or changed since: asked with only once a later run finds it the same.
                keep.append({**candidate, "view": view[0], "seen_at": self._run_started_at or get_epoch_timestamp_in_ms()})
                continue
            keep.append(candidate)
            if int(candidate.get("seen_at") or 0) < self._run_started_at:
                ranked.append((root not in view[2], candidate))
        return [c for _, c in sorted(ranked, key=lambda r: r[0])], keep

    async def _reach_pair(
        self, domain: str, other: str, ordered: list[dict], enabled: list[AppUser], exclude: set[str]
    ) -> tuple[bool | None, set[str]]:
        """Whether a share with `domain` reaches the people of `other`, asking no one in `exclude`: no as soon as a
        file says so, yes only once files of two different owners say so, as one owner can open a single file to the
        person being asked. None (unknown) otherwise. With the people who said yes."""
        covered_by: list[set[str]] = []
        people: set[str] = set()
        for candidate in ordered:
            answer, person = await self._probe_domain_reach(candidate, domain, other, enabled, exclude)
            if answer is False:
                return False, set()
            if answer and all(owners.isdisjoint(candidate["owners"]) for owners in covered_by):
                covered_by.append(set(candidate["owners"]))
                people.add(person)
                if len(covered_by) == 2:
                    return True, people
        return None, set()

    async def _probe_domain_reach(
        self, candidate: dict, domain: str, other: str, enabled: list[AppUser], exclude: set[str]
    ) -> tuple[bool | None, str]:
        """Open a My Drive file whose only permissions are its owners and one discoverable share with `domain`, as an
        enabled person of `other` with no address on `domain` and not in `exclude`, picked at random each time: found
        means the share reaches them, "not found" that it does not. The owner reads the file before and after, and it
        must look as it did on the earlier run and the same both times, or a share or move made in between could
        answer. None (unknown) for anything else: no such file or person any more, a change, or another error. With
        the person asked."""
        file_id, owners = candidate["file_id"], candidate["owners"]
        owner = next((u for u in enabled if (u.email or "").lower() in owners), None)
        people = [
            u for u in enabled
            if _email_domain(u.email) == other and (u.email or "").lower() not in owners
            and (u.email or "").lower() not in exclude
            and domain not in self._alias_domains.get((u.email or "").lower(), set())
        ]
        if owner is None or not people:
            return None, ""
        person = secrets.choice(people)
        email = (person.email or "").lower()
        try:
            owner_source = await self._build_user_drive_data_source(owner)
            before = await self._owner_view(owner_source, file_id)
            if before is None or before[1] != (domain, owners) or before[0] != candidate.get("view"):
                return None, email
            source = await self._build_user_drive_data_source(person)
            try:
                await source.files_get(fileId=file_id, fields="id", supportsAllDrives=True)
                answer = True
            except HttpError as e:
                if e.resp.status != HttpStatusCode.NOT_FOUND.value:
                    raise
                answer = False
            if await self._owner_view(owner_source, file_id) != before:
                self.logger.info(f"File {file_id} changed while checking how far a share with {domain} reaches; asked again later")
                return None, email
        except Exception as e:
            self.logger.warning(f"Could not check how far a share with {domain} reaches with file {file_id}: {e}")
            return None, email
        return answer, email

    async def _sync_user_groups(self) -> None:
        """Sync user groups and their members from Google Workspace Admin API."""
        if await self._copies_no_source_access():
            return
        try:
            if not self.admin_data_source:
                self.logger.error("Admin data source not initialized. Call init() first.")
                raise ValueError("Admin data source not initialized")

            self.logger.info("Fetching all groups from Google Workspace Admin API...")
            page_token: Optional[str] = None
            total_groups_processed = 0
            listed_group_ids: set[str] = set()

            while True:
                try:
                    # Fetch groups with pagination
                    result = await self.admin_data_source.groups_list(
                        customer="my_customer",
                        orderBy="email",
                        pageToken=page_token,
                        maxResults=200  # Maximum allowed by Google Admin API
                    )

                    groups_data = result.get("groups", [])

                    # Process each group
                    for group in groups_data:
                        if group.get("email"):
                            listed_group_ids.add(group["email"].lower())
                        try:
                            await self._process_group(group)
                            total_groups_processed += 1
                        except Exception as e:
                            # The group keeps its stored members, and the run is not a complete read.
                            self._sync_ledger.note_incomplete()
                            self.logger.error(
                                f"Error processing group {group.get('id', 'unknown')}: {e}",
                                exc_info=True
                            )
                            continue

                    # Check for next page
                    page_token = result.get("nextPageToken")
                    if not page_token:
                        break

                    self.logger.info(f"Processed {len(groups_data)} groups (total so far: {total_groups_processed})")

                except Exception as e:
                    self.logger.error(f"Error fetching groups page: {e}", exc_info=True)
                    raise

            self.logger.info(f"✅ Successfully synced {total_groups_processed} user groups")
            await self._remove_deleted_groups(listed_group_ids)

        except Exception as e:
            self.logger.error(f"❌ Error syncing user groups: {e}", exc_info=True)
            raise

    async def _remove_deleted_groups(self, listed_group_ids: set[str]) -> None:
        """Delete the stored groups a complete listing no longer has, once the directory confirms each is gone.

        A deleted Workspace group keeps its members in PipesHub otherwise, and with them every grant made to
        the group. The directory documents no error answers for members.list
        (https://developers.google.com/workspace/admin/directory/reference/rest/v1/members/list), so its "not
        found" is confirmed with groups.get for that exact group. A group members.list answers for is written
        with the membership it lists now (none when the answer is empty), read the way a listed group's is; a
        group whose members cannot all be read, or whose answer is neither, is kept as stored for the next run.
        """
        stored = await self.data_entities_processor.get_all_user_groups(self.connector_id, raise_on_error=True)
        kept: list[AppUserGroup] = []
        for group in stored:
            group_id = (group.source_user_group_id or "").lower()
            if not group_id or "@" not in group_id or group_id in listed_group_ids:
                continue
            state = await self._unlisted_group_state(group_id)
            if state in ("empty", "members"):
                if not await self._write_unlisted_group(group, state):
                    kept.append(group)
                continue
            if state != "gone":
                kept.append(group)
                continue
            self.logger.info(f"Group {group_id} was deleted in Google Workspace; removing it and its access")
            await self.data_entities_processor.on_user_group_deleted(group.source_user_group_id, self.connector_id)
        # A kept group was not rewritten this run, so the full sync's unlink would otherwise drop its members.
        self.data_entities_processor.note_kept_user_groups(self.connector_id, kept)

    async def _unlisted_group_state(self, group_id: str) -> str:
        """What the directory says of a group missing from the listing: "gone" only when members.list and
        groups.get both answer 404, "empty" when members.list answers that it has no members, "members" when it
        lists members, else "kept"."""
        try:
            answer = await self.admin_data_source.members_list(groupKey=group_id, maxResults=1)
        except HttpError as e:
            if e.resp.status != HttpStatusCode.NOT_FOUND.value:
                self.logger.warning(f"Could not confirm that group {group_id} was deleted; keeping it: {e}")
                return "kept"
            try:
                await self.admin_data_source.groups_get(groupKey=group_id)
            except HttpError as again:
                if again.resp.status != HttpStatusCode.NOT_FOUND.value:
                    self.logger.warning(f"Could not confirm that group {group_id} was deleted; keeping it: {again}")
                    return "kept"
                return "gone"
            self.logger.warning(f"Group {group_id} is missing from the listing but still exists; keeping it")
            return "kept"
        if self._is_empty_members_answer(answer):
            return "empty"
        if isinstance(answer, dict) and isinstance(answer.get("members"), list) and answer["members"]:
            return "members"
        self.logger.warning(f"Group {group_id} is missing from the listing but still exists; keeping it")
        return "kept"

    async def _write_unlisted_group(self, group: AppUserGroup, state: str) -> bool:
        """Write a stored group the listing left out with the membership the directory gives it now ("empty":
        none; "members": every page read, nested members included), so a user who left it loses the access
        granted to it. False when the members cannot all be read, or the write fails (the run is then
        incomplete); the group is then kept as stored."""
        group_id = group.source_user_group_id
        app_users: list[AppUser] = []
        if state == "members":
            try:
                app_users = await self._group_app_users(group_id, conclusive=True)
            except Exception as e:
                self.logger.warning(f"Could not read all members of group {group_id}; keeping it as stored: {e}")
                return False
        try:
            await self.data_entities_processor.on_new_user_groups([(group, app_users)])
        except Exception as e:
            self._sync_ledger.note_incomplete()
            self.logger.error(f"Could not write the members of group {group_id}: {e}", exc_info=True)
            return False
        self.logger.info(f"Group {group_id} is missing from the listing; wrote its {len(app_users)} current members")
        return True

    async def _process_group(self, group: Dict) -> None:
        """
        Process a single group: fetch members and create AppUserGroup with AppUser objects.

        Args:
            group: Group dictionary from Google Admin API
        """
        try:
            group_id = group.get("email")
            if not group_id:
                self.logger.warning("Skipping group without ID")
                return

            # Create AppUserGroup object
            group_name = group.get("name", "")
            if not group_name:
                group_name = group.get("email", group_id)

            # Convert creation time to epoch milliseconds
            source_created_at = None
            creation_time = group.get("creationTime")
            if creation_time:
                try:
                    source_created_at = parse_timestamp(creation_time)
                except Exception as e:
                    self.logger.warning(f"Failed to parse creation time for group {group_id}: {e}")

            user_group = AppUserGroup(
                source_user_group_id=group_id,
                app_name=self.connector_name,
                connector_id=self.connector_id,
                name=group_name,
                description=group.get("description"),
                source_created_at=source_created_at
            )

            self.logger.debug(f"Fetching members for group: {group.get('name', group_id)}")
            app_users = await self._group_app_users(group_id)

            # Stored even with no direct user members: a group of groups still needs its
            # node, or a file shared with it gets no grant edge and a synced user who
            # reaches it through a nested group is never asked before a delete.
            await self.data_entities_processor.on_new_user_groups([(user_group, app_users)])
            self.logger.debug(f"Processed group '{group_name}' with {len(app_users)} direct user members")

        except Exception as e:
            self.logger.error(f"Error processing group {group.get('id', 'unknown')}: {e}", exc_info=True)
            raise

    async def _group_app_users(self, group_id: str, *, conclusive: bool = False) -> list[AppUser]:
        """The group's users as the directory lists them now, every page and nested group read; raises when
        any page cannot be read, so a partial membership is never taken as the whole. With conclusive, it also
        raises when a page is neither a members list nor the directory's empty answer, or when a member stands
        for a whole domain (CUSTOMER), whose users the directory does not list."""
        # Drive gives the members of a nested group the outer group's access, so the group's users are
        # read with their nested memberships, not only its direct members.
        members = await self._fetch_group_members(group_id, include_derived=True, conclusive=conclusive)
        if conclusive and any(str(m.get("type", "")).upper() == "CUSTOMER" for m in members):
            raise ValueError(f"group {group_id} holds every user of a domain; its users are not listed")

        # Filter to only include user members (skip groups and customers), each once: a user can be a
        # member directly and through a nested group.
        seen_emails: set[str] = set()
        user_members = []
        for m in members:
            email_key = (m.get("email") or "").lower()
            if m.get("type") == "USER" and email_key not in seen_emails and not self._is_inactive_email(email_key):
                seen_emails.add(email_key)
                user_members.append(m)

        # Create AppUser objects for each member
        app_users: List[AppUser] = []
        for member in user_members:
            try:
                member_email = member.get("email", "")
                if not member_email:
                    self.logger.warning(f"Skipping member without email: {member.get('id')}")
                    continue

                # For group members, we may not have full user details
                # Use email as fallback for full_name if not available
                member_id = member.get("id", "")

                # Try to find user in synced users list for full details
                full_name = member_email  # Default to email
                source_created_at_user = None

                # Look up user in synced users if available
                if self.synced_users:
                    for synced_user in self.synced_users:
                        if synced_user.source_user_id == member_id or synced_user.email.lower() == member_email.lower():
                            full_name = synced_user.full_name
                            source_created_at_user = synced_user.source_created_at
                            break

                app_user = AppUser(
                    app_name=self.connector_name,
                    connector_id=self.connector_id,
                    source_user_id=member_id,
                    email=member_email,
                    full_name=full_name,
                    source_created_at=source_created_at_user
                )
                app_users.append(app_user)

                # Directory API reports an out-of-domain member as type USER (its
                # EXTERNAL type is documented "not currently used"), so external
                # members arrive through the same filter as everyone else and are
                # only distinguishable against the synced workspace set.
                self._track_external_collaborator(EntityType.USER, member_email)

            except Exception as e:
                self.logger.error(f"Error processing group member {member.get('id', 'unknown')}: {e}", exc_info=True)
                continue
        return app_users

    async def _clear_cut_out_grants_below_limited_folders(self) -> None:
        """For every folder that turned limited, this run or an earlier one not finished, settle the grants its
        stored descendants hold for members of the drive, who now reach them only if a grant names them: a limit cuts
        the members' access through the drive, and a grant of a member's own could have stood in for it.

        First, at once and for the whole subtree, the members' own grants on the descendants go, in batches on the
        store (Managers', who keep access through limits, stay). Then each descendant's list is read as a member who
        can read sharing, and the members it names directly get their grants back, at most
        LIMITED_SUBTREE_MAX_CHECKS descendants a run: until a descendant is read, a member it names directly cannot
        open it here (a short under-admit, never an over-admit). The rest, and a folder whose step failed, wait on
        the checkpoint for the next run."""
        if await self._copies_no_source_access():
            return
        pending = await self._load_limited_pending()
        left: list[dict[str, object]] = []
        budget = [LIMITED_SUBTREE_MAX_CHECKS]  # spent in place, so a check that raises still counts
        children_by_drive: dict[str, dict[str, list[Record]]] = {}
        for (drive_id, folder_id), state in sorted(pending.items(), key=lambda kv: kv[0]):
            if self._shared_drives_listed and drive_id not in self._listed_shared_drive_ids:
                # A listing can come back short, so only a drive confirmed gone, or one with no record group (another
                # organization's, which an earlier version queued), is dropped. One that still exists is settled.
                group = await self.data_entities_processor.get_record_group_by_external_id(self.connector_id, drive_id)
                exists = await self._shared_drive_exists(drive_id) if group is not None else False
                if exists is False:
                    self.logger.info(f"Shared drive {drive_id} is gone or not this Workspace's; folder {folder_id} is dropped")
                    continue
                if exists is None:  # unknown this run: kept, and its members are not read
                    if state.get("stripped"):  # grants to give back are waiting on it
                        self._sync_ledger.note_incomplete()
                    left.append({"drive": drive_id, "folder": folder_id, **state})
                    continue
            try:
                if drive_id not in children_by_drive:
                    children_by_drive[drive_id] = await self._stored_children_of_drive(drive_id)
                done = await self._clear_cut_out_grants_below(
                    drive_id, folder_id, state, budget, children_by_drive[drive_id]
                )
            except Exception as e:
                self.logger.warning(
                    f"The items below limited folder {folder_id} could not be settled ({type(e).__name__}: {e}); "
                    "they are settled next run"
                )
                self._sync_ledger.note_incomplete()
                done = False
            if not done:  # kept with the progress made, whether or not it raised
                left.append({"drive": drive_id, "folder": folder_id, **state})
        if pending:
            async with self._limited_pending_lock:
                await self._save_limited_pending(left)
        if self._upgrade_drives:
            marker = await self.drive_delta_sync_point.read_sync_point(STAND_IN_CLEANUP_SYNC_POINT_KEY) or {}
            done = sorted(set(marker.get(STAND_IN_CLEANED_DRIVES) or []) | self._upgrade_drives)
            await self.drive_delta_sync_point.update_sync_point(STAND_IN_CLEANUP_SYNC_POINT_KEY, {STAND_IN_CLEANED_DRIVES: done})

    async def _stored_children_of_drive(self, drive_id: str) -> dict[str, list[Record]]:
        """The stored records of a shared drive by their parent's external id. A failed read raises."""
        children: dict[str, list[Record]] = {}
        group = await self.data_entities_processor.get_record_group_by_external_id(self.connector_id, drive_id)
        if group is None or not getattr(group, "id", None):
            return children
        page_after: str | None = None
        page_size = drive_full_sync_reconciler.RECONCILE_PAGE_SIZE
        while True:
            page = await self.data_entities_processor.get_records_by_status(
                self.connector_id, None, limit=page_size, record_group_id=group.id, after_key=page_after
            )
            if not isinstance(page, list):
                raise TypeError("the stored record listing was not readable")
            for record in page:
                if record.parent_external_record_id:
                    children.setdefault(record.parent_external_record_id, []).append(record)
            if len(page) < page_size or not getattr(page[-1], "id", None):
                return children
            page_after = page[-1].id

    async def _clear_cut_out_grants_below(
        self, drive_id: str, folder_id: str, state: dict[str, object], budget: list[int],
        children: dict[str, list[Record]],
    ) -> bool:
        """Settle the stored descendants of one limited folder, recording progress in `state` and spending `budget`
        as it goes, so a failure keeps both. Returns whether it is finished. A failed read raises."""
        below: list[Record] = []
        todo, seen = [folder_id], {folder_id}
        while todo:
            for child in children.get(todo.pop(), ()):
                if child.external_record_id not in seen:
                    seen.add(child.external_record_id)
                    below.append(child)
                    todo.append(child.external_record_id)
        members = set(await self._drive_member_email_list(drive_id))
        managers = set(await self._drive_manager_emails(drive_id))
        if drive_id in self._drive_members_unread or drive_id in self._drive_sharers_unread:
            # Unknown, not empty: anyone could be a member or a Manager through the group, so nothing is decided.
            raise RuntimeError(f"A member group of shared drive {drive_id} could not be listed")
        cut = members - managers
        below.sort(key=lambda r: r.id)
        if not state.get("stripped"):
            await self.data_entities_processor.remove_user_grants_from_records(
                [r.id for r in below if r.id], cut, self.connector_id
            )
            state.update(after=None, stripped=True, retry=[])
        read_as = {"inherited_permissions_disabled": False, "inherits_from_drive": True, "item_drive_id": drive_id}

        async def give_back(record: Record) -> bool:
            """Give back the direct grants the descendant's list names. False when its read failed: it waits in
            the retry list, and the descendants after it are still read.

            shortcut: with no Manager to list it, nothing is given back, so a member's own share below the folder
            (a writer's too) is lost; asking each member as themselves cannot tell their own share from a group's or
            the folder's, and a user grant written from that would outlast those. An active Manager avoids it."""
            try:
                # Raises when someone who can read sharing got no list: nothing is decided from it.
                managed = await self._permissions_as_drive_manager(record.external_record_id, drive_id, None, read_as)
                # No one can read it: a member reaches it through the drive only (decision for such drives).
                named = [
                    p for p in (managed[0] if managed is not None else ())
                    if p.entity_type == EntityType.USER and (p.email or "").lower() in cut
                ]
                if named:
                    await self.data_entities_processor.add_permission_to_record(record, named)
            except Exception as e:
                self.logger.warning(
                    f"The grants of {record.external_record_id} below limited folder {folder_id} could not be given "
                    f"back yet ({type(e).__name__}: {e}); it is read again next run"
                )
                self._sync_ledger.note_incomplete()
                return False
            return True

        by_id = {r.id: r for r in below}
        retry = [i for i in state.get("retry") or [] if i in by_id]  # one no longer stored is not read
        state["retry"] = retry
        for record_id in list(retry):
            if budget[0] <= 0:
                return False
            budget[0] -= 1
            if await give_back(by_id[record_id]):
                retry.remove(record_id)
        for record in below:
            after = state.get("after")
            if after is not None and record.id <= after:
                continue
            if budget[0] <= 0:
                return False
            budget[0] -= 1
            if not await give_back(record):
                retry.append(record.id)
            state["after"] = record.id
        return not retry

    async def _drive_manager_emails(self, drive_id: str) -> list[str]:
        """Active Workspace users who are Managers of a shared drive; they keep access through limited folders."""
        return (await self._drive_members_by_role(drive_id))[0]
    async def _permissions_as_owner(
        self, file_id: str, metadata: dict, user_email: str | None, *, limited_access: bool
    ) -> tuple[list[Permission], bool, list[str]] | None:
        """A My Drive item's list read as its owner, for a reader who got an empty one: the owner can always read
        its sharing ("Can access detailed file permissions", owner: Yes,
        https://developers.google.com/workspace/drive/api/guides/ref-roles). None when the owner is the reader, is
        not an active user of this Workspace, or cannot be asked."""
        owner = next(
            (o.get("emailAddress", "").lower() for o in metadata.get("owners") or [] if o.get("emailAddress")), ""
        )
        by_email = {u.email.lower(): u for u in self.synced_users if u.email and u.is_active}
        if not owner or owner == (user_email or "").lower() or owner not in by_email:
            return None
        source = await self._build_user_drive_data_source(by_email[owner])
        try:
            read = await self._fetch_permissions(
                file_id, is_drive=False, user_email=user_email, drive_data_source=source,
                inherited_permissions_disabled=limited_access, can_share=True, item_is_stored=True,
            )
        except _EmptyPermissionList:
            return None
        except HttpError as e:
            if e.resp.status == HttpStatusCode.NOT_FOUND.value or is_permission_denied_403(e):
                return None
            raise
        return None if read[1] else read

    async def _proven_grants_after_held_runs(
        self, file_id: str, metadata: dict, user_email: str | None
    ) -> tuple[list[Permission], bool, list[str]] | None:
        """Count a run in which a stored My Drive item's list came back empty (once per run). After MAX_HELD_RUNS
        such runs its list is what can be proven, its owner and the reader who opened it, so a grant removed
        meanwhile does not stay for good. None until then: the item fails and keeps what is stored."""
        async with self._empty_list_lock:
            runs = await self._load_empty_list_runs()
            if file_id not in self._empty_list_counted:
                self._empty_list_counted.add(file_id)
                runs[file_id] = runs.get(file_id, 0) + 1
                await self._save_empty_list_runs(runs)
            if runs.get(file_id, 0) < MAX_HELD_RUNS:
                return None
        # A suspended owner keeps no grant: their access was taken off when they were suspended.
        proven = [
            Permission(email=o["emailAddress"], type=PermissionType.OWNER, entity_type=EntityType.USER)
            for o in metadata.get("owners") or []
            if o.get("emailAddress") and not self._is_inactive_email(o["emailAddress"])
        ]
        # Every reader who opened it this run, not only this one: each read replaces the stored list.
        readers = self._empty_list_readers.setdefault(file_id, set())
        # A limited folder its reader cannot list the children of shows them its name, not its content
        # (https://developers.google.com/workspace/drive/api/guides/limited-expansive-access): no grant.
        metadata_only = bool(metadata.get("inheritedPermissionsDisabled")) and not (
            (metadata.get("capabilities") or {}).get("canListChildren")
        )
        if user_email and not metadata_only:
            readers.add(user_email.lower())
        owners = {(p.email or "").lower() for p in proven}
        proven += [
            Permission(email=email, type=PermissionType.READ, entity_type=EntityType.USER)
            for email in sorted(readers - owners)
        ]
        self.logger.warning(
            f"The permission list of {file_id} came back empty {MAX_HELD_RUNS} runs in a row; only its owner and its "
            "reader keep access until a list can be read"
        )
        # Read again by id next run, until a real list is read; not a failure, so the feed moves on.
        if (failed := _failed_items.get()) is not None:
            failed.add(file_id)
        return DrivePermissionBatch(proven, saw_permission_details=False), False, []

    async def _load_empty_list_runs(self) -> dict[str, int]:
        if self._empty_list_runs is None:
            stored = await self.drive_delta_sync_point.read_sync_point(EMPTY_LIST_RUNS_SYNC_POINT_KEY) or {}
            try:
                runs = json.loads(stored.get("runs") or "{}")
            except ValueError:
                runs = {}
            self._empty_list_runs = {str(k): int(v) for k, v in runs.items()} if isinstance(runs, dict) else {}
        return self._empty_list_runs

    async def _save_empty_list_runs(self, runs: dict[str, int]) -> None:
        await self.drive_delta_sync_point.update_sync_point(
            EMPTY_LIST_RUNS_SYNC_POINT_KEY, {"runs": json.dumps(runs, sort_keys=True)}
        )

    async def _forget_empty_list_runs(self, file_id: str) -> None:
        async with self._empty_list_lock:
            # A later empty list in this run counts again, from nothing.
            self._empty_list_counted.discard(file_id)
            self._empty_list_readers.pop(file_id, None)
            runs = await self._load_empty_list_runs()
            if runs.pop(file_id, None) is not None:
                await self._save_empty_list_runs(runs)

    async def _reconcile_drive_restrictions(self, drive: dict) -> None:
        """A drive whose restrictions differ from the ones its items were last synced under is listed again in
        full for every user: a change feed reports no change on an item whose share a restriction now cuts
        or restores. The new restrictions are remembered only once that is done, so a failure repeats it.

        Assumed restrictions (an answer without them) are never saved. The first run that assumes them reads the
        drive again under them, so they reach every item, and marks it; later runs that still assume them keep the
        tokens. The first answer that has them lists the drive again because of the mark."""
        drive_id = drive.get("id")
        if not drive_id:
            return
        if isinstance(drive.get("restrictions"), dict):
            self._drive_restrictions[drive_id] = self._restriction_flags(drive["restrictions"])
        if await self._copies_no_source_access():
            return
        now = await self._drive_restrictions_of(drive_id)
        key = f"{DRIVE_RESTRICTIONS_SYNC_POINT_PREFIX}{drive_id}"
        stored = await self.drive_delta_sync_point.read_sync_point(key)
        assumed = bool((stored or {}).get(DRIVE_RESTRICTIONS_ASSUMED))
        if drive_id in self._assumed_restrictions:
            if not assumed:
                await self._read_drive_again(drive_id, reseed=True)
                # Marked only once the tokens are gone, so a failure in between reads it again next run.
                await self.drive_delta_sync_point.update_sync_point(key, {DRIVE_RESTRICTIONS_ASSUMED: True})
            return
        hidden = [str(g) for g in ((stored or {}).get("hidden_groups") or [])]
        seen = dict(str(e).rpartition("|")[::2] for e in ((stored or {}).get("group_hashes") or []))
        relist = assumed or now != self._restriction_flags(stored)
        for group in hidden:
            if relist:
                break
            # Relist only when it can help: a group hidden earlier can be read now.
            relist = await self._group_member_emails(group) is not None
        # The limits changed or a hidden group can be read: a file shared out of the drive with someone who is not a
        # member reaches them only through their own shared-with-me listing, which no change feed reports again.
        reseed = relist
        # Who is a member at all: a person who leaves a members-only drive keeps no access through a share of their
        # own ("Whether access to items inside this shared drive is restricted to its members",
        # https://developers.google.com/workspace/drive/api/reference/rest/v3/drives), and the drive's change log
        # records only "removal of a member", not its items
        # (https://developers.google.com/workspace/drive/api/guides/about-changes). The member list read here, users
        # and member groups, is the one remembered, so a change after it is caught by the next run; a drive without a
        # remembered list (written before it was kept) is read again once.
        membership = None
        if now.get("driveMembersOnly"):
            users, groups = await self._drive_direct_members(drive_id)
            membership = self._people_digest(users | {f"group:{g}" for g in groups})
            relist = relist or seen.get(DRIVE_DIRECT_MEMBERS_MARK) != membership
        for group, digest in seen.items():
            if relist:
                break
            if group == DRIVE_DIRECT_MEMBERS_MARK:
                continue
            # The grants written for the drive depend on who is in these groups. One that cannot be read now has
            # changed too: the relist hides it, and it is then remembered as hidden, so this happens once.
            people = await self._group_member_emails(group)
            relist = people is None or self._people_digest(people) != digest
        if not relist:
            self._hidden_groups[drive_id] = set(hidden)
            self._group_digests[drive_id] = seen
            return
        await self._read_drive_again(drive_id, reseed=reseed)
        if any(now.values()) and not await self._drive_has_synced_member(drive_id):
            # Nobody synced lists the drive again, so its stored items get the new limits here; a failure raises
            # before the new restrictions are saved, so the next run tries again.
            await self._apply_limits_to_stored_items(drive_id)
        digests = {DRIVE_DIRECT_MEMBERS_MARK: membership} if membership else {}
        await self.drive_delta_sync_point.update_sync_point(
            key,
            {
                **now, "hidden_groups": [], "group_hashes": [f"{g}|{d}" for g, d in digests.items()],
                DRIVE_RESTRICTIONS_ASSUMED: False,
            },
        )
        self._hidden_groups[drive_id] = set()
        self._group_digests[drive_id] = digests

    async def _apply_limits_to_stored_items(self, drive_id: str) -> None:
        """Apply a shared drive's limits to the stored grants of each of its items (`_drop_grants_the_drive_keeps_out`),
        for a drive no synced user lists again."""
        after: str | None = None
        while True:
            page = await self.data_entities_processor.get_records_in_record_group(
                self.connector_id, drive_id, limit=FILTER_CLEANUP_PAGE_SIZE, after_key=after
            )
            for record in page:
                if not record.is_placeholder:
                    await self._drop_grants_the_drive_keeps_out(record, drive_id)
            if len(page) < FILTER_CLEANUP_PAGE_SIZE:
                break
            after = page[-1].id

    async def _drop_grants_the_drive_keeps_out(self, record: Record, drive_id: str | None) -> None:
        """An item whose sharing the reader cannot see keeps its stored grants, but its drive's limits still apply to
        them, as `_restriction_filter` applies them to a list read in full: a person the drive keeps out loses their
        grant, and a group or audience it keeps out loses its grant, a group being replaced by grants for those of its
        people who pass, each with the group's stored role."""
        if not drive_id or drive_id not in self._listed_shared_drive_ids:
            return
        if not any((await self._drive_restrictions_of(drive_id)).values()):
            return
        restrict, may_stay = await self._restriction_filter(drive_id, is_drive=False)
        user_roles, group_roles = await self.data_entities_processor.get_roles_on_record(record.id)
        for email in list(user_roles):
            if not may_stay(email):
                await self.data_entities_processor.delete_principal_permission_from_record(
                    record.id, email, self.connector_id
                )
                del user_roles[email]
        held = [
            Permission(external_id=group_id, email=group_id, type=PermissionType(role), entity_type=EntityType.GROUP)
            for group_id, role in group_roles.items()
        ]
        kept = await restrict(held)
        kept_groups = {p.external_id for p in kept if p.entity_type == EntityType.GROUP}
        # A person has one edge and a grant replaces it, so each gets only their highest role from the split groups,
        # and only when it is above the role they already hold.
        best: dict[str, Permission] = {}
        for p in kept:
            email = (p.email or "").lower()
            if p.entity_type != EntityType.GROUP and _role_rank(p) > _role_rank(best.get(email), user_roles.get(email)):
                best[email] = p
        if best:
            await self.data_entities_processor.add_permission_to_record(record, list(best.values()))
        for group in held:
            if group.external_id not in kept_groups:
                await self.data_entities_processor.delete_group_permission_from_record(
                    record.id, group.external_id, self.connector_id
                )

    async def _drop_reporters_grant(
        self,
        record: Record,
        user: AppUser | None,
        sources: dict[str, GoogleDriveDataSource],
        drive_id: str | None,
    ) -> None:
        """The user who reported a file removed can no longer open it, whatever the others can: their own grant goes
        now. A failure raises, so the change is held and the grant is removed when it is read again, also when the
        file itself is kept. A user no longer in the directory lost every direct grant then (`_apply_user_status`).

        A My Drive item stores its folders' grants as its own, and Drive promises a change entry for each item below a
        changed one only in a shared drive ("When permissions change on a shared drive or an item inside a shared
        drive, ... The change log records a separate entry for the modified item and for each affected descendant",
        https://developers.google.com/workspace/drive/api/guides/about-changes#syncing_permissions_and_capabilities).
        So for a My Drive item, and each stored item below a My Drive folder, this user is asked whether they can still
        open it (`_drop_grants_of_people_who_cannot_open`). An item whose check fails is listed with the other parked
        items and asked about again on later runs, for all its holders and groups (`_recheck_parked_holders`), so the
        retry needs neither this user still synced nor still in the groups; the others, and the change, go on."""
        if await self._copies_no_source_access():
            return
        if user is None:
            return
        await self.data_entities_processor.delete_permission_from_record(
            record_id=record.id, user_email=user.email, connector_id=record.connector_id
        )
        if drive_id:
            return
        items, pending, seen = [record], [record.external_record_id], set()
        while pending and record.mime_type in FOLDER_MIME_TYPES:
            parent_id = pending.pop()
            if parent_id in seen:
                continue
            seen.add(parent_id)
            # Trashed ones too: restored, they would come back still granting this user.
            for child in await self.data_entities_processor.get_records_by_parent(
                self.connector_id, parent_id, visibility=RecordVisibility.ALL
            ):
                if child.mime_type in FOLDER_MIME_TYPES:
                    pending.append(child.external_record_id)
                items.append(child)
        for item in items:
            try:
                await self._drop_grants_of_people_who_cannot_open(item, sources, only=user)
            except Exception as e:
                self.logger.warning(
                    f"Could not tell whether a user removed from {record.record_name} can still open "
                    f"{item.record_name} ({_failure_summary(e)}); it is checked again on later runs"
                )
                await self._park_holder_recheck(item.external_record_id, None)

    async def _park_holder_recheck(self, file_id: str, reader_email: str | None) -> None:
        """List an item whose holders' check failed (see `_process_drive_item` and `_drop_reporters_grant`), with the
        reader the check left out, so the retry asks the same people. A failed write raises, so the item or the change
        that found it fails and is read again.

        shortcut: one list for the connector, read and rewritten whole; split it if it ever holds many thousands."""
        items = [str(x) for x in ((await self.drive_delta_sync_point.read_sync_point(HOLDER_RECHECK_SYNC_POINT_KEY) or {}).get("items") or [])]
        entry = f"{file_id}:{(reader_email or '').lower()}"  # Drive ids never contain ":"
        if entry not in items:
            await self.drive_delta_sync_point.update_sync_point(HOLDER_RECHECK_SYNC_POINT_KEY, {"items": sorted([*items, entry])})

    async def _recheck_parked_holders(self) -> None:
        """Ask again about each item `_park_holder_recheck` listed: every holder and group but the reader the first
        try left out (an item below a lost folder leaves out nobody). One that is gone or answered leaves the list; one
        whose check fails again stays. Nothing here holds a checkpoint."""
        if await self._copies_no_source_access():
            return
        items = [str(x) for x in ((await self.drive_delta_sync_point.read_sync_point(HOLDER_RECHECK_SYNC_POINT_KEY) or {}).get("items") or [])]
        left: list[str] = []
        for entry in items:
            file_id, _, reader_email = entry.partition(":")
            try:
                record = await self.data_entities_processor.get_record_by_external_id(
                    connector_id=self.connector_id, external_record_id=file_id
                )
                if record is not None:
                    await self._drop_grants_of_people_who_cannot_open(
                        record, self._holder_sources, skip_email=reader_email or None
                    )
            except Exception as e:
                self.logger.warning(f"Still not known who can open item {file_id}: {_failure_summary(e)}")
                left.append(entry)
        if left != items:
            await self.drive_delta_sync_point.update_sync_point(HOLDER_RECHECK_SYNC_POINT_KEY, {"items": left})

    async def _drop_grants_of_people_who_cannot_open(
        self,
        record: Record,
        sources: dict[str, GoogleDriveDataSource],
        *,
        only: AppUser | None = None,
        skip_email: str | None = None,
    ) -> None:
        """Remove the grant of each synced user the graph says can open the record (``only``: that user) who Drive
        says cannot: files.get answers 404 notFound when "the user doesn't have read access to a file, or the file
        doesn't exist" (https://developers.google.com/workspace/drive/api/guides/handle-errors#not-found). A file in
        the trash still opens: "other users can still access the file in the owner's trash until it's permanently
        deleted" (`trashed`, https://developers.google.com/workspace/drive/api/reference/rest/v3/files).

        A group's grant goes when one of its members gets that 404: a group permission grants "members of a specific
        group" (https://developers.google.com/workspace/drive/api/guides/manage-sharing), so a member who cannot open
        the item shows the group no longer grants it. Members who can open it another way keep their own grants. An
        audience grant (a discoverable share) is checked the same way: the people it always holds decide, and those of
        other domains it may reach only show it is still open.
        With ``only`` the groups asked about are that user's, and when that user still opens the item (perhaps through a
        share of their own) up to AUDIENCE_PROBES of the group's other members are asked too; otherwise up to
        AUDIENCE_PROBES members of each group, picked at random. A refusal or other error on a member's check decides
        nothing: a group whose asked members all failed to answer is undecided. Every holder and group is tried; then
        the first failure, or an undecided group, raises, so the item, change or parked item is read again rather than
        taken as kept. A group the directory will not list keeps its grant.

        shortcut: a holder outside this deployment's synced users (an external collaborator) cannot be asked and keeps
        their grant; ask through another source if one appears. A group none of whose asked members is cut off keeps
        its grant; ask more members if that proves too few."""
        synced = await self._synced_user_emails()
        by_email = {u.email.lower(): u for u in self.synced_users if u.email}
        holders = await self.data_entities_processor.get_users_with_permission_to_node(
            record.id, CollectionNames.RECORDS.value, raise_on_error=True
        )
        failure: Exception | None = None  # the first; every other holder and group is still tried
        for holder in holders:
            email = (holder.email or "").lower()
            if (
                not email
                or email == (skip_email or "").lower()
                or (only is not None and email != only.email.lower())
                or email not in synced
                or email not in by_email
            ):
                continue
            try:
                if not await self._user_can_open(record, by_email[email], sources, trashed_opens=True):
                    self.logger.info(f"Removing a grant on {record.record_name}: its holder can no longer open it")
                    await self.data_entities_processor.delete_permission_from_record(
                        record_id=record.id, user_email=holder.email, connector_id=record.connector_id
                    )
            except Exception as e:
                failure = failure or e
        groups = await self.data_entities_processor.get_groups_with_permission_to_node(
            record.id, CollectionNames.RECORDS.value, raise_on_error=True
        )
        answers: dict[str, bool | None] = {}
        undecided = False

        async def answer(email: str) -> bool | None:
            if email not in answers:
                answers[email] = await self._audience_member_can_open(
                    record, by_email[email], sources, trashed_opens=True
                )
            return answers[email]

        for group in groups:
            group_id = group.source_user_group_id
            if not group_id:
                continue
            if _is_audience_group(group_id):
                # An audience (a discoverable share): the people it always holds decide, those of other domains it
                # may reach only show it is still open, as for a stored record (`_another_synced_user_can_open`).
                own, more = (set(people) for people in self._audience_people(group_id))
            else:
                # A group the directory will not list cannot be asked about; its grant stays, as for a stored record
                # whose group members cannot be read (`_another_synced_user_can_open`).
                own, more = await self._synced_group_members(group_id) or set(), set()

            def pool_of(people: set[str]) -> list[str]:
                return sorted(e for e in people if e in by_email and e in synced and e != (skip_email or "").lower())

            asked: list[str] = []
            if only is not None:
                if only.email.lower() not in own:
                    continue
                asked.append(only.email.lower())
                # The reporting user still opening it may be through a share of their own: the group's other
                # people answer for the group then.
                if await answer(only.email.lower()) is not True:
                    asked_more: list[list[str]] = []
                else:
                    asked_more = [[e for e in pool_of(own) if e != only.email.lower()], pool_of(more - own)]
            else:
                asked_more = [pool_of(own), pool_of(more - own)]
            for pool in asked_more:
                asked += secrets.SystemRandom().sample(pool, min(max(AUDIENCE_PROBES - len(asked) + (only is not None), 0), len(pool)))
            found = [await answer(e) for e in asked]
            if any(answers[e] is False for e in asked if e in own):
                self.logger.info(f"Removing a group's grant on {record.record_name}: a member can no longer open it")
                try:
                    await self.data_entities_processor.delete_group_permission_from_record(
                        record.id, group_id, record.connector_id
                    )
                except Exception as e:
                    failure = failure or e
            elif found and all(f is None for f in found):
                undecided = True  # nobody asked answered: retried, never taken as kept
        if failure is not None:
            raise failure
        if undecided:
            # The item fails (and is read again by id), or the change or parked item stays listed.
            raise _RemovalUndecided(f"Whether a member of a group granted on {record.record_name} can open it is unknown")

    async def _queue_limited_subtree(self, drive_id: str, folder_id: str) -> None:
        """Note a folder that turned limited on the checkpoint at once: the user's feed checkpoint can move past
        the change before the run settles the folder's descendants, and nothing would report it again. A folder
        already waiting starts over: it turned limited again."""
        async with self._limited_pending_lock:
            pending = await self._load_limited_pending()
            pending[(drive_id, folder_id)] = {"after": None, "stripped": False, "retry": []}
            await self._save_limited_pending(
                [{"drive": d, "folder": f, **state} for (d, f), state in sorted(pending.items(), key=lambda kv: kv[0])]
            )

    async def _drive_role_of(self, drive_id: str, email: str) -> str | None:
        """The highest role (a PermissionType value) a shared drive's member list gives a person, directly or through
        a member group; None when it names them nowhere. A member group the directory cannot list adds nothing."""
        email, best = email.lower(), None
        for permission in await self._drive_acl(drive_id):
            if permission.entity_type == EntityType.USER:
                named = (permission.email or "").lower() == email
            elif permission.entity_type == EntityType.GROUP and permission.external_id:
                named = email in (await self._group_member_emails(permission.external_id) or ())
            else:
                named = False
            if named and PERMISSION_HIERARCHY.get(permission.type.value, 0) > PERMISSION_HIERARCHY.get(best or "", 0):
                best = permission.type.value
        return best

    async def _drive_members_by_role(self, drive_id: str) -> tuple[list[str], list[str]]:
        """Active Workspace users who are a shared drive's Managers, and those who are its Content managers or
        Contributors, directly or through a member group, this run. Not only those this deployment syncs: reading an
        item's sharing as one syncs nothing of theirs. A member group the directory cannot list adds nobody."""
        if drive_id not in self._drive_managers:
            by_type: dict[PermissionType, list[str]] = {PermissionType.OWNER: [], PermissionType.WRITE: []}
            for permission in await self._drive_acl(drive_id):
                if permission.type not in by_type:
                    continue
                if permission.entity_type == EntityType.USER and permission.email:
                    by_type[permission.type].append(permission.email.lower())
                elif permission.entity_type == EntityType.GROUP and permission.external_id:
                    people = await self._group_member_emails(permission.external_id)
                    if people is None:  # unknown, not empty: who can read sharing is not known
                        self._drive_sharers_unread.add(drive_id)
                    by_type[permission.type].extend(sorted(people or ()))
            active = {u.email.lower() for u in self.synced_users if u.email and u.is_active}
            managers = [e for e in dict.fromkeys(by_type[PermissionType.OWNER]) if e in active]
            writers = [e for e in dict.fromkeys(by_type[PermissionType.WRITE]) if e in active and e not in managers]
            self._drive_managers[drive_id] = (managers, writers)
            if not managers and not writers:
                self.logger.warning(
                    f"Shared drive {drive_id} has no active member who can read its items' sharing, so its members "
                    "reach them through the drive only. Adding an active Manager to the drive restores exact sharing"
                )
        return self._drive_managers[drive_id]

    async def _drive_sharing_readers(self, drive_id: str) -> list[str]:
        """Who can read a shared drive item's sharing, Managers first: "Can access detailed file permissions" is Yes
        for Managers, Content managers and Contributors, No for Commenters and Viewers
        (https://developers.google.com/workspace/drive/api/guides/ref-roles). Only Managers keep access through a
        limited folder (https://developers.google.com/workspace/drive/api/guides/limited-expansive-access), so a
        Content manager or Contributor a limit cuts out is passed over when their read is refused."""
        managers, writers = await self._drive_members_by_role(drive_id)
        return [*managers, *writers]

    async def _permissions_as_drive_manager(
        self, file_id: str, drive_id: str, user_email: str | None, read_as: dict
    ) -> tuple[list[Permission], bool, list[str]] | None:
        """The item's permission list read as a member of its shared drive who can read sharing (Managers first,
        then Content managers and Contributors), for a reader who cannot. None when none could read it: the
        reader's own grant is all that is known then.

        `user_email` stays the reader, so a link share still grants the reader as their own full read would. The
        list is the same for every member of the drive, so it is read once per item and run for all of them, the
        members' syncs that run side by side included."""
        member = (user_email or "").lower() in await self._drive_member_email_list(drive_id)
        key = (file_id, None if member else (user_email or "").lower())
        async with self._manager_read_locks.setdefault(key, asyncio.Lock()):
            if key not in self._manager_reads:
                self._manager_reads[key] = await self._read_as_drive_manager(file_id, drive_id, user_email, read_as)
        read = self._manager_reads[key]
        if read is None:
            return None
        permissions, is_fallback, shared = read
        return DrivePermissionBatch(
            list(permissions),
            saw_permission_details=getattr(permissions, "saw_permission_details", False),
            user_grants=getattr(permissions, "user_grants", None),
            grant_details_seen=getattr(permissions, "grant_details_seen", False),
            allowed_email=getattr(permissions, "allowed_email", None),
        ), is_fallback, list(shared)

    async def _read_as_drive_manager(
        self, file_id: str, drive_id: str, user_email: str | None, read_as: dict
    ) -> tuple[list[Permission], bool, list[str]] | None:
        by_email = {u.email.lower(): u for u in self.synced_users if u.email and u.is_active}
        tried, unanswered = 0, False
        for email in await self._drive_sharing_readers(drive_id):
            if email == (user_email or "").lower() or email not in by_email:
                continue
            if tried >= MAX_IMPERSONATION_CANDIDATES:
                break
            tried += 1
            source = self._manager_sources.get(email)
            if source is None:
                source = self._manager_sources[email] = await self._build_user_drive_data_source(by_email[email])
            try:
                # can_share: an empty list from one who can read sharing is not an answer; the next is asked.
                read = await self._fetch_permissions(
                    file_id, is_drive=False, user_email=user_email, drive_data_source=source, can_share=True, **read_as
                )
            except _EmptyPermissionList:
                unanswered = True
                continue
            except HttpError as e:
                if e.resp.status == HttpStatusCode.NOT_FOUND.value or is_permission_denied_403(e):
                    continue
                raise
            if not read[1]:
                return read
        if unanswered:
            # One who can read sharing got no entries, which is not a list: nothing is decided from it.
            raise _EmptyPermissionList(f"No one who can read the sharing of {file_id} got a list")
        if drive_id in self._drive_sharers_unread:
            raise RuntimeError(f"Who can read the sharing of shared drive {drive_id} is unknown: a group was not listed")
        return None

    async def _read_item_access(
        self,
        file_id: str,
        metadata: dict,
        file_record: FileRecord,
        existing_record: Record | None,
        user_email: str,
        drive_data_source: GoogleDriveDataSource | None,
        owner_emails: set[str],
    ) -> tuple[list[Permission], list[str], bool, bool, bool]:
        """An item's grants as Drive lists them, for ``_process_drive_item``: (the grants, the synced people given it
        on the item itself, whether the grants are the item's list, whether the record changed, whether its metadata
        did). Sets whether the record takes its parent's access. A failure raises: the item is not written without
        its grants, and the caller counts it as failed and holds the checkpoint.

        Without a licence key nothing is read: no grants, and the record's inherit flag is left alone; the processor
        keeps the stored grants and edges whatever it is handed (``skips_permission_writes``)."""
        new_permissions = []
        individually_shared_emails: List[str] = []
        permissions_changed = is_updated = metadata_changed = False
        if await self._copies_no_source_access():
            return new_permissions, individually_shared_emails, True, existing_record is not None, False
        try:
            # Fetch permissions for this file using the provided drive_data_source
            # If drive_data_source is provided, use it; otherwise fall back to service account
            limited_access = bool(metadata.get("inheritedPermissionsDisabled"))
            item_drive_id = metadata.get("driveId")
            if (
                limited_access and item_drive_id and existing_record is not None
                # Another organization's drive is not the admin's to list, as in the reader's read below.
                and item_drive_id in self._listed_shared_drive_ids
                and metadata.get("mimeType") == MimeTypes.GOOGLE_DRIVE_FOLDER.value
                and file_id not in self._limited_subtrees.get(item_drive_id, ())
                and (
                    item_drive_id in self._upgrade_drives
                    or await self.data_entities_processor.inheritance_when_unreadable(
                        CollectionNames.RECORDS.value, existing_record.id
                    )
                )
            ):
                # It turned limited (it still inherits as stored): the changes feed reports it alone, so what
                # its descendants hold for members it now cuts out is checked once the run has written it.
                self._limited_subtrees.setdefault(item_drive_id, set()).add(file_id)
                await self._queue_limited_subtree(item_drive_id, file_id)
            read_as = {
                "inherited_permissions_disabled": limited_access,
                "inherits_from_drive": not item_drive_id or file_record.external_record_group_id == item_drive_id,
                "item_drive_id": item_drive_id,
            }
            try:
                new_permissions, is_fallback_permissions, individually_shared_emails = await self._fetch_permissions(
                    file_id,
                    is_drive=False,
                    user_email=user_email,
                    drive_data_source=drive_data_source,
                    can_list_children=bool((metadata.get("capabilities") or {}).get("canListChildren")),
                    can_share=bool((metadata.get("capabilities") or {}).get("canShare")),
                    item_is_stored=existing_record is not None,
                    **read_as,
                )
                list_read = True
            except _EmptyPermissionList:
                if item_drive_id:
                    raise
                read = await self._permissions_as_owner(file_id, metadata, user_email, limited_access=limited_access)
                list_read = read is not None
                if read is None:
                    read = await self._proven_grants_after_held_runs(file_id, metadata, user_email)
                if read is None:
                    raise
                new_permissions, is_fallback_permissions, individually_shared_emails = read
            if list_read and not is_fallback_permissions:  # a new item may have been counted too
                await self._forget_empty_list_runs(file_id)
            member_reads_through_drive = keeps_own_grant = False
            # A drive this Workspace does not hold is not the admin's to list: its reader's own result stands.
            in_scope = bool(item_drive_id) and item_drive_id in self._listed_shared_drive_ids
            reader_lists = bool((metadata.get("capabilities") or {}).get("canShare")) or (
                in_scope and (user_email or "").lower() in await self._drive_sharing_readers(item_drive_id)
            )
            if in_scope and (is_fallback_permissions or not reader_lists):
                # Commenters and Viewers "can't access detailed file permissions", so whatever list they get is
                # not the item's (https://developers.google.com/workspace/drive/api/guides/ref-roles); a member
                # who can read sharing reads it instead.
                managed = await self._permissions_as_drive_manager(file_id, item_drive_id, user_email, read_as)
                if managed is not None:
                    new_permissions, is_fallback_permissions, individually_shared_emails = managed
                elif not is_fallback_permissions:
                    # Nobody can read it: only the reader's own access is known.
                    restrict = (await self._restriction_filter(item_drive_id, is_drive=False))[0]
                    own = [Permission(email=user_email, type=PermissionType.READ, entity_type=EntityType.USER)]
                    new_permissions = await restrict(own) if user_email else []
                    is_fallback_permissions, individually_shared_emails = True, []
                if managed is None and read_as["inherits_from_drive"] and user_email and (
                    await self._is_drive_member(item_drive_id, user_email)
                ):
                    # No Manager can list it: a member reaches it from the drive through the folders above, as in
                    # Drive, so a limited folder cuts them out; a grant of their own would outlast that. One who
                    # edits it while the drive makes them less than a writer holds a share of their own (or a
                    # group's) that gives more than the drive: their stored grant stays.
                    # shortcut: a member cut out by a limited folder whose own grant on this item is no more than
                    # their drive role loses it here, and a folder turning limited takes every member's own grant
                    # below it whatever its role (`_clear_cut_out_grants_below`), since only a Manager can list
                    # them; an active Manager on the drive avoids both.
                    new_permissions, member_reads_through_drive = [], True
                    keeps_own_grant = bool((metadata.get("capabilities") or {}).get("canEdit")) and (
                        PERMISSION_HIERARCHY.get(await self._drive_role_of(item_drive_id, user_email) or "", 0)
                        < PERMISSION_HIERARCHY[PermissionType.WRITE.value]
                    )

            if is_fallback_permissions:
                permissions_changed = False
                if limited_access:
                    # Its own metadata says it inherits nothing, whatever the list held. Its stored grants stay:
                    # on a shared drive item they are its own (what it inherits is not stored on it), and a writer
                    # may have just written them.
                    file_record.inherit_permissions = False
                    if existing_record is not None:
                        await self.data_entities_processor.stop_inheriting_permissions(
                            existing_record.id, self.connector_id
                        )
                elif member_reads_through_drive:
                    file_record.inherit_permissions = True
                else:
                    file_record.inherit_permissions = await self.data_entities_processor.inheritance_when_unreadable(
                        CollectionNames.RECORDS.value, existing_record.id if existing_record else None
                    )
                if existing_record is not None:
                    if new_permissions:
                        # The reader's own grant is a read grant whatever their role: it never lowers a role they
                        # hold, such as one a group's split gave them (`_drop_grants_the_drive_keeps_out`).
                        held = (await self.data_entities_processor.get_roles_on_record(existing_record.id))[0]
                        if raising := [
                            p for p in new_permissions
                            if _role_rank(p) > _role_rank(None, held.get((p.email or "").lower()))
                        ]:
                            await self.data_entities_processor.add_permission_to_record(existing_record, raising)
                    elif user_email and member_reads_through_drive and not keeps_own_grant:
                        # They reach it from the drive (a grant of their own, from an earlier sync, would outlast a
                        # limited folder): their own stored grant goes, everyone else's stays.
                        await self.data_entities_processor.delete_permission_from_record(
                            existing_record.id, user_email, self.connector_id
                        )
                    await self._drop_grants_the_drive_keeps_out(existing_record, item_drive_id)
                    if (
                        not item_drive_id
                        and not owner_emails & await self._synced_user_emails()
                        and existing_record.id not in self._holders_checked
                    ):
                        # Nobody synced can read this item's sharing again (its owner is suspended or not synced),
                        # so the grants stored from an earlier read are never replaced: each is checked instead.
                        # A check that fails is listed and asked again on later runs; the item is still written,
                        # so one undecided answer does not hold the reader's feed.
                        try:
                            await self._drop_grants_of_people_who_cannot_open(
                                existing_record, self._holder_sources, skip_email=user_email
                            )
                        except Exception as e:
                            self.logger.warning(
                                f"Could not check who can still open {existing_record.record_name} "
                                f"({_failure_summary(e)}); it is checked again on later runs"
                            )
                            await self._park_holder_recheck(file_id, user_email)
                        self._holders_checked.add(existing_record.id)
                    # Written as a metadata update, never as a new record: in a full sync the first write of
                    # a new record replaces its stored grants with the list given, and this user's single
                    # grant is not the file's list. A reader reaching the file first removed everyone else.
                    is_updated = True
                    metadata_changed = True
            else:
                permissions_changed = True
                file_record.inherit_permissions = item_inherits_parent(
                    saw_permission_details=bool(getattr(new_permissions, "saw_permission_details", False)),
                    inherited_permissions_disabled=limited_access,
                    is_fallback=False,
                )
                if existing_record:
                    is_updated = True

        except Exception as e:
            # Not written without its grants: the caller counts the item as failed and holds the checkpoint.
            self.logger.warning(
                f"Failed to fetch permissions for file {file_id} ({metadata.get('name', 'unknown')}): {e}"
            )
            raise
        return new_permissions, individually_shared_emails, permissions_changed, is_updated, metadata_changed
