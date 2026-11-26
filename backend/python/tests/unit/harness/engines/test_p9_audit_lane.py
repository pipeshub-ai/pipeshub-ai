"""P9 AU-8 on the real lane: the grant auditor over a worker's and the model gateway's own request logs,
against Node's pinned grants (``GET /internal/runs/{runId}/audit``), as the nightly runs it.

* A clean run (its pin grants ``create`` and ``lookup``): every tool call and model call is inside the grant.
* A planted run: its pin grants ``lookup`` only, but the lane's catalog (a test-only path, not narrowed to the
  pin as the product's assembler narrows it) still serves ``create``; the engine calls it, the gateway runs it,
  and the auditor flags exactly that call.

    MONGO_IT_URI="mongodb://localhost:<port>/?replicaSet=rs0" pytest -m "docker and slow" \
        tests/unit/harness/engines/test_p9_audit_lane.py
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING

import pytest

from app.harness.audit.runner import AUDIT_CREDENTIAL_ENV, parser, run
from harness_core.effects import args_digest
from tests.unit.harness.engines.durable_stack import CREATE_ID, LOOKUP_ID
from tests.unit.harness.engines.p9_lane import mongo_uri, p9_lane
from tests.unit.harness.engines.real_stack import engine_image, lane_available

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = [pytest.mark.docker, pytest.mark.slow, pytest.mark.timeout(1200, method="thread")]


def prompt(run_id: str) -> str:
    return "\n".join((f'tool create {{"title": "{run_id}"}}', f'tool lookup {{"q": "{run_id}"}}', f"say done {run_id}"))


async def test_au8_the_auditor_is_clean_on_a_granted_run_and_flags_a_planted_call(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    available, reason = lane_available(engine_image("opencode"))
    if not available:
        pytest.skip(reason)
    uri = mongo_uri()
    if uri is None:
        pytest.skip("needs MONGO_IT_URI (a MongoDB replica set) for Node's Mongo mode")
    model_log = tmp_path / "model-gateway.log"
    handler = logging.FileHandler(model_log)
    handler.setFormatter(logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s"))
    gateway_logger = logging.getLogger("harness_core.gateway.model")
    gateway_logger.addHandler(handler)
    gateway_logger.setLevel(logging.INFO)
    try:
        async with p9_lane(tmp_path, uri=uri) as p9:
            lane = p9.lane
            monkeypatch.setenv(AUDIT_CREDENTIAL_ENV, p9.node.credential)

            def audit(*runs: str) -> list[str]:
                logs = [str(p) for r in runs for p in lane.workdir.glob(f"{r}-*.log")] + [str(model_log)]
                return [arg for log in logs for arg in ("--log", log)]

            base = ["--once", "--node-url", p9.node.base_url, "--credential-from-env"]

            await lane.admit("p9-au-clean", prompt("p9-au-clean"))
            _ = await (await lane.worker("p9-au-clean")).wait(600)
            assert (await lane.until_claimable("p9-au-clean"))["status"] == "completed"
            assert lane.saas.effects[args_digest({"title": "p9-au-clean"})] == 1
            with caplog.at_level(logging.INFO, logger="app.harness.audit"):
                clean = await run(parser().parse_args([*base, *audit("p9-au-clean")]))
            assert clean == 0, [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
            checked = [r for r in caplog.records if getattr(r, "harness_event", "") == "audit.checked"]
            assert checked and int(checked[-1].__dict__["harness"]["calls"]) >= 3, "tool and model calls were read"
            caplog.clear()

            await lane.admit("p9-au-planted", prompt("p9-au-planted"), tools=(LOOKUP_ID,))
            _ = await (await lane.worker("p9-au-planted")).wait(600)
            _ = await lane.until_claimable("p9-au-planted")
            assert lane.saas.effects[args_digest({"title": "p9-au-planted"})] == 1, "the planted call reached the tool"
            with caplog.at_level(logging.ERROR, logger="app.harness.audit"):
                planted = await run(parser().parse_args([*base, *audit("p9-au-planted")]))
            assert planted == 1
            flagged = [
                r.__dict__["harness"] for r in caplog.records if getattr(r, "harness_event", "") == "audit.violation"
            ]
            assert {(f["runId"], f["reason"], f["subject"]) for f in flagged} == {
                ("p9-au-planted", "tool_not_granted", CREATE_ID)
            }, flagged
            report = os.environ.get("P9_AUDIT_REPORT")
            if report:
                with open(report, "w") as out:
                    out.write(f'{{"clean": {clean}, "planted": {planted}, "violations": {len(flagged)}}}\n')
    finally:
        gateway_logger.removeHandler(handler)
        handler.close()
