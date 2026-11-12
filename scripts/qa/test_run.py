"""Runner: plans become the right commands, results map back to catalog ids,
and nothing ends without an explicit outcome.

Run: python3 -m unittest discover -s scripts -p 'test_*.py'
"""

import argparse
import contextlib
import io
import json
import os
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest import mock

from qa import __main__ as qa_main
from qa import catalog, config, junit, paths, report, run, testing

JIRA_SYNC = "it:connectors/jira/test_jira.py::TestJira::test_tc_sync_001_full_sync"
ACL = "it:retrieval/test_search.py::test_acl_filter"


def plan_for(ids, graph=("neo4j",), needed=True):
    return {
        "head": "abc1234", "base": "000", "edition": "all", "profile": "standard", "mode": "pr",
        "tests": [{"id": i, "source": "floor", "why": ["x"], "grep": None} for i in ids],
        "stack": {"needed": needed, "graph_db": list(graph), "broker": "redis", "compose_profiles": []},
        "deferred": [], "dropped": [], "budget_trimmed": [], "missing": [], "unmapped": [], "planner": {"adapter": "none", "status": "skipped"},
    }


class RunCase(unittest.TestCase):
    def setUp(self) -> None:
        self.root, tmp = testing.make_repo()
        self.addCleanup(tmp.cleanup)
        self.cfg = config.load(self.root)
        self.rows = catalog.index(catalog.build(self.cfg))
        out = tempfile.TemporaryDirectory()
        self.addCleanup(out.cleanup)
        self.out = Path(out.name)


class TestEditionFlag(unittest.TestCase):
    def test_unknown_edition_exits_2(self) -> None:
        root, tmp = testing.make_repo(overlay=False)
        self.addCleanup(tmp.cleanup)
        err = io.StringIO()
        with mock.patch.dict(os.environ, {"QA_ROOT": str(root)}), contextlib.redirect_stderr(err):
            code = qa_main.main(["--out", str(root / "qa-out"), "impact", "--edition", "plus"])
        self.assertEqual(code, 2)
        self.assertIn("unknown edition 'plus'; known: all, oss", err.getvalue())


class TestResolve(RunCase):
    def test_browser_tests_run_after_the_integration_tests(self) -> None:
        # e2e sorts first by name, and indexing with the built-in model would stop the fixtures setting theirs.
        invs, _ = run.resolve(self.cfg, plan_for(["e2e:tests/e2e/login.spec.ts", ACL]), self.rows, self.out)
        self.assertEqual([i.family for i in invs], ["it-pytest", "playwright"])

    def test_ordered_lifecycle_module_runs_whole(self) -> None:
        invs, _ = run.resolve(self.cfg, plan_for([JIRA_SYNC]), self.rows, self.out)
        self.assertEqual(len(invs), 1)
        self.assertIn("connectors/jira/test_jira.py", invs[0].cmd)
        self.assertNotIn("connectors/jira/test_jira.py::TestJira::test_tc_sync_001_full_sync", invs[0].cmd)

    def test_single_unordered_test_runs_by_node_id(self) -> None:
        invs, _ = run.resolve(self.cfg, plan_for([ACL]), self.rows, self.out)
        self.assertIn("retrieval/test_search.py::test_acl_filter", invs[0].cmd)

    def test_one_invocation_per_graph_db(self) -> None:
        invs, _ = run.resolve(self.cfg, plan_for([ACL], graph=("neo4j", "arango")), self.rows, self.out)
        self.assertEqual([i.env["TEST_GRAPH_DB_TYPE"] for i in invs], ["neo4j", "arango"])

    def test_whole_suite_passes_the_suite_root(self) -> None:
        ids = [i for i, r in self.rows.items() if r["suite"] == "py-unit"]
        invs, _ = run.resolve(self.cfg, plan_for(ids, needed=False), self.rows, self.out)
        self.assertIn("tests/unit", invs[0].cmd)

    def test_mocha_gets_a_generated_config_with_explicit_spec(self) -> None:
        invs, _ = run.resolve(self.cfg, plan_for(["node:tests/modules/auth/auth.controller.test.ts"], needed=False), self.rows, self.out)
        config_path = Path(invs[0].cmd[invs[0].cmd.index("--config") + 1])
        self.assertIn("tests/modules/auth/auth.controller.test.ts", config_path.read_text(encoding="utf-8"))

    def test_unknown_id_is_not_run_with_reason(self) -> None:
        _, not_run = run.resolve(self.cfg, plan_for(["it:nope.py::test_x"]), self.rows, self.out)
        self.assertEqual(not_run, {"it:nope.py::test_x": ("no_result", "not in the catalog")})

    def test_smoke_selection_passes_grep(self) -> None:
        plan = plan_for([])
        plan["tests"] = [{"id": "e2e:tests/e2e/login.spec.ts", "source": "floor", "why": ["x"], "grep": "@smoke"}]
        invs, _ = run.resolve(self.cfg, plan, self.rows, self.out)
        self.assertEqual(invs[0].cmd[-2:], ["--grep", "@smoke"])

    def test_playwright_extra_args_come_from_env(self) -> None:
        plan = plan_for([])
        plan["tests"] = [{"id": "e2e:tests/e2e/login.spec.ts", "source": "floor", "why": ["x"], "grep": None}]
        with mock.patch.dict(os.environ, {"QA_PLAYWRIGHT_ARGS": "--update-snapshots=missing --workers=1"}):
            invs, _ = run.resolve(self.cfg, plan, self.rows, self.out)
        self.assertEqual(invs[0].cmd[-2:], ["--update-snapshots=missing", "--workers=1"])

    def test_playwright_suite_env_reaches_the_invocation(self) -> None:
        plan = plan_for([])
        plan["tests"] = [{"id": "e2e:tests/e2e/login.spec.ts", "source": "floor", "why": ["x"], "grep": None}]
        with mock.patch.dict(self.cfg.suite("e2e"), {"env": {"E2E_VISUAL": "true"}}):
            invs, _ = run.resolve(self.cfg, plan, self.rows, self.out)
        self.assertEqual(invs[0].env["E2E_VISUAL"], "true")


class TestMocharcScope(RunCase):
    """A mocha suite runs what its own mocharc selects, as its npm script does."""

    def test_the_suites_mocharc_spec_and_ignore_decide_its_files(self) -> None:
        apps = "backend/nodejs/apps"
        testing.write(self.root, f"{apps}/.mocharc.yaml", "require:\n  - 'ts-node/register'\nspec:\n"
                      "  - 'tests/modules/auth/*.test.ts'\n  - 'tests/kept/**/*.test.ts'\nignore: 'tests/kept/live/**'\n")
        testing.write(self.root, f"{apps}/tests/kept/a.test.ts", "it('a', () => {});\n")
        testing.write(self.root, f"{apps}/tests/kept/live/b.test.ts", "it('b', () => {});\n")
        testing.write(self.root, f"{apps}/tests/stale/c.test.ts", "it('c', () => {});\n")
        testing.commit(self.root, "mocharc")
        cfg = config.load(self.root)
        rows = catalog.index(catalog.build(cfg))
        mine = sorted(i for i, r in rows.items() if r["suite"] == "node-unit")
        self.assertEqual(mine, ["node:tests/kept/a.test.ts", "node:tests/modules/auth/auth.controller.test.ts"])
        # Left out by the suite's own config: not an orphan either.
        self.assertEqual([o for o in catalog.orphans(cfg) if o.startswith(apps)], [])
        invs, _ = run.resolve(cfg, plan_for(mine, needed=False), rows, self.out)
        rc = json.loads(Path(invs[0].cmd[invs[0].cmd.index("--config") + 1]).read_text(encoding="utf-8"))
        self.assertEqual((rc["spec"], rc["ignore"]), (["tests/modules/auth/*.test.ts", "tests/kept/**/*.test.ts"], ["tests/kept/live/**"]))


class TestMocharcParser(unittest.TestCase):
    def globs(self, text: str):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "apps").mkdir()
            (root / "apps/.mocharc.yaml").write_text(text, encoding="utf-8")
            return catalog.mocharc_globs(root, {"family": "mocha", "cwd": "apps"})

    def test_a_flow_spec_is_an_error_naming_the_file(self) -> None:
        with self.assertRaisesRegex(catalog.MocharcError, r"apps/\.mocharc\.yaml: line 1: unsupported YAML form"):
            self.globs("spec: ['a.test.ts','b.test.ts']\n")

    def test_a_trailing_comment_is_not_part_of_the_value(self) -> None:
        self.assertEqual(self.globs("spec: 'tests/x.test.ts' # all\n"), (["apps/tests/x.test.ts"], []))
        self.assertEqual(self.globs("spec: tests/x.test.ts # all\n"), (["apps/tests/x.test.ts"], []))

    def test_a_column_zero_comment_does_not_end_a_list(self) -> None:
        self.assertEqual(self.globs("spec:\n  - 'a.ts'\n# a note\n\n  - b.ts # why\n  - \"c.ts\"\ntimeout: 1\n"),
                         (["apps/a.ts", "apps/b.ts", "apps/c.ts"], []))

    def test_a_flow_ignore_is_an_error(self) -> None:
        with self.assertRaisesRegex(catalog.MocharcError, "line 2: unsupported YAML form"):
            self.globs("spec: 'a.ts'\nignore: ['live/**']\n")

    def test_anything_else_it_cannot_read_is_an_error(self) -> None:
        for text, why in (("spec: 'tests/{a,b}.ts'\n", "glob syntax"), ("spec: 'tests/[ab].ts'\n", "glob syntax"),
                          ("spec: '!tests/a.ts'\n", "glob syntax"), ("spec: |\n  a.ts\n", "unsupported"),
                          ("spec:\n  nested: 1\n", "expected a list item"), ("spec:\ntimeout: 1\n", "no entries"),
                          ("spec: a.ts\nspec: b.ts\n", "twice"), ("spec: 'a.ts\n", "quoted value"), ("spec: *x\n", "unsupported"),
                          ("nonsense\n", "not a top-level key")):
            with self.subTest(text), self.assertRaisesRegex(catalog.MocharcError, why):
                self.globs(text)

    def test_other_keys_may_use_any_form(self) -> None:
        self.assertEqual(self.globs("require: ['x']\nextension:\n  - ts\n---\nspec:\n- 'a.ts'\nignore: 'b/**'\n"), (["apps/a.ts"], ["apps/b/**"]))


class TestMocharcLint(RunCase):
    APPS = "backend/nodejs/apps"

    def lint(self, mocharc: str):
        testing.write(self.root, f"{self.APPS}/.mocharc.yaml", mocharc)
        testing.commit(self.root, "mocharc")
        cfg = config.load(self.root)
        return catalog.lint(cfg, catalog.build(cfg))

    def test_a_spec_that_matches_nothing_is_a_lint_error(self) -> None:
        errors, _ = self.lint("spec: 'typo/**/*.test.ts'\n")
        self.assertTrue(any("suite node-unit: the spec of .mocharc.yaml matches none of the" in e for e in errors), errors)

    def test_a_file_forgotten_from_the_list_is_counted(self) -> None:
        testing.write(self.root, f"{self.APPS}/tests/forgotten.test.ts", "it('a', () => {});\n")
        errors, warnings = self.lint("spec: 'tests/modules/**/*.test.ts'\n")
        self.assertFalse([e for e in errors if "node-unit" in e], errors)
        self.assertTrue(any("suite node-unit: 1 of the 3 files its tests globs select are left out by .mocharc.yaml" in w for w in warnings), warnings)

    def test_catalog_commands_fail_naming_an_unreadable_mocharc(self) -> None:
        testing.write(self.root, f"{self.APPS}/.mocharc.yaml", "spec: ['tests/a.test.ts']\n")
        testing.commit(self.root, "bad mocharc")
        for action in ("lint", "check"):
            err = io.StringIO()
            with mock.patch.object(paths, "REPO", self.root), mock.patch.dict(os.environ, {"QA_ROOT": str(self.root)}), \
                    contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
                config.clear_cache()
                code = qa_main.main(["catalog", action])
            self.assertEqual(code, 1, action)
            self.assertIn("error: backend/nodejs/apps/.mocharc.yaml: line 1", err.getvalue(), action)


class TestNoCaseInJunit(unittest.TestCase):
    def outcome(self, family: str, junit_file: bool, exit_code: int) -> dict:
        ids = ["node:tests/a.test.ts"]
        with tempfile.TemporaryDirectory() as d:
            x = Path(d) / "j.xml"
            x.write_text('<testsuite name="Mocha"><testcase classname="c" name="n" file="/r/tests/other.test.ts"/></testsuite>', encoding="utf-8")
            inv = run.Invocation("s", "s", family, "apps", ["x"], ids, x if junit_file else None)
            return run._outcomes(inv, {ids[0]: {"path": "apps/tests/a.test.ts"}}, exit_code)[ids[0]]

    def test_a_clean_exit_with_no_case_for_the_file_is_an_error(self) -> None:
        got = self.outcome("mocha", True, 0)
        self.assertEqual(got["outcome"], "error")
        self.assertIn("no result in JUnit", got["details"])

    def test_a_suite_with_no_per_file_report_still_passes_on_its_exit_code(self) -> None:
        self.assertEqual(self.outcome("npm-script", False, 0)["outcome"], "pass")
        self.assertEqual(self.outcome("npm-script", False, 1)["outcome"], "fail")

    def test_a_failed_exit_keeps_its_reason(self) -> None:
        self.assertIn("no per-file result", self.outcome("mocha", True, 1)["details"])


class TestLogCause(unittest.TestCase):
    def cause(self, text: str) -> str:
        with tempfile.TemporaryDirectory() as d:
            log = Path(d) / "x.log"
            log.write_text(text, encoding="utf-8")
            return run._log_cause(str(log))

    def test_it_names_the_abort_not_earlier_chatter(self) -> None:
        got = self.cause("ValueError: from a test that printed\nImportError while loading conftest 'c.py'.\nc.py:7: in <module>\n"
                         "E   ModuleNotFoundError: No module named 'x'\nKeyError: later\n")
        self.assertEqual(got, "ImportError while loading conftest 'c.py'.. ModuleNotFoundError: No module named 'x'")
        self.assertEqual(self.cause("ValueError: early\n" + "ok\n" * 300 + "ERROR: file or directory not found: a.py\n"),
                         "ERROR: file or directory not found: a.py")

    def test_credentials_never_reach_the_excerpt(self) -> None:
        got = self.cause("ConnectionError: https://bob:hunter2@host/db Authorization: Bearer abc.def-123456 tok ghp_abcdefghijkl\n")
        for secret in ("hunter2", "abc.def-123456", "ghp_abcdefghijkl", "bob:"):
            self.assertNotIn(secret, got)
        self.assertIn("https://***:***@host/db", got)
        self.assertIn("RuntimeError: bearer ***", self.cause("RuntimeError: bearer abcdefgh12345\n"))


JIRA_FULL = "it:connectors/jira/test_jira.py::TestJira::test_tc_sync_001_full_sync"
JIRA_INCR = "it:connectors/jira/test_jira.py::TestJira::test_tc_incr_001_incremental"
RANKING = "it:retrieval/test_search.py::test_ranking"


class TestConftestGroups(RunCase):
    """Stack tests run in one pytest process per conftest scope, each with its own JUnit file."""

    def setUp(self) -> None:
        super().setUp()
        for d in ("retrieval", "connectors/jira"):
            testing.write(self.root, f"integration-tests/{d}/conftest.py", "")
        testing.commit(self.root, "conftests")
        self.cfg = config.load(self.root)
        self.rows = catalog.index(catalog.build(self.cfg))
        self.ids = [ACL, JIRA_FULL]

    def invs(self, ids=None, explicit=False):
        return run.resolve(self.cfg, plan_for(ids or self.ids), self.rows, self.out, explicit=explicit)[0]

    def test_each_conftest_scope_gets_its_own_process_and_junit(self) -> None:
        invs = self.invs()
        self.assertEqual([i.label for i in invs], ["it-stack-neo4j-connectors_jira", "it-stack-neo4j-retrieval"])
        self.assertEqual([i.ids for i in invs], [[JIRA_FULL], [ACL]])
        self.assertEqual(len({i.junit for i in invs}), 2)
        self.assertIn("retrieval/test_search.py::test_acl_filter", invs[1].cmd)
        self.assertNotIn("retrieval/test_search.py::test_acl_filter", invs[0].cmd)
        self.assertEqual(invs[1].modules, junit.module_names(["retrieval/test_search.py"]))

    def test_groups_multiply_by_graph_db_and_one_group_keeps_the_plain_label(self) -> None:
        got = run.resolve(self.cfg, plan_for(self.ids, graph=("neo4j", "arango")), self.rows, self.out)[0]
        self.assertEqual(sorted({(i.graph_db, i.ids[0]) for i in got}), sorted((g, t) for g in ("arango", "neo4j") for t in self.ids))
        self.assertEqual([i.label for i in self.invs([ACL])], ["it-stack-neo4j"])

    def test_labels_never_collide(self) -> None:
        got = run._group_suffixes(["a_b/c", "a/b_c", "plain", "x-retry", "x"])
        self.assertEqual(len(set(got.values())), 5)
        self.assertEqual((got["plain"], got["x"]), ("-plain", "-x"))
        self.assertRegex(got["a_b/c"], r"^-a_b_c-[0-9a-f]{8}$")
        self.assertRegex(got["x-retry"], r"^-x-retry-[0-9a-f]{8}$")
        self.assertEqual(run._group_suffixes(["only"]), {"only": ""})

    def test_owners_that_sanitise_alike_get_their_own_junit_files(self) -> None:
        for d, name in (("a_b/c", "test_one.py"), ("a/b_c", "test_two.py")):
            testing.write(self.root, f"integration-tests/{d}/conftest.py", "")
            testing.write(self.root, f"integration-tests/{d}/{name}", "def test_x():\n    assert 1 == 1\n")
        testing.commit(self.root, "alike")
        self.cfg, self.rows = config.load(self.root), None
        self.rows = catalog.index(catalog.build(self.cfg))
        invs = self.invs(["it:a_b/c/test_one.py::test_x", "it:a/b_c/test_two.py::test_x"])
        self.assertEqual(len({i.junit for i in invs}), 2)
        self.assertEqual(len({i.label for i in invs}), 2)

    def test_nested_conftests_are_separate_groups(self) -> None:
        for d, name in (("a", "test_outer.py"), ("a/b", "test_inner.py")):
            testing.write(self.root, f"integration-tests/{d}/conftest.py", "")
            testing.write(self.root, f"integration-tests/{d}/{name}", "def test_x():\n    assert 1 == 1\n")
        testing.commit(self.root, "nested")
        self.cfg = config.load(self.root)
        self.rows = catalog.index(catalog.build(self.cfg))
        invs = self.invs(["it:a/test_outer.py::test_x", "it:a/b/test_inner.py::test_x"])
        self.assertEqual([i.ids for i in invs], [["it:a/test_outer.py::test_x"], ["it:a/b/test_inner.py::test_x"]])

    def test_a_group_past_the_deadline_is_not_started_and_blocks(self) -> None:
        results: dict = {ACL: {"outcome": "pass"}}
        invs = self.invs()
        for inv in invs:
            inv.deadline = run.time.monotonic() - 1
        with mock.patch.object(run, "_execute", side_effect=AssertionError("started")):
            for inv in invs:
                run._run_one(self.cfg, inv, self.rows, self.out, {}, {}, results, [], False)
        self.assertEqual(results[JIRA_FULL]["kind"], "deadline")
        self.assertIn("was not started", results[JIRA_FULL]["details"])
        self.assertEqual(results[ACL]["outcome"], "not_run")  # a pass elsewhere does not cover this group
        verdict, blocking = report.verdict(self.cfg, {"tests": [{"id": i, **r} for i, r in results.items()]}, frozenset({"secret", "profile"}))
        self.assertEqual(verdict, "fail")
        self.assertEqual(sorted(blocking), sorted(self.ids))

    def test_a_failure_is_not_overwritten_by_the_deadline(self) -> None:
        results: dict = {ACL: {"outcome": "fail", "details": "x"}}
        inv = self.invs([ACL])[0]
        inv.deadline = run.time.monotonic() - 1
        run._run_one(self.cfg, inv, self.rows, self.out, {}, {}, results, [], False)
        self.assertEqual(results[ACL]["outcome"], "fail")

    def timeout_given(self, inv) -> int:
        seen = []

        def fake(cmd, **kw):
            seen.append(kw["timeout"])
            return mock.Mock(returncode=0)

        with mock.patch.object(run.subprocess, "run", fake):
            run._execute(self.cfg, inv, self.out / "logs", {})
        return seen[0]

    def test_a_group_gets_its_own_timeout_or_the_time_left(self) -> None:
        inv = self.invs()[0]
        self.assertEqual(self.timeout_given(inv), 90 * 60)
        inv.deadline = run.time.monotonic() + 100
        self.assertLessEqual(self.timeout_given(inv), 100)
        self.assertGreater(self.timeout_given(inv), 90)
        inv.deadline = run.time.monotonic() + 100000
        self.assertEqual(self.timeout_given(inv), 90 * 60)

    def test_the_run_sets_the_deadline_on_every_invocation(self) -> None:
        seen = []

        def fake_run_one(cfg, inv, *a):
            seen.append(inv.deadline)

        with mock.patch.object(run, "preflight", return_value=({}, [])), mock.patch.object(run, "_run_one", fake_run_one), \
                mock.patch.dict(os.environ, {"QA_DEADLINE_SECONDS": "60"}):
            run.run(self.cfg, plan_for(["py:tests/unit/services/test_retrieval.py"], needed=False), self.rows, self.out, retry=False)
        self.assertEqual(len(seen), 1)
        self.assertLessEqual(seen[0], run.time.monotonic() + 60)

    def test_the_engine_and_the_fixture_agree_on_the_variable(self) -> None:
        text = (paths.REPO / "integration-tests/conftest.py").read_text(encoding="utf-8")
        self.assertIn(run.LEAVE_MODELS, text)

    def test_a_longer_target_is_blamed_not_its_suffix(self) -> None:
        inv = run.Invocation("g", "s", "it-pytest", "integration-tests", ["py", "pytest", "x.py::t", "z/x.py::t"], ["it:x.py::t", "it:z/x.py::t"], self.out / "j.xml")
        log = self.out / "suffix.log"
        log.write_text("ERROR: not found: /w/integration-tests/z/x.py::t\n", encoding="utf-8")
        rows = {"it:x.py::t": {"path": "integration-tests/x.py"}, "it:z/x.py::t": {"path": "integration-tests/z/x.py"}}
        rerun, errors = run._drop_missing(inv, rows, str(log))
        self.assertEqual(list(errors), ["it:z/x.py::t"])
        self.assertEqual(rerun.ids, ["it:x.py::t"])

    def test_a_rerun_that_exits_4_again_fails_closed(self) -> None:
        log = self.out / "again.log"
        log.write_text(f"ERROR: not found: /w/integration-tests/{RANKING.split(':', 1)[1]}\n", encoding="utf-8")
        calls = []

        def execute(cfg, inv, log_dir, env):
            calls.append(inv)
            return {"exit": 4, "label": inv.label, "log": str(log)}

        with mock.patch.object(run, "_execute", execute):
            results, _ = self.run_groups(execute, [ACL, RANKING], explicit=True)
        self.assertEqual(len(calls), 2, "rerun once only")
        self.assertEqual(results[RANKING]["outcome"], "error")
        self.assertEqual(results[ACL]["outcome"], "error")
        self.assertIn("wrote no JUnit file", results[ACL]["details"])

    def test_a_directory_without_a_conftest_groups_under_its_top_level_directory(self) -> None:
        self.assertEqual(run._conftest_owner(self.root / "integration-tests", "connectors/jira/deep/test_x.py"), "connectors/jira")
        self.assertEqual(run._conftest_owner(self.root / "integration-tests", "other/deep/test_x.py"), "other")
        self.assertEqual(run._conftest_owner(self.root / "integration-tests", "test_top.py"), ".")

    def run_groups(self, execute, ids=None, explicit=False):
        results: dict = {}
        not_run: dict = {}
        log: list = []
        for inv in self.invs(ids, explicit):
            run._run_one(self.cfg, inv, self.rows, self.out / "logs", {}, not_run, results, log, False)
        return results, log

    def junit_for(self, inv, passed):
        inv.junit.parent.mkdir(parents=True, exist_ok=True)
        cases = "".join(f'<testcase classname="{m}" name="{name}"/>' for m, name in passed)
        inv.junit.write_text(f"<testsuite>{cases}</testsuite>", encoding="utf-8")

    def test_a_group_that_writes_no_junit_fails_only_its_own_tests(self) -> None:
        def execute(cfg, inv, log_dir, env):
            if "retrieval" in inv.label:
                self.junit_for(inv, [("retrieval.test_search", "test_acl_filter")])
                return {"exit": 0, "label": inv.label}
            return {"exit": 4, "label": inv.label}

        with mock.patch.object(run, "_execute", execute):
            results, _ = self.run_groups(execute)
        self.assertEqual(results[ACL]["outcome"], "pass")
        self.assertEqual(results[JIRA_FULL]["outcome"], "error")
        self.assertIn("wrote no JUnit file", results[JIRA_FULL]["details"])

    def test_every_group_gets_the_whole_timeout(self) -> None:
        seen = []

        def fake(cmd, **kw):
            seen.append(kw["timeout"])
            return mock.Mock(returncode=0)

        with mock.patch.object(run.subprocess, "run", fake), mock.patch.dict(os.environ, {"QA_TIMEOUT_IT_PYTEST": "900"}):
            for inv in self.invs():
                run._execute(self.cfg, inv, self.out / "logs", {})
        self.assertEqual(seen, [900, 900])

    def test_a_test_pytest_cannot_find_is_the_only_error_and_the_group_still_runs(self) -> None:
        logs = self.out / "abort.log"
        logs.write_text(f"collecting ... ERROR: not found: /w/integration-tests/{RANKING.split(':', 1)[1]}\n(no match in any of [<Module x>])\n",
                        encoding="utf-8")
        calls = []

        def execute(cfg, inv, log_dir, env):
            calls.append(inv)
            if len(calls) == 1:
                return {"exit": 4, "label": inv.label, "log": str(logs)}
            self.junit_for(inv, [("retrieval.test_search", "test_acl_filter")])
            return {"exit": 0, "label": inv.label, "log": str(logs)}

        with mock.patch.object(run, "_execute", execute):
            results, log = self.run_groups(execute, [ACL, RANKING], explicit=True)
        self.assertEqual(results[ACL]["outcome"], "pass")
        self.assertEqual(results[RANKING]["outcome"], "error")
        self.assertIn(f"pytest cannot find {RANKING.split(':', 1)[1]}", results[RANKING]["details"])
        self.assertEqual(len(log), 2)
        self.assertNotIn(RANKING.split(":", 1)[1], calls[1].cmd)
        self.assertEqual(calls[1].ids, [ACL])

    def test_a_log_naming_an_unknown_target_stays_closed(self) -> None:
        inv = self.invs([ACL])[0]
        log = self.out / "other.log"
        log.write_text("ERROR: not found: /w/elsewhere.py::x\n", encoding="utf-8")
        self.assertEqual(run._drop_missing(inv, self.rows, str(log)), (None, {}))

    def test_when_every_test_is_missing_nothing_reruns(self) -> None:
        inv = self.invs([ACL])[0]
        log = self.out / "all.log"
        log.write_text("ERROR: found no collectors for /w/integration-tests/retrieval/test_search.py::test_acl_filter\n", encoding="utf-8")
        rerun, errors = run._drop_missing(inv, self.rows, str(log))
        self.assertIsNone(rerun)
        self.assertEqual(list(errors), [ACL])


class TestRunKeepsItsPlan(RunCase):
    def test_a_plan_from_elsewhere_is_copied_into_the_run_directory(self) -> None:
        elsewhere = self.out / "elsewhere.json"
        elsewhere.write_text(json.dumps(plan_for([ACL])), encoding="utf-8")
        target = self.out / "run"
        with mock.patch.object(paths, "REPO", self.root), mock.patch.dict(os.environ, {"QA_ROOT": str(self.root)}), \
                mock.patch.object(run, "run", return_value={"tests": [], "fatal": [], "seconds": 0}), \
                contextlib.redirect_stdout(io.StringIO()):
            config.clear_cache()
            self.assertEqual(qa_main.main(["--out", str(target), "run", "--plan", str(elsewhere)]), 0)
        self.assertEqual(json.loads((target / "plan.json").read_text(encoding="utf-8"))["tests"][0]["id"], ACL)


class TestPytestAbort(unittest.TestCase):
    def test_an_aborted_pytest_names_its_cause(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            log = Path(d) / "it.log"
            log.write_text("ImportError while loading conftest '/w/integration-tests/sdk_tests/handwritten/python/conftest.py'.\n"
                           "sdk_tests/handwritten/python/conftest.py:7: in <module>\n    from pipeshub_sdk import X\n"
                           "E   ModuleNotFoundError: No module named 'pipeshub_sdk'\n", encoding="utf-8")
            inv = run.Invocation("it-stack", "it-stack", "it-pytest", "integration-tests", ["pytest"], ["it:a.py::t"], Path(d) / "none.xml")
            got = run._with_cause(inv, run._outcomes(inv, {}, 4), str(log))["it:a.py::t"]
        self.assertEqual(got["outcome"], "error")
        self.assertTrue(got["details"].endswith(
            "(exit 4); see its log: ImportError while loading conftest "
            "'/w/integration-tests/sdk_tests/handwritten/python/conftest.py'.. ModuleNotFoundError: No module named 'pipeshub_sdk'"),
            got["details"])


class TestUnittestAttribution(unittest.TestCase):
    IDS = ["scripts:scripts/qa/test_a.py", "scripts:scripts/qa/test_b.py", "scripts:scripts/test_c.py"]
    ROWS = {i: {"path": i.split(":", 1)[1]} for i in IDS}
    SEP = "=" * 70

    def attribute(self, text: str | None, exit_code: int = 1):
        inv = run.Invocation("scripts", "scripts", "unittest", ".", ["python"], self.IDS, None)
        if text is None:
            return run._attribute_unittest(inv, self.ROWS, exit_code, None)
        with tempfile.TemporaryDirectory() as d:
            log = Path(d) / "scripts.log"
            log.write_text(text, encoding="utf-8")
            return run._attribute_unittest(inv, self.ROWS, exit_code, str(log))

    def summary(self, ran: int, counts: str) -> str:
        return f"{'-' * 70}\nRan {ran} tests in 1.200s\n\nFAILED ({counts})\nresults: fail 1 (1s)\n"

    def block(self, kind: str, name: str, where: str, why: str = "AssertionError: 1 != 2") -> str:
        return f"{self.SEP}\n{kind}: {name} ({where})\n{'-' * 70}\nTraceback (most recent call last):\n  File x\n{why}\n\n"

    def test_a_failure_counts_against_the_file_that_failed(self) -> None:
        got, suite = self.attribute("FAIL: stray (printed.by.a.test)\n" + self.block("FAIL", "test_x", "qa.test_a.TestA.test_x")
                                    + self.block("ERROR", "setUpClass", "test_c.TestC") + self.summary(40, "failures=1, errors=1"))
        self.assertIsNone(suite)
        self.assertEqual(got[self.IDS[0]]["outcome"], "fail")
        self.assertEqual(got[self.IDS[0]]["details"], "1 failing: test_x: AssertionError: 1 != 2")
        self.assertEqual(got[self.IDS[1]]["outcome"], "pass")
        self.assertEqual(got[self.IDS[2]]["outcome"], "error")

    def test_a_module_that_cannot_load_is_traced_to_its_file(self) -> None:
        got, suite = self.attribute(self.block("ERROR", "qa.test_b", "unittest.loader._FailedTest.qa.test_b", "ImportError: nope")
                                    + self.summary(1, "errors=1"))
        self.assertIsNone(suite)
        self.assertEqual(got[self.IDS[1]]["outcome"], "error")

    def test_what_no_file_explains_is_one_suite_level_failure_with_the_count(self) -> None:
        got, suite = self.attribute(self.block("FAIL", "test_y", "other.test_z.T.test_y") + self.summary(40, "failures=1"))
        self.assertEqual({r["outcome"] for r in got.values()}, {"pass"})
        self.assertEqual(suite["outcome"], "fail")
        self.assertIn("1 of 40 tests failed, 1 not traced to a selected file: test_y", suite["details"])

    def test_a_run_with_no_summary_is_one_error_and_the_files_are_unreported(self) -> None:
        got, suite = self.attribute("Traceback\nImportError: boom\n", -1)
        self.assertEqual({(r["outcome"], r["kind"]) for r in got.values()}, {("not_run", "no_result")})
        self.assertEqual(suite["outcome"], "error")
        self.assertIn("ended before its summary (exit -1): ImportError: boom", suite["details"])
        self.assertEqual(self.attribute(None)[1]["outcome"], "error")

    def test_the_suite_level_result_reaches_the_results(self) -> None:
        root, tmp = testing.make_repo()
        self.addCleanup(tmp.cleanup)
        testing.write(root, "scripts/qa/test_only.py", "def f():\n    pass\n")
        testing.commit(root, "unittest file")
        cfg = config.load(root)
        with mock.patch.dict(cfg.suite("py-unit"), {"family": "unittest", "cwd": ".", "tests": ["scripts/qa/test_*.py"], "mirror": []}, clear=False):
            rows = catalog.index(catalog.build(cfg))
            ids = [i for i, r in rows.items() if r["suite"] == "py-unit"]
            with tempfile.TemporaryDirectory() as out:
                result = self._run(cfg, rows, ids, Path(out))
        got = {t["id"]: t for t in result["tests"]}
        self.assertEqual(got["scripts:(suite)"]["outcome"], "fail")
        self.assertEqual(got[ids[0]]["outcome"], "pass")

    def _run(self, cfg, rows, ids, out):
        log = out / "scripts.log"
        log.write_text(self.block("FAIL", "test_y", "other.test_z.T.test_y") + self.summary(3, "failures=1"), encoding="utf-8")
        with mock.patch.object(run, "preflight", return_value=({}, [])), \
                mock.patch.object(run, "_execute", return_value={"exit": 1, "label": "x", "log": str(log)}):
            return run.run(cfg, plan_for(ids, needed=False), rows, out, retry=False)


class TestEngineEnvIsHidden(unittest.TestCase):
    def test_exported_engine_variables_are_hidden_while_a_module_runs(self) -> None:
        with mock.patch.dict(os.environ, {"QA_PORT_OFFSET": "20000", "PIPESHUB_TEST_ENV": "local", "HOME_X": "1"}):
            testing.hide_engine_env()
            try:
                self.assertEqual((os.environ.get("QA_PORT_OFFSET"), os.environ.get("PIPESHUB_TEST_ENV"), os.environ["HOME_X"]),
                                 (None, None, "1"))
            finally:
                testing.restore_engine_env()
            self.assertEqual(os.environ["QA_PORT_OFFSET"], "20000")


class TestExternalStack(RunCase):
    def test_external_stack_runs_only_its_own_graph_db(self) -> None:
        with mock.patch.dict(os.environ, {"QA_STACK_GRAPH_DB": "neo4j"}):
            dry = run.run(self.cfg, plan_for([ACL], graph=("neo4j", "arango")), self.rows, self.out, stack_mode="external", dry_run=True)
        self.assertEqual([s.graph_db for s in dry["stack"]], ["neo4j"])


class TestGraphRuns(RunCase):
    def _run(self, execute_outcomes, **kw):
        stacks = [mock.Mock(graph_db=g, events=[], environment={}, build=False, upgrade=False, env=mock.Mock(return_value={"G": g})) for g in ("neo4j", "arango")]
        outs = iter(execute_outcomes)
        with mock.patch.object(run, "preflight", return_value=({}, [])), \
             mock.patch.object(run, "_stacks", return_value=stacks), \
             mock.patch.object(run, "_execute", return_value={"exit": 0, "label": "x"}) as ex, \
             mock.patch.object(run, "_outcomes", side_effect=lambda *a: {ACL: next(outs)}):
            result = run.run(self.cfg, plan_for([ACL], graph=("neo4j", "arango")), self.rows, self.out, stack_mode="external", retry=False)
        return result, ex

    def test_a_neo4j_failure_is_not_hidden_by_an_arango_pass(self) -> None:
        result, _ = self._run([{"outcome": "fail", "seconds": 1, "details": "neo4j: boom"}, {"outcome": "pass", "seconds": 1, "details": ""}])
        self.assertEqual(result["tests"][0]["outcome"], "fail")
        self.assertIn("neo4j: boom", result["tests"][0]["details"])

    def test_external_stack_runs_the_graph_it_serves(self) -> None:
        with mock.patch.dict(os.environ, {"QA_STACK_GRAPH_DB": "neo4j"}), \
             mock.patch.object(run, "preflight", return_value=({}, [])), \
             mock.patch.object(run.Stack, "start"), mock.patch.object(run.Stack, "stop"), mock.patch.object(run.Stack, "collect_logs"), \
             mock.patch.object(run.Stack, "env", return_value={}), \
             mock.patch.object(run, "_execute", return_value={"exit": 0, "label": "x"}) as ex, \
             mock.patch.object(run, "_outcomes", return_value={ACL: {"outcome": "pass", "seconds": 1, "details": ""}}):
            result = run.run(self.cfg, plan_for([ACL], graph=("neo4j", "arango")), self.rows, self.out, stack_mode="external", retry=False)
        self.assertEqual(ex.call_count, 1)
        self.assertEqual(result["tests"][0]["outcome"], "pass")


class TestSeed(unittest.TestCase):
    def setUp(self) -> None:
        import importlib.util
        path = Path(__file__).resolve().parents[2] / "integration-tests/stack/seed.py"
        spec = importlib.util.spec_from_file_location("qa_seed_under_test", path)
        self.seed = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.seed)

    def fake(self, org_status: int, auth_ok: bool):
        def call(method, url, body, headers=None):
            if url.endswith("/api/v1/org"):
                return org_status, {}, {"error": "x"}
            if url.endswith("/initAuth"):
                return 200, {"x-session-token": "s"}, {}
            if url.endswith("/authenticate"):
                # An OSS access token: it already carries userId, so no org switch.
                return (200, {}, {"accessToken": "h.eyJ1c2VySWQiOiAidSJ9.s"}) if auth_ok else (401, {}, {})
            return 200, {}, {}
        return call

    def test_multi_org_stack_reuses_an_existing_admin(self) -> None:
        with mock.patch.object(self.seed, "_call", self.fake(401, auth_ok=True)):
            self.assertEqual(self.seed.seed("http://x", "a@b.test", "pw"), "reused")

    def test_ee_login_switches_to_the_org_token(self) -> None:
        import base64
        import json as _json
        def jwt(claims):
            return "h." + base64.urlsafe_b64encode(_json.dumps(claims).encode()).decode().rstrip("=") + ".s"
        seen = []
        def call(method, url, body, headers=None):
            seen.append((url.rsplit("/api/v1/", 1)[1], (headers or {}).get("Authorization")))
            if url.endswith("/initAuth"):
                return 200, {"x-session-token": "s"}, {}
            if url.endswith("/authenticate"):
                return 200, {}, {"accessToken": jwt({"email": "a@b.test"}), "orgId": "o1"}
            if url.endswith("/token/switch"):
                return 200, {}, {"accessToken": jwt({"userId": "u1"})}
            return 200, {}, {}
        with mock.patch.object(self.seed, "_call", call):
            self.seed.seed("http://x", "a@b.test", "pw")
        self.assertEqual(seen[-1], ("org/onboarding-status", "Bearer " + jwt({"userId": "u1"})))

    def test_multi_org_stack_without_an_admin_says_why(self) -> None:
        with (
            mock.patch.object(self.seed, "_call", self.fake(401, auth_ok=False)),
            self.assertRaisesRegex(self.seed.SeedError, "verified signups"),
        ):
            self.seed.seed("http://x", "a@b.test", "pw")


class TestComposeOverrides(unittest.TestCase):
    def test_overrides_follow_the_ci_file_and_expand_the_graph_db(self) -> None:
        from qa import stack
        s = stack.Stack(Path("."), "managed", "arango", "redis", [], "p", "http://x", Path("."))
        with mock.patch.dict(os.environ, {"QA_COMPOSE_OVERRIDES": "/o/ports.{graph}.yml"}):
            cmd = s.compose("up", "-d")
        self.assertEqual(cmd[4:8], ["-f", s.compose_file, "-f", "/o/ports.arango.yml"])


class TestStackEnv(unittest.TestCase):
    def test_a_managed_stack_names_its_app_container_and_lifts_the_rate_limits(self) -> None:
        from qa import stack
        with mock.patch.dict(os.environ, clear=True):
            env = stack.Stack(Path("."), "managed", "neo4j", "redis", [], "proj", "http://x", Path(".")).env()
        self.assertEqual(env["PIPESHUB_APP_CONTAINER"], "proj-pipeshub-ai-1")
        self.assertEqual(env["MAX_REQUESTS_PER_MINUTE"], "200000")

    def test_an_external_stack_keeps_its_own_settings(self) -> None:
        from qa import stack
        with mock.patch.dict(os.environ, clear=True):
            env = stack.Stack(Path("."), "external", "neo4j", "redis", [], "proj", "http://x", Path(".")).env()
        self.assertNotIn("PIPESHUB_APP_CONTAINER", env)
        self.assertNotIn("MAX_REQUESTS_PER_MINUTE", env)


HEAD = "0123456789abcdef" + "0" * 24


class TestManagedStack(RunCase):
    def managed_plan(self, ids=(JIRA_SYNC,), graph=("neo4j", "arango")):
        plan = plan_for(list(ids), graph=graph)
        plan["head"] = HEAD
        return plan

    def test_managed_stack_is_named_after_the_commit(self) -> None:
        with mock.patch.dict(os.environ, {"QA_RUN_ID": "77"}):
            os.environ.pop("QA_COMPOSE_PROJECT", None)
            stacks = run._stacks(self.cfg, self.managed_plan(), "managed", None, self.out / "logs", True, self.out)
            sharded = run._stacks(self.cfg, self.managed_plan(), "managed", None, self.out / "logs", True, self.out, shard=(2, 4))
        self.assertEqual([s.project for s in stacks], ["qa-0123456789ab-77-neo4j", "qa-0123456789ab-77-arango"])
        image = json.loads((self.out / "stack/image.json").read_text(encoding="utf-8"))
        self.assertEqual(image["services"]["pipeshub-ai"]["image"], "pipeshub-ai:qa-0123456789ab")
        self.assertEqual(stacks[0].extra_env["GIT_COMMIT"], HEAD)
        self.assertEqual([s.build for s in stacks], [True, False])
        self.assertIn(str(self.out / "stack/image.json"), stacks[0].compose("up"))
        self.assertEqual(sharded[0].project, "qa-0123456789ab-77s2-neo4j")

    def test_overlay_stack_hook_adds_env_and_compose_files(self) -> None:
        from qa import stack
        tmp = Path(tempfile.mkdtemp(dir=self.out))
        env, files = self.cfg.prepare_stack(tmp)
        self.assertEqual((env, files), ({"PLUS_STACK": "1"}, [str(tmp / "plus.json")]))
        s = stack.Stack(Path("."), "managed", "neo4j", "redis", [], "p", "http://x", Path("."), overrides=files, extra_env=env)
        with mock.patch.dict(os.environ, {"QA_COMPOSE_OVERRIDES": ""}):
            self.assertEqual(s.compose("up")[-3:], ["-f", files[0], "up"])
            self.assertEqual(s.env()["PLUS_STACK"], "1")

    def test_stack_setup_failure_errors_the_stack_tests_and_never_starts_compose(self) -> None:
        from qa import stack
        with mock.patch.dict(os.environ, {"PLUS_FAIL": "1"}), \
             mock.patch.object(run, "preflight", return_value=({}, [])), \
             mock.patch.object(stack.subprocess, "run") as sp:
            result = run.run(self.cfg, self.managed_plan(), self.rows, self.out, stack_mode="managed", build=True, retry=False)
        test = result["tests"][0]
        self.assertEqual(test["outcome"], "error")
        self.assertIn("stack setup:", test["details"])
        self.assertIn("qa_hooks.py", test["details"])
        self.assertFalse([c for c in sp.call_args_list if "up" in c.args[0]])
        self.assertFalse(sp.called)

    def test_managed_stack_password_is_accepted_and_redacted(self) -> None:
        import re
        from qa import stack
        validator = re.compile(r"^(?=.*?[A-Z])(?=.*?[a-z])(?=.*?[0-9])(?=.*?[#?!@$%^&*-]).{8,}$")
        first, second = stack.run_credentials({}), stack.run_credentials({})
        self.assertNotEqual(first["PIPESHUB_TEST_USER_PASSWORD"], second["PIPESHUB_TEST_USER_PASSWORD"])
        self.assertEqual(first["PIPESHUB_TEST_USER_EMAIL"], "qa-admin@example.com")
        for creds in (first, second):
            pw = creds["PIPESHUB_TEST_USER_PASSWORD"]
            self.assertRegex(pw, validator)
            self.assertLessEqual(len(pw.encode("utf-8")), 72)
            with mock.patch.dict(os.environ, {"PIPESHUB_TEST_USER_PASSWORD": pw}):
                self.assertNotIn(pw, report.redact(f"pw={pw}"))
        self.assertEqual(stack.run_credentials({"PIPESHUB_TEST_USER_EMAIL": "a@b.test"})["PIPESHUB_TEST_USER_EMAIL"], "a@b.test")

    def test_built_image_is_removed_once_after_the_last_graph(self) -> None:
        def go(env):
            calls = []
            with mock.patch.dict(os.environ, env), \
                 mock.patch.object(run, "preflight", return_value=({}, [])), \
                 mock.patch.object(run.Stack, "start", lambda s: calls.append(("start", s.graph_db))), \
                 mock.patch.object(run.Stack, "stop", lambda s: calls.append(("stop", s.graph_db))), \
                 mock.patch.object(run.Stack, "collect_logs", lambda s: None), \
                 mock.patch.object(run, "_execute", return_value={"exit": 0, "label": "x"}), \
                 mock.patch.object(run, "_outcomes", return_value={ACL: {"outcome": "pass", "seconds": 1, "details": ""}}), \
                 mock.patch("qa.run.subprocess.run", side_effect=lambda cmd, **kw: calls.append(("sh", cmd))):
                run.run(self.cfg, self.managed_plan([ACL]), self.rows, self.out, stack_mode="managed", build=True, retry=False)
            return calls

        calls = go({"QA_KEEP_IMAGE": "0"})
        removals = [c for c in calls if c[0] == "sh" and c[1][:3] == ["docker", "image", "rm"]]
        self.assertEqual(removals, [("sh", ["docker", "image", "rm", "pipeshub-ai:qa-0123456789ab"])])
        self.assertEqual(calls[-1], removals[0])
        self.assertEqual([c for c in calls if c[0] == "stop"], [("stop", "neo4j"), ("stop", "arango")])
        self.assertFalse([c for c in go({"QA_KEEP_IMAGE": "1"}) if c[0] == "sh"])


class TestHealth(unittest.TestCase):
    def test_a_200_that_is_still_starting_is_not_healthy(self) -> None:
        import importlib.util
        spec = importlib.util.spec_from_file_location("qa_health_under_test", Path(__file__).resolve().parents[2] / "integration-tests/stack/health.py")
        health = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(health)

        def reply(body: bytes):
            resp = mock.MagicMock(status=200)
            resp.read.return_value = body
            resp.__enter__.return_value = resp
            return resp

        with mock.patch.object(health.urllib.request, "urlopen", return_value=reply(b'{"status": "unhealthy"}')):
            self.assertFalse(health.ok("http://x/api/v1/health"))
        with mock.patch.object(health.urllib.request, "urlopen", return_value=reply(b'{"status": "healthy"}')):
            self.assertTrue(health.ok("http://x/api/v1/health"))


class TestPreflight(RunCase):
    def test_missing_secret_is_not_run_not_failed(self) -> None:
        invs, _ = run.resolve(self.cfg, plan_for([JIRA_SYNC]), self.rows, self.out)
        with mock.patch.object(run, "_probe", return_value=True), mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("JIRA_TOKEN", None)
            not_run, _ = run.preflight(self.cfg, invs, self.rows, stack_mode="external")
        self.assertEqual(not_run[JIRA_SYNC], ("secret", "missing env JIRA_TOKEN"))

    def test_any_one_model_key_is_enough(self) -> None:
        rows = {k: dict(v) for k, v in self.rows.items()}
        rows[JIRA_SYNC]["needs"] = ["env:TEST_OPENAI_API_KEY|TEST_AZURE_OPENAI_API_KEY+TEST_AZURE_OPENAI_ENDPOINT"]
        invs, _ = run.resolve(self.cfg, plan_for([JIRA_SYNC]), rows, self.out)
        keys = ("TEST_OPENAI_API_KEY", "TEST_AZURE_OPENAI_API_KEY", "TEST_AZURE_OPENAI_ENDPOINT", "PIPESHUB_REQUIRE_CONNECTOR_SECRETS")

        def preflight(**env: str) -> dict[str, tuple[str, str]]:
            with mock.patch.object(run, "_probe", return_value=True), mock.patch.dict(os.environ, env):
                for k in keys:
                    if k not in env:
                        os.environ.pop(k, None)
                return run.preflight(self.cfg, invs, rows, stack_mode="external")[0]

        self.assertEqual(preflight()[JIRA_SYNC][1], "missing env one of TEST_OPENAI_API_KEY | TEST_AZURE_OPENAI_API_KEY+TEST_AZURE_OPENAI_ENDPOINT")
        self.assertIn(JIRA_SYNC, preflight(TEST_AZURE_OPENAI_API_KEY="k"))
        self.assertNotIn(JIRA_SYNC, preflight(TEST_AZURE_OPENAI_API_KEY="k", TEST_AZURE_OPENAI_ENDPOINT="https://x"))
        self.assertNotIn(JIRA_SYNC, preflight(TEST_OPENAI_API_KEY="k"))

    def test_overlapping_key_needs_name_only_the_stricter(self) -> None:
        rows = {k: dict(v) for k, v in self.rows.items()}
        rows[JIRA_SYNC]["needs"] = ["env:TEST_OPENAI_API_KEY|TEST_GROQ_API_KEY", "env:TEST_OPENAI_API_KEY", "env:JIRA_TOKEN"]
        invs, _ = run.resolve(self.cfg, plan_for([JIRA_SYNC]), rows, self.out)
        with mock.patch.object(run, "_probe", return_value=True), mock.patch.dict(os.environ, {}):
            for k in ("TEST_OPENAI_API_KEY", "TEST_GROQ_API_KEY", "JIRA_TOKEN", "PIPESHUB_REQUIRE_CONNECTOR_SECRETS"):
                os.environ.pop(k, None)
            not_run, _ = run.preflight(self.cfg, invs, rows, stack_mode="external")
        self.assertEqual(not_run[JIRA_SYNC], ("secret", "missing env TEST_OPENAI_API_KEY, JIRA_TOKEN"))

    def test_stack_tests_without_a_stack_are_not_run(self) -> None:
        result = run.run(self.cfg, plan_for([ACL]), self.rows, self.out, stack_mode="none")
        self.assertEqual(result["tests"][0]["outcome"], "not_run")
        self.assertIn("needs the integration stack", result["tests"][0]["details"])
        self.assertEqual(result["tests"][0]["kind"], "scope")

    def test_preflight_names_the_kind(self) -> None:
        invs, _ = run.resolve(self.cfg, plan_for([JIRA_SYNC]), self.rows, self.out)
        with mock.patch.dict(os.environ, {"QA_PYTHON": "/bin/false", "QA_IT_PYTHON": "/bin/false"}):
            invs, _ = run.resolve(self.cfg, plan_for([JIRA_SYNC]), self.rows, self.out)
            not_run, _ = run.preflight(self.cfg, invs, self.rows, stack_mode="external")
        self.assertEqual(not_run[JIRA_SYNC][0], "toolchain")


class TestRetry(RunCase):
    def test_ordered_module_failure_is_not_retried(self) -> None:
        invs, _ = run.resolve(self.cfg, plan_for([JIRA_SYNC, ACL]), self.rows, self.out)
        self.assertEqual([i.ids for i in invs], [[JIRA_SYNC], [ACL]])
        seen = []

        def outcomes(i, rows, code):
            seen.append(list(i.ids))
            bad = "-retry" not in i.label
            return {t: {"outcome": "fail" if bad else "pass", "seconds": 1.0, "details": "x"} for t in i.ids}

        results: dict = {}
        with mock.patch.object(run, "_execute", return_value={"exit": 1, "label": "x"}), \
             mock.patch.object(run, "_outcomes", side_effect=outcomes):
            for inv in invs:
                run._run_one(self.cfg, inv, self.rows, self.out, {}, {}, results, [], True)
        self.assertEqual(seen, [[JIRA_SYNC], [ACL], [ACL]])
        self.assertEqual(results[JIRA_SYNC]["outcome"], "fail")
        self.assertEqual(results[ACL]["outcome"], "flaky")


class TestExecute(RunCase):
    """Runs real pytest when it is importable; the engine itself needs only stdlib."""

    def setUp(self) -> None:
        super().setUp()
        try:
            import pytest  # noqa: F401
        except ImportError:
            self.skipTest("pytest not installed")

    def test_pass_and_fail_map_back_to_files(self) -> None:
        testing.write(self.root, "backend/python/tests/unit/test_broken.py", "def test_no():\n    assert False, 'boom'\n")
        testing.commit(self.root, "broken")
        rows = catalog.index(catalog.build(config.load(self.root)))
        ids = ["py:tests/unit/test_broken.py", "py:tests/unit/services/test_retrieval.py"]
        with mock.patch.dict(os.environ, {"QA_PYTHON": sys.executable, "PYTHONPATH": str(self.root / "backend/python")}):
            result = run.run(self.cfg, plan_for(ids, needed=False), rows, self.out, retry=False)
        got = {t["id"]: t for t in result["tests"]}
        self.assertEqual(got["py:tests/unit/services/test_retrieval.py"]["outcome"], "pass")
        self.assertEqual(got["py:tests/unit/test_broken.py"]["outcome"], "fail")
        self.assertIn("boom", got["py:tests/unit/test_broken.py"]["details"])

    def test_collection_error_is_error_not_fail(self) -> None:
        testing.write(self.root, "backend/python/tests/unit/test_syntax.py", "def test_x(:\n    pass\n")
        testing.commit(self.root, "syntax error")
        rows = catalog.index(catalog.build(config.load(self.root)))
        with mock.patch.dict(os.environ, {"QA_PYTHON": sys.executable, "PYTHONPATH": str(self.root / "backend/python")}):
            result = run.run(self.cfg, plan_for(["py:tests/unit/test_syntax.py"], needed=False), rows, self.out, retry=False)
        self.assertEqual(result["tests"][0]["outcome"], "error")

    def test_pass_on_retry_is_flaky(self) -> None:
        marker = self.out / "seen"
        testing.write(self.root, "backend/python/tests/unit/test_flaky.py", textwrap.dedent(f"""\
            import os
            def test_once():
                if not os.path.exists({str(marker)!r}):
                    open({str(marker)!r}, "w").close()
                    assert False, "first run fails"
            """))
        testing.commit(self.root, "flaky")
        rows = catalog.index(catalog.build(config.load(self.root)))
        with mock.patch.dict(os.environ, {"QA_PYTHON": sys.executable, "PYTHONPATH": str(self.root / "backend/python")}):
            result = run.run(self.cfg, plan_for(["py:tests/unit/test_flaky.py"], needed=False), rows, self.out)
        self.assertEqual(result["tests"][0]["outcome"], "flaky")


class TestMissingJunit(unittest.TestCase):
    def test_no_junit_file_is_named_not_called_a_collection_error(self) -> None:
        inv = run.Invocation("py-unit", "py-unit", "pytest", "backend/python", ["pytest"], ["py:tests/unit/test_a.py"], Path("/nonexistent/junit.xml"))
        got = run._outcomes(inv, {}, 1)["py:tests/unit/test_a.py"]
        self.assertEqual(got["outcome"], "error")
        self.assertIn("wrote no JUnit file", got["details"])


class TestPlaywrightRuns(unittest.TestCase):
    JUNIT = textwrap.dedent("""\
        <testsuites>
        <testsuite name="setup/auth.setup.ts"><testcase name="authenticate @smoke" classname="setup/auth.setup.ts">
        <error message="TEST_USER_EMAIL and TEST_USER_PASSWORD must be set. Copy .env.test.example">x</error></testcase></testsuite>
        <testsuite name="chat/chat-streaming.spec.ts"><testcase name="streams" classname="chat/chat-streaming.spec.ts"><skipped/></testcase></testsuite>
        <testsuite name="chat/chat-basic.spec.ts"><testcase name="sends" classname="chat/chat-basic.spec.ts"/></testsuite>
        <testsuite name="setup/ai-models.cleanup.ts"><testcase name="cleans" classname="setup/ai-models.cleanup.ts"><error message="boom">x</error></testcase></testsuite>
        </testsuites>""")
    IDS = ["e2e:tests/e2e/chat/chat-streaming.spec.ts", "e2e:tests/e2e/chat/chat-basic.spec.ts"]

    def outcomes(self, junit_text: str) -> dict:
        with tempfile.TemporaryDirectory() as d:
            x = Path(d) / "junit-e2e.xml"
            x.write_text(junit_text, encoding="utf-8")
            inv = run.Invocation("e2e", "e2e", "playwright", "frontend", ["npx"], self.IDS, x)
            rows = {i: {"path": "frontend/" + i.split(":", 1)[1]} for i in self.IDS}
            return run._outcomes(inv, rows, 1)

    def test_specs_skipped_after_a_failed_setup_are_errors(self) -> None:
        got = self.outcomes(self.JUNIT)
        streaming = got["e2e:tests/e2e/chat/chat-streaming.spec.ts"]
        self.assertEqual(streaming["outcome"], "error")
        self.assertEqual(streaming["details"], "setup failed: authenticate @smoke: TEST_USER_EMAIL and TEST_USER_PASSWORD must be set. "
                                               "Copy .env.test.example")
        self.assertEqual(got["e2e:tests/e2e/chat/chat-basic.spec.ts"]["outcome"], "pass")

    def test_a_skip_for_its_own_reason_stays_a_skip(self) -> None:
        passed = self.JUNIT.replace('<error message="TEST_USER_EMAIL and TEST_USER_PASSWORD must be set. Copy .env.test.example">x</error>', "")
        # A failed cleanup is not a setup.
        self.assertEqual(self.outcomes(passed)["e2e:tests/e2e/chat/chat-streaming.spec.ts"]["outcome"], "skip")

    def test_a_managed_stack_gives_playwright_its_credentials_and_url(self) -> None:
        seen = {}

        def fake(cmd, **kw):
            seen.update(kw["env"])
            return mock.Mock(returncode=0)

        with tempfile.TemporaryDirectory() as d, mock.patch.object(run.subprocess, "run", fake):
            cfg = mock.Mock(root=Path(d))
            (Path(d) / "frontend").mkdir()
            stack = {"PIPESHUB_TEST_USER_EMAIL": "qa@example.com", "PIPESHUB_TEST_USER_PASSWORD": "Qa1!pw",
                     "PIPESHUB_BASE_URL": "http://localhost:3000", "TEST_USER_EMAIL": ""}
            inv = run.Invocation("e2e", "e2e", "playwright", "frontend", ["npx"], [], None)
            with mock.patch.dict(os.environ, {"BASE_URL": "http://set.by.caller"}):
                run._execute(cfg, inv, Path(d) / "logs", stack)
            self.assertEqual((seen["TEST_USER_EMAIL"], seen["TEST_USER_PASSWORD"]), ("qa@example.com", "Qa1!pw"))
            self.assertEqual((seen["BASE_URL"], seen["NEXT_PUBLIC_API_BASE_URL"]), ("http://set.by.caller", "http://localhost:3000"))
            seen.clear()
            run._execute(cfg, run.Invocation("u", "u", "pytest", "frontend", ["x"], [], None), Path(d) / "logs", stack)
            self.assertEqual(seen.get("TEST_USER_EMAIL"), "")


class TestJunitMapping(unittest.TestCase):
    def test_pytest_classnames_map_to_files_and_tests(self) -> None:
        modules = junit.module_names(["connectors/jira/test_jira.py"])
        case = junit.Case("s", "connectors.jira.test_jira.TestJira", "test_tc_sync_001_full_sync[neo4j]", None, "pass", 1.0, "")
        self.assertEqual(junit.pytest_key(case, modules), ("connectors/jira/test_jira.py", "TestJira::test_tc_sync_001_full_sync"))

    def test_collection_error_maps_to_its_own_file(self) -> None:
        modules = junit.module_names(["unit/test_cross_store.py"])
        case = junit.Case("s", "", "unit.test_cross_store", None, "error", 0.0, "collection failure")
        self.assertEqual(junit.pytest_key(case, modules), ("unit/test_cross_store.py", "<collection>"))

    def test_mocha_failures_keep_the_assertion_and_name_the_unloadable_spec(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            x = Path(d) / "junit.xml"
            x.write_text(textwrap.dedent("""\
                <testsuite name="Mocha Tests">
                  <testcase classname="cfg" name="saves" file="/r/apps/tests/a.test.ts"><failure>expected 1 to equal 2
                AssertionError: expected 1 to equal 2
                    at Context.it (tests/a.test.ts:9:5)</failure></testcase>
                  <testcase classname="" name="Uncaught error outside test suite" file="undefined"><failure>Cannot find module '../x'
                Require stack:
                - /r/apps/tests/b.test.ts
                - /r/apps/node_modules/mocha/lib/nodejs/file-unloader.js</failure></testcase>
                </testsuite>"""), encoding="utf-8")
            a, b = junit.read(x)
        self.assertEqual(a.message, "expected 1 to equal 2")
        self.assertTrue(junit.file_match(b, "tests/b.test.ts"))
        self.assertEqual(b.message, "Cannot find module '../x'")

    def test_worst_outcome_wins(self) -> None:
        self.assertEqual(junit.worst(["pass", "skip", "fail"]), "fail")
        self.assertEqual(junit.worst(["skip", "skip"]), "skip")

    def test_worst_ranks_flaky_above_pass(self) -> None:
        self.assertEqual(junit.worst(["pass", "flaky"]), "flaky")


class TestReportGate(RunCase):
    def _nr(self, *kinds: str) -> dict:
        return {"tests": [{"id": k, "outcome": "not_run", "kind": k} for k in kinds]}

    def test_secret_and_scope_not_run_do_not_block(self) -> None:
        results = {"tests": [
            {"id": "a", "outcome": "not_run", "kind": "secret"},
            {"id": "b", "outcome": "not_run", "kind": "scope"},
            {"id": "c", "outcome": "flaky"},
        ]}
        self.assertEqual(report.verdict(self.cfg, results)[0], "pass")
        results["tests"].append({"id": "d", "outcome": "fail"})
        self.assertEqual(report.verdict(self.cfg, results), ("fail", ["d"]))

    def test_infra_toolchain_cancelled_and_missing_results_block(self) -> None:
        for kind in ("infra", "toolchain", "cancelled", "no_result"):
            self.assertEqual(report.verdict(self.cfg, self._nr(kind)), ("fail", [kind]))

    def test_not_run_without_a_kind_blocks(self) -> None:
        self.assertEqual(report.verdict(self.cfg, {"tests": [{"id": "a", "outcome": "not_run"}]}), ("fail", ["a"]))

    def test_fatal_blocks_without_strict(self) -> None:
        self.assertEqual(report.verdict(self.cfg, {"tests": [], "fatal": ["docker is not available"]}), ("fail", ["infra"]))

    def test_ci_gate_blocks_scope(self) -> None:
        self.assertEqual(report.verdict(self.cfg, self._nr("scope"), allowed=frozenset({"secret", "profile"}))[0], "fail")

    def test_strict_blocks_every_not_run(self) -> None:
        self.assertEqual(report.verdict(self.cfg, self._nr("secret"), allowed=frozenset())[0], "fail")

    def test_quarantined_not_run_does_not_block(self) -> None:
        quarantined = "q"
        results = {"tests": [{"id": quarantined, "outcome": "not_run", "kind": "infra"}]}
        with mock.patch.object(self.cfg, "quarantined_ids", return_value={quarantined}):
            self.assertEqual(report.verdict(self.cfg, results)[0], "pass")

    def _auto_file(self, text: str) -> str:
        path = self.out / "quarantine.json"
        path.write_text(text, encoding="utf-8")
        return str(path)

    def test_auto_file_quarantines_in_config_and_report(self) -> None:
        import datetime as dt
        today = dt.date.today()
        active = {"id": ACL, "owner": "qa-auto", "issue": "https://github.com/o/r/issues/7", "auto": True,
                  "added": today.isoformat(), "expires": (today + dt.timedelta(days=3)).isoformat()}
        expired = {"id": JIRA_SYNC, "owner": "qa-auto", "issue": "https://x/issues/8", "auto": True,
                   "expires": (today - dt.timedelta(days=2)).isoformat(), "expired": True}
        auto = self._auto_file(json.dumps({"tests": [active, expired]}))
        with mock.patch.dict(os.environ, {"QA_QUARANTINE_AUTO": auto}):
            cfg = config.load(self.root)
        self.assertEqual(cfg.quarantined_ids(), {ACL})
        results = {"tests": [{"id": ACL, "outcome": "fail", "details": "boom"}, {"id": JIRA_SYNC, "outcome": "pass"}]}
        self.assertEqual(report.verdict(cfg, results), ("pass", []))
        body = report.render(cfg, plan_for([ACL, JIRA_SYNC]), results)
        section = body.split("### Quarantined (ran, not blocking)\n\n", 1)[1]
        self.assertEqual(section.splitlines()[0], f"- `{ACL}`: fail (https://github.com/o/r/issues/7)")
        self.assertNotIn(JIRA_SYNC, section.split("\n\n")[0])
        # An entry without an issue link shows none, not "(None)".
        cfg.quarantine.append({"id": JIRA_SYNC, "owner": "qa-auto", "auto": True,
                               "expires": (today + dt.timedelta(days=3)).isoformat()})
        body = report.render(cfg, plan_for([ACL, JIRA_SYNC]), results)
        section = body.split("### Quarantined (ran, not blocking)\n\n", 1)[1].split("\n\n")[0]
        self.assertIn(f"- `{JIRA_SYNC}`: pass", section.splitlines())
        self.assertNotIn("None", section)
        cfg.quarantine.pop()
        # An auto entry past expiry is not a lint error; it is simply no longer active.
        cfg.quarantine.append({**active, "expires": (today - dt.timedelta(days=1)).isoformat()})
        errors, _ = catalog.lint(cfg, catalog.build(cfg))
        self.assertFalse([e for e in errors if "quarantine" in e], errors)

    def test_auto_quarantine_never_changes_the_committed_catalog(self) -> None:
        import datetime as dt
        rows = catalog.build(self.cfg)
        target = next(r["id"] for r in rows if r.get("status") != "planned")
        expires = (dt.date.today() + dt.timedelta(days=3)).isoformat()
        auto = self._auto_file(json.dumps({"tests": [{"id": target, "owner": "qa-auto", "issue": "https://github.com/o/r/issues/1",
                                                          "added": dt.date.today().isoformat(), "expires": expires, "auto": True}]}))
        with mock.patch.dict(os.environ, {"QA_QUARANTINE_AUTO": auto}):
            cfg = config.load(self.root)
        self.assertIn(target, cfg.quarantined_ids())
        self.assertEqual(catalog.build(cfg), rows)
        self.assertEqual(catalog.stale(cfg), catalog.stale(self.cfg))

    def test_unreadable_auto_file_is_ignored(self) -> None:
        auto = self._auto_file("not json")
        err = io.StringIO()
        with mock.patch.dict(os.environ, {"QA_QUARANTINE_AUTO": auto}), contextlib.redirect_stderr(err):
            cfg = config.load(self.root)
        self.assertEqual(cfg.quarantine, self.cfg.quarantine)
        self.assertIn("QA_QUARANTINE_AUTO", err.getvalue())

    def test_merged_results_prefer_executed_outcomes(self) -> None:
        merged = report.merge_results([
            {"tests": [{"id": "a", "outcome": "not_run"}, {"id": "b", "outcome": "pass"}]},
            {"tests": [{"id": "a", "outcome": "fail"}]},
        ])
        self.assertEqual({t["id"]: t["outcome"] for t in merged["tests"]}, {"a": "fail", "b": "pass"})

    def test_a_planned_test_with_no_result_is_reported_not_run(self) -> None:
        merged = report.merge_results([{"tests": [{"id": "a", "outcome": "pass"}]}], ["a", "b"])
        self.assertEqual({t["id"]: t["outcome"] for t in merged["tests"]}, {"a": "pass", "b": "not_run"})
        self.assertEqual(next(t for t in merged["tests"] if t["id"] == "b")["kind"], "no_result")

    def test_stack_parts_nested_in_the_download_are_merged(self) -> None:
        # A stack artifact that also uploads files from outside qa-out lands under qa-out/qa-out/.
        out = self.out / "qa-out"
        (out / "qa-out").mkdir(parents=True)
        (out / "plan.json").write_text(json.dumps({"tests": [{"id": "e"}, {"id": "u"}]}), encoding="utf-8")
        (out / "results-unit.json").write_text(json.dumps({"tests": [
            {"id": "u", "outcome": "pass"},
            {"id": "e", "outcome": "not_run", "kind": "scope", "details": "outside --scope unit"}]}), encoding="utf-8")
        (out / "qa-out" / "results-stack-neo4j-1of1.json").write_text(json.dumps({"tests": [
            {"id": "e", "outcome": "skip"},
            {"id": "u", "outcome": "not_run", "kind": "scope", "details": "outside --scope stack"}]}), encoding="utf-8")
        with mock.patch.dict(os.environ, {"QA_JOB_RESULTS": "unit=success,stack=success"}):
            _, merged = qa_main._merged(out)
        self.assertEqual({t["id"]: t["outcome"] for t in merged["tests"]}, {"e": "skip", "u": "pass"})

    def post(self, env: dict, older: str = "", fail_minimize: bool = False, body: str = "<!-- qa-engine:oss -->\n## QA\nbody") -> list:
        import subprocess
        calls = []

        def fake(cmd, **kw):
            calls.append((cmd, json.loads(kw["input"]) if kw.get("input") else None))
            if "graphql" in cmd and fail_minimize:
                raise subprocess.CalledProcessError(1, cmd, stderr="forbidden")
            out = older if "--paginate" in cmd else '{"html_url": "u"}'
            return subprocess.CompletedProcess(cmd, 0, out, "")

        with mock.patch.object(report.subprocess, "run", fake), mock.patch.dict(os.environ, env, clear=False), \
                contextlib.redirect_stderr(io.StringIO()) as err:
            for k in ("QA_COMMENT_RUN",):
                if k not in env:
                    os.environ.pop(k, None)
            self.assertEqual(report.post_comment("o/r", 1, body, "oss"), "u")
        self.err = err.getvalue()
        return calls

    def test_the_per_push_comment_is_one_sticky_comment_of_the_bot(self) -> None:
        calls = self.post({}, older="55")
        self.assertIn('select(.user.login == "github-actions[bot]" and (.body | startswith("<!-- qa-engine:oss -->")) '
                      'and (.body | contains("<!-- qa-run:") | not))', " ".join(calls[0][0]))
        self.assertEqual(calls[1][0][:4], ["gh", "api", "-X", "PATCH"])
        self.assertIn("comments/55", calls[1][0][4])

    def test_each_qa_run_posts_a_new_comment_and_collapses_older_runs(self) -> None:
        env = {"QA_COMMENT_RUN": "42", "QA_COMMENT_COMMAND": "/qa run", "QA_COMMENT_ACTOR": "alice",
               "QA_COMMENT_HEAD": "a" * 40, "QA_COMMENT_URL": "https://github.com/o/r/actions/runs/42"}
        calls = self.post(env, older="NODE1\nNODE2")
        self.assertEqual(calls[0][0][:4], ["gh", "api", "-X", "POST"])
        body = calls[0][1]["body"]
        self.assertTrue(body.startswith("<!-- qa-engine:oss -->\n<!-- qa-run:42 -->\n"), body)
        self.assertIn("`/qa run` requested by alice at `aaaaaaaaaaaa`. [Run](https://github.com/o/r/actions/runs/42)", body)
        self.assertNotIn("@", body)
        self.assertIn('(.body | contains("<!-- qa-run:42 -->") | not)', " ".join(calls[1][0]))
        minimized = [c[0] for c in calls if "graphql" in c[0]]
        self.assertEqual([c[-1] for c in minimized], ["id=NODE1", "id=NODE2"])
        self.assertIn("classifier: OUTDATED", minimized[0][4])

    def test_a_failed_collapse_is_only_a_warning(self) -> None:
        self.post({"QA_COMMENT_RUN": "42", "QA_COMMENT_ACTOR": "@x y"}, older="NODE1", fail_minimize=True)
        self.assertIn("::warning::could not collapse older QA comments", self.err)

    def test_every_failure_is_listed_by_suite_or_counted_never_dropped(self) -> None:
        tests = ([{"id": f"it:x/test_{i}.py::t", "outcome": "error", "details": "boom " * 30} for i in range(3000)]
                 + [{"id": f"py:tests/unit/test_{i}.py", "outcome": "fail", "details": "assert"} for i in range(30)]
                 + [{"id": "node:tests/a.test.ts", "outcome": "fail", "details": "x"}])
        plan = {**plan_for([t["id"] for t in tests]), "edition": "oss"}
        plan["tests"] = [{**t, "module": "graph-db" if t["id"].startswith("py") else None} for t in plan["tests"]]
        body = report.render(self.cfg, plan, {"tests": tests})
        self.assertLessEqual(len(body), report.MAX_BODY)
        self.assertIn("it: 3000, py: 30, node: 1", body)
        self.assertIn("#### py (30)", body)
        self.assertIn("in it (see results.json)", body)
        self.assertIn("- `node:tests/a.test.ts`", body)
        self.assertIn("| graph-db | 30 | 30 | 0 |", body)
        self.assertIn("| (no module) | 3001 | 3001 | 0 |", body)

    def test_a_comment_never_exceeds_the_limit(self) -> None:
        with mock.patch.dict(os.environ, {"QA_COMMENT_RUN": "42"}):
            calls = self.post({"QA_COMMENT_RUN": "42"}, body="<!-- qa-engine:oss -->\n" + "x" * 70000)
        body = calls[0][1]["body"]
        self.assertLessEqual(len(body), report.MAX_COMMENT)
        self.assertTrue(body.endswith(report.TRUNCATED))

    def test_quarantined_and_not_run_lists_are_capped_with_a_count(self) -> None:
        tests = [{"id": f"it:x/t{i}.py::t", "outcome": "not_run", "kind": "scope", "details": f"reason {i}"} for i in range(40)]
        ids = [t["id"] for t in tests]
        cfg = mock.Mock(wraps=self.cfg, flows=self.cfg.flows, quarantine=[{"id": i, "issue": "x"} for i in ids],
                        quarantined_ids=mock.Mock(return_value=set(ids)))
        cfg.flow = self.cfg.flow
        plan = {**plan_for(ids), "edition": "oss"}
        body = report.render(cfg, plan, {"tests": tests})
        self.assertIn("- … and 20 more (see results.json)", body)
        self.assertIn("| … | and 15 more reasons (see results.json) | 15 |", body)

    def test_a_cancelled_stack_job_is_named_and_still_blocks(self) -> None:
        unit = {"tests": [{"id": "u", "outcome": "pass"},
                          {"id": "s", "outcome": "not_run", "kind": "scope", "details": "outside --scope unit"}]}
        # Locally (no job results) the deferral stays a scope not_run.
        self.assertEqual(report.merge_results([unit], ["u", "s"])["tests"][0]["kind"], "scope")
        merged = report.merge_results([unit], ["u", "s"], report.job_results("unit=success,stack=cancelled"))
        s = merged["tests"][0]
        self.assertEqual((s["outcome"], s["kind"], s["details"]), ("not_run", "cancelled", "the stack test job was cancelled"))
        verdict, blocking = report.verdict(self.cfg, merged, frozenset({"secret", "profile"}))
        self.assertEqual((verdict, blocking), ("fail", ["s"]))
        failed = report.merge_results([unit], ["u", "s"], {"stack": "failure"})["tests"][0]
        self.assertEqual((failed["kind"], failed["details"]), ("no_result", "the stack test job did not report it (failure)"))
        # A stack part that ran the test wins as before.
        ran = report.merge_results([unit, {"tests": [{"id": "s", "outcome": "pass"}]}], ["u", "s"], {"stack": "success"})
        self.assertEqual(ran["tests"][0]["outcome"], "pass")

    def test_report_names_planner_fallback(self) -> None:
        plan = plan_for([ACL])
        plan["planner"] = {"adapter": "claude_code", "status": "failed", "error": "timeout"}
        body = report.render(self.cfg, plan, None)
        self.assertIn("the floor alone ran", body)
        self.assertTrue(body.startswith("<!-- qa-engine:all -->"))

    def _flow_plan(self):
        plan = plan_for([ACL, "it:retrieval/test_search.py::test_ranking", "e2e:frontend/tests/e2e/login.spec.ts"])
        for t, f in zip(plan["tests"], (["search"], ["search"], ["login"])):
            t["flows"] = f
        return plan

    def test_report_groups_results_by_flow(self) -> None:
        results = {"tests": [
            {"id": ACL, "outcome": "fail"},
            {"id": "it:retrieval/test_search.py::test_ranking", "outcome": "pass"},
            {"id": "e2e:frontend/tests/e2e/login.spec.ts", "outcome": "pass"},
        ]}
        body = report.render(self.cfg, self._flow_plan(), results)
        self.assertIn("| search | P0 | 2 | 1 | 0 |", body)
        self.assertIn("| login | P1 | 1 | 0 | 0 |", body)
        self.assertLess(body.index("| search | P0"), body.index("| login | P1"))

    def test_report_without_flows_still_renders(self) -> None:
        body = report.render(self.cfg, plan_for([ACL, "it:retrieval/test_search.py::test_ranking"]), None)
        self.assertIn("| (no flow) |  | 2 |  |  |", body)

    def test_report_lists_affected_flows_without_tests(self) -> None:
        plan = plan_for([ACL])
        plan["flows"] = {"login": "trigger x"}
        self.assertIn("Affected flows with no planned test: login", report.render(self.cfg, plan, None))

    def test_report_redacts_secret_values(self) -> None:
        plan = plan_for([ACL])
        results = {"tests": [{"id": "t", "outcome": "fail", "details": "bad key hunter2hunter2"}]}
        with mock.patch.dict(os.environ, {"S3_SECRET_KEY": "hunter2hunter2"}):
            body = report.render(self.cfg, plan, results)
        self.assertNotIn("hunter2hunter2", body)
        self.assertIn("***", body)

    def test_short_and_non_secret_values_are_kept(self) -> None:
        self.assertEqual(report.redact("port 445 key abc", {"SMB_PORT": "445", "API_KEY": "abc"}), "port 445 key abc")

    def test_longer_secret_is_replaced_first(self) -> None:
        env = {"A_TOKEN": "secret1", "B_TOKEN": "secret1-extra"}
        self.assertEqual(report.redact("x secret1-extra y", env), "x *** y")

    def test_a_secret_with_markdown_characters_is_hidden_whole(self) -> None:
        secret = "ab|cd`ef" + "x" * 300
        results = {"tests": [{"id": "t", "outcome": "fail", "details": f"bad {secret}"}]}
        with mock.patch.dict(os.environ, {"API_TOKEN": secret}):
            body = report.render(self.cfg, plan_for([ACL]), results)
        self.assertNotIn("ab|cd", body)
        self.assertNotIn("ab\\|cd", body)
        self.assertNotIn("xxxxxx", body)

    def test_run_redacts_results_and_the_files_it_uploads(self) -> None:
        secret = "hunter2hunter2"
        (self.out / "catalog-rows.json").write_text("[]", encoding="utf-8")
        plan_path = self.out / "plan.json"
        plan_path.write_text(json.dumps(plan_for([ACL])), encoding="utf-8")

        def fake_run(cfg, plan, rows, out, **kw):
            (out / "logs").mkdir(exist_ok=True)
            (out / "logs" / "stack.log").write_text(f"key={secret}\n", encoding="utf-8")
            (out / "junit-a.xml").write_text(f"<failure>{secret}</failure>", encoding="utf-8")
            return {"tests": [{"id": ACL, "outcome": "fail", "details": f"got {secret}"}], "fatal": [f"boom {secret}"], "seconds": 1}

        with mock.patch.dict(os.environ, {"S3_SECRET_KEY": secret, "QA_ROOT": str(self.root)}), \
                mock.patch.object(run, "run", fake_run):
            qa_main.main(["--out", str(self.out), "run", "--plan", str(plan_path), "--scope", "unit"])
        for name in ("results-unit.json", "logs/stack.log", "junit-a.xml"):
            self.assertNotIn(secret, (self.out / name).read_text(encoding="utf-8"), name)
        results = json.loads((self.out / "results-unit.json").read_text(encoding="utf-8"))
        with mock.patch.dict(os.environ, {}, clear=True):
            body = report.render(self.cfg, plan_for([ACL]), results)
        self.assertNotIn(secret, body)

    def test_a_run_that_raises_still_redacts_its_logs(self) -> None:
        secret = "hunter2hunter2"
        (self.out / "catalog-rows.json").write_text("[]", encoding="utf-8")
        plan_path = self.out / "plan.json"
        plan_path.write_text(json.dumps(plan_for([ACL])), encoding="utf-8")

        def fake_run(cfg, plan, rows, out, **kw):
            (out / "logs").mkdir(exist_ok=True)
            (out / "logs" / "stack.log").write_text(f"key={secret}\n", encoding="utf-8")
            raise RuntimeError("stack failed to start")

        with mock.patch.dict(os.environ, {"S3_SECRET_KEY": secret, "QA_ROOT": str(self.root)}), \
                mock.patch.object(run, "run", fake_run), self.assertRaises(RuntimeError):
            qa_main.main(["--out", str(self.out), "run", "--plan", str(plan_path), "--scope", "stack"])
        self.assertNotIn(secret, (self.out / "logs" / "stack.log").read_text(encoding="utf-8"))

    def test_merge_lets_a_real_outcome_beat_a_scope_not_run(self) -> None:
        merged = report.merge_results([
            {"tests": [{"id": "a", "outcome": "not_run", "kind": "scope"}]},
            {"tests": [{"id": "a", "outcome": "skip"}]},
        ], ["a"])
        self.assertEqual(merged["tests"][0]["outcome"], "skip")

    def test_merge_keeps_the_blocking_not_run_kind(self) -> None:
        for parts in (["scope", "infra"], ["infra", "scope"]):
            merged = report.merge_results([{"tests": [{"id": "a", "outcome": "not_run", "kind": k}]} for k in parts], ["a"])
            self.assertEqual(merged["tests"][0]["kind"], "infra", parts)
        merged = report.merge_results([{"tests": [{"id": "a", "outcome": "not_run", "kind": k}]} for k in ("secret", "scope")])
        self.assertEqual(merged["tests"][0]["kind"], "secret")
        merged = report.merge_results([{"tests": [{"id": "a", "outcome": "not_run", "kind": k}]} for k in ("scope", "secret")])
        self.assertEqual(merged["tests"][0]["kind"], "secret")

    def test_merge_marks_a_test_no_part_reported_no_result(self) -> None:
        merged = report.merge_results([{"tests": []}, {"tests": []}], ["a"])
        self.assertEqual(merged["tests"][0]["kind"], "no_result")


class TestScope(RunCase):
    def test_tests_outside_the_scope_are_not_run_scope(self) -> None:
        result = run.run(self.cfg, plan_for([ACL]), self.rows, self.out, stack_mode="none", scope="unit")
        self.assertEqual([(t["id"], t["outcome"], t["kind"]) for t in result["tests"]], [(ACL, "not_run", "scope")])
        self.assertIn("outside --scope unit", result["tests"][0]["details"])


RANKING = "it:retrieval/test_search.py::test_ranking"


class TestShard(RunCase):
    def test_assign_is_longest_first_and_deterministic(self) -> None:
        from qa import shard
        got = shard.assign({"a": (10, ["a"]), "b": (7, ["b"]), "c": (5, ["c"]), "d": (3, ["d"])}, 2)
        self.assertEqual(got, [["a", "d"], ["b", "c"]])

    def test_every_stack_test_lands_in_exactly_one_shard(self) -> None:
        from qa import shard
        plan = plan_for(list(self.rows))
        stack = set(shard.stack_ids(self.cfg, plan, self.rows))
        self.assertIn("e2e:tests/e2e/login.spec.ts", stack)
        self.assertNotIn("py:tests/unit/test_patch_only.py", stack)
        for n in range(1, 6):
            parts = [shard.select(self.cfg, plan, self.rows, k, n) for k in range(1, n + 1)]
            self.assertEqual(set().union(*parts), stack, n)
            self.assertEqual(sum(len(p) for p in parts), len(stack), n)

    def test_a_connector_suite_and_a_file_never_split(self) -> None:
        from qa import shard
        rows = {
            "a": {"path": "x/test_a.py", "markers": ["integration", "jira"]},
            "b": {"path": "x/test_b.py", "markers": ["jira"]},
            "c": {"path": "y/test_c.py", "markers": ["integration"]},
            "d": {"path": "y/test_c.py", "markers": []},
            "e": {"path": "z/test_e.py", "markers": []},
        }
        g = shard.groups(rows, list(rows), {"jira": 2.0})
        self.assertEqual(g["jira"], (2.0, ["a", "b"]))
        self.assertEqual(g["y/test_c.py"], (shard.DEFAULT_MINUTES, ["c", "d"]))
        for n in range(1, 5):
            where = {i: k for k, ids in enumerate(shard.assign(g, n)) for i in ids}
            self.assertEqual(where["a"], where["b"])
            self.assertEqual(where["c"], where["d"])

    def test_an_ordered_file_with_mixed_markers_is_one_group(self) -> None:
        from qa import shard
        rows = {
            "f1": {"path": "x/test_life.py", "markers": ["jira"], "ordered": True},
            "f2": {"path": "x/test_life.py", "markers": ["confluence"], "ordered": True},
            "f3": {"path": "x/test_life.py", "markers": ["jira", "confluence"], "ordered": True},
            "j": {"path": "x/test_jira.py", "markers": ["jira"]},
        }
        g = shard.groups(rows, list(rows), {"jira": 9.0, "confluence": 8.0})
        self.assertEqual(g["x/test_life.py"], (shard.DEFAULT_MINUTES, ["f1", "f2", "f3"]))
        self.assertEqual(g["jira"], (9.0, ["j"]))
        for n in range(1, 4):
            self.assertEqual(sum(1 for s in shard.assign(g, n) if {"f1", "f2", "f3"} & set(s)), 1, n)

    def test_shard_run_leaves_other_shards_tests_not_run_scope(self) -> None:
        from qa import shard
        plan = plan_for([JIRA_SYNC, ACL, RANKING, "e2e:tests/e2e/login.spec.ts", "py:tests/unit/test_patch_only.py"])
        mine = shard.select(self.cfg, plan, self.rows, 1, 2)
        others = set(shard.stack_ids(self.cfg, plan, self.rows)) - mine
        self.assertTrue(others)
        result = run.run(self.cfg, plan, self.rows, self.out, stack_mode="none", scope="stack", shard=(1, 2))
        by_id = {t["id"]: t for t in result["tests"]}
        for i in others:
            self.assertEqual(by_id[i]["kind"], "scope")
            self.assertIn("another stack shard (this is 1/2)", by_id[i]["details"])
        for i in mine:
            self.assertNotIn("another stack shard", by_id[i]["details"])
        self.assertIn("outside --scope stack", by_id["py:tests/unit/test_patch_only.py"]["details"])

    HEAVY = "it:heavy/test_heavy.py::test_heavy_path"

    def managed_plan(self, ids, graph=("neo4j",)):
        return {**plan_for(list(ids), graph=graph), "head": HEAD}

    def test_plan_lists_the_variants_its_tests_need(self) -> None:
        from qa import validate
        both = validate._stack(self.cfg, {}, "standard", [self.rows[ACL], self.rows[self.HEAVY]])
        self.assertEqual(both["variants"], ["", "solo"])
        self.assertEqual(validate._stack(self.cfg, {}, "standard", [self.rows[self.HEAVY]])["variants"], ["solo"])
        self.assertEqual(validate._stack(self.cfg, {}, "standard", [self.rows["py:tests/unit/test_patch_only.py"]])["variants"], [])

    def test_a_run_executes_only_its_variant(self) -> None:
        plan = self.managed_plan([ACL, self.HEAVY], graph=("neo4j",))
        projects = []
        with mock.patch.dict(os.environ, {"QA_RUN_ID": "77"}), \
             mock.patch.object(run, "preflight", return_value=({}, [])), \
             mock.patch.object(run.Stack, "start", lambda s: projects.append(s.project)), \
             mock.patch.object(run.Stack, "stop", lambda s: None), \
             mock.patch.object(run.Stack, "collect_logs", lambda s: None), \
             mock.patch.object(run, "_execute", return_value={"exit": 0, "label": "x"}), \
             mock.patch.object(run, "_outcomes", return_value={self.HEAVY: {"outcome": "pass", "seconds": 1, "details": ""}}):
            os.environ.pop("QA_COMPOSE_PROJECT", None)
            result = run.run(self.cfg, plan, self.rows, self.out, stack_mode="managed", retry=False, variant="solo")
        by_id = {t["id"]: t for t in result["tests"]}
        self.assertEqual(by_id[ACL]["kind"], "scope")
        self.assertIn("default stack variant", by_id[ACL]["details"])
        self.assertEqual(by_id[self.HEAVY]["outcome"], "pass")
        self.assertTrue(projects[0].endswith("-solo-neo4j"), projects)

    def test_default_run_marks_variant_tests_and_starts_no_stack_for_them(self) -> None:
        result = run.run(self.cfg, plan_for([self.HEAVY]), self.rows, self.out, stack_mode="none")
        t = result["tests"][0]
        self.assertEqual((t["outcome"], t["kind"]), ("not_run", "scope"))
        self.assertIn("solo stack variant", t["details"])
        with mock.patch.object(run.Stack, "start") as start:
            run.run(self.cfg, self.managed_plan([ACL]), self.rows, self.out, stack_mode="managed", variant="solo", retry=False)
        start.assert_not_called()

    def test_unknown_variant_exits_non_zero(self) -> None:
        err = io.StringIO()
        with mock.patch.dict(os.environ, {"QA_ROOT": str(self.root)}), contextlib.redirect_stderr(err):
            code = qa_main.main(["--out", str(self.out), "run", "--ids", ACL, "--stack-variant", "typo"])
        self.assertEqual(code, 2)
        self.assertIn("unknown stack variant 'typo'; known: ", err.getvalue())

    def test_a_fixed_compose_project_keeps_the_variant(self) -> None:
        from qa import stack
        with mock.patch.dict(os.environ, {"QA_COMPOSE_PROJECT": "mine"}):
            self.assertEqual(stack.project_name("abc", "1", "neo4j", "solo"), "mine-solo-neo4j")
            self.assertEqual(stack.project_name("abc", "1", "neo4j"), "mine-neo4j")

    def test_hooks_see_the_variant(self) -> None:
        os.environ.pop("QA_STACK_VARIANT", None)
        with mock.patch.object(run, "preflight", return_value=({}, [])), \
             mock.patch.object(run.Stack, "start"), mock.patch.object(run.Stack, "stop"), \
             mock.patch.object(run.Stack, "collect_logs"), \
             mock.patch.object(run, "_execute", return_value={"exit": 0, "label": "x"}), \
             mock.patch.object(run, "_outcomes", return_value={}):
            run.run(self.cfg, self.managed_plan([self.HEAVY]), self.rows, self.out, stack_mode="managed", retry=False, variant="solo")
        self.assertEqual((self.out / "stack/variant.txt").read_text(encoding="utf-8"), "solo")
        self.assertNotIn("QA_STACK_VARIANT", os.environ)

    def test_variant_parts_merge_to_the_executed_outcome(self) -> None:
        scope = {"outcome": "not_run", "kind": "scope", "details": "x"}
        default_part = {"tests": [{"id": "a", **{"outcome": "pass"}}, {"id": "h", **scope}]}
        variant_part = {"tests": [{"id": "a", **scope}, {"id": "h", "outcome": "fail"}]}
        got = {t["id"]: t["outcome"] for t in report.merge_results([default_part, variant_part], ["a", "h"])["tests"]}
        self.assertEqual(got, {"a": "pass", "h": "fail"})
        alone = report.merge_results([default_part], ["a", "h"])
        self.assertEqual({t["id"]: t["outcome"] for t in alone["tests"]}["h"], "not_run")
        self.assertEqual(report.verdict(self.cfg, alone, allowed=frozenset())[0], "fail")

    def test_bad_shard_argument_exits_2(self) -> None:
        for bad in ("3/2", "x", "0/2"):
            err = io.StringIO()
            with mock.patch.dict(os.environ, {"QA_ROOT": str(self.root)}), contextlib.redirect_stderr(err), \
                    self.assertRaises(SystemExit) as cm:
                qa_main.main(["--out", str(self.out), "run", "--ids", ACL, "--shard", bad])
            self.assertEqual(cm.exception.code, 2, bad)
            self.assertIn("--shard must be K/N with 1 <= K <= N", err.getvalue())

    def test_sharded_results_file_name(self) -> None:
        plan_path = self.out / "plan.json"
        plan_path.write_text(json.dumps(plan_for([ACL])), encoding="utf-8")
        seen = {}

        def fake_run(cfg, plan, rows, out, **kw):
            seen.update(kw)
            return {"tests": [], "seconds": 0, "fatal": []}

        with mock.patch.dict(os.environ, {"QA_ROOT": str(self.root)}), mock.patch.object(run, "run", fake_run), \
                contextlib.redirect_stdout(io.StringIO()):
            qa_main.main(["--out", str(self.out), "run", "--plan", str(plan_path), "--scope", "stack", "--graph-db", "neo4j", "--shard", "1/2"])
        self.assertEqual(seen["shard"], (1, 2))
        self.assertTrue((self.out / "results-stack-neo4j-1of2.json").is_file())

    def test_merge_keeps_the_worst_executed_outcome_across_parts(self) -> None:
        def merged(*outcomes):
            parts = [{"tests": [{"id": "a", "outcome": o}]} for o in outcomes]
            return report.merge_results(parts, ["a"])["tests"][0]["outcome"]
        self.assertEqual(merged("pass", "fail"), "fail")
        self.assertEqual(merged("fail", "pass"), "fail")
        self.assertEqual(merged("pass", "flaky"), "flaky")
        self.assertEqual(merged("fail", "error"), "error")
        self.assertEqual(merged("error", "pass"), "error")
        self.assertEqual(merged("skip", "pass"), "pass")
        self.assertEqual(merged("pass", "skip"), "pass")
        self.assertEqual(merged("pass", "not_run", "fail"), "fail")

    def test_emit_gh_outputs_names_the_shards(self) -> None:
        from qa import validate
        (self.out / "plan-input.json").write_text("{}", encoding="utf-8")
        gh = self.out / "gh-output"
        plan = {**plan_for([ACL, RANKING]), "matrix": []}
        with mock.patch.dict(os.environ, {"QA_ROOT": str(self.root), "QA_STACK_SHARDS": "3", "GITHUB_OUTPUT": str(gh)}), \
                mock.patch.object(validate, "merge", return_value=plan), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(qa_main.main(["--out", str(self.out), "validate", "--emit-gh-outputs"]), 0)
        lines = gh.read_text(encoding="utf-8").splitlines()
        self.assertIn("shards=[1]", lines)
        self.assertIn("shard_total=1", lines)


class TestAllowNotRun(unittest.TestCase):
    def parse(self, *argv: str) -> frozenset:
        p = argparse.ArgumentParser()
        p.add_argument("--strict", action="store_true")
        p.add_argument("--allow-not-run", default=",".join(sorted(report.DEFAULT_ALLOWED)))
        return qa_main._allowed(p.parse_args(argv))

    def test_default_and_list(self) -> None:
        self.assertEqual(self.parse(), report.DEFAULT_ALLOWED)
        self.assertEqual(self.parse("--allow-not-run", "secret,profile"), {"secret", "profile"})

    def test_empty_list_and_strict_allow_nothing(self) -> None:
        self.assertEqual(self.parse("--allow-not-run", ""), frozenset())
        self.assertEqual(self.parse("--strict"), frozenset())

    def test_unknown_kind_is_rejected(self) -> None:
        with self.assertRaises(SystemExit):
            self.parse("--allow-not-run", "secret,bogus")



class TestPinnedBases(unittest.TestCase):
    COMPOSE = "deployment/docker-compose/docker-compose.integration.neo4j.yml"
    BASE = "pipeshubai/pipeshub-ai-base:python-deps"

    def stack(self, root=None):
        from qa import stack
        return stack.Stack(root or Path(__file__).resolve().parents[2], "managed", "neo4j", "redis", [], "p", "http://x", Path("."))

    def fake(self, inspect_out, pull_rc=0):
        calls = []

        def run_(cmd, **kw):
            calls.append((cmd, kw))
            if cmd[:2] == ["docker", "pull"]:
                return mock.Mock(returncode=pull_rc, stdout="")
            if cmd[:3] == ["docker", "image", "inspect"]:
                return mock.Mock(returncode=0, stdout=inspect_out)
            return mock.Mock(returncode=0, stdout="")
        return run_, calls

    def test_base_image_args_come_from_the_compose_file(self) -> None:
        from qa import stack
        text = (Path(__file__).resolve().parents[2] / self.COMPOSE).read_text(encoding="utf-8")
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("PYTHON_DEPS_IMAGE", None)
            os.environ.pop("RUNTIME_BASE_IMAGE", None)
            self.assertEqual(stack.base_image_args(text), {"PYTHON_DEPS_IMAGE": self.BASE, "RUNTIME_BASE_IMAGE": "pipeshubai/pipeshub-ai-base:runtime"})
        with mock.patch.dict(os.environ, {"PYTHON_DEPS_IMAGE": "x:y"}):
            self.assertEqual(stack.base_image_args(text)["PYTHON_DEPS_IMAGE"], "x:y")

    def test_repo_digest_picks_the_matching_repository(self) -> None:
        from qa import stack
        self.assertEqual(stack.repo_digest("a/b:t", ["c/d@sha256:1", "a/b@sha256:2"]), "a/b@sha256:2")
        self.assertEqual(stack.repo_digest("a/b@sha256:9", []), "a/b@sha256:9")
        self.assertIsNone(stack.repo_digest("a/b:t", []))

    def test_base_without_repo_digest_is_recorded_unpinned(self) -> None:
        from qa import stack
        s = self.stack()
        fake, _ = self.fake("[]", pull_rc=1)
        with mock.patch.object(stack.subprocess, "run", fake):
            s.pin_bases()
        self.assertNotIn("PYTHON_DEPS_IMAGE", s.extra_env)
        self.assertIn(self.BASE, s.environment["unpinned"])
        # The Dockerfile's Node stage has no digest pin; its tag moves with the Node version.
        self.assertTrue(any(i.startswith("node:") for i in s.environment["unpinned"]), s.environment["unpinned"])
        self.assertEqual(s.environment["base_images"], {})

    def test_a_failing_pull_leaves_the_base_unpinned(self) -> None:
        import subprocess
        from qa import stack
        for exc in (subprocess.TimeoutExpired("docker", 300), FileNotFoundError("docker")):
            s = self.stack()
            with mock.patch.object(stack.subprocess, "run", side_effect=exc):
                s.pin_bases()
            self.assertIn(self.BASE, s.environment["unpinned"])
            self.assertNotIn("PYTHON_DEPS_IMAGE", s.extra_env)

    def test_from_lines_skip_flags_and_stage_names(self) -> None:
        from qa import stack
        root = Path(tempfile.mkdtemp())
        (root / "deployment/docker-compose").mkdir(parents=True)
        (root / self.COMPOSE).write_text("", encoding="utf-8")
        (root / "Dockerfile").write_text("FROM --platform=linux/amd64 node:20 AS builder\nFROM builder AS final\nFROM alpine:3\n", encoding="utf-8")
        s = self.stack(root)
        s.pin_bases()
        self.assertEqual(s.environment["unpinned"], ["node:20", "alpine:3"])

    def test_pinned_base_is_passed_to_the_build(self) -> None:
        from qa import stack
        s = self.stack()
        s.log_dir = Path(tempfile.mkdtemp())
        s.build = True
        fake, calls = self.fake('["pipeshubai/pipeshub-ai-base@sha256:abc"]')
        with mock.patch.object(stack.subprocess, "run", fake):
            s.pin_bases()
            s._run(s.compose("build"), "x.log")
        build = next(kw for cmd, kw in calls if cmd[-1] == "build")
        self.assertEqual(build["env"]["PYTHON_DEPS_IMAGE"], "pipeshubai/pipeshub-ai-base@sha256:abc")
        self.assertEqual(s.environment["base_images"]["PYTHON_DEPS_IMAGE"], "pipeshubai/pipeshub-ai-base@sha256:abc")

    def test_file_hashes_are_stable(self) -> None:
        from qa import stack
        root, tmp = testing.make_repo()
        self.addCleanup(tmp.cleanup)
        testing.write(root, "package-lock.json", "{}\n")
        testing.write(root, "sub/uv.lock", "a\n")
        testing.commit(root, "locks")
        before = stack.file_hashes(root, stack.LOCKFILES)
        self.assertEqual(before, stack.file_hashes(root, stack.LOCKFILES))
        self.assertEqual({"package-lock.json", "sub/uv.lock"} - set(before), set())
        testing.write(root, "package-lock.json", '{"a": 1}\n')
        after = stack.file_hashes(root, stack.LOCKFILES)
        self.assertNotEqual(before["package-lock.json"], after["package-lock.json"])
        self.assertEqual(before["sub/uv.lock"], after["sub/uv.lock"])


REPO = Path(__file__).resolve().parents[2]
COMPOSE_CONFIG = {"services": {
    "pipeshub-ai": {"ports": [{"target": 3000, "published": "3000"}, {"target": 8088, "published": "8088"}, {"target": 8000, "published": "8001", "protocol": "tcp"}]},
    "web-fixtures": {"ports": [{"host_ip": "127.0.0.1", "target": 8080, "published": "8089"}]},
    "worker": {},
}}


class TestStackPorts(RunCase):
    def stack(self, root=REPO):
        from qa import stack
        return stack.Stack(root, "managed", "neo4j", "redis", [], "p", "http://localhost:3000", self.out)

    def fake_config(self, rc=0):
        calls = []

        def run_(cmd, **kw):
            calls.append(cmd)
            return mock.Mock(returncode=rc, stdout=json.dumps(COMPOSE_CONFIG) if rc == 0 else "")
        return run_, calls

    def test_ports_come_from_compose_config_over_every_file_the_run_uses(self) -> None:
        from qa import stack
        s = self.stack()
        s.overrides.append("/w/license.json")
        fake, calls = self.fake_config()
        with mock.patch.dict(os.environ, {"QA_COMPOSE_OVERRIDES": "/o/extra.yml"}), mock.patch.object(stack.subprocess, "run", fake):
            self.assertEqual(s.host_ports(), [3000, 8001, 8088, 8089])
        self.assertEqual(calls[0][-3:], ["config", "--format", "json"])
        self.assertIn("/w/license.json", calls[0])
        self.assertIn("/o/extra.yml", calls[0])

    def test_without_docker_the_ci_file_is_parsed(self) -> None:
        from qa import stack
        s = self.stack()
        with mock.patch.object(stack.subprocess, "run", side_effect=FileNotFoundError("docker")):
            ports = s.host_ports()
        self.assertIn(8001, ports)
        self.assertNotIn(8000, ports)
        self.assertLessEqual({3000, 8088, 8091, 6334, 1025, 8025, 27017}, set(ports))

    def test_preflight_reports_the_stacks_busy_ports_only(self) -> None:
        stacks = [mock.Mock(host_ports=mock.Mock(return_value=[3000, 8001, 8089]))]
        invs, _ = run.resolve(self.cfg, plan_for([ACL]), self.rows, self.out)
        with mock.patch.object(run, "_probe", return_value=True), mock.patch.object(run.shutil, "which", return_value="/bin/docker"), \
             mock.patch.object(run, "_port_free", lambda p: p not in (8000, 8001)):
            _, fatal = run.preflight(self.cfg, invs, self.rows, "managed", stacks)
        self.assertEqual([f for f in fatal if f.startswith("ports")], ["ports in use: 8001 (use --stack external for a running stack, or QA_PORT_OFFSET to move this one)"])

    def test_offset_remaps_every_published_port_and_points_the_tests_at_them(self) -> None:
        from qa import stack
        s = self.stack()
        fake, _ = self.fake_config()
        with mock.patch.dict(os.environ, {"QA_COMPOSE_OVERRIDES": "", "WEB_FIXTURES_TEST_URL": "http://localhost:8089"}), \
             mock.patch.object(stack.subprocess, "run", fake):
            s.remap(20000, self.out / "stack")
        override = (self.out / "stack/ports-neo4j.yml").read_text(encoding="utf-8")
        self.assertIn("  pipeshub-ai:\n    ports: !override\n", override)
        self.assertIn('- "23000:3000/tcp"', override)
        self.assertIn('- "28001:8000/tcp"', override)
        self.assertIn('- "127.0.0.1:28089:8080/tcp"', override)
        self.assertNotIn("worker", override)
        self.assertEqual(s.overrides, [str(self.out / "stack/ports-neo4j.yml")])
        self.assertEqual(s.host_ports(), [23000, 28001, 28088, 28089])
        self.assertEqual(s.base_url, "http://localhost:23000")
        self.assertEqual(s.env()["PIPESHUB_BASE_URL"], "http://localhost:23000")
        self.assertEqual(s.extra_env["PIPESHUB_CONNECTOR_URL"], "http://localhost:28088")
        self.assertEqual(s.extra_env["WEB_FIXTURES_TEST_URL"], "http://localhost:28089")
        self.assertEqual(s.events, ["ports moved by 20000; no test setting names [8001]"])

    def test_bad_offset_and_a_tree_without_the_ports_hook_fail_setup(self) -> None:
        from qa import stack
        fake, _ = self.fake_config()
        with mock.patch.object(stack.subprocess, "run", fake):
            with self.assertRaisesRegex(RuntimeError, "out of range"):
                self.stack().remap(60000, self.out)
            with self.assertRaisesRegex(RuntimeError, "ports.py is missing"):
                self.stack(root=self.root).remap(10, self.out)

    def test_ports_hook_moves_local_settings_and_keeps_remote_ones(self) -> None:
        from qa import stack
        hook = stack._load(REPO, "ports")
        got = hook.env(lambda p: p + 1, {
            "TEST_MONGO_URI": "mongodb://u:p@localhost:27017/?authSource=admin",
            "SMB_HOST": "files.example.com", "TEST_OLLAMA_ENDPOINT": "https://ollama.example.com",
            "JIRA_TEST_BASE_URL": "http://127.0.0.1:8102/rest",
        })
        self.assertEqual(got["TEST_MONGO_URI"], "mongodb://u:p@localhost:27018/?authSource=admin")
        self.assertEqual(got["KAFKA_BOOTSTRAP_SERVERS"], "localhost:29093")
        self.assertEqual(got["JIRA_TEST_BASE_URL"], "http://127.0.0.1:8103/rest")
        self.assertEqual(got["REDIS_PORT"], "6380")
        self.assertNotIn("SMB_PORT", got)
        self.assertNotIn("TEST_OLLAMA_ENDPOINT", got)
        self.assertNotIn("TEST_NEO4J_URI", got)

    def test_dry_run_lists_hook_files_and_the_image_tag_and_starts_nothing(self) -> None:
        from qa import stack
        plan = plan_for([JIRA_SYNC])
        plan["head"] = HEAD
        with mock.patch.object(stack.subprocess, "run") as sp, mock.patch("qa.run.subprocess.run") as rp:
            dry = run.run(self.cfg, plan, self.rows, self.out, stack_mode="managed", build=True, dry_run=True)
        text = run.describe_dry_run(dry)
        self.assertIn(str(self.out / "stack/plus.json"), text)
        self.assertIn("image pipeshub-ai:qa-0123456789ab (built here)", text)
        self.assertFalse(sp.called or rp.called)

    def test_missing_compose_file_means_no_base_args(self) -> None:
        from qa import stack
        s = self.stack(root=self.root)
        with mock.patch.object(stack.subprocess, "run") as sp:
            s.pin_bases()
        self.assertEqual(s.environment["base_images"], {})
        self.assertFalse(sp.called)


class TestBuildFailure(RunCase):
    def test_a_failed_build_errors_every_stack_and_records_no_environment(self) -> None:
        import subprocess as sp_mod
        from qa import stack
        plan = plan_for([JIRA_SYNC], graph=("neo4j", "arango"))
        plan["head"] = HEAD
        commands = []

        def fake_run(self_, cmd, log, timeout=1800):
            commands.append(cmd)
            if "build" in cmd:
                raise sp_mod.CalledProcessError(1, cmd)

        def fake_pin(self_):
            self_.environment["base_images"] = {"PYTHON_DEPS_IMAGE": "x@sha256:1"}
        with mock.patch.object(run, "preflight", return_value=({}, [])), \
             mock.patch.object(stack.Stack, "_run", fake_run), mock.patch.object(stack.Stack, "pin_bases", fake_pin), \
             mock.patch("qa.run.subprocess.run"):
            result = run.run(self.cfg, plan, self.rows, self.out, stack_mode="managed", build=True, retry=False)
        self.assertFalse([c for c in commands if "up" in c])
        self.assertEqual(len([c for c in commands if "build" in c]), 1)
        self.assertEqual(result["tests"][0]["outcome"], "error")
        self.assertNotIn("environment", result)


class TestUpgradeVariant(RunCase):
    SEED = "it:weekly/test_upgrade.py::test_seed"
    COMPARE = "it:weekly/test_upgrade.py::test_counts_survive"
    OLD = "aaaaaaaaaaaa" + "0" * 28

    def go(self, build_fails: bool = False, build: bool = True, release_error: str = "", preexisting: bool = False, restart_fails: bool = False):
        import contextlib
        import subprocess as sp_mod
        from qa import stack
        events: list[str] = []

        def fake_sub(cmd, **kw):
            if cmd[:3] == ["docker", "image", "inspect"]:
                return sp_mod.CompletedProcess(cmd, 0 if cmd[-1].endswith("aaaaaaaaaaaa") and preexisting else 1, "", "")
            if cmd[:3] == ["docker", "image", "rm"]:
                events.append("rm:" + cmd[3])
            elif cmd[:2] == ["docker", "compose"]:
                op = next((a for a in cmd if a in ("build", "up", "restart", "down", "logs")), "")
                events.append(op + (":old" if op == "build" and any(str(self.out / "tree") in a for a in cmd) else ""))
                if op == "restart" and restart_fails:
                    raise sp_mod.CalledProcessError(1, cmd)
                if op == "build" and build_fails and any(str(self.out / "tree") in a for a in cmd):
                    raise sp_mod.CalledProcessError(1, cmd)
            return sp_mod.CompletedProcess(cmd, 0, "", "")

        @contextlib.contextmanager
        def tree(root, sha):
            events.append("worktree:" + sha[:12])
            yield self.out / "tree"

        fake_mod = mock.Mock()
        fake_mod.wait.return_value = []
        fake_mod.seed.return_value = "ok"

        def fake_execute(cfg, inv, log_dir, env):
            events.append(f"test:{inv.phase}:{env.get('QA_UPGRADE_DIR', '').endswith('stack/upgrade')}")
            return {"exit": 0, "label": inv.label}
        plan = {**plan_for([self.SEED, self.COMPARE]), "head": HEAD}
        with mock.patch.dict(os.environ, {"QA_RUN_ID": "5", "QA_KEEP_IMAGE": "0"}), \
             mock.patch.object(run, "preflight", return_value=({}, [])), \
             mock.patch.object(stack, "previous_release", **({"side_effect": RuntimeError(release_error)} if release_error else {"return_value": self.OLD})), \
             mock.patch.object(run.collect, "worktree", tree), \
             mock.patch.object(stack, "_load", return_value=fake_mod), \
             mock.patch.object(run, "_execute", fake_execute), \
             mock.patch.object(run, "_outcomes", lambda inv, rows, code: {i: {"outcome": "pass", "seconds": 1, "details": ""} for i in inv.ids}), \
             mock.patch("subprocess.run", fake_sub):
            os.environ.pop("QA_COMPOSE_PROJECT", None)
            result = run.run(self.cfg, plan, self.rows, self.out, stack_mode="managed", build=build, retry=False, variant="upgrade")
        return result, events

    def test_upgrade_variant_boots_old_then_new_on_the_same_volumes(self) -> None:
        result, events = self.go()
        ops = [e for e in events if e.split(":")[0] in ("build", "up", "restart", "down", "test", "rm")]
        self.assertEqual(ops, ["build:old", "up", "test:before:True", "build", "up", "restart", "test:after:True", "down",
                               "rm:pipeshub-ai:qa-0123456789ab", "rm:pipeshub-ai:qa-aaaaaaaaaaaa"])
        self.assertEqual({t["id"]: t["outcome"] for t in result["tests"]}, {self.SEED: "pass", self.COMPARE: "pass"})
        self.assertIn("upgrade: aaaaaaaaaaaa -> 0123456789ab", result["stack"][0]["events"])
        self.assertIn("second boot healthy", result["stack"][0]["events"])

    def test_upgrade_build_failure_errors_the_tests_and_leaves_nothing(self) -> None:
        result, events = self.go(build_fails=True)
        for t in result["tests"]:
            self.assertEqual(t["outcome"], "error")
            self.assertIn("upgrade from aaaaaaaaaaaa", t["details"])
        self.assertNotIn("up", events)
        self.assertNotIn("down", events)  # nothing was started, so there is nothing to tear down
        self.assertFalse([e for e in events if e.startswith("test:")])
        self.assertIn("rm:pipeshub-ai:qa-aaaaaaaaaaaa", events)

    def test_upgrade_switch_failure_tears_down_and_removes_both_images(self) -> None:
        result, events = self.go(restart_fails=True)
        for t in result["tests"]:
            self.assertEqual(t["outcome"] if t["id"] == self.COMPARE else "error", "error")
        self.assertIn("upgrade from aaaaaaaaaaaa", next(t for t in result["tests"] if t["id"] == self.COMPARE)["details"])
        self.assertIn("down", events)
        self.assertNotIn("test:after:True", events)
        self.assertIn("rm:pipeshub-ai:qa-aaaaaaaaaaaa", events)
        self.assertIn("rm:pipeshub-ai:qa-0123456789ab", events)

    def test_a_release_image_that_existed_before_the_run_is_not_removed(self) -> None:
        _, events = self.go(preexisting=True)
        self.assertNotIn("rm:pipeshub-ai:qa-aaaaaaaaaaaa", events)
        self.assertIn("rm:pipeshub-ai:qa-0123456789ab", events)

    def test_a_missing_release_tag_errors_the_tests_naming_the_ref(self) -> None:
        result, events = self.go(release_error="no v* tag before 0123456789ab")
        for t in result["tests"]:
            self.assertEqual(t["outcome"], "error")
            self.assertIn("upgrade from the previous release: no v* tag before 0123456789ab", t["details"])
        self.assertEqual([e for e in events if e in ("build", "build:old", "up")], [])
        self.assertFalse([e for e in events if "aaaaaaaaaaaa" in e])

    def test_previous_release_is_the_last_v_tag_before_head(self) -> None:
        from qa import stack
        self.assertRaisesRegex(RuntimeError, "no v\\* tag before", stack.previous_release, self.root, testing.git(self.root, "rev-parse", "HEAD"))
        first = testing.git(self.root, "rev-parse", "HEAD")
        testing.git(self.root, "tag", "v0.1.0")
        testing.write(self.root, "a.txt", "1")
        second = testing.commit(self.root, "two")
        testing.git(self.root, "tag", "v0.2.0")
        testing.write(self.root, "a.txt", "2")
        head = testing.commit(self.root, "three")
        testing.git(self.root, "tag", "not-a-release")
        self.assertEqual(stack.previous_release(self.root, head), second)
        self.assertEqual(stack.previous_release(self.root, second), first)

    def test_dry_run_names_both_images_and_the_switch(self) -> None:
        from qa import stack
        plan = {**plan_for([self.SEED, self.COMPARE]), "head": HEAD}
        with mock.patch.object(stack, "previous_release", return_value=self.OLD):
            dry = run.run(self.cfg, plan, self.rows, self.out, stack_mode="managed", dry_run=True, variant="upgrade")
            text = run.describe_dry_run(dry)
        self.assertIn("pipeshub-ai:qa-aaaaaaaaaaaa", text)
        self.assertIn("pipeshub-ai:qa-0123456789ab", text)
        self.assertIn("restart pipeshub-ai", text)
        self.assertIn("-before", text)
        self.assertIn("-after", text)


class TestEnvironmentReport(RunCase):
    def test_merged_results_keep_the_environment_and_every_digest(self) -> None:
        env = {"image": "pipeshub-ai:qa-0123456789ab", "image_digest": "sha256:a", "base_images": {"PYTHON_DEPS_IMAGE": "b@sha256:1"}, "unpinned": ["node:20"]}
        merged = report.merge_results([
            {"tests": [{"id": ACL, "outcome": "pass"}], "environment": env},
            {"tests": [{"id": ACL, "outcome": "pass"}]},
            {"tests": [{"id": ACL, "outcome": "pass"}], "environment": {**env, "image_digest": "sha256:b"}},
        ])
        self.assertEqual(merged["environment"]["image_digests"], ["sha256:a", "sha256:b"])
        self.assertEqual(merged["environment"]["base_images"], env["base_images"])
        body = report.render(self.cfg, plan_for([ACL]), merged)
        self.assertIn("### Environment", body)
        self.assertIn("- Image `pipeshub-ai:qa-0123456789ab`: `sha256:a`, `sha256:b`", body)
        self.assertIn("- Base `PYTHON_DEPS_IMAGE`: `b@sha256:1`", body)
        self.assertIn("- Not pinned: `node:20`", body)
        self.assertNotIn("environment", report.merge_results([{"tests": []}]))



setUpModule, tearDownModule = testing.hide_engine_env, testing.restore_engine_env


if __name__ == "__main__":
    unittest.main()
