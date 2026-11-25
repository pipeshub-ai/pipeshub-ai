"""P9 BR-3: the workspace backup and restore drill on the real lane (the runbook's steps, automated).

A two-turn engine run crashes before its second turn on a Mongo-backed Node (database A, object store A).
The session is exported (Node's ``session-backup-cli``: its documents, sealed as stored, and the org's wrapped
data keys) and the org's objects copied; both are restored into a fresh database B and a fresh object store,
and Node is started on B. A worker resumes the run there: it completes with turn 1's work (its workspace
materializes from the restored head, objects and key) and no effect runs twice. Erasure on B then tombstones
the journal and destroys the workspace key; database A still holds the session untouched.

    MONGO_IT_URI="mongodb://localhost:<port>/?replicaSet=rs0" pytest -m "docker and slow" \
        tests/unit/harness/engines/test_p9_backup_drill.py
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
from collections import Counter
from typing import TYPE_CHECKING, Any, cast

import httpx
import pytest

from harness_core.effects import args_digest
from tests.unit.harness.engines.p9_lane import NODE_APP, ORG_ID, mongo_uri, p9_lane
from tests.unit.harness.engines.real_stack import engine_image, lane_available

if TYPE_CHECKING:
    from pathlib import Path

    from tests.unit.harness.engines.p9_lane import P9Lane

pytestmark = [pytest.mark.docker, pytest.mark.slow, pytest.mark.timeout(1800, method="thread")]

CLI = "src/modules/agent-harness/ops/session-backup-cli.ts"
RUN = "p9-br3"


def backup_cli(*args: str) -> dict[str, Any]:
    """Node's session backup CLI, as an operator runs it (ts-node here; ``node dist/…`` in an image)."""
    done = subprocess.run(
        ["npx", "ts-node", "--transpile-only", CLI, *args],
        cwd=NODE_APP,
        env={**os.environ, "TS_NODE_PROJECT": "tsconfig.json"},
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    assert done.returncode == 0, f"{args[0]} failed ({done.returncode}): {done.stdout}{done.stderr}"
    return cast("dict[str, Any]", json.loads(done.stdout.strip().splitlines()[-1]))


async def state(p9: P9Lane, session: str) -> dict[str, Any]:
    answer = await p9.lane.http.get("/session-state", params={"orgId": ORG_ID, "sessionId": session})
    return cast("dict[str, Any]", answer.raise_for_status().json())


async def move_node(p9: P9Lane, database: str) -> None:
    """Node stops and starts again on ``database`` (same port, same deployment authority)."""
    await asyncio.to_thread(p9.node.kill9)
    p9.node.database = database
    await asyncio.to_thread(p9.node.start)


def prompt(run_id: str) -> str:
    return "\n".join(
        (
            f"write notes/a.md first-turn {run_id}",
            f"write out/report.md report {run_id}",
            f'tool create {{"title": "{run_id}"}}',
            f"say first {run_id}",
        )
    )


def follow_up(run_id: str) -> str:
    return "\n".join(
        (
            f"write out/second.md second {run_id}",
            f'tool create {{"title": "{run_id}-2"}}',
            f"say second {run_id}",
        )
    )


async def test_br3_backup_restore_resume_and_erase(tmp_path: Path) -> None:
    available, reason = lane_available(engine_image("opencode"))
    if not available:
        pytest.skip(reason)
    uri = mongo_uri()
    if uri is None:
        pytest.skip("needs MONGO_IT_URI (a MongoDB replica set) for Node's Mongo mode")
    async with p9_lane(tmp_path, uri=uri) as p9:
        lane = p9.lane
        await lane.admit(RUN, prompt(RUN))
        await lane.steer(RUN, follow_up(RUN))
        session = lane.sessions[RUN]
        source_db = p9.node.database
        restored_db = f"{source_db}_restored"

        # 1. The run crashes before its second turn: turn 1's work is journaled, sealed and saved.
        crash = {"engine.kernel.before_turn_start": {"action": "kill9", "trigger": {"nth": 2}}}
        assert await (await lane.worker(RUN, arms=crash)).wait(600) is None, "the worker should have died"
        assert (await lane.until_claimable(RUN))["status"] not in {"completed", "failed"}
        before = await lane.events(RUN)
        assert Counter(e["type"] for e in before)["turn.completed"] == 1
        source = await state(p9, session)
        assert source["key"] == "present" and source["manifests"] >= 1

        # 2. Backup: the session's documents and the org's objects.
        archive = tmp_path / "archive"
        exported = await asyncio.to_thread(
            backup_cli, "export", "--mongo-uri", uri, "--db", source_db, "--org", ORG_ID, "--session", session,
            "--out", str(archive),
        )
        assert exported["counts"]["sessionEvents"] == source["events"]
        objects = lane.workdir / "objects"
        restored_objects = tmp_path / "objects-restored"
        shutil.copytree(objects / ORG_ID, restored_objects / ORG_ID)

        # 3. Restore into a fresh database and object store; Node moves to them.
        await move_node(p9, restored_db)
        restored = await asyncio.to_thread(
            backup_cli, "restore", "--mongo-uri", uri, "--db", restored_db, "--in", str(archive)
        )
        assert restored["digest"] == exported["digest"]
        objects.rename(lane.workdir / "objects-source")
        restored_objects.rename(objects)
        assert (await state(p9, session))["events"] == source["events"]

        # 4. The run resumes on the restored copy and completes; no effect runs twice.
        status = None
        for _ in range(4):
            _ = await (await lane.worker(RUN)).wait(600)
            status = (await lane.until_claimable(RUN))["status"]
            if status in {"completed", "failed", "cancelled"}:
                break
        assert status == "completed", status
        after = await lane.events(RUN)
        types = Counter(e["type"] for e in after)
        assert types["turn.completed"] == 2 and types["run.completed"] == 1
        assert lane.saas.effects[args_digest({"title": RUN})] == 1
        assert lane.saas.effects[args_digest({"title": f"{RUN}-2"})] == 1
        assert lane.saas.duplicates() == {}

        # 5. Erasure still works on the restored data.
        erased = await lane.http.post("/erase", json={"orgId": ORG_ID, "sessionId": session}, timeout=180)
        assert erased.status_code == 200, erased.text
        gone = await state(p9, session)
        assert gone["sessionErased"] and gone["key"] == "destroyed" and gone["manifests"] == 0
        assert gone["erased"] == gone["events"] - 1, gone
        with pytest.raises(httpx.HTTPStatusError):
            _ = await lane.events(RUN)

        # 6. The source database is untouched.
        _ = (await lane.http.post("/drop-database")).raise_for_status()
        await move_node(p9, source_db)
        untouched = await state(p9, session)
        assert untouched == {**source, "events": untouched["events"]} and untouched["erased"] == 0
        assert untouched["events"] == source["events"] and untouched["key"] == "present"
