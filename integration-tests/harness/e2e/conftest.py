"""The options-matrix runner (QA-6): one test per covering-array row, grouped by stack (04 §4.5).

    cd integration-tests/harness/e2e
    python -m pytest test_pairwise.py -k ST-1                    # bring ST-1 up, run its rows, tear it down
    python -m pytest --stacks=ST-1,ST-3 --results=out/rows.txt   # two stacks, one result line per row
    python -m pytest --attach=ST-1=qa6b -k ST-1                  # rows against a stack left up (run.sh matrix-up)
    python -m pytest --mcp-hub test_mcp_kinds.py                 # MCP-E2E: a module on its own hub-on stack

Give an option its value with ``=``: pytest reads a separate value that names an existing file as a test path, before
this conftest is loaded.

Rows are ordered by stack, then by the process settings they need, so each stack comes up once and its services
restart only when the next row needs other settings. A row whose setup the lane cannot make yet is skipped as
blocked with its finding (never passed); a row that is red for a recorded finding (``known_red.json``) is a strict
xfail, so it fails the lane the day it turns green and its entry must go. Needs Docker, the stack's images and a
Python with backend/python installed (the history checks use ``harness_core``).
"""

from __future__ import annotations

import json
import os
import secrets
from collections.abc import Iterator
from pathlib import Path

import lane
import live
import matrix
import mcp_rows
import pytest
from plan import plan
from results import ResultLog

HERE = Path(__file__).resolve().parent
KNOWN_RED = json.loads((HERE / "known_red.json").read_text())["rows"]


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption("--stacks", default="", help="comma-separated stack profiles to run (default: every stack)")
    parser.addoption("--attach", default="", help="STACK=RUN pairs: use stacks left up by run.sh matrix-up")
    parser.addoption("--keep", action="store_true", help="leave the stacks this session brought up running")
    parser.addoption("--results", default=None, help="file for one result line per row (default: <tmp>/rows.txt)")
    parser.addoption("--mcp-hub", action="store_true",
                     help="opt in: the MCP-E2E modules (marker mcp_hub), each on a stack of its own with the MCP hub on")
    parser.addoption("--live", action="store_true",
                     help="opt in: azure_openai rows run on the real deployment (live.py), never by default")
    parser.addoption("--live-cap-usd", type=float, default=None, help="the live run's hard spend cap (at most 7 USD)")


def pytest_configure(config: pytest.Config) -> None:
    path = config.getoption("--results")
    config._qa6_results = ResultLog(Path(path) if path else None)  # type: ignore[attr-defined]
    config._qa6_live = None  # type: ignore[attr-defined]
    if config.getoption("--live"):
        cap = config.getoption("--live-cap-usd")
        try:
            config._qa6_live = live.LiveRun(live.spend_from(cap if cap is not None else 0.0), live.credentials())  # type: ignore[attr-defined]
        except live.LiveRefused as error:
            raise pytest.UsageError(f"--live: {error}") from None


def selected_stacks(config: pytest.Config) -> set[str]:
    chosen = {s.strip() for s in str(config.getoption("--stacks")).split(",") if s.strip()}
    return chosen or set(matrix.STACKS)


def pytest_generate_tests(metafunc: pytest.Metafunc) -> None:
    array = getattr(metafunc.module, "ARRAY", None)
    if array is None or "row" not in metafunc.fixturenames:
        return
    stacks = selected_stacks(metafunc.config)
    params = []
    for row in matrix.rows(array):
        if row.stack not in stacks:
            continue
        marks = [pytest.mark.stack(row.stack)]
        red = KNOWN_RED.get(row.id)
        if red is not None:
            marks.append(pytest.mark.xfail(strict=True, reason=f"{red['finding']}: {red['why']}"))
        params.append(pytest.param(row, id=row.test_id, marks=marks))
    metafunc.parametrize("row", params)


def _order(item: pytest.Item) -> tuple[str, str]:
    callspec = getattr(item, "callspec", None)
    row = callspec.params.get("row") if callspec is not None else None
    if not isinstance(row, matrix.Row):
        return ("", "")
    return (row.stack, json.dumps(plan(row).process.env(), sort_keys=True))


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    items.sort(key=_order)
    if not config.getoption("--mcp-hub"):
        # They bring their own stacks up (the hub on): a rows run does not pay for them unless it asks.
        skip = pytest.mark.skip(reason="the MCP-E2E modules run with --mcp-hub")
        for item in items:
            if item.get_closest_marker("mcp_hub") is not None:
                item.add_marker(skip)


class Stacks:
    """One stack up at a time: the next stack's rows start after the previous stack is torn down."""

    def __init__(self, config: pytest.Config) -> None:
        self.attach = dict(pair.split("=", 1) for pair in str(config.getoption("--attach")).split(",") if "=" in pair)
        self.keep = bool(config.getoption("--keep"))
        self.current: tuple[tuple[str, bool], lane.Stack, bool] | None = None
        self.failed: dict[tuple[str, bool], str] = {}

    def get(self, stack_id: str, *, mcp_hub: bool = False) -> lane.Stack:
        """The stack profile, brought up or attached. ``mcp_hub`` asks for one with the MCP hub on and the scripted
        MCP servers (``run.sh`` ``E2E_MCP_HUB=1``, MCP-E2E): another bring-up of the same profile."""
        key = (stack_id, mcp_hub)
        if key in self.failed:
            pytest.fail(f"{stack_id} did not come up: {self.failed[key]}", pytrace=False)
        if self.current is not None and self.current[0] == key:
            return self.current[1]
        self.close()
        run = self.attach.get(stack_id)
        owned = run is None
        if run is None:
            if stack_id in lane.HELM_STACKS and not lane.kind_available():
                pytest.skip(f"blocked QA6-F3: {stack_id} is a Helm stack and kind, kubectl or the app image "
                            f"(E2E_APP_IMAGE) is missing on this host")
            # QA6_RUN_PREFIX says whose rows stacks these are: the Compose project and database are named after the run.
            rows_prefix = os.environ.get("QA6_RUN_PREFIX", "qa6")
            prefix, extra = ("mcpe2e", mcp_rows.hub_environment()) if mcp_hub else (rows_prefix, {})
            run = f"{prefix}{stack_id.lower().replace('-', '')}{secrets.token_hex(2)}"
            try:
                lane.launch(stack_id, run, extra)
            except lane.StackError as error:
                self.failed[key] = str(error)[-1500:]
                pytest.fail(f"{stack_id} did not come up: {self.failed[key]}", pytrace=False)
        stack = lane.Stack.attach(run, stack_id)
        if mcp_hub != (stack.env.get("E2E_MCP_HUB") == "1"):
            pytest.skip(f"the attached {stack_id} run {run} was brought up with{'out' if mcp_hub else ''} E2E_MCP_HUB=1")
        self.current = (key, stack, owned)
        return stack

    def close(self) -> None:
        if self.current is not None:
            _, stack, owned = self.current
            if owned and not self.keep:
                lane.tear_down(stack.stack_id, stack.run)
            self.current = None


@pytest.fixture(scope="session")
def live_run(request: pytest.FixtureRequest) -> live.LiveRun | None:
    return request.config._qa6_live  # type: ignore[attr-defined]


@pytest.fixture(scope="session")
def stacks(request: pytest.FixtureRequest) -> Iterator[Stacks]:
    manager = Stacks(request.config)
    yield manager
    manager.close()


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item: pytest.Item, call: pytest.CallInfo[None]):  # noqa: ANN201 - pytest hook
    outcome = yield
    report = outcome.get_result()
    callspec = getattr(item, "callspec", None)
    row = callspec.params.get("row") if callspec is not None else None
    if isinstance(row, matrix.Row):
        item.config._qa6_results.record(row, report)  # type: ignore[attr-defined]


def pytest_sessionfinish(session: pytest.Session) -> None:
    log: ResultLog = session.config._qa6_results  # type: ignore[attr-defined]
    target = log.write(Path(os.environ.get("QA6_RESULTS_DIR", str(lane.STATE_DIR))) / "qa6-rows.txt")
    terminal = session.config.pluginmanager.get_plugin("terminalreporter")
    if terminal is not None:
        terminal.write_sep("-", f"one result line per row: {target}")
        for line in log.lines():
            terminal.write_line(line)
