"""qa improve: weak tests of the change, then stronger and missing tests, kept only when proven.

Run: python3 -m unittest discover -s scripts -p 'test_*.py'
"""

import contextlib
import io
import json
import os
import shutil
import unittest
from pathlib import Path
from unittest import mock

from qa import __main__ as qa_main
from qa import config, generate, improve, paths, planner, prove, run, testing

PROVIDER = "backend/python/app/services/graph_db/provider.py"
TEST_PATH = "backend/python/tests/unit/services/graph_db/test_provider.py"
TEST_ID = "py:tests/unit/services/graph_db/test_provider.py"
COMPANION = "backend/python/tests/unit/services/graph_db/test_provider_qa.py"
GOOD = "from app.services.graph_db import provider\n\n\ndef test_empty() -> None:\n    assert provider.get_records() == []\n"


def reply(kind="stronger", level="unit", content=GOOD, covers=(PROVIDER,), **kw) -> dict:
    t = {"kind": kind, "level": level, "title": f"{kind} test", "covers": list(covers), "content": content}
    if kind == "stronger":
        t["for"] = TEST_ID
    t.update(kw)
    return t


class ImproveCase(unittest.TestCase):
    def setUp(self) -> None:
        testing.without_ci_env(self)
        self.root, tmp = testing.make_repo()
        self.addCleanup(tmp.cleanup)
        self.base = testing.git(self.root, "rev-parse", "HEAD")
        testing.write(self.root, PROVIDER, "def get_records(flag=False):\n    if flag:\n        return [1]\n    return []\n")
        testing.commit(self.root, "change the provider")
        self.out = self.root.parent / f"{self.root.name}-out"  # outside the repo, so git status shows only the repo
        self.addCleanup(lambda: shutil.rmtree(self.out, ignore_errors=True))
        self.asked: list[dict] = []
        self.proved: list[str] = []

    def qa(self, *argv: str, tests=(), status="ok", verdicts=None, outcomes=None) -> int:
        def fake_ask(root, adapter, task, input_path, output_path, timeout=600):
            self.assertEqual(task, "improve")
            self.asked.append(json.loads(input_path.read_text(encoding="utf-8")))
            st = {"adapter": "test", "status": status, **({"error": "Timeout: 900s"} if status == "failed" else {})}
            output_path.write_text(json.dumps({"tests": list(tests) if status == "ok" else [], "_planner": st}), encoding="utf-8")
            return st

        def fake_prove(cfg, row, out, **kw):
            self.proved.append(row["id"])
            self.assertTrue((cfg.root / row["path"]).is_file())
            return (verdicts or {}).get(row["id"], {"verdict": "proven", "reason": "fails under mutation"})

        def fake_run(cfg, plan, rows, out, **opts):
            # head runs in the checkout pass; every variant gets the next outcome.
            tid = plan["tests"][0]["id"]
            tree = Path(os.environ["QA_ROOT"]).resolve()
            outcome = "pass" if tree == self.root.resolve() else outcomes.pop(0)
            return {"schema": "qa-results/v1", "tests": [{"id": tid, "outcome": outcome, "seconds": 0.1}],
                    "invocations": [], "fatal": []}

        patches = [mock.patch.object(paths, "REPO", self.root), mock.patch.dict(os.environ, {"QA_ROOT": str(self.root)}),
                   mock.patch.object(planner, "ask", fake_ask)]
        patches.append(mock.patch.object(run, "run", fake_run) if outcomes is not None
                       else mock.patch.object(prove, "prove", fake_prove))
        with contextlib.ExitStack() as stack:
            for p in patches:
                stack.enter_context(p)
            so, err = io.StringIO(), io.StringIO()
            stack.enter_context(contextlib.redirect_stdout(so))
            stack.enter_context(contextlib.redirect_stderr(err))
            config.clear_cache()
            code = qa_main.main(["--out", str(self.out), "improve", "--base", self.base, *argv])
        config.clear_cache()
        self.stdout, self.err = so.getvalue(), err.getvalue()
        path = self.out / "improve.json"
        self.result = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None
        return code

    def changed(self) -> set:
        # config.load imports the overlay hook, which leaves bytecode; that is not an edit.
        return {p for p in (testing.git(self.root, "diff", "--name-only", "HEAD") + "\n"
                            + testing.git(self.root, "ls-files", "--others", "--exclude-standard")).split()
                if "__pycache__" not in p}

    def weak_file(self, data) -> str:
        path = self.out.parent / f"{self.root.name}-weak.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        self.addCleanup(path.unlink, missing_ok=True)
        return str(path)


class TestCandidatesAndCheck(ImproveCase):
    def test_the_mirror_test_of_a_changed_source_is_the_candidate(self) -> None:
        cfg = config.load(self.root)
        from qa import catalog
        cands = improve.candidates(cfg, catalog.build(cfg), [PROVIDER], 20)
        self.assertEqual([(c["id"], c["covers"]) for c in cands], [(TEST_ID, [PROVIDER])])
        self.assertEqual(improve.candidates(cfg, catalog.build(cfg), [PROVIDER], 0), [])

    def test_a_test_that_passes_every_mutant_is_weak(self) -> None:
        self.assertEqual(self.qa("--check", outcomes=["pass"] * 10), 0)
        check = json.loads((self.out / "improve-check.json").read_text(encoding="utf-8"))
        self.assertEqual([w["id"] for w in check["weak"]], [TEST_ID])
        self.assertTrue(check["weak"][0]["revert"])
        self.assertEqual(len(check["weak"][0]["survived"]), 3)  # --check-mutants defaults to 3
        self.assertEqual(self.changed(), set())
        self.assertEqual(self.asked, [])

    def test_a_test_a_variant_breaks_is_strong(self) -> None:
        self.assertEqual(self.qa("--check", outcomes=["fail", "fail"]), 0)
        check = json.loads((self.out / "improve-check.json").read_text(encoding="utf-8"))
        self.assertEqual((check["weak"], [s["id"] for s in check["strong"]]), ([], [TEST_ID]))

    def test_the_check_stops_at_its_time_budget(self) -> None:
        self.assertEqual(self.qa("--check", "--minutes", "-1", outcomes=[]), 0)
        check = json.loads((self.out / "improve-check.json").read_text(encoding="utf-8"))
        self.assertEqual(check["unchecked"], [{"id": TEST_ID, "reason": "time budget spent"}])


class TestDraft(ImproveCase):
    WEAK = [{"id": TEST_ID, "revert": False, "survived": [{"file": PROVIDER, "line": 2, "op": "if -> if not"}]}]

    def test_draft_checks_replies_picks_new_paths_and_writes_nothing(self) -> None:
        forged = self.WEAK + [{"id": "py:tests/unit/services/test_retrieval.py", "revert": True, "survived": []},
                              {"id": TEST_ID, "revert": True, "survived": []}]
        forged[0] = {**forged[0], "survived": forged[0]["survived"] + [{"file": ".github/x.py", "line": 1, "op": "rm -rf"}]}
        tests = [reply(), reply("missing"), reply("missing", level="integration"),
                 reply("missing", content="import os\n" + GOOD), reply(**{"for": "py:tests/unit/other.py"}),
                 reply("missing", covers=["backend/python/app/services/untested.py"])]
        self.assertEqual(self.qa("--draft", "--weak", self.weak_file(forged), tests=tests), 0)
        # Only the candidate id, once, with only the mutant prove could have made.
        self.assertEqual(self.asked[0]["weak"], [{"id": TEST_ID, "path": TEST_PATH, "revert": False,
                                                  "survived": self.WEAK[0]["survived"]}])
        self.assertIn("if flag", self.asked[0]["diff"])
        drafted = [(d["kind"], d["path"], d["id"]) for d in self.result["drafted"]]
        self.assertEqual(drafted, [("stronger", COMPANION, "py:tests/unit/services/graph_db/test_provider_qa.py"),
                                   ("missing", COMPANION.replace("_qa.py", "_qa_2.py"),
                                    "py:tests/unit/services/graph_db/test_provider_qa_2.py")])
        whys = " | ".join(d["why"] for d in self.result["dropped"])
        for needle in ("needs a stack", "imports os", "not a weak test", "covers no changed Python file"):
            self.assertIn(needle, whys)
        self.assertEqual(self.changed(), set())
        self.assertEqual(json.loads((self.out / "improve" / "1-llm.json").read_text(encoding="utf-8")), {"content": GOOD})
        self.assertIn("passes with `if -> if not`", (self.out / "improve.md").read_text(encoding="utf-8"))

    def test_an_unreadable_weak_list_still_drafts_missing_tests(self) -> None:
        bad = self.out.parent / f"{self.root.name}-bad.json"
        bad.write_text("{not json", encoding="utf-8")
        self.addCleanup(bad.unlink, missing_ok=True)
        self.assertEqual(self.qa("--draft", "--weak", str(bad), tests=[reply(), reply("missing")]), 0)
        self.assertEqual(self.asked[0]["weak"], [])
        self.assertEqual([d["kind"] for d in self.result["drafted"]], ["missing"])

    def test_a_model_failure_is_reported_as_skipped(self) -> None:
        self.assertEqual(self.qa("--draft", status="failed"), 0)
        self.assertEqual((self.result["drafted"], self.result["skipped"]), ([], "the model failed: Timeout: 900s"))
        self.assertIn("Skipped: the model failed", (self.out / "improve.md").read_text(encoding="utf-8"))

    def test_the_cap_on_missing_tests_holds(self) -> None:
        self.assertEqual(self.qa("--draft", "--max-new", "1", tests=[reply("missing"), reply("missing")]), 0)
        self.assertEqual(len(self.result["drafted"]), 1)


class TestFilesForTheModel(ImproveCase):
    """The engine that reads PR files for the model holds the model keys in its environment."""

    def test_only_regular_files_inside_the_tree_are_read(self) -> None:
        outside = self.root.parent / f"{self.root.name}-outside.py"
        outside.write_text("SECRET = 1\n", encoding="utf-8")
        self.addCleanup(outside.unlink, missing_ok=True)
        d = self.root / "backend/python/app/services"
        (d / "environ.py").symlink_to("/proc/self/environ")
        (d / "outside.py").symlink_to(outside)
        (d / "zero.py").symlink_to("/dev/zero")
        (d / "folder.py").mkdir()
        (d / "inner.py").symlink_to(d / "retrieval.py")  # inside the tree, but still a link
        (self.root / "linkdir").symlink_to("/proc/self")
        for name in ("environ.py", "outside.py", "zero.py", "folder.py", "inner.py", "missing.py"):
            self.assertIsNone(generate.tree_text(self.root, f"backend/python/app/services/{name}", 100), name)
        self.assertIsNone(generate.tree_text(self.root, "linkdir/environ", 100))
        self.assertEqual(generate.tree_text(self.root, PROVIDER, 7), "def get")

    def test_a_changed_source_linked_to_the_environment_never_reaches_the_model(self) -> None:
        link = "backend/python/app/services/graph_db/leak.py"
        (self.root / link).symlink_to("/proc/self/environ")
        (self.root / TEST_PATH).unlink()
        (self.root / TEST_PATH).symlink_to("/proc/self/environ")
        testing.commit(self.root, "links")
        weak = [{"id": TEST_ID, "revert": True, "survived": []}]
        with mock.patch.dict(os.environ, {"QA_FAKE_KEY": "sk-should-never-leak"}):
            self.assertEqual(self.qa("--draft", "--weak", self.weak_file(weak), tests=[reply("missing")]), 0)
        text = (self.out / "improve" / "improve-input.json").read_text(encoding="utf-8")
        self.assertNotIn("sk-should-never-leak", text)
        self.assertNotIn(link, self.asked[0]["sources"])
        self.assertEqual([f["path"] for f in self.asked[0]["files"]], [PROVIDER])


class TestProveDrafts(ImproveCase):
    def draft(self) -> None:
        weak = self.weak_file(TestDraft.WEAK)
        self.assertEqual(self.qa("--draft", "--weak", weak, tests=[reply(), reply("missing")]), 0)

    def test_only_proven_files_stay_and_nothing_else_changes(self) -> None:
        self.draft()
        second = "py:tests/unit/services/graph_db/test_provider_qa_2.py"
        with mock.patch.object(planner, "ask", side_effect=AssertionError("asked")):
            code = self.qa("--from-drafts", verdicts={second: {"verdict": "unproven", "reason": prove.SURVIVED}})
        self.assertEqual(code, 0)
        self.assertEqual([k["id"] for k in self.result["kept"]], ["py:tests/unit/services/graph_db/test_provider_qa.py"])
        self.assertEqual(self.result["removed"], [{"id": second, "reason": prove.SURVIVED}])
        self.assertEqual(self.changed(), {COMPANION})
        self.assertEqual((self.root / COMPANION).read_text(encoding="utf-8"), GOOD)
        self.assertIn("nothing was committed", self.stdout)

    def test_a_tampered_draft_is_rechecked(self) -> None:
        self.draft()
        data = json.loads((self.out / "improve.json").read_text(encoding="utf-8"))
        data["drafted"][0]["path"] = ".github/workflows/test_x.py"
        data["drafted"][1]["reply"] = "../../evil.json"
        (self.out / "improve.json").write_text(json.dumps(data), encoding="utf-8")
        (self.out / "improve" / "1-llm.json").write_text(json.dumps({"content": "import os\n" + GOOD}), encoding="utf-8")
        self.assertEqual(self.qa("--from-drafts"), 0)
        self.assertEqual([r["reason"] for r in self.result["removed"]], ["unsafe path", "no draft for this id"])
        self.assertEqual((self.proved, self.changed()), ([], set()))

    def test_a_taken_path_is_never_overwritten(self) -> None:
        self.draft()
        testing.write(self.root, COMPANION, "# a person's file\n")
        self.assertEqual(self.qa("--from-drafts"), 0)
        self.assertEqual(self.result["removed"][0]["reason"], "the path is taken")
        self.assertEqual((self.root / COMPANION).read_text(encoding="utf-8"), "# a person's file\n")

    def test_a_prove_crash_deletes_the_file(self) -> None:
        self.draft()
        with mock.patch.object(prove, "prove", side_effect=RuntimeError("boom")), self.assertRaises(RuntimeError):
            with mock.patch.object(paths, "REPO", self.root), mock.patch.dict(os.environ, {"QA_ROOT": str(self.root)}):
                config.clear_cache()
                improve.prove_drafts(config.load(self.root), self.out, 15)
        self.assertEqual(self.changed(), set())


class TestCli(ImproveCase):
    def test_the_whole_loop_leaves_one_proven_companion_uncommitted(self) -> None:
        # check: the revert and 3 mutants all pass (weak); prove: the companion fails on the revert, twice.
        self.assertEqual(self.qa(tests=[reply()], outcomes=["pass"] * 4 + ["fail", "fail"]), 0)
        self.assertEqual([w["id"] for w in self.result["weak"]], [TEST_ID])
        self.assertEqual(self.result["kept"], [{"id": "py:tests/unit/services/graph_db/test_provider_qa.py",
                                                "reason": "fails when the change is reverted"}])
        self.assertEqual(self.changed(), {COMPANION})
        self.assertEqual(testing.git(self.root, "log", "--format=%s", "-1"), "change the provider")

    def test_go_improve_cannot_go_with_plan_only_or_commit(self) -> None:
        for extra in (["--plan-only"], ["--commit", "HEAD"], ["--dry-run"]):
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                qa_main.main(["go", "--improve", *extra])

    def test_prove_phase_needs_a_draft(self) -> None:
        self.assertEqual(self.qa("--from-drafts"), 2)
        self.assertIn("run improve --draft first", self.err)

    def test_new_files_may_hold_several_tests_but_nothing_else(self) -> None:
        self.assertEqual(generate.check_content(None, GOOD + "\n\ndef test_two():\n    assert 1 == 1\n"), [])
        self.assertIn("no test function", generate.check_content(None, "X = 1\n"))
        for extra in ("\ndef helper():\n    return 1\n", "\nclass TestMore:\n    pass\n", "\nprint('x')\n",
                      "\ndef test_empty():\n    assert 2 == 2\n"):
            self.assertTrue(any("module level" in p for p in generate.check_content(None, GOOD + extra)), extra)
        self.assertTrue(generate.check_content(None, GOOD.replace("assert provider.get_records() == []", "provider.get_records()")))



setUpModule, tearDownModule = testing.hide_engine_env, testing.restore_engine_env


if __name__ == "__main__":
    unittest.main()
