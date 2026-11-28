# pyright: ignore-file

"""
Jira Cloud on the simulator: every path of a reindex
====================================================

A reindex of one record asks Jira for it again (``POST .../reindex/record/{id}``): a record that changed at the source is
written, one that did not is queued for indexing as it is. What the reader sees is the stored record: its name, version
and project, and who reads it.

  TC-JCSIM-RIX-001  An issue that did not change is read again, and stays as it is
  TC-JCSIM-RIX-002  An issue edited at the source takes its new name and version, without a sync
  TC-JCSIM-RIX-003  An issue moved to another project stays in the one it was synced to (the sync files it)
  TC-JCSIM-RIX-004  An issue gone from the source is not removed by a reindex (the sync does it)
  TC-JCSIM-RIX-005  A security level set since the sync is applied by the reindex, and no one else gains access
  TC-JCSIM-RIX-006  A reindex while Jira fails (every status, a drop, a cut body) changes nothing and loses no access
  TC-JCSIM-RIX-007  A file: unchanged, removed, its issue gone; and a restricted comment's record is left as stored
  TC-JCSIM-RIX-008  An issue with a restricted comment: the edit is stored, the comment records wait for the sync
  TC-JCSIM-RIX-009  With indexing of issues and files off, a reindex indexes them (it is asked for)
"""

import pytest
from connectors.jira.cloud_sim_content import (
    ISSUE_URL,
    add_comment,
    comment_text,
    count_calls,
    doc,
    ids_by_external,
    new_issue,
    para,
    roles,
    wait_for,
)
from connectors.jira.cloud_sim_world import (  # noqa: F401 - fixtures of the jira_cloud_sim suite
    COMMENT_PREFIX,
    FILE_PREFIX,
    JiraCloudWorld,
    drop,
    fault,
    indexing_filter,
    jira_cloud_sim,
    jira_cloud_sim_people,
    jira_cloud_world,
    malformed,
    project_filter,
    succeeded,
)

GET_ISSUE = "GET /rest/api/3/issue/{"


def _roomy_project(w: JiraCloudWorld) -> str:
    for _tid, t in sorted(w.sim.expected()["ticket_index"].items(), key=lambda kv: int(kv[0])):
        if t["files"]:
            return t["key"].rsplit("-", 1)[0]
    raise AssertionError("no issue with a file")


async def _record(w: JiraCloudWorld, external_id: str) -> dict:
    record = await w.record_for(external_id)
    assert record is not None, f"no live record for {external_id}"
    return record


async def _first_indexed(w: JiraCloudWorld, rid: str) -> None:
    await w.wait_indexed(rid)


async def _reindex(w: JiraCloudWorld, rid: str, calls_before: int) -> None:
    """Reindex a record and wait until the connector has asked Jira for it."""
    w.client.reindex_record(rid)

    async def asked():
        return count_calls(w, GET_ISSUE) > calls_before

    await wait_for(asked, "the connector's read of the issue", timeout=90, interval=2)


@pytest.mark.integration
@pytest.mark.jira_cloud_sim
@pytest.mark.asyncio(loop_scope="session")
class TestJiraCloudSimReindexPaths:

    async def test_tc_jcsim_rix_001_unchanged(self, jira_cloud_world: JiraCloudWorld) -> None:
        w = jira_cloud_world
        assert succeeded(await w.start(filters=project_filter("in", [_roomy_project(w)])))
        records = await w.creator_records()
        ticket = next(r for r in records if r["externalRecordId"].isdigit())
        await _first_indexed(w, ticket["id"])
        before = await w.holdings()
        await _reindex(w, ticket["id"], count_calls(w, GET_ISSUE))
        after = await _record(w, ticket["externalRecordId"])
        assert (after["version"], after["recordName"]) == (ticket["version"], ticket["recordName"])
        await w.assert_holdings_kept(before, "after an unchanged issue was reindexed")

    async def test_tc_jcsim_rix_002_changed(self, jira_cloud_world: JiraCloudWorld) -> None:
        w = jira_cloud_world
        assert succeeded(await w.start(filters=project_filter("in", [_roomy_project(w)])))
        ticket = next(r for r in await w.creator_records() if r["externalRecordId"].isdigit())
        await _first_indexed(w, ticket["id"])
        key = w.sim.expected()["ticket_index"][ticket["externalRecordId"]]["key"]
        w.sim.rest("PUT", f"/issue/{key}", {"fields": {"summary": "Edited then reindexed"}})
        w.client.reindex_record(ticket["id"])

        async def renamed():
            rec = await _record(w, ticket["externalRecordId"])
            return rec if "Edited then reindexed" in str(rec["recordName"]) else None

        rec = await wait_for(renamed, "the reindexed issue's new name")
        assert rec["version"] == ticket["version"] + 1, (ticket["version"], rec["version"])
        assert rec["externalGroupId"] == ticket["externalGroupId"]
        assert succeeded(await w.sync())
        await w.assert_matches_source("after an edited issue was reindexed and a sync ran")

    async def test_tc_jcsim_rix_003_moved(self, jira_cloud_world: JiraCloudWorld) -> None:
        w = jira_cloud_world
        assert succeeded(await w.start())
        by_ext = {r["externalRecordId"]: r for r in await w.creator_records()}
        moved = [str(c["issue_id"]) for c in w.sim.advance(move=1)["changes"]["move"]]
        readable = [t for t in moved if t in by_ext]
        assert readable, "the moved issue is not one the creator reads"
        record = by_ext[readable[0]]
        await _reindex(w, record["id"], count_calls(w, GET_ISSUE))
        now = await _record(w, readable[0])
        assert (now["externalGroupId"], now["recordName"], now["version"]) == (
            record["externalGroupId"], record["recordName"], record["version"]), "a reindex re-filed a moved issue"
        assert succeeded(await w.sync())
        await w.assert_matches_source("after a moved issue was reindexed and a sync ran")

    async def test_tc_jcsim_rix_004_gone(self, jira_cloud_world: JiraCloudWorld) -> None:
        w = jira_cloud_world
        assert succeeded(await w.start())
        by_ext = {r["externalRecordId"]: r for r in await w.creator_records()}
        gone = next(str(c["issue_id"]) for c in w.sim.advance(delete=1)["changes"]["delete"] if str(c["issue_id"]) in by_ext)
        await _reindex(w, by_ext[gone]["id"], count_calls(w, GET_ISSUE))
        assert await w.record_for(gone) is not None, "a reindex removed the record of a deleted issue"
        assert succeeded(await w.sync())
        await w.assert_matches_source("after the record of a deleted issue was reindexed and a sync ran")

    async def test_tc_jcsim_rix_005_level_set_since_the_sync(self, jira_cloud_world: JiraCloudWorld) -> None:
        w = jira_cloud_world
        assert succeeded(await w.start())
        project, levels = w.leveled_project()
        issue = w.issue_keys(project)[1]
        tid = next(t for t, v in w.sim.expected()["ticket_index"].items() if v["key"] == issue)
        record = await _record(w, tid)
        await _first_indexed(w, record["id"])
        w.mutate({"op": "security", "issue": issue, "level": levels[0]["name"]})
        before = await w.holdings()
        await _reindex(w, record["id"], count_calls(w, GET_ISSUE))
        await w.assert_matches_source("after the reindex of an issue put under a level", settle=60)
        w.mutate({"op": "security", "issue": issue, "level": None})
        await _reindex(w, record["id"], count_calls(w, GET_ISSUE))
        await w.assert_matches_source("after the reindex of an issue taken out of its level", settle=60)
        assert before

    async def test_tc_jcsim_rix_006_while_jira_fails(self, jira_cloud_world: JiraCloudWorld) -> None:
        w = jira_cloud_world
        assert succeeded(await w.start(filters=project_filter("in", [_roomy_project(w)])))
        ticket = next(r for r in await w.creator_records() if r["externalRecordId"].isdigit())
        await _first_indexed(w, ticket["id"])
        key = w.sim.expected()["ticket_index"][ticket["externalRecordId"]]["key"]
        w.sim.rest("PUT", f"/issue/{key}", {"fields": {"summary": "Edited while Jira fails"}})
        before = await w.holdings()
        for name, rule in (("401", fault(ISSUE_URL, 401)), ("403", fault(ISSUE_URL, 403)), ("404", fault(ISSUE_URL, 404)),
                           ("410", fault(ISSUE_URL, 410)), ("429", fault(ISSUE_URL, 429)), ("500", fault(ISSUE_URL, 500)),
                           ("drop", drop(ISSUE_URL)), ("cut", malformed(ISSUE_URL))):
            w.faults(rule)
            w.client.reindex_record(ticket["id"])

            async def fired():
                return w.sim.call("GET", "/_sim/faults/log") or None

            await wait_for(fired, f"the {name} fault to fire", timeout=60, interval=2)
            w.clear_faults()
            rec = await _record(w, ticket["externalRecordId"])
            assert (rec["version"], rec["recordName"]) == (ticket["version"], ticket["recordName"]), name
        await w.assert_holdings_kept(before, "after reindexes while Jira failed")
        assert succeeded(await w.sync())
        await w.assert_matches_source("after the faults and a clean sync")

    async def test_tc_jcsim_rix_007_files_and_comment_records(self, jira_cloud_world: JiraCloudWorld) -> None:
        w = jira_cloud_world
        project = _roomy_project(w)
        assert succeeded(await w.start(filters=project_filter("in", [project])))
        role = next(iter(roles(w, project)))
        issue = new_issue(w, project, "Reindex of files", doc(para("body of the reindexed file issue")))
        key = issue["key"]
        keep = w.sim.upload(key, "kept.txt", b"kept")[0]
        drop_ = w.sim.upload(key, "removed.txt", b"removed")[0]
        c = add_comment(w, key, comment_text("restricted words to reindex"), {"type": "role", "value": role})
        assert succeeded(await w.sync())
        by_ext = ids_by_external(await w.records())
        file_rid, gone_rid = by_ext[f"{FILE_PREFIX}{keep['id']}"], by_ext[f"{FILE_PREFIX}{drop_['id']}"]
        comment_rid = by_ext[f"{COMMENT_PREFIX}{c['id']}"]
        await w.wait_indexed(file_rid)
        before = await w.holdings()
        calls = count_calls(w, GET_ISSUE)
        await _reindex(w, file_rid, calls)
        w.sim.rest("DELETE", f"/attachment/{drop_['id']}")
        await _reindex(w, gone_rid, count_calls(w, GET_ISSUE))
        w.client.reindex_record(comment_rid)
        w.sim.rest("DELETE", f"/issue/{key}")
        await _reindex(w, file_rid, count_calls(w, GET_ISSUE))
        live = {r["externalRecordId"] for r in await w.records()}
        assert {f"{FILE_PREFIX}{keep['id']}", f"{FILE_PREFIX}{drop_['id']}", f"{COMMENT_PREFIX}{c['id']}"} <= live, \
            "a reindex removed a record"
        assert before["records"] <= live | set()
        assert succeeded(await w.sync())
        await w.assert_matches_source("after files and a restricted comment were reindexed and the issue deleted")

    async def test_tc_jcsim_rix_008_issue_with_restricted_comment(self, jira_cloud_world: JiraCloudWorld) -> None:
        w = jira_cloud_world
        project = _roomy_project(w)
        assert succeeded(await w.start(filters=project_filter("in", [project])))
        role = next(iter(roles(w, project)))
        issue = new_issue(w, project, "Issue with a restricted comment", doc(para("a body")))
        key, tid = issue["key"], str(issue["id"])
        add_comment(w, key, comment_text("restricted again"), {"type": "role", "value": role})
        assert succeeded(await w.sync())
        record = await _record(w, tid)
        await w.wait_indexed(record["id"])
        w.sim.rest("PUT", f"/issue/{key}", {"fields": {"summary": "Renamed with a restricted comment"}})
        w.client.reindex_record(record["id"])

        async def renamed():
            rec = await _record(w, tid)
            return rec if "Renamed with a restricted comment" in str(rec["recordName"]) else None

        await wait_for(renamed, "the new name of an issue with a restricted comment")
        add_comment(w, key, comment_text("restricted a second time"), {"type": "role", "value": role})
        assert succeeded(await w.sync())
        await w.assert_matches_source("after the sync that reads the comments a reindex leaves to it")

    async def test_tc_jcsim_rix_009_indexing_filters(self, jira_cloud_world: JiraCloudWorld) -> None:
        w = jira_cloud_world
        project = _roomy_project(w)
        filters = {**project_filter("in", [project]), **indexing_filter("issues", False),
                   **{"indexing": {"values": {**indexing_filter("issues", False)["indexing"]["values"],
                                              **indexing_filter("issue_attachments", False)["indexing"]["values"]}}}}
        assert succeeded(await w.start(filters=filters))
        records = await w.records()
        ticket = next(r for r in records if r["externalRecordId"].isdigit())
        file = next(r for r in records if r["externalRecordId"].startswith(FILE_PREFIX))
        assert str(ticket["indexingStatus"]).upper() == "AUTO_INDEX_OFF", ticket["indexingStatus"]
        assert str(file["indexingStatus"]).upper() == "AUTO_INDEX_OFF", file["indexingStatus"]
        # A reindex is asked for by a person: it indexes the record whatever the filters say (the code says so where the
        # file record is built), so an unchanged file and an edited issue both leave the off state.
        key = w.sim.expected()["ticket_index"][ticket["externalRecordId"]]["key"]
        w.sim.rest("PUT", f"/issue/{key}", {"fields": {"summary": "Edited while indexing is off"}})
        w.client.reindex_record(ticket["id"])
        w.client.reindex_record(file["id"])

        async def indexed():
            now = {r["externalRecordId"]: r for r in await w.records()}
            both = (now[ticket["externalRecordId"]], now[file["externalRecordId"]])
            return all(str(r["indexingStatus"]).upper() != "AUTO_INDEX_OFF" for r in both) or None

        await wait_for(indexed, "the reindexed records to be indexed", timeout=150)
        rec = await _record(w, ticket["externalRecordId"])
        assert "Edited while indexing is off" in str(rec["recordName"])
