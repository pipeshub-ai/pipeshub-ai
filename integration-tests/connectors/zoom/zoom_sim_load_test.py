# pyright: ignore-file

"""
Zoom on the simulator: what an unchanged sync costs Zoom's API
==============================================================

Zoom rates its endpoints (Light, Medium, Heavy: participants report, meeting reads), and each incremental sync lists
every active host's report from the day before their checkpoint, reading each meeting it lists again. This case
measures what that costs on an account of 50 users and 500 meetings in the window, and bounds it: an unchanged sync
reads participants, recordings and meeting settings only for the overlap with the last run, and no transcripts.

  TC-ZMSIM-LOAD-001  First sync of 50 users and 500 meetings, then an unchanged sync: calls per endpoint are logged,
                     the second reads participants, recordings and meeting settings only of the meetings of the last
                     two days (the overlap with the last run), and a page or two of the report per user [I]
"""

import datetime
import json
import logging

import pytest

from connectors.zoom.sim_instances import large
from connectors.zoom.sim_world import ZoomWorld, succeeded

logger = logging.getLogger("zoom-sim")
USERS, PER_USER = 50, 10


@pytest.mark.integration
@pytest.mark.zoom_sim
@pytest.mark.asyncio(loop_scope="session")
class TestZoomSimLoad:

    async def test_tc_zmsim_load_001_calls_of_an_unchanged_sync(self, zoom_world: ZoomWorld) -> None:
        w = zoom_world
        # Indexing off: it streams every record (participants, transcript) and would be counted as the sync's.
        off = {"indexing": {"values": {"meetings": {"operator": "is", "value": False, "type": "boolean"}}}}
        assert succeeded(await w.start(large(USERS, PER_USER), filters=off))
        assert len(await w.records()) == USERS * PER_USER
        first = w.sim.stats()["calls"]

        w.sim.call("POST", "/_sim/zoom/stats/reset")
        assert succeeded(await w.sync())
        again = w.sim.stats()["calls"]
        logger.warning("ZOOM_CALLS first sync %s", json.dumps(first, sort_keys=True))
        logger.warning("ZOOM_CALLS unchanged sync %s", json.dumps(again, sort_keys=True))
        print("ZOOM_CALLS first sync", json.dumps(first, sort_keys=True))
        print("ZOOM_CALLS unchanged sync", json.dumps(again, sort_keys=True))
        # What a run reads again is the overlap with the last one: meetings that started since the day before the
        # checkpoint (the connector's safety margin).
        today = datetime.datetime.now(datetime.UTC).date()
        overlap = sum(1 for m in w.sim.expected()["meetings"]
                      if (today - datetime.datetime.fromisoformat(m["start_time"].replace("Z", "+00:00")).date()).days <= 2)
        assert 0 < overlap < USERS * PER_USER
        assert again.get("reportMeetingParticipants", 0) <= overlap, "an unchanged sync read participants it had"
        assert again.get("recordingGet", 0) <= overlap, "an unchanged sync read recordings it had"
        assert again.get("GetMeetingTranscript", 0) == 0
        assert again.get("meeting", 0) <= overlap, "a sync of the same day read the settings of more than the overlap"
        assert again.get("reportMeetings", 0) <= USERS * 2, "more than a page or two of the report per user"
        assert again.get("users", 0) <= 2
