"""Run history on the qa-history branch: the branch store.

Run: cd scripts && python3 -m unittest qa.test_reliability
"""

import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from qa import branchstore, history, quarantine
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
            ({"GITHUB_EVENT_NAME": "pull_request_target", "GITHUB_REF": "refs/heads/main"}, False),
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

    def test_default_branch_comes_from_the_event_payload(self) -> None:
        event = self.tmp / "event.json"
        event.write_text(json.dumps({"repository": {"default_branch": "trunk"}}), encoding="utf-8")
        env = {"GITHUB_EVENT_NAME": "push", "GITHUB_EVENT_PATH": str(event), "QA_DEFAULT_BRANCH": "dev"}
        self.assertTrue(branchstore.may_write({**env, "GITHUB_REF": "refs/heads/trunk"})[0])
        self.assertFalse(branchstore.may_write({**env, "GITHUB_REF": "refs/heads/dev"})[0])
        self.assertFalse(branchstore.may_write({**env, "GITHUB_REF": "refs/heads/main"})[0])
        # An unreadable payload falls back to QA_DEFAULT_BRANCH, then main.
        env["GITHUB_EVENT_PATH"] = str(self.tmp / "missing.json")
        self.assertTrue(branchstore.may_write({**env, "GITHUB_REF": "refs/heads/dev"})[0])
        del env["QA_DEFAULT_BRANCH"]
        self.assertTrue(branchstore.may_write({**env, "GITHUB_REF": "refs/heads/main"})[0])

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

    def test_write_dir_keeps_normal_permissions(self) -> None:
        import os
        umask = os.umask(0o022)
        try:
            history.write_dir(self.tmp / "h", {"runs.jsonl": "x\n"})
        finally:
            os.umask(umask)
        self.assertEqual((self.tmp / "h" / "runs.jsonl").stat().st_mode & 0o777, 0o644)
        self.assertEqual(sorted(p.name for p in (self.tmp / "h").iterdir()), ["runs.jsonl"])

    def test_fetch_file_missing_is_none(self) -> None:
        with mock.patch.object(branchstore, "_gh", side_effect=RuntimeError("gh: Not Found (HTTP 404)")) as gh:
            self.assertIsNone(branchstore.fetch_file("o/r", "quarantine.json"))
        self.assertEqual(gh.call_args.args[0][-1], "repos/o/r/contents/quarantine.json?ref=qa-history")
        with mock.patch.object(branchstore, "_gh", return_value='{"tests": []}'):
            self.assertEqual(branchstore.fetch_file("o/r", "quarantine.json"), '{"tests": []}')
        with mock.patch.object(branchstore, "_gh", side_effect=RuntimeError("HTTP 500")):
            with self.assertRaises(RuntimeError):
                branchstore.fetch_file("o/r", "quarantine.json")


TODAY = dt.date(2026, 10, 31)


def _entry(test_id: str, expires: dt.date, **extra) -> dict:
    return {"id": test_id, "owner": "qa-auto", "issue": "u", "expires": expires.isoformat(), "auto": True, **extra}


class TestAutoQuarantine(unittest.TestCase):
    def setUp(self) -> None:
        patcher = mock.patch.object(quarantine, "_labelled", False)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_three_flakes_in_ten_quarantines_with_expiry_and_issue(self) -> None:
        got = quarantine.update([], {"t": "pkpkpkpppp"}, {"t": "u"}, set(), TODAY)
        self.assertEqual(len(got), 1)
        e = got[0]
        self.assertEqual(e["id"], "t")
        self.assertEqual(e["issue"], "u")
        self.assertEqual(e["expires"], (TODAY + dt.timedelta(days=14)).isoformat())
        self.assertEqual(e["owner"], "qa-auto")
        self.assertIs(e["auto"], True)
        self.assertEqual(e["added"], TODAY.isoformat())
        self.assertEqual(e["reason"], "flaky in 3 of the last 10 runs: pkpkpkpppp")
        # The oldest flake has left the window: only 2 in the last 10.
        self.assertEqual(quarantine.update([], {"t": "pkpkppppppp"}, {"t": "u"}, set(), TODAY), [])

    def test_short_window_counts(self) -> None:
        self.assertEqual(quarantine.candidates({"t": "kkpk", "s": "kkp"}, [], set(), TODAY), ["t"])

    def test_issue_failure_defers_and_retry_never_duplicates(self) -> None:
        calls = []

        def failing(args):
            calls.append(args)
            if args[:2] == ["issue", "create"]:
                raise RuntimeError("gh issue: HTTP 502")
            return "[]" if args[:2] == ["issue", "list"] else ""
        with mock.patch.object(quarantine, "_gh", side_effect=failing):
            self.assertIsNone(quarantine.ensure_issue("o/r", "t", "kkk"))
            self.assertIsNone(quarantine.ensure_issue("o/r", "t", "kkk"))
        self.assertEqual(sum(c[:2] == ["label", "create"] for c in calls), 1)
        self.assertIn("--force", next(c for c in calls if c[:2] == ["label", "create"]))
        self.assertEqual(quarantine.update([], {"t": "kkk"}, {}, set(), TODAY), [])

        calls.clear()
        listed = [{"title": "Flaky test: t2", "url": "other"}, {"title": "Flaky test: t", "url": "https://x/1"}]

        def found(args):
            calls.append(args)
            return json.dumps(listed) if args[:2] == ["issue", "list"] else "https://x/new\n"
        with mock.patch.object(quarantine, "_gh", side_effect=found):
            self.assertEqual(quarantine.ensure_issue("o/r", "t", "kkk"), "https://x/1")
            self.assertEqual(quarantine.ensure_issue("o/r", "t3", "kkk"), "https://x/new")
        creates = [c for c in calls if c[:2] == ["issue", "create"]]
        self.assertEqual(len(creates), 1)
        self.assertIn("Flaky test: t3", creates[0])
        self.assertIn(quarantine.LABEL, creates[0])
        listing = next(c for c in calls if c[:2] == ["issue", "list"])
        self.assertIn(quarantine.LABEL, listing)
        self.assertIn("open", listing)

    def test_expired_entry_is_not_renewed_within_cooldown(self) -> None:
        current = [_entry("t", TODAY - dt.timedelta(days=1))]
        windows = {"t": "kkkkk"}
        self.assertEqual(quarantine.candidates(windows, current, set(), TODAY), [])
        kept = quarantine.update(current, windows, {"t": "u"}, set(), TODAY)
        self.assertEqual(len(kept), 1)
        self.assertIs(kept[0]["expired"], True)
        last_day = TODAY - dt.timedelta(days=1) + dt.timedelta(days=30)
        self.assertEqual(quarantine.candidates(windows, kept, set(), last_day), [])
        self.assertEqual(len(quarantine.update(kept, windows, {"t": "u"}, set(), last_day)), 1)
        later = TODAY - dt.timedelta(days=1) + dt.timedelta(days=31)
        self.assertEqual(quarantine.update(kept, windows, {}, set(), later), [])
        self.assertEqual(quarantine.candidates(windows, kept, set(), later), ["t"])
        again = quarantine.update(kept, windows, {"t": "u2"}, set(), later)
        self.assertEqual([(e["id"], e["issue"], e.get("expired")) for e in again], [("t", "u2", None)])

    def test_active_entry_is_not_a_candidate(self) -> None:
        current = [_entry("t", TODAY)]
        self.assertEqual(quarantine.candidates({"t": "kkk"}, current, set(), TODAY), [])
        self.assertEqual(quarantine.update(current, {"t": "kkk"}, {"t": "u2"}, set(), TODAY), current)

    def test_ten_passes_release_early(self) -> None:
        current = [_entry("t", TODAY + dt.timedelta(days=5)), _entry("s", TODAY + dt.timedelta(days=5))]
        got = quarantine.update(current, {"t": "pppppppppp", "s": "ppppppppp"}, {}, set(), TODAY)
        self.assertEqual([e["id"] for e in got], ["s"])

    def test_static_entries_are_left_alone(self) -> None:
        self.assertEqual(quarantine.candidates({"t": "kkkk", "s": "kkk"}, [], {"t"}, TODAY), ["s"])
        self.assertEqual([e["id"] for e in quarantine.update([], {"t": "kkkk"}, {"t": "u"}, {"t"}, TODAY)], [])


if __name__ == "__main__":
    unittest.main()
