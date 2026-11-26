# pyright: ignore-file

"""
Zoom on the simulator: what a meeting's content is, and reindexing
==================================================================

What PipesHub streams for a meeting (its transcript and its participants table) must be what Zoom serves, and a
meeting whose transcript Zoom cannot give streams an empty transcript, never another meeting's or a stale one.

  TC-ZMSIM-CONTENT-001  Transcripts ready, not ready, expired, deleted and none, as WebVTT and as plain text: the
                        streamed transcript equals the one Zoom serves; the others are empty [J]
  TC-ZMSIM-CONTENT-002  A UUID that begins with a slash and holds two slashes streams like any other; so does one
                        with a plus and an equals sign [J]
  TC-ZMSIM-CONTENT-003  Empty and unusual transcripts: no cues, one cue, markup and emoji in the text; the streamed
                        text is Zoom's, unchanged [J]
  TC-ZMSIM-CONTENT-004  The participants table: every named participant is in it, host first, with address or "—" [J]
  TC-ZMSIM-CONTENT-005  A transcript that becomes ready after the first sync streams its text at once, and a
                        recording deleted at Zoom streams an empty transcript, with the table kept [J/D]
  TC-ZMSIM-CONTENT-007  The recording's share link is stored on the meeting's record; none when there is no recording [J]
  TC-ZMSIM-CONTENT-008  A download that redirects to another Zoom host: the transcript arrives, the token stays home [N/J]
  TC-ZMSIM-CONTENT-006  Reindex of one record, of a record after Zoom changed it, and of a record whose meeting
                        Zoom deleted: the request is accepted and the record is indexed again [K]
"""

import pytest

from connectors.zoom.sim_instances import GUEST, SLASHY, small
from connectors.zoom.sim_world import ZoomWorld, encode_uuid, succeeded


def _inst(key: str, days_ago: float, state: str, **extra) -> dict:
    return {"key": key, "days_ago": days_ago, "transcript": state, "participants": [{"user": "bob"}], **extra}


@pytest.mark.integration
@pytest.mark.zoom_sim
@pytest.mark.asyncio(loop_scope="session")
class TestZoomSimContent:

    @pytest.mark.parametrize("fmt", ["vtt", "plain"])
    async def test_tc_zmsim_content_001_transcript_states(self, zoom_world: ZoomWorld, fmt: str) -> None:
        w = zoom_world
        states = ["ready", "not_ready", "expired", "deleted", "none"]
        doc = small(meetings=[{"key": "m", "host": "ann", "topic": "States", "type": 8,
                               "instances": [_inst(f"t_{s}", n + 1, s, recording=s == "ready")
                                             for n, s in enumerate(states)]}])
        assert succeeded(await w.start(doc, behaviours={"download_format": fmt}))
        await w.assert_matches_source("after the first sync")
        for state in states:
            got = await w.stream(w.instance(f"t_{state}")["uuid"])
            if state == "ready":
                want = w.sim.http.get(f"{w.sim.base_url}/_sim/zoom/content/t_ready", timeout=30).text
                assert got["Transcript"] == want, f"the streamed {fmt} transcript differs from Zoom's"
                assert (fmt == "vtt") == got["Transcript"].startswith("WEBVTT")
            else:
                assert got["Transcript"] == "", f"a {state} transcript streamed {got['Transcript'][:40]!r}"

    async def test_tc_zmsim_content_002_awkward_uuids(self, zoom_world: ZoomWorld) -> None:
        w = zoom_world
        uuids = {"u1": SLASHY, "u2": "/leading+slash==", "u3": "mid//dle=", "u4": "p+l/us==", "u5": "plain=="}
        doc = small(meetings=[{"key": "m", "host": "ann", "topic": "Uuids", "type": 8,
                               "instances": [_inst(k, n + 1, "ready", uuid=u, recording=True)
                                             for n, (k, u) in enumerate(uuids.items())]}])
        assert succeeded(await w.start(doc))
        await w.assert_matches_source("after the first sync")
        for key, uuid in uuids.items():
            want = w.sim.http.get(f"{w.sim.base_url}/_sim/zoom/content/{key}", timeout=30).text
            got = await w.stream(uuid)
            assert got["Transcript"] == want, f"{uuid!r} did not stream its own transcript"
            assert "Bob Stone" in got["Participants"]

    async def test_tc_zmsim_content_003_unusual_transcripts(self, zoom_world: ZoomWorld) -> None:
        w = zoom_world
        cues = {"c0": [], "c1": [["Bob", "Only line"]],
                "c2": [["Bob", "<b>markup</b> & 🚀 emoji | pipe"], ["Ann", "ünïcödé 会议\ttab"]]}
        doc = small(meetings=[{"key": "m", "host": "ann", "topic": "Cues", "type": 8,
                               "instances": [_inst(k, n + 1, "ready", transcript_cues=c)
                                             for n, (k, c) in enumerate(cues.items())]}])
        assert succeeded(await w.start(doc))
        for key in cues:
            want = w.sim.http.get(f"{w.sim.base_url}/_sim/zoom/content/{key}", timeout=30).text
            assert (await w.stream(w.instance(key)["uuid"]))["Transcript"] == want, f"{key} differs from Zoom's"

    async def test_tc_zmsim_content_004_participants_table(self, zoom_world: ZoomWorld) -> None:
        w = zoom_world
        doc = small(meetings=[{"key": "m", "host": "ann", "topic": "Table", "type": 2,
                               "instances": [{"key": "t1", "days_ago": 2, "participants": [
                                   {"user": "bob"}, {"email": GUEST, "name": "Guest"}, {"name": "Phone caller"}]}]}])
        assert succeeded(await w.start(doc))
        rows = (await w.stream(w.instance("t1")["uuid"]))["Participants"].split("\n")[2:]
        assert [r.split("|")[1].strip() for r in rows] == ["Ann Owner", "Bob Stone", "Guest", "Phone caller"]
        assert [r.split("|")[2].strip() for r in rows] == ["ann@acme.example", "bob@acme.example", GUEST, "—"]

    async def test_tc_zmsim_content_005_transcript_ready_later_and_recording_deleted(self, zoom_world: ZoomWorld) -> None:
        w = zoom_world
        doc = small(meetings=[{"key": "m", "host": "ann", "topic": "Later", "type": 8,
                               "instances": [_inst("l1", 2, "not_ready"), _inst("l2", 3, "ready", recording=True)]}])
        assert succeeded(await w.start(doc))
        assert (await w.stream(w.instance("l1")["uuid"]))["Transcript"] == ""
        w.ops({"op": "transcript_ready", "instance": "l1"}, {"op": "recording_deleted", "instance": "l2"})
        want = w.sim.http.get(f"{w.sim.base_url}/_sim/zoom/content/l1", timeout=30).text
        assert (await w.stream(w.instance("l1")["uuid"]))["Transcript"] == want
        deleted = await w.stream(w.instance("l2")["uuid"])
        assert deleted["Transcript"] == "" and "Bob Stone" in deleted["Participants"]

    async def test_tc_zmsim_content_006_reindex(self, zoom_world: ZoomWorld) -> None:
        w = zoom_world
        assert succeeded(await w.start(small()))
        first = await w.record_for(w.instance("s1")["uuid"])
        assert w.client.reindex_record(first["id"]).get("success", True)
        number = w.sim.expected()["keys"]["standup"]
        w.api("PATCH", f"/meetings/{number}", {"topic": "Standup (renamed)"})
        assert succeeded(await w.sync())
        again = await w.record_for(w.instance("s1")["uuid"])
        assert w.client.reindex_record(again["id"]).get("success", True)
        w.ops({"op": "meeting_deleted", "meeting": "standup"})
        assert w.client.reindex_record(again["id"]).get("success", True)
        got = await w.stream(w.instance("s1")["uuid"])
        assert "Transcript" in got and "Participants" in got, "a record whose meeting Zoom deleted still streams its instance"

    async def test_tc_zmsim_content_007_the_recording_link(self, zoom_world: ZoomWorld) -> None:
        """The recording's share link Zoom gives is the one the meeting's record stores; a meeting without a recording
        has none."""
        w = zoom_world
        doc = small(meetings=[{"key": "m", "host": "ann", "topic": "Links", "type": 8,
                               "instances": [_inst("rec", 2, "ready", recording=True), _inst("norec", 3, "ready")]}])
        assert succeeded(await w.start(doc))
        share = w.api("GET", f"/meetings/{encode_uuid(w.instance('rec')['uuid'])}/recordings")["share_url"]
        assert share and await w.recording_url(w.instance("rec")["uuid"]) == share
        assert not await w.recording_url(w.instance("norec")["uuid"])

    async def test_tc_zmsim_content_008_a_download_that_redirects(self, zoom_world: ZoomWorld) -> None:
        """Zoom's download_url may redirect ("use curl --location") to another host: the transcript still arrives,
        and the access token does not follow it there."""
        w = zoom_world
        assert succeeded(await w.start(small(), behaviours={"download_redirect": "other_host"}))
        want = w.sim.http.get(f"{w.sim.base_url}/_sim/zoom/content/s1", timeout=30).text
        assert (await w.stream(w.instance("s1")["uuid"]))["Transcript"] == want
        stats = w.sim.stats()
        assert stats["storage_requests"] >= 1, "the download never reached the redirect's target"
        assert stats["storage_with_authorization"] == 0, "the access token was sent to the redirect's host"
