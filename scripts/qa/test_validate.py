"""The rules no model can break: the floor stays, failures fall back to it,
additions are catalog ids in this edition, and only additions are trimmed.

Run: python3 -m unittest discover -s scripts -p 'test_*.py'
"""

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from qa import catalog, collect, config, impact, paths, planner, schema, testing, validate

SEARCH = "it:retrieval/test_search.py::test_ranking"
ACL = "it:retrieval/test_search.py::test_acl_filter"
OFF_FLOOR = "py:tests/unit/test_patch_only.py"
PLUS_TEST = "it:plus/test_ai_usage.py::test_budget"


class ValidateCase(unittest.TestCase):
    def setUp(self) -> None:
        self.root, tmp = testing.make_repo()
        self.addCleanup(tmp.cleanup)
        base = testing.git(self.root, "rev-parse", "HEAD")
        testing.write(self.root, "backend/python/app/services/retrieval.py", "def search():\n    return [2]\n")
        testing.commit(self.root, "change retrieval")
        self.cfg = config.load(self.root)
        changeset, _ = collect.collect(self.cfg, base, None, mode="pr")
        self.rows = catalog.index(catalog.build(self.cfg))
        self.plan_input = impact.compute(self.cfg, changeset, list(self.rows.values()), scope="oss")
        self.floor = set(self.plan_input["floor"]["tests"])

    def test_plan_tests_carry_flows(self) -> None:
        plan = validate.merge(self.cfg, self.plan_input, None, self.rows)
        self.assertEqual(next(t for t in plan["tests"] if t["id"] == SEARCH)["flows"], ["search"])
        self.assertEqual(plan["flows"], self.plan_input["flows"])

    def llm(self, **kw) -> dict:
        out = {"schema": "qa-plan-llm/v1", "add": [], "add_matrix": [], "missing": [], "risk": {"level": "low", "reasons": []},
               "_planner": {"adapter": "test", "status": "ok"}}
        out.update(kw)
        return out

    def merge(self, llm) -> dict:
        return validate.merge(self.cfg, self.plan_input, llm, self.rows)


class TestInvariants(ValidateCase):
    def test_floor_survives_a_model_that_ignores_it(self) -> None:
        plan = self.merge(self.llm(add=[{"id": OFF_FLOOR, "reason": "ranking uses search", "confidence": 0.9}]))
        self.assertTrue(self.floor <= {t["id"] for t in plan["tests"]})
        self.assertIn(OFF_FLOOR, {t["id"] for t in plan["tests"] if t["source"] == "model"})

    def test_model_failure_means_floor_only(self) -> None:
        plan = self.merge({"_planner": {"adapter": "claude_code", "status": "failed", "error": "timeout"}, "add": [{"id": SEARCH}]})
        self.assertEqual({t["id"] for t in plan["tests"]}, self.floor)
        self.assertEqual(plan["planner"]["status"], "failed")

    def test_malformed_output_is_a_failure(self) -> None:
        plan = self.merge(self.llm(schema="something-else", add="not a list"))
        self.assertEqual(plan["planner"]["status"], "failed")
        self.assertEqual({t["id"] for t in plan["tests"]}, self.floor)

    def test_unknown_ids_are_dropped_and_listed(self) -> None:
        plan = self.merge(self.llm(add=[{"id": "it:made/up.py::test_x", "reason": "x"}]))
        self.assertEqual(plan["dropped"], [{"id": "it:made/up.py::test_x", "why": "not in the catalog"}])

    def test_overlay_id_in_a_base_run_is_dropped(self) -> None:
        plan = self.merge(self.llm(add=[{"id": PLUS_TEST, "reason": "x"}]))
        self.assertNotIn(PLUS_TEST, {t["id"] for t in plan["tests"]})
        self.assertIn("edition plus", plan["dropped"][0]["why"])

    def test_phase_a_edition_value_still_valid(self) -> None:
        self.assertEqual(schema.check({**self.plan_input, "edition": "custom"}, "plan-input.v1.json"), [])

    def test_budget_trims_low_confidence_additions_not_the_floor(self) -> None:
        # A 2 minute budget fits one integration test (assumed 90 s).
        self.plan_input["budget"]["extra_minutes"] = 2
        adds = [
            {"id": "it:connectors/jira/test_jira.py::TestJira::test_tc_sync_001_full_sync", "reason": "b", "confidence": 0.8},
            {"id": "it:connectors/jira/test_jira.py::TestJira::test_tc_incr_001_incremental", "reason": "c", "confidence": 0.8},
            {"id": "py:tests/unit/test_patch_only.py", "reason": "d", "confidence": 0.1},
        ]
        plan = self.merge(self.llm(add=adds))
        self.assertTrue(self.floor <= {t["id"] for t in plan["tests"]})
        self.assertEqual([t["id"] for t in plan["budget_trimmed"]], ["it:connectors/jira/test_jira.py::TestJira::test_tc_incr_001_incremental"])

    def test_high_risk_raises_the_profile(self) -> None:
        plan = self.merge(self.llm(risk={"level": "high", "reasons": ["tenant isolation"]}))
        self.assertEqual(plan["profile"], "conservative")
        self.assertEqual(plan["stack"]["graph_db"], ["neo4j", "arango"])

    def test_model_text_cannot_inject_markup_or_mentions(self) -> None:
        plan = self.merge(self.llm(add=[{"id": SEARCH, "reason": "@everyone <img src=x> [click](https://evil) `rm -rf`"}]))
        reason = next(t for t in plan["tests"] if t["id"] == SEARCH)["why"][0]
        for bad in ("@", "<", "](", "https://", "`"):
            self.assertNotIn(bad, reason)

    def test_clean_text_drops_bare_www_and_ftp_links(self) -> None:
        text = validate.clean_text("see www.evil.example, WWW.Evil.example or ftp://evil.example and FTP://x")
        for bad in ("www.", "WWW.", "ftp://", "FTP://"):
            self.assertNotIn(bad, text)

    def test_unknown_matrix_entry_is_dropped(self) -> None:
        plan = self.merge(self.llm(add_matrix=[{"entry": "mysql", "reason": "x"}, {"entry": "qdrant", "reason": "y"}]))
        self.assertIn("qdrant", plan["matrix"])
        self.assertNotIn("mysql", plan["matrix"])

    def test_stack_needs_come_from_selected_tests(self) -> None:
        plan = self.merge(self.llm())
        self.assertTrue(plan["stack"]["needed"])
        self.assertEqual(plan["stack"]["broker"], "redis")


class TestPlannerAdapters(ValidateCase):
    def run_adapter(self, adapter: str, env: dict[str, str]) -> dict:
        with tempfile.TemporaryDirectory() as tmp:
            inp = Path(tmp) / "plan-input.json"
            inp.write_text(json.dumps(self.plan_input), encoding="utf-8")
            out = Path(tmp) / "plan-llm.json"
            with mock.patch.dict(os.environ, env):
                planner.run(self.root, adapter, inp, out, timeout=30)
            return json.loads(out.read_text(encoding="utf-8"))

    def test_command_adapter_works_with_any_cli(self) -> None:
        reply = json.dumps({"schema": "qa-plan-llm/v1", "add": [{"id": SEARCH, "reason": "r", "confidence": 1}]})
        llm = self.run_adapter("command", {"QA_PLANNER_CMD": f"echo '{reply}'"})
        self.assertEqual(llm["_planner"]["status"], "ok")
        plan = self.merge(llm)
        self.assertIn(SEARCH, {t["id"] for t in plan["tests"]})

    def test_litellm_adapter_works_with_any_provider_model(self) -> None:
        import sys
        import types
        reply = json.dumps({"schema": "qa-plan-llm/v1", "add": [{"id": SEARCH, "reason": "r", "confidence": 1}]})
        sent: dict = {}

        def completion(**kw):
            sent.update(kw)
            return types.SimpleNamespace(choices=[types.SimpleNamespace(message=types.SimpleNamespace(content=reply))])

        with mock.patch.dict(sys.modules, {"litellm": types.SimpleNamespace(completion=completion)}):
            llm = self.run_adapter("litellm", {"QA_PLANNER_MODEL": "azure/gpt-test"})
        self.assertEqual(llm["_planner"]["status"], "ok")
        self.assertEqual(sent["model"], "azure/gpt-test")
        self.assertIn("cannot read files", sent["messages"][0]["content"])
        self.assertIn('"candidates"', sent["messages"][1]["content"])
        self.assertIn(SEARCH, {t["id"] for t in self.merge(llm)["tests"]})

    def test_litellm_input_is_capped(self) -> None:
        import sys
        import types
        sent: dict = {}

        def completion(**kw):
            sent.update(kw)
            return types.SimpleNamespace(choices=[types.SimpleNamespace(message=types.SimpleNamespace(content="{}"))])

        self.plan_input["floor"]["items"] = {f"py:t{i}.py": {"why": ["x" * 400]} for i in range(2000)}
        self.plan_input["candidates"] = [{"id": f"it:c{i}.py::t", "title": "y" * 300, "covers": []} for i in range(2000)]
        out = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, out, True)
        (out / "plan-input.json").write_text(json.dumps(self.plan_input), encoding="utf-8")
        (out / "changeset.diff").write_text("d" * 200_000, encoding="utf-8")
        with mock.patch.dict(sys.modules, {"litellm": types.SimpleNamespace(completion=completion)}), \
                mock.patch.dict(os.environ, {"QA_PLANNER_MODEL": "m"}):
            planner._litellm(self.root, out / "plan-input.json", 5)
        self.assertLessEqual(len(sent["messages"][1]["content"].encode()), planner.MAX_INPUT_BYTES + 60_000 + 100)

    def test_litellm_adapter_without_a_model_falls_back_to_the_floor(self) -> None:
        env = {k: v for k, v in os.environ.items() if k != "QA_PLANNER_MODEL"}
        with mock.patch.dict(os.environ, env, clear=True):
            llm = self.run_adapter("litellm", {})
        self.assertEqual(llm["_planner"]["status"], "failed")
        self.assertIn("QA_PLANNER_MODEL", llm["_planner"]["error"])

    def test_crashing_adapter_reports_failure_and_still_writes_output(self) -> None:
        llm = self.run_adapter("command", {"QA_PLANNER_CMD": "exit 3"})
        self.assertEqual(llm["_planner"]["status"], "failed")
        self.assertEqual({t["id"] for t in self.merge(llm)["tests"]}, self.floor)

    def test_prompt_comes_from_the_engine_not_the_tree(self) -> None:
        testing.write(self.root, "scripts/qa/prompts/planner.md", "PR-WRITTEN PROMPT\n")
        text = planner._instructions(self.root, self.root / "plan-input.json")
        self.assertNotIn("PR-WRITTEN PROMPT", text)
        self.assertIn((paths.REPO / planner.PROMPT).read_text(encoding="utf-8"), text)

    def test_notes_come_from_the_engine_overlays_not_the_tree(self) -> None:
        testing.write(self.root, "scripts/plus/qa/planner_notes.md", "PR-WRITTEN NOTES\n")
        text = planner._instructions(self.root, self.root / "plan-input.json")
        self.assertNotIn("PR-WRITTEN NOTES", text)


PROBE = {"probe": {"prompt": "scripts/qa/prompts/planner.md", "schema": "plan-llm.v1.json", "diff": False, "empty": {"x": 1}}}


class TestModelTasks(ValidateCase):
    def setUp(self) -> None:
        super().setUp()
        patcher = mock.patch.dict(planner.TASKS, PROBE)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.inp, self.out = self.dir / "probe-input.json", self.dir / "probe-llm.json"
        self.inp.write_text('{"a": 1}', encoding="utf-8")

    def test_every_task_keeps_the_read_only_tools(self) -> None:
        import subprocess
        seen: list = []

        def fake_run(cmd, **kw):
            seen.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, json.dumps({"result": '{"schema": "qa-plan-llm/v1"}'}), "")

        tasks = sorted(set(planner.TASKS) - set(PROBE))
        self.assertEqual(tasks, ["implement", "plan", "review", "scenarios"])
        with mock.patch.object(planner.subprocess, "run", fake_run), \
                mock.patch.object(planner.shutil, "which", return_value="/bin/claude"):
            for task in tasks:
                planner.ask(self.root, "claude_code", task, self.inp, self.out, timeout=5)
        self.assertEqual(len(seen), len(tasks))
        for cmd in seen:
            # Security: no Bash at all (git diff --output writes files), and only the user's settings, so the
            # tree under test cannot add hooks or tools through its .claude/settings*.json.
            self.assertEqual(cmd[cmd.index("--allowedTools") + 1], "Read,Glob,Grep")
            self.assertEqual(cmd[cmd.index("--setting-sources") + 1], "user")

    def test_invalid_task_reply_is_the_empty_result(self) -> None:
        with mock.patch.dict(planner.ADAPTERS, {"fake": lambda *a, **kw: {"bogus": 1}}):
            planner.ask(self.root, "fake", "probe", self.inp, self.out)
        result = json.loads(self.out.read_text(encoding="utf-8"))
        self.assertEqual(result["_planner"]["status"], "failed")
        self.assertEqual({k: v for k, v in result.items() if k != "_planner"}, {"x": 1})

    def test_task_prompt_comes_from_the_engine_checkout(self) -> None:
        testing.write(self.root, "scripts/qa/prompts/planner.md", "PR-WRITTEN PROMPT\n")
        with mock.patch.dict(os.environ, {"QA_ROOT": str(self.root)}):
            text = planner._instructions(self.root, self.inp, "probe")
        self.assertIn((paths.REPO / "scripts/qa/prompts/planner.md").read_text(encoding="utf-8"), text)
        self.assertNotIn("PR-WRITTEN PROMPT", text)
        # The probe reuses planner.md, which itself mentions the diff; only the run section must not.
        self.assertNotIn("changeset.diff", text.rsplit("## This run", 1)[1])

    def test_oversized_task_input_fails_without_a_call(self) -> None:
        self.inp.write_text("x" * 200_001, encoding="utf-8")
        called = mock.Mock(return_value={"schema": "qa-plan-llm/v1"})
        with mock.patch.dict(planner.ADAPTERS, {"fake": called}):
            status = planner.ask(self.root, "fake", "probe", self.inp, self.out)
        self.assertEqual(status["status"], "failed")
        called.assert_not_called()


class TestJson(unittest.TestCase):
    def test_prose_around_json_is_tolerated(self) -> None:
        self.assertEqual(planner.extract_json('Sure!\n```json\n{"schema": "x"}\n```\nDone.'), {"schema": "x"})


if __name__ == "__main__":
    unittest.main()
