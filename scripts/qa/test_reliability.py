"""Run history on the qa-history branch: the branch store.

Run: cd scripts && python3 -m unittest qa.test_reliability
"""

import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from qa import (
    branchstore,
    catalog,
    config,
    history,
    metrics,
    quarantine,
    schema,
    shadow,
    testing,
)
from qa.testing import (
    TODAY,
    fixture_metrics,
    git,
    history_files,
    history_run,
    shadow_records,
    ts,
)


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

    def test_garbled_event_payload_falls_back(self) -> None:
        event = self.tmp / "event.json"
        for text in ("not json", '{"repository": null}'):
            event.write_text(text, encoding="utf-8")
            env = {"GITHUB_EVENT_PATH": str(event)}
            self.assertEqual(branchstore._default_branch({**env, "QA_DEFAULT_BRANCH": "dev"}), "dev", text)
            self.assertEqual(branchstore._default_branch(env), "main", text)

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

    def test_entry_without_expiry_counts_as_expired(self) -> None:
        current = [{"id": "t", "owner": "qa-auto", "issue": "u", "auto": True}]
        self.assertEqual(quarantine.candidates({"t": "kkk"}, current, set(), TODAY), ["t"])
        got = quarantine.update(current, {"t": "kkk"}, {"t": "u2"}, set(), TODAY)
        self.assertEqual([(e["id"], e["issue"]) for e in got], [("t", "u2")])

    def test_label_failure_is_retried(self) -> None:
        calls = []

        def gh(args):
            calls.append(args)
            if args[:2] == ["label", "create"] and len(calls) == 1:
                raise RuntimeError("gh label: HTTP 502")
            return "[]" if args[:2] == ["issue", "list"] else "https://x/1\n"
        with mock.patch.object(quarantine, "_gh", side_effect=gh):
            self.assertIsNone(quarantine.ensure_issue("o/r", "t", "kkk"))
            self.assertEqual(quarantine.ensure_issue("o/r", "t", "kkk"), "https://x/1")
            quarantine.ensure_issue("o/r", "t2", "kkk")
        self.assertEqual(sum(c[:2] == ["label", "create"] for c in calls), 2)

    def test_static_entries_are_left_alone(self) -> None:
        self.assertEqual(quarantine.candidates({"t": "kkkk", "s": "kkk"}, [], {"t"}, TODAY), ["s"])
        self.assertEqual([e["id"] for e in quarantine.update([], {"t": "kkkk"}, {"t": "u"}, {"t"}, TODAY)], [])



def _xml(path: Path, cases: list[tuple[str, str, str]]) -> Path:
    """cases: (classname, name, outcome) with outcome pass | fail | error."""
    body = []
    for cls, name, outcome in cases:
        inner = {"pass": "", "fail": '<failure message="boom">x</failure>', "error": '<error message="collection failure">E</error>'}[outcome]
        body.append(f'<testcase classname="{cls}" name="{name}" time="0.1">{inner}</testcase>')
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f'<testsuites><testsuite name="pytest">{"".join(body)}</testsuite></testsuites>', encoding="utf-8")
    return path


JIRA_SYNC = "it:connectors/jira/test_jira.py::TestJira::test_tc_sync_001_full_sync"
RETRIEVAL = "py:tests/unit/services/test_retrieval.py"


class TestShadow(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory(prefix="qa-shadow-")
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)

    def test_parametrized_and_collection_errors_map(self) -> None:
        rows = [{"id": "it:connectors/jira/test_jira.py::TestJira::test_x"}, {"id": "py:tests/unit/foo_test.py"}]
        it = _xml(self.tmp / "it.xml", [("connectors.jira.test_jira.TestJira", "test_x[a]", "fail"),
                                         ("connectors.jira.test_jira.TestJira", "test_y", "pass")])
        self.assertEqual(shadow.failures(shadow.junit.read(it), rows, "it"),
                         ({"it:connectors/jira/test_jira.py::TestJira::test_x"}, []))
        py = _xml(self.tmp / "py.xml", [("", "tests.unit.foo_test", "error")])
        self.assertEqual(shadow.failures(shadow.junit.read(py), rows, "py"), ({"py:tests/unit/foo_test.py"}, []))
        # A test with no row of its own maps to its file.
        other = _xml(self.tmp / "o.xml", [("connectors.jira.test_jira.TestJira", "test_new", "fail")])
        self.assertEqual(shadow.failures(shadow.junit.read(other), rows, "it")[0], {"it:connectors/jira/test_jira.py"})

    def test_unmapped_failure_counts_as_a_miss(self) -> None:
        path = _xml(self.tmp / "u.xml", [("nowhere.test_gone", "test_a", "fail")])
        ids, unmapped = shadow.failures(shadow.junit.read(path), [{"id": "py:tests/unit/foo_test.py"}], "py")
        self.assertEqual((ids, unmapped), (set(), ["nowhere.test_gone::test_a"]))
        rec = {"ts": ts(TODAY), "head": "h", "caught": ["py:a.py"], "missed": [], "unmapped": unmapped}
        got = shadow.enforcement([rec], TODAY)
        self.assertEqual((got["failures"], got["caught"]), (2, 1))
        self.assertIn("nowhere.test_gone::test_a", got["missed"])

    def test_file_level_selection_catches_test_failures(self) -> None:
        self.assertTrue(shadow.is_caught("it:f.py::C::t", {"it:f.py"}))
        self.assertTrue(shadow.is_caught("py:f.py", {"py:f.py::t"}))
        self.assertTrue(shadow.is_caught("it:f.py::C::t", {"it:f.py::C::other"}))
        self.assertFalse(shadow.is_caught("it:f.py::C::t", {"it:g.py", "it:f.pyx::t", "py:f.py"}))

    def test_compare_uses_the_floor_at_the_head(self) -> None:
        root, tmp = testing.make_repo()
        self.addCleanup(tmp.cleanup)
        base = git(root, "rev-parse", "HEAD")
        testing.write(root, "backend/python/app/connectors/sources/jira/sync.py", "def fetch():\n    return 2\n")
        head = testing.commit(root, "jira change")
        git(root, "checkout", "-q", base)  # the plan must come from the head, not the checkout
        xml = _xml(self.tmp / "reports-neo4j-1" / "neo4j-results.xml", [
            ("connectors.jira.test_jira.TestJira", "test_tc_sync_001_full_sync", "fail"),
            ("gone.test_x", "test_y", "error"),
        ])
        py = _xml(self.tmp / "py-junit-1" / "junit-1.xml", [("tests.unit.services.test_retrieval", "test_search", "fail")])
        cfg = config.load(root)
        self.assertIn(JIRA_SYNC, shadow.selected_by_floor(cfg, base, head, "all"))
        rec = shadow.compare(cfg, base, head, [("it", xml), ("py", py)], workflow="integration-tests.yml", run_id="9", edition="all", ts=7)
        self.assertEqual(rec, {"ts": 7, "workflow": "integration-tests.yml", "run_id": "9", "head": head, "base": base,
                               "edition": "all", "caught": [JIRA_SYNC], "missed": [RETRIEVAL], "unmapped": ["gone.test_x::test_y"]})
        self.assertEqual(git(root, "rev-parse", "HEAD"), base)
        self.assertEqual(git(root, "worktree", "list").count("\n"), 0)

    def test_switch_needs_two_weeks_twenty_failures_and_98_percent(self) -> None:
        self.assertEqual(shadow.enforcement([], TODAY)["status"], "insufficient")
        self.assertEqual(shadow.enforcement(shadow_records(13, 50, 50), TODAY)["status"], "insufficient")
        self.assertEqual(shadow.enforcement(shadow_records(15, 19, 19), TODAY)["status"], "insufficient")
        ready = shadow.enforcement(shadow_records(15, 50, 49), TODAY)
        self.assertEqual((ready["status"], ready["ready"], ready["recall"], ready["failures"], ready["caught"]),
                         ("ready", True, 0.98, 50, 49))
        self.assertEqual(ready["missed"], ["py:t49.py"])
        self.assertEqual(ready["since"], (TODAY - dt.timedelta(days=15)).isoformat())
        not_ready = shadow.enforcement(shadow_records(15, 50, 48), TODAY)
        self.assertEqual((not_ready["status"], not_ready["ready"]), ("not_ready", False))
        # The same (head, id) recorded twice counts once.
        twice = shadow_records(15, 50, 49)
        twice.append(dict(twice[1]))
        self.assertEqual(shadow.enforcement(twice, TODAY)["failures"], 50)
        # Records older than the window do not count toward the failures.
        stale = shadow_records(15, 0, 0)
        stale[0] = {**stale[0], "caught": [f"py:old{i}.py" for i in range(30)]}
        self.assertEqual(shadow.enforcement(stale, TODAY)["failures"], 0)

    def test_fetch_downloads_failed_runs_only(self) -> None:
        calls = []
        since = dt.datetime(2026, 10, 30, tzinfo=dt.timezone.utc)

        def gh(args):
            calls.append(args)
            if args[:2] == ["run", "list"]:
                wf = args[args.index("--workflow") + 1]
                return json.dumps([
                    {"databaseId": 1 if wf == "unit-test.yml" else 2, "headSha": "a" * 40, "createdAt": "2026-10-30T10:00:00Z"},
                    {"databaseId": 3, "headSha": "b" * 40, "createdAt": "2026-10-29T10:00:00Z"},  # too old
                    {"databaseId": 4 if wf == "unit-test.yml" else 5, "headSha": "c" * 40, "createdAt": "2026-10-31T10:00:00Z"},
                ])
            run_id, pattern, dest = args[2], args[args.index("-p") + 1], Path(args[args.index("-D") + 1])
            if run_id in ("1", "2"):
                _xml(dest / pattern.replace("*", "1") / "junit-1.xml", [("a", "b", "fail")])
            return ""
        with mock.patch.object(shadow, "_gh", side_effect=gh):
            got = shadow.fetch("o/r", ["unit-test.yml", "integration-tests.yml"], since, self.tmp)
        lists = [c for c in calls if c[:2] == ["run", "list"]]
        self.assertEqual(len(lists), 2)
        self.assertTrue(all(c[c.index("--status") + 1] == "failure" and c[c.index("-R") + 1] == "o/r" for c in lists))
        downloads = [(c[2], c[c.index("-p") + 1]) for c in calls if c[:2] == ["run", "download"]]
        self.assertEqual(sorted(downloads), [("1", "py-junit-*"), ("2", "reports-*"), ("4", "py-junit-*"), ("5", "reports-*")])
        self.assertEqual([(r["run_id"], r["workflow"], r["head"]) for r in got],
                         [("1", "unit-test.yml", "a" * 40), ("2", "integration-tests.yml", "a" * 40)])
        self.assertEqual(got[0]["junit"], [("py", self.tmp / "1" / "py-junit-1" / "junit-1.xml")])
        self.assertEqual(got[1]["junit"], [("it", self.tmp / "2" / "reports-1" / "junit-1.xml")])

    def test_switch_boundaries_are_inclusive(self) -> None:
        # Exactly 14 days of data and exactly 20 failures are enough.
        self.assertEqual(shadow.enforcement(shadow_records(14, 20, 20), TODAY)["status"], "ready")
        self.assertEqual(shadow.enforcement(shadow_records(14, 19, 19), TODAY)["status"], "insufficient")
        self.assertEqual(shadow.enforcement(shadow_records(13, 20, 20), TODAY)["status"], "insufficient")

    def test_verdict_names_its_population(self) -> None:
        got = shadow.enforcement(shadow_records(15, 50, 49), TODAY)
        self.assertEqual(got["families"], ["it", "py"])
        self.assertIn("not whole-suite recall", got["population"])

    def test_fetch_skips_a_workflow_whose_run_list_fails(self) -> None:
        def gh(args):
            if args[:2] == ["run", "list"]:
                if "unit-test.yml" in args:
                    raise RuntimeError("gh run: HTTP 502")
                return json.dumps([{"databaseId": 2, "headSha": "a" * 40, "createdAt": "2026-10-31T10:00:00Z"}])
            _xml(Path(args[args.index("-D") + 1]) / "reports-1" / "j.xml", [("a", "b", "fail")])
            return ""
        with mock.patch.object(shadow, "_gh", side_effect=gh), \
                mock.patch("sys.stderr", new_callable=lambda: __import__("io").StringIO()) as err:
            got = shadow.fetch("o/r", ["unit-test.yml", "integration-tests.yml"], dt.datetime(2026, 10, 30, tzinfo=dt.timezone.utc), self.tmp)
        self.assertEqual([r["run_id"] for r in got], ["2"])
        self.assertIn("unit-test.yml", err.getvalue())

    def test_fetch_command_continues_past_a_bad_run(self) -> None:
        from qa import __main__ as cli
        to = self.tmp / "h" / "shadow.jsonl"
        runs = [{"run_id": str(i), "workflow": "unit-test.yml", "head": c * 40, "junit": []} for i, c in ((1, "a"), (2, "b"))]
        with mock.patch.object(shadow, "fetch", return_value=runs), \
                mock.patch.object(cli.collect, "git", return_value="f" * 40), \
                mock.patch.object(shadow, "compare", side_effect=[ValueError("bad junit"), {"ts": 1, "head": "b"}]), \
                mock.patch("sys.stderr", new_callable=lambda: __import__("io").StringIO()) as err:
            self.assertEqual(cli.main(["shadow", "fetch", "--repo", "o/r", "--to", str(to)]), 0)
        self.assertEqual(to.read_text(encoding="utf-8"), '{"head": "b", "ts": 1}\n')
        self.assertIn("run 1", err.getvalue())
        self.assertNotIn("Traceback", err.getvalue())

    def test_append_jsonl_appends_after_a_truncated_line(self) -> None:
        from qa import __main__ as cli
        path = self.tmp / "s.jsonl"
        path.write_text('{"a": 1}\n{"b"', encoding="utf-8")
        cli._append_jsonl(path, {"c": 3})
        cli._append_jsonl(path, {"d": 4})
        self.assertEqual(path.read_text(encoding="utf-8"), '{"a": 1}\n{"b"\n{"c": 3}\n{"d": 4}\n')

    def test_verdict_exit_codes(self) -> None:
        from qa import __main__ as cli
        store = self.tmp / "h"
        for records, code in ((shadow_records(15, 50, 49), 0), (shadow_records(15, 50, 40), 1), ([], 2)):
            history.write_dir(store, {"shadow.jsonl": "".join(json.dumps(r) + "\n" for r in records)})
            with mock.patch.object(shadow, "_today", return_value=TODAY), \
                    mock.patch("sys.stdout", new_callable=lambda: __import__("io").StringIO()) as out:
                self.assertEqual(cli.main(["shadow", "verdict", "--store", str(store)]), code)
            self.assertEqual(json.loads(out.getvalue())["status"], ["ready", "not_ready", "insufficient"][code])


class TestMetrics(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory(prefix="qa-metrics-")
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)

    def test_percentile_is_nearest_rank(self) -> None:
        values = [float(v) for v in range(10, 101, 10)]
        self.assertEqual((metrics.percentile(values, 50), metrics.percentile(values, 95)), (50.0, 100.0))
        self.assertEqual(metrics.percentile([7.0], 95), 7.0)
        self.assertIsNone(metrics.percentile([], 50))

    def test_percentiles_per_trigger(self) -> None:
        runs = [history_run(TODAY - dt.timedelta(days=i % 5), "nightly", float(w)) for i, w in enumerate(range(100, 9, -10))]
        runs += [history_run(TODAY, "local", 500.0), history_run(TODAY, "local", None), history_run(TODAY - dt.timedelta(days=31), "nightly", 9999.0)]
        got = metrics.compute(history_files(runs), [], TODAY, bugs={}, coverage={})["duration"]
        self.assertEqual(got["nightly"], {"runs": 10, "p50": 50.0, "p95": 100.0})
        self.assertEqual(got["local"], {"runs": 2, "p50": 500.0, "p95": 500.0})

    def test_trend_groups_by_day_and_trigger(self) -> None:
        trend = fixture_metrics()["trend"]
        self.assertEqual(len(trend), 4)  # three nightly days and one local; the 40-day-old run is outside the window
        self.assertEqual(trend[-1], {"date": TODAY.isoformat(), "trigger": "nightly", "runs": 1, "pass": 5, "fail": 1,
                                     "error": 0, "flaky": 1, "not_run": 0})
        self.assertEqual(trend[-2]["trigger"], "local")
        self.assertEqual(trend[-2]["not_run"], 1)

    def test_flake_rate_and_quarantined_list(self) -> None:
        tests = {"py:a.py": "ppkk", "it:b.py": "pppp", "py:gone.py": "kp"}
        quarantined = [{"id": "py:a.py", "expires": "2026-11-10", "auto": True},
                       {"id": "py:old.py", "expires": "2026-10-20", "auto": True, "expired": True}]
        rows = [{"id": "py:a.py", "suite": "py-unit"}, {"id": "it:b.py", "suite": "it-stack"},
                {"id": "py:s.py", "suite": "py-unit", "status": "quarantined"}]
        files = history_files([], tests, quarantined)
        files["tests.json"] = json.dumps({**json.loads(files["tests.json"]), "py:old.py": {"o": "kkkk", "seen": "2026-09-01"}})
        got = metrics.compute(files, rows, TODAY, bugs={}, coverage={})["flake"]  # py:old.py was last seen outside the window
        self.assertEqual(got["rate"], 0.3)
        self.assertEqual(got["by_suite"], {"py-unit": 0.5, "it-stack": 0.0, "unknown": 0.5})
        self.assertEqual(got["quarantined"], ["py:a.py", "py:s.py"])

    def test_coverage_counts_tests_per_module_and_flow(self) -> None:
        root, tmp = testing.make_repo()
        self.addCleanup(tmp.cleanup)
        cfg = config.load(root)
        rows = catalog.build(cfg)
        cov = metrics.compute(history_files([]), rows, TODAY, bugs={}, coverage={"py-unit": None}, cfg=cfg)["coverage"]
        self.assertEqual(cov["flows"]["search"], {"tests": 2, "priority": "P0"})
        self.assertEqual(cov["flows"]["login"], {"tests": 1, "priority": "P1"})
        self.assertEqual(cov["flows"]["usage"], {"tests": 1, "priority": "P0"})
        self.assertEqual(cov["modules"]["retrieval"], {"tests": 3})
        self.assertEqual(cov["modules"]["graph-db"], {"tests": 1})
        self.assertEqual(cov["modules"]["connectors"], {"tests": 0})  # a module with no tests still shows
        self.assertEqual(cov["line"], {"py-unit": None})
        self.assertEqual(cov["api_operations"], {"hit_rate": None, "source": "not measured"})

    def test_flow_priority_without_the_registry_is_the_best_of_its_rows(self) -> None:
        rows = [{"id": "x", "flows": ["f"], "priority": "P2"}, {"id": "y", "flows": ["f"], "priority": "P0"},
                {"id": "z", "flows": ["f"], "priority": None}]
        self.assertEqual(metrics.compute(history_files([]), rows, TODAY, bugs={}, coverage={})["coverage"]["flows"]["f"],
                         {"tests": 3, "priority": "P0"})

    def test_line_coverage_formats(self) -> None:
        files = {
            "coverage.json": json.dumps({"totals": {"percent_covered": 87.5}}),
            "coverage.xml": '<?xml version="1.0" ?><coverage line-rate="0.875" branch-rate="0"></coverage>',
            "coverage-summary.json": json.dumps({"total": {"lines": {"total": 8, "covered": 7, "pct": 87.5}}}),
        }
        for name, text in files.items():
            (self.tmp / name).write_text(text, encoding="utf-8")
            self.assertEqual(metrics.line_coverage(self.tmp / name), 87.5, name)
        self.assertIsNone(metrics.line_coverage(self.tmp / "missing.json"))
        (self.tmp / "bad.json").write_text("{", encoding="utf-8")
        self.assertIsNone(metrics.line_coverage(self.tmp / "bad.json"))

    def test_bugs_caught_and_escaped(self) -> None:
        since = TODAY - dt.timedelta(days=30)

        def gh(args):
            if args[:2] == ["pr", "list"]:
                return json.dumps([{"number": 1, "headRefName": "fix-a", "mergedAt": "2026-10-20T00:00:00Z"},
                                   {"number": 2, "headRefName": "fix-b", "mergedAt": "2026-10-21T00:00:00Z"},
                                   {"number": 3, "headRefName": "fix-c", "mergedAt": "2026-09-01T00:00:00Z"}])
            if args[:2] == ["run", "list"]:
                return json.dumps([  # newest first, as gh lists them
                    {"headBranch": "fix-a", "conclusion": "success", "createdAt": "2026-10-19T02:00:00Z"},
                    {"headBranch": "fix-a", "conclusion": "failure", "createdAt": "2026-10-19T01:00:00Z"},
                    {"headBranch": "fix-b", "conclusion": "success", "createdAt": "2026-10-20T01:00:00Z"},
                    {"headBranch": "fix-c", "conclusion": "success", "createdAt": "2026-08-31T02:00:00Z"},
                    {"headBranch": "fix-c", "conclusion": "failure", "createdAt": "2026-08-31T01:00:00Z"},
                ])
            if args[:2] == ["issue", "list"]:
                return json.dumps([
                    {"number": 7, "createdAt": "2026-10-25T00:00:00Z", "labels": [{"name": "escaped"}, {"name": "flow:search"}, {"name": "flow:search"}]},
                    {"number": 8, "createdAt": "2026-10-26T00:00:00Z", "labels": None},
                    {"number": 9, "createdAt": "2026-08-01T00:00:00Z", "labels": [{"name": "escaped"}, {"name": "flow:login"}]},
                ])
            raise AssertionError(args)
        with mock.patch.object(shadow, "_gh", side_effect=gh) as fake, \
                mock.patch.object(metrics, "PR_WORKFLOWS", ("qa-pr.yml",)):
            got = metrics.bugs("o/r", since)
        self.assertEqual(got, {"caught": 1, "escaped": 2, "escaped_by_flow": {"search": 1}, "unlinked": 1})
        issue_list = next(c.args[0] for c in fake.call_args_list if c.args[0][:2] == ["issue", "list"])
        self.assertEqual(issue_list[issue_list.index("--label") + 1], "escaped")
        self.assertEqual(issue_list[issue_list.index("--state") + 1], "all")

    def test_bugs_without_gh_report_an_error(self) -> None:
        with mock.patch.object(shadow, "_gh", side_effect=RuntimeError("gh: not logged in")):
            got = metrics.bugs("o/r", TODAY)
        self.assertEqual(list(got), ["error"])
        self.assertIn("not logged in", got["error"])
        self.assertEqual(list(metrics.bugs("", TODAY)), ["error"])
        with mock.patch.object(metrics, "PR_WORKFLOWS", ()):
            self.assertIn("qa-pr", metrics.bugs("o/r", TODAY)["error"])
        self.assertEqual(fixture_metrics(bugs=got)["bugs"], got)

    def test_enforcement_is_in_the_metrics(self) -> None:
        got = fixture_metrics()["enforcement"]
        self.assertEqual((got["status"], got["families"]), ("ready", ["it", "py"]))

    def test_summary_leads_with_enforcement(self) -> None:
        text = metrics.summary(fixture_metrics())
        first = next(line for line in text.splitlines() if line.strip() and not line.startswith("#"))
        self.assertIn("ready", first)
        self.assertIn("not whole-suite recall", text)
        for heading in ("Trend", "Flaky tests", "Duration", "Coverage", "Bugs"):
            self.assertIn(heading, text)
        self.assertIn("not measured", metrics.summary(fixture_metrics(bugs={"error": "no repo"}, coverage={"py-unit": None})))
        self.assertIn("Caught is 0", text)
        caught = metrics.summary(fixture_metrics(bugs={"caught": 2, "escaped": 0, "escaped_by_flow": {}, "unlinked": 0}))
        self.assertNotIn("Caught is 0", caught)


class TestPublish(unittest.TestCase):
    """`qa history publish`, `pull` and `fetch-quarantine` against a fixture repo and a temp bare remote."""

    PLAN = {"head": "c" * 40, "branch": "main", "edition": "all", "profile": "standard", "mode": "range",
            "tests": [{"id": "a", "source": "floor"}], "planner": {}}

    def setUp(self) -> None:
        from qa import __main__ as cli
        self.cli = cli
        self.root, tmp = testing.make_repo()
        self.addCleanup(tmp.cleanup)
        self.tmp = self.root.parent / (self.root.name + "-x")
        self.tmp.mkdir()
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, ignore_errors=True))
        self.bare = self.tmp / "remote.git"
        git(self.tmp, "init", "-q", "--bare", str(self.bare))
        git(self.root, "remote", "add", "origin", str(self.bare))
        self.summary = self.tmp / "summary.md"
        env = {k: v for k, v in __import__("os").environ.items()
               if not k.startswith(("GITHUB_", "QA_"))}
        self.env = {**env, "QA_ROOT": str(self.root)}
        self.issues: list[list[str]] = []

        def qgh(args):
            self.issues.append(args)
            if args[:2] == ["issue", "list"]:
                return "[]"
            if args[:2] == ["issue", "create"]:
                return "https://github.com/o/r/issues/1\n"
            return ""
        root, load = self.root, config.load
        patches = [mock.patch.object(quarantine, "_labelled", False),
                   mock.patch.object(quarantine, "_gh", side_effect=qgh),
                   mock.patch.object(shadow, "_gh", return_value="[]"),
                   mock.patch.object(branchstore, "_gh", side_effect=AssertionError("no fetch in publish")),
                   mock.patch.object(branchstore.time, "sleep"),
                   mock.patch.object(cli.config, "load", side_effect=lambda *a, **k: load(root))]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def _run_dir(self, name: str, outcome: str) -> Path:
        d = self.tmp / name
        d.mkdir()
        (d / "plan.json").write_text(json.dumps(self.PLAN), encoding="utf-8")
        (d / "results.json").write_text(json.dumps({"seconds": 3, "tests": [{"id": "a", "outcome": outcome, "seconds": 1.0}]}),
                                        encoding="utf-8")
        return d

    def _main(self, argv: list[str], env: dict) -> tuple[int, str, str]:
        import contextlib
        import io
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.dict("os.environ", env, clear=True), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                code = self.cli.main(argv)
            except SystemExit as exc:
                code = exc.code
        return code, out.getvalue(), err.getvalue()

    def _remote_file(self, name: str) -> str:
        return git(self.bare, "show", f"qa-history:{name}")

    def test_publish_end_to_end(self) -> None:
        env = {**self.env, "GITHUB_EVENT_NAME": "schedule", "GITHUB_ACTIONS": "true",
               "GITHUB_REF": "refs/heads/main", "GITHUB_STEP_SUMMARY": str(self.summary)}
        for i in range(3):
            d = self._run_dir(f"run{i}", "flaky")
            code, out, err = self._main(["--out", str(d), "history", "publish", "--remote", "origin", "--repo", "o/r"], env)
            self.assertEqual(code, 0, err)
            self.assertRegex(out.strip().splitlines()[-1], r"^[0-9a-f]{40}$")
        q = json.loads(self._remote_file("quarantine.json"))["tests"]
        self.assertEqual([(e["id"], e["issue"]) for e in q], [("a", "https://github.com/o/r/issues/1")])
        self.assertEqual(schema.check(json.loads(self._remote_file("metrics.json")), "metrics.v1.json"), [])
        self.assertEqual(len(self._remote_file("runs.jsonl").splitlines()), 3)
        self.assertIn("Enforcement", self.summary.read_text(encoding="utf-8"))
        self.assertEqual(git(self.bare, "log", "-1", "--format=%s", "qa-history"), f"qa-history: nightly all {'c' * 12}")
        self.assertEqual(sum(a[:2] == ["issue", "create"] for a in self.issues), 1)  # only the third run qualifies

    def test_publish_refused_for_pull_request(self) -> None:
        env = {**self.env, "GITHUB_EVENT_NAME": "pull_request", "GITHUB_ACTIONS": "true", "GITHUB_REF": "refs/pull/1/merge"}
        code, _, err = self._main(["--out", str(self._run_dir("r", "pass")), "history", "publish", "--remote", "origin"], env)
        self.assertEqual(code, 2)
        self.assertIn("may not write", err)
        self.assertEqual(git(self.root, "ls-remote", "origin", "qa-history"), "")

    def test_local_publish_without_remote_or_repo(self) -> None:
        with mock.patch.object(metrics, "bugs") as bugs:
            for i in range(3):
                code, out, err = self._main(["--out", str(self._run_dir(f"run{i}", "flaky")), "history", "publish"], self.env)
                self.assertEqual(code, 0, err)
        bugs.assert_not_called()
        self.assertIn("deferred (no --repo): a", out)
        self.assertTrue(git(self.root, "rev-parse", "--verify", "refs/heads/qa-history"))
        self.assertEqual(json.loads(git(self.root, "show", "qa-history:quarantine.json"))["tests"], [])
        self.assertIn("no --repo", json.loads(git(self.root, "show", "qa-history:metrics.json"))["bugs"]["error"])
        self.assertEqual(self.issues, [])

    def test_publish_without_results_still_writes_shadow_and_metrics(self) -> None:
        d = self.tmp / "empty"
        d.mkdir()
        (d / "plan.json").write_text(json.dumps(self.PLAN), encoding="utf-8")
        lines = self.tmp / "shadow.jsonl"
        lines.write_text(json.dumps({"ts": ts(TODAY), "head": "h", "caught": ["py:x.py"], "missed": [], "unmapped": []}) + "\n",
                         encoding="utf-8")
        code, _, err = self._main(["--out", str(d), "history", "publish", "--shadow", str(lines)], self.env)
        self.assertEqual(code, 0, err)
        self.assertNotIn("runs.jsonl", git(self.root, "ls-tree", "--name-only", "qa-history").split())
        self.assertEqual(len(git(self.root, "show", "qa-history:shadow.jsonl").splitlines()), 1)
        self.assertEqual(json.loads(git(self.root, "show", "qa-history:metrics.json"))["enforcement"]["failures"], 1)

    def test_publish_conflict_exits_4(self) -> None:
        with mock.patch.object(branchstore, "update", side_effect=branchstore.Conflict("moved")):
            code, _, err = self._main(["--out", str(self._run_dir("r", "pass")), "history", "publish"], self.env)
        self.assertEqual(code, 4)
        self.assertIn("moved", err)

    def test_pull_then_flaky_reads_the_branch(self) -> None:
        for i in range(2):
            self._main(["--out", str(self._run_dir(f"run{i}", "flaky")), "history", "publish"], self.env)
        to = self.tmp / "pulled"
        code, _, err = self._main(["history", "pull", "--to", str(to)], self.env)
        self.assertEqual(code, 0, err)
        code, out, _ = self._main(["history", "flaky", "--store", str(to)], self.env)
        self.assertEqual((code, out), (0, "1.000 a\n"))

    def test_fetch_quarantine(self) -> None:
        to = self.tmp / "q" / "quarantine.json"
        with mock.patch.object(branchstore, "fetch_file", return_value=None):
            self.assertEqual(self._main(["history", "fetch-quarantine", "--repo", "o/r", "--to", str(to)], self.env)[0], 0)
        self.assertEqual(json.loads(to.read_text(encoding="utf-8")), {"tests": []})
        with mock.patch.object(branchstore, "fetch_file", return_value='{"tests": [{"id": "a"}]}'):
            self._main(["history", "fetch-quarantine", "--repo", "o/r", "--to", str(to)], self.env)
        self.assertEqual(json.loads(to.read_text(encoding="utf-8"))["tests"], [{"id": "a"}])
        with mock.patch.object(branchstore, "fetch_file", side_effect=RuntimeError("gh api: HTTP 500")):
            code, _, err = self._main(["history", "fetch-quarantine", "--repo", "o/r", "--to", str(to)], self.env)
        self.assertEqual(code, 1)
        self.assertIn("HTTP 500", err)


if __name__ == "__main__":
    unittest.main()
