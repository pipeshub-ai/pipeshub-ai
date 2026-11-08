import asyncio
from unittest.mock import MagicMock

from app.core.db import shared


def test_the_databases_of_a_closed_loop_are_terminated_and_a_live_loops_are_kept() -> None:
    dead = asyncio.new_event_loop()
    dead.close()
    live = asyncio.new_event_loop()
    try:
        dead_db, live_db = MagicMock(), MagicMock()
        cfg = MagicMock()
        shared._entries[(cfg, dead)] = shared._Entry(dead_db, 1)
        shared._entries[(cfg, live)] = shared._Entry(live_db, 1)
        shared._prune_closed()
        dead_db.terminate.assert_called_once()
        live_db.terminate.assert_not_called()
        assert (cfg, dead) not in shared._entries and (cfg, live) in shared._entries
    finally:
        shared._entries.pop((cfg, live), None)
        shared._entries.pop((cfg, dead), None)
        live.close()
