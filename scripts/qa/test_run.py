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
from qa import catalog, config, junit, report, run, testing

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


class TestExternalStack(RunCase):
    def test_external_stack_runs_only_its_own_graph_db(self) -> None:
        with mock.patch.dict(os.environ, {"QA_STACK_GRAPH_DB": "neo4j"}):
            dry = run.run(self.cfg, plan_for([ACL], graph=("neo4j", "arango")), self.rows, self.out, stack_mode="external", dry_run=True)
        self.assertEqual([s.graph_db for s in dry["stack"]], ["neo4j"])


class TestGraphRuns(RunCase):
    def _run(self, execute_outcomes, **kw):
        stacks = [mock.Mock(graph_db=g, events=[], env=mock.Mock(return_value={"G": g})) for g in ("neo4j", "arango")]
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
        self.assertIn("node:20-slim", s.environment["unpinned"])
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


if __name__ == "__main__":
    unittest.main()
