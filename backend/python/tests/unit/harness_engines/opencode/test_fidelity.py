"""OC-10: the recorded fake opencode emits the captured event sequences."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from harness_core.engines.jsonview import as_object, as_str
from harness_core.sandbox.tar import tar_stream as tar_of
from harness_core.testing.engines.contract import ENDPOINTS, SKILLS_DIR, make_profile
from harness_core.testing.engines.gateway import ConformanceGateway, GrantedTool
from harness_core.testing.sandbox.egress import FakeEgress
from harness_engines.opencode_api.http import OpencodeHttp
from tests.unit.harness_engines.opencode.conftest import OpencodeHarness

if TYPE_CHECKING:
    from collections.abc import Mapping

    from harness_core.engines.types import JsonValue

FIXTURES = Path(__file__).parent / "fixtures"
NOISE = {
    "server.connected",
    "plugin.added",
    "catalog.updated",
    "integration.updated",
    "reference.updated",
    "session.updated",
    "session.diff",
}


def project(events: list[Mapping[str, JsonValue]], session: str) -> list[tuple[str, ...]]:
    """The order-significant shape of a root session's events: types, roles, part kinds, statuses."""
    out: list[tuple[str, ...]] = []
    for event in events:
        kind = as_str(event.get("type"))
        props = as_object(event.get("properties"))
        if kind in NOISE or as_str(props.get("sessionID")) != session:
            continue
        if kind == "message.updated":
            info = as_object(props.get("info"))
            done = "completed" if as_object(info.get("time")).get("completed") else ""
            out.append((kind, as_str(info.get("role")), as_str(info.get("finish")), done))
        elif kind == "message.part.updated":
            part = as_object(props.get("part"))
            ended = "end" if as_object(part.get("time")).get("end") else ""
            out.append((kind, as_str(part.get("type")), as_str(as_object(part.get("state")).get("status")), ended))
        elif kind == "session.status":
            out.append((kind, as_str(as_object(props.get("status")).get("type"))))
        else:
            out.append((kind,))
    return out


def captured(name: str) -> list[tuple[str, ...]]:
    lines = (FIXTURES / name).read_text().splitlines()
    return project([json.loads(line) for line in lines], "ses_001")


async def _drive(prompt: str, *, permission: list[JsonValue] | None = None, reply: str | None = None) -> list[Mapping[str, JsonValue]]:
    harness = OpencodeHarness()
    egress = FakeEgress()
    ConformanceGateway(endpoints=ENDPOINTS, grant=(GrantedTool("search"),)).install(egress)
    sandbox = await harness.new_sandbox(egress)
    await sandbox.put_tar(SKILLS_DIR, tar_of({"report/SKILL.md": b"---\nname: report\n---\n"}))
    launch = harness.adapter.render_launch(make_profile(harness), ENDPOINTS, SKILLS_DIR)
    env = {**launch.env, "OPENCODE_SERVER_PASSWORD": "pw"}
    process = await sandbox.exec(list(launch.argv), env=env)
    await asyncio.sleep(0)
    http = OpencodeHttp(sandbox, 4096, "pw")
    seen: list[Mapping[str, JsonValue]] = []
    idle = asyncio.Event()
    connected = asyncio.Event()

    async def tap() -> None:
        async for event in http.events():
            seen.append(event)
            connected.set()
            kind = as_str(event.get("type"))
            props = as_object(event.get("properties"))
            if kind == "permission.asked" and reply is not None:
                await http.request("POST", f"/permission/{as_str(props.get('id'))}/reply", {"reply": reply})
            if kind == "session.idle" and as_str(props.get("sessionID")) == "ses_001":
                idle.set()

    reader = asyncio.get_running_loop().create_task(tap())
    await asyncio.wait_for(connected.wait(), 10)
    body: dict[str, JsonValue] = {"title": "probe"}
    if permission is not None:
        body["permission"] = permission
    session = as_object(await http.request("POST", "/session", body))
    await http.request(
        "POST", f"/session/{as_str(session.get('id'))}/prompt_async", {"parts": [{"type": "text", "text": prompt}]}
    )
    await asyncio.wait_for(idle.wait(), 10)
    await asyncio.sleep(0.05)
    reader.cancel()
    await http.aclose()
    await process.signal(15)
    return seen


@pytest.mark.parametrize(
    ("capture", "prompt", "permission", "reply"),
    [
        ("native-locked-mcp_call.events.jsonl", 'tool search {"query": "q4 revenue"}\nsay done', None, None),
        ("native-locked-title.events.jsonl", "say ok", None, None),
        (
            "native-locked-bash_ask.events.jsonl",
            "shell echo probe > probe.txt\nsay done",
            [{"permission": "bash", "pattern": "*", "action": "ask"}],
            "once",
        ),
    ],
)
async def test_oc10_fake_matches_the_captured_sequence(
    capture: str, prompt: str, permission: list[JsonValue] | None, reply: str | None
) -> None:
    events = await _drive(prompt, permission=permission, reply=reply)
    assert project(events, "ses_001") == captured(capture)
