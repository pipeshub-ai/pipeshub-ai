"""The QA agent on the default branch: a full run's results as round 0, the feature map in batches, a ship verdict."""

from __future__ import annotations

import contextlib
import io
import json
import unittest
from pathlib import Path
from unittest import mock

from qa import __main__ as qa_main
from qa import agent, features, report, run, testing
from qa.test_agent import PROVIDER, RETRIEVAL, AgentCase

AREA = """\
[[feature]]
id = "{area}.one"
title = "One"
flow = "search"
what = "w"
status = "draft"

  [[feature.behavior]]
  id = "{area}.one.b"
  says = "s"
  priority = "P0"
  tests = ["{test}"]
"""


def release_verdict(**kw):
    return {"schema": "qa-agent/v1", "verdict": "ship-with-known-issues", "summary": "Retrieval was flaky.",
            "areas": [{"area": "search", "verdict": "ship-with-known-issues", "why": "a flaky test"}],
            "rounds": [{"why": "rerun the failure", "tests": [RETRIEVAL]}],
            "findings": [{"kind": "flaky", "title": "retrieval flakes", "tests": [RETRIEVAL], "evidence": "fail then pass"}],
            "unproven": [], **kw}


class TestBatches(unittest.TestCase):
    def test_every_area_lands_in_one_batch_balanced_and_overlays_keep_their_name(self) -> None:
        fmap = {"features": [
            {"id": "a", "source": "scripts/qa/features/auth.toml", "behavior": [{}] * 6},
            {"id": "b", "source": "scripts/qa/features/search.toml", "behavior": [{}] * 4},
            {"id": "c", "source": "scripts/qa/features/chat.toml", "behavior": [{}] * 3},
            {"id": "d", "source": "scripts/plus/qa/features/extra.toml", "behavior": [{}] * 2}]}
        groups = features.batches(fmap, 2)
        self.assertEqual(groups, [["auth", "plus/extra"], ["search", "chat"]])
        self.assertEqual(sorted(a for g in groups for a in g), ["auth", "chat", "plus/extra", "search"])
        self.assertEqual(features.batches(fmap, 2), groups)  # every batch job computes the same split
        self.assertEqual(len(features.batches(fmap, 9)), 4)  # never an empty batch


class TestReleaseVerdict(unittest.TestCase):
    def test_release_values_are_accepted_only_in_a_release_run(self) -> None:
        raw = release_verdict()
        v, problems = agent.validate_verdict(raw, {RETRIEVAL}, agent.RELEASE_VERDICTS)
        self.assertEqual((v["verdict"], problems), ("ship-with-known-issues", []))
        self.assertEqual(v["areas"][0]["area"], "search")
        v, problems = agent.validate_verdict(raw, {RETRIEVAL})
        self.assertEqual(v["verdict"], "unsure")
        self.assertTrue(problems)

    def test_the_worst_batch_decides_and_a_missing_verdict_is_unsure(self) -> None:
        docs = [{"verdict": {"verdict": "ship"}}, {"verdict": {"verdict": "dont-ship"}}, {"verdict": {}}]
        self.assertEqual(report.release_verdict(docs), "dont-ship")
        self.assertEqual(report.release_verdict(docs[:1] + docs[2:]), "unsure")
        self.assertEqual(report.release_verdict(docs[:1]), "ship")


class TestReleaseMerge(unittest.TestCase):
    def test_the_batch_that_reran_a_test_beats_the_other_batches_round_zero(self) -> None:
        round0 = {"id": RETRIEVAL, "outcome": "fail", "details": "x"}
        rerun = {"id": RETRIEVAL, "outcome": "flaky", "details": "y", "agent_round": 1}
        for parts in ([{"tests": [rerun]}, {"tests": [round0]}], [{"tests": [round0]}, {"tests": [rerun]}]):
            self.assertEqual(report.merge_results(parts)["tests"][0]["outcome"], "flaky")
        both = report.merge_results([{"tests": [{**rerun, "outcome": "pass"}]}, {"tests": [{**rerun, "outcome": "fail"}]}])
        self.assertEqual(both["tests"][0]["outcome"], "fail")  # two reruns: the worst still wins


class TestReleaseCommand(AgentCase):
    def setUp(self) -> None:
        super().setUp()
        testing.write(self.root, "scripts/qa/features/search.toml", AREA.format(area="search", test=RETRIEVAL))
        testing.write(self.root, "scripts/qa/features/graph.toml", AREA.format(area="graph", test=PROVIDER))
        testing.commit(self.root, "map")
        self.full = self.root.parent / f"{self.root.name}-full"
        self.full.mkdir()
        self.addCleanup(lambda: __import__("shutil").rmtree(self.full, ignore_errors=True))
        plan = {"base": self.base, "head": None, "mode": "full", "edition": "oss", "profile": "full", "stack": {"needed": False},
                "tests": [{"id": RETRIEVAL, "why": ["full"], "module": None, "source": "rules"}, {"id": PROVIDER, "why": ["full"], "module": None, "source": "rules"}]}
        (self.full / "plan.json").write_text(json.dumps(plan))
        (self.full / "results-unit.json").write_text(json.dumps({"tests": [
            {"id": RETRIEVAL, "outcome": "fail", "details": "assert 2 == 3"}, {"id": PROVIDER, "outcome": "fail", "details": "boom"}]}))

    def agent(self, *extra: str) -> tuple[int, str]:
        err = io.StringIO()
        with mock.patch.object(run, "run", side_effect=AssertionError("round 0 must come from the full run")), \
             mock.patch.object(agent.paths, "REPO", Path(__file__).resolve().parents[2]), \
             contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
            code = qa_main.main(["--out", str(self.out), "agent", "--full-run", str(self.full), "--stack", "none", *extra])
        return code, err.getvalue()

    def test_a_batch_takes_its_areas_keeps_tests_and_map_edits_and_reverts_product_code(self) -> None:
        self.scenario.write_text(json.dumps({
            "rounds": {"1": [{"id": RETRIEVAL, "outcome": "pass"}]},
            "write": {"backend/python/tests/unit/services/test_new.py": "def test_x():\n    assert 1\n",
                      "scripts/qa/features/search.toml": AREA.format(area="search", test=RETRIEVAL) + "# linked\n",
                      "backend/python/app/services/retrieval.py": "def search():\n    return []\n"},
            "verdict": release_verdict()}))
        code, err = self.agent("--batch", "1/2")
        self.assertEqual(code, 0, err)
        inp = json.loads((self.out / "agent/input.json").read_text())
        self.assertEqual((inp["mode"], inp["batch"]), ("release", "1/2"))
        self.assertEqual(len(inp["areas"]), 1)
        mine = inp["areas"][0]
        linked = RETRIEVAL if mine == "search" else PROVIDER
        self.assertEqual([p["id"] for p in inp["round0"]["problems"]], [linked])  # only this batch's areas' failures
        self.assertEqual(json.loads(Path(inp["features"]).read_text())["features"][0]["id"], f"{mine}.one")
        argv = json.loads(Path(str(self.scenario) + ".argv").read_text())
        self.assertIn("whole product on the default branch", argv[argv.index("-p") + 1])
        doc = json.loads((self.out / "agent-batch-1.json").read_text())
        self.assertEqual(doc["verdict"]["verdict"], "ship-with-known-issues")
        self.assertEqual(doc["release"]["batch"], "1/2")
        self.assertIn("scripts/qa/features/search.toml", doc["tests_added"])
        self.assertIn("backend/python/tests/unit/services/test_new.py", doc["tests_added"])
        self.assertIn("backend/python/app/services/retrieval.py", " ".join(doc["notes"]))
        self.assertEqual((self.root / "backend/python/app/services/retrieval.py").read_text(), "def search():\n    return [1, 2]\n")
        self.assertIn("scripts/qa/features/search.toml", (self.out / "agent-tests-1.patch").read_text())
        results = {t["id"]: t for t in json.loads((self.out / "results-agent-1.json").read_text())["tests"]}
        self.assertEqual(results[RETRIEVAL]["outcome"], "flaky")
        self.assertEqual(results[RETRIEVAL]["agent_round"], 1)

        # The report job: every batch's files in one directory.
        (self.out / "results-agent-2.json").write_text(json.dumps({"tests": [
            {"id": RETRIEVAL, "outcome": "fail", "details": "round 0"}, {"id": PROVIDER, "outcome": "pass", "agent_round": 1}]}))
        (self.out / "agent-batch-2.json").write_text(json.dumps({
            "verdict": {"verdict": "dont-ship", "summary": "graph broke", "findings": []},
            "release": {"batch": "2/2", "areas": ["graph"]}, "tests_added": [], "extra_tests": [], "notes": []}))
        with contextlib.redirect_stdout(io.StringIO()):
            qa_main.main(["--out", str(self.out), "report", "--quiet"])
        body = (self.out / "report.md").read_text()
        self.assertIn("### Release verdict", body)
        self.assertIn("**dont-ship** over 2 batches", body)
        self.assertIn("### QA agent: batch 1/2", body)
        self.assertIn("round 0 is the full run's results", body)

    def test_a_run_that_is_not_full_and_a_bad_batch_are_refused(self) -> None:
        self.assertEqual(self.agent("--batch", "3/2")[0], 2)
        plan = json.loads((self.full / "plan.json").read_text())
        (self.full / "plan.json").write_text(json.dumps({**plan, "mode": "pr"}))
        code, err = self.agent()
        self.assertEqual(code, 2)
        self.assertIn("not a full run", err)


if __name__ == "__main__":
    unittest.main()
