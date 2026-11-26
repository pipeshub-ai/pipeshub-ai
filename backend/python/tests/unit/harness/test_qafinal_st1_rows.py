"""QAFINAL-FU-ST1: the ST-1 options-matrix rows that failed on the runner's side, held without a stack.

Webhook rows: a new webhook is signed by default (TR-04), so its create answer holds ``webhookSecret``, not the
``webhookToken`` the driver read. Kill-switch rows: the ``kill_switch`` level set Labs "Agent harness" off, which is
the org's switch (routing R03, ``org_disabled``); the kill switch is the operator's env setting (CTL-5, R02).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[5]
E2E = REPO / "integration-tests" / "harness" / "e2e"
sys.path.insert(0, str(E2E))
sys.path.insert(0, str(REPO / "scripts"))

import checks  # noqa: E402
import driver  # noqa: E402
import matrix  # noqa: E402
import observe  # noqa: E402
import plan  # noqa: E402
import routing_table  # noqa: E402
from lane import StackError  # noqa: E402

import scripts  # noqa: E402

NODE_SRC = REPO / "backend" / "nodejs" / "apps" / "src" / "modules" / "agent-harness"
ROWS = matrix.all_rows()


def _node() -> str:
    node = shutil.which("node")
    if node is None:
        if os.environ.get("PXH_REQUIRE_NODE") == "1":
            pytest.fail("PXH_REQUIRE_NODE=1 but node is not on PATH")
        pytest.skip("node is not on PATH")
    return node


def _node_signature(tmp_path: Path, key: str, timestamp: str, body: bytes) -> str:
    """Node's own ``webhookSignature`` (domain/schedules.ts), run on the bytes the driver sends."""
    source = (NODE_SRC / "domain" / "schedules.ts").read_text()
    match = re.search(r"^export function webhookSignature\(.*?^}\n", source, re.DOTALL | re.MULTILINE)
    assert match is not None, "schedules.ts defines webhookSignature"
    program = tmp_path / "sign.ts"
    program.write_text(
        "import { createHmac } from 'node:crypto';\n"
        + match.group(0).removeprefix("export ")
        + f"console.log(webhookSignature({json.dumps(key)}, {json.dumps(timestamp)}, "
        f"Buffer.from({json.dumps(body.decode())}, 'utf8')));\n",
        encoding="utf-8",
    )
    run = subprocess.run(
        [_node(), "--experimental-strip-types", "--no-warnings", str(program)],
        capture_output=True, text=True, timeout=30, check=False,
    )
    assert run.returncode == 0, run.stderr
    return run.stdout.strip()


def test_st1_w1_a_new_webhook_is_signed_so_its_create_answer_holds_a_secret_not_a_token() -> None:
    service = (NODE_SRC / "application" / "schedule-service.ts").read_text()
    assert "auth: request.auth ?? 'signature'" in service, "TR-04: a new webhook is signed unless asked otherwise"


def test_st1_w2_the_driver_signs_a_delivery_as_node_verifies_it(tmp_path: Path) -> None:
    body = {"event": "qa6", "row": "PW-035", "delivery": 0}
    headers = driver.webhook_headers({"webhookSecret": "s3cr3t"}, body, now=1_762_900_000.7)
    assert headers[driver.TIMESTAMP_HEADER] == "1762900000"
    assert headers[driver.SIGNATURE_HEADER] == _node_signature(
        tmp_path, "s3cr3t", "1762900000", json.dumps(body).encode()
    )
    assert driver.TOKEN_HEADER not in headers


def test_st1_w3_the_headers_are_the_routes_own() -> None:
    routes = (NODE_SRC / "transport" / "schedule-routes.ts").read_text()
    for name, value in (("SIGNATURE", driver.SIGNATURE_HEADER), ("TIMESTAMP", driver.TIMESTAMP_HEADER),
                        ("TOKEN", driver.TOKEN_HEADER)):
        assert f"TRIGGER_{name}_HEADER = '{value}'" in routes


def test_st1_w4_a_token_webhook_takes_its_token_and_an_answer_with_neither_is_named() -> None:
    assert driver.webhook_headers({"webhookToken": "t0k"}, {}) == {driver.TOKEN_HEADER: "t0k"}
    with pytest.raises(StackError, match="no webhook credential"):
        driver.webhook_headers({"schedule": {"id": "s1"}}, {})


def test_st1_k1_the_kill_switch_level_is_the_operators_env_setting_on_node_with_labs_left_on() -> None:
    rows = [r for r in ROWS if r["path"] == "kill_switch"]
    assert rows, "the arrays hold kill-switch rows"
    for row in rows:
        p = plan.plan(row)
        assert p.process.env()["AGENT_HARNESS_KILL_SWITCH"] == "true", row.id
        assert row.expected.reason == "kill_switch", row.id
    assert not hasattr(plan.OrgSettings(), "kill_switch"), "the org's Labs switch is not the kill switch"
    assert plan.plan(next(r for r in ROWS if r["path"] == "durable")).process.env()["AGENT_HARNESS_KILL_SWITCH"] == "false"
    fixtures = (E2E / "fixtures.py").read_text()
    assert "AGENT_HARNESS_FLAG: True" in fixtures


def test_st1_k2_routing_names_the_env_switch_kill_switch_and_labs_off_org_disabled() -> None:
    """The reason a person is told: the routing table's, and Node reads the switch from its env (CTL-5)."""
    assert routing_table.e2e_route({"mode": "quick", "path": "kill_switch"}) == ("legacy", "kill_switch")
    assert routing_table.answer(routing_table.input_of({"rollout": "org_disabled"}))[1]["reason"] == "org_disabled"
    settings = (NODE_SRC / "adapters" / "configuration" / "platform-rollout-settings.ts").read_text()
    assert "envFlag('AGENT_HARNESS_KILL_SWITCH', this.environment)" in settings
    flags = (REPO / "docs" / "agent-harness-v2" / "FLAGS.md").read_text()
    assert "| flag `AGENT_HARNESS_KILL_SWITCH` | false | legacy | kill_switch |" in flags
    assert "| flag `ENABLE_AGENT_HARNESS` | true | legacy | org_disabled |" in flags


def _scripted_remember(engine: bool) -> dict[str, object]:
    script = scripts.for_row("memory", "QA6 PW-999 abc", engine=engine)
    line = next(line for line in script.lines if line.startswith("tool remember "))
    return json.loads(line.removeprefix("tool remember "))


@pytest.mark.parametrize("engine", [False, True])
def test_st1_m1_the_memory_rows_remember_passes_the_gateways_check_of_the_tools_own_schema(engine: bool) -> None:
    """PW-099: the engine's ``remember`` was refused at the gateway (``memories`` is required): the script sent the
    one-memory shape from before FIX-P1-F2, which the loop's tool still reads but the gateway's schema check does not."""
    from harness_core.gateway.arguments import ArgumentCheck
    from harness_core.memories.tool import RememberTool

    tool = RememberTool(source=None, scopes=("user",))  # type: ignore[arg-type] - the schema reads no source
    schema = tool.to_schema().input_schema
    arguments = _scripted_remember(engine)
    assert ArgumentCheck().check("remember", schema, arguments) == ()
    assert ArgumentCheck().check("remember", schema, {"content": "x", "kind": "preference", "scope": "user"}) != ()
    assert any(scripts.REMEMBERED in str(item["content"]) and "bullet" in str(item["content"])
               for item in arguments["memories"])  # type: ignore[union-attr] - the check above holds the shape


def _engine_turn(child_checkpointed_on: str) -> observe.Observation:
    """PW-050's journal: the loop's turn ``r1`` spawns the engine run ``c1`` through ``code_task``; the engine's
    workspace commit is adopted onto the turn's stream, its checkpoint stays on its own."""
    spawn = {"runId": "r1", "type": "child.spawned", "data": {"childRunId": "c1"}}
    turn = [spawn, {"runId": "r1", "type": "workspace.committed"}]
    engine = [{"runId": "c1", "type": "workspace.committed"}, {"runId": child_checkpointed_on, "type": "engine.checkpointed"}]
    obs = observe.Observation(route="durable", terminal="finished", first_frame="conversation_created")
    obs.run_id, obs.events, obs.session_events = "r1", turn, [*turn, *engine]
    return obs


def test_st1_e1_the_engine_check_reads_the_checkpoint_on_the_engine_runs_own_stream() -> None:
    check = next(c for c in checks.CHECKS if c.name == "engine set: workspace committed, checkpointed")
    p = plan.plan(next(r for r in ROWS if r.id == "PW-050"))
    assert check.applies(p, _engine_turn("c1"))
    assert check.verify(p, _engine_turn("c1")) == []
    assert check.verify(p, _engine_turn("another-run")) == ["no engine.checkpointed in the engine run's journal"]
