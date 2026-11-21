"""C-25 (G2C-2): ``slow`` is reserved for gate runs, so no feature lane drops out of the regular Docker lane.

The regular Docker lane is ``-m "docker and not slow"`` and the ``slow`` lanes run only as named gate runs. A
module marked ``slow`` for another reason is run by nobody: DL-4 (stage 1's ``test_real_code_task.py``, retired by S1R) failed for a day
after H1 because every real-binary lane carried ``slow`` and no lane selected it. A new ``slow`` module must be
added here with the gate run that runs it.
"""

from __future__ import annotations

import re

from tests.cohesion.support import PYTHON_ROOT

_SLOW = re.compile(r"pytest\.mark\.slow\b")

GATE_RUNS: dict[str, str] = {
    "tests/unit/harness/engines/test_g4_crash_matrix.py": "G4 crash matrix (X8 gate)",
    "tests/unit/harness/engines/test_p9_fault_matrix.py": "P9 gate run (engine fault rows)",
    "tests/unit/harness/engines/test_p9_backup_drill.py": "P9 gate run (backup drill)",
    "tests/unit/harness/engines/test_p9_audit_lane.py": "P9 gate run (grant auditor)",
    "tests/unit/harness/engines/test_real_start_time.py": "start-time benchmark (quiet host)",
    "tests/unit/harness/engines/test_real_snapshot_cost.py": "P5.9b snapshot cost benchmark (quiet host)",
    "tests/unit/harness/engines/test_pf03_resume_e2e.py": "PF-03 resume benchmark (bench_harness.py --profile resume)",
    "tests/unit/harness_core/workspace/sync/test_perf.py": "PF-2 workspace-sync benchmark (quiet host, WALLCLOCK-1)",
    "tests/unit/harness/engines/test_dp8_engine_latency.py": "DP8 engine latency on the real binaries (quiet host, R23-PERF)",
    "tests/unit/harness/sandbox_broker/test_qa22_sandbox_start.py": "SLO #17 gate (bench_harness.py --profile sandbox, perf-harness.yml)",
    "tests/unit/harness/perf/test_pf05_tool_gateway_overhead.py": "PF-05 gate (bench_harness.py --profile tool-gateway)",
    "tests/unit/harness/perf/test_pf07_model_gateway_ttft.py": "PF-07 gate (bench_harness.py --profile model-gateway)",
    "tests/unit/harness/engines/test_live_claude_code.py": "live model lane (spends; keys)",
    "tests/unit/harness/engines/test_live_skill_packs.py": "live model lane (spends; keys)",
    "tests/unit/harness/engines/test_e2e4_live_matrix.py": "live tool matrix (spends; keys; E2E4 §8)",
    "tests/cohesion/test_c24_durable_wire_parity.py": "durable-default gate (wire parity over the parity cases)",
    # Main's (89767b97e4): its unit shards select no marker, so they run them; only the harness lanes drop `slow`.
    "tests/unit/modules/parsers/test_docling_lazy_import.py": "main's unit shards (unit-test.yml, no marker filter)",
    "tests/unit/test_edition_config_lazy_routers.py": "main's unit shards (unit-test.yml, no marker filter)",
    "tests/unit/test_import_boundaries.py": "main's unit shards (unit-test.yml, no marker filter)",
    "tests/integration/graph_db/test_replace_edges_to_memory_real_neo4j.py": (
        "main's Neo4j memory-preset run, by hand against a neo4j at NEO4J_IT_PRESET=small (backend-matrix.yml skips it)"
    ),
    # Main's (166e620897, #5503): the same memory-preset run as its sibling above.
    "tests/integration/graph_db/test_graph_memory_limit_real_neo4j.py": (
        "main's Neo4j memory-preset run, by hand against a neo4j at NEO4J_IT_PRESET=small (backend-matrix.yml skips it)"
    ),
}


def _slow_modules() -> set[str]:
    found: set[str] = set()
    for path in (PYTHON_ROOT / "tests").rglob("test_*.py"):
        if path.name == "test_c25_slow_lanes.py":
            continue
        if _SLOW.search(path.read_text(encoding="utf-8")):
            found.add(path.relative_to(PYTHON_ROOT).as_posix())
    return found


def test_c25_only_gate_runs_are_slow() -> None:
    marked = _slow_modules()
    unregistered = sorted(marked - set(GATE_RUNS))
    assert not unregistered, (
        f"{unregistered} are marked slow, which takes them out of the regular Docker lane; mark them slow only "
        "if a gate run selects them, and register them in GATE_RUNS"
    )
    stale = sorted(set(GATE_RUNS) - marked)
    assert not stale, f"{stale} are registered as gate runs but no longer marked slow"

