"""Google Drive for Workspace: reading who may see what from Google Workspace.

Google groups with their members, the groups a discoverable ("anyone" or domain) share reaches, and the
grants below a folder that turned limited. The free connector keeps listing and syncing drives and files and
calls these methods; without a licence key it reads no Google groups (the processor's licence gate).
"""

import hashlib
import json
import secrets
from typing import TYPE_CHECKING, Dict, List, Optional

from googleapiclient.errors import HttpError

from app.config.constants.arangodb import (
    CollectionNames,
)
from app.config.constants.http_status_code import HttpStatusCode
from app.connectors.sources.google.drive.utils import drive_full_sync_reconciler
from app.models.entities import (
    AppUser,
    AppUserGroup,
    Record,
)
from app.models.permission import EntityType
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
    """Part of the permission-reading half of ``GoogleDriveTeamConnector``; the per-item list read is still in the connector."""

    async def _copies_no_source_access(self) -> bool:
        """Without a licence key a company-wide connector reads no access from Google Workspace."""
        return await self.data_entities_processor.permission_sync_gated(self.connector_id)

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
        found" is confirmed with groups.get for that exact group; any other answer keeps the group for the next run.
        """
        stored = await self.data_entities_processor.get_all_user_groups(self.connector_id, raise_on_error=True)
        for group in stored:
            group_id = (group.source_user_group_id or "").lower()
            if not group_id or "@" not in group_id or group_id in listed_group_ids:
                continue
            try:
                await self.admin_data_source.members_list(groupKey=group_id, maxResults=1)
            except HttpError as e:
                if e.resp.status != HttpStatusCode.NOT_FOUND.value:
                    self.logger.warning(f"Could not confirm that group {group_id} was deleted; keeping it: {e}")
                    continue
                try:
                    await self.admin_data_source.groups_get(groupKey=group_id)
                except HttpError as again:
                    if again.resp.status != HttpStatusCode.NOT_FOUND.value:
                        self.logger.warning(f"Could not confirm that group {group_id} was deleted; keeping it: {again}")
                        continue
                else:
                    self.logger.warning(f"Group {group_id} is missing from the listing but still exists; keeping it")
                    continue
            else:
                self.logger.warning(f"Group {group_id} is missing from the listing but still exists; keeping it")
                continue
            self.logger.info(f"Group {group_id} was deleted in Google Workspace; removing it and its access")
            await self.data_entities_processor.on_user_group_deleted(group.source_user_group_id, self.connector_id)

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

            # Fetch members for this group
            self.logger.debug(f"Fetching members for group: {group.get('name', group_id)}")
            # Drive gives the members of a nested group the outer group's access, so the group's users are
            # read with their nested memberships, not only its direct members.
            members = await self._fetch_group_members(group_id, include_derived=True)

            # Filter to only include user members (skip groups and customers), each once: a user can be a
            # member directly and through a nested group.
            seen_emails: set[str] = set()
            user_members = []
            for m in members:
                email_key = (m.get("email") or "").lower()
                if m.get("type") == "USER" and email_key not in seen_emails and not self._is_inactive_email(email_key):
                    seen_emails.add(email_key)
                    user_members.append(m)

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

            # Stored even with no direct user members: a group of groups still needs its
            # node, or a file shared with it gets no grant edge and a synced user who
            # reaches it through a nested group is never asked before a delete.
            await self.data_entities_processor.on_new_user_groups([(user_group, app_users)])
            self.logger.debug(f"Processed group '{group_name}' with {len(app_users)} direct user members")

        except Exception as e:
            self.logger.error(f"Error processing group {group.get('id', 'unknown')}: {e}", exc_info=True)
            raise

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
