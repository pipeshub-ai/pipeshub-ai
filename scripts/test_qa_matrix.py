"""QA-FINAL: the final pass's matrix (scripts/qa_matrix.py) and the lanes qa_full.py adds for it. Standard library
only: `python3 -m unittest discover -s scripts -p 'test_*.py'`."""

from __future__ import annotations

import contextlib
import copy
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import qa_full  # noqa: E402
import qa_matrix  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


class TheMatrixIsTrueToTheTree(unittest.TestCase):
    """The checked-in data: whole, every proof and regression test where it says, every gap tracked, the doc current."""

    def test_the_data_has_no_problem_and_the_doc_is_rendered_from_it(self) -> None:
        data = qa_matrix.load()
        self.assertEqual(qa_matrix.validate(data, run_lanes=qa_matrix.qa_full_lanes()), [])
        self.assertEqual(qa_matrix.DOC.read_text(encoding="utf-8"), qa_matrix.render(data),
                         "docs/test-plan/QA-FINAL.md is out of date: python3 scripts/qa_matrix.py --write")

    def test_every_reported_case_has_a_regression_test_that_needs_no_stack(self) -> None:
        reports = {r["id"]: r for r in qa_matrix.load()["reportedCases"]}
        self.assertEqual(set(reports), {
            "brief-verbosity", "raw-artifact-in-report", "citation-chips", "skip-reasks", "not-found-confidence",
            "mcp-preapproval-per-call", "connected-sources-lazy", "dark-mode-card-overflow", "failed-card-answers",
            "public-url"})
        for rid, report in reports.items():
            self.assertIn("regressions", {t["lane"] for t in report["tests"]}, rid)

    def test_the_regressions_lane_runs_each_runner_from_its_own_folder(self) -> None:
        tests = qa_matrix.regression_tests(qa_matrix.load())
        self.assertEqual(tests["other"], [])
        self.assertIn("tests/unit/harness/assembly/test_answer_contract_skip.py", tests["python"])
        self.assertIn("tests/cohesion/test_r25ui_human_stack_public_url.py", tests["python"])
        self.assertIn("tests/modules/agent-harness/cardfail-idempotent-answer.http.test.ts", tests["node"])
        self.assertIn("tests/e2e/harness/support/card-fit.test.ts", tests["vitest"])
        for runner, base in (("python", "backend/python"), ("node", "backend/nodejs/apps"), ("vitest", "frontend")):
            for path in tests[runner]:
                self.assertTrue((ROOT / base / path).is_file(), path)


class KnownAxeFaults(unittest.TestCase):
    def test_every_known_axe_fault_names_a_capture_and_the_row_that_tracks_it(self) -> None:
        manifest = json.loads((ROOT / "frontend/tests/e2e/qa/ux-captures.json").read_text(encoding="utf-8"))
        for capture, known in manifest.get("axeKnown", {}).items():
            self.assertIn(capture, manifest["captures"])
            self.assertTrue(known["rules"], capture)
            self.assertEqual(known["rules"], sorted(set(known["rules"])), capture)
            self.assertRegex(known.get("tracked", ""), r"^[A-Z][A-Za-z0-9._-]*$", capture)


def _data() -> dict:
    return {
        "lanes": [{"id": "unit", "where": "local"}, {"id": "e2e", "where": "local"}, {"id": "live", "where": "live"}],
        "rows": [{"surface": s, "dimension": d, "editions": "both", "proof": ["scripts/qa_matrix.py"], "lanes": ["unit"],
                  "gap": "none", "gapNote": ""} for s, _ in qa_matrix.SURFACES for d, _ in qa_matrix.DIMENSIONS],
        "reportedCases": [{"id": "r", "report": "x", "statusRows": ["R-1"], "level": "frontend", "tests": [
            {"path": "scripts/qa_matrix.py", "contains": ["def validate"], "lane": "unit"},
            {"path": "scripts/qa_full.py", "contains": ["def lane_unit"], "lane": "regressions"}]}],
    }


class Validation(unittest.TestCase):
    def setUp(self) -> None:
        self.data = _data()
        self.data["lanes"].append({"id": "regressions", "where": "local"})

    def problems(self, data: dict, **kw) -> list[str]:
        return qa_matrix.validate(data, root=ROOT, **kw)

    def test_a_whole_matrix_has_no_problem(self) -> None:
        self.assertEqual(self.problems(self.data, run_lanes={"unit", "e2e", "regressions"}, status={"R-1": "DONE"}), [])

    def test_a_missing_or_doubled_cell_is_named(self) -> None:
        data = copy.deepcopy(self.data)
        data["rows"].pop(0)
        data["rows"].append(copy.deepcopy(data["rows"][-1]))
        found = self.problems(data)
        self.assertIn("row chat/function: 0 rows, want exactly 1", found)
        self.assertIn("row keys/parity: 2 rows, want exactly 1", found)

    def test_a_proof_that_is_gone_and_a_lane_nobody_runs_are_named(self) -> None:
        data = copy.deepcopy(self.data)
        data["rows"][0]["proof"] = ["tests/nowhere.py"]
        data["rows"][1]["lanes"] = ["nightly"]
        found = self.problems(data, run_lanes={"unit", "regressions"})
        self.assertIn("row chat/function: proof tests/nowhere.py does not exist", found)
        self.assertIn("row chat/performance: lane nightly is not in the matrix's lanes", found)
        self.assertIn("lane e2e: local, but qa_full.py has no such lane", found)

    def test_a_gap_must_say_what_is_left_and_name_the_row_that_tracks_it(self) -> None:
        data = copy.deepcopy(self.data)
        data["rows"][0].update(gap="missing", gapNote="")
        data["rows"][1].update(gap="live-model", gapNote="needs a model", fu=["NOPE-1"])
        found = self.problems(data, status={"R-1": "DONE"})
        self.assertIn("row chat/function: gap missing says nothing of what is not proven (gapNote)", found)
        self.assertIn("row chat/function: gap missing names no follow-up row that tracks it (fu)", found)
        self.assertIn("row chat/performance: fu NOPE-1 is not a tracker row", found)

    def test_none_needs_a_local_lane_and_not_applicable_needs_none(self) -> None:
        data = copy.deepcopy(self.data)
        data["rows"][0]["lanes"] = ["live"]
        data["rows"][5].update(gap="n/a", gapNote="no model-graded eval applies", lanes=[], proof=[])
        found = self.problems(data)
        self.assertEqual(found, ["row chat/function: gap none, but no local lane runs its proof"])

    def test_a_regression_test_that_lost_its_case_or_its_fix_row_is_named(self) -> None:
        data = copy.deepcopy(self.data)
        data["reportedCases"][0]["tests"][0]["contains"] = ["the case that was deleted"]
        found = self.problems(data, status={"R-1": "OPEN"})
        self.assertIn("reported case r: scripts/qa_matrix.py no longer holds 'the case that was deleted'", found)
        self.assertIn("reported case r: tracker row 'R-1' is not DONE ('OPEN')", found)

    def test_a_report_held_only_by_stack_tests_is_named(self) -> None:
        data = copy.deepcopy(self.data)
        data["reportedCases"][0]["tests"] = data["reportedCases"][0]["tests"][:1]
        self.assertIn("reported case r: no test in the regressions lane (one that needs no stack)", self.problems(data))

    def test_status_rows_reads_tracked_rows_and_package_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "tracker.md"
            path.write_text("| ID | P | What | Status | Evidence | Next |\n|---|---|---|---|---|---|\n"
                            "| FU-1 | P2 | a | MISSING | e | **Waits on: CI.** n |\n"
                            "| PKG-1 THING | done on `harness/x`: it |\n")
            rows = qa_matrix.status_rows(path)
        self.assertEqual(rows["FU-1"], "MISSING")
        self.assertTrue(rows["PKG-1 THING"].startswith("done on"))


class Summary(unittest.TestCase):
    LANES = {"unit": {"where": "local"}, "e2e": {"where": "local"}, "live": {"where": "live"}, "ci": {"where": "ci"}}

    def verdict(self, gap: str, lanes: list[str], ran: dict[str, str]) -> str:
        return qa_matrix.verdict({"gap": gap, "lanes": lanes}, self.LANES, ran)

    def test_each_row_is_marked_by_its_local_lanes_and_by_what_it_waits_on(self) -> None:
        self.assertEqual(self.verdict("none", ["unit", "e2e"], {"unit": "passed", "e2e": "flaky"}), "proven here")
        self.assertEqual(self.verdict("none", ["unit", "e2e"], {"unit": "passed", "e2e": "failed"}), "failed")
        self.assertEqual(self.verdict("none", ["unit", "e2e"], {"unit": "passed"}), "not run")
        self.assertEqual(self.verdict("none", ["unit"], {"unit": "skipped"}), "not run")
        self.assertEqual(self.verdict("partial", ["unit"], {"unit": "passed"}), "proven here, gap partial")
        self.assertEqual(self.verdict("live-model", ["unit", "live"], {"unit": "passed"}), "proven here, needs live model")
        self.assertEqual(self.verdict("live-model", ["live"], {}), "needs live model")
        self.assertEqual(self.verdict("ci-only", ["ci"], {}), "needs CI")
        self.assertEqual(self.verdict("n/a", [], {}), "not applicable")

    def test_the_summary_marks_rows_and_reports_and_says_when_it_was_a_sample(self) -> None:
        data = _data()
        data["lanes"].append({"id": "regressions", "where": "local"})
        report = {"sha": "abc1234567890", "options": {"sample": True},
                  "lanes": [{"id": "unit", "status": "passed"}, {"id": "regressions", "status": "failed"}]}
        with tempfile.TemporaryDirectory() as tmp:
            summary = qa_matrix.write_summary(data, report, Path(tmp))
            text = (Path(tmp) / "final.md").read_text()
        self.assertEqual(summary["verdicts"], {"proven here": 66})
        self.assertEqual(summary["reportedCases"], [{"id": "r", "lanes": {"regressions": "failed", "unit": "passed"},
                                                    "verdict": "failed"}])
        self.assertIn("**Sample run**", text)


class FinalLanes(unittest.TestCase):
    def test_the_final_profile_adds_the_lanes_with_stacks_of_their_own_and_the_default_does_not(self) -> None:
        with contextlib.redirect_stdout(io.StringIO()) as plain:
            qa_full.main(["--list"])
        with contextlib.redirect_stdout(io.StringIO()) as final:
            qa_full.main(["--list", "--final"])
        for lane in qa_full.FINAL_ONLY:
            self.assertNotIn(f" {lane} ", plain.getvalue())
            self.assertIn(f" {lane} ", final.getvalue())
        order = [line.split()[0] for line in final.getvalue().splitlines()]
        self.assertEqual(order[0], "regressions")
        self.assertEqual(order[-1], "human-stack")
        self.assertLess(order.index("e2e"), order.index("mcphub"), "the shared stack goes down before stacks of their own")

    def test_node_files_runs_only_the_files_named_with_mocharc_requires(self) -> None:
        argv = qa_full.node_files(["tests/x.test.ts"])
        self.assertIn("--no-config", argv)
        self.assertEqual(argv[-1], "tests/x.test.ts")
        requires = qa_full.mocharc_requires()
        self.assertIn("ts-node/register", requires)
        self.assertIn("./tests/helpers/no-network-global.ts", requires)
        self.assertEqual(argv.count("--require"), len(requires))

    def test_a_sampled_npm_script_drops_its_globs_and_reporter(self) -> None:
        argv = qa_full.npm_script_files("test:itest", ["tests/modules/a.mongo.itest.ts"])
        self.assertEqual(argv[0], "npx")
        self.assertNotIn("--reporter", argv)
        self.assertFalse([a for a in argv if "*" in a])
        self.assertEqual(argv[-1], "tests/modules/a.mongo.itest.ts")
        covered = qa_full.npm_script_files("test:pg-harness", ["tests/modules/a.pg.itest.ts"])
        self.assertNotIn("c8", covered)
        self.assertNotIn(".c8rc.pg-harness.json", covered)
        self.assertIn("mocha", covered)

    def test_every_sampled_file_exists(self) -> None:
        bases = {"python": ROOT / "backend/python", "node": ROOT / "backend/nodejs/apps", "frontend": ROOT / "frontend",
                 "rows": qa_full.E2E_ROWS_DIR, "fault": qa_full.FAULT_DIR}
        for key, args in qa_full.SAMPLE.items():
            for arg in args:
                if arg.startswith("-") or not arg.endswith((".py", ".ts")) and "/" not in arg:
                    continue
                self.assertTrue(any((base / arg).exists() for base in bases.values()), f"{key}: {arg}")

    def test_a_pytest_rerun_keeps_the_runs_own_options(self) -> None:
        self.assertEqual(qa_full.rerun_pytest_options(["--mcp-hub", "-m", "x", "--stacks=ST-1", "a.py", "-k", "y"]),
                         ["--mcp-hub", "--stacks=ST-1"])

    def test_a_playwright_rerun_drops_the_sample_spec_but_keeps_its_grep(self) -> None:
        self.assertEqual(qa_full.rerun_options(["--workers", "1", "tests/e2e/a.spec.ts", "--grep", "AC-E6"]),
                         ["--grep", "AC-E6"])


if __name__ == "__main__":
    unittest.main()
