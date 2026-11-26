"""One row end to end: its plan, its stack, its turn, then the oracle's and 04 §4.4's checks."""

from __future__ import annotations

import json
import os
from dataclasses import asdict
from pathlib import Path
from typing import TYPE_CHECKING

import live
import pytest
from checks import live_problems, problems
from driver import Driver, collect
from fixtures import Fixtures
from lane import StackError
from plan import plan

if TYPE_CHECKING:
    from conftest import Stacks
    from lane import Stack
    from live import LiveRun
    from observe import Observation
    from matrix import Row


def run_row(row: Row, stacks: Stacks, live_run: LiveRun | None = None) -> None:
    p = plan(row)
    if p.blocked:
        pytest.skip("; ".join(f"blocked {b.finding}: {b.reason}" for b in p.blocked))
    if live_run is not None and not live_run.takes(dict(row.factors)):
        pytest.skip("not a live row (the live variant runs the azure_openai loop rows)")
    if live_run is not None and live_run.spend.exhausted:
        pytest.exit(f"live spend cap reached: {live_run.spend.spent_usd:.2f} of {live_run.spend.cap_usd:.2f} USD")
    stack = stacks.get(row.stack)
    fixtures = Fixtures(stack, live=live.live_model_config(live_run.creds) if live_run is not None else None)
    try:
        record = Driver(stack, fixtures, p).run()
    except StackError as error:
        if "QA6-F" in str(error):
            pytest.skip(f"blocked {error}")
        raise
    observation = collect(stack, p, record)
    _keep(row, stack, observation)
    canaries = (stack.env.get("E2E_JIRA_TOKEN", ""), stack.env.get("E2E_LLM_KEY", ""))
    if live_run is not None:
        live_run.spend.charge(row.id, observation.session_events)
        found = live_problems(p, observation)
    else:
        found = problems(p, observation, canaries)
    assert not found, f"{row.id} on {row.stack}:\n  " + "\n  ".join(found) + (
        f"\n  notes: {observation.notes}" if observation.notes else "")


def _keep(row: Row, stack: Stack, observation: Observation) -> None:
    """The row's observation and the scripted model's requests, for a red row's finding (QA6_RESULTS_DIR/rows)."""
    out = Path(os.environ.get("QA6_RESULTS_DIR", str(stack.run_dir))) / "rows"
    out.mkdir(parents=True, exist_ok=True)
    body = asdict(observation)
    body["wires"] = sorted(body["wires"])
    body["log_text"] = f"({len(observation.log_text)} characters of Node's log)"
    body["modelRequests"] = (stack.model.ok("GET", "/__control/requests") or {}).get("requests")
    (out / f"{row.id}.json").write_text(json.dumps(body, indent=1, default=str))
