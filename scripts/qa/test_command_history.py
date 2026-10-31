"""/qa command parsing, history and culprit search.

Run: python3 -m unittest discover -s scripts -p 'test_*.py'
"""

import contextlib
import datetime as dt
import io
import json
import os
import signal
import subprocess
import sys
import tempfile
import textwrap
import time
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

    def test_commit_pins_the_head(self) -> None:
        out = command.parse(f"/qa commit {FULL}", "write")
        self.assertEqual((out["command"], out["mode"], out["head"]), ("commit", "pr", FULL))

    def test_commit_needs_the_full_sha(self) -> None:
        with self.assertRaisesRegex(command.CommandError, "40-character"):
            command.parse("/qa commit abc1234", "write")
        for body in ("/qa commit", f"/qa commit {FULL.upper()}"):
            with self.assertRaises(command.CommandError, msg=body):
                command.parse(body, "write")

    def test_commit_needs_write(self) -> None:
        with self.assertRaises(command.CommandError):
            command.parse(f"/qa commit {FULL}", "read")

    def test_fork_commit_must_be_the_trusted_head(self) -> None:
        # The workflow rejects a fork range head that differs from trusted_head.
        out = command.parse(f"/qa commit {FULL} --trusted {'a' * 40}", "admin")
        self.assertEqual((out["head"], out["trusted_head"]), (FULL, "a" * 40))

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

    def test_review_command_parses_and_needs_write(self) -> None:
        out = command.parse("/qa review", "write")
        self.assertEqual((out["command"], out["review"], out["mode"]), ("review", True, "pr"))
        for body, perm in (("/qa review", "read"), ("/qa review now", "write")):
            with self.assertRaises(command.CommandError, msg=body):
                command.parse(body, perm)
        self.assertIs(command.parse("/qa run", "write")["review"], False)

    def test_profile_flag(self) -> None:
        self.assertEqual(command.parse("/qa run --profile conservative", "write")["profile"], "conservative")


class TestHistory(unittest.TestCase):
    PLAN = {"head": "x", "branch": "main", "profile": "standard", "tests": [{"id": "a", "source": "floor"}], "planner": {}}

    def add(self, files, outcome, today=None, plan=None):
        results = {"seconds": 5, "tests": [{"id": "a", "outcome": outcome, "seconds": 2.0}]}
        e = history.entry(plan or self.PLAN, results, trigger="nightly")
        return history.add_run(files, e, results, today or dt.date(2026, 10, 1))

    def test_compact_entries_feed_failing_and_flaky(self) -> None:
        files: dict = {}
        for outcome in ("fail", "flaky", "pass"):
            files = self.add(files, outcome)
        self.assertEqual(history.failing_on_main(history.runs(files)), ["a"])
        self.assertEqual(history.windows(files)["a"], "fkp")
        self.assertEqual(history.flaky_rates(history.windows(files)), {"a": 0.333})
        last = history.runs(files)[-1]
        self.assertEqual((last["outcomes"], last["counts"]), ({}, {"pass": 1}))
        with tempfile.TemporaryDirectory() as tmp:
            history.write_dir(Path(tmp) / "h", files)
            self.assertEqual(history.read_dir(Path(tmp) / "h"), files)

    def test_full_profile_entry_stores_selected_as_all(self) -> None:
        e = history.entry({**self.PLAN, "profile": "full"}, {"seconds": 1, "tests": []}, trigger="local")
        self.assertEqual(e["selected"], "all")
        self.assertEqual(history.misses([e], ["a"], "x"), [])

    def test_history_is_bounded(self) -> None:
        today = dt.date(2026, 10, 31)
        now = int(dt.datetime(2026, 10, 31, tzinfo=dt.timezone.utc).timestamp())
        files = {"runs.jsonl": "".join(json.dumps({"ts": now - i}) + "\n" for i in range(1200))}
        self.assertEqual(len(history.trim(files, today)["runs.jsonl"].splitlines()), 1000)
        old = {"runs.jsonl": json.dumps({"ts": now - 91 * 86400}) + "\n" + json.dumps({"ts": now}) + "\n",
               "tests.json": json.dumps({"gone": {"o": "p", "seen": "2026-08-01"}, "kept": {"o": "p", "seen": "2026-10-30"}})}
        trimmed = history.trim(old, today)
        self.assertEqual([r["ts"] for r in history.runs(trimmed)], [now])
        self.assertEqual(set(history.windows(trimmed)), {"kept"})
        files = {}
        for _ in range(12):
            files = self.add(files, "pass", today)
        self.assertEqual(history.windows(files)["a"], "p" * 10)

    def test_trigger_names(self) -> None:
        for event, name in (("schedule", "nightly"), ("push", "main"), ("workflow_dispatch", "dispatch"), ("pull_request", "pr")):
            self.assertEqual(history.trigger({"GITHUB_EVENT_NAME": event}), name)
        self.assertEqual(history.trigger({}), "local")

    def test_stuck_needs_three_distinct_heads(self) -> None:
        def r(o):
            return {"tests": [{"id": "a", "outcome": o}]}
        self.assertEqual(history.stuck([("h1", r("fail")), ("h2", r("error")), ("h3", r("fail"))]), ["a"])
        self.assertEqual(history.stuck([("h1", r("fail")), ("h2", r("fail")), ("h2", r("fail"))]), [])
        self.assertEqual(history.stuck([("h1", r("fail")), ("h2", r("pass")), ("h3", r("fail"))]), [])

    def test_record_and_stuck_commands(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            dirs = []
            for head in ("h1", "h2", "h3"):
                d = Path(tmp) / head
                d.mkdir()
                (d / "plan.json").write_text(json.dumps({**self.PLAN, "head": head}), encoding="utf-8")
                (d / "results.json").write_text(json.dumps({"seconds": 1, "tests": [{"id": "a", "outcome": "fail"}]}), encoding="utf-8")
                dirs.append(str(d))
            store = Path(tmp) / "store"
            with contextlib.redirect_stdout(io.StringIO()) as out, mock.patch.dict(os.environ, {"GITHUB_EVENT_NAME": "schedule"}):
                self.assertEqual(qa_main.main(["--out", dirs[0], "history", "record", "--store", str(store)]), 0)
                self.assertEqual(qa_main.main(["stuck", "--runs", *dirs]), 3)
                self.assertEqual(qa_main.main(["stuck", "--runs", *dirs[:2]]), 0)
            self.assertEqual(out.getvalue(), "a\n")
            self.assertEqual(history.runs(history.read_dir(store))[0]["trigger"], "nightly")

    def test_stuck_skips_runs_without_a_head(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            dirs = []
            for i in range(3):
                d = Path(tmp) / str(i)
                d.mkdir()
                plan = {k: v for k, v in self.PLAN.items() if k != "head"}
                (d / "plan.json").write_text(json.dumps(plan), encoding="utf-8")
                (d / "results.json").write_text(json.dumps({"tests": [{"id": "a", "outcome": "fail"}]}), encoding="utf-8")
                dirs.append(str(d))
            with mock.patch.object(history, "stuck", wraps=history.stuck) as stuck:
                self.assertEqual(qa_main.main(["stuck", "--runs", *dirs]), 0)
            self.assertEqual(stuck.call_args.args[0], [])

    def test_record_into_a_file_exits_2(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp) / "run"
            d.mkdir()
            (d / "plan.json").write_text(json.dumps(self.PLAN), encoding="utf-8")
            (d / "results.json").write_text(json.dumps({"seconds": 1, "tests": []}), encoding="utf-8")
            store = Path(tmp) / "history.jsonl"
            store.write_text("", encoding="utf-8")
            with contextlib.redirect_stderr(io.StringIO()) as err, self.assertRaises(SystemExit) as cm:
                qa_main.main(["--out", str(d), "history", "record", "--store", str(store)])
            self.assertEqual(cm.exception.code, 2)
            self.assertIn("is a file", err.getvalue())

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

    def test_go_commit_without_a_base_branch_exits_with_a_message(self) -> None:
        testing.git(self.root, "branch", "-m", "main", "trunk")
        with self.assertRaises(SystemExit) as ctx:
            self.go("--commit", self.first)
        self.assertIn("cannot find a merge-base", str(ctx.exception.code))
        self.assertEqual(len(testing.git(self.root, "worktree", "list").splitlines()), 1)

    def test_go_commit_cleans_up_on_sigterm(self) -> None:
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        ready = Path(scratch.name) / "started"
        script = textwrap.dedent(f"""
            import sys, time
            from pathlib import Path
            from unittest import mock
            from qa import __main__ as qa_main, run

            def slow(cfg, *a, **kw):
                Path({str(ready)!r}).write_text(str(cfg.root))
                time.sleep(60)

            with mock.patch.object(run, "run", side_effect=slow):
                sys.exit(qa_main.main(["--out", "qa-out", "go", "--commit", {self.first!r}, "--adapter", "none", "--scope", "unit"]))
        """)
        env = {**os.environ, "QA_ROOT": str(self.root), "PYTHONPATH": str(Path(qa_main.__file__).resolve().parents[1])}
        proc = subprocess.Popen([sys.executable, "-c", script], env=env, cwd=self.root,
                                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        self.addCleanup(lambda: proc.poll() is None and proc.kill())
        deadline = time.time() + 30
        while not ready.exists() and proc.poll() is None and time.time() < deadline:
            time.sleep(0.05)
        self.assertTrue(ready.exists(), proc.stderr.read().decode() if proc.poll() is not None else "run never started")
        tree = Path(ready.read_text())
        proc.send_signal(signal.SIGTERM)
        rc = proc.wait(timeout=30)
        self.assertEqual(rc, 143, proc.stderr.read().decode())
        proc.stderr.close()
        self.assertFalse(tree.exists())
        self.assertEqual(len(testing.git(self.root, "worktree", "list").splitlines()), 1)

    def test_go_commit_defaults_to_a_managed_build(self) -> None:
        self.go("--commit", self.first)
        self.assertEqual((self.seen["stack_mode"], self.seen["build"]), ("managed", True))
        self.go("--commit", self.first, "--stack", "none")
        self.assertEqual((self.seen["stack_mode"], self.seen["build"]), ("none", True))


class TestWorktree(unittest.TestCase):
    def test_a_second_sigterm_does_not_stop_the_cleanup(self) -> None:
        root, tmp = testing.make_repo()
        self.addCleanup(tmp.cleanup)
        sha = testing.git(root, "rev-parse", "HEAD")
        real = subprocess.run

        def run_(cmd, *a, **kw):
            if cmd[:3] == ["git", "worktree", "remove"]:
                os.kill(os.getpid(), signal.SIGTERM)
            return real(cmd, *a, **kw)

        def handler(*_):
            raise RuntimeError("second SIGTERM")
        previous = signal.signal(signal.SIGTERM, handler)
        self.addCleanup(signal.signal, signal.SIGTERM, previous)
        with mock.patch.object(qa_main.collect.subprocess, "run", run_), qa_main.collect.worktree(root, sha) as tree:
            pass
        self.assertFalse(tree.exists())
        self.assertEqual(len(testing.git(root, "worktree", "list").splitlines()), 1)
        self.assertIs(signal.getsignal(signal.SIGTERM), handler)

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
