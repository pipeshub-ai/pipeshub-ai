"""Run history on the qa-history branch: the branch store.

Run: cd scripts && python3 -m unittest qa.test_reliability
"""

import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from qa import branchstore, history
from qa.testing import git


def _repo(path: Path) -> Path:
    path.mkdir(parents=True)
    git(path, "init", "-q", "-b", "main")
    (path / "README").write_text("x\n", encoding="utf-8")
    git(path, "add", "README")
    git(path, "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-q", "-m", "base")
    return path


def _append(line: str):
    def mutate(files):
        return {**files, "runs.jsonl": files.get("runs.jsonl", "") + line + "\n"}
    return mutate


class TestBranchStore(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory(prefix="qa-branchstore-")
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.calls: list[list[str]] = []
        real = branchstore._git

        def recording(root, args, **kw):
            self.calls.append(list(args))
            return real(root, args, **kw)
        patches = [mock.patch.object(branchstore, "_git", recording), mock.patch.object(branchstore.time, "sleep")]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def _remote(self) -> tuple[Path, Path, Path]:
        work = _repo(self.tmp / "work")
        bare = self.tmp / "remote.git"
        git(self.tmp, "init", "-q", "--bare", str(bare))
        git(work, "push", "-q", str(bare), "main")
        a, b = self.tmp / "a", self.tmp / "b"
        for d in (a, b):
            git(self.tmp, "clone", "-q", str(bare), str(d))
        return bare, a, b

    def test_first_publish_creates_an_orphan_branch(self) -> None:
        root = _repo(self.tmp / "r")
        head = git(root, "rev-parse", "HEAD")
        status = git(root, "status", "--porcelain")
        sha = branchstore.update(root, _append('{"ts": 1}'), remote=None, message="qa: record run")
        self.assertEqual(git(root, "rev-parse", "refs/heads/qa-history"), sha)
        self.assertEqual(git(root, "rev-list", "--parents", "-n1", sha), sha)
        self.assertEqual(git(root, "show", "qa-history:runs.jsonl"), '{"ts": 1}')
        self.assertEqual(git(root, "log", "-1", "--format=%an <%ae>|%cn", sha),
                         "qa-history <qa-history@users.noreply.github.com>|qa-history")
        self.assertEqual((git(root, "rev-parse", "HEAD"), git(root, "status", "--porcelain")), (head, status))
        self.assertEqual(branchstore.read(root, None), (sha, {"runs.jsonl": '{"ts": 1}\n'}))

    def test_concurrent_publish_keeps_both_entries(self) -> None:
        bare, a, b = self._remote()
        landed = []

        def mutate_a(files):
            if not landed:
                landed.append(branchstore.update(b, _append("B"), remote="origin", message="qa: B"))
            return _append("A")(files)
        branchstore.update(a, mutate_a, remote="origin", message="qa: A")
        self.assertEqual(git(bare, "show", "qa-history:runs.jsonl").splitlines(), ["B", "A"])
        self.assertEqual(git(bare, "rev-list", "--count", "qa-history"), "2")
        pushes = [c for c in self.calls if c[0] == "push"]
        self.assertEqual(len(pushes), 3)  # B, A rejected, A again
        self.assertFalse([x for c in pushes for x in c if x in ("--force", "-f") or x.startswith(("+", "--force"))])

    def test_local_cas_retries_on_a_moved_ref(self) -> None:
        root = _repo(self.tmp / "r")
        moved = []

        def mutate(files):
            if not moved:
                moved.append(branchstore.update(root, _append("B"), remote=None, message="qa: B"))
            return _append("A")(files)
        sha = branchstore.update(root, mutate, remote=None, message="qa: A")
        self.assertEqual(git(root, "show", "qa-history:runs.jsonl").splitlines(), ["B", "A"])
        self.assertEqual(git(root, "rev-parse", f"{sha}^"), moved[0])
        self.assertEqual(branchstore.time.sleep.call_count, 1)

    def test_gives_up_after_the_attempts(self) -> None:
        root = _repo(self.tmp / "r")
        tries = []

        def mutate(files):
            tries.append(1)
            branchstore.update(root, _append("other"), remote=None, message="qa: other")
            return _append("A")(files)
        with self.assertRaises(branchstore.Conflict):
            branchstore.update(root, mutate, remote=None, message="qa: A")
        self.assertEqual(len(tries), 5)
        self.assertNotIn("A", git(root, "show", "qa-history:runs.jsonl").splitlines())

    def test_publish_refused_outside_schedule_or_main_push(self) -> None:
        cases = [
            ({"GITHUB_EVENT_NAME": "schedule"}, True),
            ({"GITHUB_EVENT_NAME": "push", "GITHUB_REF": "refs/heads/main"}, True),
            ({"GITHUB_EVENT_NAME": "push", "GITHUB_REF": "refs/heads/feat"}, False),
            ({"GITHUB_EVENT_NAME": "pull_request", "GITHUB_REF": "refs/pull/1/merge"}, False),
            ({"GITHUB_EVENT_NAME": "workflow_dispatch", "GITHUB_REF": "refs/heads/main"}, False),
            ({"GITHUB_EVENT_NAME": "issue_comment", "GITHUB_REF": "refs/heads/main"}, False),
            ({}, True),
        ]
        for env, allowed in cases:
            ok, reason = branchstore.may_write(env)
            self.assertEqual(ok, allowed, env)
            if env and not allowed:
                self.assertIn(env["GITHUB_EVENT_NAME"], reason)
                self.assertIn(env["GITHUB_REF"], reason)
        self.assertEqual(branchstore.may_write({"GITHUB_EVENT_NAME": "push", "GITHUB_REF": "refs/heads/dev",
                                                "QA_DEFAULT_BRANCH": "dev"})[0], True)

    def test_damaged_files_do_not_stop_a_publish(self) -> None:
        root = _repo(self.tmp / "r")
        branchstore.update(root, lambda f: {"runs.jsonl": '{"ts": 5}\n{"ts": 6, "he'}, remote=None, message="seed")
        results = {"seconds": 1, "tests": [{"id": "a", "outcome": "flaky"}]}
        today = dt.date.today()
        e = history.entry({"head": "h", "tests": []}, results, trigger="local")
        branchstore.update(root, lambda f: history.add_run(f, e, results, today), remote=None, message="qa: record run")
        _, files = branchstore.read(root, None)
        self.assertEqual([r["head"] for r in history.runs(files)], ["h"])
        self.assertEqual(len(files["runs.jsonl"].splitlines()), 1)
        self.assertEqual(history.windows(files), {"a": "k"})

    def test_fetch_file_missing_is_none(self) -> None:
        with mock.patch.object(branchstore, "_gh", side_effect=RuntimeError("gh: Not Found (HTTP 404)")) as gh:
            self.assertIsNone(branchstore.fetch_file("o/r", "quarantine.json"))
        self.assertEqual(gh.call_args.args[0][-1], "repos/o/r/contents/quarantine.json?ref=qa-history")
        with mock.patch.object(branchstore, "_gh", return_value='{"tests": []}'):
            self.assertEqual(branchstore.fetch_file("o/r", "quarantine.json"), '{"tests": []}')
        with mock.patch.object(branchstore, "_gh", side_effect=RuntimeError("HTTP 500")):
            with self.assertRaises(RuntimeError):
                branchstore.fetch_file("o/r", "quarantine.json")


if __name__ == "__main__":
    unittest.main()
