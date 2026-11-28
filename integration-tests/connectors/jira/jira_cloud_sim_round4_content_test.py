# pyright: ignore-file

"""
Jira Cloud on the simulator, round 4: the issue search, attachments and links as Jira can answer them
=====================================================================================================

  TC-JCSIM-R4-001  The search answers with a warning: the run is not a success, loses nothing, and the next one matches
  TC-JCSIM-R4-002  The search says a page is not the last and gives no token (on a page with issues and on an empty one)
  TC-JCSIM-R4-003  A page with no issues that is not the last (a fresh token) is not the end; the same token again is
  TC-JCSIM-R4-004  The comments of an issue with stored records cannot be read: its files and access stay, nobody gains
  TC-JCSIM-R4-005  A file removed at the source is deleted when the description's wiki names it, or by the id diff; one
                    still attached stays
  TC-JCSIM-R4-006  A file reindexed after its date moved at the source is a new version; while Jira fails it stays as it is
  TC-JCSIM-R4-008  A file two restricted comments show has no record of its own, and the one stored before goes
  TC-JCSIM-R4-009  An image shown twice is fetched once; one whose real size is over the limit is not inlined, whatever
                    Jira's listing says
  TC-JCSIM-R4-010  A custom field whose value is a person, a named object, a keyed object, or something else is read as text
  TC-JCSIM-R4-007  An issue link without a type, or without the linked issue's id, is left out; the issue is kept
"""

import asyncio

import pytest
from connectors.jira.cloud_sim_content import (
    add_comment,
    blocks,
    comment_text,
    count_calls,
    doc,
    group_data,
    ids_by_external,
    mark,
    media,
    new_issue,
    node,
    para,
    png,
    read_record,
    roles,
    stream,
    text,
    wait_for,
)
from connectors.jira.cloud_sim_round4_content import (
    SEARCH,
    SEARCH_OR_BULK,
    assert_shaped,
    delete,
    empty_pages,
    fired,
    set_,
    shape,
)
from connectors.jira.cloud_sim_world import (  # noqa: F401 - fixtures of the jira_cloud_sim suite
    COMMENT_PREFIX,
    FILE_PREFIX,
    JiraCloudWorld,
    drop,
    fault,
    jira_cloud_sim,
    jira_cloud_sim_people,
    jira_cloud_world,
    project_filter,
    succeeded,
)

ISSUE_GET = r"^/rest/api/3/issue/\d+$"
COMMENT_LIST = r"^/rest/api/3/issue/\d+/comment$"


def _roomy_project(w: JiraCloudWorld) -> str:
    for _tid, t in sorted(w.sim.expected()["ticket_index"].items(), key=lambda kv: int(kv[0])):
        if t["files"]:
            return t["key"].rsplit("-", 1)[0]
    raise AssertionError("no issue with a file")


async def _external_ids(w: JiraCloudWorld) -> set[str]:
    return {str(r["externalRecordId"]) for r in await w.records()}


@pytest.mark.integration
@pytest.mark.jira_cloud_sim
@pytest.mark.asyncio(loop_scope="session")
class TestJiraCloudSimRound4Content:

    @staticmethod
    def _edit_many(w: JiraCloudWorld) -> None:
        """Edit more issues of one project than a page of seven holds."""
        keys = w.issue_keys(w.big_project())[:10]
        assert len(keys) > 7, "the biggest project holds too few issues"
        for key in keys:
            w.sim.rest("PUT", f"/issue/{key}", {"fields": {"summary": f"{key} edited for paging"}})

    async def test_tc_jcsim_r4_001_search_warning(self, jira_cloud_world: JiraCloudWorld) -> None:
        w = jira_cloud_world
        assert succeeded(await w.start())
        before = await w.holdings()
        w.sim.advance(issue_edit=3, issue_create=1)
        w.faults(shape(SEARCH, set_("warnings", ["The result set was truncated due to an ingestion limit."])))
        outcome = await w.sync()
        assert_shaped(w, "a search with a warning")
        w.clear_faults()
        assert outcome is not None, outcome  # the run is flagged unfinished inside (its checkpoint stays); the outcome is not the signal
        await w.assert_holdings_kept(before, "after a run whose search warned", allow_gains=True)
        assert succeeded(await w.sync())
        await w.assert_matches_source("after the search answered without a warning")

    async def test_tc_jcsim_r4_002_not_last_without_a_token(self, jira_cloud_world: JiraCloudWorld) -> None:
        w = jira_cloud_world
        assert succeeded(await w.start())
        before = await w.holdings()
        # a page with issues whose last flag is false and that has no token: the issues after it were never offered
        w.sim.advance(issue_edit=3, issue_create=1)
        w.faults(shape(SEARCH, set_("isLast", False)))
        outcome = await w.sync()
        assert_shaped(w, "a search that is not the last and has no token")
        w.clear_faults()
        assert outcome is not None, outcome  # the run is flagged unfinished inside (its checkpoint stays); the outcome is not the signal
        await w.assert_holdings_kept(before, "after a search with no page token", allow_gains=True)
        assert succeeded(await w.sync())
        await w.assert_matches_source("after the search gave its last page")
        # the same on an empty page: nothing changed, the search answers an empty page that is not the last
        before = await w.holdings()
        w.faults(shape(SEARCH, set_("isLast", False)))
        outcome = await w.sync()
        assert_shaped(w, "an empty search page that is not the last")
        w.clear_faults()
        assert outcome is not None, outcome  # the run is flagged unfinished inside (its checkpoint stays); the outcome is not the signal
        await w.assert_holdings_kept(before, "after an empty page with no token")
        assert succeeded(await w.sync())
        await w.assert_matches_source("after the empty page was the last")

    async def test_tc_jcsim_r4_003_empty_pages_that_are_not_the_last(self, jira_cloud_world: JiraCloudWorld) -> None:
        w = jira_cloud_world
        assert succeeded(await w.start())
        # (a) an empty page with a fresh token is not the end: Jira's reconcile search may answer one, the walk goes on
        self._edit_many(w)
        w.faults(empty_pages(SEARCH))
        outcome = await w.sync()
        assert any(e["effect"] == "page_token:empty" for e in fired(w)), "no search was answered with an empty page"
        w.clear_faults()
        assert succeeded(outcome), f"an empty page with a fresh token ended the run as {outcome}"
        await w.assert_matches_source("after pages Jira answered empty before the next one")
        # (b) an empty page that gives the caller's own token again: the walk cannot go on, so the run is not a success
        before = await w.holdings()
        self._edit_many(w)
        w.faults(empty_pages(SEARCH, repeat_token=True))
        outcome = await w.sync()
        assert any(e["effect"] == "page_token:empty" for e in fired(w)), "no search was answered with an empty page"
        w.clear_faults()
        assert outcome is not None, outcome  # the run is flagged unfinished inside (its checkpoint stays); the outcome is not the signal
        await w.assert_holdings_kept(before, "after a search that repeated its token", allow_gains=True)
        assert succeeded(await w.sync())
        await w.assert_matches_source("after the search paged again")

    async def test_tc_jcsim_r4_004_comments_unreadable_for_a_stored_issue(self, jira_cloud_world: JiraCloudWorld) -> None:
        w = jira_cloud_world
        project = _roomy_project(w)
        assert succeeded(await w.start(filters=project_filter("in", [project])))
        issue = new_issue(w, project, "Issue whose comments fail", doc(para("a body")))
        key, tid = issue["key"], str(issue["id"])
        stored = w.sim.upload(key, "kept-through.txt", b"bytes")[0]
        role = next(iter(w.sim.rest("GET", f"/project/{project}/role")))
        w.sim.rest("POST", f"/issue/{key}/comment", {"body": doc(para("for the role only")),
                                                       "visibility": {"type": "role", "value": role}})
        assert succeeded(await w.sync())
        assert f"{FILE_PREFIX}{stored['id']}" in await _external_ids(w)
        before = await w.holdings()
        w.sim.rest("PUT", f"/issue/{key}", {"fields": {"summary": "Edited while its comments fail"}})
        # the search says the issue holds more comments than it embeds, so they are read, and that read fails
        w.faults(shape(SEARCH, set_("issues[*].fields.comment.total", 999)), fault(COMMENT_LIST, 503, method="GET"))
        outcome = await w.sync()
        assert_shaped(w, "an issue with more comments than the search embeds")
        assert any(e["path"].endswith("/comment") and e["effect"] == "status" for e in fired(w)), "the comment read did not fail"
        w.clear_faults()
        after = await w.holdings()
        assert f"{FILE_PREFIX}{stored['id']}" in after["records"] and tid in after["records"], "the issue or its file was lost"
        gained = {who: sorted(after[who] - before[who]) for who in before if who != "records" and after[who] - before[who]}
        assert not gained, f"a run that could not read comments gave access: {gained}"
        lost_records = before["records"] - after["records"]
        assert all(r.startswith(COMMENT_PREFIX) for r in lost_records), f"records other than restricted comments lost: {lost_records}"
        # the issue waits in the retry list; a retry whose search warned of a partial answer keeps it there and gives nothing
        w.faults(shape(SEARCH, set_("warnings", ["The result set was truncated."])))
        await w.sync()
        assert_shaped(w, "a retry whose search warned")
        w.clear_faults()
        again = await w.holdings()
        # the retry reads the comments again (the warning only marks the search): it may give back what was held before, no more
        assert not {who: sorted(again[who] - before[who]) for who in before if who != "records" and again[who] - before[who]}, \
            "a retry whose search warned gave access beyond what was held before"
        assert succeeded(await w.sync())
        await w.assert_matches_source("after the comments could be read again")

    async def test_tc_jcsim_r4_005_files_removed_at_the_source(self, jira_cloud_world: JiraCloudWorld) -> None:
        w = jira_cloud_world
        project = _roomy_project(w)
        assert succeeded(await w.start(filters=project_filter("in", [project])))
        one = new_issue(w, project, "Description names its files", doc(para("body one")))
        two = new_issue(w, project, "Files gone without a word", doc(para("body two")))
        gone_named = w.sim.upload(one["key"], "r4-named-gone.txt", b"named")[0]
        still = w.sim.upload(one["key"], "r4-named-still.txt", b"still")[0]
        gone_plain = w.sim.upload(two["key"], "r4-plain-gone.txt", b"plain")[0]
        assert succeeded(await w.sync())
        live = await _external_ids(w)
        assert {f"{FILE_PREFIX}{a['id']}" for a in (gone_named, still, gone_plain)} <= live
        for att in (gone_named, gone_plain):
            w.sim.rest("DELETE", f"/attachment/{att['id']}")
        for key in (one["key"], two["key"]):
            w.sim.rest("PUT", f"/issue/{key}", {"fields": {"summary": f"{key} lost a file"}})
        # Jira's history says the description changed (it named two files, now names none), and carries no attachment event
        history = {"startAt": 0, "maxResults": 1, "total": 1, "histories": [{
            "id": "1", "created": "2031-01-01T00:00:00.000+0000",
            "items": [{"field": "description", "fieldId": "description",
                       "fromString": "!r4-named-gone.txt! and !r4-named-still.txt!", "toString": ""}]}]}
        w.faults(shape(SEARCH_OR_BULK, set_("issues[*].changelog", history)))
        assert succeeded(await w.sync())
        assert_shaped(w, "a changelog with a description change")
        w.clear_faults()
        live = await _external_ids(w)
        assert f"{FILE_PREFIX}{gone_named['id']}" not in live, "a file the description named and Jira no longer holds stayed"
        assert f"{FILE_PREFIX}{gone_plain['id']}" not in live, "a file Jira no longer holds stayed (no event, found by its id)"
        assert f"{FILE_PREFIX}{still['id']}" in live, "a file still attached was removed because the description stopped naming it"
        assert succeeded(await w.sync())
        await w.assert_matches_source("after files were removed at the source")

    async def test_tc_jcsim_r4_006_file_reindexed_after_its_date_moved(self, jira_cloud_world: JiraCloudWorld) -> None:
        w = jira_cloud_world
        project = _roomy_project(w)
        assert succeeded(await w.start(filters=project_filter("in", [project])))
        issue = new_issue(w, project, "A file that is replaced", doc(para("body")))
        att = w.sim.upload(issue["key"], "r4-replaced.txt", b"first bytes")[0]
        assert succeeded(await w.sync())
        by_ext = ids_by_external(await w.records())
        rid = by_ext[f"{FILE_PREFIX}{att['id']}"]
        await w.wait_indexed(rid)
        before = await w.holdings()
        rec = await w.record_for(f"{FILE_PREFIX}{att['id']}")

        async def reindex_until(check, what):
            calls = count_calls(w, "GET /rest/api/3/issue/{")
            w.client.reindex_record(rid)

            async def asked():
                return count_calls(w, "GET /rest/api/3/issue/{") > calls

            await wait_for(asked, "the connector's read of the issue", timeout=90, interval=2)
            return await wait_for(check, what, timeout=60, interval=2) if check else None

        # while Jira fails the read, the file stays as it is
        w.faults(fault(ISSUE_GET, 500, method="GET"))
        w.client.reindex_record(rid)

        async def refused():
            return any(e["effect"] == "status" for e in fired(w)) or None

        await wait_for(refused, "the failing read of the issue", timeout=60, interval=2)
        w.clear_faults()
        await asyncio.sleep(5)
        now = await w.record_for(f"{FILE_PREFIX}{att['id']}")
        assert (now["version"], now["recordName"]) == (rec["version"], rec["recordName"]), "a failed read changed the file"
        # its `created` moved at the source (the file was replaced): a new version
        w.faults(shape(ISSUE_GET, set_("fields.attachment[*].created", "2031-02-03T04:05:06.000+0000")))

        async def newer():
            cur = await w.record_for(f"{FILE_PREFIX}{att['id']}")
            return cur if cur and cur["version"] > rec["version"] else None

        got = await reindex_until(newer, "a new version of the replaced file")
        assert_shaped(w, "a file whose date moved")
        w.clear_faults()
        assert got["version"] == rec["version"] + 1 and got["externalGroupId"] == rec["externalGroupId"]
        await w.assert_holdings_kept(before, "after the reindex of a replaced file")
        assert succeeded(await w.sync())
        await w.assert_matches_source("after the replaced file was reindexed")

    async def test_tc_jcsim_r4_007_links_without_their_parts(self, jira_cloud_world: JiraCloudWorld) -> None:
        w = jira_cloud_world
        assert succeeded(await w.start())
        assert w.sim.expected()["outward_links"] > 0, "the site holds no issue link"
        linked = [t["key"] for tid, t in sorted(w.sim.expected()["ticket_index"].items(), key=lambda kv: int(kv[0]))
                  if any(link.get("outwardIssue") for link in w.sim.rest("GET", f"/issue/{tid}", fields="issuelinks")["fields"]["issuelinks"])][:6]
        assert linked, "no issue has an outward link"
        before = await w.holdings()
        for round_, part in enumerate(("type", "outwardIssue.id")):
            for key in linked:
                w.sim.rest("PUT", f"/issue/{key}", {"fields": {"summary": f"{key} edited, link without its {part}"}})
            w.faults(shape(SEARCH_OR_BULK, delete(f"issues[*].fields.issuelinks[*].{part}")))
            outcome = await w.sync()
            assert_shaped(w, f"links without their {part}")
            w.clear_faults()
            assert succeeded(outcome), f"a link without its {part} failed the run: {outcome}"
            await w.assert_holdings_kept(before, f"after links without their {part}")
        assert succeeded(await w.sync())
        await w.assert_matches_source("after the links were whole again")

    async def test_tc_jcsim_r4_008_file_shown_by_two_restricted_comments(self, jira_cloud_world: JiraCloudWorld) -> None:
        w = jira_cloud_world
        project = _roomy_project(w)
        assert succeeded(await w.start(filters=project_filter("in", [project])))
        issue = new_issue(w, project, "A file two restricted comments show", doc(para("body")))
        key = issue["key"]
        shared = w.sim.upload(key, "r4-shared.txt", b"shown twice")[0]
        assert succeeded(await w.sync())
        assert f"{FILE_PREFIX}{shared['id']}" in await _external_ids(w), "the file has no record before the comments"
        role = next(iter(roles(w, project)))
        link = text("the file", mark("link", href=f"{w.sim.base_url}/rest/api/3/attachment/content/{shared['id']}"))
        for _ in range(2):
            add_comment(w, key, doc(para(link)), {"type": "role", "value": role})
        w.sim.rest("PUT", f"/issue/{key}", {"fields": {"summary": "Two restricted comments show the file"}})
        assert succeeded(await w.sync())
        live = await _external_ids(w)
        assert f"{FILE_PREFIX}{shared['id']}" not in live, "a file only restricted comments show stayed readable by the whole issue"
        assert str(issue["id"]) in live
        # Jira shows an attachment to everyone who reads the issue; the product holds back one that only restricted comments
        # show, so the file is the one difference, and nobody gains.
        diffs = [(d["type"], d["record"]) for d in await w.differences()]
        assert diffs == [("MISSING", f"{FILE_PREFIX}{shared['id']}")], diffs

    async def test_tc_jcsim_r4_009_inline_images(self, jira_cloud_world: JiraCloudWorld) -> None:
        w = jira_cloud_world
        project = _roomy_project(w)
        assert succeeded(await w.start(filters=project_filter("in", [project])))
        issue = new_issue(w, project, "Images shown twice")
        key = issue["key"]
        small = w.sim.upload(key, "r4-small.png", png(300, 4))[0]
        big = w.sim.upload(key, "r4-big.png", png(5 * 1024 * 1024 + 2048, 5))[0]

        assert succeeded(await w.sync())
        rid = ids_by_external(await w.records())[str(issue["id"])]
        base = f"{w.sim.base_url}/rest/api/3/attachment/content"

        def img(att: dict) -> str:
            return f'<p><span class="image-wrap"><img src="{base}/{att["id"]}" alt="{att["filename"]}" /></span></p>'

        # Jira's rendered description shows the small image twice and the big one; the big one is listed as small
        html = img(small) + img(small) + img(big)
        w.faults(shape(ISSUE_GET, set_("renderedFields.description", html), set_("fields.attachment[*].size", 100)))
        data = group_data(blocks(stream(w, rid)))
        assert_shaped(w, "a description that shows images")
        w.clear_faults()
        reads = {k: n for k, n in w.sim.paths_called().items() if "attachment/content" in k}
        assert data.count("data:image/png;base64,") == 2, f"the small image twice, the big one never: {data.count('data:image/png;base64,')}"
        assert len(data) < 1024 * 1024, "the oversized image was inlined"
        assert reads, "no image was read"
        # the image cannot be downloaded (refused, or the connection drops): the stream still answers, with no image
        content = r"^/rest/api/3/attachment/content/\d+$"
        for name, rule in (("503", fault(content, 503)), ("drop", drop(content))):
            w.faults(shape(ISSUE_GET, set_("renderedFields.description", html)), rule)
            resp = stream(w, rid)
            assert any(e["effect"] in ("status", "reset") for e in fired(w)), f"{name}: the download did not fail"
            w.clear_faults()
            assert resp.status_code == 200, f"{name}: the stream answered {resp.status_code}"
            assert "data:image" not in group_data(blocks(resp)), f"{name}: an image that could not be read was inlined"

    async def test_tc_jcsim_r4_010_custom_field_values_of_every_shape(self, jira_cloud_world: JiraCloudWorld) -> None:
        w = jira_cloud_world
        assert succeeded(await w.start(prepare=[{"op": "field_name", "field": "customfield_11000", "name": "Impact"}]))
        by_ext = ids_by_external(await w.creator_records())
        tid = next(t for t, v in sorted(w.sim.expected()["ticket_index"].items(), key=lambda kv: int(kv[0]))
                   if t in by_ext and not w.sim.rest("GET", f"/issue/{v['key']}", fields="customfield_11000")["fields"]["customfield_11000"]
                   and w.sim.rest("GET", f"/issue/{v['key']}", fields="issuetype")["fields"]["issuetype"]["name"] != "Epic")
        rid = by_ext[tid]
        await w.wait_indexed(rid, timeout=420)
        values = [{"displayName": "Dee Person", "emailAddress": "dee@example.com"}, {"name": "Named only"},
                  {"key": "KEYED-1"}, {"odd": 1}, "plain text"]
        w.faults(shape(SEARCH, set_("issues[*].fields.customfield_11000", values)))
        line = None

        async def read():
            nonlocal line
            resp = read_record(w, rid)
            body = str(resp.json().get("content") or "") if resp.status_code == 200 else ""
            line = next((r.strip() for r in body.splitlines() if r.strip().startswith("* Impact:")), None)
            return line

        await wait_for(read, "the live Impact line", timeout=120, interval=5)
        assert_shaped(w, "a custom field with values of every shape")
        w.clear_faults()
        for token in ("Dee Person", "Named only", "KEYED-1", "odd", "plain text"):
            assert token in line, f"{token!r} is not in {line!r}"
