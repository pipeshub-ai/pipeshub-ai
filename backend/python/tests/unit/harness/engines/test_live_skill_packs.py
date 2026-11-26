"""LV-1..LV-4 (SK1): the pptx and pdf skills on opencode and Codex with a live model (Azure OpenAI through E3).

The real lane of ``test_real_skill_packs.py`` with E3 as ``app.model_gateway_main`` wires it (Node's grant and
budget routes faked as in EV-2), calling the live Azure deployment. Prompts are plain requests: they do not
name the skills, so whether an engine picks a skill up is part of what is measured. A dry run (one minimal turn
per engine) prices the lane first and stops it when the projection exceeds ``SK1_LIVE_MAX_USD`` (default 5);
every run's budget is pinned in E3's ledger as well.

Runs only with the Azure test variables (``HARNESS_LIVE_ENV_FILE``) and ``SK1_SKILLS_DIR``. Prints one JSON line
per task (``SK1-LIVE``) and writes them to ``SK1_LIVE_REPORT`` when set. Values of secrets are never printed.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, override

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from app.model_gateway_main import create_gateway
from harness_core.access.types import AccessRouteRef, PinnedRoute, route_digest
from harness_core.budget.memory import InMemoryBudgetLedger
from harness_core.budget.types import RunAccountRef
from harness_core.clock import SystemClock
from harness_core.engines.events import UsageReported, turn_id_for
from harness_core.engines.types import EngineTask
from harness_core.gateway.model_server import create_model_gateway_app
from tests.unit.harness.access.budget_node import BudgetNode
from tests.unit.harness.access.support import StackConfig, published
from tests.unit.harness.engines.live_lane import (
    MODEL_KEY,
    ORG,
    LiveNode,
    _ai_models,  # pyright: ignore[reportPrivateUsage]
    _model,  # pyright: ignore[reportPrivateUsage]
    live_env,  # noqa: F401 - a fixture
)
from tests.unit.harness.engines.real_stack import GatewayRequest
from tests.unit.harness.engines.test_real_skill_packs import (
    _E3_PORT,  # pyright: ignore[reportPrivateUsage]
    ENGINES,
    PLACEMENTS,
    SkillLane,
    check_deck,
    check_report,
    inputs,
    placing_broker,
    real_stack,  # noqa: F401 - a fixture
    skill_reads,
    skill_scripts_run,
    skills_tree,  # noqa: F401 - a fixture
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Mapping

    from tests.unit.harness.engines.real_stack import RealStack

pytestmark = [pytest.mark.docker, pytest.mark.integration, pytest.mark.slow, pytest.mark.timeout(5400, method="thread")]

MAX_USD = float(os.environ.get("SK1_LIVE_MAX_USD") or 5)
# Projection from one minimal turn: 2 tasks x 2 turns x up to 25 model calls, each carrying about twice the
# dry turn's context on average.
PROJECTION_FACTOR = 2 * 2 * 25 * 2
RUNTIME = (
    "The sandbox has no network access. Python 3.12 is installed with python-pptx, pypdf, reportlab, defusedxml, "
    "lxml and Pillow; Node.js, LibreOffice and Poppler are not available, and nothing can be installed."
)
TASKS: Mapping[str, tuple[str, str]] = {
    "pptx": (
        "Create a 5-slide PowerPoint deck at out/deck.pptx from the brief in in/brief.md: a title slide, then one "
        "slide per section of the brief with the section's heading as the slide title; the regional revenue section "
        f"must be a table with the regions and their revenue. {RUNTIME}",
        "Update out/deck.pptx in place: add a sixth slide at the end titled 'Appendix: method' that says the "
        "figures come from the finance export of October 2, and change East's revenue in the table from 143 to "
        "151. Leave every other slide exactly as it is.",
    ),
    "pdf": (
        "Write a 3-page PDF report at out/report.pdf titled 'Q3 Operations Report', one section per page: "
        "page 1 '1. Summary' saying 'Revenue grew 12 percent to 446 thousand USD in the third quarter.', page 2 "
        "'2. Incidents' saying 'Four incidents were declared; the mean time to recover was 38 minutes.', page 3 "
        "'3. Outlook' saying 'The fourth quarter plan holds headcount flat and targets 99.95 percent uptime.' "
        f"{RUNTIME}",
        "Update the report: append the page of in/appendix.pdf to the end of out/report.pdf, keeping pages 1-3 "
        "unchanged; fill the form in/form.pdf with reviewer 'Dana Reyes' and approved total '446' and save it as "
        "out/form-filled.pdf; extract the full text of the updated report to out/report.txt.",
    ),
}


@dataclass
class EngineLiveNode(LiveNode):
    """EV-2's fake of Node's grant routes, naming each run's engine (E3 checks the engine's model API)."""

    engines: dict[str, str] = field(default_factory=dict[str, str])

    @override
    def grant_run(self, run_id: str, attempt_id: str) -> None:
        super().grant_run(run_id, attempt_id)
        self.grants[run_id][1]["engine"] = self.engines[run_id]

    async def charged(self, run_id: str) -> float:
        left = await self.budget.ledger.remaining(RunAccountRef(ORG, run_id))
        return left.committed.usd_micros / 1e6


@contextlib.asynccontextmanager
async def live_gateway(stack: RealStack, node: EngineLiveNode) -> AsyncIterator[list[GatewayRequest]]:
    """E3 as the model gateway role builds it, on the port the proxy's static upstream names; the requests it
    received are recorded (for the listing evidence; bodies only, no headers)."""
    node_server = TestServer(node.app(), host="127.0.0.1")
    await node_server.start_server()
    config = StackConfig(_ai_models(), await published(stack.models.mint))
    gateway = await create_gateway(config, node_url=str(node_server.make_url("")).rstrip("/"))  # type: ignore[arg-type]
    requests: list[GatewayRequest] = []

    @web.middleware
    async def record(request: web.Request, handler: Any) -> web.StreamResponse:  # noqa: ANN401
        raw = await request.read()
        body: dict[str, Any] = {}
        with contextlib.suppress(ValueError):
            body = json.loads(raw) if raw else {}
        requests.append(GatewayRequest(request.method, request.path, "", body))
        return await handler(request)

    app = create_model_gateway_app(gateway)
    app.middlewares.append(record)
    runner = web.AppRunner(app, access_log=None, handler_cancellation=True)
    await runner.setup()
    await web.TCPSite(runner, stack.host_ip, _E3_PORT["port"], reuse_address=True).start()
    try:
        yield requests
    finally:
        await runner.cleanup()
        await node_server.close()


def _lane(engine: str, stack: RealStack, broker: Any, node: EngineLiveNode, route: PinnedRoute, task: str) -> SkillLane:  # noqa: ANN401
    run_id = f"sk1live-{engine}-{task}-{uuid.uuid4().hex[:6]}"
    node.engines[run_id] = engine
    return SkillLane(
        engine, stack, broker, bind_run=node.grant_run, run_id=run_id, routes_digest=route_digest((route,)),
        access=AccessRouteRef("route-azure-live", "d1"),
    )


def _listed(requests: list[GatewayRequest], engine: str) -> bool:
    dest = PLACEMENTS[engine].dest
    text = json.dumps([r.body for r in requests[:1]])
    return "Use this skill whenever the user wants to do anything with PDF files" in text and dest in text


@pytest.mark.usefixtures("live_env")
async def test_lv_pptx_and_pdf_skills_on_both_engines_live(
    stack: RealStack, skills_tree: dict[str, dict[str, bytes]]  # noqa: F811 - the imported fixture
) -> None:
    key = os.environ["TEST_AZURE_OPENAI_API_KEY"]
    route = PinnedRoute("azureopenai", _model(), config_key=MODEL_KEY, config_model=_model())
    node = EngineLiveNode(issued=stack.models.issued, route=route, budget=BudgetNode(InMemoryBudgetLedger(SystemClock()), ORG))
    rows: list[dict[str, Any]] = []
    async with live_gateway(stack, node) as requests:
        dry: dict[str, float] = {}
        for engine in ENGINES:
            async with placing_broker(stack, engine, skills_tree[engine]) as broker:
                lane = _lane(engine, stack, broker, node, route, "dry")
                start = len(requests)
                outcome = await lane.controller.run_turn(lane.context(1), EngineTask(turn_id_for(lane.run_id, 0), "Reply with the single word: ready."))
                dry[engine] = await node.charged(lane.run_id)
                rows.append({"engine": engine, "task": "dry", "reason": outcome.reason, "usd": dry[engine],
                             "listed": _listed(requests[start:], engine)})
        projected = sum(dry.values()) * PROJECTION_FACTOR
        print(f"\nSK1-LIVE dry {json.dumps(dry)} projected_usd={projected:.4f} cap={MAX_USD}")
        if projected + sum(dry.values()) > MAX_USD:
            pytest.fail(f"projected USD {projected:.2f} exceeds the cap {MAX_USD}: the live tasks were not run")
        for engine in ENGINES:
            for task, (create, update) in TASKS.items():
                async with placing_broker(stack, engine, skills_tree[engine]) as broker:
                    lane = _lane(engine, stack, broker, node, route, task)
                    await lane.seed(inputs())
                    start = len(requests)
                    try:
                        first, after_first, second = await lane.two_turns(create, update)
                        reasons = [first.reason, second.reason]
                        published = await lane.published()
                        failed = (check_deck if task == "pptx" else check_report)(published, after_first)
                        error = None
                    except Exception as exc:  # recorded: a live engine may fail a task
                        reasons, published, failed, error = [], {}, ["crashed"], f"{type(exc).__name__}: {exc}"[:300]
                if keep := os.environ.get("SK1_LIVE_ARTIFACTS"):
                    folder = Path(keep) / f"{engine}-{task}"
                    folder.mkdir(parents=True, exist_ok=True)
                    for name, data in published.items():
                        (folder / name.replace("@", ".v")).write_bytes(data)
                    (folder / "transcript.txt").write_text(lane.transcript())
                usage = [e for e in lane.events() if isinstance(e, UsageReported)]
                commands = lane.commands()
                rows.append(
                    {
                        "engine": engine,
                        "task": task,
                        "reasons": reasons,
                        "failed": failed,
                        "versions": sorted(published),
                        "listed": _listed(requests[start:], engine),
                        "skill_reads": len(skill_reads(lane, task)),
                        "skill_scripts": [(re.findall(r"scripts/[\w/]+\.py", c) or [c[:40]])[0] + f" -> {code}" for c, code in skill_scripts_run(lane, task)],
                        "installs": [c[:80] for c, _ in commands if "pip install" in c or "npm install" in c],
                        "commands": len(commands),
                        "model_calls": len(usage),
                        "usd": await node.charged(lane.run_id),
                        "error": error,
                    }
                )
    total = sum(float(r["usd"]) for r in rows)
    for row in rows:
        print(f"\nSK1-LIVE {json.dumps(row)}")
    print(f"\nSK1-LIVE total_usd={total:.4f}")
    if report := os.environ.get("SK1_LIVE_REPORT"):
        Path(report).write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    assert total <= MAX_USD
    assert key not in json.dumps(rows)

