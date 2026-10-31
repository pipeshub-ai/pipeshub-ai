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
from qa import catalog, collect, config, paths, review, testing

ACL = "it:retrieval/test_search.py::test_acl_filter"
UNIT = "py:tests/unit/services/test_retrieval.py"


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
        body = review.render(rv, "oss", run_url="https://example.invalid/run/1")
        not_tested = body.split("### Not tested", 1)[1]
        self.assertIn(f"{ACL} (secret): secret: JIRA_TOKEN", not_tested)
        self.assertTrue(body.rstrip().endswith("Screenshots, traces and logs: https://example.invalid/run/1"))


if __name__ == "__main__":
    unittest.main()
