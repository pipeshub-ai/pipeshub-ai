"""``PARSE_LANES`` off (or unset) leaves the parse pool exactly as it was (T53).

The fixture ``fixtures/parse_pool_off_trace.json`` was recorded from the sources
before the lane pool existed (see ``parse_pool_trace``): what the legacy pool
launches, the threads it starts, the answers of ``parse_pool_workers()`` and a
hash of every top-level statement and method of ``parse_pool.py`` and
``pdf/table_detector.py``. The module hashes are compared whole, minus a named
allowlist of the entries the lane work had to touch; each of those has its
behaviour pinned by the tests below instead.
"""
from __future__ import annotations

import ast
import builtins
import io
import json
import os
import threading
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest

from app.modules.parsers import parse_pool
from app.modules.parsers.pdf import table_detector
from tests.unit.modules.parsers.parse_pool_trace import (
    FIXTURE,
    module_hashes,
    record,
    workers_grid,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

TRACE = json.loads(FIXTURE.read_text())

# What may differ from the recorded source, and why. Everything else is pinned.
PARSE_POOL_ALLOWED = {
    "import:from app.services.resource_governor.lanes import parse_lanes_mode",  # the mode switch
    "if:TYPE_CHECKING",  # a type-only import of the lane pool
    "assign:__all__",  # pool_stats
    "_State.pool", "_State.interactive_pool",  # the lane pools live beside the legacy one
    "parse_pool_workers",  # the on branch; the legacy branch is pinned by the grid below
    "_Worker.__init__",  # extra_env, empty in off
    "_Lane._start_worker", "_Lane._new_worker",  # the one-line hook
    "_pool", "submit", "shutdown_parse_pool", "pool_stats",  # mode branches, pinned by the branch tests
    "run_blocking",  # the optional cancel event, pinned by test_run_blocking_cancel_event_is_optional
    "_Job.kind", "_Job.covered", "_covered",  # the optional kind and covered fields: None and False leave the legacy pool untouched
}
TABLE_DETECTOR_ALLOWED = {
    "_session",  # reads _threads()
    "_threads", "assign:_THREADS_EXPLICIT",  # new; _THREADS and its parsing are untouched
}


def _changed(recorded: dict[str, str], current: dict[str, str]) -> set[str]:
    return {k for k in recorded.keys() | current.keys() if recorded.get(k) != current.get(k)}


def test_off_pin_covers_whole_module_except_allowlist() -> None:
    for name, module, allowed in (
        ("parse_pool.py", parse_pool, PARSE_POOL_ALLOWED),
        ("pdf/table_detector.py", table_detector, TABLE_DETECTOR_ALLOWED),
    ):
        current = module_hashes(Path(module.__file__))
        unexpected = _changed(TRACE["hashes"][name], current) - allowed
        assert not unexpected, f"{name} changed outside the allowlist: {sorted(unexpected)}"


def test_off_pin_allowlist_is_not_padded() -> None:
    """Every allowlisted entry really differs, so the list cannot hide more than it names."""
    current = module_hashes(Path(parse_pool.__file__))
    missing = {k for k in PARSE_POOL_ALLOWED if TRACE["hashes"]["parse_pool.py"].get(k) == current.get(k)}
    assert not missing, sorted(missing)


@pytest.fixture(params=["unset", "off", "empty", "junk"])
def lanes_off(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    if request.param == "unset":
        monkeypatch.delenv("PARSE_LANES", raising=False)
    else:
        monkeypatch.setenv("PARSE_LANES", {"off": "off", "empty": "", "junk": "yes"}[request.param])
    saved = parse_pool._State.governor
    yield
    parse_pool.shutdown_parse_pool()
    parse_pool._State.governor = saved


def test_off_pin_pool_branches(lanes_off: None, monkeypatch: pytest.MonkeyPatch) -> None:
    """Off builds the legacy pool for every kind of caller, and nothing that belongs to lanes."""
    from app.services.resource_governor import lanes

    def forbidden(*a: object, **k: object) -> None:
        raise AssertionError("the lane budget was touched in off")

    monkeypatch.setattr(lanes, "open_lane_budget", forbidden)
    monkeypatch.setenv("PARSE_POOL_WORKERS", "2")
    # A governor with no ``lanes`` attribute at all: reading it would raise.
    parse_pool.set_resource_governor(SimpleNamespace(ceilings=SimpleNamespace(heavy=8)))  # type: ignore[arg-type]
    before = {t.name for t in threading.enumerate()}
    pool = parse_pool._pool()
    assert isinstance(pool, parse_pool._ParsePool)
    assert parse_pool._pool(interactive=True) is pool  # interactive is ignored
    started = sorted(t.name for t in threading.enumerate() if t.name not in before)
    assert started == ["parse-pool-0", "parse-pool-1"]  # no reaper, no lane threads
    assert parse_pool._State.interactive_pool is None
    assert parse_pool.pool_stats() == {}


def test_off_pin_replays_the_recorded_trace(lanes_off: None) -> None:
    now = record(parse_pool)
    assert now["launches"] == TRACE["launches"]
    assert 2 <= now["launch_count"] <= 4
    assert now["threads"] == TRACE["threads"] == ["parse-pool-0", "parse-pool-1"]
    assert now["results"] == TRACE["results"]
    assert now["grid"] == TRACE["grid"]


def test_off_pin_workers_grid(lanes_off: None) -> None:
    grid = workers_grid(parse_pool)
    assert len(grid) == 7 * 64 and grid == TRACE["grid"]


def test_off_opens_no_file_for_lanes(lanes_off: None, monkeypatch: pytest.MonkeyPatch) -> None:
    opened: list[str] = []
    for owner, name in ((builtins, "open"), (io, "open"), (os, "open"), (os, "mkdir")):
        real = getattr(owner, name)

        def spy(path, *a, _real=real, **k) -> object:
            opened.append(str(path))
            return _real(path, *a, **k)

        monkeypatch.setattr(owner, name, spy)
    monkeypatch.setenv("PARSE_POOL_WORKERS", "1")
    parse_pool.set_resource_governor(SimpleNamespace(ceilings=SimpleNamespace(heavy=4)))  # type: ignore[arg-type]
    import asyncio

    asyncio.run(parse_pool.submit(os.getpid, label="x"))
    assert not [p for p in opened if "cpu-lanes" in p or "presence" in p]


def test_shutdown_order_in_off_is_pool_first() -> None:
    """Both services stop their pool before the governor closes (the one change in off)."""
    root = Path(__file__).parents[4] / "app"
    for name in ("parsing_main.py", "indexing_main.py"):
        tree = ast.parse((root / name).read_text())
        lifespan = next(n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef) and n.name == "lifespan")
        lines = {
            ast.unparse(n.func).rsplit(".", 1)[-1] if ast.unparse(n.func).endswith("shutdown_parse_pool") else ast.unparse(n.func): n.lineno
            for n in ast.walk(lifespan)
            if isinstance(n, ast.Call)
        }
        assert lines["shutdown_parse_pool"] < lines["governor.close"], name


def test_run_blocking_cancel_event_is_optional(lanes_off: None) -> None:
    """The one new parameter of ``run_blocking`` changes nothing unless it is given."""
    import inspect

    parameter = inspect.signature(parse_pool.run_blocking).parameters["cancelled"]
    assert parameter.default is None and parameter.kind is inspect.Parameter.KEYWORD_ONLY
    assert parse_pool.run_blocking(os.getpid, label="x") != os.getpid()  # a one-shot worker, as before
    gone = threading.Event()
    gone.set()
    with pytest.raises(parse_pool.ParseAbandonedError):
        parse_pool.run_blocking(os.getpid, label="x", cancelled=gone)


def test_kind_is_ignored_in_off(lanes_off: None) -> None:
    """A kind passed in off changes nothing: the legacy pool runs the job, the job reads no permit state."""
    from app.services.resource_governor.kinds import ParseKind
    from app.services.resource_governor.memory_domain import set_permit_covers

    token = set_permit_covers()
    try:
        assert parse_pool._covered(ParseKind.EXCEL_LOAD) is False
    finally:
        from app.services.resource_governor.memory_domain import reset_permit_covers

        reset_permit_covers(token)
    other = parse_pool.run_blocking(os.getpid, label="x", kind=ParseKind.EXCEL_LOAD)
    assert other != os.getpid()
    job = parse_pool._Job(os.getpid, (), "x", 0)
    assert (job.kind, job.covered) == (None, False)
