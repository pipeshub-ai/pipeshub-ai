"""The spend CI job splits its suite over shards: every test must land in exactly one shard, so a new test cannot be
skipped by the split."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests.support.shard_plugin import parse_shard, shard_of

PYTHON_ROOT = Path(__file__).resolve().parents[3]
WORKFLOW = PYTHON_ROOT.parent.parent / ".github" / "workflows" / "unit-test.yml"
SPEND_PATHS = [
    "tests/integration/spend",
    "tests/unit/services/llm_gateway/test_fleet_breaker.py",
    "tests/unit/modules/pipeline/test_hold_ledger.py",
    "tests/unit/modules/pipeline/test_pause_budget_simulation.py",
    "tests/unit/services/messaging/test_fleet_state_startup.py",
]


def _collect(*extra: str, extra_env: dict[str, str] | None = None) -> list[str]:
    # a contributor's own PYTEST_ADDOPTS (a shard, verbosity) must not change what the baseline collection sees
    env = {k: v for k, v in os.environ.items() if k not in ("PYTEST_XDIST_WORKER", "PYTEST_ADDOPTS")}
    env.update(extra_env or {})
    out = subprocess.run(
        [sys.executable, "-m", "pytest", *SPEND_PATHS, "-m", "not live_smoke", "--collect-only", "-p", "no:cacheprovider",
         "-p", "tests.support.shard_plugin",
         "--verbosity=-1", *extra],
        cwd=PYTHON_ROOT, env=env, capture_output=True, text=True, check=True, timeout=300,
    ).stdout
    return [line for line in out.splitlines() if "::" in line]


@pytest.mark.timeout(600)
def test_every_spend_test_lands_in_exactly_one_shard() -> None:
    count = 3
    everything = _collect()
    assert len(everything) > 100
    assert len(set(everything)) == len(everything)
    expected = [[t for t in everything if shard_of(t, count) == i] for i in range(1, count + 1)]
    assert sorted(t for shard in expected for t in shard) == sorted(everything), "shards must cover every test exactly once"
    assert all(expected), "no shard may be empty"
    # and the plugin really selects that slice
    assert _collect("--shard=2/3") == expected[1]


@pytest.mark.timeout(300)
def test_an_inherited_verbosity_does_not_change_what_collection_prints() -> None:
    """A contributor's ``PYTEST_ADDOPTS=-v`` must not turn the collected ids into a tree."""
    assert len(_collect("--shard=1/3", extra_env={"PYTEST_ADDOPTS": "-v"})) > 20


@pytest.mark.timeout(300)
def test_an_inherited_shard_option_does_not_narrow_the_baseline(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PYTEST_ADDOPTS", "--shard=1/3")
    assert len(_collect()) > 2 * len(_collect("--shard=1/3"))  # the whole suite, not one slice of it


def test_the_workflow_runs_every_shard_of_the_matrix() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    assert '--shard="${{ matrix.shard }}/3"' in text
    assert "shard: [1, 2, 3]" in text


def test_the_split_is_deterministic_and_covers_one_to_n() -> None:
    nodeid = "tests/x.py::test_a[1]"
    assert shard_of(nodeid, 3) == shard_of(nodeid, 3)
    assert {shard_of(f"t{i}", 3) for i in range(200)} == {1, 2, 3}


@pytest.mark.parametrize("bad", ["0/3", "4/3", "x", "1/0", "1-3"])
def test_a_malformed_shard_is_refused(bad: str) -> None:
    with pytest.raises(pytest.UsageError):
        parse_shard(bad)


