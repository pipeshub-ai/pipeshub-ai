# pyright: ignore-file

"""
Linear on the simulator: sync filters
=====================================

The connector filters by team (in / not in) and by creation and modification date; the filters are saved as the app saves
them, on a created connector or on a disabled one. The comparison knows the filter (``Scope``) and requires exactly what
Linear shows inside it.

  TC-LNSIM-FLT-001  Teams "in" (ENG, OPS, SECOPS): only their issues, projects, files and groups [G]
  TC-LNSIM-FLT-002  Teams "not in" (ENG) [G]
  TC-LNSIM-FLT-003  A filter changed later, with the connector off: the next run is a full sync of the new scope and what
                    left the scope is unreadable by everyone [G, F]
  TC-LNSIM-FLT-004  Issues created after a date: older issues are out and the comparison (placeholders excluded) holds [G]
  TC-LNSIM-FLT-005  A filter that names a team the workspace does not have (or none the account sees): the run does not
                    fail and holds nothing of other teams [G]
  TC-LNSIM-FLT-006  A date filter changed to a wider one, with the connector off: the older issues come in [G]
  TC-LNSIM-FLT-007  An issue edited after a modification-date cut: it is read, an untouched older one is not [G]
  TC-LNSIM-FLT-008  A manual sync (the "manual sync" filter) reads the same scope as a scheduled one [G]
  TC-LNSIM-FLT-009  Every indexing filter off (issues, issue_attachments, documents, files, projects): each kind of record
                    is stored and readable, and none is queued for indexing [G]
  TC-LNSIM-FLT-010  Only "files" off: file records are not indexed, issues and projects are [G]
  TC-LNSIM-FLT-011  An indexing filter turned off, then on again later: the records stay equal to the source, and a full
                    sync after it queues what the filter let in [G]
  TC-LNSIM-FLT-012  The "manual indexing" switch on: nothing is indexed on its own whatever the five filters say; the
                    scope read is unchanged [G]
  TC-LNSIM-PH-001   Placeholder sweep and promotion (twin of the live placeholder case): an issue edited after the cut
                    syncs, its two older ancestors are stored as stubs, swept (named, revisioned), and promoted in place
                    once the window is dropped [G, H]
"""

import time

import pytest
from connectors.linear.sim_instances import small, workspace
from connectors.linear.sim_world import LinearWorld, Scope, date_filter, succeeded, team_filter


OFF = "AUTO_INDEX_OFF"
INDEXING_KEYS = ("issues", "issue_attachments", "documents", "files", "projects")


def indexing(**values: bool) -> dict:
    """The "indexing" part of a connector's filters, saved as the app saves it (every key not named stays on)."""
    return {"indexing": {"values": {k: {"operator": "is", "type": "boolean", "value": v} for k, v in values.items()}}}


def with_documents() -> dict:
    """``small()`` plus a document on an issue, so every kind of record the indexing filters name exists."""
    scenario = small()
    scenario["issues"][1]["documents"] = [{"key": "d1", "title": "Design notes", "content": "# Design", "creator": "alice"}]
    return scenario


def by_type(records: list[dict]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for r in records:
        out.setdefault(r["recordType"], []).append(r)
    return out


def chain() -> dict:
    """Three ENG issues, each the sub-issue of the one before: ``gp`` <- ``mid`` <- ``leaf``."""
    scenario = small()
    scenario["issues"] = [
        {"key": "gp", "team": "ENG", "title": "Grandparent", "creator": "alice"},
        {"key": "mid", "team": "ENG", "title": "Parent", "creator": "alice", "parent": "gp"},
        {"key": "leaf", "team": "ENG", "title": "Leaf", "creator": "alice", "parent": "mid"},
        {"key": "o1", "team": "OPS", "title": "Ops one", "creator": "bob"}]
    scenario["projects"] = []
    return scenario


def ids(w: LinearWorld, *keys: str) -> list[str]:
    return [t["id"] for t in w.sim.expected()["teams"] if t["key"] in keys]


@pytest.mark.integration
@pytest.mark.linear_sim
@pytest.mark.asyncio(loop_scope="session")
class TestLinearSimFilters:

    async def _teams(self, w: LinearWorld, operator: str, keys: list[str], account=None) -> None:
        w.load(account or workspace())
        filters = team_filter(operator, ids(w, *keys))
        w.scope = Scope(teams=(operator, keys))
        w.create_connector(filters=filters)
        assert succeeded(await w.enable())

    async def test_tc_lnsim_flt_001_teams_in(self, linear_world: LinearWorld) -> None:
        w = linear_world
        await self._teams(w, "in", ["ENG", "OPS", "SECOPS"])
        await w.assert_matches_source("teams ENG, OPS and SECOPS only")
        assert not {r["externalGroupId"] for r in await w.records()} & set(ids(w, "PORTAL", "SEC"))

    async def test_tc_lnsim_flt_002_teams_not_in(self, linear_world: LinearWorld) -> None:
        w = linear_world
        await self._teams(w, "not_in", ["ENG"])
        await w.assert_matches_source("every team except ENG")

    async def test_tc_lnsim_flt_003_filter_changed_later(self, linear_world: LinearWorld) -> None:
        w = linear_world
        await self._teams(w, "in", ["ENG", "OPS", "SECOPS"])
        await w.assert_matches_source("first scope")
        outcome = await w.restart(filters=team_filter("not_in", ids(w, "ENG")), scope=Scope(teams=("not_in", ["ENG"])))
        assert succeeded(outcome)
        assert await w.sync(full=True)
        await w.assert_matches_source("every team except ENG, after the filter changed")

    async def test_tc_lnsim_flt_004_created_after_a_date(self, linear_world: LinearWorld) -> None:
        w = linear_world
        w.load(workspace())
        created = sorted(i["created"] for i in w.sim.expected()["issues"])
        cut = created[len(created) // 2] + 1
        w.scope = Scope(created_after=cut)
        w.create_connector(filters={"sync": {"values": date_filter("created", cut, None)}})
        assert succeeded(await w.enable())
        await w.assert_matches_source("issues created after the cut")

    async def test_tc_lnsim_flt_005_a_team_that_is_not_there(self, linear_world: LinearWorld) -> None:
        w = linear_world
        w.load(small())
        w.scope = Scope(teams=("in", ["NOPE"]))
        w.create_connector(filters=team_filter("in", ["00000000-0000-4000-8000-000000000000"]))
        outcome = await w.enable()
        assert outcome is not None
        assert not await w.records(), "a filter that names no team of the account still stored records"
        await w.assert_matches_source("a filter naming no team", allow_empty=True)

    async def test_tc_lnsim_flt_006_a_wider_date_filter(self, linear_world: LinearWorld) -> None:
        w = linear_world
        w.load(workspace())
        created = sorted(i["created"] for i in w.sim.expected()["issues"])
        cut = created[len(created) // 2] + 1
        w.scope = Scope(created_after=cut)
        w.create_connector(filters={"sync": {"values": date_filter("created", cut, None)}})
        assert succeeded(await w.enable())
        await w.assert_matches_source("narrow")
        w.scope = Scope(created_after=created[0])
        assert succeeded(await w.restart(filters={"sync": {"values": date_filter("created", created[0], None)}}))
        await w.assert_matches_source("after the filter was widened")

    async def test_tc_lnsim_flt_007_modified_after_a_date(self, linear_world: LinearWorld) -> None:
        w = linear_world
        w.load(small())
        cut = int(time.time() * 1000) + 5_000
        w.create_connector(filters={"sync": {"values": date_filter("modified", cut, None)}})
        assert succeeded(await w.enable())
        assert not [r for r in await w.records() if r["recordType"] == "TICKET"], "an older issue was read"
        w.ops({"op": "advance", "ms": 20_000}, {"op": "edit_issue", "issue": "e2", "title": "edited after the cut"})
        assert succeeded(await w.sync())
        names = [r["recordName"] for r in await w.records() if r["recordType"] == "TICKET"]
        assert any("edited after the cut" in n for n in names), names
        assert not any("First" in n or "Ops one" in n for n in names), names

    async def test_tc_lnsim_flt_008_manual_sync_reads_the_same_scope(self, linear_world: LinearWorld) -> None:
        w = linear_world
        await self._teams(w, "in", ["ENG", "OPS"], account=small())
        w.ops({"op": "add_issue", "team": "OPS", "title": "added before the manual run", "creator": "bob"})
        assert succeeded(await w.sync(full=True))
        await w.assert_matches_source("a manual full sync inside the team filter")

    async def _kinds(self, w: LinearWorld) -> dict[str, list[dict]]:
        kinds = by_type(await w.records())
        assert {"TICKET", "PROJECT"} <= set(kinds), f"the workspace should hold issues and projects: {sorted(kinds)}"
        return kinds

    async def test_tc_lnsim_flt_009_every_indexing_filter_off(self, linear_world: LinearWorld) -> None:
        w = linear_world
        w.load(with_documents())
        w.create_connector(filters=indexing(**dict.fromkeys(INDEXING_KEYS, False)))
        assert succeeded(await w.enable())
        await w.assert_matches_source("every indexing filter off")
        kinds = await self._kinds(w)
        assert len(kinds) >= 4, f"expected issues, links, documents, files and projects, got {sorted(kinds)}"
        on = [(t, r["recordName"], r["indexingStatus"]) for t, rs in kinds.items() for r in rs if r["indexingStatus"] != OFF]
        assert not on, f"records queued for indexing although every indexing filter is off: {on[:10]}"

    async def test_tc_lnsim_flt_010_only_files_off(self, linear_world: LinearWorld) -> None:
        w = linear_world
        w.load(with_documents())
        w.create_connector(filters=indexing(files=False))
        assert succeeded(await w.enable())
        await w.assert_matches_source("files not indexed")
        kinds = await self._kinds(w)
        assert kinds.get("FILE"), f"the workspace should hold file records: {sorted(kinds)}"
        assert all(r["indexingStatus"] == OFF for r in kinds["FILE"]), [r["indexingStatus"] for r in kinds["FILE"]]
        others = [(t, r["recordName"], r["indexingStatus"]) for t, rs in kinds.items() if t != "FILE" for r in rs
                  if r["indexingStatus"] == OFF]
        assert not others, f"a filter for files switched off the indexing of something else: {others[:10]}"

    async def test_tc_lnsim_flt_011_a_filter_turned_on_again(self, linear_world: LinearWorld) -> None:
        w = linear_world
        w.load(with_documents())
        w.create_connector(filters=indexing(**dict.fromkeys(INDEXING_KEYS, False)))
        assert succeeded(await w.enable())
        assert all(r["indexingStatus"] == OFF for r in await w.records())
        before = {r["externalRecordId"] for r in await w.records()}
        assert succeeded(await w.restart(filters=indexing(**dict.fromkeys(INDEXING_KEYS, True))))
        assert await w.sync(full=True)
        await w.assert_matches_source("indexing filters turned on again")
        assert before <= {r["externalRecordId"] for r in await w.records()}, "records were lost when a filter changed"
        deadline = time.monotonic() + 120
        while True:
            off = [r["recordName"] for r in await w.records() if r["indexingStatus"] == OFF]
            if not off or time.monotonic() > deadline:
                break
            time.sleep(5)
        assert not off, f"records still switched off for indexing after every filter was turned on and a full sync: {off[:10]}"

    async def test_tc_lnsim_flt_012_manual_indexing_switch(self, linear_world: LinearWorld) -> None:
        w = linear_world
        w.load(with_documents())
        w.scope = Scope(teams=("in", ["ENG"]))
        w.create_connector(filters={**team_filter("in", ids(w, "ENG")),
                                    **indexing(enable_manual_sync=True, issues=True, files=True)})
        assert succeeded(await w.enable())
        await w.assert_matches_source("manual indexing on, inside the team filter")
        records = await w.records()
        assert records
        on = [(r["recordType"], r["recordName"], r["indexingStatus"]) for r in records if r["indexingStatus"] != OFF]
        assert not on, f"records indexed on their own although manual indexing is on: {on[:10]}"

    async def test_tc_lnsim_ph_001_placeholder_sweep_and_promotion(self, linear_world: LinearWorld) -> None:
        w = linear_world
        w.load(chain())
        w.scope = Scope(teams=("in", ["ENG"]))
        cut = int(time.time() * 1000) + 5_000
        narrow = {"sync": {"values": {
            **team_filter("in", ids(w, "ENG"))["sync"]["values"], **date_filter("modified", cut, None)}}}
        w.create_connector(filters=narrow)
        w.ops({"op": "advance", "ms": 20_000}, {"op": "edit_issue", "issue": "leaf", "title": "Leaf, edited"})
        assert succeeded(await w.enable())
        gp, mid, leaf = (w.ident("issues", k) for k in ("gp", "mid", "leaf"))
        graph = w.graph
        got_leaf = await graph.get_typed_record_by_external_id(w.connector_id, leaf)
        assert got_leaf is not None and got_leaf.is_placeholder is False
        stubs = {}
        for level, ext in enumerate((mid, gp), start=1):
            stub = await graph.get_typed_record_by_external_id(w.connector_id, ext)
            assert stub is not None, f"ancestor at depth {level} was not stored: the sweep did not expand that level"
            assert stub.is_placeholder is True, f"ancestor at depth {level} is out of the window and must stay a stub"
            assert stub.external_revision_id, f"ancestor at depth {level} was never backfilled by the sweep"
            assert not stub.virtual_record_id, "a stub must not be indexed"
            stubs[ext] = stub.id
        tickets = {s.external_record_id for s in await graph.get_placeholder_records(w.connector_id)
                   if s.record_type.value == "TICKET"}
        assert tickets == {mid, gp}, f"placeholders must be exactly the out-of-window ancestors: {sorted(tickets)}"
        for parent, kid in ((mid, leaf), (gp, mid)):
            assert kid in await graph.get_record_outgoing_relations(w.connector_id, parent, "PARENT_CHILD"), \
                f"the edge {parent} -> {kid} is missing"
        # Window dropped: a full sync promotes the stubs in place.
        w.scope = Scope(teams=("in", ["ENG"]))
        assert succeeded(await w.restart(filters=team_filter("in", ids(w, "ENG"))))
        assert await w.sync(full=True)
        for ext in (mid, gp):
            real = await graph.get_typed_record_by_external_id(w.connector_id, ext)
            assert real is not None and real.is_placeholder is False, f"{ext} must be promoted once it is in scope"
            assert real.id == stubs[ext], "the stub was replaced instead of promoted in place"
            assert not str(real.external_revision_id).startswith("placeholder:"), real.external_revision_id
        assert not [s for s in await graph.get_placeholder_records(w.connector_id) if s.record_type.value == "TICKET"]
        await w.assert_matches_source("after the stubs were promoted")
