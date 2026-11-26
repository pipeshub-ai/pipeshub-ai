"""The ``stress`` tests: skipped by default, selected by ``RUN_STRESS_TESTS=1`` or ``-m stress``, never lost.

A load test or a wall-clock bound that needs a quiet host must not run in the per-PR unit job, and must still run
somewhere (``.github/workflows/stress-nightly.yml``). The marker is defined once, in ``pytest.ini``.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING

from tests.support.stress_marker import skip_stress_tests

if TYPE_CHECKING:
    import pytest

ROOT = Path(__file__).resolve().parents[2]


def _items() -> list[SimpleNamespace]:
    plain = SimpleNamespace(keywords={}, markers=[], add_marker=lambda m: plain.markers.append(m))
    stress = SimpleNamespace(keywords={"stress": True}, markers=[], add_marker=lambda m: stress.markers.append(m))
    return [plain, stress]


def _skipped(config_mark: str, env: dict[str, str], monkeypatch: pytest.MonkeyPatch) -> list[bool]:
    monkeypatch.delenv("RUN_STRESS_TESTS", raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    items = _items()
    config = SimpleNamespace(getoption=lambda name: config_mark)
    skip_stress_tests(config, items)  # type: ignore[arg-type]
    return [bool(i.markers) for i in items]


def test_stress_tests_are_skipped_by_default_and_selected_by_the_env_var_or_the_marker(monkeypatch: pytest.MonkeyPatch) -> None:
    assert _skipped("", {}, monkeypatch) == [False, True]  # the default: only the stress test is skipped
    assert _skipped("", {"RUN_STRESS_TESTS": "1"}, monkeypatch) == [False, False]
    assert _skipped("stress", {}, monkeypatch) == [False, False]


def test_the_marker_is_defined_once() -> None:
    ini = (ROOT / "pytest.ini").read_text()
    assert len(re.findall(r"^\s+stress:", ini, flags=re.M)) == 1
    assert '-m "not stress"' not in ini  # a default deselect would also hide them from RUN_STRESS_TESTS=1


def test_the_stress_files_collect_under_the_marker_and_a_nightly_job_runs_them() -> None:
    out = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider", "-m", "stress",
         "tests/unit/modules/parse_engine/test_class_gate_bench.py", "tests/unit/telemetry/test_ops_exporter_d159_load.py"],
        cwd=ROOT, capture_output=True, text=True, timeout=120, check=False,
    ).stdout
    counts = {m.group(1): int(m.group(2)) for m in re.finditer(r"^(tests/\S+\.py): (\d+)$", out, flags=re.M)}
    assert counts == {  # the four stress tests of these files: the class-gate timing bound and three exporter load tests
        "tests/unit/modules/parse_engine/test_class_gate_bench.py": 1,
        "tests/unit/telemetry/test_ops_exporter_d159_load.py": 3,
    }, out
    workflow = (ROOT.parent.parent / ".github" / "workflows" / "stress-nightly.yml").read_text()
    assert "schedule:" in workflow and "RUN_STRESS_TESTS" in workflow and "-m stress" in workflow
    assert "LITELLM_LOCAL_MODEL_COST_MAP" in workflow  # no external price-map fetch at collection
