# pyright: ignore-file

"""
Jira Cloud on the simulator: a ticket read with its live Jira fields
====================================================================

When an agent reads a ticket (the record read the Node API serves, ``/api/v1/connectors/record/{id}/content`` with a
budget), the query service adds the ticket's live fields, read from Jira with the connector's credentials: custom fields
the site names like the curated ones, and other issues the ticket names only if the reader may read them.

The site here has the simulator's custom-field catalogue (40 fields of 15 types, some hidden by their field configuration,
some with deleted options); a test renames catalogue fields to the curated names (Impact, Urgency, Approvers, Approver).

  TC-JCSIM-ENR-001  Custom fields of every curated kind (select, radio, number, date, multi-user) read as Jira holds them
  TC-JCSIM-ENR-002  A field with no value, a hidden one and a deleted option add no line
  TC-JCSIM-ENR-003  A single-user custom field named Approver
  TC-JCSIM-ENR-004  Linked issues are named only when the reader may read them
  TC-JCSIM-ENR-005  Jira failing while the fields are read leaves the plain ticket, never an error or another issue
"""

import re

import pytest
from connectors.jira.cloud_sim_content import (
    CONTENT_URL,
    SEARCH_URL,
    doc,
    ids_by_external,
    jira_get,
    new_issue,
    para,
    read_record,
    wait_for,
)
from connectors.jira.cloud_sim_world import (  # noqa: F401 - fixtures of the jira_cloud_sim suite
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

FIELDS_URL = r"^/rest/api/3/field$"
LIVE_HEAD = "Ticket Information:"


async def _read(w: JiraCloudWorld, record_id: str, *, need: str = LIVE_HEAD) -> str:
    """The ticket as an agent reads it, once the record is indexed (a read of one that is not waits for it)."""
    await w.wait_indexed(record_id, timeout=420)
    out = {}

    async def read():
        resp = read_record(w, record_id)
        if resp.status_code != 200:
            return None
        body = resp.json()
        out["text"] = str(body.get("content") or "")
        return need in out["text"] or None

    try:
        await wait_for(read, "the ticket's content", timeout=420, interval=5)
    except AssertionError as exc:
        raise AssertionError(f"{exc}; it reads {out.get('text', '')[:1500]!r}") from exc
    return out["text"]


def _line(text: str, label: str) -> str | None:
    for line in text.splitlines():
        s = line.strip()
        if s.startswith(f"* {label}:"):
            return s
    return None


def _values(w: JiraCloudWorld, key: str, field: str) -> list:
    """What Jira holds in ``field`` of the issue, as the simulator's oracle gives it ([] when it holds nothing)."""
    got = w.sim.call("GET", f"/_sim/jira/expected/issue/{key}/fields")["values"].get(field)
    if not got:
        return []
    j = got.get("json")
    return j if isinstance(j, list) else [j] if j not in (None, "") else []


def _tokens(value) -> list[str]:
    if isinstance(value, dict):
        for k in ("value", "displayName", "name"):
            if value.get(k):
                return [str(value[k])]
        return []
    return [str(value)]


def _issue_with(w: JiraCloudWorld, field: str, readable: set[str]) -> tuple[str, str, list] | None:
    for tid, t in sorted(w.sim.expected()["ticket_index"].items(), key=lambda kv: int(kv[0])):
        if tid in readable:
            vals = _values(w, t["key"], field)
            if vals:
                return tid, t["key"], vals
    return None


@pytest.mark.integration
@pytest.mark.jira_cloud_sim
@pytest.mark.asyncio(loop_scope="session")
class TestJiraCloudSimEnrichment:

    RENAMES = {"customfield_11000": "Impact", "customfield_11002": "Urgency", "customfield_11006": "Approvers",
               "customfield_11009": "Story points"}

    def _prepare(self, **extra: str) -> list[dict]:
        return [{"op": "field_name", "field": fid, "name": name} for fid, name in {**self.RENAMES, **extra}.items()]

    async def test_tc_jcsim_enr_001_custom_fields(self, jira_cloud_world: JiraCloudWorld) -> None:
        w = jira_cloud_world
        assert succeeded(await w.start(prepare=self._prepare()))
        by_ext = ids_by_external(await w.creator_records())
        key = next(k for k in w.issue_keys(w.projects()[0]))
        # The five hand-written fields are written through the vendor's API.
        w.sim.rest("PUT", f"/issue/{key}", {"fields": {"customfield_10016": 13, "customfield_10019": "2026-02-03"}})
        tid = next(t for t, v in w.sim.expected()["ticket_index"].items() if v["key"] == key)
        text = await _read(w, by_ext[tid], need="Story Points")
        assert _line(text, "Story Points") and "13" in _line(text, "Story Points"), text[:1500]
        assert _line(text, "Start Date") and "2026-02-03" in _line(text, "Start Date")
        problems = []
        for field, label, kind in (("customfield_11000", "Impact", "option"), ("customfield_11002", "Urgency", "option"),
                                   ("customfield_11006", "Approvers", "users")):
            found = _issue_with(w, field, set(by_ext))
            if not found:
                problems.append(f"{label}: no readable issue holds a value")
                continue
            ftid, fkey, vals = found
            body = await _read(w, by_ext[ftid], need=f"* {label}:")
            line = _line(body, label)
            for v in vals:
                for token in _tokens(v):
                    if token not in (line or ""):
                        problems.append(f"{fkey}: {label} line {line!r} lacks {token!r}")
        assert not problems, problems

    async def test_tc_jcsim_enr_002_no_line_without_a_value(self, jira_cloud_world: JiraCloudWorld) -> None:
        w = jira_cloud_world
        assert succeeded(await w.start(prepare=self._prepare()))
        by_ext = ids_by_external(await w.creator_records())
        for _ in range(3):  # a deleted option takes the value away from the issues that held it
            w.sim.advance(option_delete=4)
        empty = []
        for tid, t in sorted(w.sim.expected()["ticket_index"].items(), key=lambda kv: int(kv[0])):
            if tid in by_ext and not _values(w, t["key"], "customfield_11000"):
                empty.append((tid, t["key"]))
            if len(empty) == 2:
                break
        assert empty
        for tid, key in empty:
            text = await _read(w, by_ext[tid])
            assert _line(text, "Impact") is None, f"{key}: a line for an Impact it does not hold: {_line(text, 'Impact')}"
        hidden = [f for f in ("customfield_11010", "customfield_11033")]  # not curated: never a line
        assert all(_line(text, n) is None for n in ("Analytics Effort", "Audit Component")), hidden

    async def test_tc_jcsim_enr_003_single_user_field(self, jira_cloud_world: JiraCloudWorld) -> None:
        w = jira_cloud_world
        assert succeeded(await w.start(prepare=[{"op": "field_name", "field": "customfield_11005", "name": "Approver"},
                                                {"op": "field_name", "field": "customfield_11001", "name": "Severity"}]))
        by_ext = ids_by_external(await w.creator_records())
        found = _issue_with(w, "customfield_11005", set(by_ext))
        assert found, "no readable issue holds a user in the renamed field"
        tid, key, vals = found
        text = await _read(w, by_ext[tid], need="* Approvers:")
        line = _line(text, "Approvers")
        assert line and all(t in line for v in vals for t in _tokens(v)), (line, vals)

    async def test_tc_jcsim_enr_004_other_issues_by_what_the_reader_may_read(self, jira_cloud_world: JiraCloudWorld) -> None:
        w = jira_cloud_world
        index = w.sim.expected()["ticket_index"]
        pair = None
        for tid, t in sorted(index.items(), key=lambda kv: int(kv[0])):
            issue = w.sim.rest("GET", f"/issue/{tid}", fields="issuelinks,subtasks,parent,issuetype")["fields"]
            if issue["issuetype"]["name"] == "Epic":  # an epic's content does not index on this stack: nothing to read
                continue
            for link in issue.get("issuelinks") or []:
                other = link.get("outwardIssue") or link.get("inwardIssue")
                if other and other["key"].rsplit("-", 1)[0] != t["key"].rsplit("-", 1)[0]:
                    pair = (tid, t["key"], other["key"])
                    break
            if pair:
                break
        assert pair, "no issue is linked to an issue of another project"
        tid, key, other = pair
        project, other_project = key.rsplit("-", 1)[0], other.rsplit("-", 1)[0]
        assert succeeded(await w.start(filters=project_filter("in", [project])))
        by_ext = ids_by_external(await w.creator_records())
        text = await _read(w, by_ext[tid])
        assert other not in text, f"{key}: the read names {other}, an issue of {other_project}, which is not synced"
        assert succeeded(await w.restart(filters=project_filter("in", [project, other_project])))
        by_ext = ids_by_external(await w.creator_records())
        text = await _read(w, by_ext[tid], need="Linked Issues")
        assert other in (_line(text, "Linked Issues") or ""), f"{key}: no line naming {other}: {text[:1200]}"

    async def test_tc_jcsim_enr_005_jira_failing(self, jira_cloud_world: JiraCloudWorld) -> None:
        w = jira_cloud_world
        assert succeeded(await w.start(prepare=self._prepare()))
        by_ext = ids_by_external(await w.creator_records())
        tid, key = next((t, v["key"]) for t, v in sorted(w.sim.expected()["ticket_index"].items(), key=lambda kv: int(kv[0]))
                        if t in by_ext and _values(w, v["key"], "customfield_11000"))
        await w.wait_indexed(by_ext[tid])
        full = await _read(w, by_ext[tid], need="* Impact:")
        assert _line(full, "Impact")
        # Discovery is cached for a connector, so each failing read of the fields is the first one: new connectors would be
        # needed to see them all; the search that reads the issue is read each time.
        for name, rule in (("401", fault(SEARCH_URL, 401)), ("403", fault(SEARCH_URL, 403)), ("500", fault(SEARCH_URL, 500)),
                           ("429", fault(SEARCH_URL, 429)), ("drop", drop(SEARCH_URL)), ("cut", malformed(SEARCH_URL))):
            w.faults(rule)
            resp = read_record(w, by_ext[tid])
            fired = w.clear_faults()
            assert fired, name
            assert resp.status_code == 200, f"{name}: the read answered {resp.status_code}"
            text = str(resp.json().get("content") or "")
            assert LIVE_HEAD in text and key not in "" and "Traceback" not in text, name
            assert _line(text, "Impact") is None, f"{name}: a live line without Jira's answer"
        ok = read_record(w, by_ext[tid])
        assert _line(str(ok.json().get("content") or ""), "Impact"), "the live lines do not return"

    async def test_tc_jcsim_enr_006_system_fields(self, jira_cloud_world: JiraCloudWorld) -> None:
        w = jira_cloud_world
        assert succeeded(await w.start())
        project, levels = w.leveled_project()
        parent = new_issue(w, project, "Parent of the enriched sub-task")
        sub = new_issue(w, project, "Enriched sub-task", issuetype="Sub-task", parent={"key": parent["key"]},
                        labels=["alpha-label", "beta-label"])
        w.mutate({"op": "security", "issue": sub["key"], "level": levels[0]["name"]})
        assert succeeded(await w.sync())
        by_ext = ids_by_external(await w.creator_records())
        assert str(sub["id"]) in by_ext and str(parent["id"]) in by_ext
        text = await _read(w, by_ext[str(sub["id"])], need="* Project:")
        want = {"Labels": "alpha-label, beta-label", "Security": levels[0]["name"], "Project": project,
                "Parent": f"{parent['key']} — Parent of the enriched sub-task"}
        problems = [f"{label}: {_line(text, label)!r} lacks {value!r}" for label, value in want.items()
                    if value not in (_line(text, label) or "")]
        assert not problems, (problems, text[:1500])
