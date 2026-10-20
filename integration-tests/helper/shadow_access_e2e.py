"""Prove a connector's ``authenticatedAs`` link reaches what its creator can open and find.

The creator signs in to PipesHub with one email and connected the source as an account
under another, so the source shares records with that account rather than with the
creator's email. With the link the creator opens and finds those records; with it
removed the product refuses them; a resync writes it back. A non-admin second user is
refused throughout.

The creator's own permissions can hide what the link adds, so the link is also lent to
the second user, who has none: whatever they open then came through it.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import pytest
import requests

from app.config.constants.arangodb import CollectionNames
from app.services.cache.accessible_records_cache import AccessibleRecordsCache
from helper.access_probe import SEARCH_LIMIT, SearchOutcome
from helper.authenticated_identity import _authenticator, _linked_user_keys
from helper.graph_provider_utils import wait_for_sync_completion
from helper.local_auth import jwt_claims, obtain_user_session_token
from helper.record_access import record_access_status, wait_for_record_access
from helper.second_user import NO_ACCESS_STATUSES, SecondUser
from retrieval.ranking import virtual_id_of

if TYPE_CHECKING:
    from helper.graph_provider import GraphProviderProtocol
    from helper.pipeshub_client import PipeshubClient
    from helper.vector_store import VectorStoreProbe

logger = logging.getLogger("shadow-access-e2e")

REACHABLE_LIMIT = 40
SAMPLE_PER_TYPE = 12
SAMPLE_LIMIT = 48
SEARCH_CANDIDATES = 3
SEARCH_PHRASE_WORDS = 14
LINK_RESTORE_TIMEOUT_SEC = 600
LINK_POLL_SEC = 5
# The accessible-records cache TTL (300s) with margin, should invalidating it from here miss.
SEARCH_SETTLE_TIMEOUT_SEC = 360
SEARCH_POLL_SEC = 15
REVOKE_SETTLE_SEC = 10
HUB_PAGE_LIMIT = 200

# How a user reaches each record, along the paths the record check follows
# (check_record_access_with_details). Org-wide shares are optional: every member has them,
# so they say nothing about what the linked account adds.
_ACCESS_PATHS = """
MATCH (u:User {id: $user_key})
MATCH (r:Record {connectorId: $connector_id})
WHERE coalesce(r.isDeleted, false) = false
WITH u, r,
  CASE
    WHEN EXISTS { (u)-[:PERMISSION {type: 'USER'}]->(r) } THEN 'direct'
    WHEN EXISTS {
      MATCH (u)-[:PERMISSION {type: 'USER'}]->(g)-[:PERMISSION]->(r)
      WHERE g:Group OR g:Role
    } THEN 'group'
    WHEN EXISTS {
      (u)-[:PERMISSION {type: 'USER'}]->(:RecordGroup)<-[:INHERIT_PERMISSIONS*1..5]-(r)
    } THEN 'record-group'
    WHEN EXISTS {
      MATCH (u)-[:PERMISSION {type: 'USER'}]->(g)-[p:PERMISSION]->(:RecordGroup)
            <-[:INHERIT_PERMISSIONS*1..5]-(r)
      WHERE (g:Group OR g:Role) AND p.type IN ['GROUP', 'ROLE']
    } THEN 'group-record-group'
    WHEN $with_org AND EXISTS { (u)-[:BELONGS_TO]->(:Organization)-[:PERMISSION]->(r) } THEN 'org'
    WHEN $with_org AND EXISTS {
      (u)-[:BELONGS_TO]->(:Organization)-[:PERMISSION]->(:RecordGroup)<-[:INHERIT_PERMISSIONS*1..2]-(r)
    } THEN 'org-record-group'
  END AS path
WHERE path IS NOT NULL
RETURN r.id AS id,
  path + CASE
    WHEN path ENDS WITH 'record-group' AND NOT EXISTS { (r)-[:INHERIT_PERMISSIONS]->(:RecordGroup) }
    THEN ' via parent record' ELSE ''
  END AS path
LIMIT $limit
"""

# The same paths in AQL, for an ArangoDB run.
_ACCESS_PATHS_AQL = """
LET u = CONCAT(@users, '/', @user_key)
LET own = (FOR p IN @@permission FILTER p._from == u AND p.type == 'USER' RETURN p._to)
LET groups = (FOR t IN own FILTER IS_SAME_COLLECTION(@groups, t) OR IS_SAME_COLLECTION(@roles, t) RETURN t)
LET via_group = (FOR g IN groups FOR p IN @@permission FILTER p._from == g RETURN p._to)
LET user_rgs = (FOR t IN own FILTER IS_SAME_COLLECTION(@record_groups, t) RETURN t)
LET via_rg = (FOR rg IN user_rgs FOR v IN 1..5 INBOUND rg @@inherit RETURN v._id)
LET group_rgs = (
  FOR g IN groups FOR p IN @@permission
  FILTER p._from == g AND p.type IN ['GROUP', 'ROLE'] AND IS_SAME_COLLECTION(@record_groups, p._to)
  RETURN p._to
)
LET via_group_rg = (FOR rg IN group_rgs FOR v IN 1..5 INBOUND rg @@inherit RETURN v._id)
LET orgs = @with_org ? (
  FOR b IN @@belongs_to FILTER b._from == u AND IS_SAME_COLLECTION(@orgs, b._to) RETURN b._to
) : []
LET via_org = (FOR o IN orgs FOR p IN @@permission FILTER p._from == o RETURN p._to)
LET org_rgs = (FOR t IN via_org FILTER IS_SAME_COLLECTION(@record_groups, t) RETURN t)
LET via_org_rg = (FOR rg IN org_rgs FOR v IN 1..2 INBOUND rg @@inherit RETURN v._id)
FOR r IN @@records
  FILTER r.connectorId == @connector_id AND r.isDeleted != true
  LET path = (
    POSITION(own, r._id) ? 'direct'
    : POSITION(via_group, r._id) ? 'group'
    : POSITION(via_rg, r._id) ? 'record-group'
    : POSITION(via_group_rg, r._id) ? 'group-record-group'
    : POSITION(via_org, r._id) ? 'org'
    : POSITION(via_org_rg, r._id) ? 'org-record-group'
    : null
  )
  FILTER path != null
  LET in_record_group = LENGTH(
    FOR e IN @@inherit FILTER e._from == r._id AND IS_SAME_COLLECTION(@record_groups, e._to) LIMIT 1 RETURN 1
  ) > 0
  LIMIT @limit
  RETURN {
    id: r._key,
    path: CONCAT(path, LIKE(path, '%record-group') AND NOT in_record_group ? ' via parent record' : ''),
  }
"""


@dataclass(frozen=True)
class Link:
    connector_id: str
    org_id: str
    creator: dict[str, Any]
    source: dict[str, Any]

    @property
    def creator_key(self) -> str:
        return self.creator.get("_key") or self.creator.get("id")

    @property
    def source_key(self) -> str:
        return self.source.get("_key") or self.source.get("id")

    @property
    def source_email(self) -> str:
        return self.source.get("email") or self.source_key


@dataclass
class SearchCandidate:
    record_id: str
    virtual_id: str
    queries: tuple[str, ...]
    refused_without_link: tuple[bool, str] = (False, "not probed")


@dataclass
class Report:
    connector_id: str
    creator_email: str
    source_email: str
    reachable: int = 0
    sampled: int = 0
    opened_with_link: int = 0
    creator_paths: dict[str, int] = field(default_factory=dict)
    shadow_only: list[str] = field(default_factory=list)
    restored: list[str] = field(default_factory=list)
    gone_after_resync: int = 0
    listed: str = "not checked"
    search: str = "not checked"
    search_proven: int = 0

    def summary(self) -> str:
        return (
            f"connector {self.connector_id}: {self.creator_email} linked to {self.source_email}; "
            f"{self.reachable} record(s) the linked account reaches, {self.sampled} sampled, "
            f"{self.opened_with_link} opened with the link (creator's own paths to them: "
            f"{self.creator_paths}), {len(self.shadow_only)} only through it "
            f"({self.gone_after_resync} gone after the resync); knowledge hub: {self.listed}; "
            f"search: {self.search}"
        )


async def verify_shadow_access(
    *,
    connector_id: str,
    pipeshub_client: PipeshubClient,
    graph_provider: GraphProviderProtocol,
    config_service: Any,
    vector_store: VectorStoreProbe,
    second_user: SecondUser,
    sync_timeout: int,
) -> Report:
    """Run the with-link, without-link and restored checks; return what each saw."""
    link = await _resolve_link(graph_provider, connector_id)
    creator = _creator_session(pipeshub_client, link)
    records = await _live_records(graph_provider, connector_id)
    reachable = await _access_paths(graph_provider, link.source_key, connector_id, with_org=False)
    creator_paths = await _access_paths(graph_provider, link.creator_key, connector_id, with_org=True)
    report = Report(connector_id, creator.email, link.source_email, reachable=len(reachable))

    opened = _check_with_link(creator, records, reachable, link, report)
    report.creator_paths = dict(Counter(creator_paths.get(rid, "none") for rid in opened))
    listed_with = _creator_listing(creator, connector_id, "with the link")
    await _drop_link(graph_provider, config_service, link)
    try:
        candidates = await _check_without_link(creator, records, opened, link, vector_store, report)
        # A creator with no access of their own is refused the whole app once the link goes
        listed_without = _listed_among(
            creator, connector_id, set(report.shadow_only), "without the link",
        )
    except BaseException:
        await _relink_directly(graph_provider, config_service, link)
        raise
    try:
        await _restore_link_by_resync(pipeshub_client, graph_provider, link, sync_timeout)
    except BaseException:
        await _relink_directly(graph_provider, config_service, link)
        raise
    await _check_restored(graph_provider, creator, second_user, link, report)
    report.listed = _listing_verdict(creator, second_user, link, report, listed_with, listed_without)
    unlisted = set(report.shadow_only) - listed_with
    if unlisted:
        report.listed += (
            f" ({len(listed_with)} node(s) listed in all; link-only listed: "
            f"{_profile(set(report.shadow_only) & listed_with, records, reachable)}; "
            f"unlisted: {_profile(unlisted, records, reachable)})"
        )
    report.search_proven, report.search = _search_verdict(creator, second_user, connector_id, candidates)
    return report


async def verify_lent_link_grants_access(
    *,
    connector_id: str,
    graph_provider: GraphProviderProtocol,
    config_service: Any,
    second_user: SecondUser,
) -> str:
    """Lend the creator's link to the second user and return what they opened through it."""
    link = await _resolve_link(graph_provider, connector_id)
    targets = await _reached_only_by_source(graph_provider, link, second_user)
    _assert_refused(second_user, connector_id, targets, "before the link was lent to them")
    lent = Link(connector_id, link.org_id, {"id": second_user.graph_id}, link.source)
    try:
        await _relink_directly(graph_provider, config_service, lent)
        listed = _assert_opened(second_user, targets, link)
    finally:
        await _relink_directly(graph_provider, config_service, link)
    _assert_refused(second_user, connector_id, targets, "once the creator's link was restored")
    return (
        f"connector {connector_id}: {second_user.email} lent the link to {link.source_email}; "
        f"{len(targets)} record(s) only that account reaches (its paths: "
        f"{dict(Counter(targets.values()))}) refused and unlisted before, opened with the link "
        f"({listed} listed in the knowledge hub), refused and unlisted after"
    )


async def _reached_only_by_source(
    graph_provider: GraphProviderProtocol, link: Link, user: SecondUser,
) -> dict[str, str]:
    reachable = await _access_paths(graph_provider, link.source_key, link.connector_id, with_org=False)
    own = await _access_paths(graph_provider, user.graph_id, link.connector_id, with_org=True)
    targets = {rid: path for rid, path in reachable.items() if rid not in own}
    if not targets:
        pytest.skip(
            f"{user.email} reaches all {len(reachable)} record(s) {link.source_email} reaches on "
            f"{link.connector_id} through their own permissions "
            f"({dict(Counter(own[rid] for rid in reachable))}), so the link cannot be isolated"
        )
    return dict(list(targets.items())[:REACHABLE_LIMIT])


def _assert_refused(user: SecondUser, connector_id: str, targets: dict[str, str], when: str) -> None:
    statuses = _open_all(user, list(targets))
    _assert_settled(statuses, user, when)
    opened = [rid for rid, status in statuses.items() if status == 200]
    assert not opened, f"{user.email} opened {len(opened)} record(s) {when}: {opened[:10]}"
    listed = _listed_among(user, connector_id, set(targets), when)
    assert not listed, f"the knowledge hub listed {len(listed)} of them to {user.email} {when}: {sorted(listed)[:10]}"


def _assert_opened(user: SecondUser, targets: dict[str, str], link: Link) -> int:
    """Every target opens while linked, and the knowledge hub lists some; return how many."""
    when = f"while linked to {link.source_email}"
    statuses = _open_all(user, list(targets))
    _assert_settled(statuses, user, when)
    refused = {rid: (status, targets[rid]) for rid, status in statuses.items() if status != 200}
    assert not refused, (
        f"{user.email} could not open {len(refused)} record(s) that {link.source_email} reaches "
        f"while linked to it (record: (status, path)): {dict(list(refused.items())[:10])}"
    )
    listed = _listed_among(user, link.connector_id, set(targets), when)
    assert listed, f"the knowledge hub listed none of the {len(targets)} record(s) {user.email} opens {when}"
    return len(listed)


async def _resolve_link(graph_provider: GraphProviderProtocol, connector_id: str) -> Link:
    creator = await _authenticator(graph_provider, connector_id)
    creator_key = creator.get("_key") or creator.get("id")
    linked = await _linked_user_keys(graph_provider, creator_key, connector_id)
    if not linked:
        pytest.skip(
            f"connector {connector_id} has no authenticatedAs link: the source account it "
            f"reads as carries {creator.get('email')}, the creator's own email"
        )
    source = await graph_provider.get_document(linked[0], CollectionNames.USERS.value)
    assert source, f"connector {connector_id} links to user {linked[0]}, which is not in the graph"
    return Link(connector_id, creator.get("orgId"), creator, source)


def _creator_session(client: PipeshubClient, link: Link) -> SecondUser:
    """The creator logged in with a password: the same token the UI uses, not the OAuth app."""
    token = obtain_user_session_token(client.base_url, client.timeout_seconds)
    user_id = link.creator.get("userId")
    if jwt_claims(token).get("userId") != user_id:
        pytest.skip(
            f"PIPESHUB_TEST_USER_EMAIL is not {link.creator.get('email')}, "
            f"who created connector {link.connector_id}"
        )
    return SecondUser(
        user_id=user_id,
        graph_id=link.creator_key,
        email=link.creator.get("email") or "",
        token=token,
        base_url=client.base_url,
        timeout=client.timeout_seconds,
    )


async def _live_records(
    graph_provider: GraphProviderProtocol, connector_id: str,
) -> dict[str, dict[str, Any]]:
    records = await graph_provider.fetch_records_by_type(connector_id, "")
    # ArangoDB hands back raw documents, keyed by _key
    return {
        rid: r for r in records
        if (rid := r.get("id") or r.get("_key")) and r.get("isDeleted") is not True
    }


async def _access_paths(
    graph_provider: GraphProviderProtocol, user_key: str, connector_id: str, *, with_org: bool,
) -> dict[str, str]:
    params = {"user_key": user_key, "connector_id": connector_id, "with_org": with_org, "limit": 5000}
    if os.getenv("TEST_GRAPH_DB_TYPE", "neo4j").lower() == "arango":
        rows = await graph_provider.execute_query(_ACCESS_PATHS_AQL, bind_vars={
            **params,
            "users": CollectionNames.USERS.value,
            "groups": CollectionNames.GROUPS.value,
            "roles": CollectionNames.ROLES.value,
            "record_groups": CollectionNames.RECORD_GROUPS.value,
            "orgs": CollectionNames.ORGS.value,
            "@permission": CollectionNames.PERMISSION.value,
            "@inherit": CollectionNames.INHERIT_PERMISSIONS.value,
            "@belongs_to": CollectionNames.BELONGS_TO.value,
            "@records": CollectionNames.RECORDS.value,
        })
    else:
        rows = await graph_provider.client.execute_query(_ACCESS_PATHS, params)
    return {row["id"]: row["path"] for row in rows or []}


def _sample(records: dict[str, dict[str, Any]], reachable: dict[str, str]) -> list[str]:
    """Records the linked account reaches, then a spread over every record type."""
    picked = [rid for rid in reachable if rid in records][:REACHABLE_LIMIT]
    by_type: dict[str, list[str]] = defaultdict(list)
    indexed_first = sorted(
        records.items(), key=lambda kv: (kv[1].get("indexingStatus") != "COMPLETED", kv[0]),
    )
    for rid, record in indexed_first:
        by_type[record.get("recordType") or "?"].append(rid)
    chosen = set(picked)
    for ids in by_type.values():
        extra = [rid for rid in ids if rid not in chosen][:SAMPLE_PER_TYPE]
        picked.extend(extra)
        chosen.update(extra)
    return picked[: REACHABLE_LIMIT + SAMPLE_LIMIT]


def _open_all(user: SecondUser, record_ids: list[str]) -> dict[str, int]:
    return {rid: record_access_status(user, rid) for rid in record_ids}


def _assert_settled(statuses: dict[str, int], user: SecondUser, when: str) -> None:
    """A 5xx or an expired login proves neither access nor refusal."""
    unsettled = {rid: s for rid, s in statuses.items() if s != 200 and s not in NO_ACCESS_STATUSES}
    assert not unsettled, f"opening records as {user.email} {when} gave errors: {unsettled}"


def _check_with_link(
    creator: SecondUser,
    records: dict[str, dict[str, Any]],
    reachable: dict[str, str],
    link: Link,
    report: Report,
) -> list[str]:
    sample = _sample(records, reachable)
    statuses = _open_all(creator, sample)
    _assert_settled(statuses, creator, "with the link")
    refused = {
        rid: (statuses[rid], reachable[rid])
        for rid in sample if rid in reachable and statuses[rid] != 200
    }
    assert not refused, (
        f"{creator.email} could not open {len(refused)} record(s) that {link.source_email} reaches, "
        f"though the link stands (record: (status, path)): {dict(list(refused.items())[:10])}"
    )
    opened = [rid for rid, status in statuses.items() if status == 200]
    report.sampled, report.opened_with_link = len(sample), len(opened)
    return opened


async def _drop_link(graph_provider: GraphProviderProtocol, config_service: Any, link: Link) -> None:
    removed = await graph_provider.remove_authenticated_as(link.connector_id)
    assert removed, f"connector {link.connector_id} had no authenticatedAs link to remove"
    await _invalidate_search_cache(config_service, link)
    await asyncio.sleep(REVOKE_SETTLE_SEC)


async def _relink_directly(graph_provider: GraphProviderProtocol, config_service: Any, link: Link) -> None:
    await graph_provider.upsert_authenticated_as(
        link.creator_key, link.source_key, link.connector_id, link.org_id,
    )
    await _invalidate_search_cache(config_service, link)


async def _invalidate_search_cache(config_service: Any, link: Link) -> None:
    """Do what the product does on a permission change, so search sees the new link at once."""
    cache = await AccessibleRecordsCache.create(logger, config_service)
    try:
        if not isinstance(cache, AccessibleRecordsCache):
            logger.warning(
                "Accessible-records cache unreachable from the test; search checks wait out its TTL",
            )
            return
        await cache.mark_connector_changed(link.org_id, link.connector_id)
        await cache.invalidate_connector(link.org_id, link.connector_id)
    finally:
        await cache.close()


async def _check_without_link(
    creator: SecondUser,
    records: dict[str, dict[str, Any]],
    opened: list[str],
    link: Link,
    vector_store: VectorStoreProbe,
    report: Report,
) -> list[SearchCandidate]:
    statuses = _open_all(creator, opened)
    _assert_settled(statuses, creator, "without the link")
    report.shadow_only = [rid for rid, status in statuses.items() if status in NO_ACCESS_STATUSES]
    if not report.shadow_only:
        pytest.skip(
            f"{creator.email} opens every sampled record of {link.connector_id} through their own "
            f"permissions, so none depends on the link: {report.summary()}"
        )
    candidates = await _search_candidates(records, report.shadow_only, vector_store)
    for candidate in candidates:
        candidate.refused_without_link = _poll_search(
            creator, link.connector_id, candidate, want_found=False,
        )
    return candidates


async def _restore_link_by_resync(
    client: PipeshubClient, graph_provider: GraphProviderProtocol, link: Link, sync_timeout: int,
) -> None:
    """The connector writes the link at the start of every sync; a resync must bring it back."""
    client.resync_connector(link.connector_id)
    deadline = time.monotonic() + LINK_RESTORE_TIMEOUT_SEC
    while True:
        linked = await _linked_user_keys(graph_provider, link.creator_key, link.connector_id)
        if linked == [link.source_key]:
            break
        assert time.monotonic() < deadline, (
            f"a resync of {link.connector_id} did not link {link.creator.get('email')} to "
            f"{link.source_email} again within {LINK_RESTORE_TIMEOUT_SEC}s (links: {linked})"
        )
        await asyncio.sleep(LINK_POLL_SEC)
    await wait_for_sync_completion(client, graph_provider, link.connector_id, timeout=sync_timeout)


async def _check_restored(
    graph_provider: GraphProviderProtocol,
    creator: SecondUser,
    second_user: SecondUser,
    link: Link,
    report: Report,
) -> None:
    live = await _live_records(graph_provider, link.connector_id)
    still_there = [rid for rid in report.shadow_only if rid in live]
    report.restored = still_there
    report.gone_after_resync = len(report.shadow_only) - len(still_there)
    for rid in still_there:
        wait_for_record_access(
            creator, rid, expect_access=True,
            description=f"a record only {link.source_email} reaches, once the resync re-linked them",
        )
    statuses = _open_all(second_user, still_there)
    wrong = {rid: status for rid, status in statuses.items() if status not in NO_ACCESS_STATUSES}
    assert not wrong, f"non-admin {second_user.email} should be refused these records, got {wrong}"


async def _search_candidates(
    records: dict[str, dict[str, Any]], shadow_only: list[str], vector_store: VectorStoreProbe,
) -> list[SearchCandidate]:
    # A virtual id shared with another record of the connector could come back through that one.
    shared = Counter(r.get("virtualRecordId") for r in records.values())
    candidates: list[SearchCandidate] = []
    for rid in shadow_only:
        record = records.get(rid) or {}
        virtual_id = record.get("virtualRecordId")
        if not virtual_id or shared[virtual_id] > 1 or record.get("indexingStatus") != "COMPLETED":
            continue
        texts = await vector_store.content_texts(virtual_id, limit=32)
        queries = _queries_for(record, texts) if texts else ()
        if queries:
            candidates.append(SearchCandidate(rid, virtual_id, queries))
        if len(candidates) == SEARCH_CANDIDATES:
            break
    return candidates


def _queries_for(record: dict[str, Any], texts: list[str]) -> tuple[str, ...]:
    """The record's title, and the opening words of its longest indexed chunk."""
    name = (record.get("recordName") or "").strip()
    phrase = " ".join(max(texts, key=len).split()[:SEARCH_PHRASE_WORDS])
    return tuple(q for q in (name, phrase) if len(q) >= 3)


def _search(user: SecondUser, connector_id: str, query: str) -> SearchOutcome:
    resp = user.search_filtered(query, {"apps": [connector_id]}, SEARCH_LIMIT)
    if resp.status_code != 200:
        return SearchOutcome(resp.status_code, ())
    body = resp.json()
    hits = (body.get("searchResponse") or body).get("searchResults") or []
    return SearchOutcome(200, tuple(vid for vid in (virtual_id_of(hit) for hit in hits) if vid))


def _poll_search(
    user: SecondUser,
    connector_id: str,
    candidate: SearchCandidate,
    *,
    want_found: bool,
    timeout: float = SEARCH_SETTLE_TIMEOUT_SEC,
) -> tuple[bool, str]:
    """Poll until every query agrees (found by one, or refused by all); return whether it did."""
    deadline = time.monotonic() + timeout
    while True:
        outcomes = [_search(user, connector_id, q) for q in candidate.queries]
        if want_found:
            settled = any(o.found(candidate.virtual_id) for o in outcomes)
        else:
            settled = all(o.refused_or_missing(candidate.virtual_id) for o in outcomes)
        if settled or time.monotonic() >= deadline:
            return settled, "; ".join(o.describe() for o in outcomes)
        time.sleep(SEARCH_POLL_SEC)


def _search_verdict(
    creator: SecondUser, second_user: SecondUser, connector_id: str, candidates: list[SearchCandidate],
) -> tuple[int, str]:
    """How many link-only records search proved, and what it saw."""
    if not candidates:
        return 0, "not checked: no indexed record is reachable only through the link"
    proven = []
    for candidate in candidates:
        found, seen = _poll_search(creator, connector_id, candidate, want_found=True)
        if not found:
            logger.info(
                "Search inconclusive for record %s: its own title and text did not return it "
                "with the link either (%s)", candidate.record_id, seen,
            )
            continue
        refused, seen_without = candidate.refused_without_link
        assert refused, (
            f"search kept returning record {candidate.record_id} to {creator.email} "
            f"after the link was removed ({seen_without})"
        )
        second_refused, seen_second = _poll_search(
            second_user, connector_id, candidate, want_found=False, timeout=0,
        )
        assert second_refused, (
            f"search returned record {candidate.record_id} to non-admin {second_user.email} ({seen_second})"
        )
        proven.append(candidate.record_id)
    if not proven:
        return 0, f"inconclusive: none of {len(candidates)} indexed link-only record(s) came back for its own text"
    return len(proven), (
        f"{len(proven)} link-only record(s) found with the link, not without it, never by the second user"
    )


def _listed(user: SecondUser, connector_id: str) -> tuple[int, set[str]]:
    """The status and node ids the knowledge hub lists for this user anywhere under the connector."""
    ids: set[str] = set()
    page = 1
    while True:
        resp = requests.get(
            f"{user.base_url}/api/v1/knowledgeBase/knowledge-hub/nodes/app/{connector_id}",
            headers=user.headers,
            params={"flattened": "true", "page": page, "limit": HUB_PAGE_LIMIT},
            timeout=user.timeout,
        )
        if resp.status_code != 200:
            return resp.status_code, ids
        body = resp.json()
        ids.update(item["id"] for item in body.get("items") or [] if item.get("id"))
        if page >= ((body.get("pagination") or {}).get("totalPages") or 1):
            return 200, ids
        page += 1


def _creator_listing(creator: SecondUser, connector_id: str, when: str) -> set[str]:
    status, ids = _listed(creator, connector_id)
    assert status == 200, f"the knowledge hub answered {status} listing {connector_id} for {creator.email} {when}"
    return ids


def _listed_among(user: SecondUser, connector_id: str, record_ids: set[str], when: str) -> set[str]:
    """Which of these records the knowledge hub lists for the user; a refusal lists none."""
    status, ids = _listed(user, connector_id)
    assert status == 200 or status in NO_ACCESS_STATUSES, (
        f"the knowledge hub answered {status} listing {connector_id} for {user.email} {when}"
    )
    return record_ids & ids


def _listing_verdict(
    creator: SecondUser,
    second_user: SecondUser,
    link: Link,
    report: Report,
    listed_with: set[str],
    listed_without: set[str],
) -> str:
    """Link-only records are listed while the link stands, and to no one else."""
    shadow = set(report.shadow_only)
    kept = shadow & listed_without
    assert not kept, (
        f"the knowledge hub kept listing {len(kept)} link-only record(s) to {creator.email} "
        f"after the link was removed: {sorted(kept)[:10]}"
    )
    shown = shadow & listed_with
    assert shown, (
        f"the knowledge hub listed none of the {len(shadow)} record(s) {creator.email} opens "
        f"only through the link to {link.source_email}"
    )
    expected_back = shown & set(report.restored)
    back = _listed_among(creator, link.connector_id, expected_back, "after the resync")
    assert back == expected_back, (
        f"after the resync the knowledge hub no longer lists {sorted(expected_back - back)[:10]} "
        f"to {creator.email}"
    )
    leaked = _listed_among(second_user, link.connector_id, shadow, "as the second user")
    assert not leaked, f"the knowledge hub listed link-only records to {second_user.email}: {sorted(leaked)[:10]}"
    return (
        f"{len(shown)} of {len(shadow)} link-only record(s) listed with the link, none without "
        f"it, all {len(back)} still live listed after the resync, none to the second user"
    )


def _profile(record_ids: set[str], records: dict[str, dict[str, Any]], reachable: dict[str, str]) -> dict:
    """Records by (type, mime type, how the linked account reaches them), to explain a listing gap."""
    return dict(Counter(
        (
            records.get(rid, {}).get("recordType"),
            records.get(rid, {}).get("mimeType"),
            reachable.get(rid, "not reached"),
        )
        for rid in record_ids
    ))
