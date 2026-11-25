"""WALLCLOCK-1: a unit test does not hold the host's clock to a fixed bound without saying why.

Four tests failed only in full gates on the shared host and passed alone (CF-09's 2.28 s, GW-6's httpx 5 s, the
langchain stream's 3.56 s CPU "stall", LS-6's 5.33 s render): each compared a reading of the host's clock to a
number. ``tests/cohesion/wallclock.py`` has the rule and what to use instead. Integration, perf and eval suites
are out of scope: they run against real services, in lanes of their own.
"""

from __future__ import annotations

import re

import pytest

from tests.cohesion import wallclock
from tests.cohesion.support import PYTHON_ROOT, REPO_ROOT

_PYTHON_SUITES = ("unit", "cohesion", "contracts", "harness_replay")
_NODE_TESTS = REPO_ROOT / "backend" / "nodejs" / "apps" / "tests"


def _python_offenders() -> list[str]:
    found: list[str] = []
    for suite in _PYTHON_SUITES:
        for path in sorted((PYTHON_ROOT / "tests" / suite).rglob("test_*.py")):
            text = path.read_text(encoding="utf-8")
            if "time" not in text and "wait_for" not in text:
                continue
            rel = path.relative_to(PYTHON_ROOT).as_posix()
            found.extend(f"{rel}:{f.line}: {f.source}" for f in wallclock.findings(text))
    return found


def _node_offenders() -> list[str]:
    found: list[str] = []
    for path in sorted(_NODE_TESTS.rglob("*.test.ts")):
        text = path.read_text(encoding="utf-8")
        rel = path.relative_to(REPO_ROOT).as_posix()
        found.extend(f"{rel}:{f.line}: {f.source}" for f in wallclock.node_findings(text))
    return found


def test_wallclock_1_python_unit_tests_bound_no_host_clock() -> None:
    offenders = _python_offenders()
    assert not offenders, (
        "these assertions hold a host-clock duration (or a wait that expects completion) to a fixed bound, which a "
        "busy gate host breaks: assert the code's own clock (VirtualTimeLoop, ManualClock), an order or a count; "
        "use tests.support.settle for a wait that only guards a hang and tests.support.cpu_budget for a CPU-work "
        "guard; mark a real-time benchmark slow (C-25); or add `# wall-clock-ok: <why>` where the bound is "
        "generous:\n" + "\n".join(offenders)
    )


def test_wallclock_1_node_unit_tests_bound_no_host_clock() -> None:
    offenders = _node_offenders()
    assert not offenders, (
        "these expectations hold a Date.now()/performance.now()/hrtime duration to a fixed upper bound: use sinon's "
        "fake timers or an injected clock, assert an order or a count, or add `// wall-clock-ok: <why>` where the "
        "bound is generous:\n" + "\n".join(offenders)
    )


@pytest.mark.parametrize(
    "source",
    [
        "def test():\n    started = time.monotonic()\n    work()\n    assert time.monotonic() - started < 1.0\n",
        "def test():\n    t0 = time.perf_counter()\n    work()\n    elapsed = time.perf_counter() - t0\n    assert elapsed < 2\n",
        "def test():\n    s = time.process_time()\n    work()\n    assert 5 > time.process_time() - s\n",
        "def test():\n    took = []\n    for _ in r:\n        a = time.monotonic()\n        took.append(time.monotonic() - a)\n"
        "    assert max(took) < 0.1\n",
        "from time import monotonic\ndef test():\n    a = monotonic()\n    assert 0.9 < monotonic() - a < 3\n",
        "async def test():\n    loop = asyncio.get_running_loop()\n    s = loop.time()\n    await x()\n"
        "    assert loop.time() - s < 1\n",
        "async def test():\n    await asyncio.wait_for(task, timeout=1)\n",
        "def test(self):\n    s = time.time()\n    self.assertLess(time.time() - s, 1)\n",
        # A helper that returns a duration, alone or in a tuple.
        "async def closed_within(r):\n    s = time.monotonic()\n    await r.read()\n    return time.monotonic() - s\n"
        "async def test():\n    assert await closed_within(r) < 1.5\n",
        "async def run():\n    s = time.monotonic()\n    w = await go()\n    return time.monotonic() - s, w\n"
        "async def test():\n    elapsed, w = await run()\n    assert elapsed < 5\n",
    ],
)
def test_wallclock_1_the_rule_sees_a_host_clock_bound(source: str) -> None:
    assert wallclock.findings(source), source


@pytest.mark.parametrize(
    "source",
    [
        # A lower bound: load only lengthens a duration.
        "def test():\n    s = time.monotonic()\n    work()\n    assert time.monotonic() - s >= 1.5\n",
        # Two durations compared: relative.
        "def test():\n    a = time.monotonic()\n    f()\n    fa = time.monotonic() - a\n    b = time.monotonic()\n"
        "    g()\n    gb = time.monotonic() - b\n    assert fa < gb * 2\n",
        # A point in time, not a duration: the code under test reads the same clock.
        "def test():\n    deadline = time.time() - 30\n    assert abs(code() - deadline) < 60\n",
        # The size of a list of durations is a count.
        "def test():\n    lags = []\n    a = time.monotonic()\n    lags.append(time.monotonic() - a)\n    assert len(lags) > 10\n",
        # A wait that expects a timeout.
        "async def test():\n    with pytest.raises(asyncio.TimeoutError):\n        await asyncio.wait_for(never, 0.1)\n",
        "async def test():\n    await asyncio.wait_for(task, SETTLE_S)\n",
        # The virtual loop's clock is the code's own.
        "def test():\n    async def main():\n        loop = asyncio.get_running_loop()\n        s = loop.time()\n"
        "        await asyncio.wait_for(x(), 1)\n        assert loop.time() - s < 1\n    run_on_virtual_time(main)\n",
        # main's loop clock (tests/support/loop_clock.py) is the code's own too.
        "async def test(monkeypatch):\n    virtual_loop_time(monkeypatch)\n    loop = asyncio.get_running_loop()\n"
        "    s = loop.time()\n    await asyncio.wait_for(x(), 1)\n    assert loop.time() - s < 1\n",
        # A slow gate run, where a real-time bound is the point.
        "@pytest.mark." "slow\ndef test():\n    s = time.monotonic()\n    assert time.monotonic() - s < 1\n",
        "pytestmark = pytest.mark." "slow\ndef test():\n    s = time.monotonic()\n    assert time.monotonic() - s < 1\n",
        # A reasoned opt-out.
        "def test():\n    s = time.monotonic()\n    # wall-clock-ok: the hang is 60 s, the fast path is ms\n"
        "    assert time.monotonic() - s < 20\n",
        # One test's clock reading is not another's.
        "def test_a():\n    started = time.monotonic()\n\ndef test_b():\n    started = 3\n    assert started - 1 < 5\n",
        # An attribute set from the clock is not a name the scan follows.
        "def test():\n    svc.last = time.time() - 3\n    delay = svc.delay()\n    assert delay < 5\n",
        # A rate held below a bound is a duration held above one.
        "def test():\n    s = time.monotonic()\n    go()\n    elapsed = time.monotonic() - s\n"
        "    assert PAGES / elapsed <= 0.5 * RATE\n",
        # The other values a duration helper returns are not durations.
        "async def run():\n    s = time.monotonic()\n    w = await go()\n    return time.monotonic() - s, w\n"
        "async def test():\n    elapsed, w = await run()\n    assert set(w.kinds) <= {'a'}\n",
    ],
)
def test_wallclock_1_the_rule_leaves_what_a_load_cannot_break(source: str) -> None:
    assert not wallclock.findings(source), source


@pytest.mark.parametrize(
    "source",
    [
        "const t0 = Date.now()\nawait run()\nexpect(Date.now() - t0).to.be.lessThan(150)\n",
        "const started = performance.now()\nrun()\nconst elapsedMs = performance.now() - started\n"
        "expect(elapsedMs).to.be.below(1000)\n",
        "const s = process.hrtime.bigint()\nrun()\nconst ms = Number(process.hrtime.bigint() - s) / 1e6\n"
        "expect(ms).to.be.below(20)\n",
        "const t = Date.now()\nawait run()\nassert.ok(Date.now() - t < 2000)\n",
    ],
)
def test_wallclock_1_the_node_rule_sees_a_host_clock_bound(source: str) -> None:
    assert wallclock.node_findings(source), source


@pytest.mark.parametrize(
    "source",
    [
        "const t = Date.now()\nawait run()\nexpect(Date.now() - t).to.be.at.least(25)\n",
        "row.expiresAt = new Date(Date.now() - 1000)\nexpect(svc.expired(row)).to.equal(true)\n",
        "const t = Date.now()\n// wall-clock-ok: the hang is 30 s, the fast path is ms\n"
        "expect(Date.now() - t).to.be.lessThan(10000)\n",
        "clock.tick(500)\nexpect(Date.now() - start).to.equal(500)\n",
    ],
)
def test_wallclock_1_the_node_rule_leaves_what_a_load_cannot_break(source: str) -> None:
    assert not wallclock.node_findings(source), source


def test_wallclock_1_an_opt_out_gives_its_reason() -> None:
    assert wallclock.OPT_OUT.search("x  # wall-clock-ok: the hang is 60 s")
    assert not wallclock.OPT_OUT.search("x  # wall-clock-ok:")
    assert not wallclock.OPT_OUT.search("x  # wall-clock-ok: ok")
    assert re.search(wallclock.OPT_OUT, "expect(x)  // wall-clock-ok: the hang is 30 s, fast path ms")
