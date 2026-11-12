"""qa agent: the floor as round 0, Claude Code's rounds, the merged results, the verdict and the kept test changes.

A fake `claude` on PATH acts out a scenario file: it writes round results as the engine would,
edits files, and writes a verdict. Test runs themselves are faked through run_round.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import stat
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest import mock

from qa import agent, catalog, config, report, testing

RETRIEVAL = "py:backend/python/tests/unit/services/test_retrieval.py"
PROVIDER = "py:backend/python/tests/unit/services/graph_db/test_provider.py"

FAKE = textwrap.dedent('''\
    #!{python}
    import json, os, re, sys, time
    from pathlib import Path
    scenario = json.loads(Path(os.environ["FAKE_CLAUDE"]).read_text())
    prompt = sys.argv[sys.argv.index("-p") + 1]
    Path(os.environ["FAKE_CLAUDE"] + ".argv").write_text(json.dumps(sys.argv))
    Path(os.environ["FAKE_CLAUDE"] + ".token").write_text(os.environ.get("CLAUDE_CODE_OAUTH_TOKEN", ""))
    inp = Path(re.search(r"Read `([^`]+)` first", prompt).group(1))
    agent_dir = inp.parent
    time.sleep(scenario.get("sleep", 0))
    for n, tests in scenario.get("rounds", {{}}).items():
        d = agent_dir / "rounds" / n
        d.mkdir(parents=True, exist_ok=True)
        (d / "results.json").write_text(json.dumps({{"tests": tests}}))
    for rel, body in scenario.get("write", {{}}).items():
        p = Path(rel)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body)
    if "verdict" in scenario:
        (agent_dir / "verdict.json").write_text(json.dumps(scenario["verdict"]))
    if "raw_verdict" in scenario:
        (agent_dir / "verdict.json").write_text(scenario["raw_verdict"])
    print(json.dumps({{"is_error": scenario.get("is_error", False), "result": "done"}}))
    sys.exit(scenario.get("exit", 0))
''')


def verdict(**kw):
    return {"schema": "qa-agent/v1", "verdict": "broken", "summary": "The change breaks retrieval.",
            "rounds": [{"why": "neighbours of the change", "tests": [PROVIDER]}],
            "findings": [{"kind": "regression", "title": "search drops results", "tests": [RETRIEVAL],
                          "evidence": "assert 2 == 3; retrieval.py:4", "files": ["backend/python/app/services/retrieval.py:4"]}],
            "unproven": [], **kw}


class AgentCase(unittest.TestCase):
    def setUp(self) -> None:
        self.root, tmp = testing.make_repo()
        self.addCleanup(tmp.cleanup)
        testing.write(self.root, ".gitignore", "qa-out/\n__pycache__/\n")  # as in the real repository
        self.base = testing.commit(self.root, "ignore run output")
        testing.write(self.root, "backend/python/app/services/retrieval.py", "def search():\n    return [1, 2]\n")
        self.head = testing.commit(self.root, "change retrieval")
        self.cfg = config.load(self.root)
        self.out = self.root / "qa-out"
        self.out.mkdir()
        self.plan = {"base": self.base, "head": self.head, "mode": "pr", "tests": [{"id": RETRIEVAL, "why": ["rule"], "module": "retrieval"}]}
        bin_dir = Path(tempfile.mkdtemp(prefix="qa-fake-claude-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(bin_dir, ignore_errors=True))
        fake = bin_dir / "claude"
        fake.write_text(FAKE.format(python=sys.executable), encoding="utf-8")
        fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
        self.scenario = bin_dir / "scenario.json"
        env = mock.patch.dict(os.environ, {"PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}", "FAKE_CLAUDE": str(self.scenario),
                                           "QA_ROOT": str(self.root), "CI": ""})
        env.start()
        self.addCleanup(env.stop)
        cwd = os.getcwd()
        os.chdir(self.root)
        self.addCleanup(os.chdir, cwd)
        self.floor_runs = []

    def run_round(self, ids, where):
        self.floor_runs.append(list(ids))
        self.round_token = os.environ.get("CLAUDE_CODE_OAUTH_TOKEN")
        where.mkdir(parents=True, exist_ok=True)
        (where / "results.json").write_text(json.dumps({"tests": [{"id": i, "outcome": "fail", "details": "assert 2 == 3"} for i in ids]}))

    def go(self, scenario, minutes=5.0):
        self.scenario.write_text(json.dumps(scenario), encoding="utf-8")
        with mock.patch.object(agent.paths, "REPO", Path(__file__).resolve().parents[2]):
            return agent.run_agent(self.cfg, self.out, self.plan, self.run_round, sess=None, minutes=minutes, max_turns=5)


class TestRounds(AgentCase):
    def test_round_zero_runs_the_floor_and_claude_gets_its_results(self) -> None:
        doc = self.go({"verdict": verdict()})
        self.assertEqual(self.floor_runs, [[RETRIEVAL]])
        inp = json.loads((self.out / "agent/input.json").read_text())
        self.assertEqual(inp["round0"]["other"][0]["id"], RETRIEVAL)
        self.assertIn("retrieval.py", inp["change"]["files"][0])
        # Every command Claude is given must fall under one of its Bash allow rules, or each call is refused.
        argv = json.loads(Path(str(self.scenario) + ".argv").read_text())
        prefixes = [t[5:-3] for t in argv[argv.index("--allowedTools") + 1].split(",") if t.startswith("Bash(")]
        for name, cmd in inp["commands"].items():
            self.assertTrue(any(cmd.startswith(p + " ") for p in prefixes), f"{name}: {cmd}")
        self.assertEqual(doc["verdict"]["verdict"], "broken")
        self.assertEqual(doc["rounds"], 1)

    def test_claude_is_limited_to_reading_test_edits_and_engine_commands(self) -> None:
        self.go({"verdict": verdict()})
        argv = json.loads(Path(str(self.scenario) + ".argv").read_text())
        allowed = argv[argv.index("--allowedTools") + 1].split(",")
        self.assertIn("Read", allowed)
        self.assertTrue(any(a.startswith("Bash(python3 ") and a.endswith(" run:*)") for a in allowed))
        self.assertFalse([a for a in allowed if a in ("Bash", "Bash(*)") or a.startswith(("Bash(git push", "Bash(curl", "Bash(pytest"))])
        self.assertEqual(argv[argv.index("--setting-sources") + 1], "user")
        self.assertIn("--strict-mcp-config", argv)
        self.assertIn("Read(//proc/**)", argv[argv.index("--disallowedTools") + 1])

    def test_later_rounds_merge_latest_wins_fail_then_pass_is_flaky_and_new_ids_join_the_plan(self) -> None:
        doc = self.go({"rounds": {"1": [{"id": RETRIEVAL, "outcome": "pass"}, {"id": PROVIDER, "outcome": "pass"}],
                                  "2": [{"id": PROVIDER, "outcome": "not_run", "kind": "scope", "details": "x"}]},
                       "verdict": verdict(verdict="works")})
        results = {t["id"]: t for t in json.loads((self.out / "results-agent.json").read_text())["tests"]}
        self.assertEqual(results[RETRIEVAL]["outcome"], "flaky")
        self.assertEqual(results[PROVIDER]["outcome"], "pass")  # a later not_run hides nothing
        self.assertEqual([t["id"] for t in doc["extra_tests"]], [PROVIDER])
        self.assertEqual(doc["rounds"], 3)

    def test_without_claude_the_floor_alone_runs_and_the_report_says_so(self) -> None:
        with mock.patch.object(agent.shutil, "which", return_value=None):
            doc = self.go({"verdict": verdict()})
        self.assertEqual(self.floor_runs, [[RETRIEVAL]])
        self.assertIn("agent unavailable: the claude CLI is not installed", doc["notes"][0])
        self.assertEqual(doc["verdict"], {})
        self.assertTrue((self.out / "results-agent.json").is_file())

    def test_in_ci_a_missing_credential_is_named(self) -> None:
        with mock.patch.dict(os.environ, {"CI": "true", "CLAUDE_CODE_OAUTH_TOKEN": "", "ANTHROPIC_API_KEY": ""}):
            doc = self.go({"verdict": verdict()})
        self.assertIn("no CLAUDE_CODE_OAUTH_TOKEN or ANTHROPIC_API_KEY", doc["notes"][0])

    def test_a_run_past_its_budget_is_stopped_and_keeps_its_rounds(self) -> None:
        doc = self.go({"sleep": 30, "verdict": verdict()}, minutes=0.02)
        self.assertTrue(any("stopped at its" in n for n in doc["notes"]), doc["notes"])
        self.assertEqual(self.floor_runs, [[RETRIEVAL]])

    def test_a_failing_claude_is_reported(self) -> None:
        doc = self.go({"is_error": True, "exit": 1})
        self.assertTrue(any(n.startswith("agent claude exited 1") for n in doc["notes"]), doc["notes"])
        self.assertIn("the agent wrote no verdict", doc["notes"])


class TestCredentials(AgentCase):
    def test_only_the_claude_process_gets_the_credential(self) -> None:
        with mock.patch.dict(os.environ, {"CI": "true", "CLAUDE_CODE_OAUTH_TOKEN": "tok-only-for-claude"}):
            doc = self.go({"verdict": verdict()})
            self.assertNotIn("CLAUDE_CODE_OAUTH_TOKEN", os.environ)
        self.assertIsNone(self.round_token)  # round 0 ran without it
        self.assertEqual(Path(str(self.scenario) + ".token").read_text(), "tok-only-for-claude")
        self.assertEqual(doc["verdict"]["verdict"], "broken")

    def test_the_credential_in_the_output_drops_it(self) -> None:
        with mock.patch.dict(os.environ, {"CLAUDE_CODE_OAUTH_TOKEN": "tok-only-for-claude"}):
            doc = self.go({"verdict": verdict(summary="token tok-only-for-claude")})
        self.assertEqual(doc["verdict"], {})
        self.assertIn("held a secret", doc["notes"][0])


class TestChanges(AgentCase):
    def test_test_files_are_kept_in_the_patch_and_product_edits_are_reverted(self) -> None:
        new_test = "backend/python/tests/unit/services/test_search_order.py"
        doc = self.go({"write": {new_test: "def test_order():\n    assert True\n",
                                 "backend/python/app/services/retrieval.py": "def search():\n    return []\n",
                                 "backend/python/app/services/new_helper.py": "x = 1\n"},
                       "verdict": verdict()})
        self.assertEqual(doc["tests_added"], [new_test])
        self.assertEqual(sorted(doc["notes"][0].split(": ", 1)[1].split(", ")),
                         ["backend/python/app/services/new_helper.py", "backend/python/app/services/retrieval.py"])
        self.assertEqual((self.root / "backend/python/app/services/retrieval.py").read_text(), "def search():\n    return [1, 2]\n")
        self.assertFalse((self.root / "backend/python/app/services/new_helper.py").exists())
        patch = (self.out / "agent-tests.patch").read_text()
        self.assertIn("+def test_order():", patch)
        self.assertNotIn("new_helper", patch)
        self.assertEqual(testing.git(self.root, "diff", "--cached", "--name-only"), "")  # nothing left staged

    def test_a_secret_in_the_output_drops_the_verdict_and_the_tests(self) -> None:
        with mock.patch.dict(os.environ, {"SOME_API_KEY": "sk-very-secret-value"}):
            doc = self.go({"write": {"backend/python/tests/unit/test_leak.py": "KEY = 'sk-very-secret-value'\n"},
                           "verdict": verdict(summary="key is sk-very-secret-value")})
        self.assertEqual(doc["verdict"], {})
        self.assertEqual(doc["tests_added"], [])
        self.assertEqual((self.out / "agent-tests.patch").read_text(), "")
        self.assertIn("held a secret", doc["notes"][0])
        self.assertNotIn("sk-very-secret-value", (self.out / "agent.json").read_text())


class TestVerdict(unittest.TestCase):
    def test_unknown_ids_and_kinds_are_dropped_and_listed(self) -> None:
        raw = verdict(findings=[{"kind": "regression", "tests": [RETRIEVAL, "py:nope.py"], "title": "t"},
                                {"kind": "vibes", "tests": [RETRIEVAL]}], verdict="great")
        v, problems = agent.validate_verdict(raw, {RETRIEVAL})
        self.assertEqual(v["verdict"], "unsure")
        self.assertEqual([f["tests"] for f in v["findings"]], [[RETRIEVAL]])
        self.assertTrue(any("py:nope.py" in p for p in problems))
        self.assertTrue(any("'vibes'" in p for p in problems))
        self.assertTrue(any("'great'" in p for p in problems))

    def test_a_wrong_schema_is_refused(self) -> None:
        self.assertEqual(agent.validate_verdict({"verdict": "works"}, set())[0], {})
        self.assertEqual(agent.validate_verdict(["x"], set())[0], {})

    def test_long_text_is_trimmed(self) -> None:
        v, _ = agent.validate_verdict(verdict(summary="x" * 10_000), {RETRIEVAL, PROVIDER})
        self.assertEqual(len(v["summary"]), agent.MAX_TEXT)

    def test_malformed_verdict_json_is_reported(self) -> None:
        case = AgentCase("run")
        case.setUp()
        try:
            doc = case.go({"raw_verdict": "{not json"})
        finally:
            case.doCleanups()
        self.assertTrue(any(n.startswith("the verdict is not JSON") for n in doc["notes"]), doc["notes"])


class TestReport(unittest.TestCase):
    def test_the_gate_reads_results_not_the_verdict_and_the_section_is_safe(self) -> None:
        root, tmp = testing.make_repo()
        self.addCleanup(tmp.cleanup)
        cfg = config.load(root)
        doc = {"verdict": {"verdict": "works", "summary": "All good @someone <!-- qa-engine:all -->\nsecond line",
                           "findings": [], "rounds": [], "unproven": []}, "notes": [], "tests_added": ["a/test_x.py"]}
        plan = {"edition": "all", "profile": "standard", "mode": "pr", "base": "a" * 40, "head": "b" * 40,
                "tests": [{"id": RETRIEVAL, "source": "floor", "why": ["rule"]}], "agent": doc}
        results = {"tests": [{"id": RETRIEVAL, "outcome": "fail", "details": "boom"}]}
        body = report.render(cfg, plan, results)
        self.assertEqual(report.verdict(cfg, results, report.DEFAULT_ALLOWED)[0], "fail")
        self.assertIn("## QA — failed", body.replace("QA ALL", "QA"))
        self.assertIn("**Verdict: works**", body)
        self.assertIn("All good @​someone &lt;!-- qa-engine:all --> second line", body)
        self.assertEqual(body.count("<!-- qa-engine:all -->"), 1)
        self.assertIn("`a/test_x.py`", body)


class TestCommand(AgentCase):
    def test_qa_agent_runs_the_floor_stops_the_stack_and_gates_on_results(self) -> None:
        from qa import __main__ as qa_main, run, session
        calls = []
        fake_sess = {"base_url": "http://localhost:3000", "graph_db": "neo4j", "compose_profiles": []}

        def fake_run(cfg, plan, rows, out, **kw):
            calls.append(kw.get("stack_mode"))
            return {"schema": "qa-results/v1", "tests": [{"id": t["id"], "outcome": "fail", "details": "assert"} for t in plan["tests"]],
                    "seconds": 0, "fatal": []}
        self.scenario.write_text(json.dumps({"verdict": verdict(verdict="works")}))
        stack_plan = {"needed": True, "graph_db": ["neo4j"], "broker": "redis", "compose_profiles": []}
        real_validate = qa_main.cmd_validate

        def validate_with_stack(args):
            rc = real_validate(args)
            p = Path(args.out) / "plan.json"
            plan = json.loads(p.read_text())
            plan["stack"] = stack_plan
            p.write_text(json.dumps(plan))
            return rc
        with mock.patch.object(qa_main, "cmd_validate", side_effect=validate_with_stack), \
             mock.patch.object(session, "up", return_value=fake_sess) as up, \
             mock.patch.object(session, "use", return_value="http://localhost:3000"), \
             mock.patch.object(session, "down", return_value=["compose down"]) as down, \
             mock.patch.object(run, "run", side_effect=fake_run), \
             mock.patch.object(agent.paths, "REPO", Path(__file__).resolve().parents[2]), \
             contextlib.redirect_stdout(io.StringIO()) as stdout, contextlib.redirect_stderr(io.StringIO()):
            code = qa_main.main(["--out", str(self.out), "agent", "--base", self.base, "--gate"])
        up.assert_called_once()
        down.assert_called_once()
        self.assertEqual(calls, ["external"])
        self.assertEqual(code, 1, stdout.getvalue())  # results fail; the verdict "works" does not pass the gate
        self.assertIn("**Verdict: works**", (self.out / "report.md").read_text())

    def test_the_stack_is_stopped_when_a_round_raises(self) -> None:
        from qa import __main__ as qa_main, run, session
        stack_plan = {"needed": True, "graph_db": ["neo4j"], "broker": "redis", "compose_profiles": []}
        real_validate = qa_main.cmd_validate

        def validate_with_stack(args):
            rc = real_validate(args)
            p = Path(args.out) / "plan.json"
            plan = json.loads(p.read_text())
            plan["stack"] = stack_plan
            p.write_text(json.dumps(plan))
            return rc
        with mock.patch.object(qa_main, "cmd_validate", side_effect=validate_with_stack), \
             mock.patch.object(session, "up", return_value={"base_url": "u", "graph_db": "neo4j", "compose_profiles": []}), \
             mock.patch.object(session, "use", return_value="u"), \
             mock.patch.object(session, "down", return_value=[]) as down, \
             mock.patch.object(run, "run", side_effect=RuntimeError("docker died")), \
             contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(RuntimeError):
                qa_main.main(["--out", str(self.out), "agent", "--base", self.base])
        down.assert_called_once()


class TestNotebook(AgentCase):
    def test_a_run_writes_a_notebook_and_the_next_run_gets_it_with_what_changed(self) -> None:
        doc = self.go({"verdict": verdict()})
        nb = json.loads((self.out / "agent-notebook.json").read_text())
        self.assertEqual((nb["schema"], nb["head"], nb["verdict"], nb["runs"]), ("qa-notebook/v1", self.head, "broken", 1))
        self.assertEqual(nb["failing"], [RETRIEVAL])
        self.assertEqual(doc["notebook"], nb)
        # The author pushes a fix; the next run gets the notebook and the files changed since.
        testing.write(self.root, "backend/python/app/services/retrieval.py", "def search():\n    return [1, 2, 3]\n")
        self.plan = dict(self.plan, head=testing.commit(self.root, "fix retrieval"))
        doc2 = self.go_with(nb, {"verdict": verdict(verdict="works", findings=[], since_last=[
            {"title": "search drops results", "now": "fixed", "evidence": "test_retrieval passes"}])})
        inp = json.loads((self.out / "agent/input.json").read_text())
        self.assertEqual(inp["previous"]["head"], self.head)
        self.assertEqual(inp["since_last"]["files"], ["backend/python/app/services/retrieval.py"])
        self.assertEqual(doc2["verdict"]["since_last"][0]["now"], "fixed")
        self.assertEqual(doc2["previous"]["head"], self.head)
        self.assertEqual(json.loads((self.out / "agent-notebook.json").read_text())["runs"], 2)

    def go_with(self, previous, scenario):
        self.scenario.write_text(json.dumps(scenario), encoding="utf-8")
        with mock.patch.object(agent.paths, "REPO", Path(__file__).resolve().parents[2]):
            return agent.run_agent(self.cfg, self.out, self.plan, self.run_round, sess=None, minutes=5, max_turns=5,
                                   previous=previous, pr=None)

    def test_since_last_handles_unchanged_and_rewritten_history(self) -> None:
        prev = {"head": self.head}
        self.assertTrue(agent.since(self.root, prev, self.head)["unchanged"])
        self.assertIn("history rewritten", agent.since(self.root, {"head": "f" * 40}, self.head)["note"])
        testing.git(self.root, "checkout", "-q", "-b", "other", self.base)
        other = testing.commit(self.root, "elsewhere")
        self.assertIn("rebased or force-pushed", agent.since(self.root, prev, other)["note"])
        self.assertIsNone(agent.since(self.root, None, self.head))

    def test_a_notebook_is_only_used_for_its_own_pr_and_is_trimmed(self) -> None:
        nb = agent.build_notebook(agent.validate_verdict(verdict(summary="s" * 5000), {RETRIEVAL, PROVIDER})[0],
                                  {RETRIEVAL: {"outcome": "fail"}}, self.plan, [], 7, None)
        self.assertEqual(len(nb["summary"]), 600)
        self.assertIsNotNone(agent.validate_notebook(nb, 7))
        self.assertIsNone(agent.validate_notebook(nb, 8))
        self.assertIsNone(agent.validate_notebook(dict(nb, head="main"), 7))
        self.assertIsNone(agent.validate_notebook({"schema": "x"}, 7))
        bad = agent.validate_notebook(dict(nb, verdict="great", runs=-3, findings=[{"kind": "vibes", "title": "t"}]), 7)
        self.assertEqual((bad["verdict"], bad["runs"], bad["findings"][0]["kind"]), ("none", 1, "test-bug"))

    def test_the_notebook_rides_in_the_comment_and_only_the_bots_own_counts(self) -> None:
        nb = agent.build_notebook(verdict(), {RETRIEVAL: {"outcome": "fail"}}, self.plan, [], 7, None)
        plan = {"edition": "all", "profile": "standard", "mode": "pr", "base": "a" * 40, "head": "b" * 40,
                "tests": [{"id": RETRIEVAL, "source": "floor", "why": ["rule"]}],
                "agent": {"verdict": {}, "notes": [], "notebook": nb}}
        body = report.render(self.cfg, plan, {"tests": [{"id": RETRIEVAL, "outcome": "fail", "details": "x"}]})
        self.assertTrue(body.splitlines()[1].startswith(report.NOTEBOOK_MARK))
        forged = {"user": {"login": "someone"}, "body": report.notebook_line(dict(nb, verdict="works"))}
        bot = {"user": {"login": "github-actions[bot]"}, "body": body}
        self.assertEqual(report.notebook_from_comments([bot, forged])["verdict"], "broken")
        self.assertIsNone(report.notebook_from_comments([forged]))

    def test_the_notebook_command_reads_the_newest_bot_comment(self) -> None:
        from qa import __main__ as qa_main
        nb = agent.build_notebook(verdict(), {}, self.plan, [], 7, None)
        old = agent.build_notebook(verdict(verdict="unsure"), {}, dict(self.plan, head=self.base), [], 7, None)
        lines = "\n".join(json.dumps(c) for c in ({"user": {"login": "github-actions[bot]"}, "body": "x\n" + report.notebook_line(old)},
                                                   {"user": {"login": "github-actions[bot]"}, "body": "x\n" + report.notebook_line(nb)},
                                                   {"user": {"login": "mallory"}, "body": report.notebook_line(dict(nb, verdict="works"))}))
        target = self.out / "prev.json"
        with mock.patch.object(qa_main.subprocess, "run", return_value=mock.Mock(returncode=0, stdout=lines, stderr="")), \
             contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(qa_main.main(["notebook", "--pr", "7", "--repo", "o/r", "--to", str(target)]), 0)
        self.assertEqual(json.loads(target.read_text())["verdict"], "broken")


class TestNotebookCommand(AgentCase):
    def test_qa_agent_hands_the_last_notebook_in_the_run_directory_to_the_next_run(self) -> None:
        from qa import __main__ as qa_main
        nb = agent.build_notebook(verdict(), {}, self.plan, [], None, None)
        (self.out / "agent-notebook.json").write_text(json.dumps(nb))
        seen = {}

        def fake_run_agent(cfg, out, plan, run_round, **kw):
            seen.update(kw)
            return {"notes": [], "verdict": {}, "rounds": 1, "tests_added": []}
        with mock.patch.object(agent, "run_agent", side_effect=fake_run_agent), \
             contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            qa_main.main(["--out", str(self.out), "agent", "--base", self.base, "--stack", "none"])
        self.assertEqual(seen["previous"]["head"], self.head)
        self.assertEqual(seen["previous"]["runs"], 1)


class TestNestedArtifact(unittest.TestCase):
    def test_the_report_finds_the_agent_files_one_level_down(self) -> None:
        from qa import __main__ as qa_main
        root, tmp = testing.make_repo()
        self.addCleanup(tmp.cleanup)
        out = root / "qa-out"
        (out / "qa-out").mkdir(parents=True)
        plan = {"edition": "all", "profile": "standard", "mode": "pr", "base": "a" * 40, "head": "b" * 40,
                "tests": [{"id": RETRIEVAL, "source": "floor", "why": ["rule"]}]}
        (out / "plan.json").write_text(json.dumps(plan))
        (out / "qa-out" / "agent.json").write_text(json.dumps({"verdict": {"verdict": "works"}, "notes": ["agent unavailable: x"],
                                                               "extra_tests": [{"id": PROVIDER, "source": "agent", "why": ["a"]}]}))
        merged_plan, _ = qa_main._merged(out)
        self.assertEqual(merged_plan["agent"]["notes"], ["agent unavailable: x"])
        self.assertEqual([t["id"] for t in merged_plan["tests"]], [RETRIEVAL, PROVIDER])


class TestCombineCommand(AgentCase):
    def pr_branch(self, number, rel, body):
        testing.git(self.root, "checkout", "-q", "-b", f"pr{number}", self.base)
        testing.write(self.root, rel, body)
        sha = testing.commit(self.root, f"pr {number}")
        testing.git(self.root, "checkout", "-q", "main")
        return sha

    def test_a_combined_run_tests_the_stacked_prs_and_reports_them(self) -> None:
        from qa import __main__ as qa_main, run
        a = self.pr_branch(11, "backend/python/app/services/untested.py", "x = 11\n")
        b = self.pr_branch(12, "backend/python/app/connectors/sources/jira/sync.py", "y = 12\n")
        self.scenario.write_text(json.dumps({"verdict": verdict(verdict="works", findings=[])}))
        with mock.patch.object(run, "run", return_value={"schema": "qa-results/v1", "tests": [], "seconds": 0, "fatal": []}), \
             mock.patch.object(agent.paths, "REPO", Path(__file__).resolve().parents[2]), \
             contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            code = qa_main.main(["--out", str(self.out), "agent", "--stack", "none", "--combine-base", self.base,
                                 "--combine", f"11:{a}:First", "--combine", f"12:{b}"])
        self.assertEqual(code, 0)
        doc = json.loads((self.out / "agent.json").read_text())
        self.assertEqual([p["number"] for p in doc["combined"]["prs"]], [11, 12])
        plan = json.loads((self.out / "plan.json").read_text())
        self.assertEqual(plan["base"], self.base)
        self.assertTrue((self.out / "combined.bundle").is_file())
        self.assertIn("Combined run of #11, #12", (self.out / "report.md").read_text())
        self.assertEqual(testing.git(self.root, "rev-list", "--merges", f"{self.base}..HEAD"), "")

    def test_a_combine_that_stops_reports_why_and_tests_nothing(self) -> None:
        from qa import __main__ as qa_main, run
        a = self.pr_branch(11, "backend/python/app/services/untested.py", "x = 11\n")
        with mock.patch.object(run, "run") as ran, contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            code = qa_main.main(["--out", str(self.out), "agent", "--combine-base", self.base,
                                 "--combine", f"11:{a}", "--combine", f"12:{'e' * 40}"])
        self.assertEqual(code, 1)
        ran.assert_not_called()
        body = (self.out / "report.md").read_text()
        self.assertIn("could not be stacked: #12: commit eeeeeeeeeeee is not in this checkout", body)
        self.assertIn("Stacked before it stopped: #11.", body)

    def test_a_malformed_combine_argument_is_refused(self) -> None:
        from qa import __main__ as qa_main
        with contextlib.redirect_stderr(io.StringIO()) as err:
            self.assertEqual(qa_main.main(["--out", str(self.out), "agent", "--combine", "11:abc"]), 2)
        self.assertIn("NUMBER:FULL_SHA", err.getvalue())


if __name__ == "__main__":
    unittest.main()
