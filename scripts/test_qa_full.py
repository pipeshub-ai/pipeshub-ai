"""QA-AUTO: the full QA runner's readers, judgements and report (scripts/qa_full.py), and its budgets' agreement with
SLO.md. Standard library only: `python3 -m unittest discover -s scripts -p 'test_*.py'`."""

from __future__ import annotations

import contextlib
import io
import json
import re
import socket
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import qa_full  # noqa: E402
from qa_full import FAILED, FLAKY, PASSED, REAL, SKIPPED, Case, Lane, Step  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


class Readers(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = Path(tempfile.mkdtemp())

    def test_junit_reads_passes_failures_errors_and_skips_with_their_reason(self) -> None:
        path = self.dir / "r.xml"
        path.write_text(
            "<testsuites><testsuite>"
            "<testcase classname='tests.unit.test_a' name='test_ok' time='0.5'/>"
            "<testcase classname='tests.unit.test_a' name='test_bad'><failure message='boom'>trace</failure></testcase>"
            "<testcase classname='tests.unit.test_a.TestK' name='test_err'><error message='E'/></testcase>"
            "<testcase classname='x.spec.ts' name='skipped one'><skipped/><properties><property name='skip' value='no SMTP'/></properties></testcase>"
            "</testsuite></testsuites>"
        )
        cases = qa_full.read_junit(path)
        self.assertEqual([c.status for c in cases], [PASSED, FAILED, FAILED, SKIPPED])
        self.assertIn("boom", cases[1].message)
        self.assertEqual(cases[3].message, "no SMTP")
        self.assertEqual(qa_full.read_junit(self.dir / "missing.xml"), [])

    def test_pytest_node_id_finds_the_file_then_the_classes(self) -> None:
        (self.dir / "tests" / "unit").mkdir(parents=True)
        (self.dir / "tests" / "unit" / "test_a.py").write_text("")
        self.assertEqual(qa_full.pytest_node_id("tests.unit.test_a.TestK", "test_x[1]", self.dir),
                         "tests/unit/test_a.py::TestK::test_x[1]")
        self.assertIsNone(qa_full.pytest_node_id("nowhere.test_b", "t", self.dir))

    def test_mocha_json_reads_failures_and_pending_once_each(self) -> None:
        path = self.dir / "m.json"
        ok = {"title": "a", "fullTitle": "S a", "file": "/t/x.test.ts", "duration": 5, "err": {}}
        bad = {"title": "b", "fullTitle": "S b", "file": "/t/x.test.ts", "duration": 2, "err": {"message": "nope"}}
        skip = {"title": "c", "fullTitle": "S c", "file": "/t/x.test.ts", "err": {}}
        path.write_text(json.dumps({"tests": [ok, bad, skip], "failures": [bad], "pending": [skip], "passes": [ok]}))
        cases = qa_full.read_mocha_json(path)
        self.assertEqual({c.name: c.status for c in cases}, {"S a": PASSED, "S b": FAILED, "S c": SKIPPED})
        self.assertEqual(next(c for c in cases if c.status == FAILED).message, "nope")

    def test_playwright_json_names_each_test_with_its_file_line_and_describe(self) -> None:
        path = self.dir / "p.json"
        path.write_text(json.dumps({"suites": [{
            "title": "qa/a.spec.ts", "file": "qa/a.spec.ts",
            "specs": [{"title": "top", "file": "qa/a.spec.ts", "line": 7,
                       "tests": [{"projectName": "harness", "status": "expected", "results": [{"duration": 1000}]}]}],
            "suites": [{"title": "Group", "file": "qa/a.spec.ts", "specs": [{
                "title": "inner", "file": "qa/a.spec.ts", "line": 20,
                "tests": [{"projectName": "harness", "status": "unexpected",
                           "results": [{"duration": 10, "errors": [{"message": "\x1b[31mred\x1b[39m"}]}]},
                          {"projectName": "harness", "status": "skipped", "annotations": [{"type": "skip", "description": "no SMTP"}], "results": []}]}]}],
        }]}))
        cases = qa_full.read_playwright_json(path)
        self.assertEqual([(c.name, c.status, c.line) for c in cases],
                         [("top", PASSED, 7), ("Group › inner", FAILED, 20), ("Group › inner", SKIPPED, 20)])
        self.assertEqual(cases[1].message, "red")
        self.assertEqual(cases[2].message, "no SMTP")


class Judgements(unittest.TestCase):
    def test_a_failure_that_passes_alone_is_flaky_and_one_that_fails_again_is_real(self) -> None:
        self.assertEqual(Case("a", "a", FAILED, rerun=PASSED).label, FLAKY)
        self.assertEqual(Case("a", "a", FAILED, rerun=FAILED).label, REAL)
        self.assertEqual(Case("a", "a", PASSED).label, "")

    def test_step_status_from_cases_and_exit_code(self) -> None:
        self.assertEqual(qa_full.judge_step(Step("s", exit_code=0, cases=[Case("a", "a", PASSED)])), PASSED)
        self.assertEqual(qa_full.judge_step(Step("s", exit_code=1, cases=[Case("a", "a", FAILED, rerun=PASSED)])), FLAKY)
        self.assertEqual(qa_full.judge_step(Step("s", exit_code=1, cases=[Case("a", "a", FAILED, rerun=PASSED),
                                                                         Case("b", "b", FAILED, rerun=FAILED)])), FAILED)
        # A runner that crashed with no failed test is an error, never a pass.
        self.assertEqual(qa_full.judge_step(Step("s", exit_code=2, cases=[Case("a", "a", PASSED)])), "error")
        self.assertEqual(qa_full.judge_step(Step("s", status=SKIPPED)), SKIPPED)

    def test_lane_and_exit_status(self) -> None:
        lanes = [Lane("a", "A", steps=[Step("s", status=PASSED)]), Lane("b", "B", steps=[Step("s", status=FLAKY)])]
        for lane in lanes:
            lane.status = qa_full.judge_lane(lane)
        self.assertEqual(qa_full.exit_status(lanes), 3)
        lanes.append(Lane("c", "C", status="error"))
        self.assertEqual(qa_full.exit_status(lanes), 1)
        self.assertEqual(qa_full.exit_status([Lane("a", "A", status=PASSED), Lane("s", "S", status=SKIPPED)]), 0)

    def test_percentile_is_nearest_rank_like_the_specs(self) -> None:
        values = [float(v) for v in range(1, 13)]
        self.assertEqual(qa_full.percentile(values, 0.5), 6.0)
        self.assertEqual(qa_full.percentile(values, 0.95), 12.0)
        self.assertIsNone(qa_full.percentile([], 0.5))

    def test_latency_rows_judge_p50_and_p95_against_the_budget(self) -> None:
        budgets = {"metrics": {"a": {"title": "A", "p50_ms": 10, "p95_ms": 20},
                               "b": {"title": "B", "p50_ms": 10, "p95_ms": 20},
                               "c": {"title": "C", "p50_ms": 10, "p95_ms": 20}}}
        rows = {r["metric"]: r["status"] for r in qa_full.judge_latency({"a": [5, 6, 19], "b": [5, 6, 25]}, budgets)}
        self.assertEqual(rows, {"a": PASSED, "b": FAILED, "c": "missing"})


class Reruns(unittest.TestCase):
    def test_a_serial_spec_rerun_classifies_the_failure_and_runs_what_its_failure_skipped(self) -> None:
        cases = [Case("p::a.spec.ts::one", "one", FAILED, file="a.spec.ts"),
                 Case("p::a.spec.ts::two", "two", SKIPPED, file="a.spec.ts"),
                 Case("p::a.spec.ts::three", "three", SKIPPED, file="a.spec.ts", message="no SMTP"),
                 Case("p::b.spec.ts::four", "four", SKIPPED, file="b.spec.ts")]
        again = [Case("p::a.spec.ts::one", "one", PASSED, file="a.spec.ts"),
                 Case("p::a.spec.ts::two", "two", FAILED, file="a.spec.ts", message="boom"),
                 Case("p::a.spec.ts::three", "three", SKIPPED, file="a.spec.ts", message="no SMTP")]
        qa_full.apply_rerun(cases, again, {"a.spec.ts"})
        self.assertEqual([(c.status, c.label) for c in cases],
                         [(FAILED, FLAKY), (FAILED, REAL), (SKIPPED, ""), (SKIPPED, "")])
        self.assertEqual(cases[1].message, "boom")

    def test_a_rerun_keeps_the_options_but_not_the_worker_count(self) -> None:
        self.assertEqual(qa_full.rerun_options(["--workers", "2", "--update-snapshots", "--workers=3", "--grep", "x"]),
                         ["--update-snapshots", "--grep", "x"])
        self.assertEqual(qa_full.rerun_options(None), [])


class PerfVerdicts(unittest.TestCase):
    def test_a_verdict_with_no_value_fails_and_a_loaded_host_skips(self) -> None:
        verdicts = [
            {"slo": "PF-05", "profile": "tool-gateway", "metric": "overhead_ms", "stat": "p50", "threshold": 35.0, "value": None, "status": "missing"},
            {"slo": "#8", "profile": "reconnect", "metric": "reconnect_ms", "stat": "p95", "threshold": 500.0, "value": None, "status": "rerun"},
            {"slo": "#3", "profile": "modes", "metric": "created_ms", "stat": "p95", "threshold": 150.0, "value": 208.6, "status": "rerun"},
            {"slo": "#7", "profile": "cancel", "metric": "stop_ms", "stat": "p95", "threshold": 500.0, "value": 221.8, "status": "pass"},
        ]
        self.assertEqual([c.status for c in qa_full.verdict_cases(verdicts)], [FAILED, FAILED, SKIPPED, PASSED])
        self.assertEqual([c.label for c in qa_full.verdict_cases(verdicts)], [REAL, REAL, "", ""])


class Report(unittest.TestCase):
    def test_the_html_names_every_lane_failure_label_and_latency_row_and_escapes_text(self) -> None:
        step = Step("python unit", status=FLAKY, exit_code=1, cases=[Case("t::<x>", "x", FAILED, message="<script>x</script>", rerun=PASSED)])
        lane = Lane("unit", "Unit", status=FLAKY, steps=[step])
        report = {
            "sha": "a" * 40, "branch": "b", "host": "h", "started_at": "t", "wall_s": 60, "exit": 3,
            "totals": {"tests": 1, PASSED: 0, FAILED: 1, SKIPPED: 0, FLAKY: 1, REAL: 0},
            "lanes": [qa_full.lane_json(lane)],
            "latency": {"rows": [{"metric": "m", "title": "First token", "samples": 3, "p50_ms": 1.0, "p95_ms": 2.0,
                                  "budget_p50_ms": 5, "budget_p95_ms": 9, "lane": "harness", "status": PASSED}], "samples": {}},
            "perf": [{"slo": "PF-05", "profile": "tool-gateway", "metric": "overhead_ms", "stat": "p50", "target": 35.0,
                      "threshold": 35.0, "value": 20.0, "status": "pass", "detail": ""}],
            "ux": [{"name": "chat-desktop-light", "path": "ux/a.png", "diff": "", "status": PASSED}],
        }
        page = qa_full.render_html(report)
        self.assertIn("Only flaky failures", page)
        self.assertIn("First token", page)
        self.assertIn("PF-05", page)
        self.assertIn("chat-desktop-light", page)
        self.assertIn("&lt;script&gt;x&lt;/script&gt;", page)
        self.assertNotIn("<script>", page)

    def test_report_json_counts_every_test_but_lists_only_the_ones_that_did_not_simply_pass(self) -> None:
        step = Step("s", cases=[Case("a", "a", PASSED), Case("b", "b", PASSED), Case("c", "c", FAILED, rerun=PASSED),
                                Case("d", "d", SKIPPED, message="no SMTP")])
        out = qa_full.lane_json(Lane("l", "L", steps=[step]))
        self.assertEqual([c["id"] for c in out["steps"][0]["cases"]], ["c", "d"])
        self.assertEqual(out["counts"], {"tests": 4, PASSED: 2, FAILED: 1, SKIPPED: 1, FLAKY: 1, REAL: 0})
        self.assertEqual(qa_full._counts_of(out["steps"][0]), out["counts"])

    def test_lanes_are_unique_and_list_runs(self) -> None:
        names = [name for name, _, _ in qa_full.LANES]
        self.assertEqual(len(names), len(set(names)))
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(qa_full.main(["--list"]), 0)
        self.assertIn("e2e", out.getvalue())

    def test_qa_classic_the_browser_lanes_are_registered_before_the_durable_lane_tears_their_stack_down(self) -> None:
        names = [name for name, _, _ in qa_full.LANES]
        for lane in ("ux", "latency", "e2e"):
            self.assertIn(lane, names)
            self.assertLess(names.index(lane), names.index("docker"))

    def test_qa_classic_every_playwright_project_the_runner_names_is_configured_and_has_its_specs(self) -> None:
        source = (ROOT / "scripts/qa_full.py").read_text()
        config = (ROOT / "frontend/playwright.config.ts").read_text()
        projects = sorted(set(re.findall(r'"([a-z-]+)",\s*skips_allowed', source))
                          | set(re.findall(r'ux_shots\(ctx, [^)]*?"(ux-shots[a-z-]*)", (?:ctx\.e2e_run|run)\)', source)))
        self.assertEqual(projects, ["authenticated", "harness", "perf", "qa-indexed", "qa-latency", "ux-shots",
                                    "ux-shots-engine"])
        for project in projects:
            self.assertRegex(config, rf"(harnessProject|uxProject)\('{project}'|name: '{project}'", project)
        qa = ROOT / "frontend/tests/e2e/qa"
        specs = {path.name for path in qa.glob("*.spec.ts")}
        self.assertTrue({"ux-shots.spec.ts", "latency.spec.ts", "latency-engine.spec.ts"} <= specs)
        self.assertTrue(any(name.startswith("indexed-journey-") for name in specs))
        self.assertTrue(any(name.startswith("engine-journey-") for name in specs))
        self.assertGreaterEqual(sum(name.startswith("journey-") for name in specs), 3)
        self.assertFalse(any("/new-interface/" in path.read_text() for path in qa.glob("*.ts")), "no QA spec imports the new interface's support")


class Budgets(unittest.TestCase):
    """The latency budgets are one table in two places: the specs read the JSON, people read SLO.md."""

    def test_slo_md_states_the_budgets_the_specs_hold(self) -> None:
        budgets = json.loads((ROOT / "frontend/tests/e2e/qa/latency-budgets.json").read_text())["metrics"]
        slo = (ROOT / "docs/agent-harness-v2/SLO.md").read_text()
        section = slo.split("## Browser latency (QA-AUTO)", 1)[1].split("\n## ", 1)[0]
        rows = {}
        for line in section.splitlines():
            m = re.match(r"^\| `([a-z_]+)` \|[^|]*\| (\d+) \| (\d+) \| (\d+) \|", line)
            if m:
                rows[m.group(1)] = (int(m.group(2)), int(m.group(3)), int(m.group(4)))
        self.assertEqual(rows, {k: (v["samples"], v["p50_ms"], v["p95_ms"]) for k, v in budgets.items()})

    def test_qa_classic_every_budget_is_measured_by_a_spec(self) -> None:
        budgets = json.loads((ROOT / "frontend/tests/e2e/qa/latency-budgets.json").read_text())["metrics"]
        specs = "".join(p.read_text() for p in (ROOT / "frontend/tests/e2e/qa").glob("latency*.spec.ts"))
        for metric, budget in budgets.items():
            if budget.get("source") == "run.sh":
                self.assertIn(metric, qa_full.STACK_TIMINGS, metric)
            else:
                self.assertIn(metric, specs, metric)

    def test_r30lat_new_budgets_name_their_grounds(self) -> None:
        """Pre-registered budgets say what they rest on, so a later change can be checked against it."""
        budgets = json.loads((ROOT / "frontend/tests/e2e/qa/latency-budgets.json").read_text())["metrics"]
        older = {"first_token_ms", "turn_done_ms", "approval_roundtrip_ms", "job_start_ms"}
        for metric, budget in budgets.items():
            if metric not in older:
                self.assertTrue(budget.get("grounds"), metric)

    def test_r30lat_stack_timings_become_samples_in_ms(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            work, out = Path(tmp) / "work", Path(tmp) / "out"
            work.mkdir()
            (work / "up-seconds").write_text("71\n")
            samples = qa_full.record_stack_timings(work, out)
            self.assertEqual(samples, {"stack_ready_ms": [71000.0]})
            self.assertEqual(qa_full.read_latency_samples(out), {"stack_ready_ms": [71000.0]})
            rows = {r["metric"]: r for r in qa_full.judge_latency(samples, {"metrics": {
                "stack_ready_ms": {"p50_ms": 300000, "p95_ms": 300000}, "upgrade_ready_ms": {"p50_ms": 120000, "p95_ms": 120000}}})}
            self.assertEqual(rows["stack_ready_ms"]["status"], PASSED)
            self.assertEqual(rows["upgrade_ready_ms"]["status"], "missing")

    def test_qa_classic_the_ux_manifest_lists_the_captures_the_spec_takes(self) -> None:
        manifest = json.loads((ROOT / "frontend/tests/e2e/qa/ux-captures.json").read_text())
        for spec, listed in (("ux-shots.spec.ts", "captures"), ("ux-shots-engine.spec.ts", "engineCaptures")):
            source = (ROOT / "frontend/tests/e2e/qa" / spec).read_text()
            self.assertEqual(sorted(re.findall(r"capture\(page, '([a-z-]+)'", source)), sorted(manifest[listed]), spec)

    def test_the_ux_manifest_names_viewports_schemes_and_the_playwright_it_was_made_with(self) -> None:
        manifest = json.loads((ROOT / "frontend/tests/e2e/qa/ux-captures.json").read_text())
        lock = json.loads((ROOT / "frontend/package-lock.json").read_text())
        self.assertEqual(manifest["playwright"], lock["packages"]["node_modules/@playwright/test"]["version"])
        self.assertEqual(set(manifest["viewports"]), {"desktop", "phone"})
        self.assertEqual(manifest["schemes"], ["light", "dark"])
        every = manifest["captures"] + manifest["engineCaptures"]
        self.assertEqual(len(every), len(set(every)))
        self.assertLessEqual(manifest["maxDiffPixelRatio"], 0.02)

    def test_qa_classic_baselines_live_in_a_folder_of_their_set_and_playwright_version(self) -> None:
        manifest = json.loads((ROOT / "frontend/tests/e2e/qa/ux-captures.json").read_text())
        ctx = qa_full.Ctx(out=Path(tempfile.mkdtemp()), python="python3", env={}, lane_cmd=[], e2e_run="r", state_dir=Path("/x"),
                          update_ux=False, soak_minutes=1, rig_samples=1, keep_stack=False, reuse_stack=False)
        self.assertEqual(qa_full.ux_baselines_dir(ctx).name, f"classic-pw-{manifest['playwright']}")


class Stack(unittest.TestCase):
    def test_qafinal_fu_eng_the_engine_job_specs_run_on_every_other_release_engine_on_its_own_stack(self) -> None:
        """QAFINAL-FU-ENG: the docker lane runs the job and job-start specs on each release engine but the default
        (whose whole durable lane runs), each with E2E_ENGINE on its own stack; a run image the host lacks skips."""
        engines = qa_full._e2e_engines()
        others = engines.kinds()[1:]
        self.assertIn("codex", others)
        self.assertIn("claude_code", others)
        self.assertNotIn(engines.default_kind(), others)
        missing = engines.image(others[0])
        ctx = qa_full.Ctx.__new__(qa_full.Ctx)
        calls: list[tuple[str, list[str], dict[str, str]]] = []

        def sample(_ctx, _lane, name, up, project, spec, *, suffix, env=None):
            self.assertEqual((up, project), ("durable-up", "harness-engine"))
            calls.append((suffix, spec, env or {}))
            return [Step(name, status=PASSED)]

        def inspect(argv, **_kw):
            return type("Done", (), {"returncode": 1 if argv[-1] == missing else 0})()

        saved = (qa_full.own_stack_sample, qa_full.subprocess.run)
        qa_full.own_stack_sample, qa_full.subprocess.run = sample, inspect
        try:
            steps = qa_full.other_engine_jobs(ctx, "docker")
        finally:
            qa_full.own_stack_sample, qa_full.subprocess.run = saved
        self.assertEqual(steps[0].status, SKIPPED)
        self.assertIn(missing, steps[0].note)
        self.assertEqual([env["E2E_ENGINE"] for _, _, env in calls], others[1:])
        self.assertEqual(len({suffix for suffix, _, _ in calls}), len(calls))
        for _, spec, _ in calls:
            self.assertEqual(spec, qa_full.ENGINE_JOB_SPECS)
            for path in spec:
                self.assertTrue((ROOT / "frontend" / path).is_file(), path)

    def test_the_lanes_stack_has_its_own_mongodb_unless_asked_otherwise(self) -> None:
        """QA-CLASSIC-FU-7: latency budgets are judged on a stack whose MongoDB no neighbour writes to."""
        saved = qa_full.os.environ.pop("E2E_STACK", None)
        try:
            self.assertEqual(qa_full.base_env(sys.executable)["E2E_STACK"], "dev-harness")
            qa_full.os.environ["E2E_STACK"] = "ST-3"
            self.assertEqual(qa_full.base_env(sys.executable)["E2E_STACK"], "ST-3")
        finally:
            qa_full.os.environ.pop("E2E_STACK", None)
            if saved is not None:
                qa_full.os.environ["E2E_STACK"] = saved

    def _ctx(self, env: dict[str, str]) -> qa_full.Ctx:
        return qa_full.Ctx(out=Path(tempfile.mkdtemp()), python="python3", env=env, lane_cmd=[], e2e_run="r",
                           state_dir=Path("/x"), update_ux=False, soak_minutes=1, rig_samples=1, keep_stack=False,
                           reuse_stack=False)

    def test_the_store_lanes_take_their_stores_from_the_itest_variables_and_skip_without_them(self) -> None:
        for lane_fn, name in ((qa_full.lane_stores, "node mongo and redis itests"), (qa_full.lane_perf, "perf rig")):
            lane = Lane("x", "x")
            lane_fn(self._ctx({"REDIS_IT_URL": "redis://127.0.0.1:6379"}), lane)
            self.assertEqual((lane.steps[0].name, lane.steps[0].status), (name, SKIPPED))
            self.assertIn("MONGO_IT_URI", lane.steps[0].note)
        lane = Lane("perf", "perf")
        qa_full.lane_perf(self._ctx({"MONGO_IT_URI": "mongodb://h:1/?replicaSet=rs0", "REDIS_IT_URL": "redis://h:2"}),
                          lane)
        self.assertEqual(lane.steps[0].status, SKIPPED)
        self.assertIn("QA_MONGO_CONTAINER", lane.steps[0].note)

    def test_each_run_names_a_database_of_its_own_and_keeps_the_uris_options(self) -> None:
        self.assertEqual(qa_full.with_database("mongodb://h:1/?replicaSet=rs0", "d", directConnection="true"),
                         "mongodb://h:1/d?replicaSet=rs0&directConnection=true")
        self.assertEqual(qa_full.with_database("mongodb://h:1/old?replicaSet=rs0", "d"), "mongodb://h:1/d?replicaSet=rs0")

    def test_an_unreachable_redis_has_no_free_db(self) -> None:
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        self.assertIsNone(qa_full.free_redis_db(f"redis://127.0.0.1:{port}"))


if __name__ == "__main__":
    unittest.main()
