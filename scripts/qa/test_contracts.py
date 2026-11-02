"""Every file one step writes matches the schema the next step reads.

Run: python3 -m unittest discover -s scripts -p 'test_*.py'
"""

import re
import tempfile
import unittest
from pathlib import Path

from qa import __main__ as qa_main
from qa import catalog, collect, config, impact, run, schema, test_prove, testing, validate


class TestContracts(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.root, cls.tmp = testing.make_repo()
        base = testing.git(cls.root, "rev-parse", "HEAD")
        testing.write(cls.root, "backend/python/app/services/graph_db/provider.py", "X = 1\n")
        testing.commit(cls.root, "change")
        cls.cfg = config.load(cls.root)
        cls.rows = catalog.build(cls.cfg)
        cls.changeset, _ = collect.collect(cls.cfg, base, None, mode="pr")
        cls.plan_input = impact.compute(cls.cfg, cls.changeset, cls.rows)
        cls.plan = validate.merge(cls.cfg, cls.plan_input, None, catalog.index(cls.rows))

    @classmethod
    def tearDownClass(cls) -> None:
        cls.tmp.cleanup()

    def test_catalog_rows(self) -> None:
        for row in self.rows:
            self.assertEqual(schema.check(row, "catalog-row.v1.json"), [], row["id"])

    def test_changeset(self) -> None:
        self.assertEqual(schema.check(self.changeset, "changeset.v1.json"), [])

    def test_plan_input(self) -> None:
        self.assertEqual(schema.check(self.plan_input, "plan-input.v1.json"), [])

    def test_plan(self) -> None:
        self.assertEqual(schema.check(self.plan, "plan.v1.json"), [])

    def test_results(self) -> None:
        with tempfile.TemporaryDirectory() as out:
            result = run.run(self.cfg, self.plan, catalog.index(self.rows), Path(out), stack_mode="none")
        self.assertEqual(schema.check(result, "results.v1.json"), [])

    def test_results_environment_block(self) -> None:
        env = {"image": "pipeshub-ai:qa-0123456789ab", "image_digest": None, "base_images": {"A_IMAGE": "a/b@sha256:1"},
               "unpinned": ["node:20-slim"], "lockfiles": {"package-lock.json": "ab"}, "compose": {"c.yml": "cd"}}
        doc = {"schema": "qa-results/v1", "tests": [], "invocations": [], "fatal": [], "environment": env}
        self.assertEqual(schema.check(doc, "results.v1.json"), [])
        self.assertTrue(schema.check({**doc, "environment": {**env, "unpinned": "node"}}, "results.v1.json"))

    def test_metrics_match_the_schema(self) -> None:
        from qa.testing import fixture_metrics
        doc = fixture_metrics(self.rows)
        self.assertEqual({t["trigger"] for t in doc["trend"]}, {"nightly", "local"})
        self.assertEqual(schema.check(doc, "metrics.v1.json"), [])
        self.assertTrue(schema.check({**doc, "flake": {**doc["flake"], "by_suite": {"py-unit": "high"}}}, "metrics.v1.json"))

    def test_validator_catches_a_wrong_shape(self) -> None:
        self.assertTrue(schema.check({"schema": "qa-plan-llm/v1", "add": [{"reason": "no id"}]}, "plan-llm.v1.json"))

    def test_review_llm_schema_rejects_extra_keys(self) -> None:
        self.assertTrue(schema.check({"requirements": [], "checklist": [], "risks": [], "x": 1}, "review-llm.v1.json"))


class TestProofContract(test_prove.ProveCase):
    def test_proof_schema_matches_prove_output(self) -> None:
        evidence = test_prove.prove_with_mutant_kill(self)
        self.assertEqual(schema.check(evidence, "proof.v1.json"), [])
        self.assertTrue(schema.check({**evidence, "verdict": "maybe"}, "proof.v1.json"))


if __name__ == "__main__":
    unittest.main()


WORKFLOWS = Path(__file__).resolve().parents[2] / ".github/workflows"
# Set by the engine per plan, or only meaningful to the integration workflow's shards.
# TEST_AI_MODEL_PROVIDER differs on purpose: a pull request's stack uses the stand-in model.
NOT_MIRRORED = {"TEST_AI_MODEL_PROVIDER", "MESSAGE_BROKER", "COMPOSE_PROFILES", "PIPESHUB_REQUIRE_CONNECTOR_SECRETS",
                "CONNECTORS_INPUT", "SHARD", "RUN_PLAYWRIGHT", "CONN_SHARD_1", "CONN_SHARD_2", "CONN_SHARD_3",
                "CONN_SHARD_4", "DEMO_PACK_QUESTIONS", "NEEDS_GMAIL_SIM", "NEEDS_ATLASSIAN_SIM",
                "NEEDS_NOTION_SIM", "NEEDS_SHAREPOINT_SIM",
                "NEEDS_LOCAL_FS_SIM", "NEEDS_BOOKSTACK_SIM", "CONN_SHARD_5"}


def _job_env(path: Path, job: str) -> dict[str, str]:
    import re
    lines = path.read_text(encoding="utf-8").split("\n")
    start = lines.index(f"  {job}:")
    env = next(i for i in range(start, len(lines)) if lines[i] == "    env:")
    out: dict[str, str] = {}
    for line in lines[env + 1:]:
        if re.match(r"^ {0,4}\S", line):
            break
        m = re.match(r"^ {6}([A-Z][A-Z0-9_]+):\s*(.*)$", line)
        if m:
            out[m.group(1)] = m.group(2)
    return out


def _stack_job() -> str:
    text = (WORKFLOWS / "qa-pipeline.yml").read_text(encoding="utf-8")
    return text[text.index("\n  run-stack:"):]


class TestTrustedDispatch(unittest.TestCase):
    def test_trusted_dispatch_compares_the_sha(self) -> None:
        text = (Path(__file__).resolve().parents[2] / ".github/workflows/qa-command.yml").read_text()
        # The reviewed sha is the full 40 characters and must equal headRefOid.
        self.assertRegex(text, r'"\$head" != "\$trusted_head" \]\]')


class TestReviewInput(unittest.TestCase):
    def test_qa_workflows_pass_the_review_input(self) -> None:
        def read(name: str) -> str:
            return (WORKFLOWS / name).read_text(encoding="utf-8")
        self.assertIn('-f review="$review"', read("qa-command.yml"))
        self.assertIn("review: ${{ inputs.review || false }}", read("qa.yml"))
        pipeline = read("qa-pipeline.yml")
        self.assertIn('"$ENGINE" review --pr', pipeline)
        self.assertIn("inputs.review && inputs.use_model && secrets.ANTHROPIC_API_KEY", pipeline)


class TestSkills(unittest.TestCase):
    def test_qa_skills_name_real_commands(self) -> None:
        root = Path(__file__).resolve().parents[2]
        skills = sorted(root.glob(".claude/skills/qa-*/SKILL.md"))
        names = {p.parent.name for p in skills}
        self.assertTrue({"qa-pr", "qa-commit", "qa-review", "qa-generate"} <= names, names)
        sub = next(a for a in qa_main.parser()._actions if getattr(a, "choices", None))
        for path in skills:
            text = path.read_text(encoding="utf-8")
            front = text.split("---")[1]
            self.assertRegex(front, rf"(?m)^name: {path.parent.name}$")
            self.assertRegex(front, r"(?m)^description: \S")
            for word in re.findall(r"python3 scripts/qa (?:--out \S+ )?([a-z][\w-]*)", text):
                self.assertIn(word, sub.choices, f"{path}: {word}")


class TestReportAndGate(unittest.TestCase):
    def test_report_and_gate_use_one_allow_list(self) -> None:
        text = (WORKFLOWS / "qa-pipeline.yml").read_text(encoding="utf-8")
        self.assertIn("\n  QA_ALLOW_NOT_RUN: ${{ (github.event_name != 'schedule' && inputs.mode != 'full') && 'secret,profile' || '' }}\n", text)
        steps = {name: text[text.index(f"- name: {name}\n"):] for name in ("Report", "Gate")}
        for name, step in steps.items():
            step = step[:step.index("\n      - ", 1)] if "\n      - " in step[1:] else step
            self.assertIn('--allow-not-run "$QA_ALLOW_NOT_RUN"', step, name)


@unittest.skipUnless((WORKFLOWS / "integration-tests.yml").is_file(), "integration workflow not in this checkout")
class TestStackJobMatchesIntegrationWorkflow(unittest.TestCase):
    def test_the_qa_stack_job_gets_every_setting_the_integration_job_does(self) -> None:
        it = _job_env(WORKFLOWS / "integration-tests.yml", "integration-tests")
        qa = _job_env(WORKFLOWS / "qa-pipeline.yml", "run-stack")
        self.assertIn("PIPESHUB_TEST_ENV", it)
        drift = {k: (v, qa.get(k)) for k, v in it.items() if k not in NOT_MIRRORED and qa.get(k) != v}
        self.assertEqual(drift, {}, "copy these from integration-tests.yml into qa-pipeline.yml run-stack env")

    def test_stack_job_uses_the_daily_environment_on_schedule(self) -> None:
        expr = "${{ github.event_name == 'schedule' && 'integration-test-daily' || 'integration-test-dev' }}"
        self.assertIn(f"environment:\n      name: {expr}", _stack_job())

    def test_stack_job_installs_the_frontend_for_e2e(self) -> None:
        step = next(s for s in _stack_job().split("\n      - ") if "npm ci" in s)
        self.assertIn("if: contains(needs.plan.outputs.families, 'playwright')", step)
        self.assertIn("working-directory: frontend", step)
        self.assertIn("playwright install --with-deps chromium", step)
