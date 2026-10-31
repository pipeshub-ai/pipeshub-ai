"""Review: requirements traced to this run's plan, deterministic risks, and a sanitised comment.

Run: python3 -m unittest discover -s scripts -p 'test_*.py'
"""

import contextlib
import io
import json
import os
import unittest
from unittest import mock

from qa import __main__ as qa_main
from qa import catalog, collect, config, paths, planner, report, review, run, testing

ACL = "it:retrieval/test_search.py::test_acl_filter"
UNIT = "py:tests/unit/services/test_retrieval.py"
PROBE = "node:tests/modules/health.test.ts"
RUN_URL = "https://github.com/o/r/actions/runs/42"


class ReviewCase(unittest.TestCase):
    def setUp(self) -> None:
        self.root, tmp = testing.make_repo()
        self.addCleanup(tmp.cleanup)
        base = testing.git(self.root, "rev-parse", "HEAD")
        testing.write(self.root, "backend/python/app/services/retrieval.py", "def search():\n    return [2]\n")
        testing.write(self.root, "backend/nodejs/apps/src/modules/auth/auth.service.ts", "export const check = () => false;\n")
        testing.commit(self.root, "change retrieval and auth")
        out = self.root / "qa-out"
        # The fixture's own rules, not the engine checkout's.
        with mock.patch.object(paths, "REPO", self.root), mock.patch.dict(os.environ, {"QA_ROOT": str(self.root)}), \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            code = qa_main.main(["--out", str(out), "go", "--adapter", "none", "--plan-only", "--base", base])
            self.cfg = config.load(self.root)
        self.assertEqual(code, 0)
        self.plan = json.loads((out / "plan.json").read_text(encoding="utf-8"))
        self.changeset = json.loads((out / "changeset.json").read_text(encoding="utf-8"))
        self.rows = catalog.index(catalog.build(self.cfg))
        self.assertIn(ACL, {t["id"] for t in self.plan["tests"]})

    def results(self, **outcomes) -> dict:
        tests = [{"id": t["id"], "outcome": "pass", "seconds": 1.0} for t in self.plan["tests"]]
        for t in tests:
            t.update(outcomes.get(t["id"], {}))
        return {"schema": "qa-results/v1", "tests": tests, "fatal": []}

    def review(self, llm, results=None, body: str = "") -> dict:
        pr = {"number": 7, "title": "Rename a KB", "body": body, "issues": [], "commits": ["rename"]}
        rv_input = review.build_input(self.plan, results, self.changeset, pr, cfg=self.cfg)
        return review.merge(self.cfg, self.plan, results, rv_input, llm, self.rows)

    @staticmethod
    def llm(requirements=(), checklist=(), risks=(), status="ok") -> dict:
        return {"requirements": list(requirements), "checklist": list(checklist), "risks": list(risks),
                "_planner": {"adapter": "test", "status": status}}


class TestReview(ReviewCase):
    def test_requirements_from_text_reads_bullets_and_checkboxes(self) -> None:
        body = "## Summary\n- Users can rename a KB\n* [x] Rename is audited\n1. Old name 404s\nplain text\n<!--\n- hidden\n-->"
        self.assertEqual(review.requirements_from_text(body), ["Users can rename a KB", "Rename is audited", "Old name 404s"])

    def test_traceability_drops_unknown_ids(self) -> None:
        rv = self.review(self.llm([{"text": "ACL hides records", "tests": [ACL, "py:nope.py"]}]),
                         self.results(**{ACL: {"outcome": "fail"}}))
        self.assertEqual(rv["requirements"][0]["tests"], [{"id": ACL, "outcome": "fail"}])
        self.assertEqual(rv["requirements"][0]["result"], "fail")
        self.assertEqual(rv["dropped"], [{"id": "py:nope.py", "why": "not in this plan"}])

    def big_plan(self) -> tuple[dict, dict]:
        bulk = [{"id": f"e2e:t/{i}.spec.ts", "flows": [], "why": ["full run: tracked file x"]} for i in range(6000)]
        plan = {**self.plan, "tests": self.plan["tests"] + bulk}
        res = {"tests": [{"id": t["id"], "outcome": "fail" if i % 1000 == 7 else "pass"} for i, t in enumerate(plan["tests"])]}
        return plan, res

    def test_review_input_stays_bounded_and_keeps_failures(self) -> None:
        plan, res = self.big_plan()
        failed = {t["id"] for t in res["tests"] if t["outcome"] == "fail"}
        rv_input = review.build_input(plan, res, self.changeset, {"title": "t"}, cfg=self.cfg)
        self.assertLess(len(json.dumps(rv_input)), planner.MAX_INPUT_BYTES)
        self.assertLessEqual(len(rv_input["tests"]), review.MAX_INPUT_TESTS)
        self.assertLessEqual(failed, {t["id"] for t in rv_input["tests"]})
        self.assertEqual(rv_input["tests_total"], len(plan["tests"]))
        self.assertEqual(rv_input["tests_omitted"], len(plan["tests"]) - len(rv_input["tests"]))

    def test_adapter_none_on_a_big_plan_is_skipped(self) -> None:
        plan, res = self.big_plan()
        src = self.root / "qa-out" / "big-input.json"
        src.write_text(json.dumps({"filler": "x" * (planner.MAX_INPUT_BYTES + 1)}), encoding="utf-8")
        status = planner.ask(self.root, "none", "review", src, self.root / "qa-out" / "big-out.json")
        self.assertEqual(status["status"], "skipped")

    def test_an_omitted_but_planned_id_cited_by_the_model_is_kept(self) -> None:
        plan, res = self.big_plan()
        omitted = "e2e:t/5.spec.ts"
        rv_input = review.build_input(plan, res, self.changeset, {"title": "t"}, cfg=self.cfg)
        self.assertNotIn(omitted, {t["id"] for t in rv_input["tests"]})
        rv = review.merge(self.cfg, plan, res, rv_input, self.llm([{"text": "r", "tests": [omitted]}]), self.rows)
        self.assertEqual(rv["requirements"][0]["tests"], [{"id": omitted, "outcome": "pass"}])

    def test_model_failure_falls_back_to_the_pr_bullets(self) -> None:
        llm = self.llm([{"text": "ignored", "tests": [ACL]}], [{"area": "a", "probe": "p", "test": ACL}], status="failed")
        rv = self.review(llm, self.results(), body="- Users can rename a KB\n- Rename is audited")
        self.assertEqual([(r["text"], r["result"]) for r in rv["requirements"]],
                         [("Users can rename a KB", "untested"), ("Rename is audited", "untested")])
        self.assertEqual(rv["checklist"], [])
        self.assertEqual(rv["model"]["status"], "failed")

    def test_risks_name_p0_flows_migrations_auth_and_missing(self) -> None:
        plan = {**self.plan, "missing": [{"source": "a.py"}, {"source": "b.py"}]}
        risks = review.risks(self.cfg, plan, self.changeset, self.rows)
        self.assertEqual([r["id"] for r in risks], ["flow:search", "auth", "missing"])
        self.assertEqual(risks[-1]["why"], "2 missing tests")

    def test_review_comment_sanitizes_pr_and_model_text(self) -> None:
        text = "ping @admin see https://x.y <img src=a> | col"
        rv = self.review(self.llm([{"text": text, "tests": [ACL]}]), self.results())
        body = review.render(rv, "oss")
        for bad in ("@admin", "https://", "<img", "`"):
            self.assertNotIn(bad, body)
        row = next(line for line in body.splitlines() if "ping" in line)
        self.assertEqual(row.count("|"), 4)
        self.assertTrue(body.startswith(review.marker("oss")))

    def test_plan_only_review_marks_requirements_not_run(self) -> None:
        rv = self.review(self.llm([{"text": "ACL hides records", "tests": [ACL]}]))
        self.assertEqual(rv["requirements"][0]["result"], "not_run")
        self.assertEqual({n["id"] for n in rv["not_run"]}, {t["id"] for t in self.plan["tests"]})
        self.assertEqual({n["kind"] for n in rv["not_run"]}, {"plan only"})

    def test_every_not_run_is_listed_with_kind_and_reason(self) -> None:
        results = self.results(**{ACL: {"outcome": "not_run", "kind": "secret", "details": "secret: JIRA_TOKEN"}})
        rv = self.review(self.llm(), results)
        body = review.render(rv, "oss", run_url=RUN_URL)
        not_tested = body.split("### Not tested", 1)[1]
        self.assertIn(f"{ACL} (secret): secret: JIRA_TOKEN", not_tested)
        self.assertTrue(body.rstrip().endswith(f"Screenshots, traces and logs: {RUN_URL}"))

    def test_run_url_outside_actions_runs_is_dropped(self) -> None:
        rv = self.review(self.llm(), self.results())
        for url in ("https://evil.example/x", f"{RUN_URL} @admin", "http://github.com/o/r/actions/runs/1"):
            self.assertNotIn("Screenshots", review.render(rv, "oss", run_url=url), url)

    def test_not_run_kind_is_sanitised(self) -> None:
        results = self.results(**{ACL: {"outcome": "not_run", "kind": "@admin|x", "details": "d"}})
        body = review.render(self.review(self.llm(), results), "oss")
        self.assertNotIn("@admin", body)

    def test_gather_reads_the_pr_and_its_first_three_issues(self) -> None:
        pr = {"title": "T", "body": "B", "commits": [{"messageHeadline": "one", "oid": "a"}],
              "closingIssuesReferences": [{"number": n} for n in (1, 2, 3, 4)]}
        calls = []

        def gh(cmd, **_):
            calls.append(cmd)
            data = pr if cmd[1] == "pr" else {"title": f"I{cmd[3]}", "body": "x"}
            return mock.Mock(stdout=json.dumps(data))
        with mock.patch.object(review.subprocess, "run", side_effect=gh):
            got = review.gather(7, "o/r")
        self.assertEqual(got["commits"], ["one"])
        self.assertEqual([i["number"] for i in got["issues"]], [1, 2, 3])
        self.assertEqual((got["number"], got["title"], got["body"]), (7, "T", "B"))
        self.assertEqual(calls[0][:4], ["gh", "pr", "view", "7"])
        self.assertTrue(all(c[-2:] == ["--repo", "o/r"] for c in calls))


REPLY = {"requirements": [{"text": "ACL hides records", "tests": [ACL]}],
         "checklist": [{"area": "search", "probe": "ranking still sorted", "test": ACL},
                       {"area": "health", "probe": "health stays up", "test": PROBE}],
         "risks": []}


class TestReviewCommand(ReviewCase):
    def setUp(self) -> None:
        super().setUp()
        self.out = self.root / "qa-out"
        (self.out / "plan.json").write_text(json.dumps({**self.plan, "edition": "oss"}), encoding="utf-8")

    def main(self, *argv: str) -> tuple[int, str]:
        def ask(root, adapter, task, input_path, output_path, timeout=600):
            self.assertEqual(task, "review")
            output_path.write_text(json.dumps({**REPLY, "_planner": {"adapter": adapter, "status": "ok"}}), encoding="utf-8")
            return {"adapter": adapter, "status": "ok"}
        err = io.StringIO()
        with mock.patch.object(paths, "REPO", self.root), mock.patch.dict(os.environ, {"QA_ROOT": str(self.root)}), \
                mock.patch.object(planner, "ask", side_effect=ask), \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
            code = qa_main.main(["--out", str(self.out), "review", *argv])
        return code, err.getvalue()

    def test_cmd_review_writes_files_and_posts_one_sticky_comment(self) -> None:
        pr = {"number": 7, "title": "T", "body": "- ACL hides records", "issues": [], "commits": ["c"]}
        with mock.patch.object(review, "gather", return_value=pr) as gather, \
                mock.patch.object(report, "post_comment", return_value="url") as post:
            code, _ = self.main("--pr", "7", "--repo", "o/r", "--comment", "--run-url", RUN_URL)
        self.assertEqual(code, 0)
        gather.assert_called_once_with(7, "o/r")
        self.assertTrue((self.out / "review.json").is_file())
        md = (self.out / "review.md").read_text(encoding="utf-8")
        post.assert_called_once()
        self.assertEqual(post.call_args.kwargs["tag"], "<!-- qa-review:oss -->")
        self.assertEqual(post.call_args.args[:3], ("o/r", 7, md))
        # CI does not run probes: it lists the command that does.
        probes = next(line for line in md.splitlines() if "python3 scripts/qa run --ids" in line)
        self.assertIn(PROBE, probes)

    def test_review_probes_run_once_and_failures_become_bugs(self) -> None:
        results = {"schema": "qa-results/v1", "fatal": [],
                   "tests": [{"id": t["id"], "outcome": "pass", "seconds": 1.0} for t in self.plan["tests"]]}
        (self.out / "results.json").write_text(json.dumps(results), encoding="utf-8")
        before = {p.name: p.read_bytes() for p in self.out.glob("results*.json")}
        probe = {"schema": "qa-results/v1", "fatal": [],
                 "tests": [{"id": PROBE, "outcome": "fail", "details": "boom", "seconds": 1.0}]}
        with mock.patch.object(run, "run", return_value=probe) as runner:
            code, _ = self.main("--run")
        self.assertEqual(code, 0)
        runner.assert_called_once()
        self.assertEqual([t["id"] for t in runner.call_args.args[1]["tests"]], [PROBE])
        self.assertEqual(runner.call_args.args[3], self.out / "review")
        self.assertIs(runner.call_args.kwargs["retry"], False)
        rv = json.loads((self.out / "review.json").read_text(encoding="utf-8"))
        self.assertEqual(rv["bugs"][0]["id"], PROBE)
        self.assertEqual(rv["bugs"][0]["details"], "boom")
        self.assertEqual(rv["bugs"][0]["repro"][-1], f"python3 scripts/qa run --ids {PROBE}")
        self.assertTrue((self.out / "review" / "results.json").is_file())
        self.assertEqual({p.name: p.read_bytes() for p in self.out.glob("results*.json")}, before)

    def test_cmd_review_without_a_plan_explains(self) -> None:
        (self.out / "plan.json").unlink()
        code, err = self.main()
        self.assertEqual(code, 2)
        self.assertIn("go --plan-only", err)


if __name__ == "__main__":
    unittest.main()
