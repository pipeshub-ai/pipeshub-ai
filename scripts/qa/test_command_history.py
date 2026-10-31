"""/qa command parsing, history and culprit search.

Run: python3 -m unittest discover -s scripts -p 'test_*.py'
"""

import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from qa import __main__ as qa_main
from qa import command, config, culprit, history, run, testing


FULL = "e4f5a6b" + "0" * 33


class TestCommand(unittest.TestCase):
    def test_range_and_since(self) -> None:
        self.assertEqual(command.parse("/qa range abc1234..def5678", "write")["head"], "def5678")
        self.assertEqual(command.parse("/qa since abc1234", "write")["base"], "abc1234")

    def test_trusted_needs_a_sha(self) -> None:
        with self.assertRaises(command.CommandError):
            command.parse("/qa run --trusted", "admin")
        self.assertEqual(command.parse(f"/qa run --trusted {FULL}", "admin")["trusted_head"], FULL)
        with self.assertRaises(command.CommandError):
            command.parse("/qa run --trusted abc1234", "admin")
        with self.assertRaises(command.CommandError):
            command.parse("/qa run --trusted xyz", "admin")

    def test_trusted_does_not_replace_the_range_head(self) -> None:
        out = command.parse(f"/qa range a1b2c3d..e4f5a6b --trusted {FULL}", "admin")
        self.assertEqual((out["head"], out["trusted_head"]), ("e4f5a6b", FULL))

    def test_since_is_its_own_mode(self) -> None:
        self.assertEqual(command.parse("/qa since abc1234", "write")["mode"], "since")

    def test_missing_only_plans(self) -> None:
        self.assertIs(command.parse("/qa missing", "write")["plan_only"], True)
        self.assertIs(command.parse("/qa run", "write")["plan_only"], False)

    def test_full_needs_admin(self) -> None:
        with self.assertRaises(command.CommandError):
            command.parse("/qa full", "write")
        self.assertEqual(command.parse("/qa full", "admin")["mode"], "full")
        self.assertEqual(command.parse("/qa full", "write", is_admin_team=True)["mode"], "full")

    def test_untrusted_arguments_are_rejected(self) -> None:
        for body in ("/qa range $(curl evil)", "/qa since main; rm -rf /", "/qa deploy", "/qa range abc"):
            with self.assertRaises(command.CommandError, msg=body):
                command.parse(body, "admin")

    def test_read_permission_cannot_run_anything(self) -> None:
        with self.assertRaises(command.CommandError):
            command.parse("/qa", "read")

    def test_profile_flag(self) -> None:
        self.assertEqual(command.parse("/qa run --profile conservative", "write")["profile"], "conservative")


class TestHistory(unittest.TestCase):
    def test_failing_on_main_and_flaky_rates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = Path(tmp) / "h.jsonl"
            for outcome in ("fail", "flaky", "pass"):
                plan = {"head": "x", "branch": "main", "tests": [{"id": "a", "source": "floor"}], "planner": {}}
                history.record(store, plan, {"tests": [{"id": "a", "outcome": outcome, "seconds": 2.0}]})
            entries = history.load(store)
            self.assertEqual(history.failing_on_main(entries), ["a"])
            self.assertEqual(history.flaky_rates(entries), {"a": 0.333})
            self.assertEqual(history.durations(entries), {"a": 2.0})

    def test_miss_is_a_failing_test_the_culprit_run_did_not_select(self) -> None:
        entries = [{"head": "c1", "selected": {"a": "floor"}}]
        self.assertEqual(history.misses(entries, ["a", "b"], "c1"), ["b"])


class TestCulpritSearch(unittest.TestCase):
    def test_finds_the_breaking_commit_with_a_fake_probe(self) -> None:
        root, tmp = testing.make_repo()
        self.addCleanup(tmp.cleanup)
        base = testing.git(root, "rev-parse", "HEAD")
        shas = []
        for i in range(4):
            testing.write(root, "backend/python/app/services/retrieval.py", f"def search():\n    return [{i}]\n")
            shas.append(testing.commit(root, f"c{i}"))
        broken_from = shas[2]
        order = testing.git(root, "rev-list", "--reverse", f"{base}..HEAD").split()

        def probe(_root, args):
            sha, ids = args[0], args[1:]
            bad = order.index(sha) >= order.index(broken_from)
            return {i: ("fail" if bad else "pass") for i in ids}

        cfg = config.load(root)
        result = culprit.search(cfg, base, "HEAD", ["it:retrieval/test_search.py::test_acl_filter"], probe)
        self.assertEqual(result["result"], "exact")
        self.assertEqual(result["culprit"], broken_from)

    def test_flaky_when_head_passes_on_recheck(self) -> None:
        root, tmp = testing.make_repo()
        self.addCleanup(tmp.cleanup)
        base = testing.git(root, "rev-parse", "HEAD")
        testing.commit(root, "empty")
        result = culprit.search(config.load(root), base, "HEAD", ["x"], lambda _r, a: {i: "pass" for i in a[1:]})
        self.assertEqual(result["result"], "flaky")


class TestGoCommit(unittest.TestCase):
    """go --commit runs the pipeline in a throwaway worktree at that commit."""

    def setUp(self) -> None:
        self.root, tmp = testing.make_repo()
        self.addCleanup(tmp.cleanup)
        self.first = testing.git(self.root, "rev-parse", "HEAD")
        testing.write(self.root, "backend/python/app/services/retrieval.py", "def search():\n    return [1]\n")
        testing.commit(self.root, "second")
        self.seen: dict = {}

    def fake_run(self, cfg, plan, rows, out, **kw):
        self.seen.update(root=cfg.root, head=plan["head"], out=out, **kw)
        self.seen["checked_out"] = testing.git(cfg.root, "rev-parse", "HEAD")
        return {"tests": [], "seconds": 0}

    def go(self, *extra: str, side_effect=None) -> tuple[int, str]:
        err = io.StringIO()
        with mock.patch.dict(os.environ, {"QA_ROOT": str(self.root)}), \
             mock.patch.object(run, "run", side_effect=side_effect or self.fake_run), \
             contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
            code = qa_main.main(["--out", "qa-out", "go", *extra, "--adapter", "none", "--scope", "unit"])
            self.assertEqual(os.environ["QA_ROOT"], str(self.root))
        return code, err.getvalue()

    def assert_cleaned_up(self) -> None:
        self.assertFalse(Path(self.seen["root"]).exists())
        self.assertEqual(len(testing.git(self.root, "worktree", "list").splitlines()), 1)

    def test_go_commit_runs_in_a_worktree_at_the_sha(self) -> None:
        code, _ = self.go("--commit", self.first)
        self.assertEqual(code, 0)
        self.assertNotEqual(Path(self.seen["root"]).resolve(), self.root.resolve())
        self.assertEqual(self.seen["checked_out"], self.first)
        self.assertEqual(self.seen["head"], self.first)
        self.assertTrue((self.root / "qa-out" / "results-unit.json").is_file())
        self.assert_cleaned_up()

    def test_go_commit_cleans_up_when_the_run_raises(self) -> None:
        def boom(cfg, plan, rows, out, **kw):
            self.fake_run(cfg, plan, rows, out, **kw)
            (out / "run.log").write_text("partial", encoding="utf-8")
            raise RuntimeError("stack exploded")

        with self.assertRaises(RuntimeError):
            self.go("--commit", self.first, side_effect=boom)
        self.assert_cleaned_up()
        self.assertEqual(Path(self.seen["out"]), self.root / "qa-out")
        self.assertTrue((self.root / "qa-out" / "run.log").is_file())
        self.assertTrue((self.root / "qa-out" / "plan.json").is_file())

    def test_go_commit_rejects_an_unknown_sha(self) -> None:
        code, err = self.go("--commit", "deadbeef" * 5)
        self.assertEqual(code, 2)
        self.assertIn("unknown commit", err)
        self.assertEqual(len(testing.git(self.root, "worktree", "list").splitlines()), 1)

    def test_go_commit_on_main_tests_its_own_change(self) -> None:
        second = testing.git(self.root, "rev-parse", "HEAD")
        code, _ = self.go("--commit", second)
        self.assertEqual(code, 0)
        changeset = json.loads((self.root / "qa-out" / "changeset.json").read_text(encoding="utf-8"))
        self.assertEqual((changeset["base"], changeset["head"]), (self.first, second))
        self.assertEqual([f["path"] for f in changeset["files"]], ["backend/python/app/services/retrieval.py"])

    def test_go_commit_on_a_root_commit_runs_the_full_profile(self) -> None:
        code, err = self.go("--commit", self.first)
        self.assertEqual(code, 0)
        self.assertIn("root commit", err)
        plan = json.loads((self.root / "qa-out" / "plan.json").read_text(encoding="utf-8"))
        self.assertEqual(plan["profile"], "full")

    def test_go_commit_and_head_are_exclusive(self) -> None:
        err = io.StringIO()
        with self.assertRaises(SystemExit) as ctx, contextlib.redirect_stderr(err):
            qa_main.main(["go", "--commit", self.first, "--head", self.first])
        self.assertEqual(ctx.exception.code, 2)
        self.assertIn("--commit and --head cannot be used together", err.getvalue())

    def test_go_commit_defaults_to_a_managed_build(self) -> None:
        self.go("--commit", self.first)
        self.assertEqual((self.seen["stack_mode"], self.seen["build"]), ("managed", True))
        self.go("--commit", self.first, "--stack", "none")
        self.assertEqual((self.seen["stack_mode"], self.seen["build"]), ("none", True))


class TestWorktree(unittest.TestCase):
    def test_worktree_is_removed_after_use(self) -> None:
        root, tmp = testing.make_repo()
        self.addCleanup(tmp.cleanup)
        sha = testing.git(root, "rev-parse", "HEAD")
        with qa_main.collect.worktree(root, sha) as tree:
            self.assertEqual(testing.git(tree, "rev-parse", "HEAD"), sha)
        self.assertFalse(tree.exists())
        self.assertEqual(len(testing.git(root, "worktree", "list").splitlines()), 1)


if __name__ == "__main__":
    unittest.main()
