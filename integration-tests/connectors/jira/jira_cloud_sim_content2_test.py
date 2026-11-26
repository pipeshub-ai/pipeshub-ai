# pyright: ignore-file

"""
Jira Cloud on the simulator: every path of what a record streams
================================================================

  TC-JCSIM-STRM-001  Every readable issue streams its summary, its public comments, its files and its inline images,
                     and never a comment restricted to some of its readers
  TC-JCSIM-STRM-002  Bodies with every ADF node and mark, a macro-looking text, and an absent, empty or wiki body
  TC-JCSIM-STRM-003  Files: empty, large, same-named and odd-named ones stream byte for byte; one removed or replaced
                     after the sync, and a failing download, are refused, never half served
  TC-JCSIM-STRM-004  Comments past the embedded ones, restricted ones, a file only a restricted comment shows, a comment
                     edited, restricted or deleted after the sync
  TC-JCSIM-STRM-005  An issue moved to another project or gone since the sync is not served
  TC-JCSIM-STRM-006  Every read of an issue's stream fails with 401, 403, 404, 429, 5xx, a drop or a cut body: the stream
                     fails, or serves what is safe
  TC-JCSIM-STRM-007  Every read of a restricted comment's stream fails the same way
  TC-JCSIM-STRM-008  (xfail) a cut answer to the description read; STRM-009: not used; STRM-010 (xfail) a group-restricted comment
"""

import hashlib
import re

import pytest
import requests
from connectors.jira.cloud_sim_content import (
    COMMENT_URL,
    COMMENTS_URL,
    CONTENT_URL,
    ISSUE_URL,
    add_comment,
    blocks,
    comment_groups,
    comment_text,
    children,
    doc,
    every_node_doc,
    EVERY_NODE_WORDS,
    group_data,
    ids_by_external,
    jira_get,
    new_issue,
    node,
    para,
    png,
    roles,
    stream,
    text,
    wait_for,
    media,
    group_names,
    mark,
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
    malformed,
    project_filter,
    succeeded,
)


def _alpha(adf_or_text: object) -> str:
    """The first word of 5 letters or more of a body: what a stream of it must hold."""
    from connectors.jira.cloud_sim_content import words_of

    raw = " ".join(words_of(adf_or_text)) if isinstance(adf_or_text, dict) else str(adf_or_text)
    found = re.findall(r"[A-Za-z]{5,}", raw)
    return found[0] if found else ""


def _snippet(adf: object) -> str:
    """The start of a comment's first text, as the rendered HTML escapes it."""
    from connectors.jira.cloud_sim_content import words_of
    words = words_of(adf)
    found = re.match(r"[A-Za-z][A-Za-z ]{7,40}", words[0]) if words else None
    return found.group(0).rstrip() if found else ""


def _roomy_project(w: JiraCloudWorld) -> str:
    """The project of an issue with a file and an inline image: where the cases below run."""
    for tid, t in sorted(w.sim.expected()["ticket_index"].items(), key=lambda kv: int(kv[0])):
        if t["inline"] and t["files"]:
            return t["key"].rsplit("-", 1)[0]
    raise AssertionError("no issue with both a file and an inline image")


def _ok(w: JiraCloudWorld, rid: str, what: str, resp: requests.Response | None = None) -> dict:
    resp = resp or stream(w, rid)
    assert resp.status_code == 200, f"{what}: the stream answered {resp.status_code}: {resp.text[:200]}"
    return blocks(resp)


def _fetch(w: JiraCloudWorld, rid: str) -> requests.Response | None:
    try:
        return stream(w, rid)
    except requests.RequestException:
        return None


SYSTEM_GROUPS = "atlassian-addons"  # Atlassian's groups of installed apps, which the connector leaves out


@pytest.mark.integration
@pytest.mark.jira_cloud_sim
@pytest.mark.asyncio(loop_scope="session")
class TestJiraCloudSimStreams:

    async def test_tc_jcsim_strm_001_every_issue(self, jira_cloud_world: JiraCloudWorld) -> None:
        w = jira_cloud_world
        assert succeeded(await w.start())
        records = await w.creator_records()
        by_ext = ids_by_external(records)
        index = w.sim.expected()["ticket_index"]
        problems: list[str] = []
        streamed = inlined = 0
        for tid, t in sorted(index.items(), key=lambda kv: int(kv[0])):
            if tid not in by_ext:
                continue
            resp = stream(w, by_ext[tid])
            if resp.status_code != 200:
                problems.append(f"{t['key']}: stream answered {resp.status_code}")
                continue
            streamed += 1
            container = blocks(resp)
            data = group_data(container)
            summary = w.sim.rest("GET", f"/issue/{tid}", fields="summary")["fields"]["summary"]
            if summary not in data and summary.replace("&", "&amp;") not in data:
                problems.append(f"{t['key']}: the summary is not in the stream")
            seen = w.sim.rest("GET", f"/issue/{tid}/comment", maxResults=1000)["comments"]
            for c in seen:
                snippet = _snippet(c["body"])
                if not snippet:
                    continue
                if c.get("visibility"):
                    if snippet in data:
                        problems.append(f"{t['key']}: restricted comment {c['id']} is in the issue's stream")
                elif snippet not in data:
                    problems.append(f"{t['key']}: public comment {c['id']} ({snippet!r}) is not in the stream")
            if t["inline"]:
                inlined += 1
                if "data:image" not in data:
                    problems.append(f"{t['key']}: its inline image is not in the stream")
            wanted = {by_ext[f"{FILE_PREFIX}{f['id']}"] for f in t["files"] if f"{FILE_PREFIX}{f['id']}" in by_ext}
            got = children(container)
            only_images = {by_ext[f"{FILE_PREFIX}{f['id']}"] for f in t["files"] if f"{FILE_PREFIX}{f['id']}" in by_ext
                           and f["name"].lower().rsplit(".", 1)[-1] in ("png", "jpg", "jpeg", "gif")}
            if not any(c.get("visibility") for c in seen) and (wanted - got) - only_images:
                problems.append(f"{t['key']}: files named {sorted(got)} but its file records are {sorted(wanted)} "
                                f"(files {t['files']}, inline {t['inline']})")
            if got - wanted:
                problems.append(f"{t['key']}: names records that are not its files: {sorted(got - wanted)}")
        assert streamed >= 40 and inlined, f"only {streamed} issues streamed, {inlined} with inline images"
        assert not problems, "\n".join(problems[:30])

    async def test_tc_jcsim_strm_002_bodies(self, jira_cloud_world: JiraCloudWorld) -> None:
        w = jira_cloud_world
        project = _roomy_project(w)
        assert succeeded(await w.start(filters=project_filter("in", [project])))
        rich = new_issue(w, project, "Rich bodies", every_node_doc("d"))
        add_comment(w, rich["key"], every_node_doc("c"))
        add_comment(w, rich["key"], comment_text("a plain comment keeps its HTML"))
        wiki = new_issue(w, project, "Wiki body", doc(para("wiki {macro} text here")))
        nothing = new_issue(w, project, "No description at all")
        braces = new_issue(w, project, "Only braces", doc(para("{only} {braces}")))
        assert succeeded(await w.sync())
        by_ext = ids_by_external(await w.records())

        data = group_data(blocks(stream(w, by_ext[str(rich["id"])])))
        missing = [x for x in EVERY_NODE_WORDS if x not in data]
        assert not missing, f"the comment's markdown lacks {missing}"
        assert "a plain comment keeps its HTML" in data
        desc = next(g for g in blocks(stream(w, by_ext[str(rich["id"])]))["block_groups"] if g["index"] == 0)
        assert desc["format"] == "markdown", "a description whose HTML lost text is indexed as markdown"
        for word in ("macro {d} kept", "Heading three words", "first bullet item", "plain code body", "header cell"):
            assert word in desc["data"], f"{word!r} missing from the description's markdown"

        # The raw description as other shapes: a wiki string, an empty string, a number, a document with no text.
        shapes = {"wiki": "h2. Wiki {macro} heading\n* item", "empty": "", "number": 5,
                  "empty-doc": {"type": "doc", "version": 1, "content": []}}
        for name, value in shapes.items():
            w.faults({"match": {"path_regex": ISSUE_URL}, "effect": {"type": "shape", "ops": [
                {"path": "fields.description", "value": value}]}})
            resp = stream(w, by_ext[str(wiki["id"])])
            w.clear_faults()
            assert resp.status_code == 200, f"{name}: {resp.status_code}"
            first = next(g for g in blocks(resp)["block_groups"] if g["index"] == 0)
            if name == "wiki":
                assert first["format"] == "markdown" and "h2. Wiki {macro} heading" in first["data"]
            else:
                assert first["format"] == "html" and "Unknown macro" in first["data"], f"{name}: {first['format']}"

        only = next(g for g in blocks(stream(w, by_ext[str(braces["id"])]))["block_groups"] if g["index"] == 0)
        assert "{only}" in only["data"] and only["format"] == "markdown"
        empty = next(g for g in blocks(stream(w, by_ext[str(nothing["id"])]))["block_groups"] if g["index"] == 0)
        assert "No description at all" in empty["data"] and empty["format"] == "html"
        await w.assert_matches_source("after issues with every kind of body")

    async def test_tc_jcsim_strm_003_files(self, jira_cloud_world: JiraCloudWorld) -> None:
        w = jira_cloud_world
        project = _roomy_project(w)
        assert succeeded(await w.start(filters=project_filter("in", [project])))
        issue = new_issue(w, project, "Files of every size")
        key = issue["key"]
        big = png(6 * 1024 * 1024 + 5)
        files = {"empty.txt": b"", "tiny.txt": b"x", "big.png": big, "same.txt": b"first of the same name",
                 "small-image.png": png(300, 9), "spaced name (1).txt": b"spaces and brackets",
                 "ünï–cödé.txt": "unicode name".encode(), "chunk.bin": bytes(range(256)) * 1100}
        made: dict[str, dict] = {}
        for name, data in files.items():
            made[name] = w.sim.upload(key, name, data)[0]
        twin = w.sim.upload(key, "same.txt", b"second of the same name")[0]
        # Images a public comment shows, by a wiki reference in its text and by the link to the file's id: not file records.
        by_name = w.sim.upload(key, "wiki-ref.png", png(200, 1))[0]
        by_id = w.sim.upload(key, "id-ref.png", png(200, 2))[0]
        add_comment(w, key, doc(para("see !wiki-ref.png|thumbnail! and !|broken!"),
                                para(text("the other", mark("link", href=f"{w.sim.base_url}/rest/api/3/attachment/content/{by_id['id']}")))))
        assert succeeded(await w.sync())
        by_ext = ids_by_external(await w.records())
        assert f"{FILE_PREFIX}{by_name['id']}" not in by_ext and f"{FILE_PREFIX}{by_id['id']}" not in by_ext, \
            "an image a public comment shows became a file record"
        for name, data in [*files.items(), ("same.txt#2", b"second of the same name")]:
            att = twin if name.endswith("#2") else made[name]
            rid = by_ext.get(f"{FILE_PREFIX}{att['id']}")
            assert rid, f"no record for {name}"
            resp = stream(w, rid)
            assert resp.status_code == 200, f"{name}: {resp.status_code}"
            assert hashlib.sha256(resp.content).hexdigest() == hashlib.sha256(data).hexdigest(), f"{name}: bytes differ"
            assert "attachment" in resp.headers.get("content-disposition", "").lower()

        # Removed at the source after the sync: a clean 404, no bytes.
        gone = made["tiny.txt"]
        w.sim.rest("DELETE", f"/attachment/{gone['id']}")
        resp = stream(w, by_ext[f"{FILE_PREFIX}{gone['id']}"])
        assert resp.status_code == 404, f"a removed file streamed with {resp.status_code}"

        # Replaced (removed, and another file with the name attached): the record's own id is gone.
        w.sim.rest("DELETE", f"/attachment/{made['same.txt']['id']}")
        fresh = w.sim.upload(key, "same.txt", b"a third, replacing one")[0]
        assert stream(w, by_ext[f"{FILE_PREFIX}{made['same.txt']['id']}"]).status_code == 404
        assert fresh["id"] != made["same.txt"]["id"]

        # A failing download is refused or fails the stream; it is never a 200 holding other bytes.
        target = made["chunk.bin"]["id"]
        want = files["chunk.bin"]
        rid = by_ext[f"{FILE_PREFIX}{target}"]
        expected_status = {401: 409, 403: 403, 404: 404, 500: 502, 503: 502}
        for code, status in expected_status.items():
            w.faults(fault(CONTENT_URL, code))
            resp = stream(w, rid)
            w.clear_faults()
            assert resp.status_code == status, f"download answered {code}: the stream says {resp.status_code}"
        for name, rule in (("drop", drop(CONTENT_URL)),):
            w.faults(rule)
            try:
                resp = stream(w, rid)
                body, status = resp.content, resp.status_code
            except requests.RequestException:
                body, status = None, None
            w.clear_faults()
            assert status != 200 or body == want, f"{name}: a 200 holding {len(body or b'')} of {len(want)} bytes"
        w.faults(fault(CONTENT_URL, 429, max_times=1))
        resp = stream(w, rid)
        w.clear_faults()
        assert resp.status_code in (200, 429), resp.status_code
        if resp.status_code == 200:
            assert resp.content == want
        w.faults(fault(CONTENT_URL, 429))
        assert stream(w, rid).status_code == 429
        w.clear_faults()
        assert stream(w, rid).content == want, "the file does not stream after the faults"

    async def test_tc_jcsim_strm_004_comments(self, jira_cloud_world: JiraCloudWorld) -> None:
        w = jira_cloud_world
        project = _roomy_project(w)
        assert succeeded(await w.start())
        role = "Administrators"
        me = w.sim.rest("GET", "/myself")["accountId"]
        group = w.sim.rest("GET", "/user/groups", accountId=me)[0]["name"]
        issue = new_issue(w, project, "Comments of every kind", doc(para("description of the comment issue")))
        key, tid = issue["key"], str(issue["id"])
        w.sim.upload(key, "secret-notes.txt", b"only the restricted comment shows this")
        w.sim.upload(key, "secret-pic.png", png(400, 3))
        w.sim.upload(key, "shared-notes.txt", b"public and restricted both show this")
        w.sim.upload(key, "public-pic.png", png(400, 5))
        bodies = [add_comment(w, key, comment_text(f"public comment number {i:03d} words")) for i in range(103)]
        by_role = add_comment(w, key, doc(para("restricted by role words", node("hardBreak")),
                                          node("mediaGroup", media("secret-notes.txt"), media("shared-notes.txt")),
                                          node("mediaSingle", media("secret-pic.png"))),
                              {"type": "role", "value": role})
        by_group = add_comment(w, key, comment_text("restricted by group words"), {"type": "group", "value": group})
        add_comment(w, key, doc(para("public mention of the shared file"), node("mediaGroup", media("shared-notes.txt")),
                                node("mediaSingle", media("public-pic.png"))))
        edited = add_comment(w, key, comment_text("comment before its edit"))
        removed = add_comment(w, key, comment_text("comment that will be deleted"))
        later = add_comment(w, key, comment_text("comment restricted later"))
        assert succeeded(await w.sync())
        by_ext = ids_by_external(await w.records())
        restricted = {c["id"]: by_ext.get(f"{COMMENT_PREFIX}{c['id']}") for c in (by_role, by_group)}
        assert all(restricted.values()), f"restricted comments without a record: {restricted}"

        # More comments than the issue embeds: the stream reads them all, page by page.
        w.faults({"match": {"path_regex": ISSUE_URL}, "effect": {"type": "shape", "ops": [
            {"path": "fields.comment.total", "value": 5000}]}})
        resp = stream(w, by_ext[tid])
        fired = w.clear_faults()
        assert fired
        container = _ok(w, by_ext[tid], "the issue with 103 comments", resp)
        data = group_data(container)
        for i in (0, 50, 99, 102):
            assert f"public comment number {i:03d} words" in data
        seen = [g["data"] for g in comment_groups(container)]
        order = [int(m) for g in seen for m in re.findall(r"number (\d{3}) words", g)]
        assert order == sorted(order), "comments are not in the order they were written"
        assert "restricted by role" not in data and "restricted by group" not in data
        assert "secret-notes.txt" not in data and "secret-pic.png" not in data, "a file only a restricted comment shows"
        named = children(container)
        for att, shown in (("secret-notes.txt", False), ("shared-notes.txt", True)):
            rec = next((r for r in await w.records() if r["recordName"] == att), None)
            if rec:
                assert (rec["id"] in named) == shown, f"{att}: child of the issue's stream is {rec['id'] in named}"

        # A restricted comment's own stream: its text, its issue's title; the other restricted comment is not in it.
        mine = {r["id"] for r in await w.creator_records()}
        assert restricted[by_role["id"]] in mine, "the creator does not read the role-restricted comment"
        role_data = group_data(_ok(w, restricted[by_role["id"]], "the role-restricted comment"))
        assert "restricted by role words" in role_data and "restricted by group words" not in role_data
        assert "Comments of every kind" in role_data and "public comment number" not in role_data
        if restricted[by_group["id"]] in mine:
            group_stream = group_data(_ok(w, restricted[by_group["id"]], "the group-restricted comment"))
            assert "restricted by group words" in group_stream and "restricted by role words" not in group_stream

        # Edited, restricted and deleted after the sync.
        w.sim.rest("PUT", f"/issue/{key}/comment/{edited['id']}", {"body": comment_text("comment after its edit")})
        w.sim.rest("PUT", f"/issue/{key}/comment/{later['id']}",
                   {"body": comment_text("comment restricted later"), "visibility": {"type": "role", "value": role}})
        w.sim.rest("DELETE", f"/issue/{key}/comment/{removed['id']}")
        data = group_data(_ok(w, by_ext[tid], "the issue after edits"))
        assert "comment after its edit" in data and "comment before its edit" not in data
        assert "comment restricted later" not in data, "a comment restricted after the sync is still served"
        assert "comment that will be deleted" not in data
        # A comment record of a comment that was deleted is gone for the reader.
        w.sim.rest("DELETE", f"/issue/{key}/comment/{by_role['id']}")
        assert stream(w, restricted[by_role["id"]]).status_code == 404
        assert succeeded(await w.sync())

    async def test_tc_jcsim_strm_005_moved_and_gone(self, jira_cloud_world: JiraCloudWorld) -> None:
        w = jira_cloud_world
        assert succeeded(await w.start())
        by_ext = ids_by_external(await w.creator_records())
        moved = [str(c["issue_id"]) for c in w.sim.advance(move=1)["changes"]["move"]]
        gone = [str(c["issue_id"]) for c in w.sim.advance(delete=1)["changes"]["delete"]]
        assert moved and gone
        for tid in moved:
            if tid not in by_ext:
                continue
            resp = stream(w, by_ext[tid])
            assert resp.status_code == 404, f"an issue moved to another project streamed with {resp.status_code}"
        for tid in gone:
            if tid in by_ext:
                assert stream(w, by_ext[tid]).status_code == 404
        assert any(t in by_ext for t in moved) or any(t in by_ext for t in gone), "the changes touched no readable issue"

    @staticmethod
    def _rules(url: str, kind: str, **trigger):
        codes = {"401": 401, "403": 403, "404": 404, "500": 500, "503": 503}
        if kind in codes:
            return fault(url, codes[kind], **trigger)
        return drop(url, **trigger) if kind == "drop" else malformed(url, **trigger)

    @staticmethod
    def _one(w: JiraCloudWorld, rid: str, *rules) -> tuple[int | None, bytes, int]:
        """One stream with the rules on; (status or None when the connection failed, body, how many faults fired)."""
        w.faults(*rules)
        try:
            resp = stream(w, rid)
            status, body = resp.status_code, resp.content
        except requests.RequestException:
            status, body = None, b""
        fired = len(w.sim.call("GET", "/_sim/faults/log") or [])
        w.sim.call("DELETE", "/_sim/faults")
        w._fault_rules = []
        return status, body, fired

    async def test_tc_jcsim_strm_006_issue_stream_faults(self, jira_cloud_world: JiraCloudWorld) -> None:
        w = jira_cloud_world
        project = _roomy_project(w)
        assert succeeded(await w.start(filters=project_filter("in", [project])))
        role = "Administrators"
        issue = new_issue(w, project, "Faults of the issue stream", doc(para("description of the fault issue")))
        key, tid = issue["key"], str(issue["id"])
        add_comment(w, key, comment_text("public comment of the fault issue"))
        add_comment(w, key, comment_text("hidden restricted words"), {"type": "role", "value": role})
        w.sim.upload(key, "a-file.txt", b"x")
        assert succeeded(await w.sync())
        by_ext = ids_by_external(await w.records())
        rid = by_ext[tid]
        index = w.sim.expected()["ticket_index"]
        pic_tid = next(t for t, v in sorted(index.items(), key=lambda kv: int(kv[0]))
                       if v["inline"] and t in by_ext and v["key"].startswith(project + "-"))
        pic_rid = by_ext[pic_tid]
        shape = {"match": {"path_regex": ISSUE_URL}, "effect": {"type": "shape", "ops": [
            {"path": "fields.comment.total", "value": 5000}]}}
        kinds = ("401", "403", "404", "500", "503", "drop", "cut")
        primary = {"401": 409, "403": 403, "404": 404}
        # A cut body of a picture is a well-formed short answer to the connector (the simulator sets its length); only
        # the JSON reads are cut.
        seen: list[str] = []
        problems: list[str] = []
        # 1. the issue itself, 2. the comments in full (the issue says it holds more than it embeds)
        for label, url, extra in (("issue", ISSUE_URL, ()), ("comments", COMMENTS_URL, (shape,))):
            for kind in kinds:
                status, body, fired = self._one(w, rid, self._rules(url, kind), *extra)
                seen.append(f"{label}/{kind}: {status} ({len(body)} bytes, {fired} fired)")
                if not fired:
                    problems.append(f"{label}/{kind}: the fault never fired")
                if status == 200:
                    problems.append(f"{label}/{kind}: a 200 although a read it needs failed ({len(body)} bytes)")
                if label == "issue" and kind in primary and status != primary[kind]:
                    problems.append(f"{label}/{kind}: {status}, expected {primary[kind]}")
                if b"hidden restricted words" in body:
                    problems.append(f"{label}/{kind}: restricted text served")
        # 3. the image of an issue: its failure costs the picture, not the stream
        for kind in kinds[:-1]:
            status, body, fired = self._one(w, pic_rid, self._rules(CONTENT_URL, kind))
            seen.append(f"image/{kind}: {status} ({len(body)} bytes, {fired} fired)")
            if not fired:
                problems.append(f"image/{kind}: the fault never fired")
            if status != 200 or b"data:image" in body:
                problems.append(f"image/{kind}: {status}; a picture that did not load must be left out")
        # 4. a 429 that passes by the next try, and one that does not
        status, body, fired = self._one(w, rid, fault(ISSUE_URL, 429, max_times=1))
        seen.append(f"issue/429 once: {status}")
        if status != 200 or fired != 1 or b"hidden restricted words" in body:
            problems.append(f"issue/429 once: {status}, fired {fired}")
        status, _body, fired = self._one(w, rid, fault(ISSUE_URL, 429))
        seen.append(f"issue/429 for good: {status}")
        if status != 429 or not fired:
            problems.append(f"issue/429 for good: {status}")
        assert not problems, {"problems": problems, "seen": seen}
        ok = stream(w, rid)
        assert ok.status_code == 200 and "description of the fault issue" in ok.text, seen

    async def test_tc_jcsim_strm_007_comment_stream_faults(self, jira_cloud_world: JiraCloudWorld) -> None:
        w = jira_cloud_world
        project = _roomy_project(w)
        assert succeeded(await w.start(filters=project_filter("in", [project])))
        role = "Administrators"
        issue = new_issue(w, project, "Faults of the comment stream", doc(para("description of the fault issue")))
        key = issue["key"]
        w.sim.upload(key, "only-restricted.txt", b"bytes")
        w.sim.upload(key, "another-file.txt", b"bytes too")
        c = add_comment(w, key, doc(para("restricted comment words"), node("mediaGroup", media("only-restricted.txt"))),
                        {"type": "role", "value": role})
        add_comment(w, key, comment_text("a public comment of the issue"))
        assert succeeded(await w.sync())
        rid = ids_by_external(await w.records())[f"{COMMENT_PREFIX}{c['id']}"]
        shape = {"match": {"path_regex": ISSUE_URL}, "effect": {"type": "shape", "ops": [
            {"path": "fields.comment.total", "value": 5000}]}}
        kinds = ("401", "404", "500", "drop", "cut")
        # The reads in order: the issue, the comment, the comments in full, the issue's rendered description.
        cases = {"issue": (ISSUE_URL, {}, ()), "comment": (COMMENT_URL, {}, ()),
                 "comments": (COMMENTS_URL, {}, (shape,)), "description": (ISSUE_URL, {"after_n": 1}, ())}
        seen: list[str] = []
        problems: list[str] = []
        assert "restricted comment words" in stream(w, rid).text
        seen.append(f"clean stream calls: {w.sim.paths_called()}")
        for label, (url, trigger, extra) in cases.items():
            for kind in kinds:
                status, body, fired = self._one(w, rid, self._rules(url, kind, **trigger), *extra)
                seen.append(f"{label}/{kind}: {status} ({len(body)} bytes, {fired} fired)")
                if not fired:
                    problems.append(f"{label}/{kind}: the fault never fired")
                elif status == 200 and (label, kind) != ("description", "cut"):
                    problems.append(f"{label}/{kind}: a 200 although a read it needs failed")
                if b"a public comment of the issue" in body:
                    problems.append(f"{label}/{kind}: another comment is in a restricted comment's stream")
            # A read that fails once and then answers is retried: 5xx and a dropped connection pass.
            for kind in ("500", "drop"):
                status, body, fired = self._one(w, rid, self._rules(url, kind, max_times=1, **trigger), *extra)
                seen.append(f"{label}/{kind} once: {status} ({fired} fired)")
                if not fired or status != 200 or b"restricted comment words" not in body:
                    problems.append(f"{label}/{kind} once: {status}, fired {fired}")
        assert not problems, {"problems": problems, "seen": seen}
        ok = stream(w, rid)
        assert ok.status_code == 200 and "restricted comment words" in ok.text
        assert "a public comment of the issue" not in ok.text

    @pytest.mark.xfail(strict=True, reason="_rendered_description (connector.py) takes an unparseable answer for an empty "
                                           "description instead of failing the read")
    async def test_tc_jcsim_strm_008_cut_description_read(self, jira_cloud_world: JiraCloudWorld) -> None:
        w = jira_cloud_world
        project = _roomy_project(w)
        assert succeeded(await w.start(filters=project_filter("in", [project])))
        issue = new_issue(w, project, "Cut description read", doc(para("a description")))
        w.sim.upload(issue["key"], "attached.txt", b"x")
        c = add_comment(w, issue["key"], comment_text("restricted words"), {"type": "role", "value": "Administrators"})
        assert succeeded(await w.sync())
        rid = ids_by_external(await w.records())[f"{COMMENT_PREFIX}{c['id']}"]
        status, _body, fired = self._one(w, rid, malformed(ISSUE_URL, after_n=1))
        assert fired
        assert status != 200

    async def _restricted_to_groups(self, w: JiraCloudWorld, project: str, names: list[str]) -> dict[str, str]:
        """A comment restricted to each group, each on an issue of its own, read by a sync: group name -> record id."""
        comments = {}
        for name in names:
            issue = new_issue(w, project, f"Comment for {name}", doc(para("a body")))
            comments[name] = add_comment(w, issue["key"], comment_text(f"restricted to {name}"), {"type": "group", "value": name})
        assert succeeded(await w.sync())
        by_ext = ids_by_external(await w.records())
        return {name: by_ext[f"{COMMENT_PREFIX}{c['id']}"] for name, c in comments.items()}

    async def test_tc_jcsim_strm_010_group_restricted_comments_are_read_by_the_groups_members(
            self, jira_cloud_world: JiraCloudWorld) -> None:
        w = jira_cloud_world
        project = _roomy_project(w)
        assert succeeded(await w.start(filters=project_filter("in", [project])))
        me = w.sim.rest("GET", "/myself")["accountId"]
        names = [g["name"] for g in w.sim.rest("GET", "/user/groups", accountId=me) if not g["name"].startswith(SYSTEM_GROUPS)]
        assert len(names) > 1, names
        records = await self._restricted_to_groups(w, project, names)
        await w.assert_matches_source("with a comment restricted to each of the sync account's groups")
        mine = {r["id"] for r in await w.creator_records()}
        assert set(records.values()) <= mine, f"not read by a member: {[n for n, rid in records.items() if rid not in mine]}"

    async def test_tc_jcsim_strm_011_a_comment_restricted_to_a_system_group_is_read_by_nobody(
            self, jira_cloud_world: JiraCloudWorld) -> None:
        """Atlassian's groups of installed apps (``atlassian-addons*``) are left out on purpose (``_process_group``), so a
        comment restricted to one is closed, the sync account included, rather than opened to anyone."""
        w = jira_cloud_world
        project = _roomy_project(w)
        assert succeeded(await w.start(filters=project_filter("in", [project])))
        me = w.sim.rest("GET", "/myself")["accountId"]
        name = next(g["name"] for g in w.sim.rest("GET", "/user/groups", accountId=me) if g["name"].startswith(SYSTEM_GROUPS))
        records = await self._restricted_to_groups(w, project, [name])
        rec = next(r for r in await w.records() if r["id"] == records[name])
        readers = {email for email, ids in (await w.readable([rec])).items() if rec["id"] in ids}
        assert not readers, f"read by {sorted(readers)}"
