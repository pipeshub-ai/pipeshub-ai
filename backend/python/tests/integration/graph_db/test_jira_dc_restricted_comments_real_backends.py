"""Jira Data Center comments only some readers of their issue may read, against a real Neo4j and ArangoDB.

Jira shows a comment restricted to a group, or to a project role, to the readers of its issue in that group or holding
that role in the issue's project. Each such comment is a record of its own under its issue (PARENT_AND_GRANT); the
issue's content holds only public comments. Comments are built and written by the connector's own code on the project
world of ``test_jira_dc_issue_security_real_backends``, and every user's access is read three ways: the access check,
search, and the Knowledge Hub listing under the issue. ``postgres/test_jira_dc_restricted_comments_pg.py`` runs the
same tests on PostgreSQL.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config.constants.arangodb import CollectionNames, Connectors
from app.models.entities import AppRole, AppUserGroup
from app.modules.retrieval.retrieval_service import RetrievalService
from app.utils.chat_helpers import accessible_node_ids
from tests.integration.graph_db import (
    test_jira_dc_issue_security_real_backends as project_world,
)
from tests.integration.graph_db.test_jira_dc_issue_security_real_backends import (
    ISSUES,
    PEOPLE,
    PROJECT_ID,
    PROJECT_KEY,
    _edited,
    _email,
    _ticket_ids,
    _users,
)

pytestmark = [pytest.mark.integration, pytest.mark.timeout(300)]
world = project_world.world  # the same fixture: one project per run, on Neo4j and on ArangoDB

ADMINS_GROUP = "g-admins"
GROUPS = {ADMINS_GROUP: {"alice", "dave"}, "g-product": {"alice", "bob"}}
ROLE_MEMBERS = {"carol"}  # PROJ's Administrators role, id 10002


def _comment(comment_id: str, *, group: str | None = None, role: str | None = None,
             updated: str = "2024-06-15T10:00:00.000+0000") -> dict:
    """As Data Center returns it: a restriction names its group or role by name (VisibilityJsonBean)."""
    out: dict[str, Any] = {"id": comment_id, "author": {"key": PEOPLE["bob"], "name": "bob", "displayName": "bob"},
                           "created": "2024-06-15T10:00:00.000+0000", "updated": updated}
    if group:
        out["visibility"] = {"type": "group", "value": group}
    if role:
        out["visibility"] = {"type": "role", "value": role}
    return out


async def _comment_world(world: dict[str, Any]) -> None:
    await project_world._sync_project(world)
    connector, processor, users = world["connector"], world["processor"], {u.email: u for u in _users(world)}
    by_name = {name: users[_email(name, world["run"])] for name in PEOPLE}
    await processor.on_new_user_groups([
        (AppUserGroup(app_name=Connectors.JIRA, connector_id=world["connector_id"], source_user_group_id=name, name=name,
                      org_id=world["org_id"]), [by_name[n] for n in sorted(members)])
        for name, members in GROUPS.items()])
    await processor.on_new_app_roles([(AppRole(app_name=Connectors.JIRA, connector_id=world["connector_id"],
                                               source_role_id=f"{PROJECT_KEY}_10002", name="PROJ - Administrators",
                                               org_id=world["org_id"]), [by_name[n] for n in sorted(ROLE_MEMBERS)])])
    connector._role_ids = {"Administrators": "10002"}


async def _write_comments(world: dict[str, Any], label: str, updated: str, comments: list[dict], *,
                          again: bool = False, **fields: object) -> None:
    """Write ``label``'s issue with ``comments`` as a sync does (the build removes the comment records that are gone)."""
    issue = _edited(label, updated, comment={"comments": comments, "total": len(comments)}, **fields)
    built = await world["connector"]._build_issue_records([issue], PROJECT_ID, _users(world), is_new_project=again)
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


COMMENTS_UNDER: dict[str, tuple[str, ...]] = {"PROJ-1": ("comment_501", "comment_502"), "PROJ-2": ("comment_511",)}


async def _comment_mismatches(world: dict[str, Any], readers: dict[str, set[str]]) -> list[str]:
    """Where PipesHub disagrees with ``readers`` (comment external id -> who Jira shows it to) by the access check,
    search, and the Knowledge Hub listing under the issue."""
    ids = await _comment_ids(world)
    assert set(ids) == set(readers), f"comment records {sorted(ids)}, expected {sorted(readers)}"
    tickets = await _ticket_ids(world)
    problems: list[str] = []
    for name in PEOPLE:
        checked, searched, _ = await project_world._access(world, name, list(ids.values()))
        for ext, record_id in ids.items():
            allowed = name in readers[ext]
            for how, seen in (("read", checked), ("find in search", searched)):
                if (record_id in seen) != allowed:
                    problems.append(f"{name} {'cannot' if allowed else 'can'} {how} {ext}")
        issue_readers = {issue["key"] for issue, who in ISSUES.values() if name in who}
        for key in issue_readers & COMMENTS_UNDER.keys():
            listed = await project_world._listed_under(world, name, tickets[key]) & set(ids.values())
            expected = {record_id for ext, record_id in ids.items()
                        if name in readers[ext] and ext in COMMENTS_UNDER[key]}
            if listed != expected:
                problems.append(f"{name} under {key}: listed {sorted(listed)}, expected {sorted(expected)}")
    return problems


async def test_a_restricted_comment_is_read_by_the_readers_of_its_issue_its_role_or_group_names(
    world: dict[str, Any],
) -> None:
    """dave is in the group but cannot browse the project; bob reads the issue but is in neither the group nor the role."""
    await _comment_world(world)
    await _write_comments(world, "plain task", "2024-06-16T10:00:00.000+0000", [
        _comment("500"), _comment("501", group=ADMINS_GROUP), _comment("502", role="Administrators")])
    await _write_comments(world, "leveled epic", "2024-06-16T10:00:00.000+0000", [_comment("511", group=ADMINS_GROUP)])

    readers = {"comment_501": {"alice"}, "comment_502": {"carol"}, "comment_511": {"alice"}}
    assert not await _comment_mismatches(world, readers)


async def test_a_comment_made_public_or_deleted_loses_its_record_and_one_restricted_again_gets_it_back(
    world: dict[str, Any],
) -> None:
    await _comment_world(world)
    await _write_comments(world, "plain task", "2024-06-16T10:00:00.000+0000", [
        _comment("501", group=ADMINS_GROUP), _comment("502", role="Administrators")])
    await _write_comments(world, "plain task", "2024-06-17T10:00:00.000+0000",
                          [_comment("501", updated="2024-06-17T10:00:00.000+0000")])
    assert not await _comment_ids(world)

    moved = _comment("501", role="Administrators", updated="2024-06-18T10:00:00.000+0000")
    await _write_comments(world, "plain task", "2024-06-18T10:00:00.000+0000", [moved])
    assert not await _comment_mismatches(world, {"comment_501": {"carol"}})


async def test_deleting_an_issue_deletes_its_comment_records(world: dict[str, Any]) -> None:
    await _comment_world(world)
    await _write_comments(world, "plain task", "2024-06-16T10:00:00.000+0000", [_comment("501", group=ADMINS_GROUP)])
    assert await _comment_ids(world)
    ds = MagicMock()
    ds.get_issue_v2 = AsyncMock(return_value=MagicMock(status=404))
    world["connector"]._get_fresh_datasource = AsyncMock(return_value=ds)
    # The audit log names the issue by key; this world's issues have no web address to look a key up by.
    stored = await world["processor"].get_record_by_external_id(connector_id=world["connector_id"], external_record_id="1")
    world["processor"].get_record_by_issue_key = AsyncMock(return_value=stored)

    await world["connector"]._handle_deleted_issue("PROJ-1")

    assert not await _comment_ids(world)
    assert "PROJ-1" not in await _ticket_ids(world)


async def test_search_and_chat_retrieval_hand_a_restricted_comment_only_to_its_group(world: dict[str, Any]) -> None:
    """Every search and chat path ends in one of these checks; bob and carol read the issue but are not in the group."""
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
        seen = {
            "search hit": vrid in await retrieval._keep_permitted_hits(
                [{"metadata": {"virtualRecordId": vrid}}], enumerated, user_key, org_id),
            "container search hit": vrid in (await graph.check_access(
                user_key, org_id, virtual_record_ids={vrid}, indexed_only=True)).records_by_vrid,
            "keyword hit": vrid in await graph.filter_accessible_virtual_record_ids([vrid], user_id, org_id),
            "fetched record": record_id in await accessible_node_ids(graph, {record_id}, user_id, org_id),
            "related record": record_id in await graph.filter_accessible_record_ids([record_id], user_id, org_id),
        }
        problems += [f"{name} {'cannot' if allowed else 'can'} get the comment as a {how}"
                     for how, got in seen.items() if got != allowed]
    assert not problems, "\n  ".join(problems)


async def test_a_reindex_does_not_let_the_next_sync_skip_a_comment_given_to_another_group(world: dict[str, Any]) -> None:
    """The comment moves from the product group (alice, bob) to the admins (alice, dave) and the issue is reindexed
    before the next sync: the reindex must not store the moved timestamp, or the sync would skip the issue and bob
    would keep the comment."""
    await _comment_world(world)
    await _write_comments(world, "plain task", "2024-06-16T10:00:00.000+0000", [_comment("501", group="g-product")])
    assert not await _comment_mismatches(world, {"comment_501": {"alice", "bob"}})

    moved = _comment("501", group=ADMINS_GROUP, updated="2024-06-17T10:00:00.000+0000")
    issue = _edited("plain task", "2024-06-17T10:00:00.000+0000", comment={"comments": [moved], "total": 1})
    await project_world._reindex(world, issue)
    await _write_comments(world, "plain task", "2024-06-17T10:00:00.000+0000", [moved])  # the next incremental sync

    assert not await _comment_mismatches(world, {"comment_501": {"alice"}})


async def test_a_reporter_outside_the_project_reads_a_comment_of_their_issue_their_group_names(
    world: dict[str, Any],
) -> None:
    """Browse held by the reporter: dave reads his own issue, so the comment restricted to his group too; bob and carol,
    outside the group, do not."""
    await _comment_world(world)
    world["connector"]._issue_security[PROJECT_ID].browse_holders = [{"type": "reporter"}]
    await _write_comments(world, "plain task", "2024-06-16T10:00:00.000+0000", [_comment("501", group=ADMINS_GROUP)],
                          again=True, reporter={"key": PEOPLE["dave"], "name": "dave"})
    record_id = (await _comment_ids(world))["comment_501"]
    graph, org_id = world["graph"], world["org_id"]
    for name, allowed in (("alice", True), ("dave", True), ("bob", False), ("carol", False)):
        user_id = world["user_ids"][name]
        checked = await graph.filter_accessible_record_ids([record_id], user_id, org_id)
        searched = await graph.filter_accessible_virtual_record_ids([f"vr-{record_id}"], user_id, org_id)
        assert (bool(checked), bool(searched)) == (allowed, allowed), f"{name} {'cannot' if allowed else 'can'} read comment_501"
