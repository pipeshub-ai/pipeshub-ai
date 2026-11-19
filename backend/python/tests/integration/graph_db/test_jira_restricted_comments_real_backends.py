"""Jira comments only some readers of their issue may read, against a real Neo4j and ArangoDB.

Jira shows a comment restricted to a group or a project role to the readers of its issue in that group or role, and a
Service Management internal note to the readers of its issue with product access (not portal customers). Each such
comment is a record of its own under its issue; the issue's content holds only public comments. Comments are built
and written by the connector's own code on the project world of ``test_jira_issue_security_real_backends``, and every
user's access is read three ways: the access check, search, and the Knowledge Hub listing under the issue.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from app.config.constants.arangodb import CollectionNames, Connectors
from app.models.entities import AppRole, AppUserGroup
from app.modules.retrieval.retrieval_service import RetrievalService
from app.utils.chat_helpers import accessible_node_ids
from tests.integration.graph_db import (
    test_jira_issue_security_real_backends as project_world,
)
from tests.integration.graph_db.test_jira_issue_security_real_backends import (
    ISSUES,
    PEOPLE,
    PROJECT_ID,
    PROJECT_KEY,
    _access,
    _edited,
    _email,
    _listed_under,
    _reindex,
    _sync_project,
    _ticket_ids,
    _users,
)

pytestmark = [pytest.mark.integration, pytest.mark.timeout(300)]
world = project_world.world  # the same fixture: one project per run, on Neo4j and on ArangoDB

ADMINS_GROUP, PRODUCT_GROUP = "g-admins", "g-product"
GROUPS = {ADMINS_GROUP: {"alice", "dave"}, PRODUCT_GROUP: {"alice", "bob"}}
ROLE_MEMBERS = {"carol"}  # PROJ's Administrators role, id 10002


def _comment(comment_id: str, *, group: str | None = None, role: str | None = None, internal: bool = False) -> dict:
    out: dict[str, Any] = {"id": comment_id, "author": {"accountId": PEOPLE["bob"], "displayName": "bob"},
                           "created": "2024-06-15T10:00:00.000+0000", "updated": "2024-06-15T10:00:00.000+0000",
                           "jsdPublic": not internal}
    if group:
        out["visibility"] = {"type": "group", "value": "jira-admins", "identifier": group}
    if role:
        out["visibility"] = {"type": "role", "value": role, "identifier": role}
    return out


async def _comment_world(world: dict[str, Any]) -> None:
    await _sync_project(world)
    connector, processor, users = world["connector"], world["processor"], {u.email: u for u in _users(world)}
    by_name = {name: users[_email(name, world["run"])] for name in PEOPLE}
    await processor.on_new_user_groups([
        (AppUserGroup(app_name=Connectors.JIRA, connector_id=world["connector_id"], source_user_group_id=gid, name=gid,
                      org_id=world["org_id"]), [by_name[n] for n in sorted(members)])
        for gid, members in GROUPS.items()])
    await processor.on_new_app_roles([(AppRole(app_name=Connectors.JIRA, connector_id=world["connector_id"],
                                               source_role_id=f"{PROJECT_KEY}_10002", name="PROJ - Administrators",
                                               org_id=world["org_id"]), [by_name[n] for n in sorted(ROLE_MEMBERS)])])
    connector._role_ids = {"Administrators": "10002"}
    connector._group_ids = {gid: gid for gid in GROUPS}
    connector._app_roles_mapping = {"jira-servicedesk": [{"groupId": PRODUCT_GROUP}]}


async def _write_comments(world: dict[str, Any], label: str, updated: str, comments: list[dict]) -> None:
    """Write ``label``'s issue with ``comments`` as a sync does, removing what the build says is gone."""
    issue = _edited(label, updated, comment={"comments": comments, "total": len(comments)})
    built, delete_ids = await world["connector"]._build_issue_records([issue], PROJECT_ID, _users(world))
    if delete_ids:
        await world["processor"].on_records_deleted_cascade(delete_ids, world["connector_id"], cascade_children=False)
    await world["connector"]._process_new_records(built, PROJECT_KEY, {"new_count": 0, "updated_count": 0})


async def _comment_ids(world: dict[str, Any]) -> dict[str, str]:
    docs = await world["graph"].get_nodes_by_filters(CollectionNames.RECORDS.value, {"connectorId": world["connector_id"]})
    out = {}
    for d in docs:
        if d.get("recordType") == "COMMENT":
            record_id = d.get("id") or d.get("_key")
            out[d["externalRecordId"]] = record_id
            await world["graph"].update_node(record_id, CollectionNames.RECORDS.value, {
                "indexingStatus": "COMPLETED", "virtualRecordId": f"vr-{record_id}"})
    return out


async def _comment_mismatches(world: dict[str, Any], readers: dict[str, set[str]]) -> list[str]:
    """Where PipesHub disagrees with ``readers`` (comment external id -> who Jira shows it to) by the access check,
    search, and the Knowledge Hub listing under the issue."""
    ids = await _comment_ids(world)
    assert set(ids) == set(readers), f"comment records {sorted(ids)}, expected {sorted(readers)}"
    tickets = await _ticket_ids(world)
    problems: list[str] = []
    for name in PEOPLE:
        checked, searched, _ = await _access(world, name, list(ids.values()))
        issue_readers = {issue["key"] for issue, who in ISSUES.values() if name in who}
        for ext, record_id in ids.items():
            allowed = name in readers[ext]
            for how, seen in (("read", checked), ("find in search", searched)):
                if (record_id in seen) != allowed:
                    problems.append(f"{name} {'cannot' if allowed else 'can'} {how} {ext}")
        for key in issue_readers & COMMENTS_UNDER.keys():  # issues with no comment list none
            listed = await _listed_under(world, name, tickets[key]) & set(ids.values())
            expected = {record_id for ext, record_id in ids.items() if name in readers[ext]
                        and ext in COMMENTS_UNDER.get(key, ())}
            if listed != expected:
                problems.append(f"{name} under {key}: listed {sorted(listed)}, expected {sorted(expected)}")
    return problems


COMMENTS_UNDER: dict[str, tuple[str, ...]] = {"PROJ-1": ("comment_501", "comment_502", "comment_503"),
                                              "PROJ-2": ("comment_511",)}


async def test_a_restricted_comment_is_read_by_the_readers_of_its_issue_its_role_or_group_names(
    world: dict[str, Any],
) -> None:
    """dave is in the group but cannot browse the project; bob reads the issue but is in neither the group nor the role."""
    await _comment_world(world)
    await _write_comments(world, "plain task", "2024-06-16T10:00:00.000+0000", [
        _comment("500"), _comment("501", group=ADMINS_GROUP), _comment("502", role="Administrators"),
        _comment("503", internal=True)])
    await _write_comments(world, "leveled epic", "2024-06-16T10:00:00.000+0000", [_comment("511", group=ADMINS_GROUP)])

    readers = {"comment_501": {"alice"}, "comment_502": {"carol"}, "comment_503": {"alice", "bob"},
               "comment_511": {"alice"}}
    assert not await _comment_mismatches(world, readers)


async def test_a_comment_made_public_or_deleted_loses_its_record_and_one_restricted_again_gets_it_back(
    world: dict[str, Any],
) -> None:
    await _comment_world(world)
    await _write_comments(world, "plain task", "2024-06-16T10:00:00.000+0000", [
        _comment("501", group=ADMINS_GROUP), _comment("502", role="Administrators")])
    await _write_comments(world, "plain task", "2024-06-17T10:00:00.000+0000", [{**_comment("501"), "updated": "2024-06-17T10:00:00.000+0000"}])
    assert not await _comment_ids(world)

    moved = {**_comment("501", role="Administrators"), "updated": "2024-06-18T10:00:00.000+0000"}
    await _write_comments(world, "plain task", "2024-06-18T10:00:00.000+0000", [moved])
    assert not await _comment_mismatches(world, {"comment_501": {"carol"}})


async def test_deleting_an_issue_deletes_its_comment_records(world: dict[str, Any]) -> None:
    await _comment_world(world)
    await _write_comments(world, "plain task", "2024-06-16T10:00:00.000+0000", [_comment("501", group=ADMINS_GROUP)])
    assert await _comment_ids(world)
    stored = await world["processor"].get_record_by_external_id(connector_id=world["connector_id"], external_record_id="1")
    await world["connector"]._delete_issue_record(stored, "PROJ-1")
    assert not await _comment_ids(world)



FIGURES = {"id": "777", "filename": "figures.txt", "mimeType": "text/plain", "size": 5, "created": "2024-06-15T10:00:00.000+0000"}
SHOWS_FIGURES = '<p><a href="https://site.example/rest/api/3/attachment/content/777">figures.txt</a></p>'


async def _write_with_file(world: dict[str, Any], updated: str, comments: list[dict], total: int | None = None) -> None:
    """Write the plain task with the figures file and ``comments`` (``total`` more than them: the rest unread)."""
    issue = _edited("plain task", updated, attachment=[FIGURES],
                    comment={"comments": comments, "total": len(comments) if total is None else total})
    issue["renderedFields"] = {"description": "", "comment": {"comments": [
        {"id": c["id"], "body": c.get("renderedBody", "")} for c in comments]}}
    built, delete_ids = await world["connector"]._build_issue_records([issue], PROJECT_ID, _users(world))
    if delete_ids:
        await world["processor"].on_records_deleted_cascade(delete_ids, world["connector_id"], cascade_children=False)
    await world["connector"]._process_new_records(built, PROJECT_KEY, {"new_count": 0, "updated_count": 0})


async def _file_readers(world: dict[str, Any]) -> set[str] | None:
    """Who may read the figures file, or None when it has no record."""
    stored = await world["processor"].get_record_by_external_id(
        connector_id=world["connector_id"], external_record_id="attachment_777")
    if stored is None:
        return None
    return {name for name in PEOPLE if await world["graph"].filter_accessible_record_ids(
        [stored.id], world["user_ids"][name], world["org_id"])}


async def test_a_file_shown_only_in_a_restricted_comment_is_read_by_the_readers_of_that_comment(
    world: dict[str, Any],
) -> None:
    """Jira shows a file attached in a restricted comment only to those who can read the comment
    (JRACLOUD-80445); bob and carol read the issue but are not in the group."""
    await _comment_world(world)
    await _write_with_file(world, "2024-06-16T10:00:00.000+0000",
                           [{**_comment("501", group=ADMINS_GROUP), "renderedBody": SHOWS_FIGURES}])
    stored = await world["processor"].get_record_by_external_id(
        connector_id=world["connector_id"], external_record_id="attachment_777")
    assert stored is not None and stored.parent_external_record_id == "comment_501"
    assert await _file_readers(world) == {"alice"}


async def test_a_file_never_outlives_its_comment_record_under_a_wider_parent(world: dict[str, Any]) -> None:
    """When the comments cannot be read the comment record goes, and its file with it; when the comment is
    made public the file is filed under the issue again, as Jira shows it to every reader of the issue then."""
    await _comment_world(world)
    restricted = {**_comment("501", group=ADMINS_GROUP), "renderedBody": SHOWS_FIGURES}
    await _write_with_file(world, "2024-06-16T10:00:00.000+0000", [restricted])
    assert await _file_readers(world) == {"alice"}

    world["connector"]._read_issue_comments = AsyncMock(side_effect=RuntimeError("HTTP 503"))
    await _write_with_file(world, "2024-06-17T10:00:00.000+0000", [], total=1)  # the comment list is unread
    assert not await _comment_ids(world)
    assert await _file_readers(world) in (None, set(), {"alice"}), "the file is read beyond its comment's readers"

    public = {**_comment("501"), "renderedBody": SHOWS_FIGURES, "updated": "2024-06-18T10:00:00.000+0000"}
    await _write_with_file(world, "2024-06-18T10:00:00.000+0000", [public])
    assert await _file_readers(world) == {"alice", "bob", "carol"}


async def test_search_and_chat_retrieval_hand_a_restricted_comment_only_to_its_group(world: dict[str, Any]) -> None:
    """Every search and chat path ends in one of these checks. The enumerated map they start from
    gives a comment record to every reader of its issue, so each check must take it away again
    from bob and carol, who read the issue but are not in the group."""
    await _comment_world(world)
    await _write_comments(world, "plain task", "2024-06-16T10:00:00.000+0000", [_comment("501", group=ADMINS_GROUP)])
    record_id = (await _comment_ids(world))["comment_501"]  # indexed as vr-<record id>
    vrid, graph, org_id = f"vr-{record_id}", world["graph"], world["org_id"]
    retrieval = RetrievalService.__new__(RetrievalService)
    retrieval.graph_provider = graph

    problems: list[str] = []
    for name in PEOPLE:
        user_id, allowed = world["user_ids"][name], name == "alice"
        user = await graph.get_user_by_user_id(user_id)
        user_key = user.get("id") or user.get("_key")
        enumerated = await graph.get_accessible_virtual_record_ids(user_id, org_id, raise_on_error=True)
        hits = [{"metadata": {"virtualRecordId": vrid}}]
        seen = {
            # search_with_filters, record-id path and narrow-access fallback (retrieval_service.py _keep_permitted_hits)
            "search hit": vrid in await retrieval._keep_permitted_hits(hits, enumerated, user_key, org_id),
            # search_with_filters, container path; search_lexical with scopes
            "container search hit": vrid in (await graph.check_access(
                user_key, org_id, virtual_record_ids={vrid}, indexed_only=True)).records_by_vrid,
            # search_lexical containers path, grep and pattern-match merge
            "keyword hit": vrid in await graph.filter_accessible_virtual_record_ids([vrid], user_id, org_id),
            # chat's fetch_full_record and GET /records/{id}/read
            "fetched record": record_id in await accessible_node_ids(graph, {record_id}, user_id, org_id),
            # chat's graph context enrichment (children, attachments, parents of hits)
            "related record": record_id in await graph.filter_accessible_record_ids([record_id], user_id, org_id),
        }
        problems += [f"{name} {'cannot' if allowed else 'can'} get the comment as a {how}"
                     for how, got in seen.items() if got != allowed]
    assert not problems, "\n  ".join(problems)


async def test_a_reindex_does_not_let_the_next_sync_skip_a_comment_given_to_another_group(world: dict[str, Any]) -> None:
    """The comment moves from the product group (alice, bob) to the admins (alice, dave) and the issue is
    reindexed before the next sync. The reindex must not store the moved timestamp without the comment:
    the next sync would skip the issue and bob would keep the comment."""
    await _comment_world(world)
    await _write_comments(world, "plain task", "2024-06-16T10:00:00.000+0000", [_comment("501", group=PRODUCT_GROUP)])
    assert not await _comment_mismatches(world, {"comment_501": {"alice", "bob"}})

    moved = {**_comment("501", group=ADMINS_GROUP), "updated": "2024-06-17T10:00:00.000+0000"}
    issue = _edited("plain task", "2024-06-17T10:00:00.000+0000", comment={"comments": [moved], "total": 1})
    await _reindex(world, issue)
    await _write_comments(world, "plain task", "2024-06-17T10:00:00.000+0000", [moved])  # the next incremental sync

    assert not await _comment_mismatches(world, {"comment_501": {"alice"}})


async def test_a_reporter_outside_the_project_reads_a_comment_of_their_issue_their_group_names(
    world: dict[str, Any],
) -> None:
    """Browse held by the reporter: dave reads his own issue, so the comment restricted to his group too, by the access
    check and by search; bob and carol, outside the group, read neither."""
    await _comment_world(world)
    world["connector"]._issue_security[PROJECT_ID].browse_holders = [{"type": "reporter"}]
    issue = _edited("plain task", "2024-06-16T10:00:00.000+0000", reporter={"accountId": PEOPLE["dave"]},
                    comment={"comments": [_comment("501", group=ADMINS_GROUP)], "total": 1})
    built, _ = await world["connector"]._build_issue_records([issue], PROJECT_ID, _users(world), is_new_project=True)
    await world["connector"]._process_new_records(built, PROJECT_KEY, {"new_count": 0, "updated_count": 0})
    record_id = (await _comment_ids(world))["comment_501"]  # indexed as vr-<record id>
    graph, org_id = world["graph"], world["org_id"]
    for name, allowed in (("alice", True), ("dave", True), ("bob", False), ("carol", False)):
        user_id = world["user_ids"][name]
        checked = await graph.filter_accessible_record_ids([record_id], user_id, org_id)
        searched = await graph.filter_accessible_virtual_record_ids([f"vr-{record_id}"], user_id, org_id)
        assert (bool(checked), bool(searched)) == (allowed, allowed), f"{name} {'cannot' if allowed else 'can'} read comment_501"


async def test_a_reporter_outside_the_project_reads_a_file_shown_only_in_a_comment_their_group_names(
    world: dict[str, Any],
) -> None:
    """Browse held by the reporter: dave reads his issue and the comment restricted to his group, so the file shown only in it."""
    await _comment_world(world)
    world["connector"]._issue_security[PROJECT_ID].browse_holders = [{"type": "reporter"}]
    shown = {**_comment("501", group=ADMINS_GROUP),
             "renderedBody": '<p><a href="https://site.example/rest/api/3/attachment/content/777">figures.txt</a></p>'}
    issue = _edited("plain task", "2024-06-16T10:00:00.000+0000", reporter={"accountId": PEOPLE["dave"]},
                    comment={"comments": [shown], "total": 1},
                    attachment=[{"id": "777", "filename": "figures.txt", "mimeType": "text/plain", "size": 5,
                                 "created": "2024-06-15T10:00:00.000+0000"}])
    issue["renderedFields"] = {"description": "", "comment": {"comments": [{"id": "501", "body": shown["renderedBody"]}]}}
    built, _ = await world["connector"]._build_issue_records([issue], PROJECT_ID, _users(world), is_new_project=True)
    await world["connector"]._process_new_records(built, PROJECT_KEY, {"new_count": 0, "updated_count": 0})
    stored = await world["processor"].get_record_by_external_id(
        connector_id=world["connector_id"], external_record_id="attachment_777")
    assert stored is not None and stored.parent_external_record_id == "comment_501"
    for name, allowed in (("alice", True), ("dave", True), ("bob", False), ("carol", False)):
        checked = await world["graph"].filter_accessible_record_ids([stored.id], world["user_ids"][name], world["org_id"])
        assert bool(checked) == allowed, f"{name} {'cannot' if allowed else 'can'} read the file"
