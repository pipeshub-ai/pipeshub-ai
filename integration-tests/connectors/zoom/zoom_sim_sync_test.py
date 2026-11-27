# pyright: ignore-file

"""
Zoom on the simulator: what a sync holds, first and after changes
=================================================================

After each sync every person of the account must read exactly the meetings Zoom's rules give them, and every meeting
must be in its host's record group (sim_world.py). Dates are relative to the real clock.

  TC-ZMSIM-SYNC-001  First sync of an account with a recurring series (alternative host, invitees, guests, a caller
                     with no address), a slash-and-double-slash UUID, instances 2 to 147 days old, a meeting held by
                     the host alone, an inactive host's meeting, a pending user [B]
  TC-ZMSIM-SYNC-002  Odd data: markup, emoji and 400-character topics, a person listed twice, mixed-case addresses,
                     names with a pipe or a line break [B]
  TC-ZMSIM-SYNC-003  A second sync with nothing changed creates, removes and re-versions nothing [C]
  TC-ZMSIM-SYNC-004  New meeting, a new instance of a series and a new host's first meeting arrive [D]
  TC-ZMSIM-SYNC-005  A renamed meeting: its record's name and revision follow [D]
  TC-ZMSIM-SYNC-006  The window: instances 1, 29, 30, 31, 61, 150, 179, 180, 181 and 185 days old, at the edges of
                     the UTC day; a user made 20 days ago is read from then [D]
  TC-ZMSIM-SYNC-007  A checkpoint far older than one report window, and older than six months, still reads the
                     meetings Zoom lists [D]
  TC-ZMSIM-SYNC-008  Each open behaviour of Zoom, with its other value: the full sync is still the oracle's [M]
  TC-ZMSIM-SYNC-009  Pages of one: users, the report and participants take several pages; a meeting landing between
                     two report pages is held after the next sync [H]
  TC-ZMSIM-SYNC-010  A full sync of an account the first sync read changes nothing [B/F]
  TC-ZMSIM-SYNC-011  A meeting deleted at Zoom while the connector is off: its past instances stay, its alternative
                     hosts and invitees go [F]
  TC-ZMSIM-SYNC-012  A host Zoom no longer lists loses the connector, not their meetings: a participant still reads them
                     and the host does not, until a full sync; back, they read again [F/E]
  TC-ZMSIM-SYNC-013  The indexing filter "Index Meeting Transcripts" off: records stored with indexing off; on
                     later, indexing starts [G]
  TC-ZMSIM-SYNC-014  Zoom lists the report items without end time: records are still written [B]
"""

import datetime

import pytest

from connectors.zoom.zoom_sim_access_test import (
    DELETED_MEETING_KEEPS_ITS_READERS,
    LATER_ACCESS_IS_NOT_READ,
    LEAVERS_KEEP_ACCESS,
)
from connectors.zoom.sim_instances import (
    DOMAIN,
    GUEST,
    SLASHY,
    base,
    small,
    users,
)
from connectors.zoom.sim_world import ZoomWorld, succeeded  # noqa: F401


def _instance(key: str, days_ago: float, host_users: list[str] | None = None, **extra) -> dict:
    return {"key": key, "days_ago": days_ago, "participants": [{"user": u} for u in (host_users or ["ann"])], **extra}


@pytest.mark.integration
@pytest.mark.zoom_sim
@pytest.mark.asyncio(loop_scope="session")
class TestZoomSimSync:

    async def test_tc_zmsim_sync_001_first_sync(self, zoom_world: ZoomWorld) -> None:
        w = zoom_world
        assert succeeded(await w.start(base()))
        await w.assert_matches_source("after the first sync")
        records = {r["externalRecordId"]: r for r in await w.records()}
        expected = {m["uuid"]: m for m in w.sim.expected()["meetings"]}
        assert expected[SLASHY]["needs_double_encoding"] and SLASHY in records
        assert w.instance("s4")["uuid"] not in records, "a meeting of the host alone is not in Zoom's report"
        assert w.instance("g1")["uuid"] not in records, "an inactive host's meeting is not read"
        for uuid, record in records.items():
            m = expected[uuid]
            assert record["recordName"] == f"{m['topic']} ({m['start_time']})"
            assert record["webUrl"] and record["webUrl"].startswith("https://zoom.us/recording/meeting/transcript#:~:text=")

    async def test_tc_zmsim_sync_002_odd_data(self, zoom_world: ZoomWorld) -> None:
        w = zoom_world
        long_topic = "Quarterly planning " + "very long topic " * 25
        doc = small(meetings=[
            {"key": "odd", "host": "ann", "topic": "<script>alert('x')</script> & <b>bold</b>", "type": 2,
             "alt_hosts": ["bob"], "invitees": ["Cara@ACME.example", "cara@acme.example"],
             "instances": [{"key": "o1", "days_ago": 4, "participants": [
                 {"user": "bob"}, {"user": "bob"}, {"email": GUEST.upper(), "name": "Guest | Pipe\nBreak"},
                 {"email": GUEST, "name": "Guest again"}]}]},
            {"key": "emoji", "host": "bob", "topic": "Launch 🚀 café – 会议", "type": 2,
             "instances": [{"key": "e1", "days_ago": 6, "participants": [{"user": "ann"}], "transcript": "ready"}]},
            {"key": "long", "host": "cara", "topic": long_topic, "type": 2,
             "instances": [{"key": "l1", "days_ago": 8, "participants": [{"user": "ann"}]}]},
            {"key": "path", "host": "cara", "topic": "../../etc/passwd %2e%2e/ ' OR 1=1 --", "type": 2,
             "instances": [{"key": "p1", "days_ago": 9, "participants": [{"user": "ann"}], "uuid": "a/b//c+d=="}]}])
        assert succeeded(await w.start(doc))
        await w.assert_matches_source("after the first sync")
        names = {r["externalRecordId"]: r["recordName"] for r in await w.records()}
        assert len(names) == 4
        assert any("<script>" in n for n in names.values()), "a topic is stored as written, never interpreted"
        table = (await w.stream(w.instance("o1")["uuid"]))["Participants"].split("\n")
        assert len(table) == 2 + len(w.instance("o1")["participants"]), f"a guest's pipe or line break must not add cells or rows: {table}"
        assert all(row.replace("\\|", "").count("|") == table[0].count("|") for row in table)

    async def test_tc_zmsim_sync_003_unchanged_sync(self, zoom_world: ZoomWorld) -> None:
        w = zoom_world
        assert succeeded(await w.start(base()))
        await w.assert_matches_source("after the first sync")
        before = {r["externalRecordId"]: (r["id"], r["version"], r["revision"]) for r in await w.records()}
        held = await w.holdings()
        assert succeeded(await w.sync())
        assert succeeded(await w.sync(full=True))
        after = {r["externalRecordId"]: (r["id"], r["version"], r["revision"]) for r in await w.records()}
        assert after == before, "an unchanged source created, removed or re-versioned records"
        await w.assert_holdings_kept(held, "after syncs of an unchanged account")
        await w.assert_matches_source("after the unchanged syncs")

    async def test_tc_zmsim_sync_004_new_meetings(self, zoom_world: ZoomWorld) -> None:
        w = zoom_world
        assert succeeded(await w.start(small()))
        w.ops({"op": "new_meeting_ended", "meeting": "standup", "instance": _instance("s3", 1, ["cara"])},
              {"op": "new_meeting_ended",
               "new_meeting": {"key": "fresh", "host": "cara", "topic": "Fresh", "invitees": ["bob"]},
               "instance": _instance("f1", 1)},
              {"op": "add_user", "user": {"key": "fay", "first_name": "Fay"}},
              {"op": "new_meeting_ended", "new_meeting": {"key": "fays", "host": "fay", "topic": "Fay's first"},
               "instance": _instance("y1", 1, ["bob"])})
        assert succeeded(await w.sync())
        await w.assert_matches_source("after a new instance, a new meeting and a new host's first meeting")

    @LATER_ACCESS_IS_NOT_READ
    async def test_tc_zmsim_sync_005_renamed_meeting(self, zoom_world: ZoomWorld) -> None:
        w = zoom_world
        assert succeeded(await w.start(small()))
        before = await w.record_for(w.instance("r1")["uuid"])
        number = w.sim.expected()["keys"]["review"]
        w.api("PATCH", f"/meetings/{number}", {"topic": "Design review (renamed)"})
        assert succeeded(await w.sync())
        await w.assert_matches_source("after a rename")
        after = await w.record_for(w.instance("r1")["uuid"])
        assert after["id"] == before["id"] and "(renamed)" in after["recordName"]
        assert after["revision"] != before["revision"]

    async def test_tc_zmsim_sync_006_window_edges(self, zoom_world: ZoomWorld) -> None:
        w = zoom_world
        ages = [1, 29, 30, 31, 61, 150, 179, 180, 181, 185]
        edge = [{"key": "late", "days_ago": 1, "at": "23:59"}, {"key": "early", "days_ago": 2, "at": "00:00"},
                {"key": "noon", "days_ago": 3, "at": "12:00"}]
        doc = small(
            users=[{**u, "created_days_ago": 20} if u["key"] == "cara" else u for u in users()
                   if u["key"] in ("ann", "bob", "cara")],
            meetings=[{"key": "aged", "host": "ann", "topic": "Aged", "type": 8, "created_days_ago": 400,
                       "instances": [_instance(f"a{n}", n, ["bob"]) for n in ages] +
                                    [{**_instance(e["key"], e["days_ago"], ["bob"]), "at": e["at"]} for e in edge]},
                      {"key": "newcomer", "host": "cara", "topic": "Cara's", "type": 2,
                       "instances": [_instance("c1", 10, ["ann"]), _instance("c2", 15, ["ann"]),
                                     _instance("c3", 50, ["ann"])]}])
        assert succeeded(await w.start(doc))
        await w.assert_matches_source("after a first sync at the 180-day and account-age edges")
        ext = {r["externalRecordId"] for r in await w.records()}
        e = {m["key"]: m["uuid"] for m in w.sim.expected()["meetings"]}
        assert e["a180"] in ext and e["a181"] not in ext and e["a185"] not in ext
        assert e["c3"] not in ext and e["c2"] in ext, "a first sync starts the day the user's account was made"

    async def test_tc_zmsim_sync_007_checkpoint_older_than_a_window(self, zoom_world: ZoomWorld) -> None:
        w = zoom_world
        doc = small(meetings=[{"key": "m", "host": "ann", "topic": "Old", "type": 8,
                               "instances": [_instance(f"k{n}", n, ["bob"]) for n in (2, 20, 45, 100, 170)]}])
        assert succeeded(await w.start(doc))
        await w.assert_matches_source("after the first sync")
        today = datetime.datetime.now(datetime.UTC).date()
        for days in (45, 400):
            await w.set_checkpoint("ann", today - datetime.timedelta(days=days))
            w.ops({"op": "new_meeting_ended", "meeting": "m",
                   "instance": _instance(f"n{days}", 1, ["cara"])})
            assert succeeded(await w.sync()), f"a checkpoint {days} days old must not fail the sync"
            await w.assert_matches_source(f"after a sync from a checkpoint {days} days old")

    @pytest.mark.parametrize("values", [
        {"report_to_inclusive": False}, {"report_max_days": 31}, {"report_history_days": 0},
        {"uuid_double_encoding_ordinary": "refused"}, {"page_size_cap": 1}, {"page_size_cap": 2},
        {"last_page_token": "absent", "page_size_cap": 2}, {"last_page_token": "absent"},
        {"page_size_over_max": "clamp"}, {"scope_enforced": False}, {"alt_hosts_format": "id"},
        {"alt_hosts_format": "mixed"}, {"report_min_participants": 1}, {"past_instances_order": "desc"},
        {"download_format": "plain", "download_requires_bearer": False}, {"refresh_token_rotation": False},
        {"secret_in_body": "accepted"}])
    async def test_tc_zmsim_sync_008_open_behaviours(self, zoom_world: ZoomWorld, values: dict) -> None:
        w = zoom_world
        assert succeeded(await w.start(base(), behaviours=values)), f"the first sync under {values} failed"
        await w.assert_matches_source(f"after the first sync under {values}")

    async def test_tc_zmsim_sync_009_pages_and_a_meeting_between_two(self, zoom_world: ZoomWorld) -> None:
        w = zoom_world
        doc = small(meetings=[{"key": "m", "host": "ann", "topic": "Paged", "type": 8,
                               "instances": [_instance(f"p{n}", n + 1, ["bob", "cara"]) for n in range(5)]}])
        assert succeeded(await w.start(doc, behaviours={"page_size_cap": 2}))
        await w.assert_matches_source("after a first sync in pages of two")
        calls = w.sim.stats()["calls"]
        assert any(n >= 3 for op, n in calls.items() if "report" in op or "participants" in op), calls
        w.defer("/report/users/", [{"op": "new_meeting_ended", "meeting": "m", "instance": _instance("late", 1, ["cara"])}],
                after=2)
        assert succeeded(await w.sync())
        applied = w.sim.call("GET", "/_sim/zoom/deferred")["applied"]
        assert applied, "the change meant to land between two pages never did"
        assert succeeded(await w.sync())
        await w.assert_matches_source("after the meeting that landed between two pages was synced")

    async def test_tc_zmsim_sync_010_full_sync_equals_incremental(self, zoom_world: ZoomWorld) -> None:
        w = zoom_world
        assert succeeded(await w.start(base()))
        incremental = await w.holdings()
        assert succeeded(await w.sync(full=True))
        await w.assert_holdings_kept(incremental, "after a full sync of the account the first sync read")
        await w.assert_matches_source("after a full sync")

    @DELETED_MEETING_KEEPS_ITS_READERS
    async def test_tc_zmsim_sync_011_meeting_deleted_while_disabled(self, zoom_world: ZoomWorld) -> None:
        w = zoom_world
        assert succeeded(await w.start(small()))
        w.client.toggle_sync(w.connector_id, enable=False)
        await w._wait_idle()
        w.ops({"op": "meeting_deleted", "meeting": "standup"})
        assert succeeded(await w.enable())
        await w.assert_matches_source("after the series was deleted while the connector was off")
        records = {r["externalRecordId"] for r in await w.records()}
        assert w.instance("s1")["uuid"] in records, "past instances stay in Zoom's report, so they stay here"

    @LEAVERS_KEEP_ACCESS
    async def test_tc_zmsim_sync_012_user_leaves_and_returns(self, zoom_world: ZoomWorld) -> None:
        """A host who leaves loses the connector (their own access), nothing else: their meetings stay for the people
        Zoom names, until a full sync removes what is out of scope. Back, they read again."""
        w = zoom_world
        assert succeeded(await w.start(small()))
        await w.keep_after_leaving("bob")
        w.ops({"op": "user_deactivated", "user": "bob"})
        assert succeeded(await w.sync())
        await w.assert_matches_source("after Bob was deactivated: his meeting stays, for others only")
        held = await w.holdings()
        r1 = w.instance("r1")["uuid"]
        assert r1 in held["records"] and r1 in held[f"ann@{DOMAIN}"], "a participant no longer reads the departed host's meeting"
        assert r1 not in held[f"bob@{DOMAIN}"], "the departed host still reads his meeting"
        assert succeeded(await w.sync(full=True))
        w.scope.kept.clear()
        await w.assert_matches_source("after a full sync removed what is out of scope")
        w.ops({"op": "user_activated", "user": "bob"})
        assert succeeded(await w.sync())
        await w.assert_matches_source("after Bob was active again")
        assert w.instance("r1")["uuid"] in {r["externalRecordId"] for r in await w.records()}

    async def test_tc_zmsim_sync_013_indexing_filter(self, zoom_world: ZoomWorld) -> None:
        w = zoom_world
        off = {"indexing": {"values": {"meetings": {"operator": "is", "value": False, "type": "boolean"}}}}
        assert succeeded(await w.start(small(), filters=off))
        await w.assert_matches_source("with indexing off")
        assert {r["indexingStatus"] for r in await w.records()} == {"AUTO_INDEX_OFF"}
        on = {"indexing": {"values": {"meetings": {"operator": "is", "value": True, "type": "boolean"}}}}
        assert succeeded(await w.restart(filters=on))
        assert succeeded(await w.sync(full=True))
        await w.assert_matches_source("with indexing on again")
        assert "AUTO_INDEX_OFF" not in {r["indexingStatus"] for r in await w.records()}

    async def test_tc_zmsim_sync_014_report_without_end_time(self, zoom_world: ZoomWorld) -> None:
        w = zoom_world
        w.load(small())
        w.faults({"match": {"path_regex": r"^/v2/report/users/[^/]+/meetings$"},
                  "effect": {"type": "shape", "ops": [{"path": "meetings[*].end_time", "delete": True}]}})
        w.create_connector()
        await w.sign_in()
        assert succeeded(await w.enable())
        w.clear_faults()
        assert {r["externalRecordId"] for r in await w.records()} == {
            m["uuid"] for m in w.sim.expected()["meetings"] if m["synced"]}

