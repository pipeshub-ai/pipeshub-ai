"""scripts/journey_matrix.py (R31-JOURNEY, ADR-TEST-01): the registry check refuses what would let a cell go untested.

Standard library only: python3 -I -m unittest discover -s scripts -p 'test_journey_matrix.py'
"""

from __future__ import annotations

import copy
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import journey_matrix as jm

RUNNER = {"SUPPORT": {"a": ["p1"], "b": ["p1", "p2"]}, "DRIVEN": {"p1": ["s1"], "p2": ["s1"]},
          "TURNS": {"use": ["a"], "solo": ["b"]}}
DATA = {
    "features": [{"id": "a"}, {"id": "b"}, {"id": "c"}],
    "paths": [{"id": "p1", "shapes": ["s1", "s2"], "primaryShape": "s1"}, {"id": "p2", "shapes": ["s1"], "primaryShape": "s1"}],
    "shapes": [{"id": "s1"}, {"id": "s2"}],
    "rules": [{"id": "X1", "kind": "impossible", "features": ["c"], "paths": ["p2"], "shapes": [], "reason": "why"},
              {"id": "X3", "kind": "out_of_scope", "features": ["a"], "paths": ["p2"], "shapes": [], "reason": "why"}],
    "tests": [{"ref": "smoke:sign-in admin", "cells": [["c", "p1", "s1"]]}],
    "gaps": [{"id": "G-1", "features": [], "paths": [], "shapes": ["s2"], "why": "w", "waitsOn": "x"}],
    "mustRun": [{"id": "MR-1", "title": "t", "cells": [["b", "p2", "s1"]]}],
    "tiers": {"gate": {"rule": "r"}, "full": {"rule": "r"}},
}
ROWS = {"G-1"}


def matrix(data: dict | None = None, runner: dict | None = None) -> jm.Matrix:
    return jm.Matrix(copy.deepcopy(data or DATA), copy.deepcopy(runner or RUNNER))


class JourneyMatrixTest(unittest.TestCase):
    def test_a_whole_registry_has_no_problem(self) -> None:
        self.assertEqual(jm.problems(matrix(), rows=ROWS), [])

    def test_a_feasible_cell_nothing_covers_and_no_gap_tracks_fails(self) -> None:
        data = copy.deepcopy(DATA)
        data["tests"] = []
        found = jm.problems(matrix(data), rows=ROWS)
        self.assertIn("cell ['c', 'p1', 's1']: no test runs it and no gap tracks it", found)

    def test_a_test_that_does_not_exist_or_is_not_on_a_real_stack_fails(self) -> None:
        self.assertIsNotNone(jm.ref_problem("smoke:no such check label anywhere"))
        self.assertIn("not a real-stack test", jm.ref_problem("pytest:backend/python/tests/unit/x.py::test_y") or "")
        self.assertIn("no def", jm.ref_problem("pytest:deployment/e2e/journeys.py::nothing_here") or "")
        self.assertIsNone(jm.ref_problem("script:deployment/e2e/journeys.py::use_turn"))

    def test_a_matrix_row_is_a_test_only_while_it_is_a_row(self) -> None:
        self.assertIsNone(jm.ref_problem("pytest:integration-tests/harness/e2e/test_pairwise.py::test_row[ST-1-PW-002]"))
        self.assertIn("not a row", jm.ref_problem(
            "pytest:integration-tests/harness/e2e/test_pairwise.py::test_row[ST-1-PW-999]") or "")

    def test_a_test_claiming_an_excluded_cell_fails(self) -> None:
        data = copy.deepcopy(DATA)
        data["tests"].append({"ref": "smoke:sign-in member", "cells": [["c", "p2", "s1"]]})
        self.assertTrue(any("excluded by X1" in p for p in jm.problems(matrix(data), rows=ROWS)))

    def test_a_rule_that_decides_no_cell_and_a_stale_gap_fail(self) -> None:
        data = copy.deepcopy(DATA)
        data["rules"].append({"id": "X2", "kind": "impossible", "features": ["c"], "paths": ["p2"], "shapes": [],
                              "reason": "the same as X1"})
        data["gaps"].append({"id": "G-2", "features": ["a"], "paths": ["p1"], "shapes": ["s1"], "why": "w", "waitsOn": "x"})
        found = jm.problems(matrix(data), rows={"G-1", "G-2"})
        self.assertTrue(any(p.startswith("rule X2: decides no cell") for p in found))
        self.assertTrue(any(p.startswith("gap G-2: every cell it matches is covered") for p in found))

    def test_a_gap_needs_its_status_row_and_what_it_waits_on(self) -> None:
        data = copy.deepcopy(DATA)
        del data["gaps"][0]["waitsOn"]
        found = jm.problems(matrix(data), rows=set())
        self.assertIn("gap G-1: no tracker row G-1", found)
        self.assertIn("gap G-1: says not what it waits on (waitsOn)", found)

    def test_without_a_tracker_the_rows_are_not_checked_but_the_rest_is(self) -> None:
        data = copy.deepcopy(DATA)
        del data["gaps"][0]["waitsOn"]
        found = jm.problems(matrix(data))
        self.assertFalse(any("tracker row" in p for p in found))
        self.assertIn("gap G-1: says not what it waits on (waitsOn)", found)

    def test_status_rows_reads_the_first_cell_of_each_table_row(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "tracker.md"
            path.write_text("| id | what |\n|---|---|\n| G-1 | a gap |\n| **R-2** | bold |\n", encoding="utf-8")
            self.assertEqual(jm.status_rows(path) & {"G-1", "R-2"}, {"G-1", "R-2"})

    def test_a_must_run_cell_must_be_a_journey(self) -> None:
        runner = copy.deepcopy(RUNNER)
        runner["SUPPORT"]["b"] = ["p1"]
        data = copy.deepcopy(DATA)
        data["gaps"].append({"id": "G-3", "features": ["b"], "paths": ["p2"], "shapes": [], "why": "w", "waitsOn": "x"})
        found = jm.problems(matrix(data, runner), rows={"G-1", "G-3"})
        self.assertTrue(any(p.startswith("must-run MR-1") and "not a journey" in p for p in found))

    def test_the_gate_runs_every_feature_on_every_path_once_and_every_must_run(self) -> None:
        p = jm.plan(matrix())
        self.assertEqual(sorted(p["tiers"]["gate"]), ["J-p1-s1-solo", "J-p1-s1-use", "J-p2-s1-solo"])
        self.assertEqual(set(p["tiers"]["full"]), set(p["tiers"]["gate"]))

    def test_r33jgaps_a_full_only_entry_leaves_its_journeys_out_of_the_gate_but_not_a_must_run(self) -> None:
        data = copy.deepcopy(DATA)
        data["tiers"]["gate"]["fullOnly"] = [{"id": "F1", "turns": ["solo"], "why": "slow"}]
        p = jm.plan(matrix(data))
        self.assertEqual(sorted(p["tiers"]["gate"]), ["J-p1-s1-use", "J-p2-s1-solo"])
        self.assertIn("J-p1-s1-solo", p["tiers"]["full"])
        self.assertEqual(jm.problems(matrix(data), rows=ROWS), [])

    def test_r33jgaps_a_full_only_entry_without_a_reason_or_a_journey_fails(self) -> None:
        data = copy.deepcopy(DATA)
        data["tiers"]["gate"]["fullOnly"] = [{"id": "F1", "turns": ["none"]}]
        found = jm.problems(matrix(data), rows=ROWS)
        self.assertTrue(any("F1: no why" in f for f in found))
        self.assertTrue(any("F1: leaves no journey" in f for f in found))

    def test_r33jgaps_a_skipped_cell_is_not_the_runners_and_needs_its_gap(self) -> None:
        runner = copy.deepcopy(RUNNER)
        runner["SKIP"] = {"a": ["p1/s1"]}
        m = matrix(runner=runner)
        self.assertFalse(m.runner_runs(("a", "p1", "s1")))
        self.assertTrue(any("['a', 'p1', 's1']" in f and "no gap" in f for f in jm.problems(m, rows=ROWS)))

    def test_the_runner_literals_are_read_without_importing_it(self) -> None:
        literals = jm.runner_literals()
        self.assertIn("mcp.first_use", literals["SUPPORT"])
        self.assertIn("job.codex", literals["SUPPORT"]["mcp.first_use"])
        self.assertIn("owner", literals["DRIVEN"]["job.codex"])

    def test_the_committed_registry_covers_the_owner_reported_breaks(self) -> None:
        m = jm.Matrix(jm.load(), jm.runner_literals())
        gate = set(jm.plan(m)["tiers"]["gate"])
        self.assertIn("J-job.codex-owner-use", gate)
        self.assertTrue(m.runner_runs(("mcp.first_use", "job.codex", "owner")))
        self.assertIsNone(m.rule_of(("mcp.first_use", "job.codex", "owner")))

    def test_the_registry_holds_every_key_its_schema_requires(self) -> None:
        self.assertEqual(jm.shape_problems(jm.load()), [])
        data = jm.load()
        del data["knownRed"]
        del data["rules"][0]["reason"]
        found = jm.shape_problems(data)
        self.assertIn("journeys.json: no knownRed", found)
        self.assertIn("journeys.json rules[0]: no reason", found)

    def test_the_runner_starts_only_when_its_literals_and_probes_agree(self) -> None:
        sys.path.insert(0, str(jm.ROOT / "deployment/e2e"))
        import journeys  # noqa: PLC0415 - the runner imports its stack client only once it runs

        self.assertEqual(journeys.consistent(), [])
        self.assertEqual({k: list(v) for k, v in journeys.SUPPORT.items()}, jm.runner_literals()["SUPPORT"])
        journeys.PROBES.pop("budget")
        try:
            self.assertIn("TURNS budget has no probe", journeys.consistent())
        finally:
            journeys.PROBES["budget"] = journeys.budget_turn


if __name__ == "__main__":
    unittest.main()
