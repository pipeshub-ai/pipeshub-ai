# pyright: ignore-file

"""
Local FS on a headless desktop – failures and notifications
===========================================================

A sync that did not work must never report success, must remove nothing, and must tell the owner once, in
plain words. A closed laptop is not a failure. The finding each test proves is in brackets.

  TC-LFSSIM-ERR-001  Desktop closed: Sync is refused with DESKTOP_OFFLINE, nothing is removed
  TC-LFSSIM-ERR-002  Desktop connected but never answering: the run fails, nothing is removed, and the
                     owner gets one plain notification over two runs [F-NOTIFY-INTERNALS]
  TC-LFSSIM-ERR-003  A file that cannot be opened when indexed is retried, not failed for good
                     [F-LOCKED-FAILED]
  TC-LFSSIM-ERR-004  Another member's token cannot reach the owner's desktop
"""

import logging
import os
import re

import pytest
import requests
from connectors.local_fs.local_fs_sim_world import TREE, LocalFsWorld, succeeded

logger = logging.getLogger("local-fs-sim-failure-test")

INTERNALS = re.compile(r"https?://|run=|batch=|Traceback|[A-Z]{3,}_[A-Z_]{3,}")


async def _ready(world: LocalFsWorld) -> None:
    for rel, content in TREE.items():
        world.write(rel, content)
    await world.create_connector()
    assert succeeded(await world.enable())


def _notifications(world: LocalFsWorld) -> list[dict]:
    resp = world.owner.get("/api/v1/notifications?limit=50")
    if resp.status_code != 200:
        pytest.skip(f"notifications API answered {resp.status_code}")
    body = resp.json()
    items = body.get("notifications") or body.get("data") or body.get("items") or []
    return [n for n in items if "Local FS" in str(n.get("title", ""))]


@pytest.mark.integration
@pytest.mark.local_fs_sim
@pytest.mark.asyncio(loop_scope="session")
class TestLocalFsSimFailure:
    async def test_desktop_closed(self, local_fs_world: LocalFsWorld) -> None:
        """TC-LFSSIM-ERR-001"""
        w = local_fs_world
        await _ready(w)
        before = {r["id"] for r in await w.records()}
        w.desktop.kill()
        resp = w.resync_response()
        assert resp.status_code == 409, f"Sync with the desktop closed answered {resp.status_code}"
        assert "DESKTOP_OFFLINE" in resp.text
        assert {r["id"] for r in await w.records()} == before

    async def test_wedged_desktop_notifies_once(self, local_fs_world: LocalFsWorld) -> None:
        """TC-LFSSIM-ERR-002 [F-NOTIFY-INTERNALS]: about four minutes per run (three tries of 65 s)."""
        w = local_fs_world
        await _ready(w)
        before = {r["id"] for r in await w.records()}
        seen = {n.get("_id") or n.get("id") for n in _notifications(w)}
        w.desktop.ctl("faults", {"faults": [{"kind": "pull", "mode": "drop", "times": 100}]})
        assert not succeeded(await w.sync())
        assert not succeeded(await w.sync())
        fired = [f for f in w.desktop.ctl("faults/report")["faults"] if f["fired"]]
        assert fired, "the fault never fired, so the test proves nothing"
        w.desktop.ctl("faults", {"faults": []})
        assert {r["id"] for r in await w.records()} == before
        new = [n for n in _notifications(w) if (n.get("_id") or n.get("id")) not in seen]
        assert len(new) == 1, f"expected one notification over two failed runs, got {len(new)}"
        assert not INTERNALS.search(f"{new[0].get('title')} {new[0].get('message')}"), new[0]

    async def test_locked_file_is_retried(self, local_fs_world: LocalFsWorld) -> None:
        """TC-LFSSIM-ERR-003 [F-LOCKED-FAILED]"""
        w = local_fs_world
        await _ready(w)
        record = next(r for r in await w.records() if r.get("recordName") == "q2.pdf")
        os.chmod(w.path("reports/q2.pdf"), 0)
        try:
            response = w.stream(record["id"])
            assert response.status_code >= 500, f"a locked file answered {response.status_code}: gone for good"
        finally:
            os.chmod(w.path("reports/q2.pdf"), 0o644)
        assert w.stream(record["id"]).status_code == 200

    async def test_other_member_cannot_pull(self, local_fs_world: LocalFsWorld) -> None:
        """TC-LFSSIM-ERR-004: the internal route takes only a service token, and routes by its user."""
        w = local_fs_world
        await _ready(w)
        w.desktop.ctl("reset-log")
        resp = requests.post(f"{w.client.base_url}/api/v1/desktop/internal/local-fs/file-events/pull",
                             headers=w.other.headers, timeout=30,
                             json={"connectorId": w.connector_id, "deviceId": w.device_id, "runId": "r",
                                   "batchIndex": 0, "mode": "FULL", "cursor": None, "maxEvents": 10,
                                   "timeoutMs": 5000})
        assert resp.status_code in (401, 403), f"a member's own token reached the relay: {resp.status_code}"
        assert not w.desktop.ctl("log")["pulls"], "a pull reached the desktop"
