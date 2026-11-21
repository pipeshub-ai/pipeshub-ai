"""P9: the fault matrix's engine rows, on the real lane with a Mongo-backed Node (``p9_lane``).

One run per row, driven by worker processes until Node holds it terminal; the fault is applied while an
attempt runs. Each row asserts its documented outcome (``Oracle``): ``resume`` (completed, possibly on a later
attempt), ``degrade`` (completed on the attempt that met the fault), ``typed`` (completed, or failed with a
reason code that is not a kernel error), ``fail_closed`` (nothing ran; the run quarantined). Every row also
asserts no duplicate effect at the SaaS, no sandbox left, and a gap-free journal. ``P9_FAULT_REPORT`` names a
JSON file the rows append their results to (the nightly's table).

    MONGO_IT_URI="mongodb://localhost:<port>/?replicaSet=rs0" pytest -m "docker and slow" \
        tests/unit/harness/engines/test_p9_fault_matrix.py
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import re
import time
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import pytest

from harness_core.effects import args_digest
from tests.unit.harness.engines.durable_stack import (
    NodeOutage,
    _free_port,  # pyright: ignore[reportPrivateUsage]
)
from tests.unit.harness.engines.p9_lane import (
    MongoReplicaSet,
    RedisBox,
    mongo_uri,
    p9_lane,
)
from tests.unit.harness.engines.real_stack import engine_image, lane_available

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from tests.unit.harness.engines.durable_stack import DurableLane, Worker
    from tests.unit.harness.engines.p9_lane import P9Lane

pytestmark = [pytest.mark.docker, pytest.mark.slow, pytest.mark.timeout(1800, method="thread")]

TERMINAL = {"completed", "failed", "cancelled"}
WAITING = {"waiting_approval", "suspended"}
MAX_DRIVES = 6
# What a dead engine releases its attempt with (H1): typed by the adapter, or a lost turn the kernel saw itself.
ENGINE_GONE_REASONS = frozenset({"engine_crashed", "engine_unhealthy", "engine_turn_lost"})


def prompt(run_id: str) -> str:
    return "\n".join(
        (
            f"P9 {run_id}",
            f"write notes/a.md draft {run_id}",
            f"write out/report.md report {run_id}",
            f'tool create {{"title": "{run_id}"}}',
            f'tool lookup {{"q": "{run_id}"}}',
            f"write out/final.md final {run_id}",
            f"say finished {run_id}",
        )
    )


@dataclass
class RowResult:
    row: str
    fault: str
    oracle: str
    run_id: str
    status: str | None = None
    attempts: list[dict[str, Any] | None] = field(default_factory=list[dict[str, Any] | None])
    error_type: str | None = None
    effects: int = 0
    fired: bool = False
    observed: dict[str, Any] = field(default_factory=dict[str, Any])
    seconds: float = 0.0
    passed: bool | None = None
    failure: str | None = None


def _report(result: RowResult) -> None:
    path = os.environ.get("P9_FAULT_REPORT")
    if not path:
        return
    target = Path(path)
    rows = cast("list[dict[str, Any]]", json.loads(target.read_text())) if target.exists() else []
    rows = [r for r in rows if r.get("row") != result.row] + [asdict(result)]
    target.write_text(json.dumps(rows, indent=1, default=str))


@pytest.fixture
def uri() -> str:
    available, reason = lane_available(engine_image("opencode"))
    if not available:
        pytest.skip(reason)
    found = mongo_uri()
    if found is None:
        pytest.skip("needs MONGO_IT_URI (a MongoDB replica set) for Node's Mongo mode")
    return found


async def sandbox_up(lane: DurableLane, run_id: str, timeout_s: float = 120) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if await asyncio.to_thread(lane.sandboxes, run_id):
            return True
        await asyncio.sleep(0.3)
    return False




async def drive(
    p9: P9Lane,
    result: RowResult,
    fault: Callable[[P9Lane, str, Worker], Awaitable[bool]] | None,
    *,
    text: str | None = None,
    worker: dict[str, Any] | None = None,
    per_attempt: dict[str, Any] | None = None,
    on_waiting: Callable[[], Awaitable[None]] | None = None,
) -> list[dict[str, Any]]:
    """Admits the row's run and drives it to a terminal state; ``fault`` runs beside the first attempt."""
    lane = p9.lane
    started = time.monotonic()
    await lane.admit(result.run_id, text or prompt(result.run_id), worker=worker)
    for drive_no in range(MAX_DRIVES):
        spawned = await lane.worker(result.run_id, **{**p9.worker_overrides(), **(per_attempt or {})})
        injected = asyncio.ensure_future(fault(p9, result.run_id, spawned)) if fault is not None and drive_no == 0 else None
        result.attempts.append(await spawned.wait(900))
        if injected is not None:
            result.fired = await injected
        run = await lane.until_claimable(result.run_id)
        result.status = run["status"]
        if result.status in WAITING and on_waiting is not None:
            await on_waiting()
            result.status = (await lane.run(result.run_id))["status"]
        if result.status in TERMINAL:
            break
    result.seconds = round(time.monotonic() - started, 1)
    events = await lane.events(result.run_id)
    failed = [e for e in events if e.get("type") in {"run.failed", "run.quarantined"}]
    if failed:
        data = cast("dict[str, Any]", failed[-1].get("data") or {})
        result.error_type = str(data.get("errorType") or data.get("reason") or failed[-1]["type"])
    result.effects = lane.saas.effects[args_digest({"title": result.run_id})]
    result.observed["journal"] = dict(
        Counter(e["type"] for e in events if e["type"].startswith(("tool.", "interaction.", "workspace.", "run.")))
    )
    result.observed["tool_outcomes"] = [_data(e).get("status") for e in events if e["type"] == "tool.outcome"]
    answers = [str(_data(e).get("answer", ""))[:200] for e in events if e["type"] == "x.pipeshub.answer.finalized"]
    result.observed["answer"] = answers[-1] if answers else None
    return events


def _data(event: dict[str, Any]) -> dict[str, Any]:
    return cast("dict[str, Any]", event.get("data") or {})


async def assert_clean(lane: DurableLane, result: RowResult, events: list[dict[str, Any]]) -> None:
    assert result.fired, f"{result.row}: the fault never fired"
    assert lane.saas.duplicates() == {}, f"{result.row}: duplicate effects {lane.saas.duplicates()}"
    seqs = [int(e["seq"]) for e in events if "seq" in e]
    assert seqs == sorted(set(seqs)), f"{result.row}: journal seq not strictly increasing"
    commands = Counter(str(e["commandId"]) for e in events if e.get("commandId"))
    assert all(n == 1 for n in commands.values()) or not commands, "a command id journaled twice"
    await asyncio.sleep(1)
    assert await asyncio.to_thread(lane.sandboxes, result.run_id) == [], f"{result.row}: sandboxes left"


def assert_oracle(result: RowResult, events: list[dict[str, Any]]) -> None:
    types = Counter(e["type"] for e in events)
    if result.oracle in {"resume", "degrade"}:
        assert result.status == "completed", f"{result.row}: {result.status} {result.error_type} {result.attempts}"
        assert types["run.completed"] == 1 and types["x.pipeshub.answer.finalized"] == 1
        assert result.effects == 1 or (result.effects == 0 and types["tool.unknown"] >= 1), result.effects
        if result.oracle == "degrade":
            assert len(result.attempts) == 1, f"{result.row}: degraded to a new attempt {result.attempts}"
    elif result.oracle == "typed":
        assert result.status in TERMINAL, f"{result.row}: {result.status}"
        assert result.status == "completed" or (result.error_type and "kernel" not in result.error_type), result
        assert result.effects <= 1
    elif result.oracle == "fail_closed":
        assert result.status == "failed", f"{result.row}: {result.status}"
        assert types["run.quarantined"] == 1 or result.error_type, result
        # An engine that dies mid-turn may have run an effect before it died: at most once, never again.
        assert result.effects <= 1
        # A dead engine is a lost turn with the adapter's typed reason, never the kernel's own error (H1, FB-5).
        released = [a.get("reason") for a in result.attempts if a and a.get("status") == "released"]
        assert released and set(released) <= ENGINE_GONE_REASONS, f"{result.row}: released {released}"


async def finish(p9: P9Lane, result: RowResult, events: list[dict[str, Any]]) -> None:
    """Checks the row and reports it either way (the nightly's table lists failed rows too)."""
    try:
        await assert_clean(p9.lane, result, events)
        assert_oracle(result, events)
    except AssertionError as failed:
        result.passed, result.failure = False, str(failed)[:500]
        _report(result)
        raise
    result.passed = True
    _report(result)


# --- FM-0: positive control -------------------------------------------------------------------------------


async def test_fm0_baseline_on_mongo_node(tmp_path: Path, uri: str) -> None:
    async with p9_lane(tmp_path, uri=uri) as p9:
        result = RowResult("FM-0", "none (baseline on Mongo-backed Node)", "degrade", "p9-fm0")

        async def nothing(_p9: P9Lane, _run: str, _w: Worker) -> bool:
            return True

        events = await drive(p9, result, nothing)
        await finish(p9, result, events)


# --- FM-1: message broker down ----------------------------------------------------------------------------


async def test_fm1_message_broker_down(tmp_path: Path, uri: str) -> None:
    import docker

    client = docker.from_env()
    box = RedisBox(client, "fm1")
    try:
        async with p9_lane(tmp_path, uri=uri, broker_redis=box.address) as p9:
            box.stop()
            result = RowResult("FM-1", "message broker (Redis Streams) down across admission and run", "resume", "p9-fm1")

            async def outbox_held(p9: P9Lane, run_id: str, _w: Worker) -> bool:
                rows = (await p9.lane.http.get("/outbox", params={"runId": run_id})).json()["rows"]
                result.observed["outbox_while_down"] = rows
                return bool(rows) and all(r["status"] != "published" for r in rows)

            events = await drive(p9, result, outbox_held, per_attempt={"claim_next": True})
            box.start()
            deadline = time.monotonic() + 60
            rows: list[dict[str, Any]] = []
            while time.monotonic() < deadline:
                rows = (await p9.lane.http.get("/outbox", params={"runId": result.run_id})).json()["rows"]
                if rows and all(r["status"] == "published" for r in rows):
                    break
                await asyncio.sleep(1)
            result.observed["outbox_after_heal"] = rows
            assert rows and all(r["status"] == "published" for r in rows), rows
            await finish(p9, result, events)
    finally:
        box.remove()
        client.close()


# --- FM-2: sandbox broker down ----------------------------------------------------------------------------


async def test_fm2_sandbox_broker_down(tmp_path: Path, uri: str) -> None:
    async with p9_lane(tmp_path, uri=uri) as p9:
        broker = p9.broker_service()
        result = RowResult("FM-2", "sandbox broker service SIGKILLed mid-turn, back after 5 s", "resume", "p9-fm2")

        async def kill_broker(p9: P9Lane, run_id: str, _w: Worker) -> bool:
            if not await sandbox_up(p9.lane, run_id):
                return False
            await asyncio.sleep(2)
            await asyncio.to_thread(broker.kill9)
            await asyncio.sleep(5)
            await asyncio.to_thread(broker.start)
            return True

        events = await drive(p9, result, kill_broker)
        await finish(p9, result, events)


# --- FM-3: egress proxy down ------------------------------------------------------------------------------


async def test_fm3_egress_proxy_down(tmp_path: Path, uri: str) -> None:
    # P5.4b: the proxy keeps its bindings and grants in Redis (as app.egress_proxy_main does), so the run resumes.
    async with p9_lane(tmp_path, uri=uri, egress_state=True) as p9:
        result = RowResult("FM-3", "egress proxy container killed mid-turn, started 5 s later", "resume", "p9-fm3")
        stack = p9.lane.stack

        async def kill_proxy(p9: P9Lane, run_id: str, _w: Worker) -> bool:
            if not await sandbox_up(p9.lane, run_id):
                return False
            await asyncio.sleep(2)
            await asyncio.to_thread(stack.client.api.kill, stack.proxy)
            await asyncio.sleep(5)
            await asyncio.to_thread(stack.client.api.start, stack.proxy)
            result.observed["control_url_after"] = await asyncio.to_thread(lambda: stack.control_url)
            # CH-05: the restarted proxy reloaded the policy, not an open one: the live sandbox may still reach only
            # its gateways, so a request for any other host from it is refused.
            sandboxes = await asyncio.to_thread(p9.lane.sandboxes, run_id)
            if sandboxes:
                probed["sandbox"] = sandboxes[0].labels.get(f"{p9.lane.namespace}.id", sandboxes[0].id)
                probed["status"] = await asyncio.to_thread(_outside_status, sandboxes[0])
            return True

        probed: dict[str, Any] = {}
        events = await drive(p9, result, kill_proxy)
        result.observed["control_url_before"] = p9.lane.control_url
        logs = await asyncio.to_thread(stack.proxy_logs)
        result.observed["proxy_refusals"] = sorted(
            {part.split("=", 1)[1] for line in logs.splitlines() if "egress.refused" in line for part in line.split() if part.startswith("reason=")}
        )
        # FB-4 fixed: the restarted proxy restored the live sandbox's binding; nothing was refused unbound.
        assert "unbound_source" not in result.observed["proxy_refusals"], result.observed["proxy_refusals"]
        result.observed["outside_after_restart"] = probed
        assert probed.get("status") == "403", probed
        outside = [line for line in logs.splitlines() if "egress.refused" in line and f"host={OUTSIDE_HOST}" in line]
        assert outside, f"the restarted proxy logged no refusal for {OUTSIDE_HOST}"
        await finish(p9, result, events)


OUTSIDE_HOST = "example.org"
# From inside a sandbox (its image's node), an absolute-form request for a host its policy does not name, through the
# proxy the sandbox's hosts file names: the status the proxy answers, or "error" when nothing answered.
_OUTSIDE_PROBE = (
    "const r=require('http').get({host:'egress-proxy',port:8097,path:'http://%s/',headers:{Host:'%s'},timeout:10000},"
    "s=>{console.log(s.statusCode);s.resume()});r.on('error',e=>console.log('error '+e.code));"
    "r.on('timeout',()=>{console.log('error timeout');r.destroy()})"
) % (OUTSIDE_HOST, OUTSIDE_HOST)


def _outside_status(container: Any) -> str:  # noqa: ANN401 - a docker container
    """The proxy's answer, asked again for a few seconds while the restarted proxy's data plane comes up."""
    answer = "error"
    for _ in range(15):
        _code, output = container.exec_run(["node", "-e", _OUTSIDE_PROBE], user="1000:1000")
        lines = output.decode("utf-8", "replace").strip().splitlines() if output else []
        answer = lines[-1] if lines else "error"
        if answer.isdigit():
            return answer
        time.sleep(1)
    return answer


# --- FM-4: model gateway down -----------------------------------------------------------------------------


async def test_fm4_model_gateway_down(tmp_path: Path, uri: str) -> None:
    async with p9_lane(tmp_path, uri=uri, e3_forwarder=True) as p9:
        result = RowResult("FM-4", "model gateway unreachable for 20 s mid-turn", "resume", "p9-fm4")
        outage = p9.lane.e3_outage
        assert outage is not None

        async def cut(p9: P9Lane, run_id: str, _w: Worker) -> bool:
            if not await sandbox_up(p9.lane, run_id):
                return False
            await asyncio.sleep(1)
            outage.down()
            await asyncio.sleep(20)
            outage.up()
            return True

        events = await drive(p9, result, cut)
        await finish(p9, result, events)


# --- FM-5: tool gateway down ------------------------------------------------------------------------------


async def test_fm5_tool_gateway_down(tmp_path: Path, uri: str) -> None:
    async with p9_lane(tmp_path, uri=uri) as p9:
        result = RowResult("FM-5", "worker's tool gateway unreachable for 20 s mid-turn", "resume", "p9-fm5")
        host = p9.lane.stack.host_ip
        port, front = _free_port(host), _free_port(host)
        forwarder = NodeOutage(host, (host, port), front)
        await forwarder.start()

        async def cut(p9: P9Lane, run_id: str, _w: Worker) -> bool:
            if not await sandbox_up(p9.lane, run_id):
                return False
            await asyncio.sleep(1)
            forwarder.down()
            await asyncio.sleep(20)
            forwarder.up()
            return True

        try:
            events = await drive(p9, result, cut, per_attempt={"gateway_port": port, "gateway_public_port": front})
        finally:
            await forwarder.stop()
        await finish(p9, result, events)


# --- FM-6: Node restart -----------------------------------------------------------------------------------


async def test_fm6_node_restart(tmp_path: Path, uri: str) -> None:
    async with p9_lane(tmp_path, uri=uri) as p9:
        result = RowResult("FM-6", "Node (Mongo-backed) SIGKILLed mid-turn, started again", "resume", "p9-fm6")
        credential = p9.node.credential

        async def restart(p9: P9Lane, run_id: str, _w: Worker) -> bool:
            if not await sandbox_up(p9.lane, run_id):
                return False
            await asyncio.sleep(3)
            await asyncio.to_thread(p9.node.kill9)
            down_at = time.monotonic()
            await asyncio.sleep(2)
            await asyncio.to_thread(p9.node.start)
            result.observed["node_down_s"] = round(time.monotonic() - down_at, 1)
            return True

        events = await drive(p9, result, restart)
        assert p9.node.credential == credential, "the restarted Node changed its worker credential"
        await finish(p9, result, events)


# --- FM-7: Redis down while an approval is held -----------------------------------------------------------


async def test_fm7_redis_down_during_held_approval(tmp_path: Path, uri: str) -> None:
    import docker
    import redis.asyncio as aioredis

    from app.harness.interaction_wakeup import wake_channel

    client = docker.from_env()
    box = RedisBox(client, "fm7")
    try:
        async with p9_lane(tmp_path, uri=uri) as p9:
            result = RowResult("FM-7", "Redis (interaction wakeup) paused while an approval is held", "resume", "p9-fm7")
            lane = p9.lane

            async def decide(run_id: str) -> bool:
                deadline = time.monotonic() + 240
                while time.monotonic() < deadline:
                    events = await lane.events(run_id)
                    requested = [str(e["data"].get("interactionId")) for e in events if e["type"] == "interaction.requested"]
                    resolved = {str(e["data"].get("interactionId")) for e in events if e["type"] == "interaction.resolved"}
                    waiting = [i for i in requested if i not in resolved]
                    if waiting:
                        await asyncio.to_thread(box.pause)
                        decided_at = time.monotonic()
                        for interaction in waiting:
                            await lane.resolve(run_id, interaction, "approved")
                        # Node's wake on the decision: lost, Redis is down.
                        redis = aioredis.from_url(box.url, socket_timeout=1, socket_connect_timeout=1)  # pyright: ignore[reportUnknownMemberType]
                        try:
                            _ = await redis.publish(wake_channel(run_id), "1")  # pyright: ignore[reportUnknownMemberType]
                            result.observed["wake_published"] = True
                        except Exception as exc:
                            result.observed["wake_published"] = type(exc).__name__
                        finally:
                            with contextlib.suppress(Exception):
                                await redis.aclose()
                        result.observed["decided_after_s"] = round(decided_at - started, 1)
                        await asyncio.sleep(15)
                        await asyncio.to_thread(box.unpause)
                        return True
                    await asyncio.sleep(0.5)
                return False

            started = time.monotonic()

            async def hold(p9: P9Lane, run_id: str, _w: Worker) -> bool:
                return await decide(run_id)

            async def resolve_waiting() -> None:
                events = await lane.events(result.run_id)
                requested = [str(e["data"].get("interactionId")) for e in events if e["type"] == "interaction.requested"]
                resolved = {str(e["data"].get("interactionId")) for e in events if e["type"] == "interaction.resolved"}
                for interaction in (i for i in requested if i not in resolved):
                    await lane.resolve(result.run_id, interaction, "approved")

            events = await drive(
                p9, result, hold, worker={"approvals": "engine"}, per_attempt={"wakeup_redis": box.url},
                on_waiting=resolve_waiting,
            )
            result.observed["approvals"] = sum(1 for e in events if e["type"] == "interaction.requested")
            await finish(p9, result, events)
    finally:
        box.remove()
        client.close()


# --- FM-8: Mongo primary step-down ------------------------------------------------------------------------


async def test_fm8_mongo_primary_step_down(tmp_path: Path) -> None:
    import docker

    available, reason = lane_available(engine_image("opencode"))
    if not available:
        pytest.skip(reason)
    client = docker.from_env()
    try:
        client.images.get("mongo:8.0.17")
    except Exception:
        pytest.skip("needs mongo:8.0.17 for the three-member replica set")
    rs = await asyncio.to_thread(MongoReplicaSet, client, "fm8")
    try:
        async with p9_lane(tmp_path, uri=rs.uri) as p9:
            result = RowResult("FM-8", "Mongo primary step-down (3 members) mid-turn", "degrade", "p9-fm8")

            async def step_down(p9: P9Lane, run_id: str, _w: Worker) -> bool:
                if not await sandbox_up(p9.lane, run_id):
                    return False
                await asyncio.sleep(2)
                old, new = await asyncio.to_thread(rs.step_down)
                result.observed["primary"] = f"{old}->{new}"
                return True

            events = await drive(p9, result, step_down)
            await finish(p9, result, events)
    finally:
        rs.remove()
        client.close()


# --- FM-9: disk full in the sandbox -----------------------------------------------------------------------


async def test_fm9_disk_full_in_sandbox(tmp_path: Path, uri: str) -> None:
    async with p9_lane(tmp_path, uri=uri) as p9:
        result = RowResult("FM-9", "disk full in the sandbox (shell fills the scratch tmpfs)", "typed", "p9-fm9")
        text = "\n".join(
            (
                f"P9 {result.run_id}",
                f"write notes/a.md before {result.run_id}",
                "shell sh -c 'dd if=/dev/zero of=fill.bin bs=1M count=600 2>&1 | tail -1; df -m . | tail -1'",
                f'tool create {{"title": "{result.run_id}"}}',
                f"write out/after.md after {result.run_id}",
                f"say finished {result.run_id}",
            )
        )

        async def armed(_p9: P9Lane, _run: str, _w: Worker) -> bool:
            return True

        events = await drive(p9, result, armed, text=text)
        worker_log = "".join(p.read_text(errors="replace") for p in p9.lane.workdir.glob(f"{result.run_id}-*.log"))
        # The partial file the full disk left is never committed: the workspace sync refuses it by size.
        result.observed["skipped_too_large"] = worker_log.count("reason=file_too_large")
        result.observed["workspace_commits"] = sum(1 for e in events if e["type"] == "workspace.committed")
        assert result.observed["skipped_too_large"] >= 1, "the sandbox never filled its disk"
        await finish(p9, result, events)


async def test_fm9f_ch13_disk_full_then_freed(tmp_path: Path, uri: str) -> None:
    """CH-13: the engine's shell fills the sandbox disk and sees the plain error; once the space is freed the run goes
    on. What was written before the fill and after it is collected, the partial fill never is, the run ends with its
    answer and effect once, and its lease is given back (nothing holds the run after it ended)."""
    async with p9_lane(tmp_path, uri=uri) as p9:
        result = RowResult("FM-9f", "disk full in the sandbox, then freed (CH-13)", "degrade", "p9-fm9f")
        fill = (
            "shell sh -c 'mkdir -p out; err=$(dd if=/dev/zero of=fill.bin bs=1M count=600 2>&1 >/dev/null "
            '| grep -m1 "No space left"); rm -f fill.bin; [ -n "$err" ] && echo "$err" > out/disk-error.txt; '
            "df -m . | tail -1'"
        )
        text = "\n".join(
            (
                f"P9 {result.run_id}",
                f"write notes/a.md before {result.run_id}",
                fill,
                f'tool create {{"title": "{result.run_id}"}}',
                f"write out/after.md after {result.run_id}",
                f"say finished {result.run_id}",
            )
        )

        async def armed(_p9: P9Lane, _run: str, _w: Worker) -> bool:
            return True

        events = await drive(p9, result, armed, text=text)
        changed = {str(path) for e in events if e["type"] == "workspace.committed" for path in _data(e).get("changed", [])}
        result.observed["changed"] = sorted(changed)
        run = await p9.lane.run(result.run_id)
        result.observed["lease"] = {k: run.get(k) for k in ("workerId", "attemptId", "leaseExpiresAt")}
        await finish(p9, result, events)
        # The engine's shell got the disk's own words (ENOSPC), which it wrote down once the space was free.
        assert {"/notes/a.md", "/out/disk-error.txt", "/out/after.md"} <= changed, changed
        assert not any(path.endswith("fill.bin") for path in changed), changed
        assert result.observed["lease"] == {"workerId": None, "attemptId": None, "leaseExpiresAt": None}, result.observed


# --- FM-10: engine binary crash-looping -------------------------------------------------------------------

_KILL_ENGINE = (
    "for p in /proc/[0-9]*; do "
    "grep -q opencode $p/cmdline 2>/dev/null && kill -9 ${p#/proc/} 2>/dev/null; done; true"
)


async def crash_loop(tmp_path: Path, uri: str, result: RowResult, *, after_s: float) -> None:
    """The engine process is killed ``after_s`` after its sandbox appears, in every attempt's sandbox."""
    async with p9_lane(tmp_path, uri=uri) as p9:
        lane = p9.lane
        stop = asyncio.Event()
        seen: dict[str, float] = {}
        kills = Counter[str]()

        async def keep_killing() -> None:
            while not stop.is_set():
                for container in await asyncio.to_thread(lane.sandboxes, result.run_id):
                    first = seen.setdefault(container.name, time.monotonic())
                    if time.monotonic() - first < after_s:
                        continue
                    with contextlib.suppress(Exception):
                        out = await asyncio.to_thread(container.exec_run, ["sh", "-c", _KILL_ENGINE])
                        kills[container.name] += 1 if out.exit_code == 0 else 0
                await asyncio.sleep(0.3)

        killer = asyncio.ensure_future(keep_killing())

        async def armed(_p9: P9Lane, _run: str, _w: Worker) -> bool:
            return True

        try:
            events = await drive(p9, result, armed)
        finally:
            stop.set()
            await killer
        result.observed["sandboxes_killed_in"] = len(kills)
        result.observed["dispositions"] = [a.get("reason") if a else "died" for a in result.attempts]
        await finish(p9, result, events)


async def test_fm10_engine_crash_loop_mid_turn(tmp_path: Path, uri: str) -> None:
    result = RowResult("FM-10", "engine binary dies 3 s into every attempt", "fail_closed", "p9-fm10")
    await crash_loop(tmp_path, uri, result, after_s=3.0)


async def test_fm10s_engine_crash_loop_at_start(tmp_path: Path, uri: str) -> None:
    result = RowResult("FM-10s", "engine binary killed whenever it starts (never healthy)", "fail_closed", "p9-fm10s")
    await crash_loop(tmp_path, uri, result, after_s=0.0)


# --- FM-11: worker <-> sandbox partition (CH-12) --------------------------------------------------------------

# CH-12's two minutes: longer than an attempt's lease, so the run outlives the attempt that lost its sandbox.
PARTITION_S = 120.0


async def test_fm11_ch12_worker_sandbox_partition(tmp_path: Path, uri: str) -> None:
    """CH-12 (C2.29, ADR-OWD-11): the worker reaches its sandbox only through the sandbox broker service; that link is
    partitioned silently (bytes held both ways, no reset) for two minutes mid-turn, then heals. There is no in-sandbox
    buffer to re-attach to: the turn is re-driven from the checkpoint, the run completes with its effect once, the
    journal stays gap-free and no sandbox is left behind."""
    async with p9_lane(tmp_path, uri=uri) as p9:
        broker = p9.broker_service()
        host = "127.0.0.1"
        link = NodeOutage(host, (host, int(broker.config["port"])))
        await link.start()
        result = RowResult("FM-11", "worker<->sandbox link (broker service) silently partitioned 2 min mid-turn (CH-12)",
                           "resume", "p9-fm11")

        async def partition(p9: P9Lane, run_id: str, _w: Worker) -> bool:
            if not await sandbox_up(p9.lane, run_id):
                return False
            await asyncio.sleep(2)
            link.partition()
            await asyncio.sleep(PARTITION_S)
            link.heal()
            result.observed["partitioned_s"] = PARTITION_S
            return True

        try:
            events = await drive(p9, result, partition, per_attempt={"broker_url": link.url, "broker_token": broker.token})
        finally:
            await link.stop()
        result.observed["attempt_dispositions"] = [a.get("reason") or a.get("status") if a else "died" for a in result.attempts]
        await finish(p9, result, events)
        # FB-9: the attempt that lost its broker link waits for the sandbox (sandbox_wait), never a kernel error.
        assert "engine_kernel_error" not in result.observed["attempt_dispositions"], result.observed


def test_every_row_is_driven_once_with_a_documented_oracle() -> None:
    """The matrix's rows are this module's: each fault is driven by exactly one row, whose oracle is one the module
    docstring defines."""
    here = Path(__file__).read_text()
    rows = re.findall(r'RowResult\(\s*"(FM-\w+)",\s*"[^"]*",\s*"(\w+)"', here)
    expected = ["FM-0", *(f"FM-{n}" for n in range(1, 12)), "FM-9f", "FM-10s"]
    assert sorted(row for row, _ in rows) == sorted(expected)
    assert {oracle for _, oracle in rows} <= {"resume", "degrade", "typed", "fail_closed"}
